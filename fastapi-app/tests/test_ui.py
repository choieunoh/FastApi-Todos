import re

import pytest
from playwright.sync_api import Page, expect

TODAY = "2026-10-12"                                # 브라우저 시계를 이 날짜 정오로 고정한다
ITEM = "[data-testid=todo-item]"


def todo(id: int, title: str, **fields) -> dict:
    return {"id": id, "title": title, "description": "", "completed": False, **fields}


@pytest.fixture(autouse=True)
def fixed_clock(page: Page):
    page.clock.set_fixed_time(f"{TODAY}T12:00:00")


def open_app(page: Page) -> None:
    page.goto("/")
    expect(page.locator("#stat-total")).not_to_have_text("", timeout=5000)
    page.wait_for_load_state("networkidle")


def titles(page: Page) -> list[str]:
    texts = page.locator(f"{ITEM} .content > span:first-child").all_inner_texts()
    return [t.split(" — ")[0] for t in texts]


def item(page: Page, title: str):
    return page.locator(ITEM).filter(has_text=title)


def stats(page: Page) -> tuple[str, ...]:
    return tuple(page.get_by_test_id(f"stat-{k}").inner_text()
                 for k in ("total", "completed", "active", "overdue", "rate"))


# ---------- 태그 ----------

def test_b1_add_with_tags(page: Page, seed):
    open_app(page)
    page.locator("#title").fill("과제 제출")
    page.locator("#tags").fill("학교, 과제")
    page.get_by_role("button", name="Add To-Do").click()
    expect(item(page, "과제 제출").get_by_test_id("todo-tag")).to_have_text(["#학교", "#과제"])


def test_b2_tag_filter_select(page: Page, seed):
    seed([todo(1, "A", tags=["학교"]), todo(2, "B", tags=["과제"]), todo(3, "C", tags=["학교", "과제"])])
    open_app(page)
    page.get_by_test_id("tag-filter").select_option("학교")
    expect(page.locator(ITEM)).to_have_count(2)
    assert titles(page) == ["A", "C"]


def test_b3_tag_chip_click(page: Page, seed):
    seed([todo(1, "A", tags=["학교"]), todo(2, "B", tags=["과제"]), todo(3, "C", tags=["학교", "과제"])])
    open_app(page)
    item(page, "B").get_by_test_id("todo-tag").click()
    expect(page.get_by_test_id("tag-filter")).to_have_value("과제")
    expect(page.locator(ITEM)).to_have_count(2)
    assert titles(page) == ["B", "C"]


def test_b4_tag_search_status_and(page: Page, seed):
    seed([
        todo(1, "보고서 초안", tags=["학교"]),
        todo(2, "보고서 제출", tags=["학교"], completed=True),
        todo(3, "발표", tags=["학교"]),
        todo(4, "보고서 정리", tags=["회사"]),
    ])
    open_app(page)
    page.get_by_test_id("tag-filter").select_option("학교")
    page.locator("#search").fill("보고서")
    page.locator("[data-filter=active]").click()
    expect(page.locator(ITEM)).to_have_count(1)
    assert titles(page) == ["보고서 초안"]


# ---------- 수정 폼 ----------

FULL = todo(1, "원래 제목", description="원래 설명", priority="높음", due_date="2026-10-20", tags=["a", "b"])


def test_b5_edit_form_prefilled(page: Page, seed):
    seed([FULL])
    open_app(page)
    item(page, "원래 제목").get_by_role("button", name="Edit").click()
    form = page.get_by_test_id("edit-form")
    expect(form).to_be_visible()
    expect(form.get_by_label("제목")).to_have_value("원래 제목")
    expect(form.get_by_label("설명")).to_have_value("원래 설명")
    expect(form.get_by_label("우선순위")).to_have_value("높음")
    expect(form.get_by_label("마감일")).to_have_value("2026-10-20")
    expect(form.get_by_label("태그")).to_have_value("a, b")


def test_b6_edit_save(page: Page, seed):
    seed([FULL])
    open_app(page)
    item(page, "원래 제목").get_by_role("button", name="Edit").click()
    form = page.get_by_test_id("edit-form")
    form.get_by_label("제목").fill("새 제목")
    form.get_by_label("우선순위").select_option("낮음")
    form.get_by_label("태그").fill("x, y")
    page.get_by_test_id("edit-save").click()
    expect(form).to_have_count(0)
    expect(item(page, "새 제목")).to_contain_text("우선순위: 낮음")
    page.reload()
    expect(item(page, "새 제목").get_by_test_id("todo-tag")).to_have_text(["#x", "#y"])
    expect(item(page, "새 제목")).to_contain_text("마감일: 2026-10-20")


def test_b7_edit_cancel(page: Page, seed):
    seed([FULL])
    open_app(page)
    item(page, "원래 제목").get_by_role("button", name="Edit").click()
    page.get_by_test_id("edit-form").get_by_label("제목").fill("버릴 제목")
    page.get_by_test_id("edit-cancel").click()
    expect(page.get_by_test_id("edit-form")).to_have_count(0)
    assert titles(page) == ["원래 제목"]


def test_b8_edit_blank_title_shows_error(page: Page, seed):
    seed([FULL])
    open_app(page)
    item(page, "원래 제목").get_by_role("button", name="Edit").click()
    page.get_by_test_id("edit-form").get_by_label("제목").fill("   ")
    page.get_by_test_id("edit-save").click()
    expect(page.locator("#error")).to_be_visible()
    expect(page.get_by_test_id("edit-form")).to_be_visible()


def test_b9_no_prompt_dialog(page: Page, seed):
    seed([FULL])
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    open_app(page)
    item(page, "원래 제목").get_by_role("button", name="Edit").click()
    page.get_by_test_id("edit-form").get_by_label("제목").fill("수정")
    page.get_by_test_id("edit-save").click()
    expect(page.get_by_test_id("edit-form")).to_have_count(0)
    assert dialogs == []


def test_b10_single_edit_form(page: Page, seed):
    seed([todo(1, "첫째"), todo(2, "둘째")])
    open_app(page)
    item(page, "첫째").get_by_role("button", name="Edit").click()
    item(page, "둘째").get_by_role("button", name="Edit").click()
    expect(page.get_by_test_id("edit-form")).to_have_count(1)
    expect(page.get_by_test_id("edit-form").get_by_label("제목")).to_have_value("둘째")


# ---------- 통계 ----------

FOUR = [
    todo(1, "완료됨", completed=True),
    todo(2, "기한 초과", due_date="2026-10-01"),
    todo(3, "보통1", tags=["t"]),
    todo(4, "보통2"),
]


def test_b11_stats(page: Page, seed):
    seed(FOUR)
    open_app(page)
    assert stats(page) == ("4", "1", "3", "1", "25%")


def test_b12_stats_update_on_toggle(page: Page, seed):
    seed(FOUR)
    open_app(page)
    item(page, "보통1").locator("input[type=checkbox]").check()
    expect(page.get_by_test_id("stat-completed")).to_have_text("2")
    assert stats(page) == ("4", "2", "2", "1", "50%")


def test_b13_stats_ignore_filters(page: Page, seed):
    seed(FOUR)
    open_app(page)
    page.locator("[data-filter=completed]").click()
    page.locator("#search").fill("보통")
    page.get_by_test_id("tag-filter").select_option("t")
    expect(page.locator(ITEM)).to_have_count(0)
    assert stats(page) == ("4", "1", "3", "1", "25%")


def test_b14_empty_rate_zero(page: Page, seed):
    open_app(page)
    assert stats(page) == ("0", "0", "0", "0", "0%")


# ---------- D-day / 시각 ----------

def test_b15_dday_labels(page: Page, seed):
    seed([todo(1, "삼일", due_date="2026-10-15"), todo(2, "오늘", due_date=TODAY), todo(3, "지남", due_date="2026-10-10")])
    open_app(page)
    expect(item(page, "삼일").get_by_test_id("dday")).to_have_text("D-3")
    expect(item(page, "오늘").get_by_test_id("dday")).to_have_text("오늘 마감")
    expect(item(page, "지남").get_by_test_id("dday")).to_have_text("D+2")


def test_b16_no_dday_for_completed_or_no_due(page: Page, seed):
    seed([todo(1, "완료", due_date="2026-10-15", completed=True), todo(2, "마감 없음")])
    open_app(page)
    expect(page.locator(ITEM)).to_have_count(2)
    expect(page.get_by_test_id("dday")).to_have_count(0)


def test_b17_created_and_completed_dates(page: Page, seed):
    open_app(page)
    page.locator("#title").fill("시각 확인")
    page.get_by_role("button", name="Add To-Do").click()
    row = item(page, "시각 확인")
    expect(row.locator(".meta")).to_contain_text(re.compile(r"생성: \d{4}-\d{2}-\d{2}"))
    expect(row.locator(".meta")).not_to_contain_text("완료:")
    row.locator("input[type=checkbox]").check()
    expect(row.locator(".meta")).to_contain_text(re.compile(r"완료: \d{4}-\d{2}-\d{2}"))
    row.locator("input[type=checkbox]").uncheck()
    expect(row.locator(".meta")).not_to_contain_text("완료:")


# ---------- 회귀 ----------

def test_b18_add_toggle_delete(page: Page, seed):
    open_app(page)
    page.locator("#title").fill("지울 항목")
    page.locator("#description").fill("설명")
    page.get_by_role("button", name="Add To-Do").click()
    row = item(page, "지울 항목")
    expect(row).to_have_count(1)

    row.locator("input[type=checkbox]").check()
    expect(row.locator(".content > span").first).to_have_class("done")

    page.once("dialog", lambda d: d.dismiss())
    row.get_by_role("button", name="Delete").click()
    expect(row).to_have_count(1)

    page.once("dialog", lambda d: d.accept())
    row.get_by_role("button", name="Delete").click()
    expect(row).to_have_count(0)


def test_b19_search_and_status_filters(page: Page, seed):
    seed([todo(1, "장보기", description="우유"), todo(2, "운동", completed=True), todo(3, "독서", description="소설")])
    open_app(page)

    page.locator("#search").fill("우유")                 # 설명으로 검색
    assert titles(page) == ["장보기"]
    page.locator("#search").fill("운동")                 # 제목으로 검색
    assert titles(page) == ["운동"]
    page.locator("#search").fill("")

    for name, expected in (("active", ["장보기", "독서"]), ("completed", ["운동"]), ("all", ["장보기", "운동", "독서"])):
        button = page.locator(f"[data-filter={name}]")
        button.click()
        assert titles(page) == expected
        expect(button).to_have_class("active")
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(page.locator("[data-filter].active")).to_have_count(1)


def test_b20_sorting(page: Page, seed):
    seed([
        todo(1, "낮음", priority="낮음", due_date="2026-10-20"),
        todo(2, "마감 없음", priority="높음"),
        todo(3, "보통", due_date="2026-10-15"),
    ])
    open_app(page)
    page.locator("#sort").select_option("priority")
    assert titles(page) == ["마감 없음", "보통", "낮음"]
    page.locator("#sort").select_option("due-date")
    assert titles(page) == ["보통", "낮음", "마감 없음"]


def test_b21_overdue_style(page: Page, seed):
    seed([todo(1, "늦음", due_date="2026-10-01"), todo(2, "늦었지만 완료", due_date="2026-10-01", completed=True)])
    open_app(page)
    late = item(page, "늦음").locator(".content > span").first
    expect(late).to_have_class("overdue")
    expect(late).to_contain_text("⚠ 기한 초과")
    expect(item(page, "늦었지만 완료")).not_to_contain_text("기한 초과")


def test_b22_version(page: Page, seed):
    open_app(page)
    expect(page.get_by_text("Version 5.0.0")).to_be_visible()


# ---------- 5.0.0 1단계: 즐겨찾기 ----------

def star(page: Page, title: str):
    return item(page, title).get_by_test_id("favorite-toggle")


def test_favorite_toggle_persists(page: Page, seed):
    seed([todo(1, "별표 대상")])
    open_app(page)
    expect(star(page, "별표 대상")).to_have_text("☆")
    expect(star(page, "별표 대상")).to_have_attribute("aria-pressed", "false")

    star(page, "별표 대상").click()
    expect(star(page, "별표 대상")).to_have_text("★")
    expect(star(page, "별표 대상")).to_have_attribute("aria-pressed", "true")
    page.reload()
    expect(star(page, "별표 대상")).to_have_text("★")

    star(page, "별표 대상").click()
    expect(star(page, "별표 대상")).to_have_text("☆")
    page.reload()
    expect(star(page, "별표 대상")).to_have_text("☆")


def test_favorite_filter_and_combination(page: Page, seed):
    seed([
        todo(1, "보고서 초안", tags=["학교"], favorite=True),
        todo(2, "보고서 제출", tags=["학교"], favorite=True, completed=True),
        todo(3, "보고서 정리", tags=["학교"]),
        todo(4, "발표", tags=["학교"], favorite=True),
        todo(5, "보고서 회의", tags=["회사"], favorite=True),
    ])
    open_app(page)
    favorite_filter = page.get_by_test_id("favorite-filter")

    favorite_filter.click()
    expect(favorite_filter).to_have_attribute("aria-pressed", "true")
    expect(favorite_filter).to_have_class("active")
    assert titles(page) == ["보고서 초안", "보고서 제출", "발표", "보고서 회의"]

    # 검색 + 상태 + 태그 + 즐겨찾기 AND
    page.get_by_test_id("tag-filter").select_option("학교")
    page.locator("#search").fill("보고서")
    page.locator("[data-filter=active]").click()
    assert titles(page) == ["보고서 초안"]
    expect(page.locator("[data-filter].active")).to_have_count(1)  # 상태 필터 선택은 하나뿐

    favorite_filter.click()                              # 끄면 나머지 필터만 남는다
    expect(favorite_filter).to_have_attribute("aria-pressed", "false")
    assert titles(page) == ["보고서 초안", "보고서 정리"]


def test_favorite_filter_keeps_stats(page: Page, seed):
    seed([todo(1, "A", favorite=True), todo(2, "B", completed=True), todo(3, "C")])
    open_app(page)
    page.get_by_test_id("favorite-filter").click()
    expect(page.locator(ITEM)).to_have_count(1)
    assert stats(page) == ("3", "1", "2", "0", "33%")


def test_favorite_kept_after_edit_and_toggle(page: Page, seed):
    seed([todo(1, "유지 확인", favorite=True)])
    open_app(page)
    item(page, "유지 확인").get_by_role("button", name="Edit").click()
    page.get_by_test_id("edit-form").get_by_label("제목").fill("수정 후")
    page.get_by_test_id("edit-save").click()
    expect(star(page, "수정 후")).to_have_text("★")

    item(page, "수정 후").locator("input[type=checkbox]").check()
    expect(page.get_by_test_id("stat-completed")).to_have_text("1")
    page.reload()
    expect(star(page, "수정 후")).to_have_text("★")


# ---------- 5.0.0 2단계: 삭제 실행 취소 ----------

def delete_item(page: Page, title: str, accept: bool = True) -> None:
    page.once("dialog", lambda d: d.accept() if accept else d.dismiss())
    item(page, title).get_by_role("button", name="Delete").click()


def test_undo_restores_deleted_item(page: Page, seed):
    seed([todo(1, "첫째"), todo(2, "지울 항목", favorite=True, completed=True, tags=["t"]), todo(3, "셋째")])
    open_app(page)
    toast = page.get_by_test_id("undo-toast")
    expect(toast).to_be_hidden()

    delete_item(page, "지울 항목")
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("1개 삭제됨")
    assert titles(page) == ["첫째", "셋째"]
    assert stats(page) == ("2", "0", "2", "0", "0%")

    page.get_by_test_id("undo-button").click()
    expect(toast).to_be_hidden()
    expect(page.locator(ITEM)).to_have_count(3)
    assert titles(page) == ["첫째", "지울 항목", "셋째"]          # 원래 자리로 돌아온다
    assert stats(page) == ("3", "1", "2", "0", "33%")
    expect(star(page, "지울 항목")).to_have_text("★")
    expect(item(page, "지울 항목").get_by_test_id("todo-tag")).to_have_text(["#t"])
    page.reload()
    assert titles(page) == ["첫째", "지울 항목", "셋째"]


def test_no_toast_when_confirm_dismissed(page: Page, seed):
    seed([todo(1, "남길 항목")])
    open_app(page)
    delete_item(page, "남길 항목", accept=False)
    expect(page.locator(ITEM)).to_have_count(1)
    expect(page.get_by_test_id("undo-toast")).to_be_hidden()


def test_toast_hides_after_5_seconds(page: Page, seed):
    seed([todo(1, "A"), todo(2, "B")])
    open_app(page)
    delete_item(page, "A")
    toast = page.get_by_test_id("undo-toast")
    expect(toast).to_be_visible()
    page.wait_for_timeout(4000)
    expect(toast).to_be_visible()                                 # 5초 전에는 보인다
    expect(toast).to_be_hidden(timeout=2500)                      # 5초가 지나면 사라진다


def test_new_delete_replaces_toast(page: Page, seed):
    seed([todo(1, "A"), todo(2, "B"), todo(3, "C")])
    open_app(page)
    delete_item(page, "A")
    expect(page.locator(ITEM)).to_have_count(2)
    page.wait_for_timeout(3000)
    delete_item(page, "B")
    expect(page.locator(ITEM)).to_have_count(1)
    page.wait_for_timeout(3000)                                   # 첫 삭제로부터는 5초가 지났다
    expect(page.get_by_test_id("undo-toast")).to_be_visible()     # 새 삭제로 시간이 다시 잰다
    page.get_by_test_id("undo-button").click()
    expect(page.locator(ITEM)).to_have_count(2)
    assert titles(page) == ["B", "C"]                             # 마지막 삭제만 되돌린다


def test_undo_failure_shows_error(page: Page, seed):
    seed([todo(1, "A")])
    open_app(page)
    page.route("**/todos/restore", lambda route: route.fulfill(status=404, body='{"detail":"복구할 수 없는 항목입니다"}'))
    delete_item(page, "A")
    page.get_by_test_id("undo-button").click()
    expect(page.locator("#error")).to_be_visible()
    expect(page.locator("#error")).to_contain_text("실행 취소 실패")
    expect(page.locator(ITEM)).to_have_count(0)
