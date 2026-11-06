"""(7.0.0 Phase 2-E) /auth/* 라우트 — DB 없이 도는 테스트.

- 실제 엔진·Session 을 만들려고 하면 실패하게 막고, db 함수와 Session 팩토리를 가짜로 바꾼다(PostgreSQL 접속 없음).
- 가짜 Session 은 열기·BEGIN·COMMIT·ROLLBACK·닫기를 순서대로 기록한다 → 트랜잭션 경계와 "commit 뒤 응답"을 확인한다.
- 클라이언트 IP 는 TestClient(client=...) 로 명시한다(기본값 'testclient' 는 IP 가 아니라 500).
"""
import ast
import inspect
import logging
import re
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


# ---------- me · logout · CSRF ----------

def active_session():
    return SimpleNamespace(csrf_token=CSRF), SimpleNamespace(id=7, username="alice")


def test_me_without_cookie_is_401_without_database(world, client):
    res = client.get("/auth/me")
    assert res.status_code == 401 and res.json() == {"detail": main.AUTH_REQUIRED} and world.events == []


def test_me_with_active_session(world, client):
    world.active = active_session()
    client.cookies.set("todo_session", TOKEN)
    res = client.get("/auth/me")
    assert res.status_code == 200 and res.json() == {"id": 7, "username": "alice", "csrf_token": CSRF}
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
