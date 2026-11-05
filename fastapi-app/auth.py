"""인증 순수 로직 (7.0.0 Phase 2-B) — 비밀번호 해시, 토큰, CSRF, Origin, 쿠키, 설정.

- DB·웹 프레임워크에 의존하지 않는다(main·db·SQLAlchemy·FastAPI 를 import 하지 않는다). 표준 라이브러리와 argon2-cffi 만 쓴다.
- 현재 시각이 필요한 함수는 시각을 인자로 받는다 (테스트의 시계 고정은 main.now() 를 넘겨서 한다).
- 비밀번호·해시·세션 토큰·CSRF 토큰 값은 예외 메시지와 로그에 넣지 않는다.
- import 할 때 해시를 계산하지 않는다. 더미 해시는 처음 쓸 때 한 번만 만든다.
- 세션 저장(2-C), 시도 제한(2-D), 라우트와 시작 가드 연결(2-E)은 여기 없다.
"""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# ── 입력 규칙 (V7_SPEC.md §28.1) ──
USERNAME_MIN, USERNAME_MAX = 3, 30
PASSWORD_MIN, PASSWORD_MAX = 8, 128
PASSWORD_MAX_BYTES = PASSWORD_MAX * 4            # UTF-8 한 글자는 최대 4바이트 — 정상 128자는 항상 이 안에 든다
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]+", re.ASCII)   # 소문자로 바꾸기 전에 검사한다 (K 켈빈 기호 등 차단)

# ── 세션·쿠키 (§29.2) ──
SESSION_TTL = timedelta(days=14)                 # 생성 후 14일 고정, 연장하지 않는다 (D-5)
SESSION_MAX_AGE = int(SESSION_TTL.total_seconds())   # 1209600
COOKIE_NAME_DEV = "todo_session"
COOKIE_NAME_SECURE = "__Host-todo_session"       # __Host- : Secure + Path=/ + Domain 없음이어야 브라우저가 받는다
TOKEN_BYTES = 32                                 # token_urlsafe(32) → 43자

# ── 설정 (§29.6, D-13) ──
APP_ENVS = ("development", "test", "production")
DEFAULT_APP_ENV = "development"

_HASHER = PasswordHasher()                       # 기본값: Argon2id, RFC 9106 low-memory (t=3, m=64MiB, p=4)


class AuthConfigError(RuntimeError):
    """인증 설정 오류 — 메시지에는 변수 이름만 넣고 값은 넣지 않는다."""


# ────────────────────────── 설정 ──────────────────────────

@dataclass(frozen=True)
class AuthSettings:
    app_env: str
    cookie_secure: bool
    registration_enabled: bool

    @property
    def cookie_name(self) -> str:
        return COOKIE_NAME_SECURE if self.cookie_secure else COOKIE_NAME_DEV


def _env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    # 없으면 기본값, 있으면 true/false 만 받는다 (빈 문자열도 오류 — compose 의 빈 치환을 놓치지 않게)
    raw = environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise AuthConfigError(f"{name} 는 true 또는 false 여야 합니다")


def load_settings(environ: Mapping[str, str] | None = None) -> AuthSettings:
    # 실제 앱 시작 가드는 2-E 에서 main.py 가 이 함수를 불러 연결한다
    env = os.environ if environ is None else environ
    raw_env = env.get("APP_ENV")
    app_env = DEFAULT_APP_ENV if raw_env is None else raw_env.strip().lower()
    if app_env not in APP_ENVS:
        raise AuthConfigError(f"APP_ENV 는 {', '.join(APP_ENVS)} 중 하나여야 합니다")
    production = app_env == "production"
    cookie_secure = _env_bool(env, "COOKIE_SECURE", False)
    if production and not cookie_secure:
        raise AuthConfigError("APP_ENV=production 에서는 COOKIE_SECURE=true 가 필요합니다")
    registration = _env_bool(env, "REGISTRATION_ENABLED", not production)
    return AuthSettings(app_env=app_env, cookie_secure=cookie_secure, registration_enabled=registration)


# ────────────────────────── 입력 검증 ──────────────────────────

def normalize_username(raw: str) -> str:
    # 앞뒤 공백 제거 → ASCII 허용 문자·길이 검사 → 소문자 (입력값은 메시지에 넣지 않는다)
    if not isinstance(raw, str):
        raise ValueError("username 은 문자열이어야 합니다")
    value = raw.strip()
    if not USERNAME_MIN <= len(value) <= USERNAME_MAX:
        raise ValueError(f"username 은 {USERNAME_MIN}~{USERNAME_MAX}자여야 합니다")
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("username 에는 영문자, 숫자, 밑줄(_), 점(.), 하이픈(-)만 쓸 수 있습니다")
    return value.lower()


def check_password(raw: str) -> str:
    # 길이만 검사하고 그대로 돌려준다 (공백 유지, 유니코드 정규화 없음). UTF-8 로 못 바꾸는 값(외톨이 서로게이트)은 거부
    if not isinstance(raw, str):
        raise ValueError("비밀번호는 문자열이어야 합니다")
    if not PASSWORD_MIN <= len(raw) <= PASSWORD_MAX:
        raise ValueError(f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자여야 합니다")
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("비밀번호에 쓸 수 없는 문자가 있습니다") from None
    return raw


# ────────────────────────── 비밀번호 해시 ──────────────────────────

class PasswordCheck(Enum):
    OK = "ok"
    MISMATCH = "mismatch"                        # 비밀번호가 다르다
    INVALID_HASH = "invalid_hash"                # 저장된 해시가 깨졌다 (내용은 드러내지 않는다)


def hash_password(password: str) -> str:
    return _HASHER.hash(check_password(password))


def _verifiable(password: object) -> bool:
    # 검증 경로 방어선(R-24): 문자열, 128자 이하, UTF-8 로 바꿀 수 있고 512바이트 이하일 때만 Argon2 에 넘긴다.
    # 글자 수를 먼저 봐서 아주 긴 입력도 인코딩 없이 바로 거른다. 최소 길이는 보지 않는다(짧으면 그냥 불일치)
    if not isinstance(password, str) or len(password) > PASSWORD_MAX:
        return False
    try:
        return len(password.encode("utf-8")) <= PASSWORD_MAX_BYTES
    except UnicodeEncodeError:
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # 없는 계정용 — 처음 쓸 때 한 번만 만든다. 실제 해시와 같은 파라미터라 검증 시간이 비슷하다
    return _HASHER.hash(secrets.token_urlsafe(16))


def verify_dummy(password: str) -> None:
    # 없는 username 으로 로그인해도 같은 비용을 써서 응답 시간으로 계정 존재를 알 수 없게 한다. 결과는 버린다
    # 검증할 수 없는 입력(너무 길거나 인코딩 불가)은 check_password_hash 와 똑같이 해시 없이 바로 끝낸다
    if not _verifiable(password):
        return
    try:
        _HASHER.verify(_dummy_hash(), password)
    except (VerificationError, InvalidHashError, UnicodeEncodeError, TypeError):
        pass


def check_password_hash(stored_hash: str, password: str) -> PasswordCheck:
    # 실패 이유를 나눠 돌려준다. 비밀번호 불일치는 VerifyMismatchError 뿐이고, 그 밖의 검증 오류
    # (형식이 깨졌거나 디코딩 실패)는 깨진 해시다. 깨진 해시도 더미 검증으로 비슷한 시간을 쓴다
    # 검증할 수 없는 입력은 Argon2 에 넘기지 않고 바로 MISMATCH — 계정 유무와 관계없이 같은 빠른 실패 경로다
    if not _verifiable(password):
        return PasswordCheck.MISMATCH
    try:
        _HASHER.verify(stored_hash, password)
        return PasswordCheck.OK
    except VerifyMismatchError:
        return PasswordCheck.MISMATCH
    except (InvalidHashError, VerificationError):
        verify_dummy(password)
        return PasswordCheck.INVALID_HASH
    except (UnicodeEncodeError, TypeError):      # 비밀번호 쪽 문제 (UTF-8 로 못 바꾸는 값 등)
        return PasswordCheck.MISMATCH


def verify_password(stored_hash: str, password: str) -> bool:
    return check_password_hash(stored_hash, password) is PasswordCheck.OK


def needs_rehash(stored_hash: str) -> bool:
    # 로그인에 성공한 해시만 넘긴다. 깨진 해시는 값 없이 ValueError
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except InvalidHashError:
        raise ValueError("저장된 비밀번호 해시 형식이 올바르지 않습니다") from None


# ────────────────────────── 토큰·CSRF ──────────────────────────

def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _to_bytes(value: str) -> bytes:
    # 클라이언트가 보낸 어떤 문자열이든 예외 없이 bytes 로 (외톨이 서로게이트 포함)
    return value.encode("utf-8", "surrogatepass")


def hash_token(token: str) -> bytes:
    # DB 에는 원문 대신 SHA-256 digest(32바이트)만 저장한다
    return hashlib.sha256(_to_bytes(token)).digest()


def csrf_matches(expected: str | None, provided: str | None) -> bool:
    # 둘 중 하나라도 비어 있으면 실패. str 끼리 compare_digest 는 비ASCII 에서 TypeError 라 bytes 로 비교한다
    if not expected or not provided or not isinstance(expected, str) or not isinstance(provided, str):
        return False
    return hmac.compare_digest(_to_bytes(expected), _to_bytes(provided))


# ────────────────────────── 세션 만료 ──────────────────────────

def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("시간대가 있는 시각이어야 합니다")


def session_expires_at(created_at: datetime) -> datetime:
    _require_aware(created_at)
    return created_at + SESSION_TTL


def is_expired(expires_at: datetime, now: datetime) -> bool:
    # 만료 시각과 같거나 지나면 만료
    _require_aware(expires_at)
    _require_aware(now)
    return now >= expires_at


# ────────────────────────── Origin·Referer (§29.3) ──────────────────────────

DEFAULT_PORTS = {"http": 80, "https": 443}
_NETLOC_RE = re.compile(r"(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?", re.ASCII)   # 사용자 정보·공백 불가
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(rf"{_LABEL}(?:\.{_LABEL})*", re.ASCII)


@dataclass(frozen=True)
class WebOrigin:
    # 스킴·호스트·포트를 정규화한 값. 같은지 비교만 한다 (문자열 접두사 비교를 쓰지 않는다)
    scheme: str                                  # "http" | "https"
    host: str                                    # 소문자, IPv6 는 대괄호 없이
    port: int                                    # 생략하면 스킴 기본 포트


def _valid_host(host: str, bracketed: bool) -> bool:
    if bracketed:
        try:
            return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
        except ValueError:
            return False
    return bool(_HOSTNAME_RE.fullmatch(host))   # IPv4 주소도 이 규칙에 맞는다


def _make_origin(scheme: str, netloc: str) -> WebOrigin | None:
    scheme = scheme.lower()
    if scheme not in DEFAULT_PORTS or not _NETLOC_RE.fullmatch(netloc):
        return None
    try:
        parts = urlsplit(f"{scheme}://{netloc}")
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname or ""
    if not host or not _valid_host(host, netloc.startswith("[")):
        return None
    if port is None:
        port = DEFAULT_PORTS[scheme]
    elif not 1 <= port <= 65535:
        return None
    return WebOrigin(scheme=scheme, host=host, port=port)


_PRINTABLE_ASCII_RE = re.compile(r"[\x21-\x7e]+", re.ASCII)


def _header_url(value: str) -> str | None:
    # R-25: urlsplit 은 탭·CR·LF 와 앞쪽 제어 문자를 조용히 지워 비정상 값을 정상처럼 만든다.
    # 그래서 파싱 전에 검사한다 — 앞뒤 공백(0x20)만 떼고, 출력 가능한 ASCII(0x21~0x7E) 밖의 문자가 있으면 거부
    if not isinstance(value, str):
        return None
    value = value.strip(" ")
    return value if _PRINTABLE_ASCII_RE.fullmatch(value) else None


def parse_origin_header(value: str) -> WebOrigin | None:
    # Origin 은 "스킴://호스트[:포트]" 만 온다. "null"·경로·쿼리·조각이 있으면 거부
    value = _header_url(value)
    if value is None or value == "null":
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.path or parts.query or parts.fragment:
        return None
    return _make_origin(parts.scheme, parts.netloc)


def parse_referer_header(value: str) -> WebOrigin | None:
    # Referer 는 전체 URL — 경로는 무시하고 스킴·호스트·포트만 본다
    value = _header_url(value)
    if value is None:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return _make_origin(parts.scheme, parts.netloc)


def request_origin(scheme: str, host_header: str | None) -> WebOrigin | None:
    # 외부에서 본 요청 Origin. scheme 을 무엇으로 볼지(프록시 헤더 신뢰)는 2-E 에서 정해 넘긴다
    if not host_header:
        return None
    return _make_origin(scheme, host_header.strip())


def origin_allowed(origin: str | None, referer: str | None, expected: WebOrigin | None) -> bool:
    # Origin 우선, 없으면 Referer, 둘 다 없으면 허용(브라우저가 아닌 클라이언트, §29.3)
    if origin is not None:
        actual = parse_origin_header(origin)
    elif referer is not None:
        actual = parse_referer_header(referer)
    else:
        return True
    return actual is not None and expected is not None and actual == expected


# ────────────────────────── 쿠키 ──────────────────────────

def cookie_kwargs(settings: AuthSettings) -> dict[str, object]:
    # Response.set_cookie(**cookie_kwargs(s), value=token) — Domain 은 두지 않는다
    return {
        "key": settings.cookie_name,
        "max_age": SESSION_MAX_AGE,
        "path": "/",
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }


def cookie_delete_kwargs(settings: AuthSettings) -> dict[str, object]:
    # Response.delete_cookie(**...) — 만들 때와 같은 이름·Path·Secure 여야 브라우저가 지운다
    return {
        "key": settings.cookie_name,
        "path": "/",
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }
