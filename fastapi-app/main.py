import calendar
import csv
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import (
    AfterValidator, BaseModel, Field, StrictBool, StringConstraints, field_validator, model_validator,
)

BASE_DIR = Path(__file__).resolve().parent       # main.py 가 있는 폴더
TODO_FILE = Path(os.environ.get("TODO_FILE", BASE_DIR / "todo.json"))  # 테스트에서 바꿔 쓸 수 있다
TEMPLATE_FILE = Path(os.environ.get("TEMPLATE_FILE", BASE_DIR / "task_templates.json"))  # 처음 저장할 때 만든다
INDEX_FILE = BASE_DIR / "templates" / "index.html"

if not TODO_FILE.exists():                       # 없으면 빈 목록으로 만들어 둔다
    TODO_FILE.write_text("[]", encoding="utf-8")

app = FastAPI(title="To-Do List API")

Priority = Literal["높음", "보통", "낮음"]
Recurrence = Literal["none", "daily", "weekly", "monthly"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]  # 할 일·하위 할 일 공용
MAX_TAGS, MAX_TAG_LEN = 10, 20
UNDO_TTL = timedelta(seconds=30)                 # 삭제 후 복구할 수 있는 시간
BULK_MAX = 100                                   # 일괄 작업 한 번에 고를 수 있는 최대 건수
UNDO_MAX = BULK_MAX                              # 복구 버퍼·복구 요청 최대 건수 — 일괄 삭제 한 번을 모두 되돌릴 수 있게
SUBTASK_MAX = 20                                 # 할 일 하나에 둘 수 있는 하위 할 일 수
KST = timezone(timedelta(hours=9), "KST")        # 내보내기 파일명 날짜 기준 (tzdata 없이 고정 오프셋)
ExportFormat = Literal["json", "csv"]
# CSV 고정 헤더 — 값은 JSON 과 같은 표기, 서버 내부용 next_spawned 는 뺀다
CSV_COLUMNS = ["id", "title", "description", "completed", "priority", "due_date", "tags",
               "favorite", "recurrence", "subtasks", "created_at", "completed_at"]
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")      # 스프레드시트가 수식으로 읽는 시작 문자


def clean_tag_list(tags: list[str]) -> list[str]:
    # 할 일·템플릿 공용 태그 규칙
    result, seen = [], set()
    for tag in (t.strip() for t in tags):
        if not tag or len(tag) > MAX_TAG_LEN:
            raise ValueError(f"태그는 1~{MAX_TAG_LEN}자여야 합니다")
        if "," in tag:
            raise ValueError("태그에 쉼표를 넣을 수 없습니다")
        if tag.casefold() not in seen:           # 대소문자 무시 중복 제거, 처음 표기 유지
            seen.add(tag.casefold())
            result.append(tag)
    if len(result) > MAX_TAGS:
        raise ValueError(f"태그는 최대 {MAX_TAGS}개입니다")
    return result


Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Tags = Annotated[list[str], AfterValidator(clean_tag_list)]


class TodoIn(BaseModel):                         # 클라이언트가 보내는 데이터 — 시각 필드는 받지 않는다
    title: Title
    description: Description = ""
    completed: bool = False
    priority: Priority = "보통"
    due_date: date | None = None
    tags: Tags = []
    favorite: StrictBool = False                 # 즐겨찾기 — "yes" 같은 문자열은 받지 않는다
    recurrence: Recurrence = "none"              # 반복 주기 — 반복하려면 마감일이 있어야 한다

    @field_validator("due_date", mode="before")
    @classmethod
    def blank_due_date(cls, v: object) -> object:
        if v == "":                              # v3 데이터의 "" 는 '마감일 없음'
            return None
        if v is not None and not isinstance(v, (str, date)):
            raise ValueError("due_date 는 YYYY-MM-DD 문자열이어야 합니다")
        return v

    @model_validator(mode="after")
    def recurrence_needs_due_date(self) -> "TodoIn":
        if self.recurrence != "none" and self.due_date is None:
            raise ValueError("반복 일정에는 마감일이 필요합니다")
        return self


class TemplateIn(BaseModel):                     # 작업 템플릿 — 마감일·완료·시각은 없다
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    title: Title
    description: Description = ""
    priority: Priority = "보통"
    tags: Tags = []
    recurrence: Recurrence = "none"              # 마감일 필수 검사는 할 일을 만들 때(POST /todos) 한다
    subtasks: list[Title] = Field(default=[], max_length=SUBTASK_MAX)   # 하위 할 일 제목들


class TaskTemplate(TemplateIn):
    id: int


class Subtask(BaseModel):                        # 하위 할 일 — id 는 그 할 일 안에서만 고유하다
    id: int
    title: Title
    completed: bool = False


class SubtaskIn(BaseModel):                      # 하위 할 일 추가 요청
    title: Title


class SubtaskUpdate(BaseModel):                  # 하위 할 일 수정 요청 — 전체 교체라 두 필드 모두 필수
    title: Title
    completed: StrictBool


class TodoItem(TodoIn):                          # 서버가 돌려주는 데이터 (id·시각·하위 할 일 있음)
    id: int
    created_at: datetime | None = None
    completed_at: datetime | None = None
    subtasks: list[Subtask] = []                 # 서버가 관리 — POST/PUT 본문의 값은 받지 않는다
    next_spawned: bool = False                   # 서버가 관리 — 다음 반복 일정을 이미 만들었는지


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


def add_months(d: date, months: int) -> date:
    # 없는 날짜는 그 달의 마지막 날로 당긴다 (1/31 + 1개월 → 2/28, 윤년이면 2/29)
    year, month = divmod(d.month - 1 + months, 12)
    year, month = d.year + year, month + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def next_due_date(due: date, recurrence: Recurrence) -> date:
    # 직전 마감일 기준으로 한 주기 뒤 (이미 지난 날짜여도 건너뛰지 않는다)
    if recurrence == "daily":
        return due + timedelta(days=1)
    if recurrence == "weekly":
        return due + timedelta(days=7)
    return add_months(due, 1)


def next_occurrence(todo: TodoItem, new_id: int) -> TodoItem:
    # 다음 반복 일정 — 내용은 복사하고 완료·시각·하위 완료 상태는 새로 시작한다
    return TodoItem(
        id=new_id,
        title=todo.title,
        description=todo.description,
        priority=todo.priority,
        due_date=next_due_date(todo.due_date, todo.recurrence),
        tags=todo.tags,
        favorite=todo.favorite,
        recurrence=todo.recurrence,
        created_at=now(),
        subtasks=[Subtask(id=i, title=s.title) for i, s in enumerate(todo.subtasks, start=1)],
    )


def apply_completion(
    todos: list[TodoItem], old: TodoItem | None, new: TodoItem,
) -> tuple[TodoItem, TodoItem | None]:
    # 완료 상태 전이 처리 (POST·PUT·bulk 공용, old 가 없으면 새로 만드는 경우)
    # - 완료 상태가 바뀔 때만 completed_at 을 정한다
    # - 반복 항목이 완료로 바뀌면 다음 일정을 한 번만 만든다 (돌려받은 쪽이 목록에 넣는다)
    became_completed = new.completed and (old is None or not old.completed)
    if old is not None and old.completed == new.completed:
        completed_at = old.completed_at
    else:
        completed_at = now() if new.completed else None
    todo = new.model_copy(update={"completed_at": completed_at})
    if not (became_completed and todo.recurrence != "none" and not todo.next_spawned):
        return todo, None
    todo = todo.model_copy(update={"next_spawned": True})
    return todo, next_occurrence(todo, next_id(todos + [todo]))


def remember_deleted(todo: TodoItem, position: int) -> None:
    # 지운 항목을 잠시 복구 버퍼에 남긴다 (단건·일괄 삭제 공용)
    RECENTLY_DELETED.pop(todo.id, None)
    RECENTLY_DELETED[todo.id] = DeletedEntry(todo=todo, position=position, deleted_at=now())
    prune_deleted()


def read_list(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def write_list(path: Path, items: list[BaseModel]) -> None:
    data = json.dumps([i.model_dump(mode="json") for i in items], indent=2, ensure_ascii=False)
    path.write_text(data, encoding="utf-8")


def load_todos() -> list[TodoItem]:
    return [TodoItem(**t) for t in read_list(TODO_FILE)]


def save_todos(todos: list[TodoItem]) -> None:
    write_list(TODO_FILE, todos)


def load_templates() -> list[TaskTemplate]:
    return [TaskTemplate(**t) for t in read_list(TEMPLATE_FILE)]


def save_templates(templates: list[TaskTemplate]) -> None:
    write_list(TEMPLATE_FILE, templates)


def find_template_index(templates: list[TaskTemplate], template_id: int) -> int:
    for i, template in enumerate(templates):
        if template.id == template_id:
            return i
    raise HTTPException(404, "Template not found")


def check_template_name(templates: list[TaskTemplate], name: str, skip_id: int | None = None) -> None:
    # 이름은 대소문자 무시로 겹치면 안 된다 (저장된 목록을 봐야 알 수 있는 규칙)
    if any(t.name.casefold() == name.casefold() and t.id != skip_id for t in templates):
        raise HTTPException(409, f"이미 있는 템플릿 이름입니다: {name}")


def find_index(todos: list[TodoItem], todo_id: int) -> int:
    for i, todo in enumerate(todos):
        if todo.id == todo_id:
            return i
    raise HTTPException(404, "To-Do item not found")


def find_subtask_index(todo: TodoItem, subtask_id: int) -> int:
    for i, subtask in enumerate(todo.subtasks):
        if subtask.id == subtask_id:
            return i
    raise HTTPException(404, "Subtask not found")


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
    todo, spawned = apply_completion(todos, None, TodoItem(id=next_id(todos), created_at=now(), **payload.model_dump()))
    save_todos(todos + [todo] + ([spawned] if spawned else []))
    return todo


# ---------- 내보내기 — 읽기 전용, 항상 전체 목록 ----------

def csv_cell(value: object) -> str:
    # JSON 과 같은 표기로 바꾸고, 수식으로 읽힐 값은 앞에 ' 를 붙인다
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    return "'" + text if text.startswith(CSV_FORMULA_PREFIXES) else text


def todos_to_csv(todos: list[TodoItem]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)                  # 쉼표·따옴표·줄바꿈이 든 값은 csv 모듈이 따옴표로 감싼다
    writer.writerow(CSV_COLUMNS)
    for todo in todos:
        row = todo.model_dump(mode="json")
        row["tags"] = ";".join(todo.tags)
        row["subtasks"] = f"{sum(s.completed for s in todo.subtasks)}/{len(todo.subtasks)}"
        writer.writerow([csv_cell(row[column]) for column in CSV_COLUMNS])
    return "\ufeff" + buffer.getvalue()          # BOM — 엑셀에서 한글이 깨지지 않게


@app.get("/todos/export", responses={200: {"content": {"text/csv": {}}}})
def export_todos(export_format: Annotated[ExportFormat, Query(alias="format")]) -> Response:
    todos = load_todos()
    filename = f"todos-{now().astimezone(KST):%Y%m%d}.{export_format}"
    if export_format == "json":                  # GET /todos 와 같은 내용
        body = json.dumps([t.model_dump(mode="json") for t in todos], indent=2, ensure_ascii=False)
        media_type = "application/json; charset=utf-8"
    else:
        body = todos_to_csv(todos)
        media_type = "text/csv; charset=utf-8"
    return Response(body.encode("utf-8"), media_type=media_type,
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


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
        todo, spawned = apply_completion(todos, old, old.model_copy(update={"completed": completed}))
        todos[positions[todo_id]] = todo
        if spawned:                              # 다음 반복 일정은 목록 끝에 붙인다 (기존 자리는 그대로)
            todos.append(spawned)
    save_todos(todos)
    return BulkOut(updated=[todos[positions[i]] for i in payload.ids], deleted=[])


@app.put("/todos/{todo_id}", responses=NOT_FOUND_RESPONSE)  # 수정 — created_at 유지, 완료 상태가 바뀔 때만 completed_at 갱신
def update_todo(todo_id: int, payload: TodoIn) -> TodoItem:
    todos = load_todos()
    index = find_index(todos, todo_id)
    old = todos[index]
    todo, spawned = apply_completion(todos, old, TodoItem(
        id=todo_id, created_at=old.created_at, subtasks=old.subtasks, next_spawned=old.next_spawned,
        **payload.model_dump()))
    todos[index] = todo
    save_todos(todos + ([spawned] if spawned else []))
    return todo


@app.delete("/todos/{todo_id}", status_code=204, responses=NOT_FOUND_RESPONSE)  # 삭제 — 잠시 복구 버퍼에 남긴다
def delete_todo(todo_id: int) -> None:
    todos = load_todos()
    index = find_index(todos, todo_id)
    todo = todos.pop(index)
    save_todos(todos)
    remember_deleted(todo, index)


# ---------- 하위 할 일 — 상위 할 일의 completed 와는 연동하지 않는다 ----------

SUBTASK_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"description": "To-Do item or subtask not found"},
}


@app.post("/todos/{todo_id}/subtasks", status_code=201,
          responses={**SUBTASK_RESPONSES, 422: {"description": f"제목 검증 실패 또는 {SUBTASK_MAX}개 초과"}})
def add_subtask(todo_id: int, payload: SubtaskIn) -> TodoItem:
    todos = load_todos()
    todo = todos[find_index(todos, todo_id)]
    if len(todo.subtasks) >= SUBTASK_MAX:        # 개수 한도는 저장된 상태로만 알 수 있다
        raise HTTPException(422, f"하위 할 일은 최대 {SUBTASK_MAX}개입니다")
    new_id = max((s.id for s in todo.subtasks), default=0) + 1
    todo.subtasks.append(Subtask(id=new_id, title=payload.title))
    save_todos(todos)
    return todo


@app.put("/todos/{todo_id}/subtasks/{subtask_id}", responses=SUBTASK_RESPONSES)
def update_subtask(todo_id: int, subtask_id: int, payload: SubtaskUpdate) -> TodoItem:
    todos = load_todos()
    todo = todos[find_index(todos, todo_id)]
    todo.subtasks[find_subtask_index(todo, subtask_id)] = Subtask(id=subtask_id, **payload.model_dump())
    save_todos(todos)
    return todo


@app.delete("/todos/{todo_id}/subtasks/{subtask_id}", status_code=204, responses=SUBTASK_RESPONSES)
def delete_subtask(todo_id: int, subtask_id: int) -> None:
    todos = load_todos()
    todo = todos[find_index(todos, todo_id)]
    del todo.subtasks[find_subtask_index(todo, subtask_id)]
    save_todos(todos)


# ---------- 작업 템플릿 — 재사용은 화면에서 추가 폼을 채우는 방식 (apply API 없음) ----------

TEMPLATE_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"description": "Template not found"},
    409: {"description": "이미 있는 템플릿 이름"},
}


@app.get("/task-templates")
def get_templates() -> list[TaskTemplate]:
    return load_templates()


@app.post("/task-templates", status_code=201, responses={409: TEMPLATE_RESPONSES[409]})
def create_template(payload: TemplateIn) -> TaskTemplate:
    templates = load_templates()
    check_template_name(templates, payload.name)
    template = TaskTemplate(id=max((t.id for t in templates), default=0) + 1, **payload.model_dump())
    save_templates(templates + [template])
    return template


@app.put("/task-templates/{template_id}", responses=TEMPLATE_RESPONSES)
def update_template(template_id: int, payload: TemplateIn) -> TaskTemplate:
    templates = load_templates()
    index = find_template_index(templates, template_id)
    check_template_name(templates, payload.name, skip_id=template_id)
    templates[index] = TaskTemplate(id=template_id, **payload.model_dump())
    save_templates(templates)
    return templates[index]


@app.delete("/task-templates/{template_id}", status_code=204, responses={404: TEMPLATE_RESPONSES[404]})
def delete_template(template_id: int) -> None:
    templates = load_templates()
    del templates[find_template_index(templates, template_id)]
    save_templates(templates)


@app.get("/", include_in_schema=False)           # 화면 서빙
def read_root() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")
