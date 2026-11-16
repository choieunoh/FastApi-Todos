"""배포 서버 통합 테스트 — `pytest -m integration` 으로만 돈다(기본 실행에서 제외, 실행은 명시적 승인 후).

(8.0.0) 데이터 API 15개는 로그인 필수(익명 401), 쓰기 11개는 CSRF 필수(403)다(V8_SPEC §40). 인증 우회 없이
실제 사용자와 같은 절차를 거친다: POST /auth/login → 세션 쿠키(requests.Session 이 보관) + 응답의 csrf_token 을
쓰기 요청의 X-CSRF-Token 헤더로. 끝나면 로그아웃해 이 테스트가 만든 세션을 지운다.

- 대상: INTEGRATION_BASE_URL — 필수(기본값 없음). 없거나 비어 있거나 http(s)://호스트[:포트] 형식이 아니면 요청을 하나도 보내지 않고
  실패한다. 이전 외부 서버 주소로 자동 대체하지 않는다(2026-11-15). 실행 전에 대상 버전·주소를 확인한다.
- 계정: INTEGRATION_USERNAME / INTEGRATION_PASSWORD 환경변수 — 대상 환경마다 만든 합성 테스트 계정 1개(V8_SPEC §42.1).
  Jenkins 에서는 Credentials 바인딩(환경변수, 콘솔 마스킹)으로만 넣는다. 저장소·명령줄·로그에 값을 넣지 않는다.
  값이 없으면 skip 이 아니라 실패한다(통합 실행을 승인했는데 인증 없이 조용히 넘어가지 않게).
- 출력·실패 메시지에는 상태 코드만 남긴다(응답 본문·쿠키·토큰·비밀번호를 넣지 않는다).
- 이 테스트가 만든 할 일은 테스트 안에서 지운다. 로그아웃은 실패해도 다른 테스트 결과를 바꾸지 않는다.
"""
import os
import re

import pytest

requests = pytest.importorskip("requests")   # requirements-dev.txt 에 있다

pytestmark = pytest.mark.integration          # 기본 pytest 실행에서는 제외된다

BASE_URL_ENV = "INTEGRATION_BASE_URL"
_BASE_URL_RE = re.compile(r"https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?")


def require_base_url(environ=os.environ) -> str:
    # 대상 주소는 명시해야 한다 — 없거나 빈 값·형식이 다른 값이면 실패(skip·기본값 대체 없음). 끝의 / 는 뗀다
    raw = environ.get(BASE_URL_ENV)
    value = (raw or "").strip().rstrip("/")
    if not value:
        pytest.fail(f"{BASE_URL_ENV} 환경변수가 없거나 비어 있습니다 — 대상 서버를 명시해야 합니다(기본값 없음)")
    if not _BASE_URL_RE.fullmatch(value):
        pytest.fail(f"{BASE_URL_ENV} 형식이 http(s)://호스트[:포트] 가 아닙니다")
    return value


@pytest.fixture(scope="module")
def base_url() -> str:
    # 모든 테스트가 요청 전에 이 fixture 를 거친다 — 주소가 없으면 어떤 요청도 보내지 않는다
    return require_base_url()


TIMEOUT = 15                                 # 초 — 요청마다


@pytest.fixture(scope="module")
def credentials() -> dict:
    username = os.environ.get("INTEGRATION_USERNAME", "")
    password = os.environ.get("INTEGRATION_PASSWORD", "")
    if not username or not password:
        pytest.fail("INTEGRATION_USERNAME·INTEGRATION_PASSWORD 환경변수가 없습니다 — 테스트 계정으로 로그인해야 합니다(값은 출력하지 않음)")
    return {"username": username, "password": password}


@pytest.fixture(scope="module")
def auth(base_url, credentials):
    # 로그인 1회 — 쿠키는 Session 이, CSRF 토큰은 헤더 값으로. 끝나면 로그아웃(세션 정리)
    session = requests.Session()
    res = session.post(f"{base_url}/auth/login", json=credentials, timeout=TIMEOUT)
    assert res.status_code == 200, f"로그인 실패: HTTP {res.status_code}"
    csrf = res.json().get("csrf_token")
    assert isinstance(csrf, str) and csrf, "로그인 응답에 csrf_token 이 없음"
    yield session, {"X-CSRF-Token": csrf}, base_url
    try:
        session.post(f"{base_url}/auth/logout", headers={"X-CSRF-Token": csrf}, timeout=TIMEOUT)
    finally:
        session.close()


def test_todo_integration(auth):
    session, csrf, base = auth
    # 1. To-Do 추가 (쓰기 — CSRF 헤더)
    create_response = session.post(f"{base}/todos", json={"title": "integration test todo"},
                                   headers=csrf, timeout=TIMEOUT)
    assert create_response.status_code == 201

    created_todo = create_response.json()
    todo_id = created_todo["id"]

    # 2. To-Do 조회 (읽기 — 세션 쿠키만)
    get_response = session.get(f"{base}/todos", timeout=TIMEOUT)
    assert get_response.status_code == 200

    todos = get_response.json()
    assert any(todo["id"] == todo_id for todo in todos)

    # 3. To-Do 수정
    update_response = session.put(f"{base}/todos/{todo_id}", json={"title": "updated integration todo"},
                                  headers=csrf, timeout=TIMEOUT)
    assert update_response.status_code == 200

    # 4. To-Do 삭제
    delete_response = session.delete(f"{base}/todos/{todo_id}", headers=csrf, timeout=TIMEOUT)
    assert delete_response.status_code == 204


def test_create_todo_without_title(auth):
    session, csrf, base = auth
    response = session.post(f"{base}/todos", json={}, headers=csrf, timeout=TIMEOUT)
    assert response.status_code == 422


def test_delete_nonexistent_todo(auth):
    session, csrf, base = auth
    response = session.delete(f"{base}/todos/99999999", headers=csrf, timeout=TIMEOUT)
    assert response.status_code == 404


# ---------- (8.0.0) 인증 정책 — 쓰기는 하지 않는다(익명·CSRF 거부는 파일을 바꾸지 않는다) ----------

@pytest.mark.parametrize("path", ["/todos", "/stats", "/task-templates", "/todos/export?format=csv"])
def test_anonymous_data_requests_are_401(base_url, path):
    response = requests.get(f"{base_url}{path}", timeout=TIMEOUT)
    assert response.status_code == 401


def test_write_without_csrf_is_403(auth):
    session, _, base = auth
    response = session.post(f"{base}/todos", json={"title": "csrf 없음 — 저장되면 안 됨"}, timeout=TIMEOUT)
    assert response.status_code == 403


def test_me_with_session(auth):
    session, _, base = auth
    response = session.get(f"{base}/auth/me", timeout=TIMEOUT)
    assert response.status_code == 200
    assert set(response.json()) == {"id", "username", "csrf_token", "created_at"}   # 값은 출력·비교하지 않는다
