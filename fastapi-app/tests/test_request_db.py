"""(7.0.0 Phase 3-1A) 요청 Session(main.request_db)·사용자 데이터 헬퍼(db.py) — DB 없이 도는 테스트.

- 실제 엔진·연결을 만들지 않는다: db.get_sessionmaker·get_engine·make_engine 을 호출하면 실패하게 바꾼다.
- 요청 Session 은 데이터 경로 15개를 건드리지 않고, 이 파일 안의 별도 FastAPI 앱(테스트 전용 경로)으로 확인한다.
- 헬퍼는 PostgreSQL 방언으로 SQL 을 컴파일해 조건을 보고, 가짜 Session 으로 잠금 추적·허용 열·원자성을 본다.
- 아직 승인되지 않은 정책(같은 deleted_at 의 100건 정리 순서, bulk·반복 생성 때 정리 여부, lock_timeout,
  템플릿 UNIQUE 충돌의 응답)은 여기서 확정하지 않는다.
"""
import ast
import inspect
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

import auth
import db
import main

TOKEN = "T" * 43                                 # 쿠키 값(가짜 조회 함수가 받기만 한다)
CSRF = "csrf-value-1"
KEY = auth.FailureKey(b"k" * 32)                 # 테스트 전용 공개값
DETAIL_MARK = "DETAIL-SECRET-3f9c"               # 응답·로그에 나오면 안 되는 DB 오류 내용
BIND_MARK = "BIND-VALUE-77e1"                    # 바인드 값 흉내
NOW = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)
SETTINGS = auth.AuthSettings(app_env="test", cookie_secure=False, registration_enabled=True)


# ---------- 공통: 실제 DB 차단 ----------

def _no_db(*args: object, **kwargs: object) -> None:
    raise AssertionError("DB 없는 테스트가 실제 DB 에 접속하려고 함")


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    for name in ("get_sessionmaker", "get_engine", "make_engine"):
        monkeypatch.setattr(db, name, _no_db)


@pytest.fixture(autouse=True)
def auth_logger_enabled(monkeypatch):
    # 다른 테스트가 Alembic 을 프로세스 안에서 돌리면 fileConfig 가 이 로거를 끈다 — 로그 검사를 위해 켠다
    monkeypatch.setattr(logging.getLogger("todo.auth"), "disabled", False)


class FakePgError(Exception):
    # psycopg 오류처럼 sqlstate 를 가진다. 메시지에는 숨겨야 할 내용을 넣는다
    def __init__(self, sqlstate: str = "XX000") -> None:
        super().__init__(f"ERROR: boom\n{DETAIL_MARK} token_hash={BIND_MARK}")
        self.sqlstate = sqlstate


def db_error(sqlstate: str = "XX000") -> OperationalError:
    return OperationalError(f"COMMIT -- {BIND_MARK}", {"token_hash": BIND_MARK}, FakePgError(sqlstate))


# ---------- 요청 Session: 가짜 factory·Session·트랜잭션 ----------

class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.factory_calls = 0
        self.sessions: list["FakeSession"] = []
        self.open = 0
        self.commit_error: BaseException | None = None
        self.rollback_error: BaseException | None = None
        self.lookup_sessions: list[object] = []
        self.active = True                       # 가짜 세션 조회 결과가 있는가
        self.raise_in_handler: BaseException | None = None

    def factory(self) -> "FakeSession":
        self.factory_calls += 1
        session = FakeSession(self)
        self.sessions.append(session)
        return session


class FakeTx:
    def __init__(self, rec: Recorder) -> None:
        self.rec = rec

    def __enter__(self) -> "FakeTx":
        self.rec.events.append("begin")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            if self.rec.commit_error is not None:
                self.rec.events.append("commit_failed")
                raise self.rec.commit_error
            self.rec.events.append("commit")
        else:
            if self.rec.rollback_error is not None:
                self.rec.events.append("rollback_failed")
                raise self.rec.rollback_error
            self.rec.events.append("rollback")
        return False


class FakeSession:
    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.closed = False

    def __enter__(self) -> "FakeSession":
        self.rec.open += 1
        self.rec.events.append("open")
        return self

    def __exit__(self, *exc: object) -> bool:
        self.rec.open -= 1
        self.closed = True
        self.rec.events.append("close")
        return False

    def begin(self) -> FakeTx:
        return FakeTx(self.rec)


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()

    def get_active_session(s, token, now):
        r.lookup_sessions.append(s)
        r.events.append("lookup")
        if not r.active:
            return None
        return SimpleNamespace(csrf_token=CSRF), SimpleNamespace(id=7, username="alice")

    monkeypatch.setattr(db, "get_active_session", get_active_session)
    return r


def build_app(rec: Recorder) -> FastAPI:
    # 데이터 경로 15개와 무관한 테스트 전용 앱 — main 의 의존성만 가져다 쓴다
    app = FastAPI()
    app.dependency_overrides[main.auth_session_factory] = lambda: rec.factory
    app.dependency_overrides[main.auth_config] = lambda: main.AuthConfig(settings=SETTINGS, failure_key=KEY)
    seen: dict[str, object] = {}
    app.state.seen = seen

    def other(rdb: main.RequestDbDep) -> main.RequestDb:   # 같은 요청 안의 다른 의존성
        return rdb

    @app.get("/probe")
    def probe(rdb: main.RequestDbDep, user: main.CurrentUser = Depends(main.require_user),
              again: main.RequestDb = Depends(other)) -> dict:
        session = rdb.session()
        rec.events.append("handler")
        seen.update(rdb=rdb, again=again, session=session)
        return {"user": user.id}

    @app.post("/write")
    def write(rdb: main.RequestDbDep, user: main.CurrentUser = Depends(main.require_csrf)) -> dict:
        rdb.session()
        rec.events.append("handler")
        return {"user": user.id}

    @app.get("/stream")
    def stream(rdb: main.RequestDbDep, user: main.CurrentUser = Depends(main.require_user)) -> StreamingResponse:
        rdb.session()

        def body():
            rec.events.append("response_body")       # 응답 본문을 보내는 순간
            yield b"ok"
        return StreamingResponse(body())

    @app.get("/stream-request-scope")
    def stream_request_scope(rdb: main.RequestDb = Depends(main.request_db)) -> StreamingResponse:
        # 대조군 — scope 를 주지 않은 yield 의존성("request")은 응답을 보낸 뒤에 정리된다
        rdb.session()

        def body():
            rec.events.append("response_body")
            yield b"ok"
        return StreamingResponse(body())

    @app.get("/missing")
    def missing(rdb: main.RequestDbDep, user: main.CurrentUser = Depends(main.require_user)) -> dict:
        rdb.session()
        rec.events.append("handler")
        raise HTTPException(404, "To-Do item not found")

    @app.get("/no-db")
    def no_db(rdb: main.RequestDbDep) -> dict:
        return {"opened": rdb.opened}

    # (R-27) 핸들러에서 나는 예외 — rec.raise_in_handler 에 담은 예외를 그대로 던진다
    @app.post("/raise")
    def raise_in_handler(rdb: main.RequestDbDep, user: main.CurrentUser = Depends(main.require_csrf)) -> dict:
        rdb.session()
        rec.events.append("handler")
        raise rec.raise_in_handler

    @app.get("/raise-stream")
    def raise_before_stream(rdb: main.RequestDbDep, user: main.CurrentUser = Depends(main.require_user)):
        rdb.session()

        def body():
            rec.events.append("response_body")
            yield b"ok"
        if rec.raise_in_handler is not None:
            raise rec.raise_in_handler
        return StreamingResponse(body())

    @app.post("/validated")
    def validated(payload: Payload, rdb: main.RequestDbDep,
                  user: main.CurrentUser = Depends(main.require_csrf)) -> dict:
        rdb.session()
        rec.events.append("handler")
        return {"n": payload.n}

    return app


class Payload(BaseModel):
    n: int


def client_for(rec: Recorder, cookie: bool = True) -> TestClient:
    c = TestClient(build_app(rec))
    if cookie:
        c.cookies.set("todo_session", TOKEN)
    return c


def assert_clean(text: str) -> None:
    assert DETAIL_MARK not in text and BIND_MARK not in text and "token_hash" not in text
    assert "Traceback" not in text and "boom" not in text


# ---------- 1·3·12: 쿠키 없음, factory override, 앱 import ----------

def test_anonymous_request_is_401_without_opening_session(rec):
    res = client_for(rec, cookie=False).get("/probe")
    assert res.status_code == 401 and res.json() == {"detail": main.AUTH_REQUIRED}
    assert rec.factory_calls == 0 and rec.events == []


def test_main_auth_me_without_cookie_does_not_call_factory(rec):
    main.app.dependency_overrides[main.auth_session_factory] = lambda: rec.factory
    main.app.dependency_overrides[main.auth_config] = lambda: main.AuthConfig(settings=SETTINGS, failure_key=KEY)
    try:
        res = TestClient(main.app).get("/auth/me")
    finally:
        main.app.dependency_overrides.clear()
    assert res.status_code == 401 and rec.factory_calls == 0 and rec.events == []


def test_main_auth_me_uses_overridden_factory_with_one_transaction(rec):
    main.app.dependency_overrides[main.auth_session_factory] = lambda: rec.factory
    main.app.dependency_overrides[main.auth_config] = lambda: main.AuthConfig(settings=SETTINGS, failure_key=KEY)
    try:
        c = TestClient(main.app)
        c.cookies.set("todo_session", TOKEN)
        res = c.get("/auth/me")
    finally:
        main.app.dependency_overrides.clear()
    assert res.status_code == 200 and res.json() == {"id": 7, "username": "alice", "csrf_token": CSRF}
    assert rec.factory_calls == 1
    assert rec.events == ["open", "begin", "lookup", "commit", "close"]


def test_unused_request_db_never_opens_session(rec):
    res = client_for(rec, cookie=False).get("/no-db")
    assert res.status_code == 200 and res.json() == {"opened": False}
    assert rec.factory_calls == 0 and rec.events == []


def test_main_routes_declare_request_db_with_function_scope_only():
    # 같은 호출 대상이 scope 가 다르게 선언되면 캐시 키가 갈라져 Session 이 2개가 된다
    scopes = set()

    def walk(dependant):
        for sub in dependant.dependencies:
            if sub.call is main.request_db:
                scopes.add(sub.scope)
            walk(sub)

    for route in main.app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            walk(dependant)
    assert scopes == {"function"}                 # /auth/me 의 require_user 경로


# ---------- 4·5·11: Session 공유, 한 번만 열기, 닫기 ----------

def test_require_user_and_handler_share_one_request_db_and_session(rec):
    c = client_for(rec)
    app_seen = c.app.state.seen                   # type: ignore[attr-defined]
    res = c.get("/probe")
    assert res.status_code == 200 and res.json() == {"user": 7}
    assert app_seen["rdb"] is app_seen["again"]
    assert rec.lookup_sessions == [app_seen["session"]]       # 인증 조회와 핸들러가 같은 Session
    assert rec.factory_calls == 1 and len(rec.sessions) == 1
    assert rec.events == ["open", "begin", "lookup", "handler", "commit", "close"]
    assert rec.sessions[0].closed and rec.open == 0


def test_csrf_route_shares_session_and_commits_once(rec):
    res = client_for(rec).post("/write", headers={"X-CSRF-Token": CSRF})
    assert res.status_code == 200
    assert rec.factory_calls == 1
    assert rec.events == ["open", "begin", "lookup", "handler", "commit", "close"]


def test_csrf_mismatch_is_403_and_rolls_back(rec):
    res = client_for(rec).post("/write", headers={"X-CSRF-Token": "wrong"})
    assert res.status_code == 403 and res.json() == {"detail": main.CSRF_INVALID}
    assert rec.events == ["open", "begin", "lookup", "rollback", "close"]


def test_unknown_session_is_401_and_rolls_back(rec):
    rec.active = False
    res = client_for(rec).get("/probe")
    assert res.status_code == 401 and res.json() == {"detail": main.AUTH_REQUIRED}
    assert rec.events == ["open", "begin", "lookup", "rollback", "close"] and rec.open == 0


# ---------- 6: 응답 전에 commit ----------

def test_commit_happens_before_response_body_is_sent(rec):
    res = client_for(rec).get("/stream")
    assert res.status_code == 200 and res.content == b"ok"
    assert rec.events == ["open", "begin", "lookup", "commit", "close", "response_body"]


def test_control_request_scope_commits_after_response(rec):
    # scope 없는 선언(대조군)은 응답 본문 뒤에 commit 된다 — RequestDbDep(scope="function")만 쓰는 이유
    res = client_for(rec).get("/stream-request-scope")
    assert res.status_code == 200
    assert rec.events == ["open", "begin", "response_body", "commit", "close"]


# ---------- 7·8: commit 실패 ----------

@pytest.mark.parametrize("sqlstate", ["XX000", "55P03", "23505"])
def test_commit_failure_is_fixed_500_without_success_or_secrets(rec, caplog, sqlstate):
    rec.commit_error = db_error(sqlstate)
    caplog.set_level(logging.DEBUG)
    res = client_for(rec).get("/probe")
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert rec.events == ["open", "begin", "lookup", "handler", "commit_failed", "close"] and rec.open == 0
    assert_clean(res.text)
    assert_clean(caplog.text)
    assert all(r.exc_info is None for r in caplog.records)
    assert "request_db_commit_failed" in caplog.text and f"sqlstate={sqlstate}" in caplog.text


def test_commit_failure_before_streaming_sends_no_body(rec):
    rec.commit_error = db_error()
    res = client_for(rec).get("/stream")
    assert res.status_code == 500 and res.content != b"ok"
    assert "response_body" not in rec.events


# ---------- 9·10: 핸들러 예외와 rollback 실패 ----------

def test_handler_http_error_rolls_back_and_keeps_status(rec):
    res = client_for(rec).get("/missing")
    assert res.status_code == 404 and res.json() == {"detail": "To-Do item not found"}
    assert rec.events == ["open", "begin", "lookup", "handler", "rollback", "close"] and rec.open == 0


def test_rollback_failure_keeps_original_status_without_secrets(rec, caplog):
    rec.rollback_error = db_error()
    caplog.set_level(logging.DEBUG)
    res = client_for(rec).get("/missing")
    assert res.status_code == 404 and res.json() == {"detail": "To-Do item not found"}
    assert rec.events == ["open", "begin", "lookup", "handler", "rollback_failed", "close"] and rec.open == 0
    assert_clean(res.text)
    assert_clean(caplog.text)
    assert all(r.exc_info is None for r in caplog.records)
    assert "request_db_rollback_failed" in caplog.text


def test_lookup_database_error_is_500_and_session_closed(rec, caplog, monkeypatch):
    def broken(s, token, now):
        rec.events.append("lookup")
        raise db_error()
    monkeypatch.setattr(db, "get_active_session", broken)
    caplog.set_level(logging.DEBUG)
    res = client_for(rec).get("/probe")
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert rec.events == ["open", "begin", "lookup", "rollback", "close"] and rec.open == 0
    assert_clean(res.text)
    assert_clean(caplog.text)


def test_request_db_finish_without_open_is_noop():
    holder = main.RequestDb(_no_db)               # type: ignore[arg-type]
    holder.finish(None)
    holder.finish(RuntimeError("x"))
    assert holder.opened is False


# ---------- (R-27) 핸들러의 DB 예외 → 고정 500, 응답·로그 비노출 ----------

class FakePsycopgError(psycopg.Error):
    # 드라이버 예외를 직접 받는 경우 — 메시지에 숨겨야 할 내용을 넣는다
    sqlstate = "40P01"                           # type: ignore[assignment]


def integrity_error() -> IntegrityError:
    return IntegrityError(f"INSERT INTO task_templates -- {BIND_MARK}", {"token_hash": BIND_MARK},
                          FakePgError("23505"))


def dbapi_error() -> DBAPIError:
    return DBAPIError(f"UPDATE todos -- {BIND_MARK}", {"token_hash": BIND_MARK}, FakePgError("40001"))


def psycopg_error() -> FakePsycopgError:
    return FakePsycopgError(f"ERROR: boom\n{DETAIL_MARK} token_hash={BIND_MARK}")


DB_ERROR_CASES = {"integrity": (integrity_error, "23505"), "dbapi": (dbapi_error, "40001"),
                  "operational": (lambda: db_error("XX000"), "XX000"), "psycopg": (psycopg_error, "40P01")}


def post_raise(rec: Recorder, client: TestClient | None = None):
    c = client or client_for(rec)
    return c.post("/raise", headers={"X-CSRF-Token": CSRF})


def assert_logs_clean(caplog) -> None:
    assert_clean(caplog.text)
    assert all(r.exc_info is None and r.exc_text is None for r in caplog.records)
    for record in caplog.records:                 # 앱 로그 인자에도 예외 객체·원문이 없다 (사건명·클래스명·SQLSTATE 문자열만)
        if record.name == "todo.auth":
            assert all(isinstance(a, (str, type(None))) for a in (record.args or ()))


@pytest.mark.parametrize("case", sorted(DB_ERROR_CASES))
def test_handler_db_error_is_fixed_500_rolled_back_and_closed(rec, caplog, case):
    make, sqlstate = DB_ERROR_CASES[case]
    rec.raise_in_handler = make()
    caplog.set_level(logging.DEBUG)
    res = post_raise(rec)
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert rec.events == ["open", "begin", "lookup", "handler", "rollback", "close"] and rec.open == 0
    assert rec.sessions[0].closed
    assert_clean(res.text)
    assert_logs_clean(caplog)
    assert f"request_db_error exc_type={type(rec.raise_in_handler).__name__} sqlstate={sqlstate}" in caplog.text


@pytest.mark.parametrize("case", sorted(DB_ERROR_CASES))
def test_handler_db_error_with_rollback_failure_stays_clean(rec, caplog, case):
    make, _ = DB_ERROR_CASES[case]
    rec.raise_in_handler = make()
    rec.rollback_error = db_error("08006")
    caplog.set_level(logging.DEBUG)
    res = post_raise(rec)
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert rec.events == ["open", "begin", "lookup", "handler", "rollback_failed", "close"] and rec.open == 0
    assert_clean(res.text)
    assert_logs_clean(caplog)
    assert "request_db_error" in caplog.text and "request_db_rollback_failed" in caplog.text
    assert "sqlstate=08006" in caplog.text


def test_db_error_before_streaming_sends_no_body(rec, caplog):
    rec.raise_in_handler = integrity_error()
    caplog.set_level(logging.DEBUG)
    res = client_for(rec).get("/raise-stream")
    assert res.status_code == 500 and res.json() == {"detail": main.SERVER_ERROR}
    assert "response_body" not in rec.events and rec.events[-2:] == ["rollback", "close"]
    assert_logs_clean(caplog)


def test_streaming_success_still_commits_before_body(rec):
    res = client_for(rec).get("/raise-stream")
    assert res.status_code == 200 and res.content == b"ok"
    assert rec.events == ["open", "begin", "lookup", "commit", "close", "response_body"]


def test_db_error_500_has_no_cause_or_context():
    # 원래 DB 예외를 __cause__·__context__ 로 붙이지 않는다 — 어디서 traceback 을 찍어도 원문이 따라가지 않게
    rec = Recorder()
    gen = main.request_db(rec.factory)
    holder = next(gen)
    holder.session()
    with pytest.raises(HTTPException) as e:
        gen.throw(integrity_error())
    assert e.value.status_code == 500 and e.value.detail == main.SERVER_ERROR
    assert e.value.__cause__ is None and e.value.__context__ is None
    assert rec.events == ["open", "begin", "rollback", "close"]


def test_commit_db_error_500_has_no_cause_or_context():
    rec = Recorder()
    rec.commit_error = db_error()
    gen = main.request_db(rec.factory)
    next(gen).session()
    with pytest.raises(HTTPException) as e:
        next(gen)
    assert e.value.status_code == 500 and e.value.__cause__ is None and e.value.__context__ is None
    assert rec.events == ["open", "begin", "commit_failed", "close"]


@pytest.mark.parametrize("status", [401, 403, 404, 409])
def test_handler_http_exceptions_keep_status_and_roll_back(rec, status):
    rec.raise_in_handler = HTTPException(status, f"detail-{status}")
    res = post_raise(rec)
    assert res.status_code == status and res.json() == {"detail": f"detail-{status}"}
    assert rec.events == ["open", "begin", "lookup", "handler", "rollback", "close"]


def test_validation_error_after_auth_is_422_and_rolls_back(rec):
    res = client_for(rec).post("/validated", json={"n": "not-a-number"}, headers={"X-CSRF-Token": CSRF})
    assert res.status_code == 422
    assert rec.events == ["open", "begin", "lookup", "rollback", "close"] and rec.open == 0


def test_validation_passes_and_commits(rec):
    res = client_for(rec).post("/validated", json={"n": 3}, headers={"X-CSRF-Token": CSRF})
    assert res.status_code == 200 and res.json() == {"n": 3}
    assert rec.events == ["open", "begin", "lookup", "handler", "commit", "close"]


@pytest.mark.parametrize("error, status", [(db_error("XX000"), 500), (db_error("55P03"), 503)])
def test_session_lookup_errors_keep_auth_500_503(rec, caplog, monkeypatch, error, status):
    # require_user 의 기존 계약(DB 오류 500, 잠금 대기 초과 503)은 HTTPException 이라 그대로 지나간다
    def broken(s, token, now):
        raise error
    monkeypatch.setattr(db, "get_active_session", broken)
    caplog.set_level(logging.DEBUG)
    res = client_for(rec).get("/probe")
    assert res.status_code == status
    assert rec.events == ["open", "begin", "rollback", "close"]
    assert "request_db_error" not in caplog.text            # DB 예외 변환은 require_user 가 이미 했다
    assert_logs_clean(caplog)


@pytest.mark.parametrize("error", [ValueError("programming bug"), KeyError("k"), RuntimeError("x")])
def test_python_errors_are_not_classified_as_db_errors(rec, caplog, error):
    rec.raise_in_handler = error
    caplog.set_level(logging.DEBUG)
    with pytest.raises(type(error)):                          # 삼키지 않고 그대로 올린다 (프로그래밍 오류를 숨기지 않음)
        post_raise(rec)
    assert rec.events == ["open", "begin", "lookup", "handler", "rollback", "close"] and rec.open == 0
    assert "request_db_error" not in caplog.text


def test_python_error_is_not_turned_into_fixed_json_500(rec):
    rec.raise_in_handler = ValueError("programming bug")
    res = post_raise(rec, TestClient(build_app(rec), raise_server_exceptions=False, cookies={"todo_session": TOKEN}))
    assert res.status_code == 500 and res.text != '{"detail":"' + main.SERVER_ERROR + '"}'
    assert rec.events[-2:] == ["rollback", "close"]


def test_commit_python_error_is_not_classified_as_db_error(rec, caplog):
    rec.commit_error = RuntimeError("bug in commit hook")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError):
        client_for(rec).get("/probe")
    assert "request_db_commit_failed" not in caplog.text
    assert rec.events[-2:] == ["commit_failed", "close"] and rec.open == 0


@pytest.mark.parametrize("value, expected", [
    ("23505", "23505"), ("55P03", "55P03"), (None, None), ("2350", None), ("23505\nDETAIL", None),
    ("ab123", None), (23505, None),
])
def test_safe_sqlstate_only_accepts_five_char_codes(value, expected):
    exc = OperationalError("x", None, SimpleNamespace(sqlstate=value))   # type: ignore[arg-type]
    assert main._safe_sqlstate(exc) == expected


def test_main_still_converts_only_db_errors():
    assert main.DB_ERRORS == (main.SQLAlchemyError, psycopg.Error)
    assert issubclass(IntegrityError, main.SQLAlchemyError) and issubclass(DBAPIError, main.SQLAlchemyError)


# ---------- DB 헬퍼: 가짜 Session ----------

class FakeResult:
    def __init__(self, value: object = None, rows: list | None = None, rowcount: int = 1) -> None:
        self.value = value
        self.rows = rows if rows is not None else []
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def one_or_none(self):
        return self.value

    def first(self):
        return self.value

    def scalars(self):
        return iter(self.rows)

    def __iter__(self):
        return iter(self.rows)


class HelperSession:
    """execute·scalars 만 흉내 낸다. commit·rollback 을 부르면 실패한다(헬퍼는 트랜잭션을 끝내지 않는다)."""

    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.statements: list[object] = []
        self.results = list(results or [])
        self.info: dict = {}
        self.tx = object()

    def _next(self) -> FakeResult:
        return self.results.pop(0) if self.results else FakeResult()

    def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return self._next()

    def scalars(self, stmt):
        self.statements.append(stmt)
        return self._next()

    def get_transaction(self):
        return self.tx

    def commit(self):
        raise AssertionError("헬퍼가 commit 함")

    def rollback(self):
        raise AssertionError("헬퍼가 rollback 함")

    def sql(self, index: int = -1) -> str:
        return compile_sql(self.statements[index])


def compile_sql(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


def locked(user_id: int = 7, results: list[FakeResult] | None = None) -> HelperSession:
    s = HelperSession([FakeResult(user_id)] + list(results or []))
    assert db.lock_user(s, user_id) is True
    return s


# ---------- lock_user·잠금 추적 ----------

def test_lock_user_selects_user_row_for_update():
    s = HelperSession([FakeResult(7)])
    assert db.lock_user(s, 7) is True
    sql = s.sql(0)
    assert "FROM users" in sql and "users.id = %(id_1)s" in sql and sql.endswith("FOR UPDATE")


def test_lock_user_missing_user_does_not_record_lock():
    s = HelperSession([FakeResult(None)])
    assert db.lock_user(s, 7) is False
    with pytest.raises(RuntimeError):
        db.insert_todo(s, 7, {"title": "t"})


WRITE_CALLS = {
    "next_todo_position": lambda s, uid: db.next_todo_position(s, uid),
    "insert_todo": lambda s, uid: db.insert_todo(s, uid, {"title": "t"}),
    "update_owned_todo": lambda s, uid: db.update_owned_todo(s, uid, 1, {"title": "t"}),
    "soft_delete_todos": lambda s, uid: db.soft_delete_todos(s, uid, [1], NOW),
    "trim_deleted_todos": lambda s, uid: db.trim_deleted_todos(s, uid),
    "restore_todos": lambda s, uid: db.restore_todos(s, uid, [1], NOW),
    "purge_expired_deleted_todos": lambda s, uid: db.purge_expired_deleted_todos(s, uid, NOW),
    "add_subtask": lambda s, uid: db.add_subtask(s, uid, 1, "t"),
    "update_owned_subtask": lambda s, uid: db.update_owned_subtask(s, uid, 1, 1, {"title": "t"}),
    "delete_owned_subtask": lambda s, uid: db.delete_owned_subtask(s, uid, 1, 1),
    "insert_template": lambda s, uid: db.insert_template(s, uid, {"name": "n", "title": "t"}),
    "update_owned_template": lambda s, uid: db.update_owned_template(s, uid, 1, {"name": "n"}),
    "delete_owned_template": lambda s, uid: db.delete_owned_template(s, uid, 1),
}


@pytest.mark.parametrize("name", sorted(WRITE_CALLS))
def test_write_helpers_refuse_without_lock(name):
    s = HelperSession()
    with pytest.raises(RuntimeError):
        WRITE_CALLS[name](s, 7)
    assert s.statements == []                     # 아무 SQL 도 보내지 않는다


@pytest.mark.parametrize("name", sorted(WRITE_CALLS))
def test_lock_of_user_a_does_not_allow_writes_for_user_b(name):
    s = locked(7)
    with pytest.raises(RuntimeError):
        WRITE_CALLS[name](s, 8)
    assert len(s.statements) == 1                 # lock_user 의 SELECT 하나뿐


@pytest.mark.parametrize("name", sorted(WRITE_CALLS))
def test_lock_from_previous_transaction_is_not_reused(name):
    s = locked(7)
    s.tx = object()                               # 트랜잭션이 끝나고 새 트랜잭션 — Session.info 는 그대로 남아 있다
    assert s.info                                 # 이전 기록이 남아 있어도
    with pytest.raises(RuntimeError):
        WRITE_CALLS[name](s, 7)


def test_lock_tracking_with_real_session_transactions(monkeypatch):
    # 실제 SQLAlchemy Session(연결 없음)의 트랜잭션 객체로 확인 — execute 만 가짜로 바꾼다
    s = Session()
    monkeypatch.setattr(s, "execute", lambda stmt, *a, **k: FakeResult(7, rowcount=1))
    s.begin()
    assert db.lock_user(s, 7) is True
    db.update_owned_todo(s, 7, 1, {"title": "t"})   # 같은 트랜잭션에서는 허용
    s.commit()
    s.begin()                                     # 새 트랜잭션 — info 는 같은 dict
    with pytest.raises(RuntimeError):
        db.update_owned_todo(s, 7, 1, {"title": "t"})
    s.rollback()
    s.close()


# ---------- 조회 헬퍼 SQL ----------

def test_active_todos_filters_owner_and_active_and_orders_by_position_id():
    s = HelperSession([FakeResult(rows=[])])
    assert db.active_todos(s, 7) == []
    sql = s.sql()
    assert "todos.user_id = %(user_id_1)s" in sql and "todos.deleted_at IS NULL" in sql
    assert sql.endswith("ORDER BY todos.position, todos.id")


def test_get_owned_todo_filters_id_owner_and_active():
    s = HelperSession([FakeResult(None)])
    assert db.get_owned_todo(s, 7, 3) is None
    sql = s.sql()
    assert "todos.id = %(id_1)s" in sql and "todos.user_id = %(user_id_1)s" in sql
    assert "todos.deleted_at IS NULL" in sql


def test_owned_todos_by_ids_filters_owner_and_active():
    rows = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    s = HelperSession([FakeResult(rows=rows)])
    assert set(db.owned_todos_by_ids(s, 7, [1, 2, 1])) == {1, 2}
    sql = s.sql()
    assert "todos.id IN (__[POSTCOMPILE_id_1])" in sql
    assert "todos.user_id = %(user_id_1)s" in sql and "todos.deleted_at IS NULL" in sql


def test_subtasks_of_joins_parent_owner_and_active():
    rows = [SimpleNamespace(todo_id=1, id=1), SimpleNamespace(todo_id=1, id=2), SimpleNamespace(todo_id=2, id=1)]
    s = HelperSession([FakeResult(rows=rows)])
    grouped = db.subtasks_of(s, 7, [1, 2])
    assert [r.id for r in grouped[1]] == [1, 2] and [r.id for r in grouped[2]] == [1]
    sql = s.sql()
    assert "JOIN todos ON todos.id = subtasks.todo_id" in sql
    assert "todos.user_id = %(user_id_1)s" in sql and "todos.deleted_at IS NULL" in sql
    assert sql.endswith("ORDER BY subtasks.todo_id, subtasks.id")


def test_owned_templates_filters_owner_and_orders_by_position_id():
    s = HelperSession([FakeResult(rows=[])])
    db.owned_templates(s, 7)
    sql = s.sql()
    assert "task_templates.user_id = %(user_id_1)s" in sql
    assert sql.endswith("ORDER BY task_templates.position, task_templates.id")


def test_get_owned_template_filters_id_and_owner():
    s = HelperSession([FakeResult(None)])
    assert db.get_owned_template(s, 7, 3) is None
    sql = s.sql()
    assert "task_templates.id = %(id_1)s" in sql and "task_templates.user_id = %(user_id_1)s" in sql


@pytest.mark.parametrize("exclude_id, expect_exclude", [(None, False), (5, True)])
def test_template_name_taken_uses_owner_and_lower_name(exclude_id, expect_exclude):
    s = HelperSession([FakeResult(None)])
    assert db.template_name_taken(s, 7, "Name", exclude_id=exclude_id) is False
    sql = s.sql()
    assert "task_templates.user_id = %(user_id_1)s" in sql
    assert "lower(task_templates.name) = lower(%(lower_1)s" in sql
    assert ("task_templates.id != %(id_1)s" in sql) is expect_exclude


# ---------- 쓰기 헬퍼 SQL ----------

def test_next_position_includes_pending_rows():
    s = locked(7, [FakeResult(4)])
    assert db.next_todo_position(s, 7) == 4
    sql = s.sql()
    assert "max(todos.position)" in sql and "todos.user_id = %(user_id_1)s" in sql
    assert "deleted_at" not in sql                # 삭제 대기 행도 max 에 포함 (§27.3)


def test_insert_todo_sets_owner_position_and_numbers_subtasks_from_one():
    s = locked(7, [FakeResult(3), FakeResult(41), FakeResult()])
    todo_id = db.insert_todo(s, 7, {"title": "t", "completed": False}, subtasks=[("a", False), ("b", False)])
    assert todo_id == 41
    insert_sql = s.sql(2)
    assert insert_sql.startswith("INSERT INTO todos") and "RETURNING todos.id" in insert_sql
    params = s.statements[2].compile(dialect=postgresql.dialect()).params
    assert params["user_id"] == 7 and params["position"] == 3
    assert s.sql(3).startswith("INSERT INTO subtasks")


@pytest.mark.parametrize("field", ["id", "user_id", "position", "deleted_at", "unknown"])
def test_insert_todo_refuses_server_owned_or_unknown_fields(field):
    s = locked(7)
    with pytest.raises(ValueError) as e:
        db.insert_todo(s, 7, {"title": "t", field: 1})
    assert field in str(e.value) and len(s.statements) == 1


@pytest.mark.parametrize("field", ["id", "user_id", "position", "deleted_at", "created_at"])
def test_update_owned_todo_refuses_protected_fields(field):
    s = locked(7)
    with pytest.raises(ValueError):
        db.update_owned_todo(s, 7, 1, {field: 1})
    assert len(s.statements) == 1


def test_update_owned_todo_has_owner_and_active_conditions():
    s = locked(7, [FakeResult(rowcount=1)])
    assert db.update_owned_todo(s, 7, 3, {"title": "t"}) is True
    sql = s.sql()
    assert sql.startswith("UPDATE todos SET title=")
    assert "todos.id = %(id_1)s" in sql and "todos.user_id = %(user_id_1)s" in sql
    assert "todos.deleted_at IS NULL" in sql


def test_update_owned_todo_reports_missing_as_false():
    s = locked(7, [FakeResult(rowcount=0)])
    assert db.update_owned_todo(s, 7, 3, {"title": "t"}) is False


def test_soft_delete_sets_deleted_at_with_owner_and_active_then_trims():
    s = locked(7, [FakeResult(rows=[2, 1]), FakeResult(rowcount=0)])
    assert db.soft_delete_todos(s, 7, [1, 2, 1], NOW) == [1, 2]   # 요청 순서, 중복 제거
    update_sql, trim_sql = s.sql(1), s.sql(2)
    assert update_sql.startswith("UPDATE todos SET deleted_at=")
    assert "todos.user_id = %(user_id_1)s" in update_sql and "todos.deleted_at IS NULL" in update_sql
    assert trim_sql.startswith("DELETE FROM todos")
    assert "todos.user_id = %(user_id_1)s" in trim_sql and "todos.deleted_at IS NOT NULL" in trim_sql
    assert "LIMIT %(param_1)s" in trim_sql        # 100건 한도 (같은 시각의 순서는 확정하지 않는다)


def test_soft_delete_requires_aware_now():
    s = locked(7)
    with pytest.raises(ValueError):
        db.soft_delete_todos(s, 7, [1], datetime(2026, 11, 8, 9, 0))


def test_restore_is_atomic_when_any_id_is_not_restorable():
    s = locked(7, [FakeResult(rows=[1])])
    assert db.restore_todos(s, 7, [1, 2], NOW) is None
    assert len(s.statements) == 2                 # lock + 확인 SELECT — UPDATE 없음
    sql = s.sql()
    assert "todos.user_id = %(user_id_1)s" in sql and "todos.deleted_at IS NOT NULL" in sql
    assert "todos.deleted_at > %(deleted_at_1)s" in sql
    params = s.statements[-1].compile(dialect=postgresql.dialect()).params
    assert params["deleted_at_1"] == NOW - timedelta(seconds=30)


def test_restore_clears_deleted_at_for_owned_recent_rows():
    s = locked(7, [FakeResult(rows=[2, 1]), FakeResult()])
    assert db.restore_todos(s, 7, [2, 1], NOW) == [2, 1]
    sql = s.sql()
    assert sql.startswith("UPDATE todos SET deleted_at=") and "todos.user_id = %(user_id_1)s" in sql
    assert "todos.deleted_at > %(deleted_at_1)s" in sql and "todos.deleted_at IS NOT NULL" in sql


def test_purge_expired_deletes_only_owner_pending_rows_older_than_window():
    s = locked(7, [FakeResult(rowcount=2)])
    assert db.purge_expired_deleted_todos(s, 7, NOW) == 2
    sql = s.sql()
    assert sql.startswith("DELETE FROM todos")
    assert "todos.user_id = %(user_id_1)s" in sql and "todos.deleted_at IS NOT NULL" in sql
    assert "todos.deleted_at <= %(deleted_at_1)s" in sql


def test_add_subtask_checks_parent_owner_before_insert():
    s = locked(7, [FakeResult(None)])
    assert db.add_subtask(s, 7, 3, "t") is None
    assert len(s.statements) == 2                 # 상위 확인에서 멈춘다
    sql = s.sql()
    assert "todos.user_id = %(user_id_1)s" in sql and "todos.deleted_at IS NULL" in sql


def test_add_subtask_uses_max_sid_plus_one():
    s = locked(7, [FakeResult(3), FakeResult(5), FakeResult()])
    assert db.add_subtask(s, 7, 3, "t") == 5
    assert "max(subtasks.id)" in s.sql(2) and s.sql(3).startswith("INSERT INTO subtasks")


def test_subtask_update_and_delete_require_parent_ownership():
    s = locked(7, [FakeResult(rowcount=1), FakeResult(rowcount=1)])
    assert db.update_owned_subtask(s, 7, 3, 2, {"completed": True}) is True
    assert db.delete_owned_subtask(s, 7, 3, 2) is True
    for sql in (s.sql(1), s.sql(2)):
        assert "subtasks.todo_id IN (SELECT todos.id FROM todos" in sql
        assert "todos.user_id = %(user_id_1)s" in sql and "todos.deleted_at IS NULL" in sql


def test_update_owned_subtask_refuses_other_fields():
    s = locked(7)
    with pytest.raises(ValueError):
        db.update_owned_subtask(s, 7, 3, 2, {"todo_id": 9})


def test_template_writes_have_owner_conditions():
    s = locked(7, [FakeResult(2), FakeResult(11), FakeResult(rowcount=1), FakeResult(rowcount=1)])
    assert db.insert_template(s, 7, {"name": "n", "title": "t"}) == 11
    assert "max(task_templates.position)" in s.sql(1) and "task_templates.user_id = %(user_id_1)s" in s.sql(1)
    params = s.statements[2].compile(dialect=postgresql.dialect()).params
    assert params["user_id"] == 7 and params["position"] == 2
    assert db.update_owned_template(s, 7, 11, {"name": "m"}) is True
    assert db.delete_owned_template(s, 7, 11) is True
    for sql in (s.sql(3), s.sql(4)):
        assert "task_templates.id = %(id_1)s" in sql and "task_templates.user_id = %(user_id_1)s" in sql


@pytest.mark.parametrize("field", ["id", "user_id", "position", "unknown"])
def test_template_helpers_refuse_server_owned_fields(field):
    s = locked(7)
    with pytest.raises(ValueError):
        db.insert_template(s, 7, {"name": "n", field: 1})
    with pytest.raises(ValueError):
        db.update_owned_template(s, 7, 1, {field: 1})


def test_data_helpers_never_commit_or_rollback():
    src = inspect.getsource(db)
    section = src[src.index("# ---------- 사용자 데이터 헬퍼 (Phase 3-1A"):]
    calls = {node.func.attr for node in ast.walk(ast.parse(section))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {"commit", "rollback", "begin", "begin_nested", "close"}
    assert "SET LOCAL lock_timeout" not in section   # 데이터 쓰기 잠금 대기 상한은 미결정 — 설정하지 않는다


def test_purge_helper_is_not_called_from_main_yet():
    # 정리 호출 시점(bulk·반복 생성)은 미결정 — 3-1A 에서는 어떤 경로에도 연결하지 않는다
    assert "purge_expired_deleted_todos" not in inspect.getsource(main)
