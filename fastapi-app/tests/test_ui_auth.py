"""(7.0.0 Phase 2-G → 8.0.0 2단계) 로그인 게이트·로그인·가입·계정 UI — DB 없이 도는 Playwright 테스트.

- `/auth/*` 응답은 `page.route` 로 흉내 낸다(실제 PostgreSQL 인증은 검증하지 않는다 — test_ui.py·test_db_postgres.py 몫).
- (8.0.0) 데이터 API 15개는 로그인 필수라 DB 없는 UI 서버로는 데이터를 받을 수 없다. 그래서 데이터 경로(`/todos`·`/stats`·
  `/task-templates`)도 브라우저 안의 가짜 응답(`FakeData`)으로 흉내 내고, 요청(메서드·경로·CSRF 헤더)을 기록한다.
  화면 HTML(`/`)만 conftest 의 임시 서버에서 받는다. 인증 우회 설정은 앱에 두지 않는다.
- 화면 로그인 게이트(V8_SPEC §41.1): 로그아웃 상태에서는 로그인 카드만 보이고 앱 영역(`app-area`)을 숨기며 데이터 요청을
  보내지 않는다. 로그인하면 앱을 보이고 불러오기를 시작한다. 데이터 401 이면 로그인 카드로 돌아간다.
- 보류 응답(`HeldRoute`)으로 늦게 오는 `/auth/me` 를 재현해 오래된 응답이 최신 인증 상태를 덮지 않는지 본다.
- (8.0.0) v7 의 접힌 진입 버튼 `#auth-open` 은 없어졌다 — 로그아웃 상태의 로그인 카드는 늘 펼쳐져 있다.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, Route, expect

from test_ui import CONTRAST_JS                  # 기존 명도 대비 검사(문서 전체의 보이는 글자)를 그대로 쓴다

ME = {"id": 7, "username": "alice", "csrf_token": "tok-me", "created_at": "2026-10-31T16:30:00+00:00"}
LOGIN = {"id": 7, "username": "alice", "csrf_token": "tok-new"}
PASSWORD_OK = {"id": 7, "username": "alice", "csrf_token": "tok-pw"}
NO_HSCROLL = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
DATA_ROUTE = re.compile(r"^https?://[^/]+/(todos|stats|task-templates)(/[^?]*)?(\?.*)?$")
APP_DIR = Path(__file__).resolve().parent.parent


def fulfill(route: Route, status: int, body: object = None, headers: dict | None = None) -> None:
    route.fulfill(status=status, content_type="application/json", headers=headers or {},
                  body="" if body is None else json.dumps(body))


def mock_me(page: Page, status: int = 401, body: object = None, then: list | None = None) -> list:
    # /auth/me 를 고정 응답으로 — then 을 주면 두 번째 호출부터 그 (status, body) 를 차례로 쓴다. 호출 기록을 돌려준다
    calls: list = []
    queue = list(then or [])

    def handle(route: Route) -> None:
        calls.append(route.request.headers.get("x-csrf-token"))
        st, bd = (status, body) if len(calls) == 1 or not queue else queue.pop(0)
        fulfill(route, st, bd if bd is not None else ({"detail": "로그인이 필요합니다"} if st == 401 else ME))
    page.route("**/auth/me", handle)
    return calls


class HeldRoute:
    """응답을 바로 하지 않고 붙잡아 둔다 — 테스트가 원하는 순간에 fulfill 한다."""

    def __init__(self) -> None:
        self.routes: list[Route] = []

    def __call__(self, route: Route) -> None:
        self.routes.append(route)


def record(page: Page, pattern: str, status: int, body: object = None, headers: dict | None = None) -> list:
    # 요청(메서드·CSRF 헤더·본문)을 기록하고 고정 응답을 준다
    seen: list = []

    def handle(route: Route) -> None:
        request = route.request
        seen.append({"method": request.method, "csrf": request.headers.get("x-csrf-token"),
                     "body": request.post_data_json if request.post_data else None})
        fulfill(route, status, body, headers)
    page.route(pattern, handle)
    return seen


def todo(id: int, title: str, **fields) -> dict:
    base = {"id": id, "title": title, "description": "", "completed": False, "priority": "보통", "due_date": None,
            "tags": [], "favorite": False, "recurrence": "none", "created_at": "2026-10-01T00:00:00+00:00",
            "completed_at": None, "subtasks": [], "next_spawned": False}
    return {**base, **fields}


class FakeData:
    """데이터 API 를 흉내 낸다 — 공유 할 일 목록(메모리), 템플릿 0개, 최소 통계. 요청은 (메서드, 경로, CSRF) 로 기록한다.
    fail[(메서드, 경로)] = 상태 코드를 두면 그 요청만 그 상태로 실패시킨다(401 = 세션 만료 흉내)."""

    FIELDS = ("title", "description", "completed", "priority", "due_date", "tags", "favorite", "recurrence")

    def __init__(self, page: Page, todos: list[dict] | None = None) -> None:
        self.todos = [dict(t) for t in (todos or [])]
        self.requests: list[tuple[str, str, str | None]] = []
        self.fail: dict[tuple[str, str], int] = {}
        page.route(DATA_ROUTE, self.handle)

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.requests if method is None or m == method]

    def csrf(self, method: str) -> list[str | None]:
        return [c for m, _, c in self.requests if m == method]

    def handle(self, route: Route) -> None:
        request = route.request
        path = urlparse(request.url).path
        self.requests.append((request.method, path, request.headers.get("x-csrf-token")))
        status = self.fail.get((request.method, path))
        if status:
            return fulfill(route, status, {"detail": "로그인이 필요합니다" if status == 401 else "x"})
        if path == "/todos" and request.method == "GET":
            return fulfill(route, 200, self.todos)
        if path == "/todos" and request.method == "POST":
            body = request.post_data_json
            new = todo(max([t["id"] for t in self.todos], default=0) + 1, body["title"],
                       **{k: body[k] for k in self.FIELDS if k in body and k != "title"})
            self.todos.append(new)
            return fulfill(route, 201, new)
        if (m := re.fullmatch(r"/todos/(\d+)", path)) and request.method == "PUT":
            current = next(t for t in self.todos if t["id"] == int(m.group(1)))
            body = request.post_data_json
            was_done = current["completed"]
            current.update({k: body[k] for k in self.FIELDS if k in body})
            if current["completed"] and not was_done:
                current["completed_at"] = "2026-11-15T03:00:00+00:00"
            elif not current["completed"]:
                current["completed_at"] = None
            return fulfill(route, 200, current)
        if path == "/task-templates" and request.method == "GET":
            return fulfill(route, 200, [])
        if path == "/stats":
            return fulfill(route, 200, {"today": "2026-11-15", "timezone": "Asia/Seoul", "total": len(self.todos),
                                        "completed": 0, "active": len(self.todos), "overdue": 0, "completion_rate": 0.0,
                                        "daily_completions": [], "completions_7d": 0, "completions_30d": 0,
                                        "trend_excluded": 0, "active_by_priority": {"높음": 0, "보통": 0, "낮음": 0},
                                        "due_compliance": {"eligible": 0, "on_time": 0, "rate": None}})
        return fulfill(route, 404, {"detail": "not found"})


def open_app(page: Page, *, idle: bool = True) -> None:
    page.goto("/")
    expect(page.get_by_test_id("theme-select")).to_be_visible()
    if idle:                                                       # 보류 응답이 있으면 networkidle 이 오지 않는다
        page.wait_for_load_state("networkidle")


def panel(page: Page):
    return page.get_by_test_id("auth-panel")


def app_area(page: Page):
    return page.get_by_test_id("app-area")


def auth_error(page: Page):
    return page.get_by_test_id("auth-error")


def fill_login(page: Page, username: str = "alice", password: str = "correct-pass-1") -> None:
    expect(page.get_by_test_id("auth-username")).to_be_visible()   # (8.0.0) 로그인 카드는 늘 펼쳐져 있다
    page.get_by_test_id("auth-username").fill(username)
    page.get_by_test_id("auth-password").fill(password)


def add_todo(page: Page, title: str) -> None:
    page.locator("#title").fill(title)
    page.get_by_role("button", name="Add To-Do").click()


def expect_signed_out(page: Page) -> None:
    expect(panel(page)).to_be_visible()
    expect(app_area(page)).to_be_hidden()
    expect(page.get_by_test_id("current-user")).to_be_hidden()
    expect(page.get_by_test_id("account-menu")).to_be_hidden()
    expect(page.get_by_test_id("logout")).to_be_hidden()


def expect_signed_in(page: Page, username: str = "alice") -> None:
    expect(page.get_by_test_id("current-user")).to_have_text(username)
    expect(app_area(page)).to_be_visible()
    expect(panel(page)).to_be_hidden()
    expect(page.get_by_test_id("account-menu")).to_be_visible()
    expect(page.get_by_test_id("logout")).to_be_visible()


# ---------- 시작 상태 — 화면 로그인 게이트 ----------

def test_me_401_shows_only_login_card_and_sends_no_data_requests(page: Page):
    # (8.0.0, 이전: 401 이어도 v6 앱·목록이 보였다) 로그인 카드만 보이고 데이터 요청이 0건이다
    data = FakeData(page, [todo(1, "공유 할 일")])
    mock_me(page, 401)
    open_app(page)
    expect_signed_out(page)
    expect(page.locator("#error")).to_be_hidden()                  # 시작 401 은 오류가 아니다
    expect(auth_error(page)).to_be_hidden()
    expect(page.get_by_test_id("todo-item")).to_have_count(0)
    expect(page.get_by_test_id("auth-username")).to_be_visible()   # 카드는 펼쳐진 채(진입 버튼 없음)
    expect(page.get_by_test_id("auth-submit")).to_be_visible()
    assert page.locator("#auth-open").count() == 0
    expect(panel(page)).to_contain_text("할 일·템플릿은 로그인한 모든 사용자가 함께 쓰는 공유 데이터입니다.")
    assert data.requests == []                                     # /todos·/stats·/task-templates 를 보내지 않는다


def test_signed_out_tab_order_is_theme_then_login_form(page: Page):
    # (8.0.0, 이전: 접힌 폼을 건너뛰고 할 일 입력으로) 테마 → 아이디 → 비밀번호 → 로그인 → 전환. 숨은 앱 영역은 Tab 에 없다
    FakeData(page)
    mock_me(page, 401)
    open_app(page)
    for testid in ("theme-select", "auth-username", "auth-password", "auth-submit", "auth-mode-toggle"):
        page.keyboard.press("Tab")
        expect(page.get_by_test_id(testid)).to_be_focused()


def test_me_200_shows_app_user_and_logout(page: Page):
    data = FakeData(page, [todo(1, "공유 할 일")])
    mock_me(page, 200, ME)
    open_app(page)
    expect_signed_in(page)
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    expect(page.locator("#error")).to_be_hidden()
    expect(page.get_by_test_id("shared-note")).to_have_text("할 일·템플릿은 로그인한 모든 사용자가 함께 보고 수정하는 공유 데이터입니다.")
    assert set(data.paths("GET")) == {"/todos", "/task-templates"}


def test_me_500_shows_login_card_with_notice_and_no_data(page: Page):
    # (8.0.0, 이전: 목록은 그대로 보였다) 상태를 확인하지 못하면 로그인 카드 + 안내, 데이터 요청 없음
    data = FakeData(page, [todo(1, "공유 할 일")])
    mock_me(page, 500, {"detail": "internal secret detail"})
    open_app(page)
    expect_signed_out(page)
    expect(page.locator("#error")).to_be_hidden()
    expect(auth_error(page)).to_have_text("로그인 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    expect(auth_error(page)).not_to_contain_text("secret")
    expect(page.get_by_test_id("auth-username")).to_be_visible()
    assert data.requests == []


def test_data_401_on_first_load_returns_to_login(page: Page):
    # /auth/me 는 200 이었지만 첫 데이터 요청이 401(세션 만료) → 로그인 카드, #error 에는 쓰지 않는다
    data = FakeData(page, [todo(1, "공유 할 일")])
    data.fail[("GET", "/todos")] = 401
    mock_me(page, 200, ME)
    open_app(page)
    expect_signed_out(page)
    expect(auth_error(page)).to_have_text("다시 로그인하세요.")
    expect(page.locator("#error")).to_be_hidden()


# ---------- 폼 ----------

def test_mode_toggle_switches_title_button_and_autocomplete(page: Page):
    FakeData(page)
    mock_me(page, 401)
    open_app(page)
    toggle, submit = page.get_by_test_id("auth-mode-toggle"), page.get_by_test_id("auth-submit")
    password = page.get_by_test_id("auth-password")
    expect(toggle).to_be_visible()                                  # (8.0.0) 카드가 늘 펼쳐져 있어 바로 보인다
    expect(submit).to_have_text("로그인")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(password).to_have_attribute("autocomplete", "current-password")
    toggle.click()
    expect(submit).to_have_text("회원가입")
    expect(page.locator("#auth-title")).to_have_text("회원가입")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(password).to_have_attribute("autocomplete", "new-password")
    toggle.click()
    expect(submit).to_have_text("로그인")


def test_register_success_uses_register_endpoint_and_opens_app(page: Page):
    data = FakeData(page, [todo(1, "공유 할 일")])
    mock_me(page, 401)
    logins = record(page, "**/auth/login", 200, LOGIN)
    registers = record(page, "**/auth/register", 201, {"id": 8, "username": "newbie", "csrf_token": "tok-reg"})
    open_app(page)
    page.get_by_test_id("auth-mode-toggle").click()
    fill_login(page, "newbie", "new-password-1")
    page.get_by_test_id("auth-submit").click()
    expect_signed_in(page, "newbie")
    expect(page.get_by_test_id("todo-item")).to_have_count(1)        # 가입하면 공유 목록을 불러온다
    assert logins == [] and len(registers) == 1
    assert registers[0]["body"] == {"username": "newbie", "password": "new-password-1"}
    assert "/todos" in data.paths("GET")


def test_enter_submits_login_and_opens_app(page: Page):
    data = FakeData(page, [todo(1, "공유 할 일")])
    mock_me(page, 401)
    logins = record(page, "**/auth/login", 200, LOGIN)
    open_app(page)
    assert data.requests == []
    fill_login(page)
    page.get_by_test_id("auth-password").press("Enter")
    expect_signed_in(page)
    expect(auth_error(page)).to_be_hidden()
    expect(page.get_by_test_id("auth-password")).to_have_value("")   # 비밀번호 칸을 비운다
    expect(page.get_by_test_id("todo-item")).to_have_count(1)        # 로그인 뒤 불러오기
    assert len(logins) == 1 and logins[0]["body"] == {"username": "alice", "password": "correct-pass-1"}


def test_double_submit_sends_one_request(page: Page):
    FakeData(page)
    mock_me(page, 401)
    held = HeldRoute()
    page.route("**/auth/login", held)
    open_app(page)
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("auth-submit")).to_be_disabled()
    page.evaluate("document.getElementById('login-form').requestSubmit()")   # 비활성 버튼을 우회한 두 번째 제출
    page.get_by_test_id("auth-password").press("Enter")
    page.wait_for_timeout(200)
    assert len(held.routes) == 1
    fulfill(held.routes[0], 200, LOGIN)
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    expect(page.get_by_test_id("auth-submit")).to_be_enabled()


# ---------- CSRF 헤더 ----------

def test_post_after_login_carries_csrf_and_get_does_not(page: Page):
    # (8.0.0, 이전: 로그인 전 POST 도 보냈다) 로그인 전에는 앱이 숨어 데이터 요청이 없고, 로그인 뒤 POST 에만 토큰
    data = FakeData(page)
    mock_me(page, 401)
    record(page, "**/auth/login", 200, LOGIN)
    open_app(page)
    assert data.requests == []
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    add_todo(page, "로그인 후")
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    assert data.csrf("POST") == ["tok-new"]                           # 로그인 응답의 토큰
    assert set(data.csrf("GET")) == {None}                             # GET 에는 붙이지 않는다


def test_login_and_register_never_carry_old_csrf(page: Page):
    FakeData(page)
    mock_me(page, 200, ME)                                            # 이미 로그인(토큰 tok-me) 상태
    logins = record(page, "**/auth/login", 200, LOGIN)
    registers = record(page, "**/auth/register", 201, LOGIN)
    open_app(page)
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    page.evaluate("""async () => {
      await api("/auth/login", "POST", { username: "alice", password: "correct-pass-1" });
      await api("/auth/register", "POST", { username: "bob", password: "correct-pass-1" });
    }""")
    assert [r["csrf"] for r in logins + registers] == [None, None]


def test_logout_success_sends_csrf_and_reloads(page: Page):
    FakeData(page)
    me_calls = mock_me(page, 200, ME, then=[(401, None)])             # 새로고침 뒤에는 로그아웃 상태
    logouts = record(page, "**/auth/logout", 204)
    open_app(page)
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    page.evaluate("window.__beforeLogout = true")
    page.get_by_test_id("logout").click()
    page.wait_for_function("window.__beforeLogout === undefined")    # 새로고침으로 화면 상태가 비었다
    assert [r["csrf"] for r in logouts] == ["tok-me"]
    expect_signed_out(page)                                           # (8.0.0) 앱 영역을 숨긴 로그인 카드
    assert len(me_calls) == 2


def test_logout_403_shows_error_and_keeps_state(page: Page):
    FakeData(page)
    mock_me(page, 200, ME)
    record(page, "**/auth/logout", 403, {"detail": "CSRF 토큰이 올바르지 않습니다"})
    open_app(page)
    page.evaluate("window.__beforeLogout = true")
    page.get_by_test_id("logout").click()
    expect(page.locator("#error")).to_contain_text("로그아웃 실패")
    expect_signed_in(page)                                            # 상태를 지우지 않는다
    assert page.evaluate("window.__beforeLogout") is True             # 새로고침하지 않았다


# ---------- 오류 문구 ----------

@pytest.mark.parametrize("mode, status, body, headers, expected", [
    ("login", 401, {"detail": "아이디 또는 비밀번호가 올바르지 않습니다"}, None, "아이디 또는 비밀번호가 올바르지 않습니다."),
    ("login", 403, {"detail": "요청 출처가 올바르지 않습니다"}, None, "요청 출처가 올바르지 않습니다"),
    ("register", 403, {"detail": "회원가입이 비활성화되어 있습니다"}, None, "회원가입이 비활성화되어 있습니다"),
    ("register", 409, {"detail": "이미 사용 중인 아이디입니다"}, None, "이미 사용 중인 아이디입니다."),
    ("register", 422, {"detail": [{"loc": ["body", "password"], "msg": "Value error, 비밀번호는 8~128자여야 합니다",
                                   "type": "value_error"}]}, None,
     "입력값을 확인해 주세요: Value error, 비밀번호는 8~128자여야 합니다"),
    ("login", 429, {"detail": "시도 횟수를 초과했습니다"}, {"Retry-After": "125"},
     "시도 횟수를 초과했습니다. 약 3분 후 다시 시도해 주세요."),                     # 125초 → 올림 3분
    ("login", 429, {"detail": "시도 횟수를 초과했습니다"}, None, "시도 횟수를 초과했습니다. 잠시 후 다시 시도해 주세요."),
    ("login", 503, {"detail": "<b>pool</b> internal"}, None, "요청이 많아 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."),
    ("login", 500, {"detail": "DETAIL: Key (username)=(alice)"}, None, "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."),
    ("login", 418, "not-json", None, "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."),
], ids=["401", "403-origin", "403-disabled", "409", "422", "429-retry-after", "429-no-header", "503", "500",
        "unexpected"])
def test_auth_error_messages(page: Page, mode, status, body, headers, expected):
    data = FakeData(page)
    mock_me(page, 401)
    record(page, f"**/auth/{mode}", status, body, headers)
    open_app(page)
    if mode == "register":
        page.get_by_test_id("auth-mode-toggle").click()
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(auth_error(page)).to_have_text(expected)
    expect(auth_error(page)).to_have_attribute("role", "alert")
    expect(page.locator("#error")).to_be_hidden()                    # 인증 오류는 auth-error 에만
    expect_signed_out(page)                                          # (8.0.0) 카드는 그대로, 앱은 숨긴 채
    expect(page.get_by_test_id("auth-password")).to_have_value("correct-pass-1")
    assert data.requests == []


def test_username_is_never_parsed_as_html(page: Page):
    FakeData(page)
    evil = '<img src=x onerror="window.__pwned=1">'
    mock_me(page, 200, {"id": 9, "username": evil, "csrf_token": "tok-x", "created_at": ME["created_at"]})
    open_app(page)
    user = page.get_by_test_id("current-user")
    expect(user).to_have_text(evil)
    assert user.locator("img").count() == 0
    page.get_by_test_id("account-menu").click()                       # 계정 패널의 아이디도 textContent
    expect(page.locator("#account-username")).to_have_text(evil)
    assert page.get_by_test_id("account-panel").locator("img").count() == 0
    assert page.evaluate("window.__pwned") is None


# ---------- 오래된 응답·무효화 ----------

def test_late_me_response_does_not_override_login(page: Page):
    data = FakeData(page)
    held = HeldRoute()
    page.route("**/auth/me", held)
    record(page, "**/auth/login", 200, LOGIN)
    open_app(page, idle=False)
    expect(panel(page)).to_be_visible()                               # /auth/me 가 오기 전에도 로그인할 수 있다
    assert data.requests == []                                        # 확인 전에는 데이터 요청 없음
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    fulfill(held.routes[0], 200, {"id": 1, "username": "olduser", "csrf_token": "tok-old"})   # 늦게 온 옛 응답
    page.wait_for_timeout(300)
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    add_todo(page, "토큰 확인")
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    assert data.csrf("POST") == ["tok-new"]
    assert data.paths("GET").count("/todos") == 2                     # 로그인 뒤 1번 + 추가 뒤 1번 (옛 응답은 불러오지 않음)


def test_data_api_401_returns_to_login_and_late_me_cannot_restore(page: Page):
    data = FakeData(page)
    data.fail[("POST", "/todos")] = 401                               # 세션 만료·다른 기기에서 비밀번호 변경 등
    held = HeldRoute()
    page.route("**/auth/me", held)
    record(page, "**/auth/login", 200, LOGIN)
    open_app(page, idle=False)
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    add_todo(page, "세션 만료")
    expect(auth_error(page)).to_have_text("다시 로그인하세요.")
    expect_signed_out(page)                                           # (8.0.0) 앱 영역을 숨긴다
    expect(page.get_by_test_id("auth-username")).to_be_focused()      # 포커스는 로그인 카드로
    expect(page.locator("#error")).to_be_hidden()                     # (8.0.0, 이전: run() 오류도 보였다) 안내는 카드에만
    fulfill(held.routes[0], 200, {"id": 1, "username": "olduser", "csrf_token": "tok-old"})
    page.wait_for_timeout(300)
    expect_signed_out(page)                                           # 옛 응답이 상태를 되살리지 않는다


# ---------- 계정 메뉴 (8.0.0) ----------

def open_account(page: Page) -> None:
    page.get_by_test_id("account-menu").click()
    expect(page.get_by_test_id("account-panel")).to_be_visible()


def test_account_panel_shows_info_and_keyboard_close(page: Page):
    FakeData(page)
    mock_me(page, 200, ME)
    open_app(page)
    menu, account = page.get_by_test_id("account-menu"), page.get_by_test_id("account-panel")
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(menu).to_have_attribute("aria-controls", "account-panel")
    expect(account).to_be_hidden()
    menu.focus()
    page.keyboard.press("Enter")
    expect(account).to_be_visible()
    expect(menu).to_have_attribute("aria-expanded", "true")
    expect(account).to_be_focused()                                   # 열면 패널로 (제목으로 읽힌다)
    expect(account).to_have_attribute("aria-labelledby", "account-title")
    expect(page.locator("#account-username")).to_have_text("alice")
    expect(page.get_by_test_id("account-created")).to_have_text("2026-11-01")   # 16:30 UTC → Asia/Seoul 다음 날
    page.keyboard.press("Tab")
    expect(page.locator("#account-close")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("password-current")).to_be_focused()
    page.keyboard.press("Escape")
    expect(account).to_be_hidden()
    expect(menu).to_be_focused()                                      # 닫으면 계정 버튼으로
    expect(menu).to_have_attribute("aria-expanded", "false")
    open_account(page)
    page.locator("#account-close").click()
    expect(account).to_be_hidden()
    expect(menu).to_be_focused()


def test_account_password_inputs_are_masked_with_autocomplete(page: Page):
    FakeData(page)
    mock_me(page, 200, ME)
    open_app(page)
    open_account(page)
    for testid, autocomplete in (("password-current", "current-password"), ("password-new", "new-password"),
                                 ("password-confirm", "new-password"), ("delete-password", "current-password")):
        field = page.get_by_test_id(testid)
        expect(field).to_have_attribute("type", "password")
        expect(field).to_have_attribute("autocomplete", autocomplete)
    for testid in ("password-error", "delete-error"):
        expect(page.get_by_test_id(testid)).to_have_attribute("role", "alert")
    expect(page.get_by_test_id("account-panel")).to_contain_text("할 일·템플릿은 공유 데이터라 삭제되지 않습니다.")


def fill_password_form(page: Page, current: str = "old-pass-123", new: str = "new-pass-456",
                       confirm: str | None = None) -> None:
    page.get_by_test_id("password-current").fill(current)
    page.get_by_test_id("password-new").fill(new)
    page.get_by_test_id("password-confirm").fill(new if confirm is None else confirm)


def test_password_confirm_mismatch_sends_nothing(page: Page):
    FakeData(page)
    mock_me(page, 200, ME)
    changes = record(page, "**/auth/password", 200, PASSWORD_OK)
    open_app(page)
    open_account(page)
    fill_password_form(page, confirm="different-789")
    page.get_by_test_id("password-submit").click()
    expect(page.get_by_test_id("password-error")).to_have_text("새 비밀번호 확인이 일치하지 않습니다.")
    expect(page.get_by_test_id("password-confirm")).to_be_focused()
    page.wait_for_timeout(200)
    assert changes == []


def test_password_change_success_uses_new_csrf_afterwards(page: Page):
    data = FakeData(page)
    mock_me(page, 200, ME)
    changes = record(page, "**/auth/password", 200, PASSWORD_OK)
    open_app(page)
    open_account(page)
    fill_password_form(page)
    page.get_by_test_id("password-submit").click()
    expect(page.get_by_test_id("status-message")).to_have_text(
        "비밀번호를 변경했습니다. 다른 기기의 로그인은 해제되었습니다.")
    assert changes == [{"method": "POST", "csrf": "tok-me",
                        "body": {"current_password": "old-pass-123", "new_password": "new-pass-456"}}]
    for testid in ("password-current", "password-new", "password-confirm"):
        expect(page.get_by_test_id(testid)).to_have_value("")         # 요청 뒤 비밀번호를 남기지 않는다
    expect(page.get_by_test_id("password-error")).to_be_hidden()
    expect_signed_in(page)
    add_todo(page, "변경 후")
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    assert data.csrf("POST") == ["tok-pw"]                            # 새 세션의 CSRF 토큰


@pytest.mark.parametrize("status, body, headers, expected", [
    (400, {"detail": "현재 비밀번호가 올바르지 않습니다"}, None, "현재 비밀번호가 올바르지 않습니다."),
    (403, {"detail": "CSRF 토큰이 올바르지 않습니다"}, None,
     "보안 확인(출처·CSRF)에 실패했습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요."),
    (409, {"detail": "다른 요청과 충돌했습니다. 다시 시도해 주세요"}, None, "다른 요청과 충돌했습니다. 다시 시도해 주세요."),
    (422, {"detail": [{"loc": ["body", "new_password"], "msg": "Value error, 비밀번호는 8~128자여야 합니다",
                       "type": "value_error"}]}, None,
     "새 비밀번호는 8~128자이고 현재 비밀번호와 달라야 합니다. (입력값을 확인해 주세요: Value error, 비밀번호는 8~128자여야 합니다)"),
    (429, {"detail": "시도 횟수를 초과했습니다"}, {"Retry-After": "600"}, "시도 횟수를 초과했습니다. 약 10분 후 다시 시도해 주세요."),
    (500, {"detail": "DETAIL: secret hash"}, None, "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."),
    (503, {"detail": "pool"}, None, "요청이 많아 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."),
], ids=["400", "403", "409", "422", "429", "500", "503"])
def test_password_change_error_messages(page: Page, status, body, headers, expected):
    data = FakeData(page)
    mock_me(page, 200, ME)
    record(page, "**/auth/password", status, body, headers)
    open_app(page)
    open_account(page)
    fill_password_form(page)
    page.get_by_test_id("password-submit").click()
    expect(page.get_by_test_id("password-error")).to_have_text(expected)
    expect(page.get_by_test_id("password-error")).not_to_contain_text("secret")
    expect(page.get_by_test_id("password-current")).to_have_value("")   # 실패해도 비밀번호를 남기지 않는다
    expect(page.locator("#error")).to_be_hidden()
    expect_signed_in(page)                                            # 로그인 상태·토큰은 그대로
    page.locator("#account-close").click()
    add_todo(page, "실패 후")
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    assert data.csrf("POST") == ["tok-me"]


def test_password_change_401_returns_to_login(page: Page):
    data = FakeData(page)
    mock_me(page, 200, ME)
    record(page, "**/auth/password", 401, {"detail": "로그인이 필요합니다"})
    open_app(page)
    open_account(page)
    fill_password_form(page)
    page.get_by_test_id("password-submit").click()
    expect(auth_error(page)).to_have_text("다시 로그인하세요.")
    expect_signed_out(page)
    expect(page.get_by_test_id("account-panel")).to_be_hidden()
    expect(page.get_by_test_id("password-current")).to_have_value("")
    assert "POST" not in {m for m, _, _ in data.requests}


def test_delete_account_confirm_cancel_sends_nothing(page: Page):
    FakeData(page)
    mock_me(page, 200, ME)
    deletes = record(page, "**/auth/account/delete", 204)
    open_app(page)
    open_account(page)
    page.get_by_test_id("delete-password").fill("old-pass-123")
    messages: list[str] = []

    def dismiss(dialog) -> None:
        messages.append(dialog.message)
        dialog.dismiss()
    page.once("dialog", dismiss)
    page.get_by_test_id("delete-submit").click()
    page.wait_for_timeout(200)
    assert deletes == [] and len(messages) == 1
    assert "공유 데이터라 삭제되지 않습니다" in messages[0]
    expect_signed_in(page)


def test_delete_account_success_reloads_to_login(page: Page):
    FakeData(page)
    mock_me(page, 200, ME, then=[(200, ME), (401, None)])             # 패널 열 때 1번, 새로고침 뒤 401
    deletes = record(page, "**/auth/account/delete", 204)
    open_app(page)
    open_account(page)
    page.get_by_test_id("delete-password").fill("old-pass-123")
    page.evaluate("window.__beforeDelete = true")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_test_id("delete-submit").click()
    page.wait_for_function("window.__beforeDelete === undefined")    # 새로고침으로 화면 상태가 비었다
    assert deletes == [{"method": "POST", "csrf": "tok-me", "body": {"password": "old-pass-123"}}]
    expect_signed_out(page)


@pytest.mark.parametrize("status, body, expected", [
    (400, {"detail": "비밀번호가 올바르지 않습니다"}, "비밀번호가 올바르지 않습니다."),
    (409, {"detail": "지금은 탈퇴할 수 없는 계정입니다. 관리자에게 문의하세요"},
     "지금은 탈퇴할 수 없는 계정입니다. 관리자에게 문의하세요."),
    (409, {"detail": "다른 요청과 충돌했습니다. 다시 시도해 주세요"}, "다른 요청과 충돌했습니다. 다시 시도해 주세요."),
    (429, {"detail": "시도 횟수를 초과했습니다"}, "시도 횟수를 초과했습니다. 잠시 후 다시 시도해 주세요."),
], ids=["400", "409-blocked", "409-conflict", "429"])
def test_delete_account_error_messages(page: Page, status, body, expected):
    FakeData(page)
    mock_me(page, 200, ME)
    record(page, "**/auth/account/delete", status, body)
    open_app(page)
    open_account(page)
    page.get_by_test_id("delete-password").fill("old-pass-123")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_test_id("delete-submit").click()
    expect(page.get_by_test_id("delete-error")).to_have_text(expected)
    expect(page.get_by_test_id("delete-password")).to_have_value("")
    expect_signed_in(page)


def test_delete_account_401_returns_to_login(page: Page):
    FakeData(page)
    mock_me(page, 200, ME)
    record(page, "**/auth/account/delete", 401, {"detail": "로그인이 필요합니다"})
    open_app(page)
    open_account(page)
    page.get_by_test_id("delete-password").fill("old-pass-123")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_test_id("delete-submit").click()
    expect(auth_error(page)).to_have_text("다시 로그인하세요.")
    expect_signed_out(page)


# ---------- 기존 기능 보존 (가짜 데이터, DB 없음) ----------

def test_kanban_drag_still_works_behind_gate(page: Page):
    # R8-5: 레이아웃 정리 뒤에도 칸반 드래그가 동작한다(기본 창 크기). 실제 서버 검증은 test_ui.py(PostgreSQL)
    data = FakeData(page, [todo(1, "보고서", priority="높음"), todo(2, "끝난 일", completed=True,
                                                                   completed_at="2026-11-10T00:00:00+00:00")])
    mock_me(page, 200, ME)
    open_app(page)
    page.get_by_test_id("view-toggle").get_by_role("button", name="칸반").click()
    active = page.locator("[data-testid=kanban-column][data-status=active]")
    done = page.locator("[data-testid=kanban-column][data-status=completed]")
    active.get_by_test_id("kanban-card").filter(has_text="보고서").drag_to(done)
    expect(done.get_by_test_id("kanban-card")).to_have_count(2)
    assert data.csrf("PUT") == ["tok-me"] and data.todos[0]["completed"] is True


def test_v4_to_v8_testids_are_kept():
    # 기존 testid 는 모두 남는다(V8_SPEC §41.4) — 화면 파일에 선언(속성·dataset·JS 문자열)이 있는지 본다
    source = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    claude = (APP_DIR.parent / "CLAUDE.md").read_text(encoding="utf-8")
    block = claude.split("- **테스트용 선택자**", 1)[1].split("- (7.0.0) **Python DB**", 1)[0]
    listed = {t for line in block.splitlines() if re.match(r"\s*- v\d:", line)
              for t in re.findall(r"`([a-z0-9-]+)`", line) if not t.startswith("data-")}   # data-id 등 속성 이름 제외
    assert {"todo-item", "auth-panel", "account-menu", "app-area", "delete-error"} <= listed and len(listed) > 80
    missing = sorted(t for t in listed if f'"{t}"' not in source)
    assert missing == []
    assert 'id="auth-open"' not in source                               # 빠지는 것은 testid 가 아닌 이 id 하나뿐


# ---------- 화면·접근성 ----------

@pytest.fixture
def mobile_page(browser, base_url):
    context = browser.new_context(base_url=base_url, timezone_id="Asia/Seoul", viewport={"width": 375, "height": 800},
                                  has_touch=True, is_mobile=True)
    page = context.new_page()
    yield page
    context.close()


@pytest.mark.parametrize("me_status", [401, 200], ids=["login-card", "signed-in-account"])
def test_375px_has_no_horizontal_scroll(mobile_page: Page, me_status):
    FakeData(mobile_page, [todo(1, "모바일")])
    mock_me(mobile_page, me_status, ME if me_status == 200 else None)
    mobile_page.route("**/auth/login", lambda route: fulfill(route, 429, {"detail": "x"}, {"Retry-After": "900"}))
    mobile_page.route("**/auth/password", lambda route: fulfill(route, 400, {"detail": "x"}))
    open_app(mobile_page)
    if me_status == 401:
        fill_login(mobile_page, "a" * 30, "p" * 40)
        mobile_page.get_by_test_id("auth-submit").click()
        expect(auth_error(mobile_page)).to_be_visible()               # 오류 문구까지 보이는 상태로
    else:
        expect(mobile_page.get_by_test_id("current-user")).to_be_visible()
        assert mobile_page.evaluate(NO_HSCROLL)
        open_account(mobile_page)                                     # 계정 패널 폼도 세로로 쌓여 넘치지 않는다
        fill_password_form(mobile_page)
        mobile_page.get_by_test_id("password-submit").tap()
        expect(mobile_page.get_by_test_id("password-error")).to_be_visible()
        for testid in ("password-submit", "delete-submit", "account-menu", "logout"):
            box = mobile_page.get_by_test_id(testid).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= 375, testid
    assert mobile_page.evaluate(NO_HSCROLL)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_auth_panel_contrast(page: Page, scheme):
    FakeData(page)
    page.emulate_media(color_scheme=scheme)
    mock_me(page, 401)
    record(page, "**/auth/login", 401, {"detail": "x"})
    open_app(page)
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(auth_error(page)).to_be_visible()                          # 카드·오류 문구가 보이는 상태에서 문서 전체 검사
    assert page.evaluate(CONTRAST_JS) == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_contrast_when_signed_in_with_account_panel(page: Page, scheme):
    # (이전: 다크 로그인 상태만) 앱 영역 + 계정 패널 + 두 오류 문구 + 주요·위험 버튼이 보이는 상태
    FakeData(page, [todo(1, "대비 확인")])
    page.emulate_media(color_scheme=scheme)
    mock_me(page, 200, ME)
    record(page, "**/auth/password", 400, {"detail": "x"})
    record(page, "**/auth/account/delete", 400, {"detail": "x"})
    open_app(page)
    expect(page.get_by_test_id("current-user")).to_be_visible()
    open_account(page)
    fill_password_form(page)
    page.get_by_test_id("password-submit").click()
    expect(page.get_by_test_id("password-error")).to_be_visible()
    page.get_by_test_id("delete-password").fill("x")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_test_id("delete-submit").click()
    expect(page.get_by_test_id("delete-error")).to_be_visible()
    assert page.evaluate(CONTRAST_JS) == []


def test_keyboard_only_login_and_aria(page: Page):
    FakeData(page)
    page.emulate_media(color_scheme="light")
    mock_me(page, 401)
    logins = record(page, "**/auth/login", 200, LOGIN)
    open_app(page)
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("theme-select")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("auth-username")).to_be_focused()     # (8.0.0) 카드가 펼쳐져 있어 바로 아이디
    page.keyboard.type("alice")
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("auth-password")).to_be_focused()
    page.keyboard.type("correct-pass-1")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    assert len(logins) == 1
    expect(page.get_by_label("아이디", exact=True)).to_have_attribute("data-testid", "auth-username")
    expect(page.get_by_label("비밀번호", exact=True)).to_have_attribute("data-testid", "auth-password")
    expect(panel(page)).to_have_attribute("aria-labelledby", "auth-title")
    expect(page.get_by_test_id("logout")).to_have_attribute("aria-label", "로그아웃")
    page.get_by_test_id("theme-select").focus()                       # (8.0.0) 로그인 뒤 헤더 순서: 테마 → 계정 → 로그아웃
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("account-menu")).to_be_focused()
    page.keyboard.press("Tab")
    logout = page.get_by_test_id("logout")
    expect(logout).to_be_focused()
    outline = logout.evaluate("e => [getComputedStyle(e).outlineStyle, getComputedStyle(e).outlineWidth]")
    assert outline == ["solid", "3px"]                                # 키보드 포커스 표시(기존 :focus-visible)
