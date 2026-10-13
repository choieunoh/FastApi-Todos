from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from conftest import REAL_TODO_FILE, REAL_TODO_HASH, file_hash
from main import TodoItem, app, load_todos, save_todos

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_file(tmp_path, monkeypatch):
    # 실제 todo.json 대신 테스트마다 새 임시 파일 사용 (본인 데이터 보호 + 테스트 간 격리)
    path = tmp_path / "todo.json"
    monkeypatch.setattr(main, "TODO_FILE", path)
    save_todos([])
    return path


def create(**fields):
    return client.post("/todos", json={"title": "할 일", **fields})


def put(todo_id: int, **fields):
    return client.put(f"/todos/{todo_id}", json={"title": "할 일", **fields})


# ---------- 생성 / 검증 ----------

def test_a1_create_returns_id_tags_and_timestamps():
    res = create()
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == 1
    assert body["tags"] == []
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
    assert body["completed_at"] is None
    assert len(load_todos()) == 1


def test_a2_blank_title_rejected():
    assert create(title="   ").status_code == 422


def test_a3_title_trimmed():
    assert create(title="  과제  ").json()["title"] == "과제"


def test_a4_title_too_long():
    assert create(title="가" * 101).status_code == 422
    assert create(title="가" * 100).status_code == 201


def test_title_missing():
    assert client.post("/todos", json={"description": "설명"}).status_code == 422


def test_description_trimmed_and_limited():
    assert create(description="  설명  ").json()["description"] == "설명"
    assert create(description="a" * 501).status_code == 422


@pytest.mark.parametrize("priority", ["높음", "보통", "낮음"])
def test_a5_valid_priority(priority):
    res = create(priority=priority)
    assert res.status_code == 201
    assert res.json()["priority"] == priority


def test_a5_invalid_priority():
    assert create(priority="긴급").status_code == 422


@pytest.mark.parametrize("due_date", ["2026-02-30", "2026/10/05", "내일"])
def test_a6_invalid_due_date(due_date):
    assert create(due_date=due_date).status_code == 422


@pytest.mark.parametrize("due_date", ["", None])
def test_a7_empty_due_date_is_null(due_date):
    res = create(due_date=due_date)
    assert res.status_code == 201
    assert res.json()["due_date"] is None
    assert load_todos()[0].due_date is None


def test_a8_tags_trimmed_and_deduplicated():
    assert create(tags=["학교", " 과제 ", "학교"]).json()["tags"] == ["학교", "과제"]


def test_a9_tags_case_insensitive_dedup_keeps_first():
    assert create(tags=["Work", "work"]).json()["tags"] == ["Work"]


@pytest.mark.parametrize("tags", [[""], ["가" * 21], ["a,b"], [f"t{i}" for i in range(11)]])
def test_a10_invalid_tags(tags):
    assert create(tags=tags).status_code == 422


def test_a11_client_timestamps_ignored():
    res = create(created_at="2000-01-01T00:00:00Z", completed_at="2000-01-01T00:00:00Z")
    body = res.json()
    assert not body["created_at"].startswith("2000")
    assert body["completed_at"] is None


def test_a12_create_completed_sets_completed_at():
    body = create(completed=True).json()
    assert body["completed_at"] is not None


# ---------- 수정 / 시각 규칙 ----------

def test_a13_put_keeps_created_at():
    created = create().json()
    updated = put(created["id"], title="바뀐 제목", created_at="2000-01-01T00:00:00Z").json()
    assert updated["created_at"] == created["created_at"]


def test_a14_complete_sets_completed_at():
    todo_id = create().json()["id"]
    assert put(todo_id, completed=True).json()["completed_at"] is not None


def test_a15_uncomplete_clears_completed_at():
    todo_id = create(completed=True).json()["id"]
    assert put(todo_id, completed=False).json()["completed_at"] is None


def test_a16_title_edit_keeps_completed_at():
    created = create(completed=True).json()
    updated = put(created["id"], title="제목만 수정", completed=True).json()
    assert updated["completed_at"] == created["completed_at"]


@pytest.mark.parametrize("fields", [
    {"title": "   "},
    {"title": "가" * 101},
    {"priority": "긴급"},
    {"due_date": "2026-02-30"},
    {"due_date": "2026/10/05"},
    {"due_date": "내일"},
    {"tags": [""]},
    {"tags": ["가" * 21]},
    {"tags": ["a,b"]},
    {"tags": [f"t{i}" for i in range(11)]},
])
def test_a17_put_validation(fields):
    todo_id = create().json()["id"]
    assert put(todo_id, **fields).status_code == 422


def test_a17_put_normalizes_values():
    todo_id = create().json()["id"]
    body = put(todo_id, title="  과제  ", due_date="", tags=["Work", " work "]).json()
    assert (body["title"], body["due_date"], body["tags"]) == ("과제", None, ["Work"])


def test_a18_missing_id():
    assert put(999).status_code == 404
    assert client.delete("/todos/999").status_code == 404


# ---------- 호환 / 회귀 ----------

def test_a19_v3_data_gets_defaults(isolated_file):
    isolated_file.write_text(
        '[{"id": 1, "title": "v1", "description": "", "completed": false},'
        ' {"id": 2, "title": "v3", "description": "", "completed": true, "priority": "높음", "due_date": ""}]',
        encoding="utf-8",
    )
    res = client.get("/todos")
    assert res.status_code == 200
    v1, v3 = res.json()
    assert (v1["priority"], v1["due_date"], v1["tags"], v1["created_at"], v1["completed_at"]) == \
        ("보통", None, [], None, None)
    assert (v3["priority"], v3["due_date"]) == ("높음", None)


def test_v3_data_survives_put(isolated_file):
    isolated_file.write_text('[{"id": 1, "title": "old", "description": "", "completed": false}]', encoding="utf-8")
    body = put(1, title="new", completed=True).json()
    assert body["created_at"] is None              # 모르는 생성 시각은 지어내지 않는다
    assert body["completed_at"] is not None


def test_a20_crud_flow():
    todo_id = create(title="흐름").json()["id"]
    assert any(t["id"] == todo_id for t in client.get("/todos").json())
    assert put(todo_id, title="수정됨").json()["title"] == "수정됨"
    assert client.delete(f"/todos/{todo_id}").status_code == 204
    assert client.get("/todos").json() == []


def test_a21_new_id_is_max_plus_one():
    save_todos([TodoItem(id=3, title="a"), TodoItem(id=7, title="b")])
    assert create().json()["id"] == 8


def test_a22_real_todo_file_untouched():
    create()
    put(1, title="x")
    client.delete("/todos/1")
    assert file_hash(REAL_TODO_FILE) == REAL_TODO_HASH


def test_index_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "Version 5.0.0" in res.text
