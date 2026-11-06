"""(7.0.0 Phase 2-D) 로그인·가입 시도 제한 — DB 없이 검증한다.

- auth.py 순수 부분: 판정·Retry-After, username HMAC 키와 설정, IP 정규화, Argon2 슬롯.
- db.py 함수: 가짜 Session 으로 SQL 순서(잠금 → 검사 → 선기록), 바인드 값, 삭제 범위(자기 id), commit 없음.
실제 PostgreSQL 동작(잠금 직렬화·동시성·SKIP LOCKED)은 test_db_postgres.py 에 있다(실행은 별도 승인).
"""
import ast
import base64
import hashlib
import hmac
import logging
import pickle
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

import auth
import db

APP_DIR = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
DIALECT = postgresql.dialect()
KEY = auth.FailureKey(b"k" * 32)
NAME = auth.login_name("Alice", KEY)
IP = "203.0.113.7"


def ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


# ────────────────────────── 판정 (limit_decision) ──────────────────────────

def rule(limit: int, *times: datetime, window: timedelta = auth.LOGIN_WINDOW) -> auth.LimitRule:
    return auth.LimitRule(limit, window, list(times))


def test_d12_values():
    assert (auth.LOGIN_LIMIT_USER_IP, auth.LOGIN_LIMIT_USER, auth.LOGIN_LIMIT_IP, auth.REGISTER_LIMIT_IP) == \
        (5, 50, 20, 10)
    assert (auth.LOGIN_WINDOW, auth.REGISTER_WINDOW, auth.FAILURE_RETENTION) == \
        (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=24))


def test_below_limit_is_allowed():
    assert auth.limit_decision([rule(5, *(ago(minutes=m) for m in range(1, 5)))], NOW) == auth.LimitDecision(True, 0)
    assert auth.limit_decision([], NOW) == auth.LimitDecision(True, 0)


def test_at_limit_is_blocked_until_oldest_leaves_window():
    d = auth.limit_decision([rule(5, *(ago(minutes=m) for m in range(1, 6)))], NOW)
    assert d == auth.LimitDecision(False, 600)         # 5분 전 기록 + 15분 − 지금


def test_retry_after_uses_kth_record_when_over_limit():
    # 7개 > 한도 5 → 오래된 3개가 빠져야 한다 = 최신 5번째(5분 전), 가장 오래된(7분 전)이 아니다
    d = auth.limit_decision([rule(5, *(ago(minutes=m) for m in range(1, 8)))], NOW)
    assert d.retry_after == 600


def test_window_boundary_is_exclusive():
    edge = ago(minutes=15)                              # 정확히 창 시작 = 창 밖
    assert auth.limit_decision([rule(1, edge)], NOW).allowed
    d = auth.limit_decision([rule(1, edge + timedelta(microseconds=1))], NOW)
    assert d == auth.LimitDecision(False, 1)            # 1µs 남음 → 올림, 최소 1초


def test_unblocked_exactly_at_release_time():
    times = [ago(minutes=m) for m in range(1, 6)]
    release = NOW + timedelta(seconds=600)
    assert not auth.limit_decision([rule(5, *times)], release - timedelta(microseconds=1)).allowed
    assert auth.limit_decision([rule(5, *times)], release).allowed


def test_retry_after_rounds_up():
    d = auth.limit_decision([rule(1, ago(minutes=14, seconds=59, milliseconds=500))], NOW)
    assert d.retry_after == 1
    d = auth.limit_decision([rule(1, ago(minutes=10, milliseconds=1))], NOW)
    assert d.retry_after == 300                         # 299.999초 → 300


def test_longest_wait_across_rules_and_unsorted_input():
    user_ip = rule(2, ago(minutes=1), ago(minutes=2))                       # 13분 뒤 풀림
    by_ip = rule(2, ago(minutes=9), ago(minutes=3), ago(minutes=12))        # 정렬 안 됨 → 최신 2번째 = 9분 전 → 6분
    d = auth.limit_decision([by_ip, user_ip], NOW)
    assert d == auth.LimitDecision(False, 13 * 60)


def test_register_window_is_one_hour():
    times = [ago(minutes=m) for m in range(1, 11)]
    d = auth.limit_decision([rule(10, *times, window=auth.REGISTER_WINDOW)], NOW)
    assert d == auth.LimitDecision(False, 50 * 60)


@pytest.mark.parametrize("now, times", [
    (NOW.replace(tzinfo=None), [ago(minutes=1)]),
    (NOW, [ago(minutes=1).replace(tzinfo=None)]),
])
def test_naive_times_rejected(now, times):
    with pytest.raises(ValueError):
        auth.limit_decision([auth.LimitRule(5, auth.LOGIN_WINDOW, times)], now)


@pytest.mark.parametrize("limit, window", [(0, auth.LOGIN_WINDOW), (5, timedelta(0))])
def test_invalid_rule_rejected(limit, window):
    with pytest.raises(ValueError):
        auth.limit_decision([auth.LimitRule(limit, window, [])], NOW)


# ────────────────────────── username HMAC 키와 설정 ──────────────────────────

def token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def test_key_from_environment():
    raw = token()
    key = auth.load_failure_key({"APP_ENV": "production", "AUTH_FAILURE_KEY": raw})
    assert key.development_fallback is False
    secret = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    expected = hmac.new(secret, b"auth-failure:v1:alice", hashlib.sha256).digest()[:22]
    assert key.username_key("alice") == base64.urlsafe_b64encode(expected).rstrip(b"=").decode()


def test_key_with_padding_is_same_key():
    raw = base64.urlsafe_b64encode(b"p" * 32).decode()            # '=' 1개 패딩
    assert raw.endswith("=")
    a = auth.load_failure_key({"APP_ENV": "production", "AUTH_FAILURE_KEY": raw})
    b = auth.load_failure_key({"APP_ENV": "production", "AUTH_FAILURE_KEY": raw.rstrip("=")})
    assert a.username_key("alice") == b.username_key("alice")


@pytest.mark.parametrize("environ", [
    {"APP_ENV": "production"},
    {},                                                 # APP_ENV 미설정 — 기본값 development 에 기대지 않는다
    {"APP_ENV": "production", "AUTH_FAILURE_KEY": ""},
])
def test_key_required_outside_explicit_dev_and_test(environ):
    with pytest.raises(auth.AuthConfigError) as e:
        auth.load_failure_key(environ)
    assert "AUTH_FAILURE_KEY" in str(e.value)


@pytest.mark.parametrize("app_env", ["development", "test", " Test "])
def test_public_fallback_only_when_dev_or_test_explicit(app_env):
    key = auth.load_failure_key({"APP_ENV": app_env})
    assert key.development_fallback is True and len(key.username_key("alice")) == 30


def test_explicit_key_preferred_in_development():
    raw = token()
    key = auth.load_failure_key({"APP_ENV": "development", "AUTH_FAILURE_KEY": raw})
    assert key.development_fallback is False
    assert key.username_key("alice") != auth.load_failure_key({"APP_ENV": "development"}).username_key("alice")


def test_invalid_app_env_rejected_before_fallback():
    with pytest.raises(auth.AuthConfigError) as e:
        auth.load_failure_key({"APP_ENV": "staging"})
    assert "APP_ENV" in str(e.value)


FIXED = base64.urlsafe_b64encode(b"v" * 32).rstrip(b"=").decode()     # 올바른 키 (테스트 id 가 매번 같게 고정값)
SHORT = base64.urlsafe_b64encode(b"v" * 31).rstrip(b"=").decode()


@pytest.mark.parametrize("raw", [
    "", " " + FIXED, FIXED + "\n", SHORT, "abc$" + FIXED, "A" * 45,      # 45자 → base64 길이 오류
    FIXED[:-1] + "+", FIXED[:-1] + "/", "=" * 4, "한글" + FIXED,
], ids=["empty", "lead-space", "newline", "31-bytes", "dollar", "bad-length", "plus", "slash", "only-pad", "non-ascii"])
def test_invalid_key_values_rejected_without_echo(raw):
    with pytest.raises(auth.AuthConfigError) as e:
        auth.load_failure_key({"APP_ENV": "production", "AUTH_FAILURE_KEY": raw})
    assert "AUTH_FAILURE_KEY" in str(e.value)
    if raw.strip():
        assert raw.strip() not in str(e.value)


def test_public_development_key_rejected_in_production():
    dev = base64.urlsafe_b64encode(auth._DEV_FAILURE_KEY).decode()
    with pytest.raises(auth.AuthConfigError):
        auth.load_failure_key({"APP_ENV": "production", "AUTH_FAILURE_KEY": dev})
    with pytest.raises(auth.AuthConfigError):
        auth.load_failure_key({"AUTH_FAILURE_KEY": dev})
    assert auth.load_failure_key({"APP_ENV": "test", "AUTH_FAILURE_KEY": dev}).username_key("alice")


def test_development_key_is_long_enough_and_public_marked():
    assert len(auth._DEV_FAILURE_KEY) >= auth.FAILURE_KEY_MIN_BYTES and b"PUBLIC" in auth._DEV_FAILURE_KEY


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.delenv("AUTH_FAILURE_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    assert auth.load_failure_key().development_fallback is True


def test_failure_key_never_shows_secret():
    secret = b"S" * 40
    key = auth.FailureKey(secret)
    assert repr(key) == str(key) == "FailureKey(<hidden>)"
    with pytest.raises(TypeError):
        pickle.dumps(key)
    with pytest.raises(auth.AuthConfigError) as e:
        auth.FailureKey(b"short")
    assert "short" not in str(e.value)
    with pytest.raises(auth.AuthConfigError):
        auth.FailureKey("x" * 40)                        # type: ignore[arg-type]


def test_username_key_shape_and_determinism():
    k = KEY.username_key("alice")
    assert len(k) == auth.USERNAME_KEY_LENGTH == 30 and re.fullmatch(r"[A-Za-z0-9_-]{30}", k)
    assert k == KEY.username_key("alice") != KEY.username_key("alice2")
    assert auth.FailureKey(b"z" * 32).username_key("alice") != k
    assert "alice" not in k


def test_login_name_normalizes_before_hmac():
    a, b, c = (auth.login_name(raw, KEY) for raw in ("alice", "  ALICE ", "Alice"))
    assert a == b == c and a.username == "alice" and a.failure_key == KEY.username_key("alice")


@pytest.mark.parametrize("raw", ["ab", "a" * 31, "al ice", "ålice", "alice@example.com", None, 123, b"alice"])
def test_login_name_invalid_format_has_no_key(raw):
    assert auth.login_name(raw, KEY) == auth.LoginName(None, None)


def test_login_name_repr_hides_username():
    assert "alice" not in repr(NAME) and NAME.failure_key not in repr(NAME)


# ────────────────────────── IP 정규화 ──────────────────────────

@pytest.mark.parametrize("host, expected", [
    ("203.0.113.7", "203.0.113.7"),
    ("127.0.0.1", "127.0.0.1"),
    ("::ffff:203.0.113.7", "203.0.113.7"),                          # IPv4-mapped → IPv4
    ("::FFFF:CB00:7107", "203.0.113.7"),
    ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
    ("2001:0DB8:0001:0002::ffff", "2001:db8:1:2::/64"),             # 같은 /64 → 같은 버킷
    ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
    ("::1", "::/64"),
    ("fe80::1%eth0", "fe80::/64"),                                  # zone ID 제거
    ("fe80::abcd%12", "fe80::/64"),
])
def test_normalize_client_ip(host, expected):
    assert auth.normalize_client_ip(host) == expected
    assert len(expected) <= 45                                       # client_ip VARCHAR(45)


@pytest.mark.parametrize("host", [
    None, "", "testclient", "localhost", " 203.0.113.7", "203.0.113.7 ", "203.0.113.07", "203.0.113",
    "256.0.0.1", "[::1]", "203.0.113.7%eth0", "fe80::1%", "::1/128", "203.0.113.7:8080",
    "２０３.0.113.7", "1" * 101, b"203.0.113.7", 3405803783,
])
def test_invalid_client_address(host):
    with pytest.raises(auth.InvalidClientAddress) as e:
        auth.normalize_client_ip(host)
    if isinstance(host, str) and host.strip():
        assert host.strip() not in str(e.value)


def test_invalid_client_address_is_value_error():
    assert issubclass(auth.InvalidClientAddress, ValueError)


def test_auth_does_not_read_proxy_headers():
    src = (APP_DIR / "auth.py").read_text(encoding="utf-8").lower()
    assert "x-real-ip" not in src and "forwarded" not in src


# ────────────────────────── Argon2 슬롯 ──────────────────────────

class SpyHasher:
    # 실제 해시기를 감싸 Argon2 호출이 슬롯 안에서 일어났는지 기록한다
    def __init__(self, real, held, delay: float = 0.0):
        self.real, self.held, self.delay = real, held, delay
        self.calls, self.active, self.max_active = [], 0, 0
        self.lock = threading.Lock()

    def _enter(self, name):
        with self.lock:
            self.calls.append((name, self.held()))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        if self.delay:
            time.sleep(self.delay)

    def _leave(self):
        with self.lock:
            self.active -= 1

    def hash(self, *a, **k):
        self._enter("hash")
        try:
            return self.real.hash(*a, **k)
        finally:
            self._leave()

    def verify(self, *a, **k):
        self._enter("verify")
        try:
            return self.real.verify(*a, **k)
        finally:
            self._leave()

    def check_needs_rehash(self, *a, **k):
        return self.real.check_needs_rehash(*a, **k)


class CountingSlots:
    # BoundedSemaphore 와 같은 인터페이스 + 지금 잡힌 수
    def __init__(self, n: int):
        self.sem, self.n, self.taken, self.lock = threading.BoundedSemaphore(n), n, 0, threading.Lock()
        self.acquired = 0

    def acquire(self, timeout=None):
        ok = self.sem.acquire(timeout=timeout)
        if ok:
            with self.lock:
                self.taken += 1
                self.acquired += 1
        return ok

    def release(self):
        with self.lock:
            self.taken -= 1
        self.sem.release()


@pytest.fixture
def slots(monkeypatch):
    s = CountingSlots(2)
    monkeypatch.setattr(auth, "_ARGON2_SLOTS", s)
    return s


@pytest.fixture
def spy(monkeypatch, slots):
    hasher = SpyHasher(auth._HASHER, held=lambda: slots.taken > 0)
    monkeypatch.setattr(auth, "_HASHER", hasher)
    auth._dummy_hash.cache_clear()                   # 더미 해시 생성(Argon2)도 슬롯 안에서 일어나는지 본다
    yield hasher
    auth._dummy_hash.cache_clear()


def test_slot_defaults():
    assert (auth.ARGON2_MAX_CONCURRENT, auth.ARGON2_SLOT_TIMEOUT) == (2, 5.0)
    assert isinstance(auth._ARGON2_SLOTS, type(threading.BoundedSemaphore(1)))


def test_slot_limits_concurrency_and_times_out(slots):
    with auth.argon2_slot(), auth.argon2_slot():
        with pytest.raises(auth.HashBusy):
            with auth.argon2_slot(timeout=0.01):
                pytest.fail("세 번째 슬롯을 얻으면 안 된다")
        assert slots.taken == 2
    assert slots.taken == 0
    with auth.argon2_slot(timeout=0.01):             # 반환 뒤에는 다시 얻는다
        assert slots.taken == 1


def test_slot_released_after_exception(slots):
    with pytest.raises(RuntimeError):
        with auth.argon2_slot():
            raise RuntimeError("검증 중 오류")
    assert slots.taken == 0
    with auth.argon2_slot(), auth.argon2_slot():     # 두 개 모두 다시 얻을 수 있다 (새는 슬롯 없음)
        assert slots.taken == 2


def test_timeout_does_not_release_unacquired_slot(slots, monkeypatch):
    monkeypatch.setattr(auth, "ARGON2_SLOT_TIMEOUT", 0.01)
    with auth.argon2_slot(), auth.argon2_slot():
        for _ in range(3):
            with pytest.raises(auth.HashBusy):
                with auth.argon2_slot():
                    pass
    assert slots.taken == 0 and slots.acquired == 2  # BoundedSemaphore 초과 반환(ValueError)도 없었다


def test_real_and_dummy_verification_run_inside_the_slot(spy):
    stored = auth.hash_password_limited("correct horse")
    assert auth.verify_login_password(stored, "correct horse") is True
    assert auth.verify_login_password(stored, "wrong password") is False
    assert auth.verify_login_password(None, "whatever pw") is False       # 없는 계정 → 더미 검증
    assert auth.verify_login_password("not-a-hash", "whatever pw") is False
    names = [n for n, _ in spy.calls]
    assert names.count("verify") >= 4 and "hash" in names                 # 깨진 해시는 더미 검증을 더 할 수 있다
    assert all(held for _, held in spy.calls)                             # 모든 Argon2 호출이 슬롯 안


def test_unverifiable_input_skips_slot_and_argon2(spy, slots):
    for password in ("x" * 129, "\ud800" * 8, None, 12345678):
        assert auth.verify_login_password(None, password) is False
        assert auth.verify_login_password("$argon2id$whatever", password) is False
    assert spy.calls == [] and slots.acquired == 0


def test_busy_slot_raises_without_argon2(spy, slots, monkeypatch):
    monkeypatch.setattr(auth, "ARGON2_SLOT_TIMEOUT", 0.01)
    with auth.argon2_slot(), auth.argon2_slot():
        for stored in (None, "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g"):
            with pytest.raises(auth.HashBusy):
                auth.verify_login_password(stored, "password1")
        with pytest.raises(auth.HashBusy):
            auth.hash_password_limited("password1")
    assert spy.calls == []


def test_registration_hash_checks_rules_before_slot(spy, slots):
    with pytest.raises(ValueError):
        auth.hash_password_limited("short")
    assert slots.acquired == 0 and spy.calls == []
    assert auth.hash_password_limited("long enough").startswith("$argon2id$")
    assert slots.acquired == 1 and slots.taken == 0


def test_concurrent_verifications_never_exceed_two(monkeypatch, slots):
    hasher = SpyHasher(auth._HASHER, held=lambda: slots.taken > 0, delay=0.05)
    monkeypatch.setattr(auth, "_HASHER", hasher)
    monkeypatch.setattr(auth, "check_password_hash", lambda stored, pw: (hasher._enter("verify"), hasher._leave(),
                                                                         auth.PasswordCheck.MISMATCH)[2])
    start = threading.Barrier(6)
    errors = []

    def worker():
        start.wait()
        try:
            auth.verify_login_password("$argon2id$x", "password1")
        except Exception as e:                        # noqa: BLE001 — 스레드 오류를 모아 본 테스트에서 실패시킨다
            errors.append(type(e).__name__)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == [] and len(hasher.calls) == 6
    assert hasher.max_active <= 2 and all(held for _, held in hasher.calls)
    assert slots.taken == 0


# ────────────────────────── db.py 함수 (가짜 Session) ──────────────────────────

class Result:
    def __init__(self, scalars=(), scalar=None, row=None, rowcount=0):
        self._scalars, self._scalar, self._row, self.rowcount = list(scalars), scalar, row, rowcount

    def scalars(self):
        return iter(self._scalars)

    def scalar_one(self):
        return self._scalar

    def one_or_none(self):
        return self._row


class FakeDB:
    # 실행한 문장을 (SQL, 바인드) 로 기록한다. commit 등 트랜잭션 조작은 기록해 테스트가 실패하게 한다
    def __init__(self, times=(), user=None, isolation="read committed", rowcount=1, new_id=101, fail_on=None):
        self.times, self.user, self.isolation = [list(t) for t in times], user, isolation
        self.rowcount, self.new_id, self.fail_on = rowcount, new_id, fail_on
        self.log, self.forbidden = [], []

    def execute(self, statement, *args, **kwargs):
        compiled = statement.compile(dialect=DIALECT)
        sql = re.sub(r"::(?:TIMESTAMP WITH TIME ZONE|[A-Z]+)", "", " ".join(str(compiled).split()))   # 타입 캐스트 생략
        params = dict(compiled.params)
        if self.fail_on and self.fail_on in sql:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))
        self.log.append((sql, params))
        if sql.startswith("SET LOCAL") or "pg_advisory_xact_lock" in sql:
            return Result()
        if sql.startswith("SHOW"):
            return Result(scalar=self.isolation)
        if sql.startswith("SELECT auth_failures.created_at"):
            return Result(scalars=self.times.pop(0) if self.times else [])
        if sql.startswith("INSERT INTO auth_failures"):
            return Result(scalar=self.new_id)
        if sql.startswith("SELECT users.id"):
            return Result(row=self.user)
        if sql.startswith("DELETE FROM auth_failures WHERE auth_failures.id IN"):
            return Result(rowcount=0)
        if sql.startswith("DELETE FROM auth_failures"):
            return Result(rowcount=self.rowcount)
        raise AssertionError(f"예상하지 못한 문장: {sql}")

    def kinds(self) -> list[str]:
        out = []
        for sql, _ in self.log:
            if sql.startswith("SET LOCAL"):
                out.append("set")
            elif sql.startswith("SHOW"):
                out.append("show")
            elif "pg_advisory_xact_lock" in sql:
                out.append("lock")
            elif sql.startswith("SELECT auth_failures.created_at"):
                out.append("count")
            elif sql.startswith("DELETE FROM auth_failures WHERE auth_failures.id IN"):
                out.append("purge")
            elif sql.startswith("INSERT"):
                out.append("insert")
            elif sql.startswith("SELECT users"):
                out.append("user")
            else:
                out.append("delete")
        return out

    def find(self, kind: str) -> list[tuple[str, dict]]:
        return [entry for entry, k in zip(self.log, self.kinds()) if k == kind]

    def commit(self):
        self.forbidden.append("commit")

    def rollback(self):
        self.forbidden.append("rollback")

    def close(self):
        self.forbidden.append("close")

    def begin(self):
        self.forbidden.append("begin")


def recent(n: int) -> list[datetime]:
    return [ago(seconds=10 * m) for m in range(1, n + 1)]     # 모두 15분 창 안


def test_begin_login_attempt_order_and_result():
    s = FakeDB(user=(7, "$argon2id$stored"))
    attempt = db.begin_login_attempt(s, NAME, IP, NOW)
    assert s.kinds() == ["set", "show", "lock", "count", "count", "count", "purge", "insert", "user"]
    assert s.log[0][0] == "SET LOCAL lock_timeout = '2s'"
    assert attempt == db.LoginAttempt(auth.LimitDecision(True, 0), 101, 7, "$argon2id$stored")
    assert "stored" not in repr(attempt)
    assert s.forbidden == []


def test_lock_uses_bound_hmac_key_not_plaintext():
    s = FakeDB()
    db.begin_login_attempt(s, NAME, IP, NOW)
    (lock_sql, lock_params), = s.find("lock")
    assert lock_sql == "SELECT pg_advisory_xact_lock(hashtextextended(%(lock_prefix)s || %(username_key)s, 0))"
    assert lock_params == {"lock_prefix": "login:", "username_key": KEY.username_key("alice")}
    for sql, params in s.log:                         # 평문 username 은 users 조회에만 간다
        if not sql.startswith("SELECT users"):
            assert "alice" not in sql and "alice" not in {str(v) for v in params.values()}
    (user_sql, user_params), = s.find("user")
    assert list(user_params.values()) == ["alice"]


def test_same_key_for_lock_counts_and_insert():
    s = FakeDB()
    db.begin_login_attempt(s, NAME, IP, NOW)
    keys = {v for _, p in s.log for k, v in p.items() if k.startswith("username_key")}
    assert keys == {NAME.failure_key}


def test_login_count_queries():
    s = FakeDB()
    db.begin_login_attempt(s, NAME, IP, NOW)
    counts = s.find("count")
    limits = [p["param_1"] for _, p in counts]
    assert limits == [5, 50, 20]
    for sql, params in counts:
        assert "auth_failures.kind = %(kind_1)s" in sql and params["kind_1"] == "login"
        assert "auth_failures.created_at > %(created_at_1)s" in sql
        assert params["created_at_1"] == NOW - timedelta(minutes=15)
        assert "ORDER BY auth_failures.created_at DESC LIMIT" in sql
    assert "client_ip" in counts[0][0] and "username_key" in counts[0][0]   # (username, IP)
    assert "client_ip" not in counts[1][0] and "username_key" in counts[1][0]   # username
    assert "client_ip" in counts[2][0] and "username_key" not in counts[2][0]   # IP
    assert counts[0][1]["client_ip_1"] == IP and counts[2][1]["client_ip_1"] == IP


def test_insert_values():
    s = FakeDB()
    db.begin_login_attempt(s, NAME, IP, NOW)
    (sql, params), = s.find("insert")
    assert sql.endswith("RETURNING auth_failures.id")
    assert params == {"kind": "login", "username_key": NAME.failure_key, "client_ip": IP, "created_at": NOW}


@pytest.mark.parametrize("rule_index, limit", [(0, 5), (1, 50), (2, 20)])
def test_blocked_attempt_writes_nothing(rule_index, limit):
    times = [[], [], []]
    times[rule_index] = recent(limit)
    s = FakeDB(times=times)
    attempt = db.begin_login_attempt(s, NAME, IP, NOW)
    assert attempt.decision.allowed is False and attempt.decision.retry_after > 0
    assert (attempt.attempt_id, attempt.user_id, attempt.password_hash) == (None, None, None)
    assert s.kinds() == ["set", "show", "lock", "count", "count", "count"]   # 기록·정리·사용자 조회 없음


def test_one_below_limit_is_recorded():
    s = FakeDB(times=[recent(4), recent(4), recent(4)])
    assert db.begin_login_attempt(s, NAME, IP, NOW).attempt_id == 101
    assert "insert" in s.kinds()


def test_malformed_username_uses_ip_rule_only_without_lock():
    s = FakeDB()
    attempt = db.begin_login_attempt(s, auth.LoginName(None, None), IP, NOW)
    assert s.kinds() == ["set", "show", "count", "purge", "insert"]
    assert s.find("insert")[0][1]["username_key"] is None
    assert attempt.user_id is None and attempt.password_hash is None and attempt.attempt_id == 101


@pytest.mark.parametrize("name", [auth.LoginName("alice", None), auth.LoginName(None, "x" * 30), "alice", None])
def test_inconsistent_login_name_rejected_before_sql(name):
    s = FakeDB()
    with pytest.raises(ValueError):
        db.begin_login_attempt(s, name, IP, NOW)
    assert s.log == []


def test_naive_now_rejected_before_sql():
    s = FakeDB()
    with pytest.raises(ValueError):
        db.begin_login_attempt(s, NAME, IP, NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        db.begin_register_attempt(s, IP, NOW.replace(tzinfo=None))
    assert s.log == []


@pytest.mark.parametrize("level", ["repeatable read", "serializable", None])
def test_requires_read_committed(level):
    s = FakeDB(isolation=level)
    with pytest.raises(db.DatabaseConfigError):
        db.begin_login_attempt(s, NAME, IP, NOW)
    assert s.kinds() == ["set", "show"]               # 잠금·검사 전에 멈춘다


@pytest.mark.parametrize("fail_on", ["pg_advisory_xact_lock", "SELECT auth_failures.created_at", "INSERT INTO"])
def test_database_errors_propagate(fail_on):
    s = FakeDB(fail_on=fail_on)
    with pytest.raises(OperationalError):
        db.begin_login_attempt(s, NAME, IP, NOW)
    assert s.forbidden == []
    assert not any(sql.startswith("SELECT users") for sql, _ in s.log)


@pytest.mark.parametrize("func", [db.finish_login_success, db.cancel_login_attempt])
def test_success_and_cancel_delete_only_own_attempt(func):
    s = FakeDB(rowcount=1)
    assert func(s, NAME, 101) is True
    assert s.kinds() == ["set", "show", "lock", "delete"]
    (sql, params), = s.find("delete")
    assert sql == ("DELETE FROM auth_failures WHERE auth_failures.id = %(id_1)s "
                   "AND auth_failures.kind = %(kind_1)s AND auth_failures.username_key = %(username_key_1)s")
    assert params == {"id_1": 101, "kind_1": "login", "username_key_1": NAME.failure_key}
    assert s.find("lock")[0][1]["username_key"] == NAME.failure_key
    assert func(FakeDB(rowcount=0), NAME, 101) is False


def test_cancel_for_malformed_username_without_lock():
    s = FakeDB()
    assert db.cancel_login_attempt(s, auth.LoginName(None, None), 55) is True
    assert s.kinds() == ["set", "show", "delete"]
    assert s.log[-1][0].endswith("AND auth_failures.username_key IS NULL") and s.log[-1][1]["id_1"] == 55


def test_success_requires_valid_username():
    s = FakeDB()
    with pytest.raises(ValueError):
        db.finish_login_success(s, auth.LoginName(None, None), 1)
    assert s.log == []


@pytest.mark.parametrize("attempt_id", [None, "101", True, 1.0])
def test_attempt_id_must_be_int(attempt_id):
    s = FakeDB()
    with pytest.raises(ValueError):
        db.finish_login_success(s, NAME, attempt_id)
    with pytest.raises(ValueError):
        db.cancel_login_attempt(s, NAME, attempt_id)
    assert s.log == []


@pytest.mark.parametrize("bad", ["", "x" * 29, "x" * 31, None, "alice"])
def test_lock_requires_hmac_key(bad):
    s = FakeDB()
    with pytest.raises(ValueError):
        db.lock_login_username(s, bad)
    assert s.log == []


def test_register_attempt_counts_ip_without_lock():
    s = FakeDB()
    attempt = db.begin_register_attempt(s, IP, NOW)
    assert attempt == db.RegisterAttempt(auth.LimitDecision(True, 0), 101)
    assert s.kinds() == ["set", "show", "count", "purge", "insert"]
    (sql, params), = s.find("count")
    assert params["kind_1"] == "register" and params["param_1"] == 10 and params["client_ip_1"] == IP
    assert params["created_at_1"] == NOW - timedelta(hours=1)
    assert s.find("insert")[0][1] == {"kind": "register", "username_key": None, "client_ip": IP, "created_at": NOW}


def test_register_blocked_writes_nothing():
    s = FakeDB(times=[recent(10)])
    attempt = db.begin_register_attempt(s, IP, NOW)
    assert attempt.attempt_id is None and attempt.decision == auth.LimitDecision(False, 3600 - 100)
    assert s.kinds() == ["set", "show", "count"]


def test_purge_old_records_sql():
    s = FakeDB()
    db.purge_old_auth_failures(s, NOW)
    (sql, params), = s.log
    assert sql.startswith("DELETE FROM auth_failures WHERE auth_failures.id IN (SELECT auth_failures.id")
    assert "auth_failures.created_at <= %(created_at_1)s" in sql and sql.endswith("FOR UPDATE SKIP LOCKED)")
    assert params["created_at_1"] == NOW - timedelta(hours=24) and params["param_1"] == 1000


# ── 소스 수준 검사: commit 없음, 로그·f-string 없음 ──

LIMIT_FUNCS = ("_begin_auth_transaction", "lock_login_username", "_newest_failure_times", "_login_rules",
               "_check_login_name", "_record_attempt", "begin_login_attempt", "_delete_own_login_attempt",
               "finish_login_success", "cancel_login_attempt", "begin_register_attempt", "purge_old_auth_failures")


def limit_nodes():
    tree = ast.parse((APP_DIR / "db.py").read_text(encoding="utf-8"))
    nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in LIMIT_FUNCS}
    assert set(nodes) == set(LIMIT_FUNCS)
    return nodes.values()


def test_limit_functions_do_not_commit_or_open_connections():
    for node in limit_nodes():
        calls = {ast.unparse(n.func).split(".")[-1] for n in ast.walk(node) if isinstance(n, ast.Call)}
        assert not calls & {"commit", "rollback", "close", "begin", "connect", "create_engine", "get_engine",
                            "get_sessionmaker", "begin_nested"}, node.name


def test_limit_functions_do_not_log_or_format_values():
    for node in limit_nodes():
        calls = {ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
        assert not any(c.startswith(("logging.", "logger.", "log.")) or c == "print" for c in calls), node.name
        assert not [n for n in ast.walk(node) if isinstance(n, ast.JoinedStr)], node.name


def test_no_log_records_during_attempts(caplog):
    with caplog.at_level(logging.DEBUG):
        db.begin_login_attempt(FakeDB(user=(1, "h")), NAME, IP, NOW)
        db.finish_login_success(FakeDB(), NAME, 101)
        db.begin_register_attempt(FakeDB(), IP, NOW)
    assert not [r for r in caplog.records if r.name.startswith(("db", "auth"))]


def test_alembic_0002_unchanged_columns():
    # 2-D 는 스키마를 바꾸지 않는다 — HMAC 30자·IPv6 /64 가 기존 열 길이에 들어간다
    t = db.AuthFailure.__table__
    assert (t.c.username_key.type.length, t.c.client_ip.type.length) == (30, 45)
    assert len(KEY.username_key("a" * 30)) == 30
    assert len(auth.normalize_client_ip("ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff")) <= 45
