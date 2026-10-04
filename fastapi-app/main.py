import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, StringConstraints, field_validator

BASE_DIR = Path(__file__).resolve().parent       # main.py 가 있는 폴더
TODO_FILE = Path(os.environ.get("TODO_FILE", BASE_DIR / "todo.json"))  # 테스트에서 바꿔 쓸 수 있다
INDEX_FILE = BASE_DIR / "templates" / "index.html"

if not TODO_FILE.exists():                       # 없으면 빈 목록으로 만들어 둔다
    TODO_FILE.write_text("[]", encoding="utf-8")

app = FastAPI(title="To-Do List API")

Priority = Literal["높음", "보통", "낮음"]
MAX_TAGS, MAX_TAG_LEN = 10, 20


class TodoIn(BaseModel):                         # 클라이언트가 보내는 데이터 — 시각 필드는 받지 않는다
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    completed: bool = False
    priority: Priority = "보통"
    due_date: date | None = None
    tags: list[str] = []

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


def now() -> datetime:
    return datetime.now(timezone.utc)


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


@app.get("/todos")                               # 목록 조회
def get_todos() -> list[TodoItem]:
    return load_todos()


@app.post("/todos", status_code=201)             # 추가 — id·시각은 서버가 매긴다
def create_todo(payload: TodoIn) -> TodoItem:
    todos = load_todos()
    new_id = max((t.id for t in todos), default=0) + 1
    created = now()
    todo = TodoItem(
        id=new_id,
        created_at=created,
        completed_at=created if payload.completed else None,
        **payload.model_dump(),
    )
    save_todos(todos + [todo])
    return todo


@app.put("/todos/{todo_id}")                     # 수정 — created_at 유지, 완료 상태가 바뀔 때만 completed_at 갱신
def update_todo(todo_id: int, payload: TodoIn) -> TodoItem:
    todos = load_todos()
    index = find_index(todos, todo_id)
    old = todos[index]
    if old.completed == payload.completed:
        completed_at = old.completed_at
    else:
        completed_at = now() if payload.completed else None
    todo = TodoItem(
        id=todo_id,
        created_at=old.created_at,
        completed_at=completed_at,
        **payload.model_dump(),
    )
    todos[index] = todo
    save_todos(todos)
    return todo


@app.delete("/todos/{todo_id}", status_code=204)  # 삭제
def delete_todo(todo_id: int) -> None:
    todos = load_todos()
    del todos[find_index(todos, todo_id)]
    save_todos(todos)


@app.get("/", include_in_schema=False)           # 화면 서빙
def read_root() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")
