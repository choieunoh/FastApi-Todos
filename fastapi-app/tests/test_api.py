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
    monkeypatch.setattr(main, "RECENTLY_DELETED", {})   # 삭제 복구 버퍼도 테스트마다 비운다
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


# ---------- 5.0.0 1단계: 즐겨찾기 ----------

def test_favorite_defaults_to_false():
    assert create().json()["favorite"] is False
    assert load_todos()[0].favorite is False


def test_favorite_set_on_create():
    assert create(favorite=True).json()["favorite"] is True


def test_favorite_toggle_with_put():
    created = create(title="별표", priority="높음", tags=["a"], completed=True).json()
    on = put(created["id"], title="별표", priority="높음", tags=["a"], completed=True, favorite=True).json()
    assert on["favorite"] is True
    assert client.get("/todos").json()[0]["favorite"] is True
    # 즐겨찾기만 바꿔도 다른 필드·시각은 그대로다
    assert (on["priority"], on["tags"], on["created_at"], on["completed_at"]) == \
        ("높음", ["a"], created["created_at"], created["completed_at"])
    off = put(created["id"], title="별표", favorite=False).json()
    assert off["favorite"] is False


@pytest.mark.parametrize("value", ["yes", "true", 1, None])
def test_favorite_must_be_boolean(value):
    assert create(favorite=value).status_code == 422
    todo_id = create().json()["id"]
    assert put(todo_id, favorite=value).status_code == 422


def test_v4_data_without_favorite(isolated_file):
    isolated_file.write_text(
        '[{"id": 1, "title": "v4", "description": "", "completed": false, "priority": "보통",'
        ' "due_date": null, "tags": ["t"], "created_at": "2026-10-01T00:00:00Z", "completed_at": null}]',
        encoding="utf-8",
    )
    res = client.get("/todos")
    assert res.status_code == 200
    assert res.json()[0]["favorite"] is False
    assert put(1, title="v4", tags=["t"], favorite=True).json()["created_at"].startswith("2026-10-01")


# ---------- 5.0.0 2단계: 삭제 실행 취소 ----------

def restore(*ids):
    return client.post("/todos/restore", json={"ids": list(ids)})


def delete(todo_id: int):
    return client.delete(f"/todos/{todo_id}")


def test_restore_keeps_id_created_at_and_content():
    create(title="앞")
    created = create(title="복구 대상", description="설명", priority="높음", due_date="2026-10-20",
                     tags=["a", "b"], completed=True, favorite=True).json()
    create(title="뒤")
    assert delete(created["id"]).status_code == 204
    assert [t["title"] for t in client.get("/todos").json()] == ["앞", "뒤"]

    res = restore(created["id"])
    assert res.status_code == 200
    assert res.json() == [created]                      # id, created_at, completed_at, 내용 모두 그대로
    assert [t["title"] for t in client.get("/todos").json()] == ["앞", "복구 대상", "뒤"]


def test_restore_twice_is_404():
    todo_id = create().json()["id"]
    delete(todo_id)
    assert restore(todo_id).status_code == 200
    assert restore(todo_id).status_code == 404


def test_restore_unknown_id_is_404():
    assert restore(999).status_code == 404
    todo_id = create().json()["id"]                     # 지우지 않은 항목도 복구 대상이 아니다
    assert restore(todo_id).status_code == 404


def test_restore_is_all_or_nothing():
    todo_id = create().json()["id"]
    delete(todo_id)
    assert restore(todo_id, 999).status_code == 404
    assert client.get("/todos").json() == []            # 하나도 복구되지 않는다
    assert restore(todo_id).status_code == 200          # 버퍼에도 그대로 남아 있다


def test_restore_many_in_original_order():
    for title in "ABCDE":
        create(title=title)
    for todo_id in (1, 5, 3):                           # 여러 자리에서 차례로 지운다
        delete(todo_id)
    res = restore(3, 1, 3, 5)                           # 중복과 순서는 상관없다
    assert [t["id"] for t in res.json()] == [3, 1, 5]
    assert [t["title"] for t in client.get("/todos").json()] == list("ABCDE")


def test_restore_after_other_changes():
    for title in "ABC":
        create(title=title)
    delete(2)
    create(title="D")
    delete(1)
    restore(2)
    assert [t["title"] for t in client.get("/todos").json()] == ["C", "B", "D"]


def test_new_id_skips_deleted_max_id():
    create()
    deleted_id = create().json()["id"]
    delete(deleted_id)
    new_id = create().json()["id"]
    assert new_id == deleted_id + 1
    assert restore(deleted_id).status_code == 200
    assert sorted(t["id"] for t in client.get("/todos").json()) == [1, deleted_id, new_id]


def test_restore_expires_after_ttl(monkeypatch):
    first, second = create().json()["id"], create().json()["id"]
    delete(first)
    delete(second)
    start = main.now()
    monkeypatch.setattr(main, "now", lambda: start + main.UNDO_TTL - main.timedelta(seconds=1))
    assert restore(second).status_code == 200            # 30초 안이면 복구된다
    monkeypatch.setattr(main, "now", lambda: start + main.UNDO_TTL + main.timedelta(seconds=1))
    assert restore(first).status_code == 404             # 30초가 지나면 404
    assert main.RECENTLY_DELETED == {}


def test_buffer_keeps_latest_100():
    assert main.UNDO_MAX == main.BULK_MAX == 100
    save_todos([TodoItem(id=i, title=f"t{i}") for i in range(1, 102)])
    for i in range(1, 102):
        delete(i)
    assert len(main.RECENTLY_DELETED) == 100
    assert restore(1).status_code == 404                 # 가장 오래된 1건은 밀려난다
    assert restore(*range(2, 102)).status_code == 200
    assert len(client.get("/todos").json()) == 100


@pytest.mark.parametrize("body", [{}, {"ids": []}, {"ids": ["a"]}, {"ids": list(range(1, 102))}])
def test_restore_validation(body):
    assert client.post("/todos/restore", json=body).status_code == 422


def test_delete_status_unchanged():
    assert delete(1).status_code == 404
    todo_id = create().json()["id"]
    res = delete(todo_id)
    assert res.status_code == 204
    assert res.content == b""


# ---------- 5.0.0 3단계: 일괄 작업 ----------

def bulk(ids, action):
    return client.post("/todos/bulk", json={"ids": ids, "action": action})


def test_bulk_complete_sets_completed_at_only_on_transition():
    done = create(title="이미 완료", completed=True).json()
    todo = create(title="미완료").json()
    res = bulk([done["id"], todo["id"]], "complete")
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == []
    by_id = {t["id"]: t for t in body["updated"]}
    assert by_id[done["id"]]["completed_at"] == done["completed_at"]    # 이미 완료면 시각 유지
    assert by_id[todo["id"]]["completed"] is True
    assert by_id[todo["id"]]["completed_at"] is not None
    assert all(t["completed"] for t in client.get("/todos").json())


def test_bulk_uncomplete_clears_completed_at():
    a = create(completed=True).json()["id"]
    b = create().json()["id"]
    body = bulk([a, b], "uncomplete").json()
    assert [(t["id"], t["completed"], t["completed_at"]) for t in body["updated"]] == \
        [(a, False, None), (b, False, None)]


def test_bulk_keeps_other_fields():
    created = create(title="그대로", priority="높음", tags=["t"], favorite=True, due_date="2026-10-20").json()
    updated = bulk([created["id"]], "complete").json()["updated"][0]
    for key in ("title", "priority", "tags", "favorite", "due_date", "created_at"):
        assert updated[key] == created[key]


def test_bulk_delete_then_restore_all():
    for title in "ABCDE":
        create(title=title)
    res = bulk([4, 2, 2], "delete")                       # 중복은 한 번만
    assert res.status_code == 200
    assert res.json() == {"updated": [], "deleted": [4, 2]}
    assert [t["title"] for t in client.get("/todos").json()] == ["A", "C", "E"]
    assert restore(4, 2).status_code == 200
    assert [t["title"] for t in client.get("/todos").json()] == list("ABCDE")


def test_bulk_deleted_ids_not_reused():
    create()
    create()
    bulk([1, 2], "delete")
    assert create().json()["id"] == 3


@pytest.mark.parametrize("action", ["complete", "uncomplete", "delete"])
def test_bulk_missing_id_changes_nothing(action, isolated_file):
    create(title="A")
    create(title="B", completed=True)
    before = isolated_file.read_bytes()
    res = bulk([1, 2, 999], action)
    assert res.status_code == 404
    assert "999" in res.json()["detail"]
    assert isolated_file.read_bytes() == before
    assert main.RECENTLY_DELETED == {}


@pytest.mark.parametrize("body", [
    {"ids": [], "action": "complete"},
    {"ids": [1], "action": "archive"},
    {"ids": [1]},
    {"action": "delete"},
    {"ids": ["a"], "action": "delete"},
    {"ids": list(range(1, 102)), "action": "complete"},
])
def test_bulk_validation(body):
    create()
    assert client.post("/todos/bulk", json=body).status_code == 422


def test_bulk_accepts_100_ids():
    save_todos([TodoItem(id=i, title=f"t{i}") for i in range(1, 101)])
    res = bulk(list(range(1, 101)), "complete")
    assert res.status_code == 200
    assert len(res.json()["updated"]) == 100


def test_bulk_delete_100_then_restore_all():
    originals = [create(title=f"할 일 {i}", tags=[f"t{i % 3}"], favorite=i % 2 == 0, completed=i % 5 == 0).json()
                 for i in range(1, 101)]
    ids = [t["id"] for t in originals]
    res = bulk(ids, "delete")
    assert res.status_code == 200
    assert res.json()["deleted"] == ids
    assert client.get("/todos").json() == []
    assert len(main.RECENTLY_DELETED) == 100

    res = restore(*ids)                                   # 100건 모두 한 번에 복구
    assert res.status_code == 200
    assert res.json() == originals                        # 원래 id·시각·내용 그대로
    assert client.get("/todos").json() == originals       # 원래 순서도 그대로
    assert main.RECENTLY_DELETED == {}


def test_restore_101_ids_is_422():
    save_todos([TodoItem(id=i, title=f"t{i}") for i in range(1, 101)])
    bulk(list(range(1, 101)), "delete")
    assert restore(*range(1, 102)).status_code == 422
    assert len(main.RECENTLY_DELETED) == 100              # 거부된 요청은 버퍼를 건드리지 않는다


def test_next_bulk_delete_pushes_out_oldest():
    save_todos([TodoItem(id=i, title=f"t{i}") for i in range(1, 106)])
    bulk(list(range(1, 101)), "delete")
    bulk(list(range(101, 106)), "delete")
    assert restore(*range(1, 6)).status_code == 404       # 먼저 지운 5건은 밀려났다
    assert restore(*range(6, 106)).status_code == 200
