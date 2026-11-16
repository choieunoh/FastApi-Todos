"""(7.0.0 Phase 2-E) /auth/* 라우트 — DB 없이 도는 테스트.

- 실제 엔진·Session 을 만들려고 하면 실패하게 막고, db 함수와 Session 팩토리를 가짜로 바꾼다(PostgreSQL 접속 없음).
- 가짜 Session 은 열기·BEGIN·COMMIT·ROLLBACK·닫기를 순서대로 기록한다 → 트랜잭션 경계와 "commit 뒤 응답"을 확인한다.
- 클라이언트 IP 는 TestClient(client=...) 로 명시한다(기본값 'testclient' 는 IP 가 아니라 500).
"""
import ast
import inspect
import logging
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError

import auth
import db
import main

IP = "203.0.113.7"
KEY = auth.FailureKey(b"k" * 32)                 # 테스트 전용 공개값
ORIGIN = "http://testserver"                     # TestClient 기본 base_url 의 Origin
SECRET_PW = "Pw-SECRET-7f3a9c"                   # 응답·로그에 나오면 안 되는 비밀번호
DETAIL_MARK = "DETAIL-SECRET-41ab"               # 응답·로그에 나오면 안 되는 DB 오류 내용
TOKEN = "T" * 43
CSRF = "csrf-value-1"
STORED = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g"   # 합성 해시 문자열
NEW_HASH = "$argon2id$new"
REAL_VERIFY = auth.verify_login_password
REAL_HASH_LIMITED = auth.hash_password_limited   # (T-A) world 가 가짜로 바꾸기 전의 실제 Argon2id 해시 함수
DEV = auth.AuthSettings(app_env="test", cookie_secure=False, registration_enabled=True)
AUTH_ENV = ("APP_ENV", "AUTH_FAILURE_KEY", "COOKIE_SECURE", "REGISTRATION_ENABLED", "DATABASE_URL")


class FakePgError(Exception):
    # psycopg 오류처럼 sqlstate·diag 를 가진다. 메시지에는 숨겨야 할 내용을 넣는다
    def __init__(self, sqlstate: str, constraint: str | None = None) -> None:
        super().__init__(f"ERROR: boom\n{DETAIL_MARK}")
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(constraint_name=constraint)


def db_error(sqlstate: str = "XX000") -> OperationalError:
    return OperationalError("SELECT 1", None, FakePgError(sqlstate))


def lock_timeout() -> OperationalError:
    return db_error("55P03")


def integrity(constraint: str) -> IntegrityError:
    return IntegrityError("INSERT", None, FakePgError("23505", constraint))


class World:
    """가짜 DB·Argon2 상태와 호출 기록 (events: 열기·BEGIN·호출·COMMIT/ROLLBACK·닫기 순서)."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.calls: list[tuple[str, int, tuple]] = []   # (이름, 그때 열린 Session 수, 인자)
        self.open = 0
        self.errors: dict[str, BaseException] = {}
        self.login_decision = auth.LimitDecision(True)
        self.register_decision = auth.LimitDecision(True)
        self.user: tuple[int, str] | None = (7, STORED)
        self.password_ok = True
        self.finish_result = True
        self.exists = False
        self.rehash = False
        self.busy: set[str] = set()
        self.active: tuple | None = None

    def record(self, name: str, *args: object) -> None:
        self.calls.append((name, self.open, args))
        self.events.append(name)
        if name in self.errors:
            raise self.errors[name]

    def call(self, name: str) -> tuple[int, tuple]:
        (found,) = [(o, a) for n, o, a in self.calls if n == name]
        return found

    def called(self, name: str) -> bool:
        return any(n == name for n, _, _ in self.calls)


class FakeTx:
    def __init__(self, world: World) -> None:
        self.world = world

    def __enter__(self) -> "FakeTx":
        self.world.events.append("begin")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.world.events.append("rollback" if exc_type else "commit")
        return False


class FakeSession:
    def __init__(self, world: World) -> None:
        self.world = world

    def __enter__(self) -> "FakeSession":
        self.world.open += 1
        self.world.events.append("open")
        return self

    def __exit__(self, *exc: object) -> bool:
        self.world.open -= 1
        self.world.events.append("close")
        return False

    def begin(self) -> FakeTx:
        return FakeTx(self.world)


@pytest.fixture(autouse=True)
def auth_logger_enabled(monkeypatch):
    # 다른 테스트가 Alembic 을 프로세스 안에서 돌리면 fileConfig(disable_existing_loggers) 가 이 로거를 끈다 — 로그 검사를 위해 켠다
    monkeypatch.setattr(logging.getLogger("todo.auth"), "disabled", False)


def _no_db(*args: object, **kwargs: object) -> None:
    raise AssertionError("DB 없는 테스트가 실제 DB 에 접속하려고 함")


@pytest.fixture
def world(monkeypatch):
    w = World()
    for name in ("get_sessionmaker", "get_engine", "make_engine"):
        monkeypatch.setattr(db, name, _no_db)

    def begin_login_attempt(s, name, ip, now):
        w.record("begin_login_attempt", name, ip, now)
        if not w.login_decision.allowed:
            return db.LoginAttempt(w.login_decision, None, None, None)
        user = w.user if name.username is not None else None
        return db.LoginAttempt(w.login_decision, 101, user[0] if user else None, user[1] if user else None)

    def finish_login_success(s, name, attempt_id):
        w.record("finish_login_success", name, attempt_id)
        return w.finish_result

    def cancel_login_attempt(s, name, attempt_id):
        w.record("cancel_login_attempt", name, attempt_id)
        return True

    def begin_register_attempt(s, ip, now):
        w.record("begin_register_attempt", ip, now)
        return db.RegisterAttempt(w.register_decision, 201 if w.register_decision.allowed else None)

    def username_exists(s, username):
        w.record("username_exists", username)
        return w.exists

    def create_user(s, username, password_hash, now):
        w.record("create_user", username, password_hash)
        return 7

    def create_session(s, user_id, now, *, replace_token=None):
        w.record("create_session", user_id, replace_token)
        return TOKEN, CSRF

    def simple(name, result):
        def fake(s, *args, **kwargs):
            w.record(name, *args)
            return result
        return fake

    def get_active_session(s, token, now):
        w.record("get_active_session", token)
        return w.active

    def verify(stored_hash, password):
        w.record("verify", stored_hash)
        if "verify" in w.busy:
            raise auth.HashBusy()
        return w.password_ok and stored_hash is not None

    def hash_limited(password):
        w.record("hash", )
        if "hash" in w.busy:
            raise auth.HashBusy()
        return NEW_HASH

    for name, fn in {
        "begin_login_attempt": begin_login_attempt, "finish_login_success": finish_login_success,
        "cancel_login_attempt": cancel_login_attempt, "begin_register_attempt": begin_register_attempt,
        "username_exists": username_exists, "create_user": create_user, "create_session": create_session,
        "purge_expired_sessions": simple("purge_expired_sessions", 0),
        "update_password_hash_if_unchanged": simple("update_password_hash_if_unchanged", True),
        "get_active_session": get_active_session, "delete_session": simple("delete_session", True),
    }.items():
        monkeypatch.setattr(db, name, fn)
    monkeypatch.setattr(auth, "verify_login_password", verify)
    monkeypatch.setattr(auth, "hash_password_limited", hash_limited)
    monkeypatch.setattr(auth, "needs_rehash", lambda h: w.rehash)
    use_config(DEV)
    main.app.dependency_overrides[main.auth_session_factory] = lambda: (lambda: FakeSession(w))
    yield w
    main.app.dependency_overrides.clear()


def use_config(settings: auth.AuthSettings) -> None:
    main.app.dependency_overrides[main.auth_config] = lambda: main.AuthConfig(settings=settings, failure_key=KEY)


@pytest.fixture
def client():
    return TestClient(main.app, client=(IP, 50000))


def login(client, username="Alice", password=SECRET_PW, headers=None, **kwargs):
    return client.post("/auth/login", json={"username": username, "password": password},
                       headers={"Origin": ORIGIN, **(headers or {})}, **kwargs)


def register(client, username="Alice", password=SECRET_PW, headers=None):
    return client.post("/auth/register", json={"username": username, "password": password},
                       headers={"Origin": ORIGIN, **(headers or {})})


def assert_clean_logs(caplog) -> None:
    # 로그에 비밀번호·DB 오류 내용·traceback 이 없다
    assert SECRET_PW not in caplog.text and DETAIL_MARK not in caplog.text and "Traceback" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


def assert_no_cookie(res) -> None:
    assert "set-cookie" not in res.headers


# ---------- 시작 가드 (실제 lifespan) ----------

@pytest.fixture
def clean_env(monkeypatch):
    for name in AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in ("get_sessionmaker", "get_engine", "make_engine"):
        monkeypatch.setattr(db, name, _no_db)
    main.app.dependency_overrides.clear()
    yield monkeypatch
    main.app.state.auth = None


@pytest.mark.parametrize("env", [
    {},                                                                  # APP_ENV 없음 + 키 없음
    {"APP_ENV": "production", "COOKIE_SECURE": "true", "DATABASE_URL": "postgresql://u@127.0.0.1:5433/x"},
    {"APP_ENV": "production", "AUTH_FAILURE_KEY": "A" * 43, "DATABASE_URL": "postgresql://u@127.0.0.1:5433/x"},
    {"APP_ENV": "production", "AUTH_FAILURE_KEY": "A" * 43, "COOKIE_SECURE": "true"},
    {"APP_ENV": "staging"},
    {"APP_ENV": "test", "AUTH_FAILURE_KEY": "short"},
], ids=["no-app-env", "prod-no-key", "prod-no-secure", "prod-no-database-url", "bad-app-env", "bad-key"])
def test_startup_guard_refuses_bad_configuration(clean_env, env):
    for name, value in env.items():
        clean_env.setenv(name, value)
    with pytest.raises(auth.AuthConfigError), TestClient(main.app):
        pass
    assert main.app.state.auth is None


@pytest.mark.parametrize("env, fallback", [
    ({"APP_ENV": "test"}, True),
    ({"APP_ENV": "development"}, True),
    ({"APP_ENV": "production", "AUTH_FAILURE_KEY": "A" * 43, "COOKIE_SECURE": "true",
      "DATABASE_URL": "postgresql://u@127.0.0.1:5433/x"}, False),
], ids=["test", "development", "production"])
def test_startup_guard_accepts_valid_configuration(clean_env, caplog, env, fallback):
    for name, value in env.items():
        clean_env.setenv(name, value)
    caplog.set_level(logging.WARNING, logger="todo.auth")
    with TestClient(main.app, client=(IP, 50000)) as c:
        config = main.app.state.auth
        assert isinstance(config, main.AuthConfig) and config.settings.app_env == env["APP_ENV"]
        assert config.failure_key.development_fallback is fallback
        assert c.get("/auth/me").status_code == 401                       # 쿠키 없음 → DB 를 열지 않고 401
    assert main.app.state.auth is None                                   # 종료하면 지운다
    assert ("auth_failure_key_public_fallback" in caplog.text) is fallback
    assert "A" * 43 not in caplog.text


def test_routes_fail_closed_without_startup_guard(clean_env):
    res = TestClient(main.app, client=(IP, 50000)).get("/auth/me")
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}


# ---------- Origin · 422 · 403 · 429 순서 ----------

def test_origin_is_checked_before_body_validation(world, client):
    res = client.post("/auth/register", json={"username": "x"}, headers={"Origin": "http://evil.example"})
    assert res.status_code == 403 and res.json() == {"detail": main.ORIGIN_INVALID}
    assert client.post("/auth/register", json={"username": "x"}, headers={"Origin": ORIGIN}).status_code == 422
    assert world.events == []


@pytest.mark.parametrize("headers, status", [
    ({"Origin": "http://evil.example"}, 403), ({"Origin": "null"}, 403), ({"Origin": "http://testserver:8080"}, 403),
    ({"Origin": "https://testserver"}, 403), ({"Referer": "http://evil.example/page"}, 403),
    ({"Referer": "http://testserver/page"}, 200), ({}, 200),
])
def test_login_origin_rule(world, client, headers, status):
    res = client.post("/auth/login", json={"username": "alice", "password": SECRET_PW}, headers=headers)
    assert res.status_code == status


def test_register_disabled_is_checked_after_body_validation(world, client):
    use_config(auth.AuthSettings(app_env="test", cookie_secure=False, registration_enabled=False))
    assert register(client, password="short").status_code == 422
    res = register(client)
    assert res.status_code == 403 and res.json() == {"detail": main.REGISTRATION_DISABLED}
    assert world.events == []                                            # 시도로 세지 않는다


def test_register_blocked_returns_429_without_recording(world, client):
    world.register_decision = auth.LimitDecision(False, 3000)
    res = register(client)
    assert res.status_code == 429 and res.headers["retry-after"] == "3000"
    assert res.json() == {"detail": main.TOO_MANY_ATTEMPTS}
    assert world.events == ["open", "begin", "begin_register_attempt", "commit", "close"]
    assert not world.called("hash") and not world.called("username_exists")


@pytest.mark.parametrize("body", [
    {"username": "ab", "password": SECRET_PW},
    {"username": "bad name", "password": SECRET_PW},
    {"username": "alice", "password": "Sx-1"},
    {"username": "alice", "password": "Z" * 129},
    {"username": "alice", "password": 12345678},
    {"username": "alice"},
], ids=["short-username", "space-username", "short-password", "long-password", "int-password", "missing"])
def test_register_422_does_not_echo_input(world, client, body):
    res = client.post("/auth/register", json=body, headers={"Origin": ORIGIN})
    assert res.status_code == 422
    for error in res.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}
    for value in body.values():
        assert str(value) not in res.text
    assert world.events == []                                            # 422 는 시도로 세지 않는다


@pytest.mark.parametrize("body", [
    {"username": "alice"}, {"username": "alice", "password": 1234}, {"username": "alice", "password": "Q" * 1025},
    {"username": ["alice"], "password": SECRET_PW},
], ids=["missing", "int", "over-structure-limit", "list"])
def test_login_422_is_structure_only_and_does_not_echo_input(world, client, body):
    res = client.post("/auth/login", json=body, headers={"Origin": ORIGIN})
    assert res.status_code == 422
    assert all(set(e) == {"loc", "msg", "type"} for e in res.json()["detail"])
    assert SECRET_PW not in res.text and "Q" * 50 not in res.text
    assert world.events == []


def test_login_requires_json_content_type(world, client):
    res = client.post("/auth/login", content='{"username":"alice","password":"x"}',
                      headers={"Origin": ORIGIN, "Content-Type": "text/plain"})
    assert res.status_code == 422 and world.events == []


# ---------- 로그인 ----------

def test_login_success_transaction_boundaries(world, client):
    res = login(client)
    assert res.status_code == 200
    assert res.json() == {"id": 7, "username": "alice", "csrf_token": CSRF}
    assert world.events == [
        "open", "begin", "begin_login_attempt", "commit", "close",       # Tx1
        "verify",                                                        # Argon2 — 트랜잭션 밖
        "open", "begin", "finish_login_success", "purge_expired_sessions", "create_session", "commit", "close",   # Tx2
    ]
    assert world.call("verify")[0] == 0                                  # 열린 Session 없음 (연결·잠금 없음)
    name, ip, _ = world.call("begin_login_attempt")[1]
    assert ip == IP and name.username == "alice" and name.failure_key == KEY.username_key("alice")
    assert world.call("finish_login_success")[1][1] == 101               # 자기 선기록 id
    cookie = res.headers["set-cookie"]
    assert cookie.startswith(f"todo_session={TOKEN};")
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and "Path=/" in cookie
    assert "Max-Age=1209600" in cookie and "Secure" not in cookie
    assert TOKEN not in res.text and STORED not in res.text


def test_login_secure_cookie_name(world, client):
    use_config(auth.AuthSettings(app_env="production", cookie_secure=True, registration_enabled=False))
    cookie = login(client).headers["set-cookie"]
    assert cookie.startswith("__Host-todo_session=") and "Secure" in cookie and "Domain" not in cookie


def test_relogin_replaces_presented_session(world, client):
    client.cookies.set("todo_session", "OLD-TOKEN")
    assert login(client).status_code == 200
    assert world.call("create_session")[1] == (7, "OLD-TOKEN")


@pytest.fixture
def real_verify(world, monkeypatch):
    monkeypatch.setattr(auth, "verify_login_password", REAL_VERIFY)
    world.user = (7, auth.hash_password("correct-password-1"))
    return world


@pytest.mark.parametrize("username, password, user", [
    ("alice", "wrong-password-1", "exists"),
    ("nobody", "wrong-password-1", "missing"),
    ("x", "wrong-password-1", "exists"),                                 # username 형식 오류
    ("alice", "P" * 200, "exists"),                                      # 비밀번호 길이 초과(구조 상한 안)
    ("alice", "correct-password-1", "broken"),                           # 깨진 해시
], ids=["wrong-password", "unknown-user", "malformed-username", "long-password", "broken-hash"])
def test_login_failures_share_one_401(real_verify, client, username, password, user):
    if user == "missing":
        real_verify.user = None
    elif user == "broken":
        real_verify.user = (7, "$argon2id$v=19$broken")
    res = login(client, username=username, password=password)
    assert res.status_code == 401 and res.json() == {"detail": main.LOGIN_FAILED}
    assert not real_verify.called("finish_login_success")                # 선기록은 실패로 남는다
    assert_no_cookie(res)


def test_login_blocked_returns_429_with_retry_after(world, client):
    world.login_decision = auth.LimitDecision(False, 37)
    res = login(client)
    assert res.status_code == 429 and res.headers["retry-after"] == "37"
    assert not world.called("verify")
    assert_no_cookie(res)


def test_login_hash_busy_compensates_own_attempt(world, client):
    world.busy.add("verify")
    res = login(client)
    assert res.status_code == 503 and res.json() == {"detail": main.SERVICE_BUSY}
    assert world.events[-6:] == ["verify", "open", "begin", "cancel_login_attempt", "commit", "close"]
    assert world.call("cancel_login_attempt")[1][1] == 101
    assert not world.called("finish_login_success") and not world.called("create_session")
    assert_no_cookie(res)


def test_login_compensation_failure_still_503(world, client, caplog):
    world.busy.add("verify")
    world.errors["cancel_login_attempt"] = db_error()
    caplog.set_level(logging.WARNING, logger="todo.auth")
    res = login(client)
    assert res.status_code == 503
    assert "auth_compensation_failed" in caplog.text
    assert_clean_logs(caplog)


@pytest.mark.parametrize("step", ["begin_login_attempt", "finish_login_success", "create_session"])
def test_login_lock_timeout_is_503(world, client, step):
    world.errors[step] = lock_timeout()
    res = login(client)
    assert res.status_code == 503 and res.json() == {"detail": main.SERVICE_BUSY}
    assert world.events[world.events.index(step) + 1] == "rollback"
    assert_no_cookie(res)


@pytest.mark.parametrize("step", ["begin_login_attempt", "finish_login_success", "purge_expired_sessions",
                                  "create_session"])
def test_login_database_error_is_500_without_detail(world, client, caplog, step):
    world.errors[step] = db_error()
    caplog.set_level(logging.WARNING, logger="todo.auth")
    res = login(client)
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert DETAIL_MARK not in res.text and "auth_login_error" in caplog.text and "sqlstate=XX000" in caplog.text
    assert world.events[world.events.index(step) + 1] == "rollback"
    assert_clean_logs(caplog)
    assert_no_cookie(res)


def test_login_database_config_error_is_500(world, client):
    world.errors["begin_login_attempt"] = db.DatabaseConfigError("격리 수준")
    assert login(client).status_code == 500


def test_login_missing_own_attempt_is_500_without_session(world, client):
    world.finish_result = False
    res = login(client)
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert world.events[-3:] == ["finish_login_success", "rollback", "close"]
    assert not world.called("create_session")
    assert_no_cookie(res)


def test_login_rehash_is_computed_outside_and_saved_in_tx2(world, client):
    world.rehash = True
    assert login(client).status_code == 200
    assert world.call("hash")[0] == 0                                    # 트랜잭션 밖
    opened, args = world.call("update_password_hash_if_unchanged")
    assert opened == 1 and args == (7, STORED, NEW_HASH)                 # Tx2 안, 기존 값 조건
    assert world.events.index("hash") < world.events.index("finish_login_success")


def test_login_rehash_busy_is_skipped(world, client, caplog):
    world.rehash = True
    world.busy.add("hash")
    caplog.set_level(logging.WARNING, logger="todo.auth")
    assert login(client).status_code == 200
    assert not world.called("update_password_hash_if_unchanged") and "auth_rehash_skipped" in caplog.text


def test_login_rehash_update_failure_is_500(world, client):
    world.rehash = True
    world.errors["update_password_hash_if_unchanged"] = db_error()
    res = login(client)
    assert res.status_code == 500
    assert not world.called("create_session") and "rollback" in world.events
    assert_no_cookie(res)


def test_unexpected_error_is_500_not_401_and_not_raised(world, client, caplog):
    # raise_server_exceptions=True 인 TestClient — 라우트 밖으로 예외가 나가면 여기서 터진다(서버 기본 traceback 경로)
    world.errors["begin_login_attempt"] = RuntimeError(DETAIL_MARK)
    caplog.set_level(logging.DEBUG)
    res = login(client)
    assert res.status_code == 500 and DETAIL_MARK not in res.text
    assert_clean_logs(caplog)


# ---------- IP ----------

def test_non_ip_client_is_500_without_database(world):
    res = login(TestClient(main.app))                                    # client.host = 'testclient'
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert world.events == []
    assert register(TestClient(main.app)).status_code == 500 and world.events == []


def test_forwarded_headers_are_ignored(world, client):
    login(client, headers={"X-Forwarded-For": "198.51.100.9", "X-Real-IP": "198.51.100.10",
                           "Forwarded": "for=198.51.100.11"})
    assert world.call("begin_login_attempt")[1][1] == IP


def test_ipv6_client_is_grouped_by_64(world):
    login(TestClient(main.app, client=("2001:db8:1:2::abcd", 50000)))
    assert world.call("begin_login_attempt")[1][1] == "2001:db8:1:2::/64"


# ---------- 가입 ----------

def test_register_success_commits_before_response(world, client):
    res = register(client)
    assert res.status_code == 201 and res.json() == {"id": 7, "username": "alice", "csrf_token": CSRF}
    assert world.events == ["open", "begin", "begin_register_attempt", "username_exists", "hash", "create_user",
                            "purge_expired_sessions", "create_session", "commit", "close"]
    assert world.call("create_user")[1] == ("alice", NEW_HASH)
    assert world.call("begin_register_attempt")[1][0] == IP
    assert res.headers["set-cookie"].startswith(f"todo_session={TOKEN};")


def test_register_duplicate_commits_attempt_then_409(world, client):
    world.exists = True
    res = register(client)
    assert res.status_code == 409 and res.json() == {"detail": main.USERNAME_TAKEN}
    assert world.events == ["open", "begin", "begin_register_attempt", "username_exists", "commit", "close"]
    assert not world.called("hash")
    assert_no_cookie(res)


def test_register_race_unique_violation_commits_then_409(world, client):
    world.errors["create_user"] = db.UsernameTaken()
    res = register(client)
    assert res.status_code == 409
    assert world.events[-3:] == ["create_user", "commit", "close"] and not world.called("create_session")


def test_register_other_constraint_violation_is_500(world, client):
    world.errors["create_user"] = integrity("ck_users_other")
    res = register(client)
    assert res.status_code == 500 and DETAIL_MARK not in res.text
    assert world.events[-3:] == ["create_user", "rollback", "close"]


def test_register_hash_busy_rolls_back_and_503(world, client):
    world.busy.add("hash")
    res = register(client)
    assert res.status_code == 503
    assert world.events[-3:] == ["hash", "rollback", "close"] and not world.called("create_user")
    assert_no_cookie(res)


def test_register_lock_timeout_is_503(world, client):
    world.errors["create_user"] = lock_timeout()
    assert register(client).status_code == 503
    assert world.events[-2:] == ["rollback", "close"]


def test_register_unexpected_error_is_500_not_409(world, client, caplog):
    world.errors["username_exists"] = RuntimeError(DETAIL_MARK)
    caplog.set_level(logging.DEBUG)
    res = register(client)
    assert res.status_code == 500
    assert_clean_logs(caplog)


# ---------- (T-A) 가입 HTTP 수준: username 정규화·실제 Argon2id·비밀번호 공백·시계 전달 ----------

@pytest.mark.parametrize("raw", [" Alice ", "\tALICE\n", "Alice "], ids=["spaces", "tab-newline", "trailing"])
def test_register_normalizes_username_whitespace_and_case(world, client, raw):
    res = register(client, username=raw)
    assert res.status_code == 201 and res.json()["username"] == "alice"
    assert world.call("username_exists")[1] == ("alice",)              # 중복 검사도 정규화한 값으로
    assert world.call("create_user")[1][0] == "alice"                  # 저장도 정규화한 값으로
    assert res.headers["set-cookie"].startswith(f"todo_session={TOKEN};")


@pytest.mark.parametrize("raw", [" ab ", " ", " ali ce "], ids=["short-after-strip", "blank", "inner-space"])
def test_register_rejects_username_invalid_after_strip(world, client, raw):
    res = client.post("/auth/register", json={"username": raw, "password": SECRET_PW}, headers={"Origin": ORIGIN})
    assert res.status_code == 422
    for error in res.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}                    # input·ctx 없음
    if raw.strip():                                                     # 공백만인 값은 문구의 띄어쓰기와 겹쳐 비교하지 않는다
        assert raw not in res.text
    assert SECRET_PW not in res.text
    assert world.events == []                                           # 422 는 DB·시도 기록 전에 끝난다


def test_register_stores_real_argon2id_hash(world, client, monkeypatch, caplog):
    monkeypatch.setattr(auth, "hash_password_limited", REAL_HASH_LIMITED)   # 이 테스트만 실제 해시
    caplog.set_level(logging.DEBUG)
    res = register(client)
    assert res.status_code == 201
    stored = world.call("create_user")[1][1]
    # 실패해도 해시·비밀번호 원문이 assert 메시지에 나오지 않게 결과만 비교한다
    is_argon2id = stored.startswith("$argon2id$")
    verifies = auth.verify_password(stored, SECRET_PW)
    leaked = SECRET_PW in stored or stored in res.text or stored in caplog.text
    assert is_argon2id and verifies and not leaked
    assert_clean_logs(caplog)


def test_register_keeps_password_whitespace(world, client, monkeypatch):
    spaced = " spaced pass 1 "                                         # 비밀번호는 앞뒤 공백을 지우지 않는다
    monkeypatch.setattr(auth, "hash_password_limited", REAL_HASH_LIMITED)
    res = register(client, password=spaced)
    assert res.status_code == 201
    stored = world.call("create_user")[1][1]
    accepts_original = auth.verify_password(stored, spaced)
    accepts_stripped = auth.verify_password(stored, spaced.strip())
    leaked = spaced in res.text or stored in res.text
    assert accepts_original and not accepts_stripped and not leaked


def test_register_and_me_pass_app_clock_to_session_functions(world, client, monkeypatch):
    # 시계 전달만 확인한다 — 정확히 14일 경계의 HTTP 판정(401)은 PostgreSQL 테스트(P2) 몫이다
    fixed = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "now", lambda: fixed)
    seen: dict[str, datetime] = {}
    fake_create, fake_lookup = db.create_session, db.get_active_session      # world 의 가짜 함수에 위임

    def create_session(s, user_id, now, *, replace_token=None):
        seen["create_session"] = now
        return fake_create(s, user_id, now, replace_token=replace_token)

    def get_active_session(s, token, now):
        seen["get_active_session"] = now
        return fake_lookup(s, token, now)

    monkeypatch.setattr(db, "create_session", create_session)
    monkeypatch.setattr(db, "get_active_session", get_active_session)
    assert register(client).status_code == 201
    world.active = active_session()
    client.cookies.clear()
    client.cookies.set("todo_session", TOKEN)
    assert client.get("/auth/me").status_code == 200
    assert seen == {"create_session": fixed, "get_active_session": fixed}


# ---------- me · logout · CSRF ----------

CREATED = datetime(2026, 11, 1, 3, 4, 5, tzinfo=timezone.utc)      # (8.0.0) /auth/me 의 가입 시각


def active_session(user_id: int = 7, csrf: str = CSRF):
    return SimpleNamespace(csrf_token=csrf), SimpleNamespace(id=user_id, username="alice", created_at=CREATED)


def test_me_without_cookie_is_401_without_database(world, client):
    res = client.get("/auth/me")
    assert res.status_code == 401 and res.json() == {"detail": main.AUTH_REQUIRED} and world.events == []


def test_me_with_active_session(world, client):
    world.active = active_session()
    client.cookies.set("todo_session", TOKEN)
    res = client.get("/auth/me")
    assert res.status_code == 200 and res.json() == {"id": 7, "username": "alice", "csrf_token": CSRF,
                                                     "created_at": "2026-11-01T03:04:05Z"}   # (8.0.0) 가입 시각 추가
    assert world.call("get_active_session")[1] == (TOKEN,)


def test_me_with_unknown_session_is_401(world, client):
    client.cookies.set("todo_session", TOKEN)
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("error, status", [(db_error(), 500), (lock_timeout(), 503)])
def test_me_database_error(world, client, caplog, error, status):
    world.errors["get_active_session"] = error
    client.cookies.set("todo_session", TOKEN)
    caplog.set_level(logging.WARNING, logger="todo.auth")
    res = client.get("/auth/me")
    assert res.status_code == status and DETAIL_MARK not in res.text
    assert_clean_logs(caplog)


def test_logout_without_cookie_is_204_and_clears_cookie(world, client):
    res = client.post("/auth/logout")
    assert res.status_code == 204 and world.events == []
    cookie = res.headers["set-cookie"]
    assert cookie.startswith('todo_session="";') and "Max-Age=0" in cookie and "Path=/" in cookie


def test_logout_with_csrf_deletes_session(world, client):
    world.active = active_session()
    client.cookies.set("todo_session", TOKEN)
    res = client.post("/auth/logout", headers={"X-CSRF-Token": CSRF})
    assert res.status_code == 204
    assert world.events == ["open", "begin", "get_active_session", "delete_session", "commit", "close"]


@pytest.mark.parametrize("headers", [{}, {"X-CSRF-Token": "wrong"}])
def test_logout_with_bad_csrf_is_403(world, client, headers):
    world.active = active_session()
    client.cookies.set("todo_session", TOKEN)
    res = client.post("/auth/logout", headers=headers)
    assert res.status_code == 403 and res.json() == {"detail": main.CSRF_INVALID}
    assert not world.called("delete_session") and world.events[-2:] == ["rollback", "close"]
    assert_no_cookie(res)


def test_logout_with_stale_cookie_is_204(world, client):
    client.cookies.set("todo_session", TOKEN)
    assert client.post("/auth/logout").status_code == 204 and not world.called("delete_session")


def test_logout_database_error_is_500(world, client):
    world.errors["get_active_session"] = db_error()
    client.cookies.set("todo_session", TOKEN)
    assert client.post("/auth/logout").status_code == 500


@pytest.mark.parametrize("method, header, ok", [
    ("POST", CSRF, True), ("POST", "wrong", False), ("POST", None, False), ("DELETE", None, False),
    ("GET", None, True), ("HEAD", None, True),
])
def test_require_csrf(method, header, ok):
    request = SimpleNamespace(method=method, headers={} if header is None else {"x-csrf-token": header})
    user = main.CurrentUser(id=7, username="alice", csrf_token=CSRF)
    if ok:
        assert main.require_csrf(request, user) is user                 # type: ignore[arg-type]
    else:
        with pytest.raises(HTTPException) as e:
            main.require_csrf(request, user)                             # type: ignore[arg-type]
        assert e.value.status_code == 403


# ---------- db.py 헬퍼 (DB 없이) ----------

class NestedSession:
    def __init__(self, exc: BaseException | None = None, first: object = None, rowcount: int = 1) -> None:
        self.exc, self.first, self.rowcount = exc, first, rowcount
        self.events: list[str] = []
        self.statements: list[object] = []

    def begin_nested(self):
        session = self

        class Savepoint:
            def __enter__(self):
                session.events.append("savepoint")

            def __exit__(self, exc_type, exc, tb):
                session.events.append("rollback_to_savepoint" if exc_type else "release_savepoint")
                return False
        return Savepoint()

    def execute(self, statement):
        self.statements.append(statement)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(scalar_one=lambda: 42, first=lambda: self.first, rowcount=self.rowcount)


def sql(statement) -> str:
    # 컴파일한 SQL — 바인드 형 변환(::VARCHAR 등)은 비교에서 뺀다
    return re.sub(r"::(?:TIMESTAMP WITH TIME ZONE|[A-Z]+)", "", str(statement.compile(dialect=postgresql.dialect())))


NOW = main.now()


def test_create_user_inserts_inside_savepoint():
    s = NestedSession()
    assert db.create_user(s, "alice", NEW_HASH, NOW) == 42                # type: ignore[arg-type]
    assert s.events == ["savepoint", "release_savepoint"]
    assert sql(s.statements[0]).startswith("INSERT INTO users (username, password_hash, created_at)")


def test_create_user_maps_only_username_unique_violation():
    s = NestedSession(integrity("uq_users_username"))
    with pytest.raises(db.UsernameTaken):
        db.create_user(s, "alice", NEW_HASH, NOW)                        # type: ignore[arg-type]
    assert s.events == ["savepoint", "rollback_to_savepoint"]            # 바깥 트랜잭션은 그대로
    other = NestedSession(integrity("ck_users_other"))
    with pytest.raises(IntegrityError):
        db.create_user(other, "alice", NEW_HASH, NOW)                    # type: ignore[arg-type]
    with pytest.raises(ValueError):
        db.create_user(NestedSession(), "alice", NEW_HASH, NOW.replace(tzinfo=None))   # type: ignore[arg-type]


def test_username_exists_and_conditional_rehash_sql():
    s = NestedSession(first=None)
    assert db.username_exists(s, "alice") is False                       # type: ignore[arg-type]
    assert "FROM users \nWHERE users.username = %(username_1)s" in sql(s.statements[0])
    assert db.username_exists(NestedSession(first=(7,)), "alice") is True   # type: ignore[arg-type]
    u = NestedSession(rowcount=0)
    assert db.update_password_hash_if_unchanged(u, 7, STORED, NEW_HASH) is False   # type: ignore[arg-type]
    statement = sql(u.statements[0])
    assert statement.startswith("UPDATE users SET password_hash=%(password_hash)s")
    assert "WHERE users.id = %(id_1)s AND users.password_hash = %(password_hash_1)s" in statement


def test_is_lock_timeout():
    assert db.is_lock_timeout(lock_timeout()) is True
    assert db.is_lock_timeout(db_error()) is False
    assert db.is_lock_timeout(auth.HashBusy()) is False
    assert db.is_lock_timeout(RuntimeError()) is False


def test_new_db_helpers_do_not_commit():
    for fn in (db.username_exists, db.create_user, db.update_password_hash_if_unchanged, db.is_lock_timeout):
        calls = {n.func.attr for n in ast.walk(ast.parse(inspect.getsource(fn)))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not calls & {"commit", "rollback", "close"}, fn.__name__


# ══════════════════════════ (8.0.0) 데이터 API 인증·계정 관리 — DB 없이 (V8_SPEC §37~§40, §43) ══════════════════════════
# - 인증 대체 픽스처를 쓰지 않는다(E-15): 실제 require_user·require_csrf·require_account_session 을 거친다.
#   대체가 남아 있지 않은지 v8_world 가 시작할 때 확인한다.
# - 가짜 Session·db 함수(world)는 위와 같다. 계정 Tx2 에서 쓰는 lock_user·delete_user_sessions·delete_user_if_unchanged 만 더한다.
import hashlib  # noqa: E402

V8_NEW_PW = "New-SECRET-9b2d"                   # 응답·로그에 나오면 안 되는 새 비밀번호
DATA_READS = [("GET", "/todos"), ("GET", "/todos/export?format=json"), ("GET", "/stats"), ("GET", "/task-templates")]
DATA_WRITES = [
    ("POST", "/todos", {"title": "t"}), ("POST", "/todos/restore", {"ids": [1]}),
    ("POST", "/todos/bulk", {"ids": [1], "action": "complete"}), ("PUT", "/todos/1", {"title": "t"}),
    ("DELETE", "/todos/1", None), ("POST", "/todos/1/subtasks", {"title": "s"}),
    ("PUT", "/todos/1/subtasks/1", {"title": "s", "completed": True}), ("DELETE", "/todos/1/subtasks/1", None),
    ("POST", "/task-templates", {"name": "n", "title": "t"}), ("PUT", "/task-templates/1", {"name": "n", "title": "t"}),
    ("DELETE", "/task-templates/1", None),
]
DATA_ROUTES = {("GET", "/todos"), ("GET", "/todos/export"), ("GET", "/stats"), ("GET", "/task-templates"),
               ("POST", "/todos"), ("POST", "/todos/restore"), ("POST", "/todos/bulk"), ("PUT", "/todos/{todo_id}"),
               ("DELETE", "/todos/{todo_id}"), ("POST", "/todos/{todo_id}/subtasks"),
               ("PUT", "/todos/{todo_id}/subtasks/{subtask_id}"), ("DELETE", "/todos/{todo_id}/subtasks/{subtask_id}"),
               ("POST", "/task-templates"), ("PUT", "/task-templates/{template_id}"),
               ("DELETE", "/task-templates/{template_id}")}
OPEN_ROUTES = {("GET", "/"), ("GET", "/metrics"), ("POST", "/auth/register"), ("POST", "/auth/login"),
               ("POST", "/auth/logout"), ("GET", "/auth/me"), ("POST", "/auth/password"),
               ("POST", "/auth/account/delete")}
SEED_TODO = [{"id": 1, "title": "공유 할 일", "description": "", "completed": False, "subtasks": [
    {"id": 1, "title": "하위", "completed": False}]}]
SEED_TEMPLATE = [{"id": 1, "name": "공유 템플릿", "title": "t"}]


@pytest.fixture
def json_files(tmp_path, monkeypatch):
    # 공유 JSON 데이터(임시 파일)와 복구 버퍼 — 지문을 비교해 바뀌었는지 본다
    import json
    todo_file, template_file = tmp_path / "todo.json", tmp_path / "task_templates.json"
    todo_file.write_text(json.dumps(SEED_TODO, ensure_ascii=False), encoding="utf-8")
    template_file.write_text(json.dumps(SEED_TEMPLATE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "TODO_FILE", todo_file)
    monkeypatch.setattr(main, "TEMPLATE_FILE", template_file)
    monkeypatch.setattr(main, "RECENTLY_DELETED", {})

    def fingerprint() -> tuple:
        return (hashlib.sha256(todo_file.read_bytes()).hexdigest(),
                hashlib.sha256(template_file.read_bytes()).hexdigest(), dict(main.RECENTLY_DELETED))
    return fingerprint


@pytest.fixture
def v8_world(world, monkeypatch):
    # 실제 인증 의존성을 거치는지 먼저 확인한다 (E-15 — test_api.py 의 대체 픽스처가 남아 있으면 안 된다)
    assert main.require_csrf not in main.app.dependency_overrides
    assert main.require_user not in main.app.dependency_overrides
    assert main.require_account_session not in main.app.dependency_overrides
    world.lock_result = True
    world.hash_unchanged = True
    world.deleted = True
    world.active_after: tuple | None | str = "same"     # Tx2 의 세션 재확인 결과 ("same" 이면 world.active 그대로)
    lookups = {"n": 0}

    def get_active_session(s, token, now):
        world.record("get_active_session", token)
        lookups["n"] += 1
        if lookups["n"] > 1 and world.active_after != "same":
            return world.active_after
        return world.active

    def lock_user(s, user_id):
        world.record("lock_user", user_id)
        return world.lock_result

    def update_hash(s, user_id, old_hash, new_hash):
        world.record("update_password_hash_if_unchanged", user_id, old_hash, new_hash)
        return world.hash_unchanged

    def delete_user(s, user_id, password_hash):
        world.record("delete_user_if_unchanged", user_id, password_hash)
        return world.deleted

    def delete_user_sessions(s, user_id):
        world.record("delete_user_sessions", user_id)
        return 2

    monkeypatch.setattr(db, "get_active_session", get_active_session)
    monkeypatch.setattr(db, "lock_user", lock_user)
    monkeypatch.setattr(db, "update_password_hash_if_unchanged", update_hash)
    monkeypatch.setattr(db, "delete_user_if_unchanged", delete_user)
    monkeypatch.setattr(db, "delete_user_sessions", delete_user_sessions)
    world.active = active_session()
    return world


def signed_in(client) -> None:
    client.cookies.set("todo_session", TOKEN)


def change_password(client, current=SECRET_PW, new=V8_NEW_PW, csrf=CSRF, headers=None, body=None):
    hdrs = {"Origin": ORIGIN, **({"X-CSRF-Token": csrf} if csrf is not None else {}), **(headers or {})}
    return client.post("/auth/password", headers=hdrs,
                       json=body if body is not None else {"current_password": current, "new_password": new})


def delete_account(client, password=SECRET_PW, csrf=CSRF, headers=None, body=None):
    hdrs = {"Origin": ORIGIN, **({"X-CSRF-Token": csrf} if csrf is not None else {}), **(headers or {})}
    return client.post("/auth/account/delete", headers=hdrs, json=body if body is not None else {"password": password})


def tx_segments(events: list[str]) -> list[list[str]]:
    # open … close 묶음(트랜잭션 하나)마다 나눈다 — 가짜 Argon2 호출(verify·hash)은 묶음 밖에 따로 남는다
    out, cur = [], None
    for e in events:
        if e == "open":
            cur = []
        elif e == "close":
            out.append(cur)
            cur = None
        elif cur is not None:
            cur.append(e)
        else:
            out.append([e])
    return out


# ---------- 데이터 API 15개 ----------

def openapi_operations() -> dict[tuple[str, str], dict]:
    # 앱에 실제로 등록된 경로(공개 API app.openapi()) — 선언용 라우터가 아니라 앱 기준이라 include_router 가 빠지면 비게 된다
    return {(m.upper(), path): op for path, item in main.app.openapi()["paths"].items() for m, op in item.items()}


def matrix_operation(method: str, concrete: str) -> tuple[str, str]:
    # 매트릭스의 실제 요청 경로(/todos/1?…)를 OpenAPI 경로 템플릿(/todos/{todo_id})에 하나만 대응시킨다
    path = concrete.split("?", 1)[0]
    hits = [(m, p) for m, p in DATA_ROUTES
            if m == method and re.fullmatch(re.sub(r"\{[^}]+\}", r"[^/]+", p), path)
            and not (("{" in p) and (method, path) in DATA_ROUTES)]      # 고정 경로(/todos/bulk)가 우선
    assert len(hits) == 1, (method, concrete, hits)
    return hits[0]


def test_data_route_inventory_and_open_routes():
    from fastapi.routing import APIRoute
    assert {(m, r.path) for r in main.data_router.routes for m in r.methods} == DATA_ROUTES   # 선언
    others = {(m, r.path) for r in main.app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert others == OPEN_ROUTES                                         # 인증 없이 열린 경로는 이것뿐 (§40.1)
    assert not others & DATA_ROUTES
    # 앱 등록: OpenAPI 에 데이터 경로 15개 + 문서에 넣는 인증 경로 6개만 있다 (/, /metrics 는 문서 제외)
    ops = set(openapi_operations())
    assert ops & DATA_ROUTES == DATA_ROUTES
    assert ops == DATA_ROUTES | (OPEN_ROUTES - {("GET", "/"), ("GET", "/metrics")})
    # 아래 HTTP 매트릭스(익명 401·CSRF 403·세션 1개)가 15개를 빠짐없이 한 번씩 덮는다
    reads = [matrix_operation(m, p) for m, p in DATA_READS]
    writes = [matrix_operation(m, p) for m, p, _ in DATA_WRITES]
    assert sorted(reads + writes) == sorted(DATA_ROUTES) and len(reads) == 4 and len(writes) == 11
    assert {m for m, _ in reads} == {"GET"} and "GET" not in {m for m, _ in writes}


def test_data_routes_document_401_and_write_403_in_openapi():
    # 문서만 (V8_SPEC §40) — 15개 모두 401, CSRF 를 보는 쓰기 11개만 403. 원래 성공·오류 응답은 그대로 남는다
    ops = openapi_operations()
    for key in DATA_ROUTES:
        codes = set(ops[key]["responses"])
        assert "401" in codes, key
        assert ("403" in codes) == (key[0] != "GET"), key
    assert {c for c in ops[("PUT", "/todos/{todo_id}")]["responses"]} == {"200", "401", "403", "404", "422"}
    assert {c for c in ops[("GET", "/stats")]["responses"]} == {"200", "401"}


@pytest.mark.parametrize("method, path, body", [(m, p, None) for m, p in DATA_READS] + DATA_WRITES)
def test_data_routes_without_session_are_401_and_touch_nothing(world, client, json_files, method, path, body):
    before = json_files()
    res = client.request(method, path, json=body)
    assert res.status_code == 401 and res.json() == {"detail": main.AUTH_REQUIRED}
    assert world.events == []                                            # 쿠키가 없으면 DB 를 열지 않는다
    assert json_files() == before


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_export_links_without_session_are_401(world, client, json_files, fmt):
    # (8.0.0 3-1) 화면의 내보내기 링크(<a download>, api() 를 거치지 않음)도 로그인 없이는 내려받지 못한다
    res = client.get(f"/todos/export?format={fmt}")
    assert res.status_code == 401 and res.json() == {"detail": main.AUTH_REQUIRED}
    assert "content-disposition" not in res.headers and world.events == []


@pytest.mark.parametrize("method, path, body", DATA_WRITES)
@pytest.mark.parametrize("csrf", [None, "wrong"], ids=["no-csrf", "bad-csrf"])
def test_data_writes_need_csrf(world, client, json_files, method, path, body, csrf):
    world.active = active_session()
    signed_in(client)
    before = json_files()
    res = client.request(method, path, json=body, headers={} if csrf is None else {"X-CSRF-Token": csrf})
    assert res.status_code == 403 and res.json() == {"detail": main.CSRF_INVALID}
    assert json_files() == before
    assert world.events == ["open", "begin", "get_active_session", "rollback", "close"]


@pytest.mark.parametrize("method, path, body", [(m, p, None) for m, p in DATA_READS] + DATA_WRITES)
def test_data_routes_with_expired_or_unknown_session_are_401(world, client, json_files, method, path, body):
    world.active = None                                                  # 만료·로그아웃·탈퇴된 세션
    signed_in(client)
    before = json_files()
    res = client.request(method, path, json=body, headers={"X-CSRF-Token": CSRF})
    assert res.status_code == 401 and json_files() == before


@pytest.mark.parametrize("method, path", DATA_READS)
def test_data_reads_need_session_but_not_csrf(world, client, json_files, method, path):
    world.active = active_session()
    signed_in(client)
    res = client.request(method, path)
    assert res.status_code == 200
    assert world.events == ["open", "begin", "get_active_session", "commit", "close"]   # 응답 전에 commit


@pytest.mark.parametrize("method, path, body", DATA_WRITES)
def test_data_writes_with_csrf_pass_auth_with_one_request_session(world, client, json_files, method, path, body):
    # 쓰기 11개 — 세션·CSRF 가 맞으면 인증을 통과해 핸들러에 닿고(401·403 아님), 요청 Session 은 1개만 열린다.
    # (app.routes 순회로는 include_router 로 붙은 의존성의 scope 를 볼 수 없어서, 실제 요청으로 확인한다)
    world.active = active_session()
    signed_in(client)
    res = client.request(method, path, json=body, headers={"X-CSRF-Token": CSRF})
    assert res.status_code not in (401, 403, 500), res.text              # 핸들러 결과(2xx·404 등)는 경로마다 다르다
    assert world.events.count("open") == 1 and world.events.count("close") == 1
    assert world.events[:3] == ["open", "begin", "get_active_session"]
    assert world.events[3:] == (["commit", "close"] if res.status_code < 400 else ["rollback", "close"])


def test_data_write_with_csrf_uses_shared_json(world, client, json_files):
    import json
    world.active = active_session()
    signed_in(client)
    res = client.post("/todos", json={"title": "새 할 일"}, headers={"X-CSRF-Token": CSRF})
    assert res.status_code == 201 and res.json()["id"] == 2
    titles = [t["title"] for t in json.loads(main.TODO_FILE.read_text(encoding="utf-8"))]
    assert titles == ["공유 할 일", "새 할 일"]                            # 같은 공유 JSON 에 저장 (E-2)
    assert world.events == ["open", "begin", "get_active_session", "commit", "close"]
    world.active = active_session(user_id=8, csrf="other-user-csrf")      # 다른 사용자도 같은 데이터를 본다
    res = client.get("/todos")
    assert [t["title"] for t in res.json()] == ["공유 할 일", "새 할 일"]


def test_data_route_session_lookup_error_is_fixed_500(world, client, json_files, caplog):
    world.errors["get_active_session"] = db_error()
    signed_in(client)
    caplog.set_level(logging.WARNING, logger="todo.auth")
    res = client.get("/todos")
    assert res.status_code == 500 and DETAIL_MARK not in res.text
    assert_clean_logs(caplog)


# ---------- GET /auth/me ----------

def test_me_includes_created_at(v8_world, client):
    signed_in(client)
    res = client.get("/auth/me")
    assert res.status_code == 200 and res.json()["created_at"] == "2026-11-01T03:04:05Z"
    assert set(res.json()) == {"id", "username", "csrf_token", "created_at"}


# ---------- POST /auth/password ----------

def test_password_change_success_rotates_all_sessions(v8_world, client, json_files, caplog):
    caplog.set_level(logging.INFO, logger="todo.auth")
    signed_in(client)
    before = json_files()
    res = change_password(client)
    assert res.status_code == 200 and res.json() == {"id": 7, "username": "alice", "csrf_token": CSRF}
    cookie = res.headers["set-cookie"]
    assert cookie.startswith(f"todo_session={TOKEN};") and "HttpOnly" in cookie
    assert tx_segments(v8_world.events) == [
        ["begin", "get_active_session", "commit"],                          # 의존성 — 짧은 트랜잭션, 바로 닫음
        ["begin", "begin_login_attempt", "commit"],                         # Tx1 — 시도 제한·선기록
        ["verify"], ["hash"],                                               # Argon2 — 열린 Session 없음
        ["begin", "finish_login_success", "lock_user", "get_active_session", "update_password_hash_if_unchanged",
         "delete_user_sessions", "purge_expired_sessions", "create_session", "commit"],   # Tx2
    ]
    assert v8_world.call("verify")[0] == 0 and v8_world.call("hash")[0] == 0
    assert v8_world.call("update_password_hash_if_unchanged")[1] == (7, STORED, NEW_HASH)
    assert v8_world.call("delete_user_sessions")[1] == (7,)              # 요청한 세션 포함 그 사용자 세션 전부
    assert v8_world.call("create_session")[1] == (7, None)               # 새 세션 — replace_token 없음
    assert v8_world.call("begin_login_attempt")[1][0].username == "alice"   # 로그인과 같은 시도 제한 (E-14)
    assert json_files() == before                                        # 공유 데이터는 그대로
    for secret in (SECRET_PW, V8_NEW_PW, NEW_HASH, STORED):
        assert secret not in res.text and secret not in caplog.text


def test_password_change_check_order(v8_world, client):
    # Origin(403) → 세션(401) → CSRF(403) → 본문(422) — 앞 단계에서 끝나면 시도 제한·Argon2 를 부르지 않는다
    signed_in(client)
    res = change_password(client, headers={"Origin": "http://evil.example"}, body={})
    assert res.status_code == 403 and res.json() == {"detail": main.ORIGIN_INVALID} and v8_world.events == []
    client.cookies.clear()
    res = change_password(client, body={})
    assert res.status_code == 401 and res.json() == {"detail": main.AUTH_REQUIRED} and v8_world.events == []
    signed_in(client)
    res = change_password(client, csrf="wrong", body={})
    assert res.status_code == 403 and res.json() == {"detail": main.CSRF_INVALID}
    assert change_password(client, csrf=None).status_code == 403
    assert change_password(client, body={}).status_code == 422
    assert not v8_world.called("begin_login_attempt") and not v8_world.called("verify")


@pytest.mark.parametrize("body", [
    {"current_password": SECRET_PW},                                          # 필드 누락
    {"current_password": SECRET_PW, "new_password": "short"},                 # 새 비밀번호 8자 미만
    {"current_password": SECRET_PW, "new_password": "x" * 129},               # 128자 초과
    {"current_password": SECRET_PW, "new_password": SECRET_PW},               # 현재와 같음
    {"current_password": "c" * 1025, "new_password": V8_NEW_PW},              # 구조 상한 초과
    {"current_password": 1, "new_password": V8_NEW_PW},                      # 문자열 아님
], ids=["missing", "too-short", "too-long", "same-as-current", "over-limit", "not-string"])
def test_password_change_validation_is_422_without_values(v8_world, client, body):
    signed_in(client)
    res = change_password(client, body=body)
    assert res.status_code == 422
    assert SECRET_PW not in res.text and "short" not in res.text and "x" * 129 not in res.text
    assert all(set(e) == {"loc", "msg", "type"} for e in res.json()["detail"])   # 입력값(input) 없음
    assert not v8_world.called("begin_login_attempt")
    assert_no_cookie(res)


def test_password_change_wrong_current_is_400_and_counted(v8_world, client):
    v8_world.password_ok = False
    signed_in(client)
    res = change_password(client, current="wrong-current-1")
    assert res.status_code == 400 and res.json() == {"detail": main.PASSWORD_MISMATCH}
    assert_no_cookie(res)
    assert v8_world.called("begin_login_attempt")                         # 선기록 = 로그인 실패와 같은 기록 (E-14)
    for name in ("finish_login_success", "cancel_login_attempt", "hash", "update_password_hash_if_unchanged",
                 "delete_user_sessions", "create_session"):
        assert not v8_world.called(name), name                           # 선기록은 실패로 남고 아무것도 안 바뀜


def test_password_change_rate_limited_is_429_before_argon2(v8_world, client):
    v8_world.login_decision = auth.LimitDecision(False, 600)
    signed_in(client)
    res = change_password(client)
    assert res.status_code == 429 and res.headers["retry-after"] == "600"
    assert res.json() == {"detail": main.TOO_MANY_ATTEMPTS}
    assert not v8_world.called("verify") and not v8_world.called("hash")


def test_password_change_account_replaced_is_401(v8_world, client):
    v8_world.user = (99, STORED)                                         # 같은 username 이 다른 계정이 됨(탈퇴 후 재가입)
    signed_in(client)
    res = change_password(client)
    assert res.status_code == 401 and not v8_world.called("finish_login_success")


@pytest.mark.parametrize("stage", ["verify", "hash"])
def test_password_change_hash_busy_is_503_and_compensates(v8_world, client, stage):
    v8_world.busy.add(stage)
    signed_in(client)
    res = change_password(client)
    assert res.status_code == 503 and res.json() == {"detail": main.SERVICE_BUSY}
    assert v8_world.called("cancel_login_attempt") and not v8_world.called("update_password_hash_if_unchanged")
    assert_no_cookie(res)


@pytest.mark.parametrize("setup, status, detail", [
    (lambda w: setattr(w, "active_after", None), 401, main.AUTH_REQUIRED),           # 그 사이 세션이 사라짐
    (lambda w: setattr(w, "active_after", active_session(user_id=8)), 401, main.AUTH_REQUIRED),   # 다른 사용자 세션
    (lambda w: setattr(w, "lock_result", False), 401, main.AUTH_REQUIRED),           # 그 사이 계정이 사라짐
    (lambda w: setattr(w, "hash_unchanged", False), 409, main.ACCOUNT_CONFLICT),     # 검증 뒤 해시가 바뀜
    (lambda w: setattr(w, "finish_result", False), 500, main.SERVER_ERROR),          # 자기 선기록 없음(내부 불일치)
], ids=["session-gone", "other-user", "user-gone", "hash-changed", "no-own-attempt"])
def test_password_change_tx2_rejections_roll_back(v8_world, client, setup, status, detail):
    setup(v8_world)
    signed_in(client)
    res = change_password(client)
    assert res.status_code == status and res.json() == {"detail": detail}
    assert v8_world.events[-2:] == ["rollback", "close"] and not v8_world.called("create_session")
    assert_no_cookie(res)


@pytest.mark.parametrize("failing", ["update_password_hash_if_unchanged", "delete_user_sessions",
                                     "purge_expired_sessions", "create_session", "lock_user"])
@pytest.mark.parametrize("error, status", [(db_error(), 500), (lock_timeout(), 503)], ids=["db-error", "lock-timeout"])
def test_password_change_session_rotation_failure_changes_nothing(v8_world, client, caplog, failing, error, status):
    # 세션 교체 실패 — Tx2 전체가 rollback: 해시 변경·세션 삭제·새 세션이 함께 취소되고 쿠키도 없다
    v8_world.errors[failing] = error
    signed_in(client)
    caplog.set_level(logging.WARNING, logger="todo.auth")
    res = change_password(client)
    assert res.status_code == status and DETAIL_MARK not in res.text
    assert v8_world.events[-2:] == ["rollback", "close"] and "commit" not in tx_segments(v8_world.events)[-1]
    assert_no_cookie(res)
    assert_clean_logs(caplog)
    assert V8_NEW_PW not in caplog.text


# ---------- POST /auth/account/delete ----------

def test_account_delete_success_keeps_shared_json(v8_world, client, json_files, caplog):
    caplog.set_level(logging.INFO, logger="todo.auth")
    signed_in(client)
    before = json_files()
    res = delete_account(client)
    assert res.status_code == 204
    cookie = res.headers["set-cookie"]
    assert cookie.startswith('todo_session="";') and "Max-Age=0" in cookie
    assert tx_segments(v8_world.events) == [
        ["begin", "get_active_session", "commit"], ["begin", "begin_login_attempt", "commit"], ["verify"],
        ["begin", "finish_login_success", "lock_user", "get_active_session", "delete_user_if_unchanged", "commit"],
    ]
    assert v8_world.call("delete_user_if_unchanged")[1] == (7, STORED)
    assert not v8_world.called("hash") and not v8_world.called("delete_user_sessions")   # 세션은 DB CASCADE
    assert json_files() == before                                        # 공유 JSON·복구 버퍼 보존 (E-5)
    assert SECRET_PW not in caplog.text and STORED not in caplog.text


def test_account_delete_check_order_and_validation(v8_world, client):
    signed_in(client)
    assert delete_account(client, headers={"Origin": "http://evil.example"}).status_code == 403
    assert delete_account(client, csrf=None).status_code == 403
    assert delete_account(client, body={}).status_code == 422
    assert delete_account(client, body={"password": "p" * 1025}).status_code == 422
    client.cookies.clear()
    assert delete_account(client).status_code == 401
    assert not v8_world.called("begin_login_attempt")


def test_account_delete_wrong_password_is_400(v8_world, client, json_files):
    v8_world.password_ok = False
    signed_in(client)
    before = json_files()
    res = delete_account(client, password="short")                      # 형식이 틀린 비밀번호도 불일치
    assert res.status_code == 400 and res.json() == {"detail": main.ACCOUNT_PASSWORD_MISMATCH}
    assert not v8_world.called("delete_user_if_unchanged") and json_files() == before
    assert_no_cookie(res)


def test_account_delete_rate_limited_is_429(v8_world, client):
    v8_world.login_decision = auth.LimitDecision(False, 120)
    signed_in(client)
    res = delete_account(client)
    assert res.status_code == 429 and res.headers["retry-after"] == "120" and not v8_world.called("verify")


def test_account_delete_blocked_reference_is_generic_409(v8_world, client, monkeypatch, caplog):
    def blocked(s, user_id, password_hash):
        v8_world.record("delete_user_if_unchanged")
        raise db.AccountDeleteBlocked()
    monkeypatch.setattr(db, "delete_user_if_unchanged", blocked)
    caplog.set_level(logging.WARNING, logger="todo.auth")
    signed_in(client)
    res = delete_account(client)
    assert res.status_code == 409 and res.json() == {"detail": main.ACCOUNT_DELETE_BLOCKED}
    for internal in ("data_migrations", "fk_", "23503", "owner_user_id"):   # 내부 구조를 드러내지 않는다 (E-13)
        assert internal not in res.text
    assert v8_world.events[-2:] == ["rollback", "close"]
    assert_no_cookie(res)
    assert "auth_account_delete_blocked" in caplog.text


@pytest.mark.parametrize("setup, status", [
    (lambda w: setattr(w, "deleted", False), 409), (lambda w: setattr(w, "active_after", None), 401),
    (lambda w: w.errors.__setitem__("delete_user_if_unchanged", db_error()), 500),
    (lambda w: w.errors.__setitem__("delete_user_if_unchanged", lock_timeout()), 503),
], ids=["hash-changed", "session-gone", "db-error", "lock-timeout"])
def test_account_delete_failures_roll_back(v8_world, client, json_files, setup, status):
    setup(v8_world)
    signed_in(client)
    before = json_files()
    res = delete_account(client)
    assert res.status_code == status and v8_world.events[-2:] == ["rollback", "close"]
    assert json_files() == before
    assert_no_cookie(res)


# ---------- db.py 계정 헬퍼 (DB 없이) ----------

def fk_violation(constraint: str) -> IntegrityError:
    return IntegrityError("DELETE", None, FakePgError("23503", constraint))


def test_delete_user_sessions_sql():
    s = NestedSession(rowcount=3)
    assert db.delete_user_sessions(s, 7) == 3                            # type: ignore[arg-type]
    assert sql(s.statements[0]) == "DELETE FROM sessions WHERE sessions.user_id = %(user_id_1)s"


def test_delete_user_if_unchanged_sql_and_error_mapping():
    s = NestedSession(rowcount=1)
    assert db.delete_user_if_unchanged(s, 7, STORED) is True             # type: ignore[arg-type]
    assert sql(s.statements[0]) == ("DELETE FROM users WHERE users.id = %(id_1)s "
                                    "AND users.password_hash = %(password_hash_1)s")
    assert db.delete_user_if_unchanged(NestedSession(rowcount=0), 7, STORED) is False   # type: ignore[arg-type]
    with pytest.raises(db.AccountDeleteBlocked):
        db.delete_user_if_unchanged(NestedSession(fk_violation(db.DATA_MIGRATION_OWNER_FK)), 7, STORED)  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):                                  # 다른 FK·다른 무결성 오류는 그대로
        db.delete_user_if_unchanged(NestedSession(fk_violation("fk_other_user_id_users")), 7, STORED)  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):
        db.delete_user_if_unchanged(NestedSession(integrity(db.DATA_MIGRATION_OWNER_FK)), 7, STORED)  # type: ignore[arg-type]


def test_account_helpers_do_not_commit():
    for fn in (db.delete_user_sessions, db.delete_user_if_unchanged):
        calls = {n.func.attr for n in ast.walk(ast.parse(inspect.getsource(fn)))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not calls & {"commit", "rollback", "close"}, fn.__name__
