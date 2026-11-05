"""(7.0.0 Phase 1-B) 실제 PostgreSQL 테스트 DB 가 있어야 도는 검사.

- TEST_DATABASE_URL 이 없으면 모두 건너뛴다(skip). 건너뛴 것은 통과가 아니다.
- 대상은 db.TEST_DB_TARGET(todo_test_user@127.0.0.1:5433/todo_test, PostgreSQL 18.x) 하나뿐이다.
  conftest.py 가 세션 시작 전에 URL 을, engine·fresh_schema 픽스처가 접속 후 실제 서버를 확인한다.
- 이 파일은 그 DB 의 public 스키마를 지웠다 만든다 (지우기 직전 같은 연결에서 db.verify_test_server).
- 개발 DB(todo_dev)·운영 DB 에는 절대 연결하지 않는다.
"""
import hashlib
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import auth
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
            db.verify_test_schema(conn)          # R-19 — 읽기 전용, 처음 지우기 전에 한 번 더 일찍 멈춘다
            conn.rollback()
    except (db.DatabaseConfigError, SQLAlchemyError) as e:
        engine.dispose()
        pytest.exit(f"테스트 DB 보호: {e}", returncode=2)   # 다른 테스트도 진행하지 않는다 (확인할 수 없어도 멈춘다)
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
    # 빈 스키마에서 head 까지 올린다. 지우기 직전에 같은 연결·트랜잭션에서 서버와 스키마 내용(R-19)을
    # 다시 확인한다. 하나라도 어긋나거나 확인할 수 없으면 지우지 않고 세션 전체를 멈춘다
    try:
        with engine.begin() as conn:
            db.verify_test_server(conn)
            db.verify_test_schema(conn)
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))   # 다른 세션이 잡고 있으면 기다리지 않고 실패
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except (db.DatabaseConfigError, SQLAlchemyError) as e:
        pytest.exit(f"테스트 DB 보호: {e}", returncode=2)
    command.upgrade(alembic_cfg, "head")
    yield engine


@contextmanager
def rolled_back(engine):
    # 한 트랜잭션 안에서 검사하고, 성공해도 실패해도 끝나면 되돌린다 — 합성 데이터를 커밋하지 않는다.
    # (`with conn.begin():` 은 정상 종료 때 커밋하므로 마지막 테스트의 데이터가 todo_test 에 남았다)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            trans.rollback()


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
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        assert_rejected(conn, insert(db.Todo).values(**todo_row(uid, 1, **fields)))
        assert count(conn, db.User) == 1 and count(conn, db.Todo) == 0


def test_defaults_and_v6_null_timestamps(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        row = conn.execute(insert(db.Todo).values(**todo_row(uid, 1)).returning(db.Todo)).one()
        assert (row.description, row.completed, row.priority, row.tags, row.favorite, row.recurrence,
                row.next_spawned, row.created_at, row.deleted_at) == ("", False, "보통", [], False, "none",
                                                                      False, None, None)


def test_position_unique_per_user(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        conn.execute(insert(db.Todo).values(**todo_row(alice, 1)))
        conn.execute(insert(db.Todo).values(**todo_row(bob, 1)))           # 다른 사용자는 같은 position 가능
        assert_rejected(conn, insert(db.Todo).values(**todo_row(alice, 1)))
        conn.execute(insert(db.Todo).values(**todo_row(alice, 2)))         # 실패 뒤에도 같은 트랜잭션에서 계속 쓴다
        assert count(conn, db.Todo) == 3


def test_template_name_unique_per_user_case_insensitive(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        base = {"title": "t"}
        conn.execute(insert(db.TaskTemplate).values(user_id=alice, position=1, name="주간 보고", **base))
        conn.execute(insert(db.TaskTemplate).values(user_id=bob, position=1, name="주간 보고", **base))
        conn.execute(insert(db.TaskTemplate).values(user_id=alice, position=2, name="Weekly", **base))
        assert_rejected(conn, insert(db.TaskTemplate).values(user_id=alice, position=3, name="WEEKLY", **base))
        assert count(conn, db.TaskTemplate) == 3


def test_explicit_ids_keep_and_sequence_can_be_moved(fresh_schema):
    # 이전처럼 원래 id 로 넣은 뒤 setval 하면 다음 id 가 최대 id + 1 이다
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        conn.execute(insert(db.Todo).values(id=7, **todo_row(uid, 1)))
        conn.execute(text("SELECT setval(pg_get_serial_sequence('todos', 'id'), 7)"))
        new_id = conn.execute(insert(db.Todo).values(**todo_row(uid, 2)).returning(db.Todo.id)).scalar_one()
        assert new_id == 8


def test_cascade_delete(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        todo_id = conn.execute(insert(db.Todo).values(**todo_row(uid, 1, due_date=date(2026, 11, 5)))
                               .returning(db.Todo.id)).scalar_one()
        conn.execute(insert(db.Subtask).values(todo_id=todo_id, id=1, title="하위"))
        conn.execute(db.User.__table__.delete().where(db.User.id == uid))
        assert conn.execute(select(db.Todo)).all() == []
        assert conn.execute(select(db.Subtask)).all() == []


# ---------- Phase 2-A: auth_failures (0002) ----------

def failure_row(kind: str, username_key: str | None, client_ip: str = "127.0.0.1") -> dict:
    return {"kind": kind, "username_key": username_key, "client_ip": client_ip,
            "created_at": datetime.now(timezone.utc)}


def test_gate_accepts_state_left_by_migrations(fresh_schema):
    with fresh_schema.connect() as conn:
        assert db.verify_test_schema(conn) == "0002"


def test_stepwise_0002_0001_round_trip(fresh_schema, alembic_cfg):
    command.downgrade(alembic_cfg, "0001")
    tables = set(inspect(fresh_schema).get_table_names())
    assert "auth_failures" not in tables and {"users", "todos", "alembic_version"} <= tables
    with fresh_schema.connect() as conn:
        assert db.verify_test_schema(conn) == "0001"
    command.upgrade(alembic_cfg, "head")
    assert "auth_failures" in inspect(fresh_schema).get_table_names()


def test_auth_failures_indexes(fresh_schema):
    indexes = {i["name"]: i["column_names"] for i in inspect(fresh_schema).get_indexes("auth_failures")}
    assert indexes == {
        "ix_auth_failures_kind_username_key_created_at": ["kind", "username_key", "created_at"],
        "ix_auth_failures_kind_client_ip_created_at": ["kind", "client_ip", "created_at"],
        "ix_auth_failures_created_at": ["created_at"],
    }


def test_auth_failures_constraints(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        conn.execute(insert(db.AuthFailure).values(**failure_row("login", "alice")))
        conn.execute(insert(db.AuthFailure).values(**failure_row("login", None)))       # 담을 수 없는 형식은 null
        conn.execute(insert(db.AuthFailure).values(**failure_row("register", None)))
        assert_rejected(conn, insert(db.AuthFailure).values(**failure_row("register", "alice")))
        assert_rejected(conn, insert(db.AuthFailure).values(**failure_row("logout", None)))
        assert count(conn, db.AuthFailure) == 3


def test_advisory_lock_key_function_available(fresh_schema):
    # 2-D 의 username 잠금이 쓸 함수 — 트랜잭션이 끝나면 자동으로 풀린다
    with rolled_back(fresh_schema) as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('login:' || :u, 0))"), {"u": "alice"})


# ---------- Phase 2-C: 로그인 세션 저장 (db.create_session 등) ----------
# 세션 함수는 commit 하지 않는다 — 여기서는 연결의 트랜잭션에 묶인 ORM Session 으로 부른다.
# 트랜잭션의 주인은 연결(rolled_back)이고 ORM Session 은 거기에 합류만 한다 — 끝나면 연결이 되돌린다.
# 커밋이 꼭 필요한 테스트(호출자 rollback, 다른 연결에서 보이는지)만 명시적으로 커밋한다.
# 두 번째 연결이 필요하면 NullPool 엔진으로 만들고 반드시 닫는다 (풀에 남은 연결은 R-19 게이트의 연결 수에 잡힌다)

T = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)


def orm(conn) -> Session:
    return Session(bind=conn, expire_on_commit=False)


def session_count(conn) -> int:
    return count(conn, db.UserSession)


def test_session_create_lookup_delete(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        s = orm(conn)
        token, csrf = db.create_session(s, uid, T)
        found = db.get_active_session(s, token, T + timedelta(hours=1))
        assert found is not None and found[1].id == uid and found[0].csrf_token == csrf
        assert db.delete_session(s, token) is True
        assert db.get_active_session(s, token, T + timedelta(hours=1)) is None
        assert db.delete_session(s, token) is False                          # 멱등
        assert session_count(conn) == 0


def test_session_expiry_boundary_in_database(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        s = orm(conn)
        token, _ = db.create_session(s, uid, T)
        expires = T + timedelta(days=14)
        assert db.get_active_session(s, token, expires - timedelta(microseconds=1)) is not None
        assert db.get_active_session(s, token, expires) is None              # 같으면 만료
        assert db.get_active_session(s, token, expires + timedelta(days=1)) is None
        stored = conn.execute(select(db.UserSession.expires_at)).scalar_one()
        assert stored == expires and stored.utcoffset() == timedelta(0)        # TIMESTAMPTZ 를 거쳐도 같은 순간
        assert session_count(conn) == 1                                       # 조회는 만료 행을 지우지 않는다


def test_relogin_replaces_only_presented_session(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        s = orm(conn)
        a, _ = db.create_session(s, uid, T)                                  # 이 브라우저
        b, _ = db.create_session(s, uid, T)                                  # 다른 기기
        c, _ = db.create_session(s, uid, T + timedelta(minutes=5), replace_token=a)
        now = T + timedelta(minutes=10)
        assert db.get_active_session(s, a, now) is None
        assert db.get_active_session(s, b, now) is not None and db.get_active_session(s, c, now) is not None
        db.create_session(s, uid, now, replace_token="not-a-valid-token")    # 형식이 틀리면 아무것도 지우지 않는다
        assert session_count(conn) == 3


def test_relogin_as_other_user_replaces_only_presented_session(fresh_schema):
    # 잠정 정책: 제시된 세션 하나만 교체한다 — 그 세션의 사용자와 새로 로그인한 사용자가 달라도 같다.
    # A 의 다른 세션과 B 의 기존 세션은 그대로 둔다
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        a1, _ = db.create_session(s, alice, T)
        a2, _ = db.create_session(s, alice, T)
        b1, _ = db.create_session(s, bob, T)
        b2, _ = db.create_session(s, bob, T + timedelta(minutes=5), replace_token=a1)
        now = T + timedelta(minutes=10)
        assert db.get_active_session(s, a1, now) is None
        owners = {name: db.get_active_session(s, tok, now)[1].id for name, tok in (("a2", a2), ("b1", b1), ("b2", b2))}
        assert owners == {"a2": alice, "b1": bob, "b2": bob}
        assert session_count(conn) == 3


def test_purge_removes_expired_sessions_of_all_users(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        db.create_session(s, alice, T - timedelta(days=15))                  # 만료됨
        db.create_session(s, bob, T - timedelta(days=14))                    # 정확히 지금 만료
        keep, _ = db.create_session(s, bob, T - timedelta(days=1))           # 유효
        assert db.purge_expired_sessions(s, T) == 2
        assert session_count(conn) == 1 and db.get_active_session(s, keep, T) is not None
        assert db.purge_expired_sessions(s, T) == 0


def test_user_delete_cascades_sessions(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        s = orm(conn)
        token, _ = db.create_session(s, uid, T)
        conn.execute(db.User.__table__.delete().where(db.User.id == uid))
        s.expunge_all()
        assert session_count(conn) == 0 and db.get_active_session(s, token, T) is None


def test_session_rollback_leaves_nothing(fresh_schema):
    with fresh_schema.connect() as conn:
        with conn.begin():
            uid = make_user(conn, "alice")
        trans = conn.begin()
        token, _ = db.create_session(orm(conn), uid, T)
        trans.rollback()                                                     # 호출자가 되돌리면 세션도 없다
        with conn.begin():
            assert db.get_active_session(orm(conn), token, T) is None and session_count(conn) == 0


def test_raw_tokens_not_stored(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        token, csrf = db.create_session(orm(conn), uid, T)
        row = conn.execute(select(db.UserSession.__table__)).one()._asdict()
        assert row["token_hash"] == hashlib.sha256(token.encode()).digest()
        assert all(token not in str(v) for v in row.values())               # 원문은 어느 열에도 없다
        assert bytes(row["token_hash"]) != token.encode() and row["csrf_token"] == csrf


def test_integrity_error_hides_parameters(fresh_schema):
    # 세 가지를 나눠 본다 (R-27)
    # 1) SQLAlchemy 바인드 파라미터 — make_engine(hide_parameters=True)로 숨겨져야 한다 (실패 조건)
    # 2) PostgreSQL 이 만드는 오류 DETAIL — 중복 키 값(token_hash)이 들어 있는지 관찰만 하고 결과(True/False)만 출력
    # 3) 실제 앱 로그·HTTP 응답 — Phase 2-E 책임 (여기서는 다루지 않는다)
    # 실패해도 예외 원문·비밀값이 출력되지 않게, 비교 결과를 bool 로 만든 뒤 단언한다
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        token, csrf = db.create_session(orm(conn), uid, T)
        marker = auth.new_csrf_token()                                       # 두 번째 INSERT 의 CSRF 원문
        with pytest.raises(IntegrityError) as e, conn.begin_nested():
            conn.execute(insert(db.UserSession).values(
                token_hash=auth.hash_token(token), user_id=uid, csrf_token=marker,
                created_at=T, expires_at=T + timedelta(days=14)))
        message = str(e.value)
        detail = getattr(getattr(e.value.orig, "diag", None), "message_detail", None) or ""
        hidden_note = "hide_parameters=True" in message
        leaked_marker = marker in message or marker in detail
        leaked_csrf = csrf in message or csrf in detail
        leaked_token = token in message or token in detail
        detail_has_hash = auth.hash_token(token).hex() in (message + detail).lower()
        del message, detail, e                                               # 실패 출력에 원문이 실리지 않게
        print(f"R-27 observation: postgres_detail_contains_token_hash={detail_has_hash}")
        assert hidden_note, "SQLAlchemy 바인드 파라미터가 숨겨지지 않음"
        assert not leaked_marker, "CSRF 원문(바인드 값)이 예외 문자열에 있음"
        assert not leaked_csrf, "기존 세션의 CSRF 원문이 예외 문자열에 있음"
        assert not leaked_token, "세션 토큰 원문이 예외 문자열에 있음"
        assert session_count(conn) == 1


def test_logout_commit_is_seen_by_other_connection(fresh_schema):
    other = db.make_engine(db.check_test_database_url(TEST_URL), poolclass=NullPool)
    try:
        with fresh_schema.connect() as conn:
            with conn.begin():
                uid = make_user(conn, "alice")
                token, _ = db.create_session(orm(conn), uid, T)
            with other.connect() as conn2:
                with conn2.begin():
                    db.verify_test_server(conn2)
                    assert db.get_active_session(orm(conn2), token, T) is not None
                with conn.begin():
                    assert db.delete_session(orm(conn), token) is True        # 로그아웃 커밋
                with conn2.begin():
                    assert db.get_active_session(orm(conn2), token, T) is None
    finally:
        other.dispose()                                                      # 다음 게이트 전에 연결을 닫는다


def test_multiple_sessions_for_same_user(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        s = orm(conn)
        tokens = [db.create_session(s, uid, T)[0] for _ in range(3)]
        assert len(set(tokens)) == 3 and session_count(conn) == 3
        assert all(db.get_active_session(s, t, T)[1].id == uid for t in tokens)
        hashes = conn.execute(select(db.UserSession.token_hash).where(db.UserSession.user_id == uid)).scalars()
        assert len({bytes(h) for h in hashes}) == 3                         # 저장된 해시도 서로 다르다
    with fresh_schema.connect() as conn:                                     # 되돌렸으므로 종료 후 남는 행이 없다
        assert count(conn, db.User) == 0 and session_count(conn) == 0
        conn.rollback()
