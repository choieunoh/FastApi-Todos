import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool, StringConstraints, field_validator

BASE_DIR = Path(__file__).resolve().parent       # main.py 가 있는 폴더
TODO_FILE = Path(os.environ.get("TODO_FILE", BASE_DIR / "todo.json"))  # 테스트에서 바꿔 쓸 수 있다
INDEX_FILE = BASE_DIR / "templates" / "index.html"

if not TODO_FILE.exists():                       # 없으면 빈 목록으로 만들어 둔다
    TODO_FILE.write_text("[]", encoding="utf-8")

app = FastAPI(title="To-Do List API")

Priority = Literal["높음", "보통", "낮음"]
MAX_TAGS, MAX_TAG_LEN = 10, 20
UNDO_TTL = timedelta(seconds=30)                 # 삭제 후 복구할 수 있는 시간
BULK_MAX = 100                                   # 일괄 작업 한 번에 고를 수 있는 최대 건수
UNDO_MAX = BULK_MAX                              # 복구 버퍼·복구 요청 최대 건수 — 일괄 삭제 한 번을 모두 되돌릴 수 있게


class TodoIn(BaseModel):                         # 클라이언트가 보내는 데이터 — 시각 필드는 받지 않는다
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    completed: bool = False
    priority: Priority = "보통"
    due_date: date | None = None
    tags: list[str] = []
    favorite: StrictBool = False                 # 즐겨찾기 — "yes" 같은 문자열은 받지 않는다

    @field_validator("due_date", mode="before")
    @classmethod
    def blank_due_date(cls, v: object) -> object:
        if v == "":                              # v3 데이터의 "" 는 '마감일 없음'
            return None
        if v is not None and not isinstance(v, (str, date)):
            raise ValueError("due_date 는 YYYY-MM-DD 문자열이어야 합니다")
        return v

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags: list[str]) -> list[str]:
        result, seen = [], set()
        for tag in (t.strip() for t in tags):
            if not tag or len(tag) > MAX_TAG_LEN:
                raise ValueError(f"태그는 1~{MAX_TAG_LEN}자여야 합니다")
            if "," in tag:
                raise ValueError("태그에 쉼표를 넣을 수 없습니다")
            if tag.casefold() not in seen:       # 대소문자 무시 중복 제거, 처음 표기 유지
                seen.add(tag.casefold())
                result.append(tag)
        if len(result) > MAX_TAGS:
            raise ValueError(f"태그는 최대 {MAX_TAGS}개입니다")
        return result


class TodoItem(TodoIn):                          # 서버가 돌려주는 데이터 (id·시각 있음)
    id: int
    created_at: datetime | None = None
    completed_at: datetime | None = None


class RestoreIn(BaseModel):                      # 삭제 실행 취소 요청
    ids: list[int] = Field(min_length=1, max_length=UNDO_MAX)


class BulkIn(BaseModel):                         # 일괄 작업 요청
    ids: list[int] = Field(min_length=1, max_length=BULK_MAX)
    action: Literal["complete", "uncomplete", "delete"]

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, ids: list[int]) -> list[int]:
        return list(dict.fromkeys(ids))          # 중복 제거, 순서 유지


class BulkOut(BaseModel):                        # 일괄 작업 결과
    updated: list[TodoItem]
    deleted: list[int]


class DeletedEntry(BaseModel):                   # 복구 버퍼 항목 — 원래 자리와 삭제 시각을 함께 둔다
    todo: TodoItem
    position: int
    deleted_at: datetime


# 삭제 직후 복구용 메모리 버퍼 (id → 항목, 오래된 것부터). 재시작하면 비워진다
RECENTLY_DELETED: dict[int, DeletedEntry] = {}


def now() -> datetime:
    return datetime.now(timezone.utc)


def prune_deleted() -> None:
    # 시간이 지난 항목을 버리고, 최대 건수를 넘으면 오래된 것부터 버린다
    limit = now() - UNDO_TTL
    for todo_id in [i for i, e in RECENTLY_DELETED.items() if e.deleted_at <= limit]:
        del RECENTLY_DELETED[todo_id]
    while len(RECENTLY_DELETED) > UNDO_MAX:
        del RECENTLY_DELETED[next(iter(RECENTLY_DELETED))]


def next_id(todos: list[TodoItem]) -> int:
    # 복구 대기 중인 id 도 피해서 매긴다 (복구할 때 id 가 겹치지 않게)
    prune_deleted()
    return max([t.id for t in todos] + list(RECENTLY_DELETED), default=0) + 1


def apply_completion(old: TodoItem | None, new: TodoItem) -> TodoItem:
    # 완료 상태가 바뀔 때만 completed_at 을 정한다 (POST·PUT·bulk 공용, old 가 없으면 새로 만드는 경우)
    if old is not None and old.completed == new.completed:
        completed_at = old.completed_at
    else:
        completed_at = now() if new.completed else None
    return new.model_copy(update={"completed_at": completed_at})


def remember_deleted(todo: TodoItem, position: int) -> None:
    # 지운 항목을 잠시 복구 버퍼에 남긴다 (단건·일괄 삭제 공용)
    RECENTLY_DELETED.pop(todo.id, None)
    RECENTLY_DELETED[todo.id] = DeletedEntry(todo=todo, position=position, deleted_at=now())
    prune_deleted()


def load_todos() -> list[TodoItem]:
    raw = TODO_FILE.read_text(encoding="utf-8") if TODO_FILE.exists() else "[]"
    return [TodoItem(**t) for t in json.loads(raw)]


def save_todos(todos: list[TodoItem]) -> None:
    data = json.dumps([t.model_dump(mode="json") for t in todos], indent=2, ensure_ascii=False)
    TODO_FILE.write_text(data, encoding="utf-8")


def find_index(todos: list[TodoItem], todo_id: int) -> int:
    for i, todo in enumerate(todos):
        if todo.id == todo_id:
            return i
    raise HTTPException(404, "To-Do item not found")


# find_index가 던지는 404를 OpenAPI 문서에 명시
NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {"description": "To-Do item not found"},
}


@app.get("/todos")                               # 목록 조회
def get_todos() -> list[TodoItem]:
    return load_todos()


@app.post("/todos", status_code=201)             # 추가 — id·시각은 서버가 매긴다
def create_todo(payload: TodoIn) -> TodoItem:
    todos = load_todos()
    todo = apply_completion(None, TodoItem(id=next_id(todos), created_at=now(), **payload.model_dump()))
    save_todos(todos + [todo])
    return todo


@app.post("/todos/restore", responses={404: {"description": "복구할 수 없는 항목이 있음"}})  # 삭제 실행 취소
def restore_todos(payload: RestoreIn) -> list[TodoItem]:
    prune_deleted()
    ids = list(dict.fromkeys(payload.ids))       # 중복 제거, 순서 유지
    missing = [i for i in ids if i not in RECENTLY_DELETED]
    if missing:                                  # 하나라도 복구할 수 없으면 아무것도 바꾸지 않는다
        raise HTTPException(404, f"복구할 수 없는 항목입니다: {missing}")
    todos = load_todos()
    # 지운 순서의 역순으로 넣어야 각 항목이 지울 때의 자리로 돌아간다
    for todo_id in [i for i in reversed(RECENTLY_DELETED) if i in ids]:
        entry = RECENTLY_DELETED.pop(todo_id)
        todos.insert(min(entry.position, len(todos)), entry.todo)
    save_todos(todos)
    return [todos[find_index(todos, i)] for i in ids]


@app.post("/todos/bulk", responses={404: {"description": "없는 항목이 있음"}})  # 일괄 완료·미완료·삭제
def bulk_todos(payload: BulkIn) -> BulkOut:
    todos = load_todos()
    positions = {t.id: i for i, t in enumerate(todos)}
    missing = [i for i in payload.ids if i not in positions]
    if missing:                                  # 하나라도 없으면 아무것도 바꾸지 않는다
        raise HTTPException(404, f"없는 항목입니다: {missing}")

    if payload.action == "delete":
        removed = []
        for todo_id in payload.ids:              # 하나씩 지우며 그때의 자리를 기억한다 (복구 순서용)
            index = find_index(todos, todo_id)
            removed.append((todos.pop(index), index))
        save_todos(todos)
        for todo, index in removed:
            remember_deleted(todo, index)
        return BulkOut(updated=[], deleted=payload.ids)

    completed = payload.action == "complete"
    for todo_id in payload.ids:
        old = todos[positions[todo_id]]
        todos[positions[todo_id]] = apply_completion(old, old.model_copy(update={"completed": completed}))
    save_todos(todos)
    return BulkOut(updated=[todos[positions[i]] for i in payload.ids], deleted=[])


@app.put("/todos/{todo_id}", responses=NOT_FOUND_RESPONSE)  # 수정 — created_at 유지, 완료 상태가 바뀔 때만 completed_at 갱신
def update_todo(todo_id: int, payload: TodoIn) -> TodoItem:
    todos = load_todos()
    index = find_index(todos, todo_id)
    old = todos[index]
    todo = apply_completion(old, TodoItem(id=todo_id, created_at=old.created_at, **payload.model_dump()))
    todos[index] = todo
    save_todos(todos)
    return todo


@app.delete("/todos/{todo_id}", status_code=204, responses=NOT_FOUND_RESPONSE)  # 삭제 — 잠시 복구 버퍼에 남긴다
def delete_todo(todo_id: int) -> None:
    todos = load_todos()
    index = find_index(todos, todo_id)
    todo = todos.pop(index)
    save_todos(todos)
    remember_deleted(todo, index)


@app.get("/", include_in_schema=False)           # 화면 서빙
def read_root() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")
