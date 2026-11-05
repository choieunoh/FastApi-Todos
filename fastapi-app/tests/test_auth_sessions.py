"""(7.0.0 Phase 2-C) 로그인 세션 저장 함수(db.py) — DB 없이 가짜 Session 으로 검증한다.

실제 PostgreSQL 동작(만료 경계·cascade·rollback·동시 연결)은 test_db_postgres.py 에 있다(실행은 별도 승인).
"""
import ast
import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

import auth
import db

APP_DIR = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
DIALECT = postgresql.dialect()


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self.row, self.rowcount = row, rowcount

    def one_or_none(self):
        return self.row


class FakeSession:
    # 실행한 문장·추가한 객체·flush 횟수를 기록한다. commit·rollback 이 불리면 기록해 테스트가 실패하게 한다
    def __init__(self, row=None, rowcount=0, fail: Exception | None = None, fail_flush: Exception | None = None):
        self.row, self.rowcount, self.fail, self.fail_flush = row, rowcount, fail, fail_flush
        self.executed, self.added, self.flushes, self.forbidden = [], [], 0, []

    def execute(self, statement, *args, **kwargs):
        if self.fail:
            raise self.fail
        self.executed.append(statement)
        return FakeResult(self.row, self.rowcount)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushes += 1
        if self.fail_flush:
            raise self.fail_flush

    def commit(self):
        self.forbidden.append("commit")

    def rollback(self):
        self.forbidden.append("rollback")

    def close(self):
        self.forbidden.append("close")


def sql(statement) -> tuple[str, dict]:
    compiled = statement.compile(dialect=DIALECT)
    return " ".join(str(compiled).split()), dict(compiled.params)


def session_row(token: str, created: datetime = T0):
    s = db.UserSession(token_hash=auth.hash_token(token), user_id=7, csrf_token=auth.new_csrf_token(),
                       created_at=created, expires_at=created + timedelta(days=14))
    u = db.User(id=7, username="alice", password_hash="x", created_at=created)
    return (s, u)


# ---------- 토큰 형식 ----------

def test_generated_tokens_are_valid():
    for _ in range(200):
        assert db.is_valid_session_token(auth.new_session_token())


def flip_last_char(token: str) -> str:
    # 같은 32바이트로 디코딩되지만 남는 비트가 다른 표기 (비정규 표기)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    i = alphabet.index(token[-1])
    return token[:-1] + alphabet[i ^ 1]


VALID = auth.new_session_token()
INVALID_TOKENS = {
    "none": None, "bytes": VALID.encode(), "int": 12345, "empty": "",
    "42-chars": VALID[:-1], "44-chars": VALID + "A", "padded": VALID + "=",
    "std-base64-plus": "+" + VALID[1:], "std-base64-slash": "/" + VALID[1:],
    "space-prefix": " " + VALID[1:], "space-suffix": VALID[:-1] + " ", "newline": VALID[:-1] + "\n",
    "non-ascii": "가" + VALID[1:], "fullwidth": "Ａ" + VALID[1:], "dot": "." + VALID[1:],
    "non-canonical-last-char": flip_last_char(VALID),
    "long": "A" * 10_000,
}


@pytest.mark.parametrize("token", list(INVALID_TOKENS.values()), ids=list(INVALID_TOKENS))
def test_invalid_tokens_rejected(token):
    assert db.is_valid_session_token(token) is False


def test_non_canonical_token_decodes_to_same_bytes_but_is_rejected():
    other = flip_last_char(VALID)
    assert base64.urlsafe_b64decode(other + "=") == base64.urlsafe_b64decode(VALID + "=")
    assert not db.is_valid_session_token(other)


# ---------- create_session ----------

def test_create_session_stores_only_hash_and_fixed_expiry(caplog):
    fake = FakeSession()
    with caplog.at_level(logging.DEBUG):
        token, csrf = db.create_session(fake, 7, T0)
    assert db.is_valid_session_token(token) and len(csrf) == 43 and token != csrf
    (row,) = fake.added
    assert row.token_hash == hashlib.sha256(token.encode()).digest() and len(row.token_hash) == 32
    assert (row.user_id, row.csrf_token, row.created_at, row.expires_at) == (7, csrf, T0, T0 + timedelta(days=14))
    stored = [getattr(row, c.key) for c in db.UserSession.__table__.columns]
    assert token not in [v for v in stored if isinstance(v, str)]               # 원문은 저장하지 않는다
    assert token.encode() not in [v for v in stored if isinstance(v, bytes)]
    assert fake.flushes == 1 and fake.executed == [] and fake.forbidden == []
    assert token not in caplog.text and csrf not in caplog.text


def test_create_session_makes_new_tokens_each_time():
    fake = FakeSession()
    pairs = {db.create_session(fake, 7, T0) for _ in range(50)}
    assert len({t for t, _ in pairs}) == 50 and len({c for _, c in pairs}) == 50


def test_create_session_replaces_only_the_presented_session():
    old = auth.new_session_token()
    fake = FakeSession()
    db.create_session(fake, 7, T0, replace_token=old)
    (statement,) = fake.executed
    text, params = sql(statement)
    assert text.startswith("DELETE FROM sessions WHERE sessions.token_hash = ")
    assert list(params.values()) == [auth.hash_token(old)]                    # 그 토큰 하나만, user_id 조건으로 넓히지 않는다
    assert "user_id" not in text and len(fake.added) == 1


def test_replace_policy_is_independent_of_users():
    # 잠정 정책: 다른 사용자(B)로 로그인하면서 A 의 세션 토큰을 내도 그 세션 하나만 지운다.
    # DELETE 는 토큰 해시 하나만 조건으로 쓰고, 어느 사용자의 다른 세션도 건드리지 않는다
    a1 = auth.new_session_token()
    fake = FakeSession()
    db.create_session(fake, 99, T0, replace_token=a1)                        # 새 사용자 id 99
    (statement,) = fake.executed
    text, params = sql(statement)
    assert params == {"token_hash_1": auth.hash_token(a1)}
    assert "user_id" not in text and " AND " not in text and " OR " not in text
    assert fake.added[0].user_id == 99


@pytest.mark.parametrize("bad", ["", "short", flip_last_char(VALID), " " + VALID[1:], 123],
                         ids=["empty", "short", "non-canonical", "space", "int"])
def test_create_session_ignores_malformed_replace_token(bad):
    fake = FakeSession()
    db.create_session(fake, 7, T0, replace_token=bad)
    assert fake.executed == [] and len(fake.added) == 1                        # 아무것도 지우지 않는다


@pytest.mark.parametrize("now", [datetime(2026, 11, 6, 9, 0), "2026-11-06T09:00:00Z"], ids=["naive", "str"])
def test_create_session_rejects_naive_now_before_any_change(now):
    fake = FakeSession()
    with pytest.raises((ValueError, TypeError)):
        db.create_session(fake, 7, now, replace_token=auth.new_session_token())
    assert fake.executed == [] and fake.added == [] and fake.flushes == 0


def test_create_session_expiry_keeps_caller_offset():
    kst = timezone(timedelta(hours=9))
    fake = FakeSession()
    db.create_session(fake, 7, T0.astimezone(kst))
    assert fake.added[0].expires_at == T0 + timedelta(days=14)               # 같은 순간


def test_create_session_propagates_flush_error():
    err = IntegrityError("INSERT INTO sessions ...", {"csrf_token": "x"}, Exception("dup"))
    fake = FakeSession(fail_flush=err)
    with pytest.raises(IntegrityError):
        db.create_session(fake, 7, T0)
    assert fake.forbidden == []                                              # rollback 은 호출자 몫


# ---------- get_active_session ----------

@pytest.mark.parametrize("token", list(INVALID_TOKENS.values()), ids=list(INVALID_TOKENS))
def test_lookup_rejects_malformed_token_without_db(token):
    fake = FakeSession(row=session_row(VALID))
    assert db.get_active_session(fake, token, T0) is None
    assert fake.executed == []


def test_lookup_query_filters_hash_and_expiry_and_joins_user():
    token = auth.new_session_token()
    fake = FakeSession(row=session_row(token))
    result = db.get_active_session(fake, token, T0 + timedelta(days=1))
    assert result is not None and result[1].username == "alice"
    (statement,) = fake.executed
    text, params = sql(statement)
    assert text.startswith("SELECT sessions.token_hash")
    assert "JOIN users ON users.id = sessions.user_id" in text
    assert "sessions.token_hash = %(token_hash_1)s" in text and "sessions.expires_at > %(expires_at_1)s" in text
    assert params == {"token_hash_1": auth.hash_token(token), "expires_at_1": T0 + timedelta(days=1)}
    assert not text.startswith(("DELETE", "UPDATE", "INSERT")) and fake.added == [] and fake.flushes == 0


def test_lookup_expiry_boundary():
    token = auth.new_session_token()
    expires = T0 + timedelta(days=14)
    assert db.get_active_session(FakeSession(row=session_row(token)), token, expires - timedelta(microseconds=1))
    # DB 조건(expires_at > now)이 빠졌더라도 파이썬에서 한 번 더 막는다 — 같으면 만료
    assert db.get_active_session(FakeSession(row=session_row(token)), token, expires) is None
    assert db.get_active_session(FakeSession(row=session_row(token)), token, expires + timedelta(seconds=1)) is None


def test_lookup_returns_none_when_no_row():
    assert db.get_active_session(FakeSession(row=None), auth.new_session_token(), T0) is None


@pytest.mark.parametrize("now", [datetime(2026, 11, 6, 9, 0), None], ids=["naive", "none"])
def test_lookup_rejects_naive_now(now):
    fake = FakeSession()
    with pytest.raises(ValueError):
        db.get_active_session(fake, auth.new_session_token(), now)
    with pytest.raises(ValueError):
        db.get_active_session(fake, "bad", now)                              # 토큰 형식보다 먼저 확인
    assert fake.executed == []


def test_lookup_propagates_db_errors():
    # DB 오류를 "로그인 안 됨(None)"이나 성공으로 바꾸지 않는다
    fake = FakeSession(fail=OperationalError("SELECT ...", {}, Exception("server closed the connection")))
    with pytest.raises(OperationalError):
        db.get_active_session(fake, auth.new_session_token(), T0)


# ---------- delete_session / purge_expired_sessions ----------

def test_delete_session_is_idempotent():
    token = auth.new_session_token()
    assert db.delete_session(FakeSession(rowcount=1), token) is True
    second = FakeSession(rowcount=0)
    assert db.delete_session(second, token) is False                         # 이미 없어도 오류 없이 False
    text, params = sql(second.executed[0])
    assert text == "DELETE FROM sessions WHERE sessions.token_hash = %(token_hash_1)s"
    assert params == {"token_hash_1": auth.hash_token(token)}


@pytest.mark.parametrize("token", list(INVALID_TOKENS.values()), ids=list(INVALID_TOKENS))
def test_delete_session_skips_db_for_malformed_token(token):
    fake = FakeSession(rowcount=5)
    assert db.delete_session(fake, token) is False
    assert fake.executed == []


def test_purge_deletes_all_users_expired_sessions():
    fake = FakeSession(rowcount=3)
    assert db.purge_expired_sessions(fake, T0) == 3
    text, params = sql(fake.executed[0])
    assert text.startswith("DELETE FROM sessions WHERE sessions.expires_at <= %(expires_at_1)s")
    assert "user_id" not in text and " AND " not in text                       # 사용자 조건 없이 전체
    assert params == {"expires_at_1": T0}


def test_purge_rejects_naive_now():
    fake = FakeSession()
    with pytest.raises(ValueError):
        db.purge_expired_sessions(fake, datetime(2026, 11, 6))
    assert fake.executed == []


# ---------- 트랜잭션 책임·경계 ----------

SESSION_FUNCS = ("is_valid_session_token", "create_session", "get_active_session", "delete_session",
                 "purge_expired_sessions")


def session_function_nodes():
    tree = ast.parse((APP_DIR / "db.py").read_text(encoding="utf-8"))
    nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in SESSION_FUNCS}
    assert set(nodes) == set(SESSION_FUNCS)
    return nodes


def test_session_functions_do_not_commit_or_open_connections():
    for name, node in session_function_nodes().items():
        calls = {ast.unparse(n.func).split(".")[-1] for n in ast.walk(node) if isinstance(n, ast.Call)}
        assert not calls & {"commit", "rollback", "begin", "close", "create_engine", "make_engine",
                            "get_engine", "get_sessionmaker", "connect", "Session"}, name


def test_session_functions_do_not_log_or_format_tokens():
    for name, node in session_function_nodes().items():
        calls = {ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
        assert not any(c.startswith(("logging.", "logger.", "log.")) or c == "print" for c in calls), name
        fstrings = [n for n in ast.walk(node) if isinstance(n, ast.JoinedStr)]
        assert fstrings == [], name                                          # 예외 메시지에 값을 끼워 넣지 않는다


def test_fake_session_never_committed_across_all_calls():
    fake = FakeSession(rowcount=1, row=None)
    token, _ = db.create_session(fake, 7, T0, replace_token=auth.new_session_token())
    db.get_active_session(fake, token, T0)
    db.delete_session(fake, token)
    db.purge_expired_sessions(fake, T0)
    assert fake.forbidden == []


# ---------- SQLAlchemy 바인드 값 숨김 ----------

def test_engine_hides_parameters():
    engine = db.make_engine("postgresql://u@nonexistent.invalid/todo_dev")   # 연결하지 않는다
    try:
        assert engine.hide_parameters is True
    finally:
        engine.dispose()


def test_statement_error_string_hides_parameters_when_flag_set():
    csrf, digest = auth.new_csrf_token(), auth.hash_token(auth.new_session_token())
    params = {"token_hash": digest, "csrf_token": csrf}
    shown = DBAPIError.instance("INSERT INTO sessions ...", params, Exception("duplicate key"), Exception,
                                hide_parameters=False)
    hidden = DBAPIError.instance("INSERT INTO sessions ...", params, Exception("duplicate key"), Exception,
                                 hide_parameters=True)
    assert csrf in str(shown)                                                # 대조군: 숨기지 않으면 보인다
    assert csrf not in str(hidden) and repr(digest) not in str(hidden)
    assert "hidden due to hide_parameters=True" in str(hidden)
