import calendar
import csv
import io
import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated, Literal, get_args

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import (
    AfterValidator, BaseModel, Field, StrictBool, StringConstraints, field_validator, model_validator,
)
import psycopg
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import auth                                      # (7.0.0) 순수 인증 함수
import db                                        # (7.0.0) DB 함수 — 엔진은 첫 요청 때 만든다(import 시 연결 없음)

BASE_DIR = Path(__file__).resolve().parent       # main.py 가 있는 폴더
TODO_FILE = Path(os.environ.get("TODO_FILE", BASE_DIR / "todo.json"))  # 테스트에서 바꿔 쓸 수 있다
TEMPLATE_FILE = Path(os.environ.get("TEMPLATE_FILE", BASE_DIR / "task_templates.json"))  # 처음 저장할 때 만든다
INDEX_FILE = BASE_DIR / "templates" / "index.html"

if not TODO_FILE.exists():                       # 없으면 빈 목록으로 만들어 둔다
    TODO_FILE.write_text("[]", encoding="utf-8")

logger = logging.getLogger("todo.auth")         # (7.0.0) 인증 경로 로그 — 예외 문자열·traceback·비밀값을 남기지 않는다


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # (7.0.0 Phase 2-E) 시작 가드 — 설정이 틀리면 AuthConfigError 로 시작을 거부한다(메시지에 값 없음)
    app.state.auth = load_auth_config()
    try:
        yield
    finally:
        app.state.auth = None


app = FastAPI(title="To-Do List API", lifespan=lifespan)
app.state.auth = None                            # 시작 가드를 거치기 전에는 인증 경로가 500 (fail-closed)

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

Priority = Literal["높음", "보통", "낮음"]
Recurrence = Literal["none", "daily", "weekly", "monthly"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]  # 할 일·하위 할 일 공용
MAX_TAGS, MAX_TAG_LEN = 10, 20
UNDO_TTL = timedelta(seconds=30)                 # 삭제 후 복구할 수 있는 시간
BULK_MAX = 100                                   # 일괄 작업 한 번에 고를 수 있는 최대 건수
UNDO_MAX = BULK_MAX                              # 복구 버퍼·복구 요청 최대 건수 — 일괄 삭제 한 번을 모두 되돌릴 수 있게
SUBTASK_MAX = 20                                 # 할 일 하나에 둘 수 있는 하위 할 일 수
# 날짜 기준 (통계·내보내기 파일명). Asia/Seoul 은 서머타임이 없어 tzdata 없이 고정 오프셋과 같다
KST = timezone(timedelta(hours=9), "KST")
TREND_DAYS = 30                                  # 완료 추이는 오늘 포함 최근 30일 (7일은 그 끝부분)
WEEK_DAYS = 7
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


class DailyCount(BaseModel):                     # 하루 완료 건수 (Asia/Seoul 날짜)
    date: date
    count: int


class DueCompliance(BaseModel):                  # 마감일 준수 — 분모가 0 이면 rate 는 null
    eligible: int
    on_time: int
    rate: float | None


class StatsOut(BaseModel):                       # 통계 대시보드 — 상위 할 일 기준, 읽기 전용
    today: date
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    total: int
    completed: int
    active: int
    overdue: int
    completion_rate: float
    daily_completions: list[DailyCount]          # 오래된 날부터 오늘까지 TREND_DAYS 개
    completions_7d: int
    completions_30d: int
    trend_excluded: int                          # 완료인데 completed_at 이 없어 추이에서 뺀 수
    active_by_priority: dict[Priority, int]
    due_compliance: DueCompliance


# 삭제 직후 복구용 메모리 버퍼 (id → 항목, 오래된 것부터). 재시작하면 비워진다
RECENTLY_DELETED: dict[int, DeletedEntry] = {}


def now() -> datetime:
    return datetime.now(timezone.utc)


def kst_date(moment: datetime) -> date:
    # 시간대가 없는 시각(예전 데이터)은 UTC 로 본다
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KST).date()


def today_kst() -> date:
    return kst_date(now())


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


# ---------- 통계 대시보드 — 읽기 전용, 날짜는 Asia/Seoul 기준 ----------

def percent(part: int, whole: int) -> float | None:
    # 소수 1자리, 반올림은 사사오입 (파이썬 round 의 은행가 반올림을 쓰지 않는다)
    if whole == 0:
        return None
    return float((Decimal(part * 100) / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def compute_stats(todos: list[TodoItem], today: date) -> StatsOut:
    completed = [t for t in todos if t.completed]
    active = [t for t in todos if not t.completed]

    # 완료 추이 — completed_at 이 있는 완료 항목만, 완료 시각의 Asia/Seoul 날짜로 센다
    days = [today - timedelta(days=n) for n in range(TREND_DAYS - 1, -1, -1)]
    counts = dict.fromkeys(days, 0)
    for todo in completed:
        if todo.completed_at is not None and (day := kst_date(todo.completed_at)) in counts:
            counts[day] += 1

    # 마감일 준수 — 마감일과 완료 시각이 모두 있는 완료 항목 중 마감일 당일까지 끝낸 비율
    eligible = [t for t in completed if t.due_date is not None and t.completed_at is not None]
    on_time = sum(kst_date(t.completed_at) <= t.due_date for t in eligible)

    return StatsOut(
        today=today,
        total=len(todos),
        completed=len(completed),
        active=len(active),
        overdue=sum(t.due_date is not None and t.due_date < today for t in active),
        completion_rate=percent(len(completed), len(todos)) or 0.0,
        daily_completions=[DailyCount(date=d, count=c) for d, c in counts.items()],
        completions_7d=sum(list(counts.values())[-WEEK_DAYS:]),
        completions_30d=sum(counts.values()),
        trend_excluded=sum(t.completed_at is None for t in completed),
        active_by_priority={p: sum(t.priority == p for t in active) for p in get_args(Priority)},
        due_compliance=DueCompliance(eligible=len(eligible), on_time=on_time,
                                     rate=percent(on_time, len(eligible))),
    )


@app.get("/stats")                               # 통계 — 저장 파일·복구 버퍼를 바꾸지 않는다
def get_stats() -> StatsOut:
    return compute_stats(load_todos(), today_kst())


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


# ────────────────────────── (7.0.0 Phase 2-E) 인증 경로 /auth/* ──────────────────────────
# V7_SPEC §28.1·§29. 기존 v6 데이터 경로에는 아직 인증을 걸지 않는다(Phase 3).
# - 트랜잭션은 라우트가 소유한다. db 함수는 commit 하지 않는다. 남겨야 할 결과(409·429)는 블록을 정상 종료(commit)한 뒤
#   블록 밖에서 응답하고, 503·500 은 블록 안에서 예외로 빠져나가 rollback 한다.
# - 로그인: Tx1(선기록) → 트랜잭션 밖 Argon2 → Tx2(자기 선기록 삭제·rehash·세션) 또는 보상 Tx (§29.5).
# - 오류 응답·로그에 예외 문자열·DETAIL·traceback·비밀번호·토큰·해시를 넣지 않는다.

LOGIN_FIELD_MAX = 1024                           # 로그인 본문 구조 상한 — 넘으면 422, 그 안의 규칙 위반은 401 (§28.1)
LOGIN_FAILED = "아이디 또는 비밀번호가 올바르지 않습니다"
AUTH_REQUIRED = "로그인이 필요합니다"
CSRF_INVALID = "CSRF 토큰이 올바르지 않습니다"
ORIGIN_INVALID = "요청 출처가 올바르지 않습니다"
REGISTRATION_DISABLED = "회원가입이 비활성화되어 있습니다"
USERNAME_TAKEN = "이미 사용 중인 아이디입니다"
TOO_MANY_ATTEMPTS = "시도 횟수를 초과했습니다. 잠시 후 다시 시도해 주세요"
SERVICE_BUSY = "요청이 많아 처리하지 못했습니다. 잠시 후 다시 시도해 주세요"
SERVER_ERROR = "서버 오류가 발생했습니다"
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True)
class AuthConfig:                                # 시작 가드가 확인한 설정 (app.state.auth)
    settings: auth.AuthSettings
    failure_key: auth.FailureKey


@dataclass(frozen=True)
class CurrentUser:                               # 유효한 세션의 사용자 (세션 토큰·해시는 담지 않는다)
    id: int
    username: str
    csrf_token: str


class LoginIn(BaseModel):                        # 구조만 검사 — username 형식·비밀번호 길이 위반은 401 (§28.1)
    username: str = Field(max_length=LOGIN_FIELD_MAX)
    password: str = Field(max_length=LOGIN_FIELD_MAX)


class RegisterIn(BaseModel):                     # 가입 규칙 위반은 422 (응답에 입력값을 넣지 않는다)
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return auth.normalize_username(value)

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return auth.check_password(value)


class AuthOut(BaseModel):
    id: int
    username: str
    csrf_token: str


class _LoginStateError(RuntimeError):
    """Tx2 에서 자기 선기록이 없음 — 내부 불일치. rollback 후 500 (세션 없음)."""


def load_auth_config(environ: Mapping[str, str] | None = None) -> AuthConfig:
    # 시작 가드: APP_ENV·COOKIE_SECURE·REGISTRATION_ENABLED, AUTH_FAILURE_KEY, 운영이면 DATABASE_URL 존재(연결하지 않음)
    env = os.environ if environ is None else environ
    settings = auth.load_settings(env)
    failure_key = auth.load_failure_key(env)
    if settings.app_env == "production" and not env.get("DATABASE_URL"):
        raise auth.AuthConfigError("APP_ENV=production 에서는 DATABASE_URL 이 필요합니다")
    if failure_key.development_fallback:
        logger.warning("auth_failure_key_public_fallback app_env=%s", settings.app_env)
    return AuthConfig(settings=settings, failure_key=failure_key)


def auth_config(request: Request) -> AuthConfig:
    # 시작 가드를 거친 설정. 없으면(가드 미실행) 인증을 처리하지 않는다
    config = getattr(request.app.state, "auth", None)
    if not isinstance(config, AuthConfig):
        logger.warning("auth_not_configured")
        raise HTTPException(500, SERVER_ERROR)
    return config


def auth_session_factory() -> Callable[[], Session]:
    # 트랜잭션마다 새 Session — 테스트에서 dependency_overrides 로 바꾼다. 엔진은 처음 부를 때 만든다
    return lambda: db.get_sessionmaker()()


def _sqlstate(exc: BaseException) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def _auth_failure(exc: Exception, event: str) -> HTTPException:
    # 예상한 실패(HashBusy·55P03)는 503, 그 밖은 500. 로그에는 사건·예외 종류·SQLSTATE 만 (str(exc)·traceback 없음)
    logger.warning("%s exc_type=%s sqlstate=%s", event, type(exc).__name__, _sqlstate(exc))
    if isinstance(exc, auth.HashBusy) or db.is_lock_timeout(exc):
        return HTTPException(503, SERVICE_BUSY)
    return HTTPException(500, SERVER_ERROR)


def _guarded(call: Callable[[], Response], event: str) -> Response:
    # 인증 라우트 본문 — HTTPException 만 그대로 내보내고, 다른 예외는 여기서 끝낸다(서버 기본 traceback 로그 없음)
    try:
        return call()
    except HTTPException:
        raise
    except Exception as exc:                     # noqa: BLE001 — 401·409 로 위장하지 않고 503·500 으로만 바꾼다
        raise _auth_failure(exc, event) from None


def _client_ip(request: Request) -> str:
    # 신뢰된 ASGI client 주소만 쓴다 (X-Forwarded-* 를 읽지 않는다). IP 가 아니면 InvalidClientAddress → 500
    return auth.normalize_client_ip(request.client.host if request.client else None)


def require_same_origin(request: Request) -> None:
    # 로그인·가입 CSRF 방지 — Origin(없으면 Referer)이 이 요청의 스킴·호스트·포트와 같아야 한다 (본문 검증보다 먼저)
    expected = auth.request_origin(request.url.scheme, request.headers.get("host"))
    if not auth.origin_allowed(request.headers.get("origin"), request.headers.get("referer"), expected):
        raise HTTPException(403, ORIGIN_INVALID)


class RequestDb:
    """(Phase 3-1A) 요청 하나의 Session·트랜잭션 — 처음 session() 을 부를 때 연다(쿠키 없는 401 은 DB 를 열지 않는다).

    commit·rollback 은 request_db 의존성만 한다. 헬퍼·핸들러·require_user 는 commit 하지 않는다.
    Session 은 auth_session_factory 로 만든다(테스트의 dependency_overrides 지점을 그대로 쓴다).
    """

    def __init__(self, factory: Callable[[], Session]) -> None:
        self._factory = factory
        self._stack: ExitStack | None = None
        self._session: Session | None = None

    @property
    def opened(self) -> bool:
        return self._session is not None

    def session(self) -> Session:
        if self._session is None:
            stack = ExitStack()
            try:
                session = stack.enter_context(self._factory())
                stack.enter_context(session.begin())
            except BaseException:
                stack.close()
                raise
            self._stack, self._session = stack, session
        return self._session

    def finish(self, exc: BaseException | None) -> None:
        # 예외가 없으면 commit, 있으면 rollback. 어느 쪽이든 Session 을 닫는다. 열지 않았으면 아무것도 하지 않는다
        stack, self._stack, self._session = self._stack, None, None
        if stack is None:
            return
        if exc is None:
            stack.__exit__(None, None, None)
        else:
            stack.__exit__(type(exc), exc, exc.__traceback__)


DB_ERRORS = (SQLAlchemyError, psycopg.Error)    # (R-27) 요청 Session 경계에서 고정 500 으로 바꾸는 DB 예외만
_SQLSTATE_CHARS = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _safe_sqlstate(exc: BaseException) -> str | None:
    # 드라이버 예외(psycopg) 또는 SQLAlchemy 래퍼(.orig)의 SQLSTATE — 5자리 영숫자일 때만 (다른 값은 로그에 넣지 않는다)
    value = getattr(exc, "sqlstate", None)
    if value is None:
        value = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if isinstance(value, str) and len(value) == 5 and set(value) <= _SQLSTATE_CHARS:
        return value
    return None


def _log_db_event(event: str, exc: BaseException) -> None:
    # 로그는 고정 사건명·예외 클래스명·SQLSTATE 만 — str(exc)·repr·traceback·exc_info 를 남기지 않는다 (R-27)
    logger.warning("%s exc_type=%s sqlstate=%s", event, type(exc).__name__, _safe_sqlstate(exc))


def _rollback(holder: RequestDb, exc: BaseException) -> None:
    try:
        holder.finish(exc)                       # rollback + Session close
    except Exception as cleanup:                 # noqa: BLE001 — rollback 실패는 기록만, 원래 처리 결과를 바꾸지 않는다
        _log_db_event("request_db_rollback_failed", cleanup)


def request_db(factory: Callable[[], Session] = Depends(auth_session_factory)) -> Iterator[RequestDb]:
    # 일반 데이터 요청의 Session 1개·트랜잭션 1개 (V7_SPEC §29.4). 반드시 RequestDbDep(scope="function")로만 참조한다 —
    # scope 가 다르면 FastAPI 의존성 캐시 키가 달라져 Session 이 2개가 된다. "function" 이면 응답을 보내기 전에 끝난다.
    # 예외 처리 (R-27):
    # - HTTPException(401·403·404·409 등)·검증 오류(422)·일반 파이썬 오류 → rollback 후 원래 예외를 그대로 올린다
    # - DB 예외(SQLAlchemyError·psycopg.Error) → rollback 후 고정 500. 원래 예외는 연결하지 않는다(from None)
    # - commit 중 DB 예외 → 고정 500 (성공 응답을 보내지 않는다). commit 중 다른 예외는 그대로 올린다
    # 500 은 except 블록 밖에서 만든다 — 원래 DB 예외가 __context__ 로도 붙지 않게
    holder = RequestDb(factory)
    db_failed = False
    try:
        yield holder
    except DB_ERRORS as exc:
        _log_db_event("request_db_error", exc)
        _rollback(holder, exc)
        db_failed = True
    except BaseException as exc:
        _rollback(holder, exc)
        raise
    if not db_failed:
        try:
            holder.finish(None)                  # commit — 성공 응답보다 먼저
        except DB_ERRORS as exc:                 # commit 실패면 성공 응답을 보내지 않는다
            _log_db_event("request_db_commit_failed", exc)
            db_failed = True
    if db_failed:
        raise HTTPException(500, SERVER_ERROR)


RequestDbDep = Annotated[RequestDb, Depends(request_db, scope="function")]   # 요청 Session 은 이 별칭으로만 받는다


def _load_current_user(request: Request, config: AuthConfig, rdb: RequestDb) -> CurrentUser | None:
    token = request.cookies.get(config.settings.cookie_name)
    if token is None:
        return None                              # DB 를 열지 않는다
    found = db.get_active_session(rdb.session(), token, now())   # 데이터 작업과 같은 Session·트랜잭션
    if found is None:
        return None
    user_session, user = found
    return CurrentUser(id=user.id, username=user.username, csrf_token=user_session.csrf_token)


def require_user(request: Request, rdb: RequestDbDep, config: AuthConfig = Depends(auth_config)) -> CurrentUser:
    # 유효한 세션이 없으면 401. DB 오류는 500·503. 요청 Session(rdb)을 핸들러와 함께 쓴다 (데이터 경로 연결은 3-4·3-5)
    try:
        user = _load_current_user(request, config, rdb)
    except Exception as exc:                     # noqa: BLE001
        raise _auth_failure(exc, "auth_session_error") from None
    if user is None:
        raise HTTPException(401, AUTH_REQUIRED)
    return user


def require_csrf(request: Request, user: CurrentUser = Depends(require_user)) -> CurrentUser:
    # GET·HEAD 가 아닌 인증 요청은 X-CSRF-Token 이 세션 값과 같아야 한다 (Phase 3 에서 데이터 경로에 건다)
    if request.method not in ("GET", "HEAD") and not auth.csrf_matches(user.csrf_token,
                                                                       request.headers.get(CSRF_HEADER)):
        raise HTTPException(403, CSRF_INVALID)
    return user


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    # 인증 경로만 입력값(input)·ctx 를 빼고 위치·메시지·종류만 돌려준다(비밀번호 원문 노출 방지). v6 경로는 기본 처리 그대로
    if request.url.path.startswith("/auth/"):
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
                  for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
    return await request_validation_exception_handler(request, exc)


def _auth_response(status_code: int, user_id: int, username: str, csrf: str, token: str,
                   config: AuthConfig) -> Response:
    # commit 이 끝난 뒤에만 부른다 — 본문에 세션 토큰·해시를 넣지 않는다
    response = JSONResponse(status_code=status_code,
                            content=AuthOut(id=user_id, username=username, csrf_token=csrf).model_dump())
    response.set_cookie(value=token, **auth.cookie_kwargs(config.settings))   # type: ignore[arg-type]
    return response


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(429, TOO_MANY_ATTEMPTS, headers={"Retry-After": str(int(retry_after))})


AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"description": "로그인 실패·미로그인"}, 403: {"description": "Origin·CSRF 불일치, 가입 비활성"},
    409: {"description": "username 중복"}, 429: {"description": "시도 제한 (Retry-After)"},
    500: {"description": "서버 오류"}, 503: {"description": "해시 슬롯 혼잡·잠금 대기 초과"},
}


@app.post("/auth/register", status_code=201, response_model=AuthOut, responses=AUTH_RESPONSES,
          dependencies=[Depends(require_same_origin)])
def register(payload: RegisterIn, request: Request, config: AuthConfig = Depends(auth_config),
             factory: Callable[[], Session] = Depends(auth_session_factory)) -> Response:
    # Origin(403) → 본문(422) → 비활성(403) → IP → 시도 제한(429) → 중복(409)·가입(201). 201·409 만 시도로 센다
    def run() -> Response:
        if not config.settings.registration_enabled:
            raise HTTPException(403, REGISTRATION_DISABLED)
        client_ip = _client_ip(request)
        moment = now()
        outcome: tuple = ()
        with factory() as session, session.begin():          # 가입 트랜잭션 1개 (해시 동안 연결 유지 — R-30)
            attempt = db.begin_register_attempt(session, client_ip, moment)
            if not attempt.decision.allowed:
                outcome = ("blocked", attempt.decision.retry_after)          # 기록 없음
            elif db.username_exists(session, payload.username):
                outcome = ("taken",)                                         # 시도 기록은 commit 해서 센다
            else:
                password_hash = auth.hash_password_limited(payload.password)  # HashBusy → rollback(세지 않음) → 503
                try:
                    user_id = db.create_user(session, payload.username, password_hash, moment)
                except db.UsernameTaken:
                    outcome = ("taken",)                                     # 경쟁 — SAVEPOINT 로 바깥 트랜잭션 유지
                else:
                    db.purge_expired_sessions(session, moment)
                    token, csrf = db.create_session(
                        session, user_id, moment, replace_token=request.cookies.get(config.settings.cookie_name))
                    outcome = ("created", user_id, token, csrf)
        # 여기부터는 commit 이 끝난 뒤다
        if outcome[0] == "blocked":
            raise _too_many(outcome[1])
        if outcome[0] == "taken":
            raise HTTPException(409, USERNAME_TAKEN)
        _, user_id, token, csrf = outcome
        return _auth_response(201, user_id, payload.username, csrf, token, config)

    return _guarded(run, "auth_register_error")


@app.post("/auth/login", response_model=AuthOut, responses=AUTH_RESPONSES,
          dependencies=[Depends(require_same_origin)])
def login(payload: LoginIn, request: Request, config: AuthConfig = Depends(auth_config),
          factory: Callable[[], Session] = Depends(auth_session_factory)) -> Response:
    # Tx1(선기록) → 트랜잭션 밖 Argon2 → Tx2(성공) 또는 보상 Tx(HashBusy). 실패 유형과 관계없이 같은 401
    def run() -> Response:
        client_ip = _client_ip(request)
        name = auth.login_name(payload.username, config.failure_key)
        with factory() as session, session.begin():          # Tx1 — 끝나면 commit, Session·연결 반환
            attempt = db.begin_login_attempt(session, name, client_ip, now())
        if not attempt.decision.allowed:
            raise _too_many(attempt.decision.retry_after)
        try:
            verified = auth.verify_login_password(attempt.password_hash, payload.password)   # DB 연결·잠금 없음
        except auth.HashBusy:
            _cancel_attempt(factory, name, attempt.attempt_id)
            raise
        if not verified or attempt.user_id is None or attempt.password_hash is None:
            raise HTTPException(401, LOGIN_FAILED)                           # 선기록은 실패로 남는다
        new_hash = _rehash(attempt.password_hash, payload.password)          # 트랜잭션 밖. 못 하면 None
        moment = now()
        with factory() as session, session.begin():          # Tx2 — 실패하면 rollback, 선기록은 남고 세션 없음
            if not db.finish_login_success(session, name, attempt.attempt_id):
                raise _LoginStateError()
            if new_hash is not None:
                db.update_password_hash_if_unchanged(session, attempt.user_id, attempt.password_hash, new_hash)
            db.purge_expired_sessions(session, moment)
            token, csrf = db.create_session(
                session, attempt.user_id, moment, replace_token=request.cookies.get(config.settings.cookie_name))
        return _auth_response(200, attempt.user_id, name.username or "", csrf, token, config)

    return _guarded(run, "auth_login_error")


def _cancel_attempt(factory: Callable[[], Session], name: auth.LoginName, attempt_id: int | None) -> None:
    # 보상 Tx — 검증에 들어가지 못한 시도의 자기 선기록만 지운다. 실패해도 응답은 503 (선기록은 남는다)
    if attempt_id is None:
        return
    try:
        with factory() as session, session.begin():
            db.cancel_login_attempt(session, name, attempt_id)
    except Exception as exc:                     # noqa: BLE001
        logger.warning("auth_compensation_failed exc_type=%s sqlstate=%s", type(exc).__name__, _sqlstate(exc))


def _rehash(stored_hash: str, password: str) -> str | None:
    # 해시 설정이 바뀐 경우만 새 해시 — 트랜잭션 밖, 슬롯 안. 슬롯 혼잡·규칙 불일치면 건너뛴다(로그인은 진행)
    try:
        if not auth.needs_rehash(stored_hash):
            return None
        return auth.hash_password_limited(password)
    except (auth.HashBusy, ValueError) as exc:
        logger.warning("auth_rehash_skipped exc_type=%s", type(exc).__name__)
        return None


@app.post("/auth/logout", status_code=204, responses=AUTH_RESPONSES)
def logout(request: Request, config: AuthConfig = Depends(auth_config),
           factory: Callable[[], Session] = Depends(auth_session_factory)) -> Response:
    # 세션이 있으면 CSRF 헤더가 맞아야 지운다(아니면 403). 세션이 없거나 만료·형식 오류여도 204 + 쿠키 삭제
    def run() -> Response:
        token = request.cookies.get(config.settings.cookie_name)
        if token is not None:
            with factory() as session, session.begin():
                found = db.get_active_session(session, token, now())
                if found is not None:
                    if not auth.csrf_matches(found[0].csrf_token, request.headers.get(CSRF_HEADER)):
                        raise HTTPException(403, CSRF_INVALID)
                    db.delete_session(session, token)
        response = Response(status_code=204)
        response.delete_cookie(**auth.cookie_delete_kwargs(config.settings))   # type: ignore[arg-type]
        return response

    return _guarded(run, "auth_logout_error")


@app.get("/auth/me", response_model=AuthOut, responses=AUTH_RESPONSES)
def me(user: CurrentUser = Depends(require_user)) -> AuthOut:
    return AuthOut(id=user.id, username=user.username, csrf_token=user.csrf_token)
