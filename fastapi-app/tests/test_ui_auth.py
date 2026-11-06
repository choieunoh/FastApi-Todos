"""(7.0.0 Phase 2-G) 로그인·가입 UI — DB 없이 도는 Playwright 테스트.

- `/auth/*` 응답은 `page.route` 로 흉내 낸다(실제 PostgreSQL 인증은 검증하지 않는다 — Phase 4 의 E2E 몫).
- 데이터 경로(`/todos` 등)는 conftest 의 임시 JSON 서버를 그대로 쓴다. 과도기 정책(V7_SPEC §30): 로그인하지 않아도
  v6 앱 영역은 보이고, 시작 시 `/auth/me` 401 은 `#error` 에 오류로 나오지 않는다.
- 보류 응답(`HeldRoute`)으로 늦게 오는 `/auth/me` 를 재현해 오래된 응답이 최신 인증 상태를 덮지 않는지 본다.
- 패널은 기본으로 접혀 있다(진입 버튼 `#auth-open` 만 보임). 폼을 쓰는 테스트는 `open_auth` 로 먼저 펼친다.
"""
import json

import pytest
from playwright.sync_api import Page, Route, expect

from test_ui import CONTRAST_JS                  # 기존 명도 대비 검사(문서 전체의 보이는 글자)를 그대로 쓴다

ME = {"id": 7, "username": "alice", "csrf_token": "tok-me"}
LOGIN = {"id": 7, "username": "alice", "csrf_token": "tok-new"}
NO_HSCROLL = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"


def fulfill(route: Route, status: int, body: object = None, headers: dict | None = None) -> None:
    route.fulfill(status=status, content_type="application/json", headers=headers or {},
                  body="" if body is None else json.dumps(body))


def mock_me(page: Page, status: int = 401, body: object = None) -> list:
    # /auth/me 를 고정 응답으로 — 호출 기록을 돌려준다
    calls: list = []

    def handle(route: Route) -> None:
        calls.append(route.request.headers.get("x-csrf-token"))
        fulfill(route, status, body if body is not None else ({"detail": "로그인이 필요합니다"} if status == 401 else ME))
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


def spy_todos(page: Page, status_for_post: int | None = None) -> list:
    # /todos 요청의 메서드·CSRF 헤더를 기록하고, 실제 임시 JSON 서버로 넘긴다(status_for_post 를 주면 POST 만 그 상태로)
    seen: list = []

    def handle(route: Route) -> None:
        seen.append((route.request.method, route.request.headers.get("x-csrf-token")))
        if status_for_post is not None and route.request.method == "POST":
            fulfill(route, status_for_post, {"detail": "로그인이 필요합니다"})
        else:
            route.continue_()
    page.route("**/todos", handle)
    return seen


def open_app(page: Page, *, idle: bool = True) -> None:
    page.goto("/")
    expect(page.locator("#stat-total")).not_to_have_text("", timeout=5000)
    if idle:                                                       # 보류 응답이 있으면 networkidle 이 오지 않는다
        page.wait_for_load_state("networkidle")


def panel(page: Page):
    return page.get_by_test_id("auth-panel")


def auth_error(page: Page):
    return page.get_by_test_id("auth-error")


def auth_open(page: Page):
    return page.locator("#auth-open")


def open_auth(page: Page) -> None:
    # 접힌 패널을 진입 버튼으로 펼친다 (이미 펼쳐져 있으면 그대로)
    if auth_open(page).get_attribute("aria-expanded") != "true":
        auth_open(page).click()
    expect(page.get_by_test_id("auth-username")).to_be_visible()


def fill_login(page: Page, username: str = "alice", password: str = "correct-pass-1") -> None:
    open_auth(page)
    page.get_by_test_id("auth-username").fill(username)
    page.get_by_test_id("auth-password").fill(password)


def add_todo(page: Page, title: str) -> None:
    page.locator("#title").fill(title)
    page.get_by_role("button", name="Add To-Do").click()


# ---------- 시작 상태 (과도기: 앱 영역은 그대로) ----------

def test_me_401_shows_panel_keeps_app_and_no_error(page: Page, seed):
    seed([{"id": 1, "title": "기존 할 일", "description": "", "completed": False}])
    mock_me(page, 401)
    open_app(page)
    expect(panel(page)).to_be_visible()
    expect(page.locator("#error")).to_be_hidden()                  # 시작 401 은 오류가 아니다
    expect(auth_error(page)).to_be_hidden()
    expect(page.get_by_test_id("current-user")).to_be_hidden()
    expect(page.get_by_test_id("logout")).to_be_hidden()
    expect(page.get_by_test_id("todo-item")).to_have_count(1)        # v6 앱 영역·목록은 그대로
    expect(page.locator("#todo-form")).to_be_visible()
    expect(auth_open(page)).to_be_visible()                          # 접힌 로그인 진입점은 늘 보인다
    expect(auth_open(page)).to_have_attribute("aria-expanded", "false")
    expect(auth_open(page)).to_have_attribute("aria-controls", "auth-form-area")
    expect(page.get_by_test_id("auth-username")).to_be_hidden()      # 기본은 접혀 있다
    expect(page.get_by_test_id("auth-submit")).to_be_hidden()
    auth_open(page).click()
    expect(auth_open(page)).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_test_id("auth-username")).to_be_visible()
    expect(page.get_by_test_id("auth-username")).to_be_focused()     # 펼치면 아이디 입력란으로
    auth_open(page).click()                                          # 다시 누르면 접힌다
    expect(auth_open(page)).to_have_attribute("aria-expanded", "false")
    expect(page.get_by_test_id("auth-username")).to_be_hidden()


def test_collapsed_form_is_skipped_by_tab(page: Page, seed):
    seed([])
    mock_me(page, 401)
    open_app(page)
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("theme-select")).to_be_focused()       # 기존 순서: 테마 → 알림 종
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("notification-bell")).to_be_focused()
    page.keyboard.press("Tab")
    expect(auth_open(page)).to_be_focused()                          # 그다음 로그인 진입 버튼
    page.keyboard.press("Tab")
    expect(page.locator("#title")).to_be_focused()                    # 접힌 입력란·버튼은 건너뛰고 할 일 입력으로


def test_me_200_shows_user_and_logout(page: Page, seed):
    seed([])
    mock_me(page, 200, ME)
    open_app(page)
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    expect(page.get_by_test_id("logout")).to_be_visible()
    expect(panel(page)).to_be_hidden()
    expect(page.locator("#error")).to_be_hidden()


def test_me_500_does_not_block_list(page: Page, seed):
    seed([{"id": 1, "title": "목록은 그대로", "description": "", "completed": False}])
    mock_me(page, 500, {"detail": "internal secret detail"})
    open_app(page)
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    expect(page.locator("#error")).to_be_hidden()
    expect(auth_error(page)).to_have_text("로그인 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    expect(auth_error(page)).not_to_contain_text("secret")
    expect(page.get_by_test_id("auth-username")).to_be_hidden()      # 폼은 접힌 채 안내만 보인다


# ---------- 폼 ----------

def test_mode_toggle_switches_title_button_and_autocomplete(page: Page, seed):
    seed([])
    mock_me(page, 401)
    open_app(page)
    expect(page.get_by_test_id("auth-mode-toggle")).to_be_hidden()   # 접힌 상태에서는 전환 버튼도 숨김
    open_auth(page)
    toggle, submit = page.get_by_test_id("auth-mode-toggle"), page.get_by_test_id("auth-submit")
    password = page.get_by_test_id("auth-password")
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


def test_register_success_uses_register_endpoint(page: Page, seed):
    seed([])
    mock_me(page, 401)
    logins = record(page, "**/auth/login", 200, LOGIN)
    registers = record(page, "**/auth/register", 201, {"id": 8, "username": "newbie", "csrf_token": "tok-reg"})
    open_app(page)
    open_auth(page)
    page.get_by_test_id("auth-mode-toggle").click()
    fill_login(page, "newbie", "new-password-1")
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("current-user")).to_have_text("newbie")
    assert logins == [] and len(registers) == 1
    assert registers[0]["body"] == {"username": "newbie", "password": "new-password-1"}


def test_enter_submits_login_and_updates_state(page: Page, seed):
    seed([])
    mock_me(page, 401)
    logins = record(page, "**/auth/login", 200, LOGIN)
    open_app(page)
    fill_login(page)
    page.get_by_test_id("auth-password").press("Enter")
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    expect(page.get_by_test_id("logout")).to_be_visible()
    expect(panel(page)).to_be_hidden()
    expect(auth_error(page)).to_be_hidden()
    expect(page.get_by_test_id("auth-password")).to_have_value("")   # 비밀번호 칸을 비운다
    expect(auth_open(page)).to_be_hidden()                           # 로그인하면 진입점도 숨긴다
    assert len(logins) == 1 and logins[0]["body"] == {"username": "alice", "password": "correct-pass-1"}


def test_double_submit_sends_one_request(page: Page, seed):
    seed([])
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

def test_post_after_login_carries_csrf_and_get_does_not(page: Page, seed):
    seed([])
    mock_me(page, 401)
    record(page, "**/auth/login", 200, LOGIN)
    todos = spy_todos(page)
    open_app(page)
    add_todo(page, "로그인 전")                                       # 로그인 전 POST — 헤더 없음
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    add_todo(page, "로그인 후")
    expect(page.get_by_test_id("todo-item")).to_have_count(2)
    posts = [csrf for method, csrf in todos if method == "POST"]
    assert posts == [None, "tok-new"]                                 # 로그인 뒤 POST 에만 받은 토큰
    assert {csrf for method, csrf in todos if method == "GET"} == {None}   # GET 에는 붙이지 않는다


def test_login_and_register_never_carry_old_csrf(page: Page, seed):
    seed([])
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


def test_logout_success_sends_csrf_and_reloads(page: Page, seed):
    seed([])
    me_calls = mock_me(page, 200, ME)
    logouts = record(page, "**/auth/logout", 204)
    open_app(page)
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    page.evaluate("window.__beforeLogout = true")
    page.get_by_test_id("logout").click()
    page.wait_for_function("window.__beforeLogout === undefined")    # 새로고침으로 화면 상태가 비었다
    assert [r["csrf"] for r in logouts] == ["tok-me"]
    expect(page.get_by_test_id("current-user")).to_have_text("alice")  # 새로 불러온 /auth/me (흉내 응답) 결과
    assert len(me_calls) == 2


def test_logout_403_shows_error_and_keeps_state(page: Page, seed):
    seed([])
    mock_me(page, 200, ME)
    record(page, "**/auth/logout", 403, {"detail": "CSRF 토큰이 올바르지 않습니다"})
    open_app(page)
    page.evaluate("window.__beforeLogout = true")
    page.get_by_test_id("logout").click()
    expect(page.locator("#error")).to_contain_text("로그아웃 실패")
    expect(page.get_by_test_id("current-user")).to_have_text("alice")  # 상태를 지우지 않는다
    expect(page.get_by_test_id("logout")).to_be_visible()
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
def test_auth_error_messages(page: Page, seed, mode, status, body, headers, expected):
    seed([])
    mock_me(page, 401)
    record(page, f"**/auth/{mode}", status, body, headers)
    open_app(page)
    open_auth(page)
    if mode == "register":
        page.get_by_test_id("auth-mode-toggle").click()
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(auth_error(page)).to_have_text(expected)
    expect(auth_error(page)).to_have_attribute("role", "alert")
    expect(page.locator("#error")).to_be_hidden()                    # 인증 오류는 auth-error 에만
    expect(page.get_by_test_id("current-user")).to_be_hidden()
    expect(page.get_by_test_id("auth-password")).to_have_value("correct-pass-1")
    expect(auth_open(page)).to_have_attribute("aria-expanded", "true")   # 오류를 볼 수 있게 폼은 펼친 채


def test_username_is_never_parsed_as_html(page: Page, seed):
    seed([])
    evil = '<img src=x onerror="window.__pwned=1">'
    mock_me(page, 200, {"id": 9, "username": evil, "csrf_token": "tok-x"})
    open_app(page)
    user = page.get_by_test_id("current-user")
    expect(user).to_have_text(evil)
    assert user.locator("img").count() == 0
    assert page.evaluate("window.__pwned") is None


# ---------- 오래된 응답·무효화 ----------

def test_late_me_response_does_not_override_login(page: Page, seed):
    seed([])
    held = HeldRoute()
    page.route("**/auth/me", held)
    record(page, "**/auth/login", 200, LOGIN)
    todos = spy_todos(page)
    open_app(page, idle=False)
    expect(panel(page)).to_be_visible()                               # /auth/me 가 오기 전에도 로그인할 수 있다
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    fulfill(held.routes[0], 200, {"id": 1, "username": "olduser", "csrf_token": "tok-old"})   # 늦게 온 옛 응답
    page.wait_for_timeout(300)
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    add_todo(page, "토큰 확인")
    expect(page.get_by_test_id("todo-item")).to_have_count(1)
    assert [csrf for method, csrf in todos if method == "POST"] == ["tok-new"]


def test_data_api_401_invalidates_and_late_me_cannot_restore(page: Page, seed):
    seed([])
    held = HeldRoute()
    page.route("**/auth/me", held)
    record(page, "**/auth/login", 200, LOGIN)
    spy_todos(page, status_for_post=401)                              # Phase 3 이후의 데이터 API 401 을 흉내 낸다
    open_app(page, idle=False)
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    add_todo(page, "세션 만료")
    expect(auth_error(page)).to_have_text("다시 로그인하세요.")
    expect(panel(page)).to_be_visible()
    expect(auth_open(page)).to_have_attribute("aria-expanded", "true")   # 다시 로그인할 수 있게 펼친다
    expect(page.get_by_test_id("auth-username")).to_be_visible()
    expect(page.get_by_test_id("current-user")).to_be_hidden()
    expect(page.locator("#error")).to_be_visible()                    # 기존 run() 오류 표시도 그대로
    fulfill(held.routes[0], 200, {"id": 1, "username": "olduser", "csrf_token": "tok-old"})
    page.wait_for_timeout(300)
    expect(page.get_by_test_id("current-user")).to_be_hidden()        # 옛 응답이 상태를 되살리지 않는다
    expect(panel(page)).to_be_visible()


# ---------- 화면·접근성 ----------

@pytest.fixture
def mobile_page(browser, base_url):
    context = browser.new_context(base_url=base_url, timezone_id="Asia/Seoul", viewport={"width": 375, "height": 800},
                                  has_touch=True, is_mobile=True)
    page = context.new_page()
    yield page
    context.close()


@pytest.mark.parametrize("me_status", [401, 200], ids=["panel", "signed-in"])
def test_375px_has_no_horizontal_scroll(mobile_page: Page, seed, me_status):
    seed([{"id": 1, "title": "모바일", "description": "", "completed": False}])
    mock_me(mobile_page, me_status, ME if me_status == 200 else None)
    mobile_page.route("**/auth/login", lambda route: fulfill(route, 429, {"detail": "x"}, {"Retry-After": "900"}))
    open_app(mobile_page)
    if me_status == 401:
        fill_login(mobile_page, "a" * 30, "p" * 40)
        mobile_page.get_by_test_id("auth-submit").click()
        expect(auth_error(mobile_page)).to_be_visible()               # 오류 문구까지 보이는 상태로
    else:
        expect(mobile_page.get_by_test_id("current-user")).to_be_visible()
    assert mobile_page.evaluate(NO_HSCROLL)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_auth_panel_contrast(page: Page, seed, scheme):
    seed([])
    page.emulate_media(color_scheme=scheme)
    mock_me(page, 401)
    record(page, "**/auth/login", 401, {"detail": "x"})
    open_app(page)
    fill_login(page)
    page.get_by_test_id("auth-submit").click()
    expect(auth_error(page)).to_be_visible()                          # 패널·오류 문구가 보이는 상태에서 문서 전체 검사
    assert page.evaluate(CONTRAST_JS) == []


def test_contrast_when_signed_in_dark(page: Page, seed):
    seed([])
    page.emulate_media(color_scheme="dark")
    mock_me(page, 200, ME)
    open_app(page)
    expect(page.get_by_test_id("current-user")).to_be_visible()
    assert page.evaluate(CONTRAST_JS) == []


def test_keyboard_only_login_and_aria(page: Page, seed):
    seed([])
    page.emulate_media(color_scheme="light")
    mock_me(page, 401)
    logins = record(page, "**/auth/login", 200, LOGIN)
    open_app(page)
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("theme-select")).to_be_focused()       # 기존 순서: 테마 → 알림 종
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("notification-bell")).to_be_focused()
    page.keyboard.press("Tab")
    expect(auth_open(page)).to_be_focused()                          # 그다음 로그인 진입 버튼 (DOM 순서 = 화면 순서)
    page.keyboard.press("Enter")
    expect(auth_open(page)).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_test_id("auth-username")).to_be_focused()     # 펼치면 아이디 입력란
    page.keyboard.type("alice")
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("auth-password")).to_be_focused()
    page.keyboard.type("correct-pass-1")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("current-user")).to_have_text("alice")
    assert len(logins) == 1
    expect(page.get_by_label("아이디")).to_have_attribute("data-testid", "auth-username")
    expect(page.get_by_label("비밀번호")).to_have_attribute("data-testid", "auth-password")
    expect(panel(page)).to_have_attribute("aria-labelledby", "auth-title")
    expect(page.get_by_test_id("logout")).to_have_attribute("aria-label", "로그아웃")
    page.get_by_test_id("theme-select").focus()                       # 로그인 뒤 헤더 순서: 테마 → 로그아웃
    page.keyboard.press("Tab")
    logout = page.get_by_test_id("logout")
    expect(logout).to_be_focused()
    outline = logout.evaluate("e => [getComputedStyle(e).outlineStyle, getComputedStyle(e).outlineWidth]")
    assert outline == ["solid", "3px"]                                # 키보드 포커스 표시(기존 :focus-visible)
