"""(7.0.0 Phase 1-B) 실제 PostgreSQL 테스트 DB 가 있어야 도는 검사.

- TEST_DATABASE_URL 이 없으면 모두 건너뛴다(skip). 건너뛴 것은 통과가 아니다.
- 대상은 db.TEST_DB_TARGET(todo_test_user@127.0.0.1:5433/todo_test, PostgreSQL 18.x) 하나뿐이다.
  conftest.py 가 세션 시작 전에 URL 을, engine·fresh_schema 픽스처가 접속 후 실제 서버를 확인한다.
- 이 파일은 그 DB 의 public 스키마를 지웠다 만든다 (지우기 직전 같은 연결에서 db.verify_test_server).
- 개발 DB(todo_dev)·운영 DB 에는 절대 연결하지 않는다.
"""
import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import auth
import db
import main
from pg_harness import (  # (3-2) 공용 하네스 — 본문은 그대로 옮겼다
    APP_DIR, CONNECT_TIMEOUT, SERVER_FAILURE_KEY, SERVER_KEY, Server, TEST_URL, _UNFINISHED_WORKERS, nullpool_engine,
    open_verified_engine, reset_test_schema, start_server, stop_server, wait_for_backends_released,
    wait_until_single_backend,
)

pytestmark = pytest.mark.postgres


if not TEST_URL:
    pytest.skip("TEST_DATABASE_URL 이 없어 PostgreSQL 테스트를 건너뜁니다 (Phase 1-B 보류)",
                allow_module_level=True)


@pytest.fixture(scope="module")
def engine():
    engine = open_verified_engine()                     # (3-2) 확인 절차는 pg_harness 로 옮겼다(그대로)
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
    reset_test_schema(engine, alembic_cfg)                                   # (3-2) 본문은 pg_harness 로 옮겼다(절차 그대로)
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
    wait_until_single_backend(fresh_schema)                                  # (3-1B-3) 업그레이드 직후 — 판정은 아래 게이트
    with fresh_schema.connect() as conn:
        assert db.verify_test_schema(conn) == "0002"


def test_stepwise_0002_0001_round_trip(fresh_schema, alembic_cfg):
    command.downgrade(alembic_cfg, "0001")
    tables = set(inspect(fresh_schema).get_table_names())
    assert "auth_failures" not in tables and {"users", "todos", "alembic_version"} <= tables
    wait_until_single_backend(fresh_schema)                                  # downgrade 직후 — 판정은 아래 게이트
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
        wait_for_backends_released(fresh_schema)                             # (3-1B-2) 닫은 연결이 서버에서 사라졌는지 확인


# ---------- Phase 2-D: 로그인·가입 시도 제한 (db.begin_login_attempt 등) ----------
# 트랜잭션 구성은 2-E(main.py) 몫이라 여기서는 테스트가 직접 짠다. 커밋이 필요한 테스트(잠금·동시성)는 NullPool 엔진을
# 쓰고 finally 에서 닫는다. 이 파일의 마지막 테스트는 rolled_back 형식으로 두어 종료 후 사용자 데이터가 0행이 되게 한다

LIMIT_KEY = auth.FailureKey(b"t" * 32)            # 테스트 전용 고정 키 (공개값)
LIMIT_IP = "203.0.113.7"


def login_as(username: str = "alice") -> auth.LoginName:
    return auth.login_name(username, LIMIT_KEY)


def failure_rows(conn) -> list:
    return conn.execute(select(db.AuthFailure.__table__).order_by(db.AuthFailure.id)).all()


BARRIER_TIMEOUT = 30                              # 초 — 동시 시작 대기 상한
JOIN_TIMEOUT = 60                                 # 초 — 작업 스레드 전체 종료 대기 상한


def test_login_attempt_records_hmac_key_and_reads_hash(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        uid = make_user(conn, "alice")
        attempt = db.begin_login_attempt(orm(conn), login_as("Alice"), LIMIT_IP, T)
        assert attempt.decision.allowed and attempt.user_id == uid and attempt.password_hash == "x"
        (row,) = failure_rows(conn)
        assert (row.id, row.kind, row.client_ip, row.created_at) == (attempt.attempt_id, "login", LIMIT_IP, T)
        assert row.username_key == login_as().failure_key and len(row.username_key) == 30
        assert "alice" not in row.username_key                               # 평문 username 은 저장하지 않는다
        missing = db.begin_login_attempt(orm(conn), login_as("nobody"), LIMIT_IP, T)
        assert missing.decision.allowed and missing.user_id is None and missing.password_hash is None


def test_login_user_ip_limit_and_retry_after_in_database(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        s = orm(conn)
        for i in range(5):
            assert db.begin_login_attempt(s, login_as(), LIMIT_IP, T + timedelta(seconds=i)).decision.allowed
        blocked = db.begin_login_attempt(s, login_as(), LIMIT_IP, T + timedelta(seconds=5))
        assert blocked.decision == auth.LimitDecision(False, 895) and blocked.attempt_id is None
        assert count(conn, db.AuthFailure) == 5                              # 429 는 기록하지 않는다
        other_ip = db.begin_login_attempt(s, login_as(), "198.51.100.1", T + timedelta(seconds=5))
        assert other_ip.decision.allowed                                     # (username, IP) 규칙은 IP 별
        after = db.begin_login_attempt(s, login_as(), LIMIT_IP, T + timedelta(minutes=15))
        assert after.decision.allowed                                        # 첫 기록이 창 밖(같은 시각) → 풀림


def test_login_username_and_ip_limits_in_database(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        s = orm(conn)
        for i in range(50):                                                  # 서로 다른 IP 50개 → username 50
            assert db.begin_login_attempt(s, login_as(), f"198.51.100.{i}", T).decision.allowed
        assert not db.begin_login_attempt(s, login_as(), "198.51.100.200", T).decision.allowed
        for i in range(20):                                                  # 같은 IP, 다른 username 20개 → IP 20
            assert db.begin_login_attempt(s, login_as(f"user{i:02d}"), "192.0.2.9", T).decision.allowed
        assert not db.begin_login_attempt(s, login_as("someone"), "192.0.2.9", T).decision.allowed
        assert not db.begin_login_attempt(s, auth.LoginName(None, None), "192.0.2.9", T).decision.allowed


def test_register_limit_in_database(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        s = orm(conn)
        for i in range(10):
            assert db.begin_register_attempt(s, LIMIT_IP, T + timedelta(minutes=i)).decision.allowed
        blocked = db.begin_register_attempt(s, LIMIT_IP, T + timedelta(minutes=10))
        assert blocked.decision == auth.LimitDecision(False, 50 * 60) and blocked.attempt_id is None
        rows = failure_rows(conn)
        assert len(rows) == 10 and {(r.kind, r.username_key) for r in rows} == {("register", None)}
        assert db.begin_login_attempt(s, login_as(), LIMIT_IP, T).decision.allowed   # 가입과 로그인은 따로 센다


def test_success_and_cancel_delete_only_own_attempt_in_database(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        s = orm(conn)
        old = db.begin_login_attempt(s, login_as(), LIMIT_IP, T - timedelta(minutes=5)).attempt_id    # 이전 실패
        a = db.begin_login_attempt(s, login_as(), LIMIT_IP, T).attempt_id
        b = db.begin_login_attempt(s, login_as(), LIMIT_IP, T).attempt_id                            # 다른 요청
        anon = db.begin_login_attempt(s, auth.LoginName(None, None), LIMIT_IP, T).attempt_id
        assert db.finish_login_success(s, login_as(), a) is True
        assert db.finish_login_success(s, login_as(), a) is False            # 이미 지움
        assert db.finish_login_success(s, login_as("bob"), b) is False      # 다른 username 의 키로는 못 지운다
        assert db.cancel_login_attempt(s, auth.LoginName(None, None), anon) is True
        assert {r.id for r in failure_rows(conn)} == {old, b}                # 이전 실패·다른 선기록은 그대로


def test_purge_old_auth_failures_in_database(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        for hours in (25, 24, 23):
            conn.execute(insert(db.AuthFailure).values(kind="register", username_key=None, client_ip=LIMIT_IP,
                                                       created_at=T - timedelta(hours=hours)))
        assert db.purge_old_auth_failures(orm(conn), T) == 2                 # 24시간 이상 지난 것 (같으면 삭제)
        assert [r.created_at for r in failure_rows(conn)] == [T - timedelta(hours=23)]


def test_auth_transaction_settings(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        db._begin_auth_transaction(orm(conn))
        assert conn.execute(text("SHOW lock_timeout")).scalar_one() == "2s"
        assert conn.execute(text("SHOW transaction_isolation")).scalar_one() == "read committed"


def test_login_lock_serializes_same_username(fresh_schema):
    # 결정적 확인: A 가 Tx1 안에서 잠금을 잡은 동안 B 는 같은 키를 못 잡고(다른 username 은 잡는다), A 커밋 뒤에는
    # 잡고 A 의 선기록을 본다
    try_lock = text("SELECT pg_try_advisory_xact_lock(hashtextextended('login:' || :k, 0))")
    other = nullpool_engine()
    try:
        with fresh_schema.connect() as conn_a, other.connect() as conn_b:
            db.verify_test_server(conn_b)
            conn_b.rollback()
            trans_a = conn_a.begin()
            db.begin_login_attempt(orm(conn_a), login_as(), LIMIT_IP, T)
            with conn_b.begin():
                assert conn_b.execute(try_lock, {"k": login_as().failure_key}).scalar_one() is False
                assert conn_b.execute(try_lock, {"k": login_as("bob").failure_key}).scalar_one() is True
            trans_a.commit()
            with conn_b.begin():
                assert conn_b.execute(try_lock, {"k": login_as().failure_key}).scalar_one() is True
                assert count(conn_b, db.AuthFailure) == 1
    finally:
        other.dispose()                                                      # 다음 게이트 전에 연결을 닫는다
        wait_for_backends_released(fresh_schema)


def test_login_lock_timeout_fails_closed(fresh_schema):
    # 잠금을 2초 안에 못 얻으면 오류(55P03)로 끝난다 — 허용으로 처리하지 않고 아무것도 기록하지 않는다
    other = nullpool_engine()
    try:
        with fresh_schema.connect() as conn_a, other.connect() as conn_b:
            trans_a = conn_a.begin()
            db.begin_login_attempt(orm(conn_a), login_as(), LIMIT_IP, T)
            trans_b = conn_b.begin()
            with pytest.raises(SQLAlchemyError) as e:
                db.begin_login_attempt(orm(conn_b), login_as(), LIMIT_IP, T)
            timed_out = getattr(getattr(e.value, "orig", None), "sqlstate", None) == "55P03"
            del e
            trans_b.rollback()
            trans_a.rollback()
            assert timed_out, "잠금 대기 시간 초과(55P03)가 아님"
            with conn_b.begin():
                assert count(conn_b, db.AuthFailure) == 0
    finally:
        other.dispose()
        wait_for_backends_released(fresh_schema)


def run_concurrently(engine, attempts: int, call) -> list[bool]:
    # 스레드마다 자기 연결·트랜잭션(커밋)으로 call(session, i) 을 동시에 시작한다. 결과(허용 여부) 목록.
    # 스레드는 daemon 이 아니다(프로세스가 끝나기 전에 연결을 닫게). 예외는 이름만 기록하고 아래에서 실패로 본다
    start = threading.Barrier(attempts)
    results: list[bool | str] = [""] * attempts

    def worker(i: int) -> None:
        conn = None
        try:
            conn = engine.connect()
            start.wait(timeout=BARRIER_TIMEOUT)
            with conn.begin():
                results[i] = call(orm(conn), i).decision.allowed
        except BaseException as e:                                           # noqa: BLE001 — 아래에서 실패로 본다
            results[i] = type(e).__name__
            start.abort()                                                    # 다른 스레드가 Barrier 에서 오래 기다리지 않게
        finally:
            if conn is not None:
                conn.close()                                                 # 정상·예외 모두 자기 연결을 닫는다

    threads = [threading.Thread(target=worker, args=(i,), daemon=False) for i in range(attempts)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + JOIN_TIMEOUT
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    alive = [t for t in threads if t.is_alive()]
    if alive:
        _UNFINISHED_WORKERS.extend(alive)                                    # 정리 단계에서 세션을 멈추게 한다
        pytest.fail(f"작업 스레드 {len(alive)}개가 {JOIN_TIMEOUT}초 안에 끝나지 않음")
    failures = sorted({r for r in results if not isinstance(r, bool)})
    assert not failures, failures
    return results  # type: ignore[return-value]


def test_concurrent_same_username_is_exact(fresh_schema):
    # 같은 username·IP 로 동시 10개 → 잠금 직렬화로 정확히 5개만 선기록, 5개는 차단
    other = nullpool_engine()
    try:
        with other.connect() as check:
            db.verify_test_server(check)
            check.rollback()
        results = run_concurrently(other, 10, lambda s, i: db.begin_login_attempt(s, login_as(), LIMIT_IP, T))
        assert results.count(True) == 5 and results.count(False) == 5
        with other.connect() as check, check.begin():
            assert count(check, db.AuthFailure) == 5
    finally:
        other.dispose()
        wait_for_backends_released(fresh_schema)


def test_concurrent_ip_rule_is_approximate(fresh_schema):
    # 같은 IP·서로 다른 username 동시 25개 → IP 규칙은 잠금이 없어 근사: 허용 ≥ 20 이고 ≤ 25, 끝난 뒤 다음 요청은 차단
    other = nullpool_engine()
    try:
        with other.connect() as check:
            db.verify_test_server(check)
            check.rollback()
        results = run_concurrently(
            other, 25, lambda s, i: db.begin_login_attempt(s, login_as(f"user{i:02d}"), LIMIT_IP, T))
        assert 20 <= results.count(True) <= 25
        with other.connect() as check, check.begin():
            assert count(check, db.AuthFailure) == results.count(True)
            assert not db.begin_login_attempt(orm(check), login_as("late"), LIMIT_IP, T).decision.allowed
    finally:
        other.dispose()
        wait_for_backends_released(fresh_schema)


# ---------- Phase 2-E: /auth/* HTTP 흐름 (실제 PostgreSQL) ----------
# 앱의 Session 팩토리 의존성(main.auth_session_factory)만 테스트 엔진의 sessionmaker 로 바꾼다 — DATABASE_URL 은 쓰지 않는다.
# 이 테스트들은 실제로 commit 한다(다음 테스트의 fresh_schema 가 지운다). 마지막 테스트(rolled_back)보다 앞에 둔다.
# 순서대로 하나씩 도는 요청은 fresh_schema 엔진(풀 연결 1개를 다시 쓴다), 동시·잠금 보유 연결은 NullPool 엔진을 쓰고
# finally 에서 닫은 뒤 wait_for_backends_released 로 다음 R-19 게이트 조건을 확인한다.

HTTP_SETTINGS = auth.AuthSettings(app_env="test", cookie_secure=False, registration_enabled=True)
HTTP_ORIGIN = "http://testserver"
HTTP_PW = "correct-horse-9"
COOKIE = HTTP_SETTINGS.cookie_name
REAL_VERIFY_LOGIN = auth.verify_login_password


def use_app_engine(engine) -> None:
    # 앱이 이 엔진으로 트랜잭션마다 새 Session 을 연다 (설정은 test, 키는 테스트 고정 키)
    main.app.dependency_overrides[main.auth_config] = lambda: main.AuthConfig(settings=HTTP_SETTINGS,
                                                                              failure_key=LIMIT_KEY)
    main.app.dependency_overrides[main.auth_session_factory] = lambda: sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def http_app(fresh_schema, monkeypatch):
    # fresh_schema(R-19 게이트 → 초기화) 다음에 앱 의존성을 바꾸고, 끝나면(실패해도) 되돌린다
    monkeypatch.setattr(logging.getLogger("todo.auth"), "disabled", False)   # Alembic fileConfig 가 끈 로거를 켠다
    use_app_engine(fresh_schema)
    try:
        yield fresh_schema
    finally:
        main.app.dependency_overrides.clear()


def http_client(ip: str = LIMIT_IP) -> TestClient:
    return TestClient(main.app, client=(ip, 50000))                  # IP 를 명시한다 (기본 'testclient' 는 500)


def call(client: TestClient, method: str, path: str, *, body: dict | None = None, token: str | None = None,
         csrf: str | None = None):
    # 쿠키 저장소에 기대지 않고 요청마다 쿠키·헤더를 명시한다
    headers = {"Origin": HTTP_ORIGIN}
    if token is not None:
        headers["Cookie"] = f"{COOKIE}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    client.cookies.clear()
    res = client.request(method, path, json=body, headers=headers)
    client.cookies.clear()
    return res


def creds(username: str = "alice", password: str = HTTP_PW) -> dict:
    return {"username": username, "password": password}


def db_state(engine) -> dict:
    with engine.connect() as conn:
        state = {"users": count(conn, db.User), "sessions": session_count(conn),
                 "failures": [(r.id, r.kind, r.username_key) for r in failure_rows(conn)]}
        conn.rollback()
    return state


def assert_no_secret(res, caplog, *secrets: str) -> None:
    # PostgreSQL DETAIL·행 값·비밀번호가 응답과 앱 로그에 없다
    for secret in ("DETAIL", "Failing row", "Key (", HTTP_PW, *secrets):
        assert secret not in res.text and secret not in caplog.text, secret
    assert "Traceback" not in caplog.text and all(r.exc_info is None for r in caplog.records)


def test_http_register_me_logout_flow(http_app):
    c = http_client()
    created = call(c, "POST", "/auth/register", body=creds("Alice"))
    assert created.status_code == 201
    body, token = created.json(), created.cookies.get(COOKIE)
    assert body["username"] == "alice" and token and token not in created.text
    me = call(c, "GET", "/auth/me", token=token)
    assert me.status_code == 200 and me.json() == body
    assert db_state(http_app)["sessions"] == 1
    assert call(c, "POST", "/auth/logout", token=token, csrf=body["csrf_token"]).status_code == 204
    assert call(c, "GET", "/auth/me", token=token).status_code == 401
    state = db_state(http_app)
    assert state["users"] == 1 and state["sessions"] == 0
    assert [(k, u) for _, k, u in state["failures"]] == [("register", None)]       # 201 은 가입 시도로 센다


def test_http_login_failures_then_429(http_app):
    c = http_client()
    assert call(c, "POST", "/auth/register", body=creds()).status_code == 201
    for _ in range(5):
        res = call(c, "POST", "/auth/login", body=creds(password="wrong-password-1"))
        assert res.status_code == 401 and res.json() == {"detail": main.LOGIN_FAILED}
    blocked = call(c, "POST", "/auth/login", body=creds())                         # 맞는 비밀번호도 검증 전에 차단
    assert blocked.status_code == 429 and 1 <= int(blocked.headers["retry-after"]) <= 900
    assert COOKIE not in blocked.headers.get("set-cookie", "")
    logins = [r for r in db_state(http_app)["failures"] if r[1] == "login"]
    assert len(logins) == 5 and {u for _, _, u in logins} == {login_as().failure_key}   # 429 는 기록하지 않는다


def test_http_login_success_deletes_only_own_attempt(http_app):
    c = http_client()
    assert call(c, "POST", "/auth/register", body=creds()).status_code == 201
    assert call(c, "POST", "/auth/login", body=creds(password="wrong-password-1")).status_code == 401
    with http_app.begin() as conn:                                                 # 다른 요청의 선기록(합성)
        conn.execute(insert(db.AuthFailure).values(kind="login", username_key=login_as().failure_key,
                                                   client_ip="198.51.100.1", created_at=datetime.now(timezone.utc)))
    before = db_state(http_app)["failures"]
    res = call(c, "POST", "/auth/login", body=creds())
    assert res.status_code == 200 and res.cookies.get(COOKIE)
    after = db_state(http_app)
    assert after["failures"] == before                                             # 자기 선기록만 지워졌다
    assert after["sessions"] == 2                                                  # 가입 세션 + 로그인 세션


def test_http_duplicate_register_409_is_counted(http_app):
    c = http_client()
    assert call(c, "POST", "/auth/register", body=creds()).status_code == 201
    dup = call(c, "POST", "/auth/register", body=creds("ALICE", "another-password-2"))
    assert dup.status_code == 409 and dup.json() == {"detail": main.USERNAME_TAKEN}
    assert COOKIE not in dup.headers.get("set-cookie", "")
    state = db_state(http_app)
    assert state["users"] == 1 and state["sessions"] == 1
    assert [k for _, k, _ in state["failures"]] == ["register", "register"]        # 409 도 commit 되어 센다


def test_http_register_unique_race_uses_savepoint(http_app, monkeypatch, caplog):
    # 사전 조회를 지나친 경쟁을 재현한다 — 실제 UNIQUE 위반을 SAVEPOINT 가 받아 409, 시도 기록은 commit
    c = http_client()
    assert call(c, "POST", "/auth/register", body=creds()).status_code == 201
    monkeypatch.setattr(db, "username_exists", lambda session, username: False)
    caplog.set_level(logging.DEBUG)
    dup = call(c, "POST", "/auth/register", body=creds(password="another-password-2"))
    assert dup.status_code == 409
    state = db_state(http_app)
    assert state["users"] == 1 and [k for _, k, _ in state["failures"]] == ["register", "register"]
    assert_no_secret(dup, caplog, "another-password-2")


def test_http_other_constraint_violation_is_500_not_409(http_app, monkeypatch, caplog):
    # NOT NULL 위반(다른 제약)은 409 로 바꾸지 않는다 → rollback·500, 시도로 세지 않음, DETAIL(행 값) 비노출
    monkeypatch.setattr(auth, "hash_password_limited", lambda password: None)
    caplog.set_level(logging.DEBUG)
    res = call(http_client(), "POST", "/auth/register", body=creds("carol"))
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert "auth_register_error exc_type=IntegrityError" in caplog.text
    assert_no_secret(res, caplog, "carol")
    assert db_state(http_app) == {"users": 0, "sessions": 0, "failures": []}


def test_http_concurrent_same_username_register(fresh_schema, monkeypatch):
    # 같은 username 동시 가입 2건 → 201 한 건, 409 한 건, 두 시도 모두 기록. 연결은 NullPool, 끝나면 정리 확인
    monkeypatch.setattr(logging.getLogger("todo.auth"), "disabled", False)
    other = nullpool_engine()
    statuses: dict[int, int] = {}

    def register_once(session, i):
        statuses[i] = call(http_client(), "POST", "/auth/register", body=creds()).status_code
        return SimpleNamespace(decision=SimpleNamespace(allowed=statuses[i] == 201))

    try:
        use_app_engine(other)
        results = run_concurrently(other, 2, register_once)
        assert sorted(statuses.values()) == [201, 409] and results.count(True) == 1
        state = db_state(fresh_schema)
        assert state["users"] == 1 and [k for _, k, _ in state["failures"]] == ["register", "register"]
    finally:
        main.app.dependency_overrides.clear()
        other.dispose()
        wait_for_backends_released(fresh_schema)


def test_http_login_rehashes_outdated_hash(http_app):
    c = http_client()
    assert call(c, "POST", "/auth/register", body=creds()).status_code == 201
    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(HTTP_PW)
    with http_app.begin() as conn:
        conn.execute(update(db.User).where(db.User.username == "alice").values(password_hash=weak))
    assert call(c, "POST", "/auth/login", body=creds()).status_code == 200
    with http_app.connect() as conn:
        stored = conn.execute(select(db.User.password_hash).where(db.User.username == "alice")).scalar_one()
        conn.rollback()
    assert stored != weak and not auth.needs_rehash(stored) and auth.verify_password(stored, HTTP_PW)


def test_http_relogin_replaces_only_presented_session(http_app):
    first = call(http_client(), "POST", "/auth/register", body=creds())
    old_token = first.cookies.get(COOKIE)
    other_device = call(http_client(), "POST", "/auth/login", body=creds()).cookies.get(COOKIE)
    relogin = call(http_client(), "POST", "/auth/login", body=creds(), token=old_token)
    assert relogin.status_code == 200
    new_token = relogin.cookies.get(COOKIE)
    c = http_client()
    assert call(c, "GET", "/auth/me", token=old_token).status_code == 401          # 제시한 세션만 교체
    assert call(c, "GET", "/auth/me", token=other_device).status_code == 200       # 다른 기기 세션은 유지
    assert call(c, "GET", "/auth/me", token=new_token).status_code == 200
    assert db_state(http_app)["sessions"] == 2


def test_http_tx2_lock_contention_is_503_without_session(http_app, monkeypatch):
    # Tx1 commit 뒤·Tx2 전에 다른 연결이 같은 username 잠금을 잡는다 → Tx2 가 2초 뒤 55P03 → 503, 세션 없음, 선기록 남음
    assert call(http_client(), "POST", "/auth/register", body=creds()).status_code == 201
    lock = text("SELECT pg_advisory_xact_lock(hashtextextended('login:' || :k, 0))")
    other = nullpool_engine()
    holder = {}

    def verify_then_hold_lock(stored_hash, password):
        verified = REAL_VERIFY_LOGIN(stored_hash, password)
        conn = other.connect()
        holder["conn"], holder["trans"] = conn, conn.begin()
        conn.execute(lock, {"k": login_as().failure_key})
        return verified

    monkeypatch.setattr(auth, "verify_login_password", verify_then_hold_lock)
    try:
        res = call(http_client(), "POST", "/auth/login", body=creds())
        assert res.status_code == 503 and res.json() == {"detail": main.SERVICE_BUSY}
        assert COOKIE not in res.headers.get("set-cookie", "")
    finally:
        if "trans" in holder:
            holder["trans"].rollback()
            holder["conn"].close()
        other.dispose()
        wait_for_backends_released(http_app)
    state = db_state(http_app)
    assert state["sessions"] == 1                                                  # 가입 세션뿐 (로그인 세션 없음)
    assert [k for _, k, _ in state["failures"]] == ["register", "login"]           # 선기록은 실패로 남는다


def test_http_hash_busy_compensates_own_attempt(http_app, monkeypatch):
    c = http_client()
    assert call(c, "POST", "/auth/register", body=creds()).status_code == 201
    assert call(c, "POST", "/auth/login", body=creds(password="wrong-password-1")).status_code == 401
    before = db_state(http_app)["failures"]

    def busy(stored_hash, password):
        raise auth.HashBusy()

    monkeypatch.setattr(auth, "verify_login_password", busy)
    res = call(c, "POST", "/auth/login", body=creds())
    assert res.status_code == 503 and COOKIE not in res.headers.get("set-cookie", "")
    assert db_state(http_app)["failures"] == before                                # 보상 Tx 가 자기 선기록만 지웠다


def test_http_missing_own_attempt_is_500_without_session(http_app, monkeypatch):
    # Tx1 commit 뒤 자기 선기록이 사라지면(합성) finish_login_success 가 False → Tx2 rollback·500, 세션 없음
    assert call(http_client(), "POST", "/auth/register", body=creds()).status_code == 201

    def verify_then_delete_attempt(stored_hash, password):
        with http_app.begin() as conn:
            conn.execute(delete(db.AuthFailure).where(db.AuthFailure.kind == "login"))
        return REAL_VERIFY_LOGIN(stored_hash, password)

    monkeypatch.setattr(auth, "verify_login_password", verify_then_delete_attempt)
    res = call(http_client(), "POST", "/auth/login", body=creds())
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert COOKIE not in res.headers.get("set-cookie", "")
    state = db_state(http_app)
    assert state["sessions"] == 1 and [k for _, k, _ in state["failures"]] == ["register"]


# ---------- (T-A) 가입 정규화·실제 DB 해시, 고정 시계 14일 만료 HTTP 흐름 ----------
# 비밀번호·해시·세션 토큰·CSRF 값이 실패 메시지에 나오지 않도록, 비교 결과를 변수에 먼저 담고 그 값만 단정한다.

def test_http_register_normalizes_username_and_stores_argon2id(http_app, caplog):
    caplog.set_level(logging.DEBUG)
    created = call(http_client(), "POST", "/auth/register", body=creds(" Alice "))
    created_username = created.json().get("username")
    assert created.status_code == 201 and created_username == "alice"
    with http_app.connect() as conn:
        rows = conn.execute(select(db.User.username, db.User.password_hash)).all()
        conn.rollback()
    assert [r.username for r in rows] == ["alice"]                                 # 정규화한 값으로 정확히 1명
    stored = rows[0].password_hash
    hash_ok = stored.startswith("$argon2id$") and auth.verify_password(stored, HTTP_PW) and HTTP_PW not in stored
    assert hash_ok
    dup = call(http_client(), "POST", "/auth/register", body=creds("\tALICE ", "another-password-2"))
    assert dup.status_code == 409 and dup.json() == {"detail": main.USERNAME_TAKEN}
    dup_has_cookie = COOKIE in dup.headers.get("set-cookie", "")
    assert not dup_has_cookie
    login = call(http_client(), "POST", "/auth/login", body=creds(" alice "))
    login_username = login.json().get("username")
    assert login.status_code == 200 and login_username == "alice"
    state = db_state(http_app)
    assert state["users"] == 1 and state["sessions"] == 2                         # 가입 + 로그인 세션 (409 는 세션 없음)
    hash_leaked = any(stored in r.text for r in (created, dup, login)) or stored in caplog.text
    assert not hash_leaked
    for res in (created, dup, login):
        assert_no_secret(res, caplog)


def test_http_session_expires_after_14_days_with_fixed_clock(http_app, monkeypatch):
    # 시계는 앞으로만 옮긴다(sleep 없음). 정확히 14일이 되는 순간부터 401. /auth/me 는 만료 세션을 지우지 않고
    # 다음 로그인(가입·로그인 때의 purge_expired_sessions)이 정리한다. 옛 쿠키 없이 로그인해 교체가 아니라 정리임을 본다
    start = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    clock = [start]
    monkeypatch.setattr(main, "now", lambda: clock[0])
    c = http_client()

    def me_status(token) -> int:
        return call(c, "GET", "/auth/me", token=token).status_code

    def stored_expiries() -> list:
        with http_app.connect() as conn:
            values = conn.execute(select(db.UserSession.expires_at)).scalars().all()
            conn.rollback()
        return values

    created = call(http_client(), "POST", "/auth/register", body=creds())
    assert created.status_code == 201
    old_token = created.cookies.get(COOKIE)
    has_old_token = old_token is not None
    max_age_ok = "Max-Age=1209600" in created.headers.get("set-cookie", "")
    assert has_old_token and max_age_ok
    assert stored_expiries() == [start + timedelta(days=14)]                     # 실제 TIMESTAMPTZ 저장값

    clock[0] = start + timedelta(days=14) - timedelta(microseconds=1)
    status = me_status(old_token)
    assert status == 200                                                          # 만료 1µs 전
    clock[0] = start + timedelta(days=14)
    status = me_status(old_token)
    assert status == 401                                                          # 같으면 만료
    assert stored_expiries() == [start + timedelta(days=14)]                     # /auth/me 는 지우지 않는다
    clock[0] = start + timedelta(days=15)
    status = me_status(old_token)
    assert status == 401

    login = call(http_client(), "POST", "/auth/login", body=creds())
    assert login.status_code == 200
    new_token = login.cookies.get(COOKIE)
    tokens_distinct = new_token is not None and new_token != old_token
    assert tokens_distinct
    assert stored_expiries() == [clock[0] + timedelta(days=14)]                  # 만료 세션은 정리되고 새 세션만
    old_status, new_status = me_status(old_token), me_status(new_token)
    assert (old_status, new_status) == (401, 200)


# ---------- (Phase 3-1B-1) 요청 Session(main.request_db)의 실제 연결 반환 ----------
# http_app 의 엔진(fresh_schema 의 모듈 엔진, 기본 풀)을 그대로 쓴다. 요청은 하나씩 차례로 보내고, 요청 중에는
# 이 테스트가 다른 연결을 붙잡지 않는다. 풀 이벤트는 관찰(체크아웃·반환 횟수)만 하고 풀 설정은 바꾸지 않는다.
# 끝에서 wait_for_backends_released 로 다음 테스트의 R-19 게이트와 같은 조건(DB 연결 1개·다른 세션 0)을 확인한다.

@contextmanager
def pool_checkouts(engine):
    # 블록 안에서 일어난 연결 체크아웃·반환 횟수 — 끝나면(실패해도) 리스너를 뗀다
    counts = {"checkout": 0, "checkin": 0}

    def on_checkout(dbapi_conn, record, proxy):
        counts["checkout"] += 1

    def on_checkin(dbapi_conn, record):
        counts["checkin"] += 1

    event.listen(engine, "checkout", on_checkout)
    event.listen(engine, "checkin", on_checkin)
    try:
        yield counts
    finally:
        event.remove(engine, "checkout", on_checkout)
        event.remove(engine, "checkin", on_checkin)


def test_http_me_without_cookie_uses_no_connection(http_app):
    # 쿠키 없는 /auth/me 는 401 이고 요청 Session 을 열지 않는다 — 연결을 꺼내지도 남기지도 않는다
    c = http_client()
    with pool_checkouts(http_app) as counts:
        statuses = [call(c, "GET", "/auth/me").status_code for _ in range(5)]
    assert statuses == [401] * 5
    assert counts == {"checkout": 0, "checkin": 0}
    assert http_app.pool.checkedout() == 0
    wait_for_backends_released(http_app)                                     # 다음 R-19 게이트 조건 (연결 1·다른 세션 0)


def test_http_me_with_session_returns_connection_each_request(http_app):
    # 유효한 세션의 /auth/me 는 요청마다 Session 하나(연결 체크아웃 1회)를 쓰고, 끝나면 연결을 풀에 돌려준다
    c = http_client()
    created = call(c, "POST", "/auth/register", body=creds())
    assert created.status_code == 201
    token = created.cookies.get(COOKIE)
    has_token = token is not None
    assert has_token
    assert http_app.pool.checkedout() == 0
    with pool_checkouts(http_app) as counts:
        responses = [call(c, "GET", "/auth/me", token=token) for _ in range(5)]
    statuses = [r.status_code for r in responses]
    same_user = all(r.json().get("username") == "alice" for r in responses)
    assert statuses == [200] * 5 and same_user
    assert counts == {"checkout": 5, "checkin": 5}                            # 요청당 Session·연결 1개, 모두 반환
    assert http_app.pool.checkedout() == 0
    assert db_state(http_app)["sessions"] == 1                               # 조회는 세션 행을 바꾸지 않는다
    wait_for_backends_released(http_app)


# ---------- (Phase 3-1B-2) 사용자 데이터 헬퍼의 소유권 격리 (실제 PostgreSQL) ----------
# 모두 rolled_back(한 연결·한 트랜잭션, 끝나면 되돌림)이라 데이터를 남기지 않는다. 헬퍼는 같은 연결의 ORM Session 으로
# 부르고, 쓰기 전에는 그 사용자로 lock_user 를 부른다(쓰기 헬퍼 규약). 원본이 그대로인지는 소유권 조건 없이 직접 읽어
# 비교한다 — 확인용 읽기일 뿐 헬퍼 결과를 만들거나 바꾸는 데 쓰지 않는다. 사용자 A·B 는 테스트마다 새로 만든다.

def todo_snapshot(conn, todo_id: int):
    return conn.execute(select(db.Todo.__table__).where(db.Todo.id == todo_id)).one()


def subtask_snapshot(conn, todo_id: int) -> list:
    return conn.execute(select(db.Subtask.__table__).where(db.Subtask.todo_id == todo_id)
                        .order_by(db.Subtask.id)).all()


def template_snapshot(conn, template_id: int):
    return conn.execute(select(db.TaskTemplate.__table__).where(db.TaskTemplate.id == template_id)).one()


def missing_todo_id(conn) -> int:
    return conn.execute(select(func.coalesce(func.max(db.Todo.id), 0) + 1000)).scalar_one()


def missing_template_id(conn) -> int:
    return conn.execute(select(func.coalesce(func.max(db.TaskTemplate.id), 0) + 1000)).scalar_one()


def test_todo_helpers_isolate_users(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, alice)
        a1 = db.insert_todo(s, alice, {"title": "A 첫째", "priority": "높음"})
        a2 = db.insert_todo(s, alice, {"title": "A 둘째"})
        assert db.lock_user(s, bob)
        b1 = db.insert_todo(s, bob, {"title": "B 첫째"})
        assert len({a1, a2, b1}) == 3                                       # 서로 다른 행
        before = todo_snapshot(conn, a1)

        # A 는 자기 것만, B 는 자기 것만 본다
        assert [t.id for t in db.active_todos(s, alice)] == [a1, a2]
        assert [t.id for t in db.active_todos(s, bob)] == [b1]
        assert db.get_owned_todo(s, alice, a1).title == "A 첫째"
        assert db.get_owned_todo(s, bob, a1) is None
        assert set(db.owned_todos_by_ids(s, bob, [a1, a2, b1])) == {b1}     # 섞인 id 에서도 자기 것만
        assert set(db.owned_todos_by_ids(s, alice, [a1, a2, b1])) == {a1, a2}

        # B 의 수정·삭제 시도는 실패하고 A 의 원본은 그대로다
        assert db.lock_user(s, bob)
        assert db.update_owned_todo(s, bob, a1, {"title": "B 가 바꿈", "completed": True}) is False
        assert db.soft_delete_todos(s, bob, [a1, a2], T) == []
        assert todo_snapshot(conn, a1) == before
        assert todo_snapshot(conn, a2).deleted_at is None
        assert [t.id for t in db.active_todos(s, alice)] == [a1, a2]

        # A 자신의 수정은 된다 (대조)
        assert db.lock_user(s, alice)
        assert db.update_owned_todo(s, alice, a1, {"title": "A 가 바꿈"}) is True
        assert db.get_owned_todo(s, alice, a1).title == "A 가 바꿈"
        assert db.get_owned_todo(s, bob, b1).title == "B 첫째"              # B 의 행은 영향 없음
        assert count(conn, db.Todo) == 3


def test_subtask_helpers_require_parent_owner(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, alice)
        a_todo = db.insert_todo(s, alice, {"title": "A 상위"}, subtasks=[("하나", False), ("둘", True)])
        assert db.add_subtask(s, alice, a_todo, "셋") == 3                  # sid = max + 1
        assert db.lock_user(s, bob)
        b_todo = db.insert_todo(s, bob, {"title": "B 상위"}, subtasks=[("하나", False)])
        before = subtask_snapshot(conn, a_todo)
        assert [(r.id, r.title, r.completed) for r in before] == [(1, "하나", False), (2, "둘", True), (3, "셋", False)]

        # 조회: 상위 소유자만
        assert [r.id for r in db.subtasks_of(s, alice, [a_todo])[a_todo]] == [1, 2, 3]
        assert db.subtasks_of(s, bob, [a_todo]) == {}
        assert set(db.subtasks_of(s, bob, [a_todo, b_todo])) == {b_todo}

        # B 의 추가·수정·삭제 시도 (todo_id 와 sid 를 함께 지정) — 모두 실패, A 의 하위 할 일은 그대로
        assert db.lock_user(s, bob)
        assert db.add_subtask(s, bob, a_todo, "B 가 추가") is None
        assert db.update_owned_subtask(s, bob, a_todo, 1, {"title": "B 가 바꿈", "completed": True}) is False
        assert db.delete_owned_subtask(s, bob, a_todo, 2) is False
        assert db.update_owned_subtask(s, bob, b_todo, 2, {"completed": True}) is False   # 자기 상위에 없는 sid
        assert subtask_snapshot(conn, a_todo) == before

        # 다른 상위의 같은 sid 는 다른 행이다 — B 가 자기 sid 1 을 바꿔도 A 의 sid 1 은 그대로
        assert db.update_owned_subtask(s, bob, b_todo, 1, {"title": "B 의 하나"}) is True
        assert subtask_snapshot(conn, a_todo) == before

        # A 자신은 된다 (대조)
        assert db.lock_user(s, alice)
        assert db.update_owned_subtask(s, alice, a_todo, 1, {"completed": True}) is True
        assert db.delete_owned_subtask(s, alice, a_todo, 3) is True
        assert [(r.id, r.completed) for r in subtask_snapshot(conn, a_todo)] == [(1, True), (2, True)]


def test_template_helpers_isolate_users_and_allow_same_name(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, alice)
        a_tpl = db.insert_template(s, alice, {"name": "주간 보고", "title": "보고서", "tags": ["업무"]})
        assert db.lock_user(s, bob)
        b_tpl = db.insert_template(s, bob, {"name": "주간 보고", "title": "B 보고서"})   # 다른 사용자는 같은 이름 허용
        assert a_tpl != b_tpl
        before = template_snapshot(conn, a_tpl)

        assert [t.id for t in db.owned_templates(s, alice)] == [a_tpl]
        assert [t.id for t in db.owned_templates(s, bob)] == [b_tpl]
        assert db.get_owned_template(s, bob, a_tpl) is None
        assert db.get_owned_template(s, alice, a_tpl).title == "보고서"

        # 이름 중복 검사는 사용자 안에서만
        assert db.template_name_taken(s, alice, "주간 보고") is True
        assert db.template_name_taken(s, alice, "주간 보고", exclude_id=a_tpl) is False   # 자기 자신은 제외
        assert db.template_name_taken(s, bob, "주간 보고", exclude_id=b_tpl) is False      # A 의 같은 이름은 B 와 무관
        assert db.template_name_taken(s, bob, "다른 이름") is False

        # B 의 수정·삭제 시도 — 실패, A 의 템플릿은 그대로
        assert db.lock_user(s, bob)
        assert db.update_owned_template(s, bob, a_tpl, {"title": "B 가 바꿈"}) is False
        assert db.delete_owned_template(s, bob, a_tpl) is False
        assert template_snapshot(conn, a_tpl) == before
        assert [t.id for t in db.owned_templates(s, alice)] == [a_tpl]

        # 각자 자기 것은 바꾸고 지울 수 있다 (대조)
        assert db.update_owned_template(s, bob, b_tpl, {"title": "B 가 바꿈"}) is True
        assert db.delete_owned_template(s, bob, b_tpl) is True
        assert db.owned_templates(s, bob) == []
        assert template_snapshot(conn, a_tpl) == before
        assert count(conn, db.TaskTemplate) == 1


def test_positions_are_independent_per_user(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, alice)
        assert db.next_todo_position(s, alice) == 1
        a_ids = [db.insert_todo(s, alice, {"title": f"A{i}"}) for i in range(3)]
        assert db.lock_user(s, bob)
        assert db.next_todo_position(s, bob) == 1                          # A 의 행과 무관하게 1부터
        b_first = db.insert_todo(s, bob, {"title": "B0"})
        assert db.lock_user(s, alice)
        a_ids.append(db.insert_todo(s, alice, {"title": "A3"}))            # A 가 더 추가해도
        assert db.lock_user(s, bob)
        assert db.next_todo_position(s, bob) == 2                          # B 의 다음 번호는 그대로

        a_rows = db.active_todos(s, alice)
        assert [t.id for t in a_rows] == a_ids and [t.position for t in a_rows] == [1, 2, 3, 4]   # position·id 순
        assert [(t.id, t.position) for t in db.active_todos(s, bob)] == [(b_first, 1)]

        # 템플릿 position 도 사용자마다 1부터
        assert db.lock_user(s, alice)
        a_tpls = [db.insert_template(s, alice, {"name": f"A{i}", "title": "t"}) for i in range(2)]
        assert db.lock_user(s, bob)
        b_tpl = db.insert_template(s, bob, {"name": "B0", "title": "t"})
        assert [(t.id, t.position) for t in db.owned_templates(s, alice)] == [(a_tpls[0], 1), (a_tpls[1], 2)]
        assert [(t.id, t.position) for t in db.owned_templates(s, bob)] == [(b_tpl, 1)]


def test_other_users_ids_look_the_same_as_missing_ids(fresh_schema):
    # B 입장에서 "A 의 id" 와 "없는 id" 의 결과가 같다 — 존재 여부를 드러내지 않는다(404 를 같게 만들 근거)
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, alice)
        a_todo = db.insert_todo(s, alice, {"title": "A"}, subtasks=[("하나", False)])
        a_tpl = db.insert_template(s, alice, {"name": "A", "title": "t"})
        gone_todo, gone_tpl = missing_todo_id(conn), missing_template_id(conn)
        assert db.lock_user(s, bob)

        def outcomes(todo_id: int, template_id: int) -> tuple:
            return (
                db.get_owned_todo(s, bob, todo_id),
                db.owned_todos_by_ids(s, bob, [todo_id]),
                db.subtasks_of(s, bob, [todo_id]),
                db.update_owned_todo(s, bob, todo_id, {"title": "x"}),
                db.soft_delete_todos(s, bob, [todo_id], T),
                db.add_subtask(s, bob, todo_id, "x"),
                db.update_owned_subtask(s, bob, todo_id, 1, {"title": "x"}),
                db.delete_owned_subtask(s, bob, todo_id, 1),
                db.get_owned_template(s, bob, template_id),
                db.update_owned_template(s, bob, template_id, {"title": "x"}),
                db.delete_owned_template(s, bob, template_id),
            )

        expected = (None, {}, {}, False, [], None, False, False, None, False, False)
        assert outcomes(a_todo, a_tpl) == expected
        assert outcomes(gone_todo, gone_tpl) == expected
        assert count(conn, db.Todo) == 1 and count(conn, db.Subtask) == 1 and count(conn, db.TaskTemplate) == 1


# ---------- (Phase 3-1B-3) soft delete·복구·정리·position (실제 PostgreSQL) ----------
# 모두 rolled_back(한 연결·한 트랜잭션, 끝나면 되돌림) — 커밋·추가 연결·실제 시간 대기 없음. 시각은 헬퍼에 넘기는 now
# 와 soft delete 시각(T 기준 고정값)으로만 정한다. 쓰기 헬퍼 앞에서는 그 사용자로 lock_user 를 부른다.
# 같은 deleted_at 의 100건 경계 순서, bulk·반복 생성 때의 정리 시점은 정하지 않았으므로 여기서 확정하지 않는다.

WINDOW = timedelta(seconds=30)
MICRO = timedelta(microseconds=1)


def deleted_at_of(conn, todo_id: int):
    return conn.execute(select(db.Todo.deleted_at).where(db.Todo.id == todo_id)).scalar_one_or_none()


def todo_exists(conn, todo_id: int) -> bool:
    return conn.execute(select(func.count()).select_from(db.Todo).where(db.Todo.id == todo_id)).scalar_one() == 1


def test_restore_window_boundary_in_database(fresh_schema):
    # 같은 시각 T 에 지운 항목 3개를 각각 다른 now 로 복구해 본다: 30초 − 1µs 성공, 정확히 30초·초과는 실패
    with rolled_back(fresh_schema) as conn:
        alice = make_user(conn, "alice")
        s = orm(conn)
        assert db.lock_user(s, alice)
        ids = [db.insert_todo(s, alice, {"title": f"A{i}"}) for i in range(3)]
        assert db.soft_delete_todos(s, alice, ids, T) == ids
        just_in, exact, late = ids

        assert db.restore_todos(s, alice, [just_in], T + WINDOW - MICRO) == [just_in]
        assert deleted_at_of(conn, just_in) is None
        assert db.restore_todos(s, alice, [exact], T + WINDOW) is None                  # 같으면 만료 (> 비교)
        assert db.restore_todos(s, alice, [late], T + WINDOW + timedelta(seconds=1)) is None
        assert deleted_at_of(conn, exact) == T and deleted_at_of(conn, late) == T       # 실패한 복구는 바꾸지 않는다
        assert [t.id for t in db.active_todos(s, alice)] == [just_in]                    # 만료 행은 정리 전에도 안 보인다

        assert db.purge_expired_deleted_todos(s, alice, T + WINDOW) == 2                 # <= now − 30초 만 완전 삭제
        assert not todo_exists(conn, exact) and not todo_exists(conn, late)
        assert todo_exists(conn, just_in)


def test_restore_is_all_or_nothing_in_database(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        now = T + timedelta(seconds=1)
        assert db.lock_user(s, alice)
        a1, a2, a_old = (db.insert_todo(s, alice, {"title": f"A{i}"}) for i in range(3))
        assert db.soft_delete_todos(s, alice, [a1, a2], T) == [a1, a2]
        assert db.soft_delete_todos(s, alice, [a_old], T - timedelta(seconds=60)) == [a_old]   # now 기준 만료
        assert db.lock_user(s, bob)
        b1 = db.insert_todo(s, bob, {"title": "B"})
        assert db.soft_delete_todos(s, bob, [b1], T) == [b1]
        missing = conn.execute(select(func.max(db.Todo.id))).scalar_one() + 1000
        before = {i: todo_snapshot(conn, i) for i in (a1, a2, a_old, b1)}

        assert db.lock_user(s, alice)
        for mixed in ([a1, a2, b1], [a1, missing], [a1, a_old], [b1]):                 # 하나라도 부적합하면
            assert db.restore_todos(s, alice, mixed, now) is None
            assert {i: todo_snapshot(conn, i) for i in before} == before                 # 아무것도 바뀌지 않는다

        assert db.restore_todos(s, alice, [a2, a1], now) == [a2, a1]                     # 유효한 자기 항목만 → 요청 순서
        assert deleted_at_of(conn, a1) is None and deleted_at_of(conn, a2) is None
        assert db.restore_todos(s, alice, [a1], now) is None                             # 두 번째 복구는 실패
        assert todo_snapshot(conn, b1) == before[b1] and todo_snapshot(conn, a_old) == before[a_old]


def test_pending_limit_keeps_newest_100(fresh_schema):
    # A 의 대기 행 101건을 서로 다른 시각에 하나씩 만들면 가장 오래된 1건만 완전 삭제(하위 할 일 포함), B 는 무관
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, bob)
        b1 = db.insert_todo(s, bob, {"title": "B"}, subtasks=[("b 하위", False)])
        assert db.soft_delete_todos(s, bob, [b1], T - timedelta(seconds=1))           # A 보다 오래된 B 의 대기 행
        b_before, b_sub_before = todo_snapshot(conn, b1), subtask_snapshot(conn, b1)

        assert db.lock_user(s, alice)
        ids = [db.insert_todo(s, alice, {"title": f"A{i}"}, subtasks=[(f"하위{i}", False)]) for i in range(101)]
        for i, todo_id in enumerate(ids):
            assert db.soft_delete_todos(s, alice, [todo_id], T + i * MICRO) == [todo_id]

        oldest, kept = ids[0], ids[1:]
        assert not todo_exists(conn, oldest) and subtask_snapshot(conn, oldest) == []  # CASCADE 로 하위도 사라짐
        pending = conn.execute(select(db.Todo.id).where(db.Todo.user_id == alice, db.Todo.deleted_at.is_not(None))
                               .order_by(db.Todo.id)).scalars().all()
        assert pending == kept and len(pending) == 100
        assert conn.execute(select(func.count()).select_from(db.Subtask)
                            .where(db.Subtask.todo_id.in_(kept))).scalar_one() == 100
        assert todo_snapshot(conn, b1) == b_before and subtask_snapshot(conn, b1) == b_sub_before


def test_purge_expired_only_touches_own_expired_rows(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, alice)
        expired, fresh, active = (db.insert_todo(s, alice, {"title": f"A{i}"}) for i in range(3))
        assert db.soft_delete_todos(s, alice, [expired], T - WINDOW - timedelta(seconds=1)) == [expired]
        assert db.soft_delete_todos(s, alice, [fresh], T - timedelta(seconds=10)) == [fresh]
        assert db.lock_user(s, bob)
        b_expired = db.insert_todo(s, bob, {"title": "B"})
        assert db.soft_delete_todos(s, bob, [b_expired], T - WINDOW - timedelta(seconds=1)) == [b_expired]
        before = {i: todo_snapshot(conn, i) for i in (fresh, active, b_expired)}

        assert db.lock_user(s, alice)
        assert db.purge_expired_deleted_todos(s, alice, T) == 1
        assert not todo_exists(conn, expired)
        assert {i: todo_snapshot(conn, i) for i in before} == before                     # A 미만료·활성, B 만료 행 그대로


def test_soft_deleted_rows_hidden_from_all_read_helpers(fresh_schema):
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        assert db.lock_user(s, bob)
        b1 = db.insert_todo(s, bob, {"title": "B"})
        b_before = todo_snapshot(conn, b1)
        assert db.lock_user(s, alice)
        gone = db.insert_todo(s, alice, {"title": "지울 것"}, subtasks=[("하위", False)])
        kept = db.insert_todo(s, alice, {"title": "남길 것"})
        assert db.soft_delete_todos(s, alice, [gone], T) == [gone]
        gone_before, sub_before = todo_snapshot(conn, gone), subtask_snapshot(conn, gone)

        # 조회 — 대기 행은 없는 것과 같다
        assert [t.id for t in db.active_todos(s, alice)] == [kept]
        assert db.get_owned_todo(s, alice, gone) is None
        assert set(db.owned_todos_by_ids(s, alice, [gone, kept])) == {kept}
        assert db.subtasks_of(s, alice, [gone]) == {}
        # 쓰기 — 모두 거부, 행은 그대로 (이미 지운 행을 다시 지워도 deleted_at 이 바뀌지 않는다)
        assert db.update_owned_todo(s, alice, gone, {"title": "x"}) is False
        assert db.soft_delete_todos(s, alice, [gone], T + timedelta(seconds=1)) == []
        assert db.add_subtask(s, alice, gone, "x") is None
        assert db.update_owned_subtask(s, alice, gone, 1, {"completed": True}) is False
        assert db.delete_owned_subtask(s, alice, gone, 1) is False
        assert todo_snapshot(conn, gone) == gone_before and subtask_snapshot(conn, gone) == sub_before
        assert todo_snapshot(conn, b1) == b_before


def test_position_after_delete_restore_and_purge(fresh_schema):
    # §27.3: 삭제해도 position 유지, 대기 중에는 그 번호를 다시 쓰지 않음, 복구하면 원래 자리, 완전 삭제 뒤 재사용 허용
    with rolled_back(fresh_schema) as conn:
        alice = make_user(conn, "alice")
        s = orm(conn)
        assert db.lock_user(s, alice)
        p1, p2, p3 = (db.insert_todo(s, alice, {"title": f"P{i}"}) for i in range(1, 4))
        assert db.soft_delete_todos(s, alice, [p3], T) == [p3]
        assert todo_snapshot(conn, p3).position == 3                                   # 삭제해도 position 유지
        assert db.next_todo_position(s, alice) == 4                                    # 대기 행 번호는 다시 쓰지 않는다
        p4 = db.insert_todo(s, alice, {"title": "P4"})
        assert db.restore_todos(s, alice, [p3], T + timedelta(seconds=1)) == [p3]
        rows = db.active_todos(s, alice)
        assert [t.id for t in rows] == [p1, p2, p3, p4] and [t.position for t in rows] == [1, 2, 3, 4]

        assert db.soft_delete_todos(s, alice, [p4], T + timedelta(seconds=2)) == [p4]
        assert db.next_todo_position(s, alice) == 5                                    # 대기 중에는 4 를 피한다
        assert db.purge_expired_deleted_todos(s, alice, T + timedelta(seconds=2) + WINDOW) == 1
        assert db.next_todo_position(s, alice) == 4                                    # 완전 삭제 뒤 재사용 허용
        p5 = db.insert_todo(s, alice, {"title": "P5"})
        rows = db.active_todos(s, alice)
        assert [t.id for t in rows] == [p1, p2, p3, p5] and [t.position for t in rows] == [1, 2, 3, 4]
        assert p5 > p4                                                                 # id 는 시퀀스라 재사용하지 않는다


# ---------- (Phase 3-1B-3 우선순위 2) 사용자 행 잠금 (실제 PostgreSQL) ----------
# 스레드 없음. Session·Connection 은 만든 쪽에서만 쓴다. 잠금 대기 상한은 테스트 연결의 SET LOCAL lock_timeout 으로만
# 둔다(앱의 L1/L2 정책·서버 설정과 무관). 두 번째 연결은 NullPool 이고 finally 에서 dispose 한 뒤 연결 반환을 확인한다.
# 사용자 생성처럼 다른 연결에서 보여야 하는 준비 데이터만 commit 한다 — 다음 fresh_schema 가 정리한다.

LOCK_WAIT_TEST = "1s"                             # 테스트 연결 전용 잠금 대기 상한 (lock_timeout)


def sqlstate_of(exc: BaseException) -> str | None:
    value = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return value if isinstance(value, str) and len(value) == 5 and value.isalnum() else None


def committed_users(engine, *names: str) -> list[int]:
    with engine.begin() as conn:                                             # 다른 연결에서 보이도록 commit
        return [make_user(conn, name) for name in names]


def test_lock_record_does_not_survive_transaction_end_in_database(fresh_schema):
    # Session 트랜잭션(여기서는 SAVEPOINT)이 끝나면 그 잠금 기록으로는 새 트랜잭션에서 쓸 수 없다.
    # SAVEPOINT 해제는 최상위 트랜잭션 종료·행 잠금 해제가 아니다 — 여기서 보는 것은 헬퍼의 트랜잭션 동일성 규약이다
    with rolled_back(fresh_schema) as conn:
        alice = make_user(conn, "alice")
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            def write_attempts() -> None:
                for call in (lambda: db.insert_todo(s, alice, {"title": "x"}),
                             lambda: db.update_owned_todo(s, alice, first, {"title": "x"}),
                             lambda: db.soft_delete_todos(s, alice, [first], T),
                             lambda: db.insert_template(s, alice, {"name": "n", "title": "t"})):
                    with pytest.raises(RuntimeError):
                        call()

            s.begin()
            tx1 = s.get_transaction()
            assert db.lock_user(s, alice)
            first = db.insert_todo(s, alice, {"title": "첫째"})
            s.commit()                                                       # 트랜잭션 1 끝 (commit)

            s.begin()
            assert s.get_transaction() is not tx1                            # 새 트랜잭션 객체
            before = count(conn, db.Todo)
            write_attempts()                                                 # 이전 기록으로는 안 된다
            assert count(conn, db.Todo) == before
            s.rollback()                                                     # 트랜잭션 2 끝 (rollback)

            s.begin()
            write_attempts()                                                 # rollback 뒤에도 마찬가지
            assert count(conn, db.Todo) == before
            assert db.lock_user(s, alice)                                    # 대조: 다시 잠그면 쓸 수 있다
            second = db.insert_todo(s, alice, {"title": "둘째"})
            assert count(conn, db.Todo) == before + 1 and second != first
            s.rollback()
        finally:
            s.close()


def end_session(trans, session) -> None:
    # 연결 트랜잭션을 먼저 되돌리고(실패한 트랜잭션에서 보낼 수 있는 것은 ROLLBACK 뿐) 그다음 Session 을 닫는다.
    # 되돌리다 오류가 나도 Session 은 닫는다
    try:
        if trans.is_active:
            trans.rollback()
    finally:
        session.close()


def test_lock_wait_timeout_rolls_back_and_releases(fresh_schema):
    # A 가 alice 를 잠근 동안 B 의 lock_user(alice) 는 테스트 상한(1초) 뒤 55P03 으로 실패하고, 아무것도 쓰지 않으며,
    # 잠금 권한도 얻지 못한다. 되돌린 뒤 B 연결은 다시 쓸 수 있고, A 연결이 잡았던 잠금은 A 를 되돌리면 모두 풀린다.
    # (DB 전체의 잠금·연결은 여기서 보지 않는다 — 그것은 R-19 게이트·wait_for_backends_released 의 몫이다)
    (alice,) = committed_users(fresh_schema, "alice")
    other = nullpool_engine()
    try:
        with fresh_schema.connect() as conn_a, other.connect() as conn_b:
            db.verify_test_server(conn_b)
            conn_b.rollback()
            trans_a = conn_a.begin()
            s_a = orm(conn_a)
            try:
                assert db.lock_user(s_a, alice)
                a_pid = conn_a.execute(text("SELECT pg_backend_pid()")).scalar_one()

                trans_b = conn_b.begin()
                s_b = orm(conn_b)
                try:
                    conn_b.execute(text(f"SET LOCAL lock_timeout = '{LOCK_WAIT_TEST}'"))
                    with pytest.raises(SQLAlchemyError) as e:
                        db.lock_user(s_b, alice)
                    state = sqlstate_of(e.value)                             # SQLSTATE 만 꺼낸다
                    del e
                    granted = db._LOCKED_USER_KEY in s_b.info                # 실패한 잠금은 권한을 남기지 않는다
                    # 잠금 기록이 없으면 insert_todo 는 첫 줄(_require_user_lock)에서 SQL 없이 거부한다 —
                    # 실패한 트랜잭션에 SQL 을 보내지 않는다
                    with pytest.raises(RuntimeError):
                        db.insert_todo(s_b, alice, {"title": "x"})
                finally:
                    end_session(trans_b, s_b)                                # ROLLBACK → Session 닫기
                assert state == "55P03" and not granted

                assert conn_b.execute(text("SELECT 1")).scalar_one() == 1    # 되돌린 뒤 연결은 다시 쓸 수 있다
                assert conn_b.execute(select(func.count()).select_from(db.Todo)
                                      .where(db.Todo.user_id == alice)).scalar_one() == 0   # 쓰기 없음
                conn_b.rollback()
            finally:
                end_session(trans_a, s_a)                                    # A 잠금 해제
            a_locks = conn_b.execute(text("SELECT count(*) FROM pg_locks WHERE pid = :pid"),
                                     {"pid": a_pid}).scalar_one()           # 이 테스트가 만든 A 연결만 본다
            conn_b.rollback()
            assert a_locks == 0
    finally:
        other.dispose()
        wait_for_backends_released(fresh_schema)


def test_lock_user_is_independent_per_user(fresh_schema):
    # A 의 트랜잭션이 alice 를 잠근 채 열려 있는 동안, B 의 bob 잠금·쓰기가 테스트 상한(1초) 안에 끝난다 —
    # 같은 잠금을 기다렸다면 55P03 으로 실패했을 것이다. 핵심은 B 의 성공과 사용자별 격리다
    alice, bob = committed_users(fresh_schema, "alice", "bob")
    with fresh_schema.begin() as conn:                                       # alice 의 할 일 1건 (B 에서 보여야 한다)
        s = orm(conn)
        try:
            assert db.lock_user(s, alice)
            a_todo = db.insert_todo(s, alice, {"title": "A"})
        finally:
            s.close()
    other = nullpool_engine()
    try:
        with fresh_schema.connect() as conn_a, other.connect() as conn_b:
            db.verify_test_server(conn_b)
            conn_b.rollback()
            trans_a = conn_a.begin()
            s_a = orm(conn_a)
            try:
                assert db.lock_user(s_a, alice)                              # A 가 alice 잠금을 얻었다

                trans_b = conn_b.begin()
                s_b = orm(conn_b)
                try:
                    conn_b.execute(text(f"SET LOCAL lock_timeout = '{LOCK_WAIT_TEST}'"))
                    b_locked = db.lock_user(s_b, bob)
                    b_todo = db.insert_todo(s_b, bob, {"title": "B"})
                    rows = [(t.id, t.position) for t in db.active_todos(s_b, bob)]
                    hidden = db.get_owned_todo(s_b, bob, a_todo) is None     # 격리
                    visible_to_b = set(db.owned_todos_by_ids(s_b, bob, [a_todo, b_todo]))
                    a_open_during_b = trans_a.is_active and conn_a.in_transaction()   # B 가 끝날 때 A 는 아직 열림
                finally:
                    end_session(trans_b, s_b)                                # bob 의 쓰기는 남기지 않는다
                assert b_locked and rows == [(b_todo, 1)]
                assert hidden and visible_to_b == {b_todo}
                assert a_open_during_b

                assert db.insert_todo(s_a, alice, {"title": "A2"}) != a_todo # A 의 잠금 기록도 그대로 유효
            finally:
                end_session(trans_a, s_a)
    finally:
        other.dispose()
        wait_for_backends_released(fresh_schema)


A_WORKER_LOCK_WAIT = "15s"                        # 작업 스레드 연결 전용 lock_timeout — 메인이 풀지 못해도 스레드가 끝난다
A_OBSERVE_TIMEOUT = 5.0                           # 초 — 작업 스레드가 실제 잠금 대기에 들어간 것을 확인하는 상한


def observe_lock_wait(conn, pid: int, timeout: float) -> bool:
    # pid 로 범위를 한정해 읽기만 한다: pg_stat_activity 의 wait_event_type = 'Lock' 이고 pg_locks 에 미획득 잠금이 있는가.
    # 메인 트랜잭션 안에서 반복하므로 매번 통계 스냅숏을 비운다(같은 트랜잭션에서는 pg_stat_activity 가 고정된다)
    deadline = time.monotonic() + timeout
    while True:
        conn.execute(text("SELECT pg_stat_clear_snapshot()"))
        waiting = conn.execute(text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                               {"pid": pid}).scalar_one_or_none()
        ungranted = conn.execute(text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND NOT granted"),
                                 {"pid": pid}).scalar_one()
        if waiting is True and ungranted > 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)                                                     # 다음 관찰까지의 간격 (추정이 아니다)


def test_lock_user_serializes_same_user_writes(fresh_schema):
    # 메인(A)이 alice 를 잠근 채 할 일을 넣는 동안, 작업 스레드(B)의 lock_user(alice) 는 실제로 기다린다(관찰).
    # A 가 commit 해 잠금을 풀면 B 가 진행해 다음 번호를 받는다 → alice 의 position 은 [1, 2], 중복 없음.
    # 연결·Session 은 만든 스레드만 쓴다. 정리 순서: A 해제 → 스레드 join → 끝났을 때만 dispose → 연결 반환 확인.
    # 스레드가 끝나지 않으면 dispose 하지 않고 _UNFINISHED_WORKERS 에 넣는다(다음 연결 확인이 pytest.exit(3) 로 멈춘다)
    (alice,) = committed_users(fresh_schema, "alice")
    other = nullpool_engine()
    handoff: dict[str, object] = {}                                          # 작업 스레드 → 메인: pid·결과 (값만)
    pid_ready = threading.Event()

    def worker() -> None:
        try:
            with other.connect() as conn_b:                                  # 이 스레드가 만들고 이 스레드만 쓴다
                db.verify_test_server(conn_b)
                conn_b.rollback()
                trans_b = conn_b.begin()
                s_b = orm(conn_b)
                try:
                    conn_b.execute(text(f"SET LOCAL lock_timeout = '{A_WORKER_LOCK_WAIT}'"))
                    handoff["pid"] = conn_b.execute(text("SELECT pg_backend_pid()")).scalar_one()
                    pid_ready.set()
                    handoff["locked"] = db.lock_user(s_b, alice)             # A 가 풀 때까지 기다린다
                    handoff["todo"] = db.insert_todo(s_b, alice, {"title": "B"})
                    trans_b.commit()
                finally:
                    end_session(trans_b, s_b)
        except BaseException as exc:                                         # noqa: BLE001 — 종류·SQLSTATE 만 넘긴다
            handoff["error"] = (type(exc).__name__, sqlstate_of(exc))
        finally:
            pid_ready.set()                                                  # pid 전에 실패해도 메인이 기다리지 않게

    thread = threading.Thread(target=worker, daemon=False)
    observed = False
    a_todo = None
    try:
        with fresh_schema.connect() as conn_a:                               # 메인 스레드가 소유
            trans_a = conn_a.begin()
            s_a = orm(conn_a)
            try:
                assert db.lock_user(s_a, alice)
                a_todo = db.insert_todo(s_a, alice, {"title": "A"})
                thread.start()
                pid_ready.wait(CONNECT_TIMEOUT + A_OBSERVE_TIMEOUT)
                b_pid = handoff.get("pid")
                if isinstance(b_pid, int):                                   # pid 를 받은 것만으로 대기라고 보지 않는다
                    observed = observe_lock_wait(conn_a, b_pid, A_OBSERVE_TIMEOUT)
                trans_a.commit()                                             # 정상: A 의 할 일을 남기고 잠금 해제
            finally:
                end_session(trans_a, s_a)                                    # 실패 경로: rollback 으로 잠금 해제
    finally:
        if thread.ident is not None:
            thread.join(JOIN_TIMEOUT)                                        # A 를 푼 뒤에 기다린다
        if thread.is_alive():
            _UNFINISHED_WORKERS.append(thread)                               # dispose·연결 닫기를 하지 않는다
            pytest.fail(f"작업 스레드가 {JOIN_TIMEOUT}초 안에 끝나지 않음")
        other.dispose()                                                      # 스레드가 끝난 뒤에만
        wait_for_backends_released(fresh_schema)

    assert observed, "작업 스레드의 잠금 대기를 관찰하지 못함"
    assert "error" not in handoff, handoff.get("error")                      # (예외 종류, SQLSTATE) 만
    assert handoff.get("locked") is True and isinstance(handoff.get("todo"), int)
    with fresh_schema.connect() as conn:
        rows = conn.execute(select(db.Todo.id, db.Todo.position).where(db.Todo.user_id == alice)
                            .order_by(db.Todo.position)).all()
        conn.rollback()
    assert [r.position for r in rows] == [1, 2]                              # 직렬화 — 같은 max 를 읽지 않았다
    assert [r.id for r in rows] == [a_todo, handoff["todo"]]


# ---------- (Phase 3-1B-3 우선순위 3) R-27 실제 DB 오류 · R-16 lower 대 casefold 관찰 ----------

R27_MARKERS = ("DETAIL", "Key (", "already exists", "pk_sessions", "Traceback")   # 응답·로그에 나오면 안 되는 표식


def test_request_db_real_unique_violation_is_hidden(fresh_schema, caplog, monkeypatch):
    # 테스트 전용 앱의 경로가 실제 main.request_db(RequestDbDep) 경계 안에서 같은 합성 token_hash 의 세션 행을 두 번 넣는다
    # → 실제 PostgreSQL 23505. 고정 500, 응답·로그 비노출, 요청 트랜잭션 전체 롤백, 다음 요청 정상을 본다.
    # 합성 값만 쓴다(실제 토큰·비밀번호·기존 세션 없음). commit 하지 않는다. hide_parameters 가 DETAIL 까지 숨긴다고 보지 않는다
    from fastapi import FastAPI

    monkeypatch.setattr(logging.getLogger("todo.auth"), "disabled", False)   # Alembic fileConfig 가 끈 로거를 켠다
    caplog.set_level(logging.DEBUG)
    token = auth.new_session_token()                                         # 합성 — 어디에도 쓰지 않는 값
    token_hash = auth.hash_token(token)
    synthetic = (token, token_hash.hex(), token_hash.hex().upper())

    app = FastAPI()
    app.dependency_overrides[main.auth_session_factory] = lambda: sessionmaker(bind=fresh_schema, expire_on_commit=False)

    @app.post("/r27/duplicate-session")
    def duplicate_session(rdb: main.RequestDbDep) -> dict:
        s = rdb.session()
        uid = s.execute(insert(db.User).values(username="r27user", password_hash="x", created_at=T)
                        .returning(db.User.id)).scalar_one()
        for _ in range(2):                                                   # 두 번째에서 pk_sessions 위반
            s.execute(insert(db.UserSession).values(
                token_hash=token_hash, user_id=uid, csrf_token=auth.new_csrf_token(),
                created_at=T, expires_at=T + timedelta(days=14)))
        return {"ok": True}

    @app.get("/r27/ping")
    def ping(rdb: main.RequestDbDep) -> dict:
        return {"one": rdb.session().execute(text("SELECT 1")).scalar_one()}

    client = TestClient(app, raise_server_exceptions=False)   # 예상 밖 예외도 테스트 쪽으로 다시 던지지 않는다
    res = client.post("/r27/duplicate-session")

    # 값은 bool 로만 만든 뒤 단언한다 — 실패 메시지에 합성 토큰·해시·DETAIL 이 실리지 않게
    is_json = res.headers.get("content-type", "").startswith("application/json")
    fixed_500 = res.status_code == 500 and is_json and res.json() == {"detail": main.SERVER_ERROR}
    surface = res.text + "\n" + "\n".join(f"{k}: {v}" for k, v in res.headers.items())
    leaked_in_response = any(x in surface for x in synthetic + R27_MARKERS)
    log_text = caplog.text
    leaked_in_logs = any(x in log_text for x in synthetic + R27_MARKERS)
    no_exc_info = all(r.exc_info is None and r.exc_text is None for r in caplog.records)
    app_args_safe = all(isinstance(a, (str, type(None)))
                        for r in caplog.records if r.name == "todo.auth" for a in (r.args or ()))
    event_logged = "request_db_error exc_type=IntegrityError sqlstate=23505" in log_text
    del res, surface, log_text
    assert fixed_500
    assert not leaked_in_response
    assert not leaked_in_logs and no_exc_info and app_args_safe
    assert event_logged

    with fresh_schema.connect() as conn:                                     # 실패한 요청은 전부 되돌려졌다
        rolled_back_all = count(conn, db.User) == 0 and session_count(conn) == 0
        conn.rollback()
    assert rolled_back_all

    ping = client.get("/r27/ping")                                           # 오류 뒤 정상 읽기 요청
    assert ping.status_code == 200 and ping.json() == {"one": 1}
    assert fresh_schema.pool.checkedout() == 0                               # Session·연결 반환
    wait_for_backends_released(fresh_schema)


R16_PAIRS = [                                                               # (사례 id, a, b) — 같은 사용자 템플릿 이름
    ("ascii", "Weekly", "WEEKLY"),
    ("sharp-s", "straße", "STRASSE"),
    ("capital-sharp-s", "ß", "ẞ"),
    ("dotted-I", "İ", "i̇"),
    ("dotless-i", "I", "ı"),
    ("fullwidth", "Ａ", "ａ"),
    ("kelvin", "K", "k"),
    ("greek-sigma", "Σ", "ς"),
]


def insert_rejected(conn, statement) -> bool:
    # SAVEPOINT 안에서 실행하고 항상 되돌린다 — UNIQUE 위반이면 True. 바깥 트랜잭션은 계속 쓸 수 있다
    nested = conn.begin_nested()
    try:
        conn.execute(statement)
    except IntegrityError:
        nested.rollback()
        return True
    nested.rollback()
    return False


@pytest.mark.parametrize("case, a, b", R16_PAIRS, ids=[p[0] for p in R16_PAIRS])
def test_template_name_lower_vs_casefold_observation(fresh_schema, case, a, b):
    # 단언: DB 안의 일관성(lower 비교 = 고유 인덱스 거부 = 헬퍼 결과), 사용자별 독립, ASCII 기준 사례.
    # 관찰만(출력): 파이썬 casefold 와 DB lower 의 차이, DB 로캘. 비교 정책은 정하지 않는다(로캘에 따라 다를 수 있다)
    with rolled_back(fresh_schema) as conn:
        alice, bob = make_user(conn, "alice"), make_user(conn, "bob")
        s = orm(conn)
        python_eq = a.casefold() == b.casefold()
        db_lower_eq = conn.execute(text("SELECT lower(:a) = lower(:b)"), {"a": a, "b": b}).scalar_one()
        assert db.lock_user(s, alice)
        db.insert_template(s, alice, {"name": a, "title": "t"})
        helper_taken = db.template_name_taken(s, alice, b)
        index_rejects = insert_rejected(conn, insert(db.TaskTemplate).values(
            user_id=alice, position=99, name=b, title="t"))
        other_user_rejected = insert_rejected(conn, insert(db.TaskTemplate).values(
            user_id=bob, position=1, name=b, title="t"))
        ctype, collate = conn.execute(text(
            "SELECT datctype, datcollate FROM pg_database WHERE datname = current_database()")).one()
        print(f"R-16 observation case={case} python_casefold_eq={python_eq} db_lower_eq={db_lower_eq} "
              f"differs={python_eq != db_lower_eq} datctype={ctype} datcollate={collate}")
        assert db_lower_eq == index_rejects == helper_taken                 # DB 안의 세 기준이 같다
        assert not other_user_rejected                                       # 다른 사용자는 같은 이름 허용
        if case == "ascii":
            assert python_eq and db_lower_eq                                 # 기존 계약 (대소문자 무시)


# ---------- Phase 2-F: 다중 프로세스·다중 워커 (실제 uvicorn + todo_test) ----------
# A: 독립 uvicorn 프로세스 2개(각 --workers 1, 포트 다름) — 요청을 보낼 프로세스를 정할 수 있어 결과가 결정적이다.
# B: 실제 uvicorn --workers 2 — 요청 분산은 OS 가 정하므로 접근 로그의 PID 로 "관찰"한다. 분산을 확인하지 못하면
#    기능 결함과 구별해 "[검증 미완료]" 로 실패시킨다(통과로 보지 않는다).
# - 서버는 fresh_schema(R-19 게이트 → 초기화) 다음에 띄우고, 다음 테스트의 게이트 전에 반드시 내린다:
#   CTRL_BREAK(정상 종료) → 감독은 Popen 핸들로 종료 → 식별을 확인한 자식만 PID 별 강제 종료(taskkill /T 미사용) →
#   감독·워커 PID 와 포트가 모두 사라졌는지 → DB 연결 정리 확인. 식별이 불확실한 프로세스는 죽이지 않는다.
#   하나라도 확인하지 못하면 pytest.exit 로 세션을 멈춘다(다음 스키마 삭제를 막는다).
# - 자식 프로세스에는 검증된 todo_test URL 을 DATABASE_URL 로만 준다(TEST_DATABASE_URL·PG* 는 뺀다). 앱 코드는 바꾸지 않고
#   워커 식별은 uvicorn 로그(Started server process [PID])와 접근 로그 형식의 %(process)d 로만 한다.

HTTP_TIMEOUT = 30                                                # 초 — 요청 하나의 상한
MW_WRONG_PW = "mw-wrong-password-1"
MW_PW = "mw-correct-pass-9"


@contextmanager
def running_servers(engine, tmp_path: Path, workers: list[int]):
    # 서버들을 띄우고, 무슨 일이 있어도 내린 뒤 DB 연결 정리까지 확인한다. 로그는 tmp_path 에 남는다
    servers: list[Server] = []
    try:
        for i, count in enumerate(workers):
            servers.append(start_server(tmp_path / f"server{i}", count))
        yield servers
    finally:
        problems = []
        for s in servers:
            try:
                problems += stop_server(s)
            except BaseException as cleanup_error:                       # noqa: BLE001 — 정리 실패로 본다
                problems.append(f"정리 중 예외 {type(cleanup_error).__name__}")
        if problems:
            pytest.exit(f"서버 프로세스를 정리하지 못했습니다 — {problems} (로그: {tmp_path})", returncode=3)
        try:
            wait_for_backends_released(engine)
        except pytest.fail.Exception as exc:
            pytest.exit(f"서버 종료 뒤 DB 연결이 정리되지 않았습니다 — {exc} (로그: {tmp_path})", returncode=3)


def http(server: Server, method: str, path: str, *, body: dict | None = None, token: str | None = None,
         csrf: str | None = None) -> httpx.Response:
    # 요청마다 새 연결(Connection: close)·쿠키·CSRF·Origin 을 명시한다
    headers = {"Origin": server.base, "Connection": "close"}
    if token is not None:
        headers["Cookie"] = f"{COOKIE}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return httpx.request(method, server.base + path, json=body, headers=headers, timeout=HTTP_TIMEOUT)


def mw_login(server: Server, username: str, password: str = MW_WRONG_PW) -> httpx.Response:
    return http(server, "POST", "/auth/login", body={"username": username, "password": password})


def in_parallel(calls: list) -> list:
    # 상한 있는 동시 실행 — 모두 같은 순간에 시작하고, 끝나지 않는 스레드가 있으면 실패
    start = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def worker(i: int) -> None:
        try:
            start.wait(timeout=BARRIER_TIMEOUT)
            results[i] = calls[i]()
        except BaseException as exc:                                  # noqa: BLE001 — 아래에서 실패로 본다
            results[i] = exc
            start.abort()

    threads = [threading.Thread(target=worker, args=(i,), daemon=False) for i in range(len(calls))]
    for t in threads:
        t.start()
    deadline = time.monotonic() + JOIN_TIMEOUT
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    assert not any(t.is_alive() for t in threads), "요청 스레드가 끝나지 않음"
    errors = [type(r).__name__ for r in results if isinstance(r, BaseException)]
    assert not errors, errors
    return results


def login_failure_rows(engine) -> list:
    with engine.connect() as conn:
        rows = [r for r in failure_rows(conn) if r.kind == "login"]
        conn.rollback()
    return rows


def assert_retry_after(res: httpx.Response) -> None:
    assert res.status_code == 429 and res.json() == {"detail": main.TOO_MANY_ATTEMPTS}
    assert 1 <= int(res.headers["retry-after"]) <= 900


def assert_clean_server_logs(servers: list[Server], *secrets: str) -> None:
    # 실제 서버 로그(stdout·stderr)에 DETAIL·행 값·traceback·비밀값이 없다 (메시지에 값 자체는 넣지 않는다)
    forbidden = {"DETAIL": "DETAIL", "Failing row": "Failing row", "Traceback": "Traceback",
                 SERVER_KEY: "AUTH_FAILURE_KEY", MW_PW: "비밀번호", MW_WRONG_PW: "비밀번호"}
    forbidden.update({s: "토큰·CSRF" for s in secrets if s})
    for server in servers:
        text = server.log_text()
        found = sorted({label for needle, label in forbidden.items() if needle in text})
        assert not found, f"서버 로그 노출: {found} (로그: {server.folder})"


def test_mp_user_limit_shared_across_processes(fresh_schema, tmp_path):
    # A1: 같은 username 의 실패를 두 프로세스에 번갈아 5번 → 6번째는 어느 프로세스든 429 (같은 IP 라 (username, IP) 규칙)
    with running_servers(fresh_schema, tmp_path, [1, 1]) as servers:
        p1, p2 = servers
        for i in range(5):
            res = mw_login(servers[i % 2], "mwuser")
            assert res.status_code == 401 and res.json() == {"detail": main.LOGIN_FAILED}
        for server in (p1, p2):
            assert_retry_after(mw_login(server, "mwuser"))
        served = [{pid for pid, m, path, s in srv.access() if path == "/auth/login"} for srv in servers]
        assert served == [set(p1.worker_pids), set(p2.worker_pids)] and p1.worker_pids != p2.worker_pids
    rows = login_failure_rows(fresh_schema)
    assert len(rows) == 5                                                      # 429 는 기록하지 않는다
    assert {(r.username_key, r.client_ip) for r in rows} == {(SERVER_FAILURE_KEY.username_key("mwuser"), "127.0.0.1")}
    assert_clean_server_logs(servers)


def test_mp_concurrent_failures_exact_across_processes(fresh_schema, tmp_path):
    # A2: 같은 username 실패 10개를 두 프로세스에 5개씩 동시에 → 정확히 401 5개·429 5개, 503 없음, 기록 5건
    with running_servers(fresh_schema, tmp_path, [1, 1]) as servers:
        results = in_parallel([lambda s=servers[i % 2]: mw_login(s, "mwrace") for i in range(10)])
        statuses = sorted(r.status_code for r in results)
        assert statuses == [401] * 5 + [429] * 5, statuses
        for res in results:
            if res.status_code == 429:
                assert_retry_after(res)
        per_server = [sum(1 for _, m, path, _ in srv.access() if path == "/auth/login") for srv in servers]
        assert per_server == [5, 5]
    rows = login_failure_rows(fresh_schema)
    assert len(rows) == 5 and {r.username_key for r in rows} == {SERVER_FAILURE_KEY.username_key("mwrace")}
    assert_clean_server_logs(servers)


def test_mp_ip_limit_shared_across_processes(fresh_schema, tmp_path):
    # A3: 서로 다른 username 21개(각 1번)를 두 프로세스에 번갈아 → 20번째까지 401, 21번째 429 (IP 규칙 20 공유)
    names = [f"mwip{i:02d}" for i in range(21)]
    with running_servers(fresh_schema, tmp_path, [1, 1]) as servers:
        for i, name in enumerate(names[:20]):
            res = mw_login(servers[i % 2], name)
            assert res.status_code == 401, (i, res.status_code)
        assert_retry_after(mw_login(servers[1], names[20]))
    rows = login_failure_rows(fresh_schema)
    assert len(rows) == 20 and {r.client_ip for r in rows} == {"127.0.0.1"}
    assert {r.username_key for r in rows} == {SERVER_FAILURE_KEY.username_key(n) for n in names[:20]}
    assert_clean_server_logs(servers)


def test_mp_session_and_csrf_shared_across_processes(fresh_schema, tmp_path):
    # A4: 한 프로세스에서 만든 세션·CSRF 를 다른 프로세스가 그대로 쓴다 (로그아웃도 공유)
    secrets_seen: list[str] = []
    with running_servers(fresh_schema, tmp_path, [1, 1]) as servers:
        p1, p2 = servers
        created = http(p1, "POST", "/auth/register", body={"username": "mwsess", "password": MW_PW})
        assert created.status_code == 201
        token, csrf = created.cookies.get(COOKIE), created.json()["csrf_token"]
        secrets_seen += [token, csrf]
        me = http(p2, "GET", "/auth/me", token=token)
        assert me.status_code == 200 and me.json() == created.json()
        assert http(p2, "POST", "/auth/logout", token=token, csrf="wrong-csrf").status_code == 403
        assert http(p1, "GET", "/auth/me", token=token).status_code == 200            # 403 은 세션을 지우지 않는다
        assert http(p2, "POST", "/auth/logout", token=token, csrf=csrf).status_code == 204
        assert http(p1, "GET", "/auth/me", token=token).status_code == 401
        login = mw_login(p2, "mwsess", MW_PW)
        assert login.status_code == 200
        token2, csrf2 = login.cookies.get(COOKIE), login.json()["csrf_token"]
        secrets_seen += [token2, csrf2]
        assert http(p1, "GET", "/auth/me", token=token2).status_code == 200
        assert http(p1, "POST", "/auth/logout", token=token2, csrf=csrf2).status_code == 204
        assert http(p2, "GET", "/auth/me", token=token2).status_code == 401
    state = db_state(fresh_schema)
    assert state["users"] == 1 and state["sessions"] == 0
    assert [k for _, k, _ in state["failures"]] == ["register"]                    # 성공 로그인의 선기록은 지워졌다
    assert_clean_server_logs(servers, *secrets_seen)


def test_uvicorn_two_workers_distribute_and_share_limit(fresh_schema, tmp_path):
    # B1: 감독 1 + 워커 2 기동. B2: GET 요청이 두 워커 모두에서 처리됨(접근 로그 PID). B3: 같은 username 실패 8개를
    # 동시에 → 정확히 401 5개·429 3개이고, 401(기록된 실패)이 두 워커에 나뉘었는지. 분산 확인은 상한 있는 반복으로만.
    unverified: list[str] = []
    with running_servers(fresh_schema, tmp_path, [2]) as (server,):
        workers = set(server.worker_pids)
        assert len(workers) == 2 and server.parent_pid not in workers                  # B1
        assert server.parent_pid == server.proc.pid
        seen: set[int] = set()
        b2_start = len(server.access())                     # 준비 확인 요청(/auth/me)은 이 앞 — B2 분산 판단에서 뺀다
        for round_no in range(5):                                                      # B2 — 최대 40 요청
            results = in_parallel([lambda: http(server, "GET", "/auth/me") for _ in range(8)])
            assert {r.status_code for r in results} == {401}
            deadline = time.monotonic() + 5
            while len(server.access()) < b2_start + 8 * (round_no + 1) and time.monotonic() < deadline:
                time.sleep(0.1)
            seen = {pid for pid, m, path, s in server.access()[b2_start:] if path == "/auth/me"}
            if seen >= workers:
                break
        if not seen >= workers:
            unverified.append(f"B2 GET 분산 미확인 (처리한 워커 {len(seen & workers)}/2)")
        distributed = False
        for attempt in range(3):                                                       # B3 — 최대 3개 username
            name = f"mwb{attempt}"
            before = len(server.access())
            results = in_parallel([lambda n=name: mw_login(server, n) for _ in range(8)])
            statuses = sorted(r.status_code for r in results)
            assert statuses == [401] * 5 + [429] * 3, statuses                          # 기능 — 분산과 무관하게 정확
            deadline = time.monotonic() + 5
            while len(server.access()) < before + 8 and time.monotonic() < deadline:
                time.sleep(0.1)
            lines = [a for a in server.access()[before:] if a[2] == "/auth/login"]
            if {pid for pid, _, _, status in lines if status == 401} >= workers:
                distributed = True
                break
        if not distributed:
            unverified.append("B3 기록된 로그인 실패가 두 워커에 나뉘지 않음 (3회)")
    rows = login_failure_rows(fresh_schema)
    assert len(rows) == 5 * (attempt + 1)                                              # username 마다 정확히 5건
    assert {r.username_key for r in rows} == {SERVER_FAILURE_KEY.username_key(f"mwb{i}") for i in range(attempt + 1)}
    assert_clean_server_logs([server])
    if unverified:
        pytest.fail("[검증 미완료] 요청 분산을 확인하지 못함 — 기능 결함이 아니라 관찰 실패: " + "; ".join(unverified))


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
