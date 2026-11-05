"""(7.0.0 Phase 1-B) 실제 PostgreSQL 테스트 DB 가 있어야 도는 검사.

- TEST_DATABASE_URL 이 없으면 모두 건너뛴다(skip). 건너뛴 것은 통과가 아니다.
- 대상은 db.TEST_DB_TARGET(todo_test_user@127.0.0.1:5433/todo_test, PostgreSQL 18.x) 하나뿐이다.
  conftest.py 가 세션 시작 전에 URL 을, engine·fresh_schema 픽스처가 접속 후 실제 서버를 확인한다.
- 이 파일은 그 DB 의 public 스키마를 지웠다 만든다 (지우기 직전 같은 연결에서 db.verify_test_server).
- 개발 DB(todo_dev)·운영 DB 에는 절대 연결하지 않는다.
"""
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

import db

pytestmark = pytest.mark.postgres

APP_DIR = Path(__file__).resolve().parent.parent
TEST_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_URL:
    pytest.skip("TEST_DATABASE_URL 이 없어 PostgreSQL 테스트를 건너뜁니다 (Phase 1-B 보류)",
                allow_module_level=True)


@pytest.fixture(scope="module")
def engine():
    # URL(대상 고정) → 실제 서버(버전·주소·포트·DB·계정) 순서로 확인한 뒤에만 테스트를 시작한다
    url = db.check_test_database_url(TEST_URL, os.environ.get("DATABASE_URL"))
    engine = db.make_engine(url)
    try:
        with engine.connect() as conn:
            db.verify_test_server(conn)
    except db.DatabaseConfigError as e:
        engine.dispose()
        pytest.exit(f"테스트 DB 보호: {e}", returncode=2)   # 다른 테스트도 진행하지 않는다
    yield engine
    engine.dispose()


@pytest.fixture
def alembic_cfg(monkeypatch):
    # env.py 는 DATABASE_URL 을 읽으므로 이 테스트 동안만 테스트 DB 로 바꾼다
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.chdir(APP_DIR)
    return Config(str(APP_DIR / "alembic.ini"))


@pytest.fixture
def fresh_schema(engine, alembic_cfg):
    # 빈 스키마에서 head 까지 올린다. 지우기 직전에 같은 연결로 서버를 다시 확인한다 (실패하면 지우지 않는다)
    with engine.begin() as conn:
        db.verify_test_server(conn)
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_cfg, "head")
    yield engine


def make_user(conn, name: str) -> int:
    return conn.execute(insert(db.User).values(
        username=name, password_hash="x", created_at=datetime.now(timezone.utc)).returning(db.User.id)).scalar_one()


def todo_row(user_id: int, position: int, **fields) -> dict:
    return {"user_id": user_id, "position": position, "title": "할 일", **fields}


def assert_rejected(conn, statement) -> None:
    # 위반 문장은 SAVEPOINT 안에서 실행한다 — 실패해도 바깥 트랜잭션은 계속 쓸 수 있고 앞의 데이터도 남는다
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(statement)
    assert conn.execute(text("SELECT 1")).scalar_one() == 1


def count(conn, model) -> int:
    return conn.execute(select(func.count()).select_from(model)).scalar_one()


def test_upgrade_downgrade_round_trip(fresh_schema, alembic_cfg):
    assert set(inspect(fresh_schema).get_table_names()) >= set(db.Base.metadata.tables)
    command.downgrade(alembic_cfg, "base")
    assert set(inspect(fresh_schema).get_table_names()) <= {"alembic_version"}
    command.upgrade(alembic_cfg, "head")
    assert set(inspect(fresh_schema).get_table_names()) >= set(db.Base.metadata.tables)


def test_models_match_migration(fresh_schema):
    # autogenerate 비교 결과가 비어 있어야 한다
    with fresh_schema.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(context, db.Base.metadata) == []


def test_session_timezone_is_utc(fresh_schema):
    with fresh_schema.connect() as conn:
        assert conn.execute(text("SHOW TimeZone")).scalar_one() == "UTC"
        moment = conn.execute(text("SELECT TIMESTAMPTZ '2026-11-05 09:00:00+09'")).scalar_one()
        assert moment.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("fields", [
    {"priority": "긴급"},
    {"recurrence": "yearly"},
    {"recurrence": "daily", "due_date": None},
])
def test_check_constraints(fresh_schema, fields):
    with fresh_schema.connect() as conn, conn.begin():
        uid = make_user(conn, "alice")
        assert_rejected(conn, insert(db.Todo).values(**todo_row(uid, 1, **fields)))
        assert count(conn, db.User) == 1 and count(conn, db.Todo) == 0


def test_defaults_and_v6_null_timestamps(fresh_schema):
    with fresh_schema.connect() as conn, conn.begin():
        uid = make_user(conn, "alice")
        row = conn.execute(insert(db.Todo).values(**todo_row(uid, 1)).returning(db.Todo)).one()
        assert (row.description, row.completed, row.priority, row.tags, row.favorite, row.recurrence,
                row.next_spawned, row.created_at, row.deleted_at) == ("", False, "보통", [], False, "none",
                                                                      False, None, None)


def test_position_unique_per_user(fresh_schema):
    with fresh_schema.connect() as conn, conn.begin():
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        conn.execute(insert(db.Todo).values(**todo_row(alice, 1)))
        conn.execute(insert(db.Todo).values(**todo_row(bob, 1)))           # 다른 사용자는 같은 position 가능
        assert_rejected(conn, insert(db.Todo).values(**todo_row(alice, 1)))
        conn.execute(insert(db.Todo).values(**todo_row(alice, 2)))         # 실패 뒤에도 같은 트랜잭션에서 계속 쓴다
        assert count(conn, db.Todo) == 3


def test_template_name_unique_per_user_case_insensitive(fresh_schema):
    with fresh_schema.connect() as conn, conn.begin():
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        base = {"title": "t"}
        conn.execute(insert(db.TaskTemplate).values(user_id=alice, position=1, name="주간 보고", **base))
        conn.execute(insert(db.TaskTemplate).values(user_id=bob, position=1, name="주간 보고", **base))
        conn.execute(insert(db.TaskTemplate).values(user_id=alice, position=2, name="Weekly", **base))
        assert_rejected(conn, insert(db.TaskTemplate).values(user_id=alice, position=3, name="WEEKLY", **base))
        assert count(conn, db.TaskTemplate) == 3


def test_explicit_ids_keep_and_sequence_can_be_moved(fresh_schema):
    # 이전처럼 원래 id 로 넣은 뒤 setval 하면 다음 id 가 최대 id + 1 이다
    with fresh_schema.connect() as conn, conn.begin():
        uid = make_user(conn, "alice")
        conn.execute(insert(db.Todo).values(id=7, **todo_row(uid, 1)))
        conn.execute(text("SELECT setval(pg_get_serial_sequence('todos', 'id'), 7)"))
        new_id = conn.execute(insert(db.Todo).values(**todo_row(uid, 2)).returning(db.Todo.id)).scalar_one()
        assert new_id == 8


def test_cascade_delete(fresh_schema):
    with fresh_schema.connect() as conn, conn.begin():
        uid = make_user(conn, "alice")
        todo_id = conn.execute(insert(db.Todo).values(**todo_row(uid, 1, due_date=date(2026, 11, 5)))
                               .returning(db.Todo.id)).scalar_one()
        conn.execute(insert(db.Subtask).values(todo_id=todo_id, id=1, title="하위"))
        conn.execute(db.User.__table__.delete().where(db.User.id == uid))
        assert conn.execute(select(db.Todo)).all() == []
        assert conn.execute(select(db.Subtask)).all() == []
