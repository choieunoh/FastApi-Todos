"""PostgreSQL 연결·세션·ORM 모델 (7.0.0).

Phase 1-A: 스키마와 연결 설정만 둔다. 아직 main.py 는 이 모듈을 쓰지 않는다(저장소는 v6 JSON 그대로).
- 시각은 TIMESTAMPTZ(UTC). 연결마다 TimeZone=UTC 로 고정해 응답에 다른 오프셋이 붙지 않게 한다.
- 시각 값은 DB now() 가 아니라 파이썬(main.now())에서 넣는다 — 테스트의 시계 고정을 그대로 쓰기 위해서다.
- 열거값은 PG ENUM 대신 VARCHAR + CHECK (Alembic 으로 값 바꾸기 쉽다).
- 스키마 변경은 Alembic 리비전으로만 한다. 앱이 create_all 을 부르지 않는다.
"""
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from sqlalchemy import (
    CHAR, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Integer,
    LargeBinary, MetaData, String, Text, UniqueConstraint, create_engine, func, text,
)
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
