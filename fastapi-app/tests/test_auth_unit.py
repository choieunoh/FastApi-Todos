"""(7.0.0 Phase 2-B) auth.py 순수 로직 단위 테스트 — DB·서버 없이 돈다."""
import ast
import json
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from starlette.responses import Response

import auth

APP_DIR = Path(__file__).resolve().parent.parent
AUTH_SRC = APP_DIR / "auth.py"
T0 = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


# ── username ──

@pytest.mark.parametrize("raw, expected", [
    ("  Alice  ", "alice"),
    ("abc", "abc"),
    ("a" * 30, "a" * 30),
    ("A.b-C_d", "a.b-c_d"),
    ("\tUser01\n", "user01"),
    ("...", "..."),                              # 명세 규칙상 허용 (추가 규칙 없음)
])
def test_username_normalized(raw, expected):
    assert auth.normalize_username(raw) == expected


@pytest.mark.parametrize("raw", [
    "ab", "a" * 31, "  ab  ", "", "   ",
    "ali ce",                                    # 중간 공백
    "철수abc", "ａｂｃ",                            # 한글, 전각
    "Kelvin",                               # 켈빈 기호 K — lower() 하면 ASCII k 가 되지만 거부
    "user@x", "a/b", "a+b", "ab\x00c",
])
def test_username_rejected(raw):
    with pytest.raises(ValueError) as e:
        auth.normalize_username(raw)
    if raw.strip():
        assert raw.strip() not in str(e.value)   # 입력값을 메시지에 넣지 않는다


def test_username_kelvin_would_pass_if_lowered_first():
    # 검사 순서가 중요한 이유: 소문자로 먼저 바꾸면 통과해 버린다
    assert "Kelvin".lower() == "kelvin"


def test_username_non_string_rejected():
    with pytest.raises(ValueError):
        auth.normalize_username(123)             # type: ignore[arg-type]


# ── 비밀번호 ──

@pytest.mark.parametrize("raw", ["a" * 8, "a" * 128, "  pass  word  ", "비밀번호는여덟자", "🔑" * 8])
def test_password_accepted_unchanged(raw):
    assert auth.check_password(raw) == raw       # 공백·문자 그대로


@pytest.mark.parametrize("raw", ["a" * 7, "a" * 129, "", "       ", "\ud800abcdefgh"])
def test_password_rejected(raw):
    with pytest.raises(ValueError) as e:
        auth.check_password(raw)
    if raw.strip():
        assert raw not in str(e.value)


def test_password_length_counts_code_points():
    assert auth.check_password("🔑" * 128)       # 128자 = 512바이트도 허용 (문자 수 기준)
    with pytest.raises(ValueError):
        auth.check_password("가" * 129)


def test_password_not_unicode_normalized():
    nfc = "café-pass"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    stored = auth.hash_password(nfc)
    assert auth.verify_password(stored, nfc)
    assert not auth.verify_password(stored, nfd)   # 정규화하지 않으므로 다른 비밀번호


# ── Argon2id ──

def test_hash_is_argon2id_and_salted():
    a, b = auth.hash_password("correct horse"), auth.hash_password("correct horse")
    assert a.startswith("$argon2id$") and b.startswith("$argon2id$")
    assert a != b                                # 솔트가 다르다
    assert "correct horse" not in a


def test_default_hasher_parameters():
    # V7_SPEC §29.1 — PasswordHasher() 기본값 (RFC 9106 low-memory)
    assert (auth._HASHER.time_cost, auth._HASHER.memory_cost, auth._HASHER.parallelism) == (3, 65536, 4)


def test_verify_ok_and_mismatch():
    stored = auth.hash_password("correct horse")
    assert auth.check_password_hash(stored, "correct horse") is auth.PasswordCheck.OK
    assert auth.check_password_hash(stored, "Correct horse") is auth.PasswordCheck.MISMATCH
    assert auth.verify_password(stored, "correct horse")
    assert not auth.verify_password(stored, "wrong horse!")


def test_hash_password_validates_length():
    with pytest.raises(ValueError):
        auth.hash_password("short")


@pytest.mark.parametrize("stored", ["", "garbage", "$argon2id$v=19$broken", "$2b$12$" + "a" * 53])
def test_invalid_stored_hash(stored):
    assert auth.check_password_hash(stored, "whatever1") is auth.PasswordCheck.INVALID_HASH
    assert auth.verify_password(stored, "whatever1") is False
    with pytest.raises(ValueError) as e:
        auth.needs_rehash(stored)
    if stored:
        assert stored not in str(e.value)        # 저장 해시 내용을 드러내지 않는다


def test_truncated_real_hash_is_invalid_not_mismatch():
    stored = auth.hash_password("correct horse")
    for broken in (stored[:-10], stored[:30]):
        assert auth.check_password_hash(broken, "correct horse") is auth.PasswordCheck.INVALID_HASH


def test_well_formed_but_altered_hash_is_mismatch():
    # 형식이 온전하면 값만 바뀐 해시는 불일치와 구분할 수 없다 — 로그인 실패(False)로 끝나는 것만 보장한다
    stored = auth.hash_password("correct horse")
    altered = stored.replace("$v=19$", "$v=99$")
    assert auth.check_password_hash(altered, "correct horse") is auth.PasswordCheck.MISMATCH
    assert auth.verify_password(altered, "correct horse") is False


def test_invalid_hash_spends_dummy_verification(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "verify_dummy", lambda pw: calls.append(1))
    auth.check_password_hash("garbage", "whatever1")
    assert calls == [1]


def test_verify_with_unencodable_password_is_mismatch():
    stored = auth.hash_password("correct horse")
    assert auth.check_password_hash(stored, "\ud800abcdefgh") is auth.PasswordCheck.MISMATCH


def test_needs_rehash():
    assert auth.needs_rehash(auth.hash_password("correct horse")) is False
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("correct horse")
    assert auth.verify_password(weak, "correct horse")   # 예전 파라미터도 검증은 된다
    assert auth.needs_rehash(weak) is True


class CountingHasher:
    # PasswordHasher 는 __slots__ 라 메서드를 바꿀 수 없어 감싸서 센다
    def __init__(self, inner: PasswordHasher):
        self.inner, self.hash_calls, self.verify_calls = inner, 0, 0

    def hash(self, password):
        self.hash_calls += 1
        return self.inner.hash(password)

    def verify(self, stored, password):
        self.verify_calls += 1
        return self.inner.verify(stored, password)

    def check_needs_rehash(self, stored):
        return self.inner.check_needs_rehash(stored)


# R-24: 검증 경로 입력 제한 — 128자 초과·인코딩 불가 입력은 Argon2 에 넘기지 않는다

@pytest.fixture
def counting(monkeypatch):
    hasher = CountingHasher(auth._HASHER)
    monkeypatch.setattr(auth, "_HASHER", hasher)
    auth._dummy_hash.cache_clear()
    yield hasher
    auth._dummy_hash.cache_clear()


@pytest.mark.parametrize("password", ["a" * 128, "가" * 128, "🔑" * 128, " " * 64 + "b" * 64])
def test_max_length_password_verifies(counting, password):
    stored = auth.hash_password(password)        # 128자 = 최대 512바이트(이모지)도 허용
    assert len(password.encode("utf-8")) <= auth.PASSWORD_MAX_BYTES
    assert auth.check_password_hash(stored, password) is auth.PasswordCheck.OK
    assert auth.verify_password(stored, password) is True
    assert counting.verify_calls == 2


UNVERIFIABLE = {                                 # 테스트 id 는 짧은 이름으로 (긴 값이 id 에 들어가지 않게)
    "ascii-129": lambda: "a" * 129,
    "hangul-129": lambda: "가" * 129,
    "emoji-129": lambda: "🔑" * 129,
    "100k": lambda: "a" * 100_000,
    "5m": lambda: "a" * 5_000_000,
    "surrogate": lambda: "\ud800" + "a" * 10,    # UTF-8 로 못 바꾸는 값
    "none": lambda: None,                        # 문자열이 아님
    "bytes": lambda: b"bytes-password",
    "int": lambda: 12345678,
}


@pytest.mark.parametrize("make", list(UNVERIFIABLE.values()), ids=list(UNVERIFIABLE))
def test_unverifiable_password_skips_argon2(counting, make):
    password = make()
    stored = auth.hash_password("a" * 128)
    counting.verify_calls = counting.hash_calls = 0
    assert auth.check_password_hash(stored, password) is auth.PasswordCheck.MISMATCH
    assert auth.verify_password(stored, password) is False
    assert auth.verify_dummy(password) is None
    assert counting.verify_calls == 0            # Argon2 검증에 전달되지 않는다
    assert counting.hash_calls == 0              # 더미 해시도 만들지 않는다


def test_prefix_of_long_password_is_not_accepted():
    # 129자 입력을 128자로 잘라서 검증하지 않는다 (앞 128자가 맞아도 실패)
    stored = auth.hash_password("a" * 128)
    assert auth.verify_password(stored, "a" * 129) is False


def test_unverifiable_input_on_broken_hash_is_fast_mismatch(counting):
    # 검증할 수 없는 입력은 해시 상태와 관계없이 같은 빠른 실패 (계정 유무·해시 상태가 드러나지 않는다)
    assert auth.check_password_hash("garbage", "a" * 129) is auth.PasswordCheck.MISMATCH
    assert counting.verify_calls == 0


def test_short_password_still_verified_as_mismatch(counting):
    stored = auth.hash_password("correct horse")
    assert auth.check_password_hash(stored, "short") is auth.PasswordCheck.MISMATCH   # 최소 길이는 검증 경로에서 보지 않는다
    assert counting.verify_calls == 1


def test_classification_kept_for_normal_input():
    stored = auth.hash_password("correct horse")
    assert auth.check_password_hash(stored, "correct horse") is auth.PasswordCheck.OK
    assert auth.check_password_hash(stored, "wrong horse!") is auth.PasswordCheck.MISMATCH
    assert auth.check_password_hash(stored[:-10], "correct horse") is auth.PasswordCheck.INVALID_HASH
    assert auth.check_password_hash("garbage", "correct horse") is auth.PasswordCheck.INVALID_HASH


def test_long_password_rejection_is_fast():
    import time
    stored = auth.hash_password("correct horse")
    huge = "a" * 5_000_000
    start = time.perf_counter()
    for _ in range(20):
        auth.check_password_hash(stored, huge)
        auth.verify_dummy(huge)
    assert time.perf_counter() - start < 0.5     # 해시하면 1회에 약 0.2초 — 40회를 0.5초 안에 끝낸다


def test_dummy_hash_created_once_and_reused(monkeypatch):
    hasher = CountingHasher(auth._HASHER)
    monkeypatch.setattr(auth, "_HASHER", hasher)
    auth._dummy_hash.cache_clear()
    try:
        for pw in ("first-try", "second-try", "\ud800bad", "x"):
            assert auth.verify_dummy(pw) is None # 어떤 입력에도 예외 없이 끝난다
        assert hasher.hash_calls == 1            # 더미 해시는 한 번만 만든다
        assert hasher.verify_calls == 3          # 검증할 수 있는 입력은 매번 실제 검증 비용을 쓴다 (인코딩 불가 입력은 R-24로 건너뜀)
        assert auth._dummy_hash().startswith("$argon2id$")
    finally:
        auth._dummy_hash.cache_clear()           # 바꾼 hasher 로 만든 값을 남기지 않는다


# ── 토큰·CSRF ──

URLSAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@pytest.mark.parametrize("make", [auth.new_session_token, auth.new_csrf_token])
def test_tokens_format_and_randomness(make):
    tokens = {make() for _ in range(200)}
    assert len(tokens) == 200
    for t in tokens:
        assert len(t) == 43 and set(t) <= URLSAFE


def test_session_and_csrf_tokens_differ():
    assert auth.new_session_token() != auth.new_csrf_token()


def test_hash_token_sha256():
    import hashlib
    token = auth.new_session_token()
    digest = auth.hash_token(token)
    assert isinstance(digest, bytes) and len(digest) == 32
    assert digest == hashlib.sha256(token.encode()).digest()
    assert auth.hash_token(token) == digest and auth.hash_token(token + "x") != digest
    assert len(auth.hash_token("쿠키\ud800")) == 32   # 이상한 쿠키 값에도 예외 없음


@pytest.mark.parametrize("expected, provided, result", [
    ("abc123", "abc123", True),
    ("abc123", "abc124", False),
    ("abc123", "abc12", False),
    ("abc123", None, False),
    ("abc123", "", False),
    (None, "abc123", False),
    ("", "", False),
    ("abc123", "토큰", False),                    # 비ASCII — str compare_digest 라면 TypeError
    ("abc123", "\ud800", False),
    ("토큰", "토큰", True),
])
def test_csrf_matches(expected, provided, result):
    assert auth.csrf_matches(expected, provided) is result


def test_csrf_uses_constant_time_compare():
    tree = ast.parse(AUTH_SRC.read_text(encoding="utf-8"))
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "csrf_matches")
    calls = {ast.unparse(n.func) for n in ast.walk(func) if isinstance(n, ast.Call)}
    assert "hmac.compare_digest" in calls
    assert not any(isinstance(n, ast.Compare) and any(isinstance(op, ast.Eq) for op in n.ops)
                   for n in ast.walk(func))       # == 비교를 쓰지 않는다


# ── 14일 만료 ──

def test_session_expiry_14_days():
    expires = auth.session_expires_at(T0)
    assert expires == T0 + timedelta(days=14)
    assert auth.SESSION_MAX_AGE == 1209600
    assert not auth.is_expired(expires, expires - timedelta(microseconds=1))
    assert auth.is_expired(expires, expires)                       # 같으면 만료
    assert auth.is_expired(expires, expires + timedelta(seconds=1))


def test_expiry_works_across_offsets():
    kst = timezone(timedelta(hours=9))
    expires = auth.session_expires_at(T0)
    assert auth.is_expired(expires, expires.astimezone(kst))


@pytest.mark.parametrize("call", [
    lambda: auth.session_expires_at(datetime(2026, 11, 5, 9, 0)),
    lambda: auth.is_expired(datetime(2026, 11, 19), T0),
    lambda: auth.is_expired(T0, datetime(2026, 11, 19)),
])
def test_naive_datetime_rejected(call):
    with pytest.raises(ValueError):
        call()


# ── Origin·Referer ──

HOST = auth.WebOrigin("http", "testserver", 80)
LOCAL = auth.WebOrigin("http", "127.0.0.1", 5001)
SECURE = auth.WebOrigin("https", "todo.example.com", 443)


@pytest.mark.parametrize("scheme, host, expected", [
    ("http", "testserver", HOST),
    ("http", "testserver:80", HOST),             # 기본 포트 정규화
    ("http", "127.0.0.1:5001", LOCAL),
    ("https", "TODO.Example.com", SECURE),       # 대소문자
    ("https", "todo.example.com:443", SECURE),
    ("HTTP", "[::1]:8000", auth.WebOrigin("http", "::1", 8000)),
])
def test_request_origin(scheme, host, expected):
    assert auth.request_origin(scheme, host) == expected


@pytest.mark.parametrize("scheme, host", [
    ("http", None), ("http", ""), ("ftp", "testserver"), ("http", "user@testserver"),
    ("http", "testserver:0"), ("http", "testserver:65536"), ("http", "testserver:+80"),
    ("http", "test server"), ("http", "bücher.de"), ("http", "[::1"), ("http", "[nothex]"),
    ("http", "-bad.example"), ("http", "example.com."), ("http", "a/b"),
])
def test_request_origin_invalid(scheme, host):
    assert auth.request_origin(scheme, host) is None


@pytest.mark.parametrize("origin, referer, expected, allowed", [
    ("http://testserver", None, HOST, True),
    ("http://testserver:80", None, HOST, True),
    ("HTTP://TestServer", None, HOST, True),
    (" http://testserver ", None, HOST, True),
    ("http://127.0.0.1:5001", None, LOCAL, True),
    ("https://todo.example.com", None, SECURE, True),
    ("http://evil.example", None, HOST, False),             # 다른 호스트
    ("http://testserver:8080", None, HOST, False),          # 다른 포트
    ("https://testserver", None, HOST, False),              # 다른 스킴 (포트도 443)
    ("https://testserver:80", None, HOST, False),           # 포트는 같아도 스킴이 다르다
    ("http://testserver.evil.example", None, HOST, False),  # 접두사가 같아도 거부
    ("http://evil.example/http://testserver", None, HOST, False),
    ("http://testserver@evil.example", None, HOST, False),  # 사용자 정보
    ("null", None, HOST, False),
    ("", None, HOST, False),                                # 빈 Origin 은 '있음'으로 보고 거부
    ("http://testserver/path", None, HOST, False),          # Origin 에는 경로가 없다
    ("http://testserver?x=1", None, HOST, False),
    ("http://[::1", None, HOST, False),                     # 파싱 실패
    ("javascript:alert(1)", None, HOST, False),
    ("http://testserver", None, None, False),               # 기대값(요청 Origin)을 모르면 거부
    ("http://evil.example", "http://testserver/page", HOST, False),   # Origin 우선
    (None, "http://testserver/some/page?q=1#f", HOST, True),
    (None, "http://testserver:80/", HOST, True),
    (None, "http://evil.example/http://testserver", HOST, False),
    (None, "http://testserver.evil.example/", HOST, False),
    (None, "not a url", HOST, False),
    (None, "", HOST, False),
    (None, None, HOST, True),                               # 둘 다 없으면 허용 (§29.3)
    (None, None, None, True),
])
def test_origin_allowed(origin, referer, expected, allowed):
    assert auth.origin_allowed(origin, referer, expected) is allowed


# R-25: urlsplit 이 지워 버리는 제어 문자·비ASCII 를 파싱 전에 거부한다 (문자열은 실제 제어 문자를 담는다)
TAB, LF, CR, NUL, VT, DEL = chr(9), chr(10), chr(13), chr(0), chr(11), chr(127)
CONTROL_INPUTS = [
    "http://test" + TAB + "server",
    "http://testser" + LF + "ver",
    NUL + "http://testserver",
    "http://testserver" + CR,
    "http://testserver" + LF,
    "http://testserver" + CR + LF,
    TAB + "http://testserver",                   # 앞의 탭 (strip 으로 지우지 않는다)
    chr(1) + "http://testserver",
    chr(31) + "http://testserver",
    "http://testserver" + VT,
    "http://testserver" + DEL,
    "http://tëstserver",                         # 비ASCII
    "http://testserver" + chr(0xa0),             # NBSP
    "http://testserver" + chr(0x3000),           # 전각 공백
]


@pytest.mark.parametrize("value", CONTROL_INPUTS)
def test_origin_with_control_or_non_ascii_rejected(value):
    assert any(not 0x21 <= ord(c) <= 0x7e for c in value)   # 실제로 거부 대상 문자가 들어 있다
    assert auth.parse_origin_header(value) is None
    assert auth.origin_allowed(value, None, HOST) is False
    assert auth.origin_allowed(value, "http://testserver/", HOST) is False   # Origin 이 있으면 Referer 로 넘어가지 않는다


@pytest.mark.parametrize("value", CONTROL_INPUTS + [v + "/page" for v in CONTROL_INPUTS[:3]])
def test_referer_with_control_or_non_ascii_rejected(value):
    assert auth.parse_referer_header(value) is None
    assert auth.origin_allowed(None, value, HOST) is False


@pytest.mark.parametrize("origin, referer", [
    ("http://testserver", None),
    ("  http://testserver  ", None),             # 앞뒤 공백(0x20)만은 떼고 허용 (기존 동작)
    ("HTTP://TESTSERVER:80", None),
    (None, "http://testserver/a/b?c=d&e=%20f#g"),
    (None, " http://testserver/ "),
])
def test_valid_origin_and_referer_still_allowed(origin, referer):
    assert auth.origin_allowed(origin, referer, HOST) is True


def test_parse_headers_reject_non_string():
    assert auth.parse_origin_header(None) is None          # type: ignore[arg-type]
    assert auth.parse_referer_header(b"http://testserver") is None   # type: ignore[arg-type]


def test_origin_not_compared_by_string_prefix():
    # Origin 판정 함수는 문자열 접두사·부분 문자열 비교를 쓰지 않고 정규화한 WebOrigin 끼리 == 로만 비교한다
    tree = ast.parse(AUTH_SRC.read_text(encoding="utf-8"))
    names = {"origin_allowed", "parse_origin_header", "parse_referer_header", "request_origin"}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(funcs) == len(names)
    for func in funcs:
        methods = {n.func.attr for n in ast.walk(func)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not methods & {"startswith", "endswith", "find", "index", "rfind", "count"}, func.name
        assert not any(isinstance(op, (ast.In, ast.NotIn))
                       for n in ast.walk(func) if isinstance(n, ast.Compare) for op in n.ops), func.name
    src = AUTH_SRC.read_text(encoding="utf-8").lower()
    assert "x-forwarded" not in src and "forwarded" not in src   # 프록시 헤더 신뢰는 2-E 에서 정한다


# ── 설정 ──

def test_settings_defaults_development():
    s = auth.load_settings({})
    assert (s.app_env, s.cookie_secure, s.registration_enabled, s.cookie_name) == \
        ("development", False, True, "todo_session")


def test_settings_test_env():
    s = auth.load_settings({"APP_ENV": "test"})
    assert (s.app_env, s.registration_enabled) == ("test", True)


def test_settings_production_requires_secure_cookie():
    with pytest.raises(auth.AuthConfigError):
        auth.load_settings({"APP_ENV": "production"})
    with pytest.raises(auth.AuthConfigError):
        auth.load_settings({"APP_ENV": "production", "COOKIE_SECURE": "false"})
    s = auth.load_settings({"APP_ENV": "production", "COOKIE_SECURE": "true"})
    assert (s.cookie_secure, s.registration_enabled, s.cookie_name) == (True, False, "__Host-todo_session")


def test_settings_registration_explicit_overrides_default():
    assert auth.load_settings({"APP_ENV": "production", "COOKIE_SECURE": "true",
                               "REGISTRATION_ENABLED": "true"}).registration_enabled is True
    assert auth.load_settings({"REGISTRATION_ENABLED": "false"}).registration_enabled is False


def test_settings_bool_case_and_whitespace():
    s = auth.load_settings({"APP_ENV": " Production ", "COOKIE_SECURE": "TRUE"})
    assert s.app_env == "production" and s.cookie_secure


def test_secure_cookie_allowed_in_development():
    assert auth.load_settings({"COOKIE_SECURE": "true"}).cookie_name == "__Host-todo_session"


@pytest.mark.parametrize("environ, name", [
    ({"APP_ENV": "staging"}, "APP_ENV"),
    ({"APP_ENV": ""}, "APP_ENV"),                # 빈 값은 미설정이 아니라 오류 (compose 빈 치환 방지)
    ({"COOKIE_SECURE": "yes-secret-value"}, "COOKIE_SECURE"),
    ({"COOKIE_SECURE": ""}, "COOKIE_SECURE"),
    ({"COOKIE_SECURE": "1"}, "COOKIE_SECURE"),
    ({"REGISTRATION_ENABLED": "on"}, "REGISTRATION_ENABLED"),
])
def test_settings_invalid_values(environ, name):
    with pytest.raises(auth.AuthConfigError) as e:
        auth.load_settings(environ)
    assert name in str(e.value)
    for value in environ.values():
        if value.strip():
            assert value not in str(e.value)     # 값은 메시지에 넣지 않는다


def test_settings_reads_os_environ_by_default(monkeypatch):
    for name in ("APP_ENV", "COOKIE_SECURE", "REGISTRATION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    assert auth.load_settings().app_env == "test"


# ── 쿠키 ──

def set_cookie_header(**kwargs) -> str:
    response = Response()
    response.set_cookie(value="TOKEN", **kwargs)
    return response.headers["set-cookie"]


def delete_cookie_header(**kwargs) -> str:
    response = Response()
    response.delete_cookie(**kwargs)
    return response.headers["set-cookie"]


def cookie_attrs(header: str) -> tuple[str, dict[str, str]]:
    first, *rest = [p.strip() for p in header.split(";")]
    attrs = {}
    for part in rest:
        key, _, value = part.partition("=")
        attrs[key.lower()] = value
    return first, attrs


def test_dev_cookie_attributes():
    settings = auth.load_settings({})
    assert auth.cookie_kwargs(settings) == {"key": "todo_session", "max_age": 1209600, "path": "/",
                                            "secure": False, "httponly": True, "samesite": "lax"}
    first, attrs = cookie_attrs(set_cookie_header(**auth.cookie_kwargs(settings)))
    assert first == "todo_session=TOKEN"
    assert attrs["max-age"] == "1209600" and attrs["path"] == "/" and attrs["samesite"] == "lax"
    assert "httponly" in attrs and "secure" not in attrs and "domain" not in attrs


def test_secure_cookie_meets_host_prefix_rules():
    settings = auth.load_settings({"APP_ENV": "production", "COOKIE_SECURE": "true"})
    first, attrs = cookie_attrs(set_cookie_header(**auth.cookie_kwargs(settings)))
    assert first == "__Host-todo_session=TOKEN"
    # __Host- 규칙: Secure, Path=/, Domain 없음
    assert "secure" in attrs and attrs["path"] == "/" and "domain" not in attrs
    assert "httponly" in attrs and attrs["samesite"] == "lax" and attrs["max-age"] == "1209600"


@pytest.mark.parametrize("environ", [{}, {"APP_ENV": "production", "COOKIE_SECURE": "true"}])
def test_delete_cookie_matches_set_cookie(environ):
    settings = auth.load_settings(environ)
    made = auth.cookie_kwargs(settings)
    deleted = auth.cookie_delete_kwargs(settings)
    assert {k: made[k] for k in deleted} == deleted          # 이름·Path·Secure·HttpOnly·SameSite 같음
    first, attrs = cookie_attrs(delete_cookie_header(**deleted))
    assert first.startswith(f"{settings.cookie_name}=")
    assert attrs["max-age"] == "0" and attrs["path"] == "/" and "domain" not in attrs
    assert ("secure" in attrs) is settings.cookie_secure


# ── 의존성 경계와 import 부작용 ──

def test_auth_imports_only_stdlib_and_argon2():
    tree = ast.parse(AUTH_SRC.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names) | {"argon2"}
    assert not roots & {"main", "db", "sqlalchemy", "fastapi", "starlette", "psycopg"}


def test_import_has_no_side_effects():
    # 새 프로세스에서 import 만 했을 때: 해시 계산 없음, main·db·SQLAlchemy·FastAPI 미로드
    code = (
        "import json, sys, auth\n"
        "print(json.dumps({'dummy': auth._dummy_hash.cache_info().currsize,"
        " 'mods': sorted(m for m in ('main', 'db', 'sqlalchemy', 'fastapi', 'starlette', 'psycopg')"
        " if m in sys.modules)}))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=APP_DIR, capture_output=True, text=True,
                         timeout=60, check=True)
    assert json.loads(out.stdout) == {"dummy": 0, "mods": []}
