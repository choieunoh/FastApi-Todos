"""(7.0.0 Phase 1-A) PostgreSQL 없이 도는 스키마·설정 검사.

- ORM 모델의 테이블·열·제약을 확인한다 (DDL 은 postgresql 방언으로 컴파일만 한다).
- Alembic 은 오프라인 모드(--sql)로 SQL 만 만들어 모델과 같은 DDL 인지 비교한다. DB 에는 연결하지 않는다.
- 실제 DB 에서 확인할 것(적용·되돌리기·제약 동작·autogenerate 비교)은 test_db_postgres.py 에 있다.
"""
import ast
import dataclasses
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Identity
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import db

APP_DIR = Path(__file__).resolve().parent.parent
OFFLINE_URL = "postgresql://offline@localhost/offline_check"   # 오프라인 SQL 생성용 (연결하지 않는다)
DIALECT = postgresql.dialect()

TABLES = {"users", "sessions", "todos", "subtasks", "task_templates", "data_migrations", "auth_failures"}


def alembic_config(buffer: io.StringIO | None = None) -> Config:
    return Config(str(APP_DIR / "alembic.ini"), output_buffer=buffer)


def offline_sql(monkeypatch, *revisions: str, downgrade: bool = False) -> str:
    # DB 연결 없이 마이그레이션 SQL 을 만든다
    monkeypatch.setenv("DATABASE_URL", OFFLINE_URL)
    monkeypatch.chdir(APP_DIR)
    buffer = io.StringIO()
    if downgrade:
        command.downgrade(alembic_config(buffer), *revisions, sql=True)
    else:
        command.upgrade(alembic_config(buffer), *revisions, sql=True)
    return buffer.getvalue()


def statements(sql: str) -> list[str]:
    # 오프라인 출력의 "-- Running upgrade ..." 같은 주석 줄을 빼고 문장 단위로 나눈다
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in code.split(";") if s.strip()]


def normalize(statement: str) -> tuple[str, frozenset[str]] | str:
    # CREATE TABLE 은 (머리, 본문 항목 집합)으로 바꿔 순서·공백 차이를 무시한다
    statement = " ".join(statement.split())
    match = re.match(r"(CREATE TABLE \w+) \((.*)\)$", statement)
    if not match:
        return statement
    items = re.split(r",\s(?![^()]*\))", match.group(2))   # 괄호 안의 쉼표(CHECK, 복합 키)는 나누지 않는다
    return match.group(1), frozenset(i.strip() for i in items)


def model_ddl() -> set:
    result = set()
    for table in db.Base.metadata.sorted_tables:
        result.add(normalize(str(CreateTable(table).compile(dialect=DIALECT))))
        for index in table.indexes:
            result.add(normalize(str(CreateIndex(index).compile(dialect=DIALECT))))
    return result


# ---------- ORM 모델 ----------

def test_tables():
    assert set(db.Base.metadata.tables) == TABLES


def test_todos_columns_match_v6_fields_plus_owner_order_and_soft_delete():
    columns = db.Base.metadata.tables["todos"].columns
    v6_fields = {"id", "title", "description", "completed", "priority", "due_date", "tags",
                 "favorite", "recurrence", "next_spawned", "created_at", "completed_at"}
    assert set(columns.keys()) == v6_fields | {"user_id", "position", "deleted_at"}
    nullable = {name for name, c in columns.items() if c.nullable}
    assert nullable == {"due_date", "created_at", "completed_at", "deleted_at"}   # v1~v3 데이터의 null 시각 허용


def test_todo_fields_cover_v6_model():
    # v6 TodoItem 의 필드가 모두 열로 있다 (subtasks 는 별도 테이블)
    import main
    columns = set(db.Base.metadata.tables["todos"].columns.keys())
    assert set(main.TodoItem.model_fields) - {"subtasks"} <= columns
    template_columns = set(db.Base.metadata.tables["task_templates"].columns.keys())
    assert set(main.TaskTemplate.model_fields) <= template_columns


def test_column_lengths_match_validation_rules():
    t = db.Base.metadata.tables
    assert t["todos"].c.title.type.length == 100
    assert t["todos"].c.description.type.length == 500
    assert t["subtasks"].c.title.type.length == 100
    assert t["task_templates"].c.name.type.length == 50
    assert t["users"].c.username.type.length == 30


def test_timestamps_are_timezone_aware():
    for table in db.Base.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith("_at"):
                assert column.type.timezone is True, f"{table.name}.{column.name}"


def test_identity_columns():
    t = db.Base.metadata.tables
    # 할 일·템플릿은 이전 때 원래 id 를 넣어야 하므로 BY DEFAULT, 나머지는 ALWAYS
    assert isinstance(t["todos"].c.id.identity, Identity) and t["todos"].c.id.identity.always is False
    assert t["task_templates"].c.id.identity.always is False
    assert t["users"].c.id.identity.always is True
    assert t["data_migrations"].c.id.identity.always is True
    assert t["subtasks"].c.id.identity is None and t["subtasks"].c.id.autoincrement is False


def test_constraint_names_and_rules():
    ddl = "\n".join(str(CreateTable(t).compile(dialect=DIALECT)) for t in db.Base.metadata.sorted_tables)
    for expected in [
        "CONSTRAINT ck_todos_priority CHECK (priority IN ('높음', '보통', '낮음'))",
        "CONSTRAINT ck_todos_recurrence CHECK (recurrence IN ('none', 'daily', 'weekly', 'monthly'))",
        "CONSTRAINT ck_todos_recurrence_needs_due_date CHECK (recurrence = 'none' OR due_date IS NOT NULL)",
        "CONSTRAINT uq_todos_user_id_position UNIQUE (user_id, position)",
        "CONSTRAINT uq_task_templates_user_id_position UNIQUE (user_id, position)",
        "CONSTRAINT ck_task_templates_priority CHECK",
        "CONSTRAINT uq_users_username UNIQUE (username)",
        "CONSTRAINT pk_subtasks PRIMARY KEY (todo_id, id)",
        "CONSTRAINT uq_data_migrations_source_sha256 UNIQUE (source_sha256)",
    ]:
        assert expected in ddl, expected


def test_foreign_keys_cascade_from_users_and_todos():
    t = db.Base.metadata.tables
    cascade = {(fk.parent.table.name, fk.column.table.name): fk.ondelete
               for table in t.values() for fk in table.foreign_keys}
    assert cascade == {
        ("sessions", "users"): "CASCADE",
        ("todos", "users"): "CASCADE",
        ("subtasks", "todos"): "CASCADE",
        ("task_templates", "users"): "CASCADE",
        ("data_migrations", "users"): None,          # 이전 기록은 계정 삭제로 함께 지우지 않는다
    }


def test_template_name_unique_per_user_case_insensitive():
    index = next(i for i in db.Base.metadata.tables["task_templates"].indexes
                 if i.name == "uq_task_templates_user_id_lower_name")
    assert index.unique
    assert str(CreateIndex(index).compile(dialect=DIALECT)) == (
        "CREATE UNIQUE INDEX uq_task_templates_user_id_lower_name ON task_templates (user_id, lower(name))")


def test_auth_failures_columns():
    # (Phase 2-A) 시도 제한 기록 — V7_SPEC §27.2
    t = db.Base.metadata.tables["auth_failures"]
    assert list(t.columns.keys()) == ["id", "kind", "username_key", "client_ip", "created_at"]
    assert {c.name for c in t.columns if c.nullable} == {"username_key"}
    assert (t.c.kind.type.length, t.c.username_key.type.length, t.c.client_ip.type.length) == (8, 30, 45)
    assert t.c.id.identity.always is True
    assert t.c.created_at.type.timezone is True
    assert not t.foreign_keys                      # 없는 계정에 대한 시도도 기록한다


def test_auth_failures_checks_and_indexes():
    t = db.Base.metadata.tables["auth_failures"]
    ddl = str(CreateTable(t).compile(dialect=DIALECT))
    assert "CONSTRAINT ck_auth_failures_kind CHECK (kind IN ('login', 'register'))" in ddl
    assert ("CONSTRAINT ck_auth_failures_register_has_no_username "
            "CHECK (kind = 'login' OR username_key IS NULL)") in ddl
    assert "CONSTRAINT pk_auth_failures PRIMARY KEY (id)" in ddl
    assert {i.name: [c.name for c in i.columns] for i in t.indexes} == {
        "ix_auth_failures_kind_username_key_created_at": ["kind", "username_key", "created_at"],
        "ix_auth_failures_kind_client_ip_created_at": ["kind", "client_ip", "created_at"],
        "ix_auth_failures_created_at": ["created_at"],
    }
    assert not any(i.unique for i in t.indexes)


def test_identifier_names_fit_postgresql_limit():
    # PostgreSQL 은 63바이트를 넘는 이름을 잘라 버린다 — 모델과 마이그레이션 이름이 어긋나지 않게
    names = set()
    for table in db.Base.metadata.tables.values():
        names |= {c.name for c in table.constraints if c.name} | {i.name for i in table.indexes}
    assert names and all(len(n.encode()) <= 63 for n in names), sorted(n for n in names if len(n) > 63)


# ---------- Alembic (오프라인) ----------

def test_single_linear_head():
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0002"]
    assert script.get_revision("0002").down_revision == "0001"
    assert script.get_revision("0001").down_revision is None
    assert [r.revision for r in script.walk_revisions()] == ["0002", "0001"]   # 갈래 없이 한 줄


def test_alembic_ini_has_no_url_and_is_ascii():
    text = (APP_DIR / "alembic.ini").read_bytes()
    assert text.isascii()                               # Windows 에서 cp949 로 읽혀도 깨지지 않게
    assert not re.search(rb"^sqlalchemy\.url\s*=", text, re.MULTILINE)   # 접속 주소·비밀번호를 두지 않는다


def test_offline_upgrade_creates_every_table(monkeypatch):
    sql = offline_sql(monkeypatch, "head")
    for table in TABLES:
        assert f"CREATE TABLE {table} (" in sql
    assert "INSERT INTO alembic_version (version_num) VALUES ('0001')" in sql
    assert re.search(r"UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001'", sql)


def test_migration_ddl_equals_model_ddl(monkeypatch):
    # 0001 이 만드는 테이블·인덱스가 모델과 같다 (제약 이름·기본값·열 타입까지)
    migration = {normalize(s) for s in statements(offline_sql(monkeypatch, "head"))
                 if s.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX"))
                 and "alembic_version" not in s}
    assert migration == model_ddl()


def test_offline_downgrade_drops_everything(monkeypatch):
    sql = offline_sql(monkeypatch, "0001:base", downgrade=True)
    drops = re.findall(r"DROP TABLE (\w+)", sql)
    assert set(drops) == TABLES - {"auth_failures"}             # 0001 이 만든 표 (auth_failures 는 0002)
    assert drops.index("todos") > drops.index("subtasks")      # 외래키 순서
    assert drops.index("users") > drops.index("todos")


def test_offline_0001_to_0002_only_adds_auth_failures(monkeypatch):
    # 0002 는 새 테이블과 인덱스만 만든다 — 0001 의 표·열·제약·데이터는 건드리지 않는다
    sql = offline_sql(monkeypatch, "0001:0002")
    kinds = [" ".join(s.split()[:3]) for s in statements(sql)]
    assert kinds == ["BEGIN", "CREATE TABLE auth_failures",
                     "CREATE INDEX ix_auth_failures_kind_username_key_created_at",
                     "CREATE INDEX ix_auth_failures_kind_client_ip_created_at",
                     "CREATE INDEX ix_auth_failures_created_at",
                     "UPDATE alembic_version SET", "COMMIT"]
    for word in ("DROP", "ALTER", "DELETE", "TRUNCATE", "INSERT", "GRANT", "REVOKE"):
        assert not re.search(rf"\b{word}\b", sql), word
    assert "kind IN ('login', 'register')" in sql and "kind = 'login' OR username_key IS NULL" in sql


def test_offline_0002_to_0001_drops_only_auth_failures(monkeypatch):
    sql = offline_sql(monkeypatch, "0002:0001", downgrade=True)
    assert re.findall(r"DROP TABLE (\w+)", sql) == ["auth_failures"]
    assert sorted(re.findall(r"DROP INDEX (\w+)", sql)) == sorted([
        "ix_auth_failures_created_at", "ix_auth_failures_kind_client_ip_created_at",
        "ix_auth_failures_kind_username_key_created_at"])
    assert "UPDATE alembic_version SET version_num='0001'" in sql
    for word in ("CREATE", "ALTER", "DELETE", "TRUNCATE", "INSERT"):
        assert not re.search(rf"\b{word}\b", sql), word


def test_offline_head_to_base_drops_auth_failures_too(monkeypatch):
    drops = re.findall(r"DROP TABLE (\w+)", offline_sql(monkeypatch, "0002:base", downgrade=True))
    assert set(drops) == TABLES and drops[0] == "auth_failures"


def relations_created_by(sql: str) -> set[tuple[str, str]]:
    # 오프라인 SQL 에서 생기는 관계 (테이블 r, PK·UNIQUE 제약과 인덱스 i, IDENTITY 시퀀스 S)
    result = set()
    for stmt in statements(sql):
        flat = " ".join(stmt.split())
        if m := re.match(r"CREATE TABLE (\w+) \(", flat):
            table = m.group(1)
            result.add((table, "r"))
            result |= {(n, "i") for n in re.findall(r"CONSTRAINT (\w+) (?:PRIMARY KEY|UNIQUE)", flat)}
            result |= {(f"{table}_{c}_seq", "S") for c in re.findall(r"[(,] (\w+) BIGINT GENERATED", flat)}
        elif m := re.match(r"CREATE (?:UNIQUE )?INDEX (\w+)", flat):
            result.add((m.group(1), "i"))
    return result


def test_schema_gate_manifests_match_migrations(monkeypatch):
    # R-19 게이트의 리비전별 고정 목록이 실제 마이그레이션이 만드는 객체와 같다 (리비전을 바꾸면 여기서 걸린다)
    full_0001 = relations_created_by(offline_sql(monkeypatch, "base:0001"))
    assert full_0001 == db.TEST_SCHEMA_MANIFESTS["0001"]
    assert {r for r in full_0001 if r[0].startswith("alembic_version")} == db.TEST_SCHEMA_MANIFESTS[None]
    added = relations_created_by(offline_sql(monkeypatch, "0001:0002"))
    assert db.TEST_SCHEMA_MANIFESTS["0002"] == db.TEST_SCHEMA_MANIFESTS["0001"] | added
    script = ScriptDirectory.from_config(alembic_config())
    assert set(db.TEST_SCHEMA_MANIFESTS) - {None} == {r.revision for r in script.walk_revisions()}
    by_kind = {k: len({n for n, kind in db.TEST_SCHEMA_MANIFESTS["0001"] if kind == k}) for k in "rSi"}
    assert by_kind == {"r": 7, "S": 4, "i": 14}            # Phase 1-B todo_dev·todo_test 실측과 같다


def test_env_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(APP_DIR)
    with pytest.raises(db.DatabaseConfigError):
        command.upgrade(alembic_config(io.StringIO()), "head", sql=True)


# ---------- 연결 설정 ----------

def test_database_url_has_no_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(db.DatabaseConfigError):
        db.database_url()


@pytest.mark.parametrize("raw", ["postgresql://u:p@h/todo_dev", "postgres://u@h:5433/todo_dev",
                                 "postgresql+psycopg://u@h/todo_dev"])
def test_urls_use_psycopg_driver(raw):
    assert db.normalize_url(raw).drivername == "postgresql+psycopg"


@pytest.mark.parametrize("raw", ["sqlite:///todo.db", "mysql://u@h/todo_dev", "postgresql://u@h"])
def test_only_postgresql_urls_with_database(raw):
    with pytest.raises(db.DatabaseConfigError):
        db.normalize_url(raw)


def test_engine_is_lazy_and_forces_utc(monkeypatch):
    # 엔진을 만들 때 연결하지 않는다 — 없는 호스트여도 만들어진다
    captured = {}
    real_create_engine = db.create_engine

    def spy(url, **kwargs):
        captured.update(kwargs)
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(db, "create_engine", spy)
    engine = db.make_engine("postgresql://u@nonexistent.invalid/todo_dev")
    assert engine.url.drivername == "postgresql+psycopg"
    assert captured["connect_args"] == {"options": "-c TimeZone=UTC"}
    assert captured["pool_pre_ping"] is True
    engine.dispose()


def test_password_not_shown_in_engine_repr():
    engine = db.make_engine("postgresql://user:s3cret@nonexistent.invalid/todo_dev")
    assert "s3cret" not in repr(engine) and "s3cret" not in str(engine.url)
    engine.dispose()


# ---------- 테스트 DB 보호 (URL) ----------

TARGET = "postgresql://todo_test_user@127.0.0.1:5433/todo_test"
DEV = "postgresql://todo_dev_user@127.0.0.1:5433/todo_dev"


@pytest.mark.parametrize("raw", [TARGET, "postgresql://todo_test_user:pw@127.0.0.1:5433/todo_test",
                                 "postgres://todo_test_user@127.0.0.1:5433/todo_test",
                                 "postgresql+psycopg://todo_test_user@127.0.0.1:5433/todo_test"])
def test_test_database_url_accepted(raw):
    url = db.check_test_database_url(raw, DEV)
    assert (url.username, url.host, url.port, url.database) == ("todo_test_user", "127.0.0.1", 5433, "todo_test")


@pytest.mark.parametrize("raw", [
    "postgresql://todo_test_user@127.0.0.1:5432/todo_test",          # PostgreSQL 16 포트
    "postgresql://todo_test_user@127.0.0.1/todo_test",               # 포트 생략 → 5432
    "postgresql://todo_test_user@localhost:5433/todo_test",          # 다른 호스트 표기
    "postgresql://todo_test_user@[::1]:5433/todo_test",
    "postgresql://todo_test_user@10.0.0.5:5433/todo_test",           # 다른 호스트
    "postgresql://todo_test_user@127.0.0.1:15432/todo_test",         # Docker 개발 구성 포트
    "postgresql://todo_test_user@127.0.0.1:5433/other_test",         # 다른 테스트 DB
    "postgresql://todo_dev_user@127.0.0.1:5433/todo_test",           # 다른 계정
    "postgresql://127.0.0.1:5433/todo_test",                         # 계정 생략
    "postgresql://todo_test_user@127.0.0.1:5433/todo_test?host=10.0.0.5",
    "postgresql://todo_test_user@127.0.0.1:5433/todo_test?port=5432",
    "postgresql://todo_test_user@127.0.0.1:5433/todo_test?service=x",
    "postgresql://todo_dev_user@127.0.0.1:5433/todo_dev",            # 개발 DB
    "postgresql://u@h:5433/todo", "postgresql://u@h:5433/test_todo",
    "postgresql://u@h:5433/todo_test_backup", "postgresql://u@h:5433/_test", "postgresql://u@h",
    "sqlite:///x_test",
])
def test_non_target_database_url_rejected(raw):
    with pytest.raises(db.DatabaseConfigError):
        db.check_test_database_url(raw)


def test_rejection_message_hides_password():
    with pytest.raises(db.DatabaseConfigError) as e:
        db.check_test_database_url("postgresql://todo_test_user:s3cret@127.0.0.1:5432/todo_test")
    assert "s3cret" not in str(e.value) and "5432" in str(e.value)


def test_test_database_must_differ_from_app_database():
    with pytest.raises(db.DatabaseConfigError):
        db.check_test_database_url(TARGET, "postgresql://todo_dev_user@127.0.0.1:5433/todo_test")
    db.check_test_database_url(TARGET, DEV)


@pytest.mark.parametrize("raw", ["postgresql://u:secretpw@localhost/todo_dev",
                                 "postgresql://todo_test_user:secretpw@127.0.0.1:5432/todo_test",
                                 "postgresql://todo_test_user:secretpw@127.0.0.1/todo_test"])
def test_pytest_refuses_to_start_with_non_target_database(raw):
    # conftest 가 세션 시작 전에 막는다 (수집만 해 보고 DB 에는 연결하지 않는다)
    env = {**os.environ, "TEST_DATABASE_URL": raw}
    env.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "tests/test_db_schema.py"],
        cwd=APP_DIR, env={**env, "PYTHONIOENCODING": "utf-8"}, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "테스트 DB 보호" in output
    assert "secretpw" not in output                    # 비밀번호를 출력하지 않는다


# ---------- 테스트 DB 보호 (접속 후 서버 확인) ----------

GOOD = db.ServerFacts(database="todo_test", user="todo_test_user", address="127.0.0.1", port=5433,
                      version_num=180006, is_superuser=False)
DEV_FACTS = db.ServerFacts(database="todo_dev", user="todo_dev_user", address="127.0.0.1", port=5433,
                           version_num=180006, is_superuser=False)


class FakeConnection:
    # 실제 DB 대신 서버 확인 SELECT 결과만 돌려준다 (실행한 문장을 기록)
    def __init__(self, facts: db.ServerFacts, url: str = TARGET):
        self.row = (facts.database, facts.user, facts.address, facts.port, str(facts.version_num),
                    facts.is_superuser)
        self.engine = type("FakeEngine", (), {"url": db.normalize_url(url)})()
        self.executed: list[str] = []

    def execute(self, statement):
        self.executed.append(str(statement))
        row = self.row
        return type("FakeResult", (), {"one": lambda _self: row})()


def test_server_facts_accepted():
    db.check_test_server_facts(GOOD)
    db.check_test_server_facts(dataclasses.replace(GOOD, version_num=180000))
    db.check_test_server_facts(dataclasses.replace(GOOD, version_num=189999))


@pytest.mark.parametrize("change", [
    {"version_num": 160010}, {"version_num": 170006}, {"version_num": 190000},   # PostgreSQL 16 등
    {"port": 5432}, {"port": None},
    {"address": "10.0.0.5"}, {"address": "::1"}, {"address": None},
    {"database": "todo_dev"}, {"database": "postgres"},
    {"user": "todo_dev_user"}, {"user": "postgres"},
    {"is_superuser": True},
])
def test_server_facts_rejected(change):
    with pytest.raises(db.DatabaseConfigError):
        db.check_test_server_facts(dataclasses.replace(GOOD, **change))


def test_verify_test_server_runs_one_select_only():
    conn = FakeConnection(GOOD)
    assert db.verify_test_server(conn) == GOOD
    assert len(conn.executed) == 1 and conn.executed[0].startswith("SELECT current_database()")
    with pytest.raises(db.DatabaseConfigError):
        db.verify_test_server(FakeConnection(dataclasses.replace(GOOD, port=5432)))


def test_fresh_schema_verifies_server_before_drop():
    # test_db_postgres.py 의 DROP SCHEMA 는 fresh_schema 안에만 있고, 같은 연결의 서버 확인 뒤에 온다
    source = (APP_DIR / "tests" / "test_db_postgres.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {n.name: ast.get_source_segment(source, n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    fresh = functions["fresh_schema"]
    assert source.count("DROP SCHEMA") == fresh.count("DROP SCHEMA") == 1
    order = [fresh.index("db.verify_test_server(conn)"), fresh.index("db.verify_test_schema(conn)"),
             fresh.index("lock_timeout"), fresh.index("DROP SCHEMA")]     # 서버 → 스키마 내용(R-19) → 삭제
    assert order == sorted(order)
    assert "pytest.exit" in fresh and "SQLAlchemyError" in fresh       # 확인할 수 없어도 멈춘다
    engine = functions["engine"]
    order = [engine.index("check_test_database_url"), engine.index("verify_test_server"),
             engine.index("verify_test_schema")]
    assert order == sorted(order)


# ---------- 테스트 DB 스키마 사전 게이트 (R-19) ----------

USER = "todo_test_user"


def inventory(revision: str | None = "0002", **changes) -> db.SchemaInventory:
    # 리비전 상태의 가짜 객체 목록 ("empty" 는 빈 스키마, None 은 base)
    relations = set() if revision == "empty" else db.TEST_SCHEMA_MANIFESTS[revision]
    tables = {n for n, k in relations if k == "r"}
    base = dict(
        database_owner=USER,
        schemas=frozenset({("public", "pg_database_owner")}),
        relations=frozenset(("public", n, k, USER) for n, k in relations),
        functions=frozenset(),
        types=frozenset({("public", t) for t in tables} | {("public", f"_{t}") for t in tables}),
        extensions=frozenset({"plpgsql"}), triggers=frozenset(), event_triggers=frozenset(), other_sessions=0,
        database_backends=1,                     # 점검 연결 자신만
        alembic_versions=None if revision == "empty" else ((revision,) if revision else ()),
    )
    base.update(changes)
    return db.SchemaInventory(**base)


def with_relation(inv: db.SchemaInventory, *extra: tuple[str, str, str, str]) -> db.SchemaInventory:
    return dataclasses.replace(inv, relations=inv.relations | set(extra))


def without_relation(inv: db.SchemaInventory, name: str) -> db.SchemaInventory:
    return dataclasses.replace(inv, relations=frozenset(r for r in inv.relations if r[1] != name))


@pytest.mark.parametrize("revision, state", [("empty", "empty"), (None, "base"), ("0001", "0001"), ("0002", "0002")])
def test_gate_accepts_known_states(revision, state):
    assert db.check_test_schema_inventory(inventory(revision)) == state


def test_gate_accepts_rows_and_test_user_as_public_owner():
    inv = inventory("0002", row_counts=(("users", 3), ("todos", 12)), schemas=frozenset({("public", USER)}))
    assert db.check_test_schema_inventory(inv) == "0002"


GATE_REJECTS = {
    "extra-table": lambda: with_relation(inventory(), ("public", "notes", "r", USER)),
    "extra-view": lambda: with_relation(inventory(), ("public", "v_users", "v", USER)),
    "extra-matview": lambda: with_relation(inventory(), ("public", "mv", "m", USER)),
    "extra-sequence": lambda: with_relation(inventory(), ("public", "orphan_seq", "S", USER)),
    "extra-index": lambda: with_relation(inventory(), ("public", "ix_extra", "i", USER)),
    "missing-index": lambda: without_relation(inventory(), "ix_auth_failures_created_at"),
    "missing-table": lambda: without_relation(inventory("0001"), "data_migrations"),
    "wrong-kind": lambda: with_relation(without_relation(inventory(), "users"), ("public", "users", "v", USER)),
    "other-owner": lambda: dataclasses.replace(inventory(), relations=frozenset(
        (s, n, k, "someone_else" if n == "todos" else o) for s, n, k, o in inventory().relations)),
    "other-schema-table": lambda: with_relation(inventory(), ("other", "t", "r", USER)),
    "extra-schema": lambda: inventory(schemas=frozenset({("public", USER), ("audit", USER)})),
    "no-public-schema": lambda: inventory("empty", schemas=frozenset()),
    "public-owner": lambda: inventory(schemas=frozenset({("public", "postgres")})),
    "db-owner": lambda: inventory(database_owner="postgres"),
    "function": lambda: inventory(functions=frozenset({("public", "f")})),
    "extra-type": lambda: dataclasses.replace(inventory(), types=inventory().types | {("public", "mood")}),
    "missing-type": lambda: dataclasses.replace(inventory(), types=frozenset()),
    "extension": lambda: inventory(extensions=frozenset({"plpgsql", "pgcrypto"})),
    "trigger": lambda: inventory(triggers=frozenset({("todos", "tg_audit")})),
    "event-trigger": lambda: inventory(event_triggers=frozenset({"evt_ddl"})),
    "other-session": lambda: inventory(other_sessions=1),
    "version-mismatch": lambda: inventory("0002", alembic_versions=("0001",)),
    "unknown-revision": lambda: inventory("0002", alembic_versions=("9999",)),
    "two-version-rows": lambda: inventory("0002", alembic_versions=("0001", "0002")),
    "base-with-tables": lambda: inventory("0002", alembic_versions=()),
    "tables-without-version": lambda: inventory("0002", alembic_versions=None),
    "empty-with-version-table": lambda: inventory("empty", alembic_versions=()),
}


@pytest.mark.parametrize("make", list(GATE_REJECTS.values()), ids=list(GATE_REJECTS))
def test_gate_rejects_unexpected_state(make):
    with pytest.raises(db.DatabaseConfigError, match="삭제하지 않음"):
        db.check_test_schema_inventory(make())


@pytest.mark.parametrize("revision", ["empty", None, "0001", "0002"])
def test_gate_accepts_exactly_one_backend(revision):
    assert db.check_test_schema_inventory(inventory(revision, database_backends=1))


@pytest.mark.parametrize("backends", [0, 2, 3, 10, -1], ids=lambda v: f"count-{v}")
def test_gate_rejects_backend_count_other_than_one(backends):
    with pytest.raises(db.DatabaseConfigError, match="DB 전체 연결 수"):
        db.check_test_schema_inventory(inventory(database_backends=backends))


@pytest.mark.parametrize("backends", [None, "1", 1.0, True, b"1", [1], (1,)],
                         ids=["none", "str", "float", "bool", "bytes", "list", "tuple"])
def test_gate_rejects_missing_or_malformed_backend_count(backends):
    with pytest.raises(db.DatabaseConfigError, match="확인할 수 없음"):
        db.check_test_schema_inventory(inventory(database_backends=backends))


def test_gate_rejects_hidden_other_role_session():
    # 2-A3 실측: 다른 역할의 세션은 pg_stat_activity 에서 datname 이 숨겨져 other_sessions=0 으로 보인다.
    # numbackends 는 역할과 관계없이 세므로 2 가 되어 막힌다
    inv = inventory("0001", other_sessions=0, database_backends=2)
    with pytest.raises(db.DatabaseConfigError, match="DB 전체 연결 수 2"):
        db.check_test_schema_inventory(inv)


def test_inventory_requires_backend_count():
    fields = {f.name for f in dataclasses.fields(db.SchemaInventory) if f.default is dataclasses.MISSING}
    assert "database_backends" in fields          # 기본값이 없어 빠뜨리면 만들 수 없다
    with pytest.raises(TypeError):
        db.SchemaInventory(**{k: v for k, v in dataclasses.asdict(inventory()).items()
                              if k != "database_backends"})


def test_gate_reports_every_problem_at_once():
    inv = inventory(other_sessions=2, database_owner="postgres", functions=frozenset({("public", "f")}))
    with pytest.raises(db.DatabaseConfigError) as e:
        db.check_test_schema_inventory(inv)
    assert all(word in str(e.value) for word in ("다른 세션 2개", "DB 소유자 postgres", "함수"))


GATE_FORBIDDEN = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT", "REVOKE",
                  "LOCK", "SET ", "COPY", "VACUUM", "CALL", "DO ")


def test_gate_sql_is_read_only():
    for key, sql in db.TEST_SCHEMA_SQL.items():
        assert sql.startswith("SELECT "), key
        assert not any(word in sql.upper() for word in GATE_FORBIDDEN), key
    tree = ast.parse((APP_DIR / "db.py").read_text(encoding="utf-8"))
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "read_test_schema_inventory")
    literals = [n.value for n in ast.walk(func) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any(any(w in lit.upper() for w in GATE_FORBIDDEN) for lit in literals)   # 행 수 세기도 SELECT 만


class InventoryConnection:
    # read_test_schema_inventory 가 보내는 SELECT 에 가짜 결과를 돌려준다 (실행한 문장 기록)
    def __init__(self, answers: dict[str, list[tuple]]):
        self.answers, self.executed = answers, []

    def execute(self, statement, *args):
        sql = str(statement)
        self.executed.append(sql)
        key = next((k for k, v in db.TEST_SCHEMA_SQL.items() if v == sql), None)
        rows = self.answers.get(key, [(0,)] if sql.startswith("SELECT count(*) FROM public.") else [])
        return type("R", (), {"all": lambda _s: rows, "scalar_one": lambda _s: rows[0][0]})()


def test_read_inventory_counts_only_known_tables_and_runs_selects_only():
    conn = InventoryConnection({
        "has_alembic_version": [(True,)], "database_owner": [(USER,)],
        "schemas": [("public", USER)],
        "relations": [("public", "users", "r", USER), ("public", "secret_notes", "r", USER),
                      ("public", "alembic_version", "r", USER)],
        "other_sessions": [(0,)], "alembic_versions": [("0001",)], "database_backends": [(1,)],
    })
    inv = db.read_test_schema_inventory(conn)
    assert dict(inv.row_counts) == {"alembic_version": 0, "users": 0}     # 모르는 표(secret_notes)는 세지 않는다
    assert inv.alembic_versions == ("0001",)
    assert all(sql.startswith("SELECT ") for sql in conn.executed)
    assert not any("secret_notes" in sql for sql in conn.executed)
    with pytest.raises(db.DatabaseConfigError):
        db.check_test_schema_inventory(inv)                               # 모르는 표가 있으니 삭제 거부
    assert inv.database_backends == 1
    assert any("pg_stat_database" in sql for sql in conn.executed)


@pytest.mark.parametrize("answer, expected", [
    ([(1,)], 1), ([(3,)], 3), ([], None), ([(1,), (1,)], None), ([(None,)], None), ([(1, 2)], None),
], ids=["one", "three", "no-row", "two-rows", "null", "two-columns"])
def test_read_inventory_backend_count_shape(answer, expected):
    conn = InventoryConnection({"has_alembic_version": [(False,)], "database_owner": [(USER,)],
                                "other_sessions": [(0,)], "database_backends": answer})
    assert db.read_test_schema_inventory(conn).database_backends == expected


# ---------- downgrade 보호 ----------

CONFIRM_X = {"downgrade_confirm": "todo_dev"}
CONFIRM_ENV = {"TODO_DOWNGRADE_CONFIRM": "todo_dev@127.0.0.1:5433"}


def test_downgrade_allowed_on_test_target():
    db.check_downgrade_allowed(db.normalize_url(TARGET), GOOD, {}, {})


@pytest.mark.parametrize("x_args, environ", [
    ({}, {}),                                                             # 확인값 없음
    (CONFIRM_X, {}),                                                      # 옵션 하나만
    ({}, CONFIRM_ENV),                                                    # 환경변수 하나만
    ({"downgrade_confirm": "todo_test"}, CONFIRM_ENV),                    # DB 이름 불일치
    (CONFIRM_X, {"TODO_DOWNGRADE_CONFIRM": "todo_dev@127.0.0.1:5432"}),   # 포트 불일치
    ({"downgrade_confirm": "yes"}, {"TODO_DOWNGRADE_CONFIRM": "yes"}),
])
def test_downgrade_blocked_on_dev_without_both_confirmations(x_args, environ):
    with pytest.raises(db.DatabaseConfigError):
        db.check_downgrade_allowed(db.normalize_url(DEV), DEV_FACTS, x_args, environ)


def test_downgrade_on_dev_needs_both_confirmations():
    db.check_downgrade_allowed(db.normalize_url(DEV), DEV_FACTS, CONFIRM_X, CONFIRM_ENV)


def test_downgrade_blocked_when_test_url_reaches_other_server():
    # URL 은 테스트 대상이어도 실제 서버가 다르면(예: 5432) 테스트 DB 로 보지 않는다
    with pytest.raises(db.DatabaseConfigError):
        db.check_downgrade_allowed(db.normalize_url(TARGET), dataclasses.replace(GOOD, port=5432), {}, {})


def test_guard_downgrade_reads_server_and_uses_connection_url():
    db.guard_downgrade(FakeConnection(GOOD), {}, offline=False, environ={})
    with pytest.raises(db.DatabaseConfigError):
        db.guard_downgrade(FakeConnection(DEV_FACTS, DEV), {}, offline=False, environ={})
    offline = FakeConnection(DEV_FACTS, DEV)
    db.guard_downgrade(offline, {}, offline=True, environ={})              # 오프라인 --sql 은 DB 를 바꾸지 않는다
    assert offline.executed == []


def test_every_revision_guards_downgrade_first():
    # 앞으로 추가할 리비전도 downgrade() 첫 문장이 db.guard_downgrade 여야 한다
    for path in sorted((APP_DIR / "alembic" / "versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        first = func.body[0]
        assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), path.name
        assert ast.unparse(first.value.func) == "db.guard_downgrade", path.name


# ---------- v6 동작 보존 ----------

_IMPORT_PROBE = r'''
# 새 프로세스에서 main 을 import 하기 "전에" 감시 장치를 단다 — 엔진 생성·DB 연결·소켓 연결·Alembic 명령을 기록하고 막는다
import json, socket, sys
sys.path.insert(0, sys.argv[1])
events = []

def watch(label):
    def blocked(*args, **kwargs):
        events.append(label)
        raise RuntimeError("blocked: " + label)
    return blocked

import sqlalchemy, sqlalchemy.engine, sqlalchemy.engine.create
for mod in (sqlalchemy, sqlalchemy.engine, sqlalchemy.engine.create):
    mod.create_engine = watch("create_engine")
import psycopg
psycopg.connect = watch("psycopg.connect")
psycopg.Connection.connect = classmethod(lambda cls, *a, **k: watch("psycopg.Connection.connect")())
# 소켓은 PostgreSQL 포트로 가는 연결만 막는다 (Windows asyncio 의 내부 루프백 socketpair 는 그대로 둔다)
DB_PORTS = {5432, 5433, 15432}
_socket_connect, _create_connection = socket.socket.connect, socket.create_connection

def guarded_connect(self, address):
    if isinstance(address, tuple) and len(address) >= 2 and address[1] in DB_PORTS:
        events.append("socket.connect")
        raise RuntimeError("blocked: socket.connect")
    return _socket_connect(self, address)

def guarded_create_connection(address, *args, **kwargs):
    if address[1] in DB_PORTS:
        events.append("socket.create_connection")
        raise RuntimeError("blocked: socket.create_connection")
    return _create_connection(address, *args, **kwargs)

socket.socket.connect = guarded_connect
socket.create_connection = guarded_create_connection
import alembic.command
for name in ("upgrade", "downgrade", "stamp", "revision"):
    setattr(alembic.command, name, watch("alembic." + name))

assert "main" not in sys.modules and "db" not in sys.modules       # 이 프로세스에서 처음 불러온다
import main                                                         # 검사 대상
import db
result = {"after_import": list(events), "engine_cache": db.get_engine.cache_info().currsize,
          "sessionmaker_cache": db.get_sessionmaker.cache_info().currsize}

# v6 데이터 경로는 JSON 저장 그대로 — DB 를 건드리지 않고 TODO_FILE 에 쓴다
from fastapi.testclient import TestClient
client = TestClient(main.app)
created = client.post("/todos", json={"title": "probe"})
result["post_status"] = created.status_code
result["get_titles"] = [t["title"] for t in client.get("/todos").json()]
result["json_titles"] = [t["title"] for t in json.loads(main.TODO_FILE.read_text(encoding="utf-8"))]
result["after_v6_requests"] = list(events)

# 자체 확인: 감시 장치가 main·db 가 실제로 쓰는 경로에서 작동한다
checks = {}
for label, call in {
    "db.make_engine": lambda: db.make_engine("postgresql://u@127.0.0.1:5433/x"),
    "db.create_engine": lambda: db.create_engine("postgresql+psycopg://u@127.0.0.1:5433/x"),
    "psycopg.connect": lambda: psycopg.connect("host=127.0.0.1 port=5433 dbname=x"),
    "socket.connect": lambda: socket.socket().connect(("127.0.0.1", 5433)),
    "socket.create_connection": lambda: socket.create_connection(("127.0.0.1", 5432)),
}.items():
    before = len(events)
    try:
        call()
    except RuntimeError:
        pass
    checks[label] = len(events) > before
result["self_check"] = checks
print(json.dumps(result))
'''


def test_importing_main_creates_no_engine_or_connection_and_v6_stays_json(tmp_path):
    # (7.0.0 Phase 2-E 계약 변경) main 은 인증 경로 때문에 db·sqlalchemy 를 import 해도 된다. 그러나 import 자체로는
    # 엔진 생성·DB 연결·Alembic 명령(스키마 변경·마이그레이션)·DB 쓰기가 없어야 하고, v6 데이터 경로는 JSON 저장 그대로다.
    # 다른 테스트의 모듈 캐시와 무관하게 새 격리 프로세스(-I)에서 감시 장치를 먼저 달고 확인한다. DB 서버·URL 이 없어도 돈다.
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "AUTH_FAILURE_KEY", "APP_ENV") and not k.startswith("PG")}
    env["TODO_FILE"] = str(tmp_path / "todo.json")             # import 때 만들어지는 파일도 임시 폴더에
    env["TEMPLATE_FILE"] = str(tmp_path / "task_templates.json")
    result = subprocess.run([sys.executable, "-I", "-c", _IMPORT_PROBE, str(APP_DIR)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["self_check"] == {"db.make_engine": True, "db.create_engine": True,
                                 "psycopg.connect": True, "socket.connect": True,
                                 "socket.create_connection": True}             # 감시 장치가 실제로 작동한다
    assert out["after_import"] == [] and out["engine_cache"] == 0 and out["sessionmaker_cache"] == 0
    assert out["post_status"] == 201 and out["get_titles"] == ["probe"] and out["json_titles"] == ["probe"]
    assert out["after_v6_requests"] == []                       # v6 경로는 DB 를 쓰지 않는다
    assert not (tmp_path / "task_templates.json").exists()      # 템플릿 파일은 처음 저장할 때만 만든다
