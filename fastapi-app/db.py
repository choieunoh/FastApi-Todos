"""PostgreSQL 연결·세션·ORM 모델 (7.0.0).

Phase 1-A: 스키마와 연결 설정만 둔다. 아직 main.py 는 이 모듈을 쓰지 않는다(저장소는 v6 JSON 그대로).
- 시각은 TIMESTAMPTZ(UTC). 연결마다 TimeZone=UTC 로 고정해 응답에 다른 오프셋이 붙지 않게 한다.
- 시각 값은 DB now() 가 아니라 파이썬(main.now())에서 넣는다 — 테스트의 시계 고정을 그대로 쓰기 위해서다.
- 열거값은 PG ENUM 대신 VARCHAR + CHECK (Alembic 으로 값 바꾸기 쉽다).
- 스키마 변경은 Alembic 리비전으로만 한다. 앱이 create_all 을 부르지 않는다.
"""
import base64
import binascii
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache

from sqlalchemy import (
    CHAR, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Integer,
    LargeBinary, MetaData, String, Text, UniqueConstraint, create_engine, delete, func, insert, select, text,
)

import auth                                      # 순수 함수만 (auth 는 db 를 import 하지 않는다)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, Session, mapped_column, sessionmaker

DRIVER = "postgresql+psycopg"                    # psycopg 3
TEST_DB_SUFFIX = "_test"                         # 테스트는 이름이 이걸로 끝나는 DB 에만 연결한다

# 제약 이름을 고정해 모델과 마이그레이션이 같은 이름을 쓰게 한다
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

PRIORITY_CHECK = "priority IN ('높음', '보통', '낮음')"
RECURRENCE_CHECK = "recurrence IN ('none', 'daily', 'weekly', 'monthly')"
EMPTY_TEXT_ARRAY = text("'{}'::text[]")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_timestamp(nullable: bool) -> MappedColumn:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


class User(Base):                                # 계정 — username 은 소문자로 정규화해 저장한다
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    username: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)     # Argon2id PHC 문자열
    created_at: Mapped[datetime] = utc_timestamp(nullable=False)


class UserSession(Base):                         # 로그인 세션 — 쿠키 토큰 원문은 저장하지 않는다
    __tablename__ = "sessions"
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)   # SHA-256(토큰)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_timestamp(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(                       # created_at + 14일 (연장 없음)
        DateTime(timezone=True), nullable=False, index=True)


class Todo(Base):                                # 할 일 — v6 TodoItem 필드 + 소유자·순서·삭제 대기
    __tablename__ = "todos"
    __table_args__ = (
        CheckConstraint(PRIORITY_CHECK, name="priority"),
        CheckConstraint(RECURRENCE_CHECK, name="recurrence"),
        CheckConstraint("recurrence = 'none' OR due_date IS NOT NULL", name="recurrence_needs_due_date"),
        UniqueConstraint("user_id", "position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)  # 이전 시 원래 id 유지
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(BigInteger, nullable=False)   # 목록 순서 (삭제해도 바꾸지 않는다)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    priority: Mapped[str] = mapped_column(String(2), nullable=False, server_default="보통")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=EMPTY_TEXT_ARRAY)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    recurrence: Mapped[str] = mapped_column(String(7), nullable=False, server_default="none")
    next_spawned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime | None] = utc_timestamp(nullable=True)    # v1~v3 데이터는 null
    completed_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    deleted_at: Mapped[datetime | None] = utc_timestamp(nullable=True)    # 실행 취소 대기 (30초)


class Subtask(Base):                             # 하위 할 일 — id 는 그 할 일 안의 sid
    __tablename__ = "subtasks"

    todo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("todos.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class TaskTemplate(Base):                        # 작업 템플릿 — 이름 중복은 같은 사용자 안에서만 막는다
    __tablename__ = "task_templates"
    __table_args__ = (
        CheckConstraint(PRIORITY_CHECK, name="priority"),
        CheckConstraint(RECURRENCE_CHECK, name="recurrence"),
        UniqueConstraint("user_id", "position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)  # 이전 시 원래 id 유지
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    priority: Mapped[str] = mapped_column(String(2), nullable=False, server_default="보통")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=EMPTY_TEXT_ARRAY)
    recurrence: Mapped[str] = mapped_column(String(7), nullable=False, server_default="none")
    subtasks: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=EMPTY_TEXT_ARRAY)


# 대소문자 무시 이름 중복 방지 (함수 인덱스는 테이블 밖에서 선언한다)
Index("uq_task_templates_user_id_lower_name", TaskTemplate.user_id, func.lower(TaskTemplate.name), unique=True)


class DataMigration(Base):                       # JSON → DB 이전 기록 — 같은 원본을 두 번 넣지 않는다
    __tablename__ = "data_migrations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    todo_count: Mapped[int] = mapped_column(Integer, nullable=False)
    template_count: Mapped[int] = mapped_column(Integer, nullable=False)
    migrated_at: Mapped[datetime] = utc_timestamp(nullable=False)


class AuthFailure(Base):                         # (Phase 2-A) 로그인 실패·가입 시도 기록 — 시도 제한(§29.5)용, 24시간 뒤 정리
    __tablename__ = "auth_failures"
    __table_args__ = (
        CheckConstraint("kind IN ('login', 'register')", name="kind"),
        CheckConstraint("kind = 'login' OR username_key IS NULL", name="register_has_no_username"),  # 가입 기록은 null
        Index("ix_auth_failures_kind_username_key_created_at", "kind", "username_key", "created_at"),
        Index("ix_auth_failures_kind_client_ip_created_at", "kind", "client_ip", "created_at"),
        Index("ix_auth_failures_created_at", "created_at"),       # 24시간 지난 기록 정리
    )

    # users 와 연결하지 않는다 — 없는 계정에 대한 시도도 기록해야 한다
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # (2-D) 정규화한 username 의 HMAC 30자(auth.FailureKey) — 평문 username 은 저장하지 않는다. 형식 오류·가입은 null
    username_key: Mapped[str | None] = mapped_column(String(30), nullable=True)
    client_ip: Mapped[str] = mapped_column(String(45), nullable=False)   # auth.normalize_client_ip (IPv4 주소, IPv6 /64)
    created_at: Mapped[datetime] = utc_timestamp(nullable=False)


# ---------- 연결 설정 ----------

class DatabaseConfigError(RuntimeError):
    pass


# PostgreSQL 테스트(스키마 삭제·마이그레이션 왕복)를 허용하는 유일한 대상 — 로컬 PostgreSQL 18.6 (V7_PLAN.md §P1)
# 다른 환경에서 돌려야 하면 이 값을 고치기 전에 사용자 승인을 받는다
TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 5433
TEST_DB_NAME = "todo_test"
TEST_DB_USER = "todo_test_user"
TEST_DB_SERVER_MAJOR = 18
TEST_DB_TARGET = f"{TEST_DB_USER}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"

# 테스트 DB 가 아닌 곳의 downgrade 확인값 (둘 다 필요)
DOWNGRADE_CONFIRM_X_ARG = "downgrade_confirm"    # alembic -x downgrade_confirm=<DB 이름>
DOWNGRADE_CONFIRM_ENV = "TODO_DOWNGRADE_CONFIRM"  # 환경변수 = <DB 이름>@<서버 주소>:<포트>

SERVER_FACTS_SQL = text(
    "SELECT current_database(), current_user, host(inet_server_addr()), inet_server_port(), "
    "current_setting('server_version_num'), (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)")


@dataclass(frozen=True)
class ServerFacts:                               # 연결한 서버가 스스로 알려 준 값
    database: str
    user: str
    address: str | None                          # 유닉스 소켓이면 None
    port: int | None
    version_num: int                             # 예: 180006
    is_superuser: bool


def normalize_url(raw: str | URL) -> URL:
    # postgresql:// 나 postgres:// 로 적어도 psycopg 3 드라이버를 쓴다. PostgreSQL 외에는 받지 않는다
    url = make_url(raw)
    if url.get_backend_name() not in ("postgresql", "postgres"):
        raise DatabaseConfigError("PostgreSQL URL 만 지원합니다")
    if not url.database:
        raise DatabaseConfigError("DB 이름이 없는 URL 입니다")
    return url.set(drivername=DRIVER)


def database_url() -> URL:
    # 기본값을 두지 않는다 — 설정을 빠뜨렸을 때 엉뚱한 DB 에 붙지 않게
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise DatabaseConfigError("DATABASE_URL 이 설정되지 않았습니다")
    return normalize_url(raw)


def check_test_database_url(raw: str | URL, app_url: str | URL | None = None) -> URL:
    # PostgreSQL 테스트(스키마를 지웠다 만든다)는 정해진 대상 하나에만 연결한다 (운영·개발 DB, PostgreSQL 16 보호)
    # 오류 메시지에는 비밀번호를 넣지 않는다
    url = normalize_url(raw)
    if not url.database.endswith(TEST_DB_SUFFIX) or url.database == TEST_DB_SUFFIX:
        raise DatabaseConfigError(f"테스트 DB 이름은 '{TEST_DB_SUFFIX}' 로 끝나야 합니다: {url.database}")
    if url.query:                                # ?host= ?port= ?service= 등으로 대상을 바꿀 수 없게
        raise DatabaseConfigError("TEST_DATABASE_URL 에는 쿼리 매개변수를 쓸 수 없습니다")
    if url.port is None:                         # 생략하면 libpq 기본값 5432(다른 서버)로 연결된다
        raise DatabaseConfigError(f"TEST_DATABASE_URL 의 포트를 생략할 수 없습니다 (:{TEST_DB_PORT} 필요)")
    if (url.username, url.host, url.port, url.database) != (TEST_DB_USER, TEST_DB_HOST, TEST_DB_PORT, TEST_DB_NAME):
        raise DatabaseConfigError(
            f"PostgreSQL 테스트 대상은 {TEST_DB_TARGET} 만 허용합니다 "
            f"(받은 값: {url.username}@{url.host}:{url.port}/{url.database})")
    if app_url:
        app = normalize_url(app_url)
        if (app.host, app.port, app.database) == (url.host, url.port, url.database):
            raise DatabaseConfigError("TEST_DATABASE_URL 이 DATABASE_URL 과 같은 DB 를 가리킵니다")
    return url


def read_server_facts(connection: Connection) -> ServerFacts:
    # 실제로 붙은 서버를 묻는다 (읽기 전용 SELECT 하나)
    row = connection.execute(SERVER_FACTS_SQL).one()
    return ServerFacts(database=row[0], user=row[1], address=row[2], port=row[3],
                       version_num=int(row[4]), is_superuser=bool(row[5]))


def check_test_server_facts(facts: ServerFacts) -> None:
    # URL 이 맞아도 실제 서버가 다르면 멈춘다 (PGHOSTADDR·프록시·포트 착오 대비)
    problems = []
    if facts.version_num // 10000 != TEST_DB_SERVER_MAJOR:
        problems.append(f"서버 버전 {facts.version_num} (필요: {TEST_DB_SERVER_MAJOR}.x)")
    if facts.port != TEST_DB_PORT:
        problems.append(f"포트 {facts.port} (필요: {TEST_DB_PORT})")
    if facts.address != TEST_DB_HOST:
        problems.append(f"서버 주소 {facts.address} (필요: {TEST_DB_HOST})")
    if facts.database != TEST_DB_NAME:
        problems.append(f"DB {facts.database} (필요: {TEST_DB_NAME})")
    if facts.user != TEST_DB_USER:
        problems.append(f"계정 {facts.user} (필요: {TEST_DB_USER})")
    if facts.is_superuser:
        problems.append("슈퍼유저 계정 (테스트 계정은 슈퍼유저가 아니어야 한다)")
    if problems:
        raise DatabaseConfigError("테스트 DB 서버 확인 실패: " + ", ".join(problems))


def verify_test_server(connection: Connection) -> ServerFacts:
    # 스키마를 지우거나 되돌리기 전에 반드시 부른다 — 같은 연결에서 확인한 뒤 작업한다
    facts = read_server_facts(connection)
    check_test_server_facts(facts)
    return facts


# ---------- 테스트 DB 스키마 사전 게이트 (R-19, Phase 2-A) ----------
# DROP SCHEMA public CASCADE 는 스키마 소유자라면 다른 소유자의 객체까지 지운다. 그래서 지우기 전에
# 테스트 DB 안의 객체가 "빈 상태" 또는 "이 프로젝트 리비전이 만든 상태"와 정확히 같은지 읽기 전용으로 확인한다.
# 리비전별 객체 목록은 고정값이다 — tests/test_db_schema.py 가 Alembic 오프라인 SQL 과 같은지 확인한다.

def _relations(tables: set[str], sequences: set[str], indexes: set[str]) -> frozenset[tuple[str, str]]:
    return frozenset({(t, "r") for t in tables} | {(s, "S") for s in sequences} | {(i, "i") for i in indexes})


_BASE_RELATIONS = _relations({"alembic_version"}, set(), {"alembic_version_pkc"})
_0001_RELATIONS = _BASE_RELATIONS | _relations(
    {"users", "sessions", "todos", "subtasks", "task_templates", "data_migrations"},
    {"users_id_seq", "todos_id_seq", "task_templates_id_seq", "data_migrations_id_seq"},
    {"pk_users", "uq_users_username", "pk_sessions", "ix_sessions_user_id", "ix_sessions_expires_at",
     "pk_todos", "uq_todos_user_id_position", "pk_subtasks", "pk_task_templates",
     "uq_task_templates_user_id_position", "uq_task_templates_user_id_lower_name",
     "pk_data_migrations", "uq_data_migrations_source_sha256"})
_0002_RELATIONS = _0001_RELATIONS | _relations(
    {"auth_failures"}, {"auth_failures_id_seq"},
    {"pk_auth_failures", "ix_auth_failures_kind_username_key_created_at",
     "ix_auth_failures_kind_client_ip_created_at", "ix_auth_failures_created_at"})

# alembic_version 값(없으면 base: 표는 있고 행이 없다) → public 의 관계 (이름, relkind)
TEST_SCHEMA_MANIFESTS: dict[str | None, frozenset[tuple[str, str]]] = {
    None: _BASE_RELATIONS,
    "0001": _0001_RELATIONS,
    "0002": _0002_RELATIONS,
}
TEST_SCHEMA_ALLOWED_PUBLIC_OWNERS = frozenset({"pg_database_owner", TEST_DB_USER})
TEST_SCHEMA_ALLOWED_EXTENSIONS = frozenset({"plpgsql"})

_USER_SCHEMA = ("n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
                "AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'")
# 모두 읽기 전용 SELECT 다 (tests/test_db_schema.py 가 확인한다)
TEST_SCHEMA_SQL = {
    "database_owner": "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()",
    "schemas": f"SELECT n.nspname, pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE {_USER_SCHEMA}",
    "relations": ("SELECT n.nspname, c.relname, c.relkind::text, pg_get_userbyid(c.relowner) FROM pg_class c "
                  f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_USER_SCHEMA}"),
    "functions": ("SELECT n.nspname, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                  f"WHERE {_USER_SCHEMA}"),
    "types": ("SELECT n.nspname, t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
              f"WHERE {_USER_SCHEMA}"),
    "extensions": "SELECT extname FROM pg_extension",
    "triggers": ("SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                 f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal AND {_USER_SCHEMA}"),
    "event_triggers": "SELECT evtname FROM pg_event_trigger",
    "other_sessions": ("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                       "AND pid <> pg_backend_pid() AND backend_type = 'client backend'"),
    # pg_stat_activity 는 pg_read_all_stats 가 없으면 다른 역할 세션의 datname 을 숨긴다(2-A3 실측).
    # numbackends 는 역할과 관계없이 이 DB 에 붙은 모든 백엔드 수라 교차 확인에 쓴다 (점검 연결 자신 포함)
    "database_backends": "SELECT numbackends FROM pg_stat_database WHERE datname = current_database()",
    "has_alembic_version": "SELECT to_regclass('public.alembic_version') IS NOT NULL",
    "alembic_versions": "SELECT version_num FROM public.alembic_version",
}


@dataclass(frozen=True)
class SchemaInventory:                           # 테스트 DB 안의 객체 목록 — 이름·종류·소유자·개수만 (데이터 내용 없음)
    database_owner: str
    schemas: frozenset[tuple[str, str]]          # (스키마, 소유자) — 시스템 스키마 제외
    relations: frozenset[tuple[str, str, str, str]]   # (스키마, 이름, relkind, 소유자)
    functions: frozenset[tuple[str, str]]
    types: frozenset[tuple[str, str]]
    extensions: frozenset[str]
    triggers: frozenset[tuple[str, str]]
    event_triggers: frozenset[str]
    other_sessions: int                          # 이 DB 에 붙은 다른 클라이언트 세션 (같은 역할만 보인다)
    database_backends: object                    # pg_stat_database.numbackends (모든 역할, 자신 포함) — 정확히 1 이어야 한다
    alembic_versions: tuple[str, ...] | None     # None: alembic_version 표 없음
    row_counts: tuple[tuple[str, int], ...] = ()  # 알려진 테이블만, 보고용


def _single_value(result_rows: list) -> object:
    # 정확히 한 행·한 열일 때만 그 값을, 아니면 None (판정에서 거부된다)
    return result_rows[0][0] if len(result_rows) == 1 and len(result_rows[0]) == 1 else None


def read_test_schema_inventory(connection: Connection) -> SchemaInventory:
    # 읽기 전용 SELECT 만 실행한다. 권한 문제 등으로 실패하면 예외가 그대로 올라가 호출한 쪽이 멈춘다
    def rows(key: str) -> list:
        return connection.execute(text(TEST_SCHEMA_SQL[key])).all()

    has_version = bool(connection.execute(text(TEST_SCHEMA_SQL["has_alembic_version"])).scalar_one())
    relations = frozenset(tuple(r) for r in rows("relations"))
    known_tables = {name for manifest in TEST_SCHEMA_MANIFESTS.values() for name, kind in manifest if kind == "r"}
    present = sorted(name for schema, name, kind, _ in relations if schema == "public" and kind == "r")
    counts = tuple((name, int(connection.execute(text(f'SELECT count(*) FROM public."{name}"')).scalar_one()))
                   for name in present if name in known_tables)   # 이름은 고정 목록에서만 — 모르는 표는 세지 않는다
    return SchemaInventory(
        database_owner=connection.execute(text(TEST_SCHEMA_SQL["database_owner"])).scalar_one(),
        schemas=frozenset(tuple(r) for r in rows("schemas")),
        relations=relations,
        functions=frozenset(tuple(r) for r in rows("functions")),
        types=frozenset(tuple(r) for r in rows("types")),
        extensions=frozenset(r[0] for r in rows("extensions")),
        triggers=frozenset(tuple(r) for r in rows("triggers")),
        event_triggers=frozenset(r[0] for r in rows("event_triggers")),
        other_sessions=int(connection.execute(text(TEST_SCHEMA_SQL["other_sessions"])).scalar_one()),
        database_backends=_single_value(rows("database_backends")),
        alembic_versions=tuple(r[0] for r in rows("alembic_versions")) if has_version else None,
        row_counts=counts,
    )


def check_test_schema_inventory(inv: SchemaInventory,
                                manifests: Mapping[str | None, frozenset[tuple[str, str]]] = TEST_SCHEMA_MANIFESTS,
                                ) -> str:
    # 순수 판정 — "empty" 또는 리비전("base", "0001", "0002")을 돌려주고, 하나라도 어긋나면 모든 이유를 모아 멈춘다
    problems: list[str] = []
    if inv.other_sessions:
        problems.append(f"이 DB 에 다른 세션 {inv.other_sessions}개")
    backends = inv.database_backends
    if type(backends) is not int:                # None·문자열·실수·bool 은 모두 거부 (bool 은 int 의 하위형)
        problems.append(f"DB 전체 연결 수를 확인할 수 없음 ({type(backends).__name__})")
    elif backends != 1:
        problems.append(f"DB 전체 연결 수 {backends} (점검 연결 1개만 허용, 다른 역할 세션 포함)")
    if inv.database_owner != TEST_DB_USER:
        problems.append(f"DB 소유자 {inv.database_owner}")
    if {s for s, _ in inv.schemas} != {"public"}:
        problems.append(f"사용자 스키마 {sorted(s for s, _ in inv.schemas)} (public 하나만 허용)")
    for schema, owner in inv.schemas:
        if schema == "public" and owner not in TEST_SCHEMA_ALLOWED_PUBLIC_OWNERS:
            problems.append(f"public 소유자 {owner}")
    if inv.extensions - TEST_SCHEMA_ALLOWED_EXTENSIONS:
        problems.append(f"확장 {sorted(inv.extensions - TEST_SCHEMA_ALLOWED_EXTENSIONS)}")
    if inv.functions:
        problems.append(f"함수 {sorted(inv.functions)}")
    if inv.triggers:
        problems.append(f"트리거 {sorted(inv.triggers)}")
    if inv.event_triggers:
        problems.append(f"이벤트 트리거 {sorted(inv.event_triggers)}")
    outside = sorted((s, n) for s, n, _, _ in inv.relations if s != "public")
    if outside:
        problems.append(f"public 밖의 관계 {outside}")
    owners = sorted({o for _, _, _, o in inv.relations} - {TEST_DB_USER})
    if owners:
        problems.append(f"다른 소유자의 관계 {owners}")

    actual = frozenset((n, k) for s, n, k, _ in inv.relations if s == "public")
    state = "?"
    if not actual and inv.alembic_versions is None:
        state = "empty"
    elif inv.alembic_versions is None:
        problems.append("alembic_version 표 없이 다른 객체가 있음")
    elif len(inv.alembic_versions) > 1:
        problems.append(f"alembic_version 행 {len(inv.alembic_versions)}개")
    else:
        revision = inv.alembic_versions[0] if inv.alembic_versions else None
        if revision not in manifests:
            problems.append(f"모르는 리비전 {revision}")
        elif actual != manifests[revision]:
            missing = sorted(manifests[revision] - actual)
            extra = sorted(actual - manifests[revision])
            problems.append(f"리비전 {revision or 'base'} 구조와 다름 (없음 {missing}, 추가 {extra})")
        else:
            state = revision or "base"
    tables = {n for n, k in actual if k == "r"}
    expected_types = {("public", t) for t in tables} | {("public", f"_{t}") for t in tables}   # 테이블 행 타입과 배열 타입
    if inv.types != expected_types:
        problems.append(f"타입이 테이블 행 타입과 다름 (추가 {sorted(inv.types - expected_types)}, "
                        f"없음 {sorted(expected_types - inv.types)})")
    if problems:
        raise DatabaseConfigError("테스트 DB 스키마 사전 확인 실패 (삭제하지 않음): " + "; ".join(problems))
    return state


def verify_test_schema(connection: Connection) -> str:
    # DROP SCHEMA 직전, verify_test_server 다음에 같은 연결·트랜잭션에서 부른다
    return check_test_schema_inventory(read_test_schema_inventory(connection))


def check_downgrade_allowed(url: URL, facts: ServerFacts, x_args: Mapping[str, str],
                            environ: Mapping[str, str]) -> None:
    # 테스트 대상(URL + 실제 서버 모두 확인)이면 허용한다
    try:
        check_test_database_url(url)
        check_test_server_facts(facts)
        return
    except DatabaseConfigError:
        pass
    # 그 밖의 DB(todo_dev 포함)는 확인값 두 개가 실제 서버와 모두 맞아야 한다 (V7_PLAN.md §P1.2 승인 절차)
    token = f"{facts.database}@{facts.address}:{facts.port}"
    if x_args.get(DOWNGRADE_CONFIRM_X_ARG) == facts.database and environ.get(DOWNGRADE_CONFIRM_ENV) == token:
        return
    raise DatabaseConfigError(
        f"테스트 DB 가 아닌 '{facts.database}' 에서는 downgrade 를 막습니다. "
        "V7_PLAN.md §P1.2 의 승인 절차(백업 → 사용자 승인 → 확인값 2개)를 따르세요")


def guard_downgrade(connection: Connection, x_args: Mapping[str, str], *, offline: bool,
                    environ: Mapping[str, str] | None = None) -> None:
    # Alembic 리비전의 downgrade() 첫 줄에서 부른다. 오프라인(--sql)은 SQL 만 출력하고 DB 를 바꾸지 않으므로 통과
    if offline:
        return
    facts = read_server_facts(connection)
    check_downgrade_allowed(connection.engine.url, facts, x_args, os.environ if environ is None else environ)


def make_engine(url: URL | str, **kwargs: object) -> Engine:
    # 엔진을 만들어도 바로 연결하지 않는다 (첫 요청 때 연결). kwargs 는 poolclass 등 추가 옵션
    url = normalize_url(url) if isinstance(url, str) else url
    return create_engine(
        url,
        pool_pre_ping=True,                      # 끊긴 연결을 재사용하지 않는다
        connect_args={"options": "-c TimeZone=UTC"},   # 응답 시각이 항상 UTC 로 나오게
        hide_parameters=True,                    # (2-C) 오류 문자열·로그에 바인드 값(CSRF 토큰·해시 등)을 넣지 않는다
        **kwargs,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine(database_url())


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    # FastAPI 의존성 — 요청마다 세션 1개·트랜잭션 1개. 예외가 나면 전부 되돌린다
    with get_sessionmaker()() as session, session.begin():
        yield session


# ---------- 로그인 세션 저장 (Phase 2-C, V7_SPEC §29.2) ----------
# - 쿠키 토큰 원문은 반환값으로만 내보내고 DB 에는 SHA-256(auth.hash_token)만 저장한다.
# - 모든 함수는 호출자의 Session·트랜잭션 안에서 동작한다. commit·rollback 하지 않고 새 엔진·연결도 만들지 않는다.
# - DB 오류는 잡지 않고 그대로 올린다 (오류가 나도 인증 성공으로 처리하지 않는다).
# - 시각은 호출자가 넘기는 시간대 있는 now 만 받는다 (DB now() 를 쓰지 않는다).
# - HTTP(쿠키 읽기·401·require_user·CSRF 검사)는 Phase 2-E(main.py)에서 연결한다.

SESSION_TOKEN_LENGTH = 43                        # auth.new_session_token() = token_urlsafe(32)
_SESSION_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)


def is_valid_session_token(token: object) -> bool:
    # 외부(쿠키)에서 온 값이 우리가 만드는 형식과 정확히 같은지 — 정규화·잘라내기 없이 그대로 본다
    if not isinstance(token, str) or len(token) != SESSION_TOKEN_LENGTH or not _SESSION_TOKEN_RE.fullmatch(token):
        return False
    try:
        raw = base64.urlsafe_b64decode(token + "=")
    except (binascii.Error, ValueError):
        return False
    # 마지막 글자의 남는 비트까지 같아야 한다 (같은 32바이트를 가리키는 다른 표기를 받지 않는다)
    return len(raw) == auth.TOKEN_BYTES and base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == token


def _require_aware(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("시간대가 있는 시각(now)이어야 합니다")


def create_session(db: Session, user_id: int, now: datetime, *, replace_token: str | None = None) -> tuple[str, str]:
    # 새 세션을 만들고 (세션 토큰 원문, CSRF 토큰)을 돌려준다. 만료는 now + 14일 고정
    # replace_token: 재로그인 때 그 요청 쿠키의 세션만 지운다 (형식이 틀리면 아무것도 지우지 않는다, 다른 기기 세션은 유지)
    _require_aware(now)                          # 시간대 없는 now 는 아무것도 바꾸기 전에 ValueError
    expires_at = auth.session_expires_at(now)
    if replace_token is not None and is_valid_session_token(replace_token):
        db.execute(delete(UserSession).where(UserSession.token_hash == auth.hash_token(replace_token)))
    token, csrf = auth.new_session_token(), auth.new_csrf_token()
    db.add(UserSession(token_hash=auth.hash_token(token), user_id=user_id, csrf_token=csrf,
                       created_at=now, expires_at=expires_at))
    db.flush()                                   # 제약 위반을 이 자리에서 드러낸다 (commit 은 호출자)
    return token, csrf


def get_active_session(db: Session, token: object, now: datetime) -> tuple[UserSession, User] | None:
    # 유효한 세션과 그 사용자를 돌려준다. 형식이 틀린 토큰은 DB 조회 없이 None. 읽기만 한다(만료 행도 지우지 않는다)
    _require_aware(now)
    if not is_valid_session_token(token):
        return None
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == auth.hash_token(token), UserSession.expires_at > now)   # 같으면 만료
    ).one_or_none()
    if row is None or auth.is_expired(row[0].expires_at, now):   # 같은 규칙을 파이썬에서도 한 번 더
        return None
    return row[0], row[1]


def delete_session(db: Session, token: object) -> bool:
    # 로그아웃 — 지웠으면 True, 없거나 형식이 틀리면 False (형식이 틀리면 DELETE 를 보내지 않는다). 여러 번 불러도 같다
    if not is_valid_session_token(token):
        return False
    result = db.execute(delete(UserSession).where(UserSession.token_hash == auth.hash_token(token)))
    return result.rowcount > 0


def purge_expired_sessions(db: Session, now: datetime) -> int:
    # 모든 사용자의 만료 세션(expires_at <= now)을 지우고 지운 수를 돌려준다 (로그인·가입 때 부른다)
    _require_aware(now)
    return db.execute(delete(UserSession).where(UserSession.expires_at <= now)).rowcount


# ---------- 로그인·가입 시도 제한 (Phase 2-D, V7_SPEC §29.5) ----------
# 로그인은 트랜잭션을 나눈다 (CLAUDE.md 의 로그인 경로 예외). 트랜잭션 구성·commit 은 호출자(2-E main.py)가 한다:
#   Tx1  begin_login_attempt   — 잠금 → 검사 → (허용이면) 선기록 INSERT → 해시 조회 → COMMIT
#        (트랜잭션 밖) auth.verify_login_password — Argon2 동안 DB 연결·잠금을 잡지 않는다
#   성공 Tx2  finish_login_success — 같은 잠금 → 자기 선기록 id 만 삭제 → (세션 생성) → COMMIT
#   실패      아무것도 하지 않는다 — 선기록이 실패 기록으로 남는다
#   슬롯 대기 초과(auth.HashBusy)  보상 Tx  cancel_login_attempt — 같은 잠금 → 자기 선기록 id 만 삭제 → COMMIT
# - 이전 실패와 다른 요청의 선기록은 지우지 않는다. 그래서 "검증에 들어간 시도는 성공이 확인되기 전까지 기록이 남는다"
#   (같은 username 의 판정이 정확하려면 이 불변식이 필요하다). 프로세스 종료·Tx2 실패면 선기록은 실패로 남는다(fail-closed).
# - 전제: READ COMMITTED, 잠금은 SET LOCAL 다음 첫 문장. 같은 username 은 잠금으로 직렬화, IP 규칙은 근사(잠금 없음).
# - 모든 함수는 commit·rollback 하지 않고 DB 오류를 그대로 올린다. 키는 auth.LoginName 의 HMAC 값만 쓴다.

AUTH_LOCK_TIMEOUT = "2s"                         # Tx1·Tx2 는 ms 단위 — 오래 기다리면 오류(55P03)로 실패(fail-closed)
AUTH_FAILURE_PURGE_BATCH = 1000                  # 한 번에 정리하는 24시간 지난 기록 수
LOGIN_LOCK_PREFIX = "login:"
_SET_AUTH_LOCK_TIMEOUT = text(f"SET LOCAL lock_timeout = '{AUTH_LOCK_TIMEOUT}'")
_SHOW_ISOLATION = text("SHOW transaction_isolation")
_LOGIN_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_prefix || :username_key, 0))")


@dataclass(frozen=True)
class LoginAttempt:                              # begin_login_attempt 결과
    decision: auth.LimitDecision
    attempt_id: int | None                       # 선기록 id — 차단(429)이면 None (기록하지 않는다)
    user_id: int | None                          # 계정이 없거나 형식 오류면 None
    password_hash: str | None = field(repr=False)


@dataclass(frozen=True)
class RegisterAttempt:                           # begin_register_attempt 결과
    decision: auth.LimitDecision
    attempt_id: int | None


def _begin_auth_transaction(db: Session) -> None:
    # 잠금 대기 상한 + READ COMMITTED 확인 (다른 격리 수준이면 잠금 뒤에도 앞 요청의 기록이 안 보일 수 있다)
    db.execute(_SET_AUTH_LOCK_TIMEOUT)
    level = db.execute(_SHOW_ISOLATION).scalar_one()
    if level != "read committed":
        raise DatabaseConfigError("시도 제한은 READ COMMITTED 트랜잭션에서만 동작합니다")


def lock_login_username(db: Session, username_key: str) -> None:
    # 같은 username(HMAC 키)의 시도를 트랜잭션 끝까지 직렬화한다 (xact 잠금 — 커밋·롤백 때 풀린다). 값은 바인드로만
    if not isinstance(username_key, str) or len(username_key) != auth.USERNAME_KEY_LENGTH:
        raise ValueError("username_key 형식이 아닙니다")
    db.execute(_LOGIN_LOCK_SQL.bindparams(lock_prefix=LOGIN_LOCK_PREFIX, username_key=username_key))


def _newest_failure_times(db: Session, *conditions: object, since: datetime, limit: int) -> list[datetime]:
    # 창 안(created_at > since)의 최신 limit 개 시각 — auth.limit_decision 에 충분하다
    return list(db.execute(
        select(AuthFailure.created_at).where(*conditions, AuthFailure.created_at > since)
        .order_by(AuthFailure.created_at.desc()).limit(limit)
    ).scalars())


def _login_rules(db: Session, name: auth.LoginName, client_ip: str, now: datetime) -> list[auth.LimitRule]:
    since = now - auth.LOGIN_WINDOW
    login = AuthFailure.kind == "login"
    rules = []
    if name.failure_key is not None:
        by_user = AuthFailure.username_key == name.failure_key
        rules.append(auth.LimitRule(auth.LOGIN_LIMIT_USER_IP, auth.LOGIN_WINDOW, _newest_failure_times(
            db, login, by_user, AuthFailure.client_ip == client_ip, since=since, limit=auth.LOGIN_LIMIT_USER_IP)))
        rules.append(auth.LimitRule(auth.LOGIN_LIMIT_USER, auth.LOGIN_WINDOW, _newest_failure_times(
            db, login, by_user, since=since, limit=auth.LOGIN_LIMIT_USER)))
    rules.append(auth.LimitRule(auth.LOGIN_LIMIT_IP, auth.LOGIN_WINDOW, _newest_failure_times(
        db, login, AuthFailure.client_ip == client_ip, since=since, limit=auth.LOGIN_LIMIT_IP)))
    return rules


def _check_login_name(name: auth.LoginName) -> None:
    if not isinstance(name, auth.LoginName) or (name.username is None) != (name.failure_key is None):
        raise ValueError("auth.login_name 결과가 필요합니다")


def _record_attempt(db: Session, kind: str, username_key: str | None, client_ip: str, now: datetime) -> int:
    purge_old_auth_failures(db, now)             # 기록할 때 정리한다 (§29.5)
    return db.execute(insert(AuthFailure).values(
        kind=kind, username_key=username_key, client_ip=client_ip, created_at=now,
    ).returning(AuthFailure.id)).scalar_one()


def begin_login_attempt(db: Session, name: auth.LoginName, client_ip: str, now: datetime) -> LoginAttempt:
    # Tx1. 차단이면 아무것도 쓰지 않는다(429 는 기록하지 않음). 허용이면 선기록을 넣고 해시를 읽는다.
    # username 형식 오류(name 이 None 쌍)는 잠금·사용자 조회 없이 IP 규칙만 — 계정이 될 수 없는 입력이다
    _require_aware(now)
    _check_login_name(name)
    _begin_auth_transaction(db)
    if name.failure_key is not None:
        lock_login_username(db, name.failure_key)
    decision = auth.limit_decision(_login_rules(db, name, client_ip, now), now)
    if not decision.allowed:
        return LoginAttempt(decision, None, None, None)
    attempt_id = _record_attempt(db, "login", name.failure_key, client_ip, now)
    row = None
    if name.username is not None:
        row = db.execute(select(User.id, User.password_hash).where(User.username == name.username)).one_or_none()
    return LoginAttempt(decision, attempt_id, row[0] if row else None, row[1] if row else None)


def _delete_own_login_attempt(db: Session, name: auth.LoginName, attempt_id: int) -> bool:
    _check_login_name(name)
    if not isinstance(attempt_id, int) or isinstance(attempt_id, bool):
        raise ValueError("선기록 id 가 필요합니다")
    _begin_auth_transaction(db)
    if name.failure_key is not None:
        lock_login_username(db, name.failure_key)
        same_key = AuthFailure.username_key == name.failure_key
    else:
        same_key = AuthFailure.username_key.is_(None)
    # 자기 선기록 id 하나만 — 이전 실패·다른 요청의 선기록은 지우지 않는다
    result = db.execute(delete(AuthFailure).where(
        AuthFailure.id == attempt_id, AuthFailure.kind == "login", same_key))
    return result.rowcount > 0


def finish_login_success(db: Session, name: auth.LoginName, attempt_id: int) -> bool:
    # 성공 Tx2 의 앞부분 — 같은 잠금 아래에서 자기 선기록만 지운다. 세션 생성(create_session)은 같은 Tx2 에서 호출자가
    if name.failure_key is None:
        raise ValueError("형식이 틀린 username 은 로그인에 성공할 수 없습니다")
    return _delete_own_login_attempt(db, name, attempt_id)


def cancel_login_attempt(db: Session, name: auth.LoginName, attempt_id: int) -> bool:
    # 보상 Tx — Argon2 슬롯을 얻지 못해(HashBusy) 검증에 들어가지 않은 시도의 자기 선기록만 지운다.
    # 실패하면(예외) 선기록은 남는다 — 인증은 어느 쪽이든 하지 않는다
    return _delete_own_login_attempt(db, name, attempt_id)


def begin_register_attempt(db: Session, client_ip: str, now: datetime) -> RegisterAttempt:
    # 가입은 IP 1시간 창만 (잠금 없음, 근사). 허용이면 시도를 기록한다 — 201·409 로 끝나면 커밋해 센다.
    # 422·403 은 이 함수 전에 끝나고, 429 는 기록하지 않으며, 503(HashBusy)은 호출자가 트랜잭션을 되돌려 세지 않는다
    _require_aware(now)
    _begin_auth_transaction(db)
    since = now - auth.REGISTER_WINDOW
    decision = auth.limit_decision([auth.LimitRule(auth.REGISTER_LIMIT_IP, auth.REGISTER_WINDOW, _newest_failure_times(
        db, AuthFailure.kind == "register", AuthFailure.client_ip == client_ip,
        since=since, limit=auth.REGISTER_LIMIT_IP))], now)
    if not decision.allowed:
        return RegisterAttempt(decision, None)
    return RegisterAttempt(decision, _record_attempt(db, "register", None, client_ip, now))


def purge_old_auth_failures(db: Session, now: datetime, limit: int = AUTH_FAILURE_PURGE_BATCH) -> int:
    # 24시간 지난 기록(created_at <= now − 24h)을 최대 limit 개 지운다. 다른 트랜잭션이 잡은 행은 건너뛴다(SKIP LOCKED)
    _require_aware(now)
    old = (select(AuthFailure.id).where(AuthFailure.created_at <= now - auth.FAILURE_RETENTION)
           .order_by(AuthFailure.id).limit(limit).with_for_update(skip_locked=True))
    return db.execute(delete(AuthFailure).where(AuthFailure.id.in_(old))).rowcount
