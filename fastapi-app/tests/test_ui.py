import re

import pytest
from playwright.sync_api import Page, expect

TODAY = "2026-10-12"                                # 브라우저 시계를 이 날짜 정오로 고정한다
ITEM = "[data-testid=todo-item]"
DONE_BOX = "input.todo-check"                                       # 상위 할 일의 완료 체크박스


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
    item(page, "보통1").locator(DONE_BOX).check()
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
    row.locator(DONE_BOX).check()
    expect(row.locator(".meta")).to_contain_text(re.compile(r"완료: \d{4}-\d{2}-\d{2}"))
    row.locator(DONE_BOX).uncheck()
    expect(row.locator(".meta")).not_to_contain_text("완료:")


# ---------- 회귀 ----------

def test_b18_add_toggle_delete(page: Page, seed):
    open_app(page)
    page.locator("#title").fill("지울 항목")
    page.locator("#description").fill("설명")
    page.get_by_role("button", name="Add To-Do").click()
    row = item(page, "지울 항목")
    expect(row).to_have_count(1)

    row.locator(DONE_BOX).check()
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
    expect(page.get_by_text("Version 6.0.0")).to_be_visible()


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

    item(page, "수정 후").locator(DONE_BOX).check()
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


# ---------- 5.0.0 3단계: 일괄 작업 ----------

def pick(page: Page, *titles_: str) -> None:
    for title in titles_:
        item(page, title).get_by_test_id("bulk-select").check()


def bulk_count(page: Page):
    return page.get_by_test_id("bulk-bar").locator("#bulk-count")


def test_bulk_selection_count(page: Page, seed):
    seed([todo(1, "A"), todo(2, "B"), todo(3, "C")])
    open_app(page)
    expect(bulk_count(page)).to_have_text("0개 선택")
    for name in ("complete", "uncomplete", "delete"):
        expect(page.get_by_test_id(f"bulk-{name}")).to_be_disabled()

    pick(page, "A", "C")
    expect(bulk_count(page)).to_have_text("2개 선택")
    expect(item(page, "A")).to_have_class("selected")
    expect(page.get_by_test_id("bulk-complete")).to_be_enabled()
    expect(item(page, "A").locator(DONE_BOX)).not_to_be_checked()   # 선택은 완료와 별개

    item(page, "A").get_by_test_id("bulk-select").uncheck()
    expect(bulk_count(page)).to_have_text("1개 선택")


def test_bulk_select_all_visible_only(page: Page, seed):
    seed([todo(1, "A"), todo(2, "B", completed=True), todo(3, "C")])
    open_app(page)
    page.locator("[data-filter=active]").click()
    select_all = page.get_by_test_id("bulk-select-all")
    select_all.click()
    expect(bulk_count(page)).to_have_text("2개 선택")
    expect(select_all).to_have_text("선택 해제")

    page.locator("[data-filter=all]").click()                       # 가려져 있던 B 는 선택되지 않았다
    expect(item(page, "B").get_by_test_id("bulk-select")).not_to_be_checked()
    expect(bulk_count(page)).to_have_text("2개 선택")
    expect(select_all).to_have_text("보이는 항목 전체 선택")

    page.locator("[data-filter=active]").click()
    select_all.click()                                               # 모두 골라져 있으면 해제
    expect(bulk_count(page)).to_have_text("0개 선택")


def test_bulk_filter_change_drops_hidden_selection(page: Page, seed):
    seed([todo(1, "보고서", tags=["학교"]), todo(2, "운동", completed=True), todo(3, "발표", tags=["학교"])])
    open_app(page)
    pick(page, "보고서", "운동", "발표")
    page.locator("[data-filter=active]").click()
    expect(bulk_count(page)).to_have_text("2개 선택")
    page.locator("#search").fill("보고")
    expect(bulk_count(page)).to_have_text("1개 선택")
    page.locator("#search").fill("")
    expect(item(page, "발표").get_by_test_id("bulk-select")).not_to_be_checked()


def test_bulk_complete_and_uncomplete_update_stats(page: Page, seed):
    seed([todo(1, "사과", favorite=True), todo(2, "포도"), todo(3, "귤", completed=True), todo(4, "자두", due_date="2026-10-01")])
    open_app(page)
    assert stats(page) == ("4", "1", "3", "1", "25%")

    pick(page, "사과", "귤", "자두")
    page.get_by_test_id("bulk-complete").click()
    expect(page.get_by_test_id("stat-completed")).to_have_text("3")
    assert stats(page) == ("4", "3", "1", "0", "75%")
    expect(bulk_count(page)).to_have_text("0개 선택")
    expect(item(page, "사과").locator(DONE_BOX)).to_be_checked()
    expect(item(page, "사과").locator(".meta")).to_contain_text("완료:")
    expect(star(page, "사과")).to_have_text("★")                       # 다른 필드는 그대로

    pick(page, "사과", "포도")
    page.get_by_test_id("bulk-uncomplete").click()
    expect(page.get_by_test_id("stat-completed")).to_have_text("2")
    assert stats(page) == ("4", "2", "2", "0", "50%")
    expect(item(page, "사과").locator(".meta")).not_to_contain_text("완료:")
    page.reload()
    assert stats(page) == ("4", "2", "2", "0", "50%")


def test_bulk_delete_confirm_and_undo(page: Page, seed):
    seed([todo(1, "사과"), todo(2, "포도", favorite=True), todo(3, "귤"), todo(4, "자두", completed=True)])
    open_app(page)
    pick(page, "포도", "자두")

    page.once("dialog", lambda d: d.dismiss())                       # 취소하면 아무 일도 없다
    page.get_by_test_id("bulk-delete").click()
    expect(page.locator(ITEM)).to_have_count(4)
    expect(page.get_by_test_id("undo-toast")).to_be_hidden()
    expect(bulk_count(page)).to_have_text("2개 선택")

    messages = []
    page.once("dialog", lambda d: (messages.append(d.message), d.accept()))
    page.get_by_test_id("bulk-delete").click()
    expect(page.locator(ITEM)).to_have_count(2)
    assert messages == ["선택한 2개를 삭제할까요?"]
    assert titles(page) == ["사과", "귤"]
    assert stats(page) == ("2", "0", "2", "0", "0%")
    expect(page.get_by_test_id("undo-toast")).to_contain_text("2개 삭제됨")
    expect(bulk_count(page)).to_have_text("0개 선택")

    page.get_by_test_id("undo-button").click()
    expect(page.locator(ITEM)).to_have_count(4)
    assert titles(page) == ["사과", "포도", "귤", "자두"]
    assert stats(page) == ("4", "1", "3", "0", "25%")
    expect(star(page, "포도")).to_have_text("★")


def test_bulk_failure_shows_error_and_keeps_selection(page: Page, seed):
    seed([todo(1, "A"), todo(2, "B")])
    open_app(page)
    page.route("**/todos/bulk", lambda route: route.fulfill(status=404, body='{"detail":"없는 항목입니다: [2]"}'))
    pick(page, "A", "B")
    page.get_by_test_id("bulk-complete").click()
    expect(page.locator("#error")).to_contain_text("일괄 완료 실패")
    expect(bulk_count(page)).to_have_text("2개 선택")
    assert stats(page) == ("2", "0", "2", "0", "0%")


# ---------- 5.0.0 4단계: 하위 할 일 ----------

def sub(id: int, title: str, completed: bool = False) -> dict:
    return {"id": id, "title": title, "completed": completed}


def subtasks_of(page: Page, title: str):
    return item(page, title).get_by_test_id("subtask-item")


def progress(page: Page, title: str):
    return item(page, title).get_by_test_id("subtask-progress")


def expand(page: Page, title: str) -> None:
    toggle = item(page, title).get_by_test_id("subtask-toggle")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    expect(item(page, title).get_by_test_id("subtask-list")).to_be_visible()


def test_subtask_add_check_and_progress(page: Page, seed):
    seed([todo(1, "보고서"), todo(2, "다른 일")])
    open_app(page)
    row = item(page, "보고서")
    expect(row.get_by_test_id("subtask-list")).to_have_count(0)        # 기본은 접힘
    expect(progress(page, "보고서")).to_have_count(0)                   # 하위가 없으면 진행률 숨김
    expect(row.get_by_test_id("subtask-toggle")).to_have_text("▸ 하위 작업 (0)")

    expand(page, "보고서")
    add = row.get_by_test_id("subtask-add")
    add.fill("자료 조사")
    add.press("Enter")
    expect(subtasks_of(page, "보고서")).to_have_count(1)
    expect(row.get_by_test_id("subtask-add")).to_be_focused()          # 이어서 입력할 수 있다
    row.get_by_test_id("subtask-add").fill("초안 작성")
    row.get_by_role("button", name="추가").click()
    expect(subtasks_of(page, "보고서").locator("span")).to_have_text(["자료 조사", "초안 작성"])
    expect(progress(page, "보고서")).to_have_text("0/2 (0%)")

    row.get_by_label("자료 조사 완료").check()
    expect(progress(page, "보고서")).to_have_text("1/2 (50%)")
    expect(subtasks_of(page, "보고서").first.locator("span")).to_have_class("done")
    expect(row.locator(DONE_BOX)).not_to_be_checked()                  # 상위 완료와 연동하지 않는다
    assert stats(page) == ("2", "0", "2", "0", "0%")                    # 통계는 상위 기준 그대로

    row.get_by_label("초안 작성 완료").check()
    expect(progress(page, "보고서")).to_have_text("2/2 (100%)")
    expect(row.locator(DONE_BOX)).not_to_be_checked()
    assert stats(page) == ("2", "0", "2", "0", "0%")

    page.reload()                                                       # 새로고침하면 접히지만 데이터는 남는다
    expect(row.get_by_test_id("subtask-list")).to_have_count(0)
    expect(progress(page, "보고서")).to_have_text("2/2 (100%)")
    expect(row.get_by_test_id("subtask-toggle")).to_have_text("▸ 하위 작업 (2)")


def test_subtask_edit_title(page: Page, seed):
    seed([todo(1, "보고서", subtasks=[sub(1, "자료 조사", True), sub(2, "초안 작성")])])
    open_app(page)
    expand(page, "보고서")
    row = item(page, "보고서")

    row.get_by_role("button", name="자료 조사 수정").click()
    field = row.get_by_label("하위 작업 제목")
    expect(field).to_have_value("자료 조사")
    expect(field).to_be_focused()
    field.fill("논문 조사")
    field.press("Enter")
    expect(subtasks_of(page, "보고서").first).to_contain_text("논문 조사")
    expect(row.get_by_label("논문 조사 완료")).to_be_checked()            # 완료 상태는 그대로

    row.get_by_role("button", name="초안 작성 수정").click()                # 취소하면 원래 제목
    row.get_by_label("하위 작업 제목").fill("버릴 제목")
    row.get_by_role("button", name="취소").click()
    expect(subtasks_of(page, "보고서").nth(1)).to_contain_text("초안 작성")

    row.get_by_role("button", name="초안 작성 수정").click()                # Esc 도 취소
    row.get_by_label("하위 작업 제목").fill("버릴 제목")
    row.get_by_label("하위 작업 제목").press("Escape")
    expect(row.get_by_label("하위 작업 제목")).to_have_count(0)
    expect(subtasks_of(page, "보고서").nth(1)).to_contain_text("초안 작성")

    page.reload()
    expand(page, "보고서")
    expect(subtasks_of(page, "보고서").locator("span")).to_have_text(["논문 조사", "초안 작성"])


def test_subtask_single_edit_and_error(page: Page, seed):
    seed([todo(1, "보고서", subtasks=[sub(1, "자료 조사"), sub(2, "초안 작성")])])
    open_app(page)
    expand(page, "보고서")
    row = item(page, "보고서")
    row.get_by_role("button", name="자료 조사 수정").click()
    row.get_by_role("button", name="초안 작성 수정").click()
    expect(row.get_by_label("하위 작업 제목")).to_have_count(1)            # 한 번에 하나만
    expect(row.get_by_label("하위 작업 제목")).to_have_value("초안 작성")

    row.get_by_label("하위 작업 제목").fill("   ")
    row.get_by_role("button", name="저장").click()
    expect(page.locator("#error")).to_contain_text("하위 작업 수정 실패")
    expect(row.get_by_label("하위 작업 제목")).to_be_visible()             # 실패하면 입력란을 그대로 둔다


def delete_subtask(page: Page, todo_title: str, subtask_title: str, accept: bool = True) -> list[str]:
    messages = []
    page.once("dialog", lambda d: (messages.append(d.message), d.accept() if accept else d.dismiss()))
    item(page, todo_title).get_by_role("button", name=f"{subtask_title} 삭제").click()
    return messages


def test_subtask_delete_confirm_accept(page: Page, seed):
    seed([todo(1, "보고서", subtasks=[sub(1, "자료 조사", True), sub(3, "초안 작성")])])
    open_app(page)
    expand(page, "보고서")
    row = item(page, "보고서")
    expect(progress(page, "보고서")).to_have_text("1/2 (50%)")

    assert delete_subtask(page, "보고서", "자료 조사") == ["정말 삭제할까요?"]   # 상위 삭제와 같은 확인창
    expect(subtasks_of(page, "보고서")).to_have_count(1)
    expect(progress(page, "보고서")).to_have_text("0/1 (0%)")
    delete_subtask(page, "보고서", "초안 작성")
    expect(subtasks_of(page, "보고서")).to_have_count(0)
    expect(progress(page, "보고서")).to_have_count(0)
    page.reload()
    expect(row.get_by_test_id("subtask-toggle")).to_have_text("▸ 하위 작업 (0)")


def test_subtask_delete_confirm_dismiss(page: Page, seed):
    seed([todo(1, "보고서", subtasks=[sub(1, "자료 조사", True), sub(2, "초안 작성")])])
    open_app(page)
    expand(page, "보고서")
    requests = []
    page.on("request", lambda r: requests.append(f"{r.method} {r.url}"))

    assert delete_subtask(page, "보고서", "자료 조사", accept=False) == ["정말 삭제할까요?"]
    expect(subtasks_of(page, "보고서").locator("span")).to_have_text(["자료 조사", "초안 작성"])
    expect(progress(page, "보고서")).to_have_text("1/2 (50%)")
    expect(item(page, "보고서").get_by_test_id("subtask-list")).to_be_visible()   # 펼친 상태도 그대로
    expect(page.locator("#error")).to_be_hidden()
    assert requests == []                                                   # API 요청을 보내지 않는다

    page.reload()
    expect(progress(page, "보고서")).to_have_text("1/2 (50%)")
    expand(page, "보고서")
    expect(subtasks_of(page, "보고서").locator("span")).to_have_text(["자료 조사", "초안 작성"])


def test_subtask_add_errors(page: Page, seed):
    seed([todo(1, "가득", subtasks=[sub(i, f"s{i}") for i in range(1, 21)])])
    open_app(page)
    expand(page, "가득")
    row = item(page, "가득")
    row.get_by_test_id("subtask-add").fill("21번째")
    row.get_by_test_id("subtask-add").press("Enter")
    expect(page.locator("#error")).to_contain_text("하위 작업 추가 실패")
    expect(subtasks_of(page, "가득")).to_have_count(20)


def test_subtask_add_blank_title_error(page: Page, seed):
    seed([todo(1, "보고서")])
    open_app(page)
    expand(page, "보고서")
    page.get_by_test_id("subtask-add").fill("   ")
    page.get_by_test_id("subtask-add").press("Enter")
    expect(page.locator("#error")).to_contain_text("하위 작업 추가 실패")
    expect(subtasks_of(page, "보고서")).to_have_count(0)


def test_subtask_expanded_state_survives_rerender(page: Page, seed):
    seed([todo(1, "보고서", subtasks=[sub(1, "자료 조사")]), todo(2, "운동")])
    open_app(page)
    expand(page, "보고서")
    star(page, "보고서").click()                                        # 다른 동작으로 목록을 다시 그려도
    expect(star(page, "보고서")).to_have_text("★")
    expect(item(page, "보고서").get_by_test_id("subtask-list")).to_be_visible()
    page.locator("[data-filter=active]").click()
    expect(item(page, "보고서").get_by_test_id("subtask-list")).to_be_visible()
    expect(item(page, "운동").get_by_test_id("subtask-list")).to_have_count(0)
    item(page, "보고서").get_by_test_id("subtask-toggle").click()        # 다시 누르면 접힌다
    expect(item(page, "보고서").get_by_test_id("subtask-list")).to_have_count(0)


def test_subtasks_kept_through_edit_bulk_and_undo(page: Page, seed):
    seed([todo(1, "보고서", subtasks=[sub(1, "자료 조사", True), sub(2, "초안 작성")]), todo(2, "운동")])
    open_app(page)

    item(page, "보고서").get_by_role("button", name="Edit").click()       # 상위 수정
    page.get_by_test_id("edit-form").get_by_label("제목").fill("최종 보고서")
    page.get_by_test_id("edit-save").click()
    expect(progress(page, "최종 보고서")).to_have_text("1/2 (50%)")

    pick(page, "최종 보고서")                                              # 일괄 완료
    page.get_by_test_id("bulk-complete").click()
    expect(item(page, "최종 보고서").locator(DONE_BOX)).to_be_checked()
    expect(progress(page, "최종 보고서")).to_have_text("1/2 (50%)")        # 하위는 그대로

    delete_item(page, "최종 보고서")                                        # 삭제 → 실행 취소
    expect(page.locator(ITEM)).to_have_count(1)
    page.get_by_test_id("undo-button").click()
    expect(page.locator(ITEM)).to_have_count(2)
    expect(progress(page, "최종 보고서")).to_have_text("1/2 (50%)")
    expand(page, "최종 보고서")
    expect(subtasks_of(page, "최종 보고서").locator("span")).to_have_text(["자료 조사", "초안 작성"])


# ---------- 5.0.0 5단계: 반복 일정 ----------

def by_due(page: Page, title: str, due: str):
    # 다음 일정은 제목이 같으므로 마감일로 구분한다
    return page.locator(ITEM).filter(has_text=title).filter(has_text=f"마감일: {due}")


def badge(row):
    return row.get_by_test_id("recurrence-badge")


def test_recurrence_add_form(page: Page, seed):
    open_app(page)
    select = page.locator("#recurrence")
    expect(select.locator("option")).to_have_text(["반복 없음", "매일", "매주", "매월"])
    expect(select).to_have_value("none")

    page.locator("#title").fill("주간 회의")
    page.locator("#due-date").fill("2026-10-13")
    select.select_option("weekly")
    page.get_by_role("button", name="Add To-Do").click()
    expect(badge(item(page, "주간 회의"))).to_have_text("🔁 매주")
    expect(select).to_have_value("none")                                  # 추가 후 기본값으로 돌아간다

    page.locator("#title").fill("일반 할 일")
    page.get_by_role("button", name="Add To-Do").click()
    expect(page.locator(ITEM)).to_have_count(2)
    expect(badge(item(page, "일반 할 일"))).to_have_count(0)              # 반복 없음이면 배지 없음


def test_recurrence_without_due_date_shows_error(page: Page, seed):
    open_app(page)
    page.locator("#title").fill("마감 없는 반복")
    page.locator("#recurrence").select_option("daily")
    page.get_by_role("button", name="Add To-Do").click()
    expect(page.locator("#error")).to_contain_text("추가 실패")
    expect(page.locator(ITEM)).to_have_count(0)


def test_recurrence_edit_form_set_change_clear(page: Page, seed):
    seed([todo(1, "운동", due_date="2026-10-13")])
    open_app(page)
    row = item(page, "운동")
    expect(badge(row)).to_have_count(0)

    for value, label in (("monthly", "🔁 매월"), ("daily", "🔁 매일")):
        row.get_by_role("button", name="Edit").click()
        page.get_by_test_id("edit-form").get_by_label("반복").select_option(value)
        page.get_by_test_id("edit-save").click()
        expect(badge(row)).to_have_text(label)

    row.get_by_role("button", name="Edit").click()
    expect(page.get_by_test_id("edit-form").get_by_label("반복")).to_have_value("daily")   # 현재 값이 채워진다
    page.get_by_test_id("edit-form").get_by_label("반복").select_option("none")
    page.get_by_test_id("edit-save").click()
    expect(badge(row)).to_have_count(0)
    page.reload()
    expect(badge(row)).to_have_count(0)
    expect(page.locator(ITEM)).to_have_count(1)


def test_recurrence_edit_clearing_due_date_shows_error(page: Page, seed):
    seed([todo(1, "운동", due_date="2026-10-13", recurrence="weekly")])
    open_app(page)
    item(page, "운동").get_by_role("button", name="Edit").click()
    page.get_by_test_id("edit-form").get_by_label("마감일").fill("")
    page.get_by_test_id("edit-save").click()
    expect(page.locator("#error")).to_contain_text("수정 실패")
    expect(page.get_by_test_id("edit-form")).to_be_visible()


def test_recurrence_check_creates_next_once(page: Page, seed):
    seed([todo(1, "주간 회의", due_date="2026-10-13", recurrence="weekly", favorite=True, tags=["회사"],
               subtasks=[sub(1, "자료", True), sub(2, "발표")]), todo(2, "다른 일")])
    open_app(page)
    assert stats(page) == ("2", "0", "2", "0", "0%")

    by_due(page, "주간 회의", "2026-10-13").locator(DONE_BOX).check()
    nxt = by_due(page, "주간 회의", "2026-10-20")
    expect(nxt).to_have_count(1)
    assert stats(page) == ("3", "1", "2", "0", "33%")
    expect(nxt.locator(DONE_BOX)).not_to_be_checked()
    expect(badge(nxt)).to_have_text("🔁 매주")
    expect(nxt.get_by_test_id("favorite-toggle")).to_have_text("★")
    expect(nxt.get_by_test_id("todo-tag")).to_have_text(["#회사"])
    expect(nxt.get_by_test_id("subtask-progress")).to_have_text("0/2 (0%)")       # 하위는 미완료로 복사
    expect(by_due(page, "주간 회의", "2026-10-13").get_by_test_id("subtask-progress")).to_have_text("1/2 (50%)")

    original = by_due(page, "주간 회의", "2026-10-13").locator(DONE_BOX)
    original.uncheck()
    expect(page.get_by_test_id("stat-completed")).to_have_text("0")
    original.check()
    expect(page.get_by_test_id("stat-completed")).to_have_text("1")
    expect(page.locator(ITEM)).to_have_count(3)                            # 다시 완료해도 또 만들지 않는다
    page.reload()
    expect(page.locator(ITEM)).to_have_count(3)


def test_recurrence_bulk_complete_creates_next(page: Page, seed):
    seed([todo(1, "월말 정산", due_date="2026-01-31", recurrence="monthly"), todo(2, "메모")])
    open_app(page)
    pick(page, "월말 정산", "메모")
    page.get_by_test_id("bulk-complete").click()
    expect(by_due(page, "월말 정산", "2026-02-28")).to_have_count(1)
    expect(page.locator(ITEM)).to_have_count(3)
    # 다음 일정(2/28)도 오늘(10/12)보다 앞이라 바로 기한 초과다 — 지난 날짜를 건너뛰지 않는 규칙
    assert stats(page) == ("3", "2", "1", "1", "67%")
    expect(by_due(page, "월말 정산", "2026-02-28").locator(".content > span").first).to_contain_text("⚠ 기한 초과")


def test_recurrence_kept_through_delete_undo(page: Page, seed):
    seed([todo(1, "매일 운동", due_date="2026-10-13", recurrence="daily")])
    open_app(page)
    delete_item(page, "매일 운동")
    expect(page.locator(ITEM)).to_have_count(0)
    page.get_by_test_id("undo-button").click()
    expect(badge(item(page, "매일 운동"))).to_have_text("🔁 매일")


# ---------- 5.0.0 6단계: 작업 템플릿 ----------

def template(id: int, name: str, title: str, **fields) -> dict:
    return {"id": id, "name": name, "title": title, "description": "", "priority": "보통",
            "tags": [], "recurrence": "none", "subtasks": [], **fields}


WEEKLY = template(1, "주간 보고", "주간 보고서", description="금요일까지", priority="높음",
                  tags=["회사", "보고"], recurrence="weekly", subtasks=["자료 수집", "초안, 검토"])


def panel(page: Page):
    return page.get_by_test_id("template-panel")


def choose_template(page: Page, name: str) -> None:
    page.get_by_test_id("template-select").select_option(label=name)


def template_names(page: Page):
    return page.get_by_test_id("template-select").locator("option")


def add_form_values(page: Page) -> tuple[str, ...]:
    return tuple(page.locator(f"#{i}").input_value()
                 for i in ("title", "description", "priority", "due-date", "recurrence", "tags", "subtasks"))


def test_template_buttons_disabled_without_selection(page: Page, seed):
    seed.templates([WEEKLY])
    open_app(page)
    expect(template_names(page)).to_have_text(["템플릿 선택", "주간 보고"])
    for name in ("load", "edit", "delete"):
        expect(page.get_by_test_id(f"template-{name}")).to_be_disabled()
    choose_template(page, "주간 보고")
    for name in ("load", "edit", "delete"):
        expect(page.get_by_test_id(f"template-{name}")).to_be_enabled()


def test_save_current_form_as_template(page: Page, seed):
    open_app(page)
    page.locator("#title").fill("회의록 작성")
    page.locator("#description").fill("회의 직후")
    page.locator("#priority").select_option("낮음")
    page.locator("#due-date").fill("2026-10-20")                          # 마감일은 템플릿에 들어가지 않는다
    page.locator("#recurrence").select_option("monthly")
    page.locator("#tags").fill("회사, 회의")
    page.locator("#subtasks").fill("참석자 정리\n\n  결정 사항  \n")
    page.get_by_test_id("template-name").fill("회의록")
    page.get_by_test_id("template-save").click()

    expect(template_names(page)).to_have_text(["템플릿 선택", "회의록"])
    expect(page.get_by_test_id("template-select")).to_have_value("1")    # 방금 저장한 템플릿이 선택된다
    expect(page.get_by_test_id("template-name")).to_have_value("")
    expect(page.locator(ITEM)).to_have_count(0)                          # 할 일은 만들지 않는다

    page.reload()
    expect(template_names(page)).to_have_text(["템플릿 선택", "회의록"])
    choose_template(page, "회의록")
    page.get_by_test_id("template-edit").click()
    form = page.get_by_test_id("template-edit-form")
    expect(form.get_by_label("반복")).to_have_value("monthly")
    expect(form.get_by_label("하위 작업")).to_have_value("참석자 정리\n결정 사항")   # 빈 줄·공백 정리


def test_load_template_fills_add_form_then_add_creates_subtasks(page: Page, seed):
    seed.templates([WEEKLY])
    seed([todo(1, "기존 할 일")])
    open_app(page)
    page.locator("#due-date").fill("2026-10-16")
    choose_template(page, "주간 보고")
    page.get_by_test_id("template-load").click()
    assert add_form_values(page) == ("주간 보고서", "금요일까지", "높음", "2026-10-16", "weekly",
                                     "회사, 보고", "자료 수집\n초안, 검토")       # 마감일은 그대로 둔다
    expect(page.locator("#title")).to_be_focused()
    expect(page.locator(ITEM)).to_have_count(1)                          # 불러오기만으로는 만들지 않는다

    page.locator("#title").fill("주간 보고서 (10월 3주)")                  # 채운 뒤 고칠 수 있다
    page.get_by_role("button", name="Add To-Do").click()
    row = item(page, "주간 보고서 (10월 3주)")
    expect(row).to_have_count(1)
    expect(row.get_by_test_id("subtask-progress")).to_have_text("0/2 (0%)")
    expect(badge(row)).to_have_text("🔁 매주")
    expect(row.get_by_test_id("todo-tag")).to_have_text(["#회사", "#보고"])
    expect(row).to_contain_text("우선순위: 높음")
    expand(page, "주간 보고서 (10월 3주)")
    expect(subtasks_of(page, "주간 보고서 (10월 3주)").locator("span")).to_have_text(["자료 수집", "초안, 검토"])
    values = add_form_values(page)
    assert values[0] == ""
    assert values[6] == ""
    expect(template_names(page)).to_have_text(["템플릿 선택", "주간 보고"])   # 템플릿은 그대로


def test_add_form_subtasks_without_template(page: Page, seed):
    open_app(page)
    page.locator("#title").fill("이사 준비")
    page.locator("#subtasks").fill("짐 싸기\n\n전입 신고")
    page.get_by_role("button", name="Add To-Do").click()
    expect(item(page, "이사 준비").get_by_test_id("subtask-progress")).to_have_text("0/2 (0%)")
    page.locator("#title").fill("하위 없음")
    page.get_by_role("button", name="Add To-Do").click()
    expect(item(page, "하위 없음").get_by_test_id("subtask-progress")).to_have_count(0)


def test_recurring_template_needs_due_date(page: Page, seed):
    seed.templates([WEEKLY])
    open_app(page)
    choose_template(page, "주간 보고")
    page.get_by_test_id("template-load").click()
    page.get_by_role("button", name="Add To-Do").click()
    expect(page.locator("#error")).to_contain_text("추가 실패")
    expect(page.locator(ITEM)).to_have_count(0)
    assert add_form_values(page)[0] == "주간 보고서"                      # 실패하면 폼을 지우지 않는다
    page.locator("#due-date").fill("2026-10-16")
    page.get_by_role("button", name="Add To-Do").click()
    expect(page.locator(ITEM)).to_have_count(1)
    expect(page.locator("#error")).to_be_hidden()


def test_template_inline_edit_all_fields(page: Page, seed):
    seed.templates([WEEKLY, template(2, "일일 점검", "서버 점검")])
    open_app(page)
    choose_template(page, "주간 보고")
    page.get_by_test_id("template-edit").click()
    form = page.get_by_test_id("template-edit-form")
    expect(form.get_by_label("템플릿 이름")).to_be_focused()
    for label, value in (("템플릿 이름", "주간 보고"), ("제목", "주간 보고서"), ("설명", "금요일까지"),
                         ("우선순위", "높음"), ("반복", "weekly"), ("태그", "회사, 보고"),
                         ("하위 작업", "자료 수집\n초안, 검토")):
        expect(form.get_by_label(label)).to_have_value(value)

    form.get_by_label("템플릿 이름").fill("월간 보고")
    form.get_by_label("제목").fill("월간 보고서")
    form.get_by_label("우선순위").select_option("보통")
    form.get_by_label("반복").select_option("monthly")
    form.get_by_label("태그").fill("회사")
    form.get_by_label("하위 작업").fill("통계 정리")
    form.get_by_role("button", name="저장").click()
    expect(form).to_have_count(0)
    expect(template_names(page)).to_have_text(["템플릿 선택", "월간 보고", "일일 점검"])
    expect(page.get_by_test_id("template-select")).to_have_value("1")

    page.reload()
    choose_template(page, "월간 보고")
    page.get_by_test_id("template-load").click()
    assert add_form_values(page) == ("월간 보고서", "금요일까지", "보통", "", "monthly", "회사", "통계 정리")


def test_template_edit_cancel_and_single_editor(page: Page, seed):
    seed.templates([WEEKLY, template(2, "일일 점검", "서버 점검")])
    open_app(page)
    choose_template(page, "주간 보고")
    page.get_by_test_id("template-edit").click()
    page.get_by_test_id("template-edit-form").get_by_label("제목").fill("버릴 제목")
    page.get_by_test_id("template-edit-form").get_by_role("button", name="취소").click()
    expect(page.get_by_test_id("template-edit-form")).to_have_count(0)

    page.get_by_test_id("template-edit").click()
    expect(page.get_by_test_id("template-edit-form").get_by_label("제목")).to_have_value("주간 보고서")
    choose_template(page, "일일 점검")                                    # 다른 템플릿을 고르면 닫힌다
    expect(page.get_by_test_id("template-edit-form")).to_have_count(0)
    page.get_by_test_id("template-edit").click()
    expect(page.get_by_test_id("template-edit-form")).to_have_count(1)
    expect(page.get_by_test_id("template-edit-form").get_by_label("제목")).to_have_value("서버 점검")


def test_template_errors(page: Page, seed):
    seed.templates([WEEKLY, template(2, "일일 점검", "서버 점검")])
    open_app(page)
    page.locator("#title").fill("아무 제목")
    page.get_by_test_id("template-name").fill(" 주간 보고 ")             # 이름 중복 (공백 무시)
    page.get_by_test_id("template-save").click()
    expect(page.locator("#error")).to_contain_text("템플릿 저장 실패: 409")
    expect(template_names(page)).to_have_count(3)
    expect(page.get_by_test_id("template-name")).to_have_value(" 주간 보고 ")

    page.get_by_test_id("template-name").fill("빈 제목")
    page.locator("#title").fill("")
    page.get_by_test_id("template-save").click()                         # 제목이 없으면 422
    expect(page.locator("#error")).to_contain_text("템플릿 저장 실패: 422")

    choose_template(page, "일일 점검")
    page.get_by_test_id("template-edit").click()
    form = page.get_by_test_id("template-edit-form")
    form.get_by_label("템플릿 이름").fill("주간 보고")
    form.get_by_role("button", name="저장").click()
    expect(page.locator("#error")).to_contain_text("템플릿 수정 실패: 409")
    expect(form).to_be_visible()                                         # 실패하면 폼이 열린 채 남는다


def test_template_delete_confirm(page: Page, seed):
    seed.templates([WEEKLY, template(2, "일일 점검", "서버 점검")])
    open_app(page)
    choose_template(page, "주간 보고")

    messages = []
    page.once("dialog", lambda d: (messages.append(d.message), d.dismiss()))
    page.get_by_test_id("template-delete").click()
    expect(template_names(page)).to_have_count(3)
    assert messages == ["정말 삭제할까요?"]

    page.once("dialog", lambda d: d.accept())
    page.get_by_test_id("template-delete").click()
    expect(template_names(page)).to_have_text(["템플릿 선택", "일일 점검"])
    expect(page.get_by_test_id("template-select")).to_have_value("")
    expect(page.get_by_test_id("template-load")).to_be_disabled()
    page.reload()
    expect(template_names(page)).to_have_text(["템플릿 선택", "일일 점검"])


def test_template_flows_never_prompt(page: Page, seed):
    seed.templates([WEEKLY])
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    open_app(page)
    page.locator("#title").fill("새 템플릿 제목")
    page.get_by_test_id("template-name").fill("새 템플릿")
    page.get_by_test_id("template-save").click()
    expect(template_names(page)).to_have_count(3)
    choose_template(page, "주간 보고")
    page.get_by_test_id("template-load").click()
    page.get_by_test_id("template-edit").click()
    page.get_by_test_id("template-edit-form").get_by_role("button", name="저장").click()
    expect(page.get_by_test_id("template-edit-form")).to_have_count(0)
    assert "prompt" not in dialogs
    assert dialogs == []


# ---------- 5.0.0 7단계: 내보내기 ----------

import csv as csv_module  # noqa: E402
import io as io_module  # noqa: E402
import json as json_module  # noqa: E402
from datetime import datetime as dt, timedelta as td, timezone as tz  # noqa: E402
from pathlib import Path as FilePath  # noqa: E402

EXPORT_TODOS = [
    todo(1, "주간 보고, \"최종\"", description="1행\n2행", priority="높음", due_date="2026-10-20",
         tags=["회사", "보고"], favorite=True, recurrence="weekly",
         subtasks=[sub(1, "자료", True), sub(2, "초안")]),
    todo(2, "=SUM(1)", completed=True),
    todo(3, "숨겨질 항목", tags=["개인"]),
]


def kst_names(ext: str) -> set[str]:
    # 서버가 KST 날짜로 이름을 짓는다 — 자정을 걸쳐도 통과하도록 앞뒤 1분을 함께 허용
    now = dt.now(tz(td(hours=9)))
    return {f"todos-{t:%Y%m%d}.{ext}" for t in (now - td(minutes=1), now, now + td(minutes=1))}


def download(page: Page, testid: str):
    with page.expect_download() as info:
        page.get_by_test_id(testid).click()
    dl = info.value
    return dl.suggested_filename, FilePath(dl.path()).read_bytes()


def test_export_links_are_download_links(page: Page, seed):
    open_app(page)
    for fmt in ("csv", "json"):
        link = page.get_by_test_id(f"export-{fmt}")
        expect(link).to_have_attribute("href", f"/todos/export?format={fmt}")
        expect(link).to_have_attribute("download", "")


def test_export_json_download(page: Page, seed):
    seed(EXPORT_TODOS)
    open_app(page)
    name, data = download(page, "export-json")
    assert name in kst_names("json")
    assert "주간 보고" in data.decode("utf-8")                              # UTF-8, 한글 그대로
    assert json_module.loads(data.decode("utf-8")) == page.request.get("/todos").json()
    assert page.locator(ITEM).count() == 3                                 # 다운로드는 화면·데이터를 바꾸지 않는다


def test_export_csv_download(page: Page, seed):
    seed(EXPORT_TODOS)
    open_app(page)
    name, data = download(page, "export-csv")
    assert name in kst_names("csv")
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv_module.reader(io_module.StringIO(data.decode("utf-8-sig"), newline="")))
    assert rows[0][:3] == ["id", "title", "description"]
    assert len(rows) == 1 + 3                                              # 행 수 = 항목 수
    assert rows[1][1:3] == ["주간 보고, \"최종\"", "1행\n2행"]               # 쉼표·따옴표·줄바꿈 복원
    assert (rows[1][6], rows[1][7], rows[1][8], rows[1][9]) == ("회사;보고", "true", "weekly", "1/2")
    assert rows[2][1] == "'=SUM(1)"                                        # 수식 무력화


def test_export_ignores_filters(page: Page, seed):
    seed(EXPORT_TODOS)
    open_app(page)
    page.get_by_test_id("tag-filter").select_option("회사")
    page.get_by_test_id("favorite-filter").click()
    page.locator("#search").fill("보고")
    expect(page.locator(ITEM)).to_have_count(1)
    _, data = download(page, "export-csv")
    assert len(list(csv_module.reader(io_module.StringIO(data.decode("utf-8-sig"), newline="")))) == 1 + 3
    _, data = download(page, "export-json")
    assert [t["title"] for t in json_module.loads(data)] == ["주간 보고, \"최종\"", "=SUM(1)", "숨겨질 항목"]
    expect(page.locator(ITEM)).to_have_count(1)                            # 필터 상태는 그대로


# ---------- 5.0.0 8단계: 캘린더 보기 ----------

CAL_TODOS = [
    todo(1, "주간 보고", due_date="2026-10-15", favorite=True, recurrence="weekly", priority="높음", tags=["회사"]),
    todo(2, "운동", due_date="2026-10-15", completed=True, tags=["개인"]),
    todo(3, "장보기", due_date="2026-10-15", priority="낮음", tags=["개인"]),
    todo(4, "독서", due_date="2026-10-15", tags=["개인"]),
    todo(5, "청소", due_date="2026-10-15", tags=["개인"]),
    todo(6, "밀린 숙제", due_date="2026-10-05"),
    todo(7, "오늘 할 일", due_date=TODAY),
    todo(8, "다음 달 회의", due_date="2026-11-03"),
    todo(9, "마감 없음"),
]


def calendar(page: Page):
    return page.get_by_test_id("calendar")


def day(page: Page, date: str):
    return page.locator(f"[data-testid=calendar-day][data-date='{date}']")


def chips(page: Page, date: str):
    return day(page, date).get_by_test_id("calendar-chip")


def open_calendar(page: Page) -> None:
    page.get_by_test_id("view-toggle").get_by_role("button", name="캘린더").click()
    expect(calendar(page)).to_be_visible()


def cal_title(page: Page):
    return page.locator("#calendar-title")


def go(page: Page, button: str, times: int = 1) -> None:
    for _ in range(times):
        calendar(page).get_by_role("button", name=button, exact=True).click()


def test_calendar_toggle_and_month_grid(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    toggle = page.get_by_test_id("view-toggle")
    expect(toggle.get_by_role("button", name="목록")).to_have_attribute("aria-pressed", "true")
    expect(calendar(page)).to_be_hidden()
    expect(page.locator(ITEM)).to_have_count(9)

    open_calendar(page)
    expect(toggle.get_by_role("button", name="캘린더")).to_have_attribute("aria-pressed", "true")
    expect(cal_title(page)).to_have_text("2026년 10월")
    expect(page.locator(".calendar-weekday")).to_have_text(["일", "월", "화", "수", "목", "금", "토"])
    days = page.get_by_test_id("calendar-day")
    expect(days).to_have_count(31)
    expect(days.first).to_have_attribute("data-date", "2026-10-01")
    # 2026-10-01 은 목요일 → 일~수 4칸을 비운다
    expect(page.locator("#calendar-grid > *").nth(7 + 4)).to_have_attribute("data-date", "2026-10-01")
    expect(page.locator("#calendar-grid > div:not(.calendar-weekday)")).to_have_count(4)

    today = day(page, TODAY)
    expect(today).to_have_class(re.compile(r"\btoday\b"))
    expect(today).to_have_attribute("aria-current", "date")
    expect(page.locator(".calendar-day.today")).to_have_count(1)
    expect(page.locator(ITEM)).to_have_count(0)                          # 날짜를 고르기 전에는 목록이 비어 있다
    expect(page.locator("#calendar-selected")).to_have_text("날짜를 누르면 그날 마감인 할 일을 볼 수 있습니다.")
    assert stats(page) == ("9", "1", "8", "1", "11%")                     # 통계는 그대로

    toggle.get_by_role("button", name="목록").click()                     # 목록으로 돌아오면 원래 화면
    expect(calendar(page)).to_be_hidden()
    assert titles(page) == [t["title"] for t in CAL_TODOS]


def test_calendar_chips_limit_and_marks(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    open_calendar(page)
    expect(chips(page, "2026-10-15")).to_have_text(["★🔁 주간 보고", "운동", "장보기"])   # 최대 3개
    expect(day(page, "2026-10-15").locator(".calendar-more")).to_have_text("+2")
    expect(chips(page, "2026-10-15").nth(0)).to_have_class(re.compile(r"\bhigh\b"))     # 우선순위 높음
    expect(chips(page, "2026-10-15").nth(1)).to_have_class(re.compile(r"\bdone\b"))     # 완료
    expect(chips(page, "2026-10-05")).to_have_class(re.compile(r"\boverdue\b"))         # 기한 초과
    expect(chips(page, TODAY)).not_to_have_class(re.compile(r"\boverdue\b"))            # 오늘 마감은 초과 아님
    expect(page.get_by_test_id("calendar-chip")).to_have_count(5)        # 10월: 3 + 1 + 1, 마감 없음은 없다
    expect(calendar(page)).not_to_contain_text("마감 없음")
    expect(calendar(page)).not_to_contain_text("다음 달 회의")


def test_calendar_chip_order_follows_sort(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    page.locator("#sort").select_option("priority")
    open_calendar(page)
    expect(chips(page, "2026-10-15")).to_have_text(["★🔁 주간 보고", "운동", "독서"])  # 높음 → 보통 → 보통


def test_calendar_navigation_year_end_and_leap_year(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    open_calendar(page)
    go(page, "이전")
    expect(cal_title(page)).to_have_text("2026년 9월")
    expect(page.get_by_test_id("calendar-day")).to_have_count(30)
    expect(page.get_by_test_id("calendar-chip")).to_have_count(0)

    go(page, "다음", 2)
    expect(cal_title(page)).to_have_text("2026년 11월")
    expect(chips(page, "2026-11-03")).to_have_text(["다음 달 회의"])
    expect(page.locator(".calendar-day.today")).to_have_count(0)         # 다른 달에는 오늘 표시가 없다

    go(page, "다음", 2)                                                  # 12월 → 다음 해 1월
    expect(cal_title(page)).to_have_text("2027년 1월")
    expect(page.get_by_test_id("calendar-day")).to_have_count(31)
    go(page, "다음")
    expect(cal_title(page)).to_have_text("2027년 2월")
    expect(page.get_by_test_id("calendar-day")).to_have_count(28)
    expect(page.get_by_test_id("calendar-day").last).to_have_attribute("data-date", "2027-02-28")
    go(page, "다음", 12)
    expect(cal_title(page)).to_have_text("2028년 2월")                    # 윤년
    expect(page.get_by_test_id("calendar-day")).to_have_count(29)
    expect(page.get_by_test_id("calendar-day").last).to_have_attribute("data-date", "2028-02-29")
    go(page, "이전", 26)
    expect(cal_title(page)).to_have_text("2025년 12월")                   # 1월 → 이전 해 12월
    go(page, "오늘")
    expect(cal_title(page)).to_have_text("2026년 10월")
    expect(day(page, TODAY)).to_have_class(re.compile(r"\btoday\b"))


def test_calendar_filters_apply(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    open_calendar(page)
    page.get_by_test_id("tag-filter").select_option("개인")
    expect(chips(page, "2026-10-15")).to_have_text(["운동", "장보기", "독서"])
    expect(day(page, "2026-10-15").locator(".calendar-more")).to_have_text("+1")
    expect(chips(page, "2026-10-05")).to_have_count(0)

    page.locator("[data-filter=active]").click()
    expect(chips(page, "2026-10-15")).to_have_text(["장보기", "독서", "청소"])
    expect(day(page, "2026-10-15").locator(".calendar-more")).to_have_count(0)

    page.locator("#search").fill("청소")
    expect(page.get_by_test_id("calendar-chip")).to_have_text(["청소"])

    page.locator("#search").fill("")
    page.get_by_test_id("tag-filter").select_option("")
    page.locator("[data-filter=all]").click()
    page.get_by_test_id("favorite-filter").click()
    expect(page.get_by_test_id("calendar-chip")).to_have_text(["★🔁 주간 보고"])
    assert stats(page) == ("9", "1", "8", "1", "11%")                     # 필터와 상관없이 그대로


def test_calendar_select_day_shows_full_items(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    open_calendar(page)
    day(page, "2026-10-15").click()
    expect(day(page, "2026-10-15")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#calendar-selected")).to_have_text("2026-10-15 마감 5개")
    assert titles(page) == ["주간 보고", "운동", "장보기", "독서", "청소"]   # +N 에 숨은 항목도 목록에는 모두

    row = item(page, "장보기")                                           # 목록 보기와 같은 항목 — 바로 완료 가능
    row.locator(DONE_BOX).check()
    expect(chips(page, "2026-10-15").nth(2)).to_have_class(re.compile(r"\bdone\b"))
    expect(page.get_by_test_id("stat-completed")).to_have_text("2")

    day(page, "2026-10-20").click()                                      # 항목이 없는 날
    expect(page.locator(ITEM)).to_have_count(0)
    expect(page.locator("#calendar-selected")).to_have_text("2026-10-20에 마감인 할 일이 없습니다.")

    chips(page, "2026-10-05").click()                                    # 칩을 눌러도 그 날짜가 골라진다
    assert titles(page) == ["밀린 숙제"]
    go(page, "다음")                                                     # 다른 달로 가면 선택이 풀린다
    expect(page.locator(ITEM)).to_have_count(0)
    expect(page.locator(".calendar-day.selected")).to_have_count(0)


def test_calendar_day_list_edit_moves_item(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    open_calendar(page)
    day(page, "2026-10-05").click()
    item(page, "밀린 숙제").get_by_role("button", name="Edit").click()
    page.get_by_test_id("edit-form").get_by_label("마감일").fill("2026-10-22")
    page.get_by_test_id("edit-save").click()
    expect(page.locator(ITEM)).to_have_count(0)                          # 그날 목록에서 빠지고
    expect(chips(page, "2026-10-22")).to_have_text(["밀린 숙제"])          # 새 날짜 칸에 나타난다
    expect(page.get_by_test_id("stat-overdue")).to_have_text("0")


def test_calendar_bulk_and_undo_on_day_list(page: Page, seed):
    seed(CAL_TODOS)
    open_app(page)
    pick(page, "밀린 숙제")                                              # 목록 보기에서 고른 항목은
    open_calendar(page)
    expect(bulk_count(page)).to_have_text("0개 선택")                      # 캘린더에서 안 보이면 선택에서 빠진다

    day(page, "2026-10-15").click()
    page.get_by_test_id("bulk-select-all").click()                       # 보이는 항목 = 그날 목록
    expect(bulk_count(page)).to_have_text("5개 선택")
    page.get_by_test_id("bulk-complete").click()
    expect(page.get_by_test_id("stat-completed")).to_have_text("5")
    expect(item(page, "주간 보고").locator(DONE_BOX)).to_be_checked()
    expect(chips(page, "2026-10-22")).to_have_text(["★🔁 주간 보고"])     # 반복 → 다음 일정이 다음 주 칸에

    delete_item(page, "청소")                                            # 삭제 → 실행 취소도 그날 목록에서
    expect(page.locator(ITEM)).to_have_count(4)
    page.get_by_test_id("undo-button").click()
    expect(page.locator(ITEM)).to_have_count(5)
    expect(day(page, "2026-10-15").locator(".calendar-more")).to_have_text("+2")


# ---------- 6.0.0 1단계: Asia/Seoul 날짜 기준 (D-day·기한 초과·캘린더·생성/완료일) ----------

KST_ZONE = tz(td(hours=9))


@pytest.fixture
def la_page(browser, base_url):
    # 브라우저 시간대가 한국이 아니어도 날짜는 Asia/Seoul 기준이어야 한다
    context = browser.new_context(base_url=base_url, timezone_id="America/Los_Angeles")
    yield context.new_page()
    context.close()


def test_kst_today_regardless_of_browser_timezone(la_page: Page, seed):
    # 2026-10-12 16:00 UTC = 로스앤젤레스 10-12 09:00, 한국 10-13 01:00 → 오늘은 10-13
    la_page.clock.set_fixed_time(dt(2026, 10, 12, 16, 0, tzinfo=tz.utc))
    seed([todo(1, "오늘", due_date="2026-10-13"), todo(2, "어제", due_date="2026-10-12"),
          todo(3, "모레", due_date="2026-10-15"),
          todo(4, "생성 시각", created_at="2026-10-12T16:00:00+00:00",
               completed=True, completed_at="2026-10-12T15:30:00")])           # 시간대 없는 값은 UTC
    open_app(la_page)
    expect(item(la_page, "오늘").get_by_test_id("dday")).to_have_text("오늘 마감")
    expect(item(la_page, "어제").get_by_test_id("dday")).to_have_text("D+1")
    expect(item(la_page, "어제")).to_contain_text("⚠ 기한 초과")
    expect(item(la_page, "모레").get_by_test_id("dday")).to_have_text("D-2")
    expect(item(la_page, "생성 시각").locator(".meta")).to_contain_text("생성: 2026-10-13")
    expect(item(la_page, "생성 시각").locator(".meta")).to_contain_text("완료: 2026-10-13")
    expect(la_page.get_by_test_id("stat-overdue")).to_have_text("1")

    open_calendar(la_page)
    expect(cal_title(la_page)).to_have_text("2026년 10월")
    expect(la_page.locator(".calendar-day.today")).to_have_attribute("data-date", "2026-10-13")


def test_kst_calendar_month_boundary(la_page: Page, seed):
    # 2026-10-31 15:30 UTC = 로스앤젤레스 10-31, 한국 11-01 → 캘린더는 11월에서 시작한다
    la_page.clock.set_fixed_time(dt(2026, 10, 31, 15, 30, tzinfo=tz.utc))
    seed([todo(1, "11월 첫날", due_date="2026-11-01")])
    open_app(la_page)
    open_calendar(la_page)
    expect(cal_title(la_page)).to_have_text("2026년 11월")
    expect(la_page.locator(".calendar-day.today")).to_have_attribute("data-date", "2026-11-01")
    go(la_page, "이전")
    expect(cal_title(la_page)).to_have_text("2026년 10월")
    go(la_page, "오늘")
    expect(cal_title(la_page)).to_have_text("2026년 11월")


# ---------- 6.0.0 1단계: 통계 대시보드 ----------
# 집계는 서버(/stats)가 실제 시계로 하므로, 데이터는 지금 한국 날짜를 기준으로 만든다

def kst_today():
    return dt.now(KST_ZONE).date()


def kst_noon(days_ago: int = 0) -> str:
    # days_ago 일 전 한국 시간 정오 (자정 경계에서 멀리 둔다)
    return dt.combine(kst_today() - td(days=days_ago), dt.min.time(), KST_ZONE).replace(hour=12).isoformat()


def due(days_from_today: int) -> str:
    return (kst_today() + td(days=days_from_today)).isoformat()


def dashboard(page: Page):
    return page.get_by_test_id("view-stats")


def open_stats(page: Page) -> None:
    page.get_by_test_id("view-toggle").get_by_role("button", name="통계").click()
    expect(dashboard(page)).to_be_visible()


def dash(page: Page) -> tuple[str, ...]:
    return tuple(page.get_by_test_id(f"dash-{k}").inner_text()
                 for k in ("total", "completed", "active", "overdue", "rate"))


def priority_counts(page: Page) -> dict[str, str]:
    rows = page.get_by_test_id("dash-priority").locator("li")
    return {rows.nth(i).get_attribute("data-priority"): rows.nth(i).locator("strong").inner_text()
            for i in range(rows.count())}


DASH_TODOS = [
    todo(1, "끝냄", completed=True, completed_at=kst_noon(0), due_date=due(0)),
    todo(2, "밀림", priority="높음", due_date=due(-2)),
    todo(3, "보통 일"),
    todo(4, "나중", priority="낮음", due_date=due(3)),
]


@pytest.fixture
def live_clock(page: Page):
    # 기존 통계 바(브라우저 계산)와 대시보드(서버 실제 시계)를 비교할 때는 브라우저 시계도 지금으로 맞춘다
    page.clock.set_fixed_time(dt.now(tz.utc))


def test_dashboard_toggle_and_cards(page: Page, seed, live_clock):
    seed(DASH_TODOS)
    open_app(page)
    expect(dashboard(page)).to_be_hidden()
    open_stats(page)
    toggle = page.get_by_test_id("view-toggle")
    expect(toggle.get_by_role("button", name="통계")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("dash-rate")).to_have_text("25.0%")              # 대시보드는 소수 1자리
    assert dash(page) == ("4", "1", "3", "1", "25.0%")
    assert stats(page) == ("4", "1", "3", "1", "25%")                            # 기존 통계 바는 정수 % 그대로
    assert priority_counts(page) == {"높음": "1", "보통": "1", "낮음": "1"}
    expect(page.get_by_test_id("dash-compliance")).to_have_text("100.0%")
    expect(page.get_by_test_id("dash-basis")).to_contain_text(f"기준일 {kst_today()} (Asia/Seoul)")
    expect(page.locator(ITEM)).to_have_count(0)                                  # 통계 보기에는 항목 목록이 없다
    expect(page.get_by_test_id("bulk-bar")).to_be_hidden()

    toggle.get_by_role("button", name="목록").click()                            # 목록으로 돌아오면 원래 화면
    expect(dashboard(page)).to_be_hidden()
    expect(page.get_by_test_id("bulk-bar")).to_be_visible()
    assert titles(page) == ["끝냄", "밀림", "보통 일", "나중"]


def test_dashboard_matches_stats_api(page: Page, seed):
    seed(DASH_TODOS)
    open_app(page)
    open_stats(page)
    expect(page.get_by_test_id("dash-total")).to_have_text("4")
    api = page.request.get("/stats").json()                                      # 화면 값은 서버 집계 그대로
    assert dash(page) == (str(api["total"]), str(api["completed"]), str(api["active"]),
                          str(api["overdue"]), f"{api['completion_rate']:.1f}%")
    assert priority_counts(page) == {p: str(n) for p, n in api["active_by_priority"].items()}


def test_dashboard_trend_ranges_and_table(page: Page, seed):
    seed([
        todo(1, "오늘 1", completed=True, completed_at=kst_noon(0)),
        todo(2, "오늘 2", completed=True, completed_at=kst_noon(0)),
        todo(3, "6일 전", completed=True, completed_at=kst_noon(6)),
        todo(4, "10일 전", completed=True, completed_at=kst_noon(10)),
        todo(5, "40일 전", completed=True, completed_at=kst_noon(40)),           # 30일 밖
        todo(6, "시각 없음", completed=True),                                    # 추이에서 제외
    ])
    open_app(page)
    open_stats(page)
    summary = page.get_by_test_id("dash-trend-summary")
    expect(summary).to_contain_text("최근 7일 완료 3건")
    expect(page.locator("[data-range='7']")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("dash-trend").locator(".dash-bar-col")).to_have_count(7)
    expect(page.get_by_test_id("dash-excluded")).to_have_text("완료 시각이 없어 추이에서 제외: 1건")

    rows = page.get_by_test_id("dash-trend-table").locator("tbody tr")           # 표 요약 (접근성)
    expect(rows).to_have_count(7)
    expect(rows.last.locator("td")).to_have_text([kst_today().isoformat(), "2"])
    expect(rows.first.locator("td")).to_have_text([due(-6), "1"])
    expect(page.get_by_test_id("dash-trend").locator(".dash-bar-col").last).to_have_attribute(
        "title", f"{kst_today()}: 2건")

    page.get_by_role("button", name="최근 30일").click()
    expect(summary).to_contain_text("최근 30일 완료 4건")
    expect(page.locator("[data-range='30']")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("dash-trend").locator(".dash-bar-col")).to_have_count(30)
    expect(rows).to_have_count(30)
    page.get_by_text("표로 보기").click()
    expect(page.get_by_test_id("dash-trend-table")).to_be_visible()


def test_dashboard_empty_and_no_compliance_data(page: Page, seed):
    open_app(page)
    open_stats(page)
    expect(page.get_by_test_id("dash-rate")).to_have_text("0.0%")
    assert dash(page) == ("0", "0", "0", "0", "0.0%")
    expect(page.get_by_test_id("dash-compliance")).to_have_text("데이터 없음")     # 분모 0
    expect(page.get_by_test_id("dash-trend-summary")).to_contain_text("최근 7일 완료 0건")
    assert priority_counts(page) == {"높음": "0", "보통": "0", "낮음": "0"}


def test_dashboard_compliance_rate(page: Page, seed):
    seed([
        todo(1, "제때", completed=True, completed_at=kst_noon(3), due_date=due(-3)),
        todo(2, "늦게", completed=True, completed_at=kst_noon(1), due_date=due(-3)),
        todo(3, "마감 없음", completed=True, completed_at=kst_noon(1)),
    ])
    open_app(page)
    open_stats(page)
    expect(page.get_by_test_id("dash-compliance")).to_have_text("50.0%")
    expect(dashboard(page)).to_contain_text("마감일이 있는 완료 2개 중 1개를 마감일까지 끝냈습니다.")


def test_dashboard_refreshes_after_change_and_ignores_filters(page: Page, seed):
    seed(DASH_TODOS)
    open_app(page)
    page.locator("#search").fill("없는 검색어")
    page.locator("[data-filter=completed]").click()
    open_stats(page)
    expect(page.get_by_test_id("dash-total")).to_have_text("4")                  # 필터와 상관없이 전체
    page.locator("#title").fill("새 할 일")                                       # 통계 보기에서도 추가 폼은 그대로
    page.get_by_role("button", name="Add To-Do").click()
    expect(page.get_by_test_id("dash-total")).to_have_text("5")
    expect(page.get_by_test_id("dash-rate")).to_have_text("20.0%")


def test_dashboard_error_shown(page: Page, seed):
    open_app(page)
    page.route("**/stats", lambda route: route.fulfill(status=500, body="boom"))
    page.get_by_test_id("view-toggle").get_by_role("button", name="통계").click()
    expect(page.locator("#error")).to_contain_text("통계 불러오기 실패")


# ---------- 6.0.0 2단계: 칸반 보드 ----------

KANBAN_TODOS = [
    todo(1, "보고서", priority="높음", due_date="2026-10-15", tags=["학교"], favorite=True,
         subtasks=[sub(1, "초안", True), sub(2, "검토")]),
    todo(2, "주간 회의", due_date="2026-10-12", recurrence="weekly", tags=["팀"]),
    todo(3, "밀린 일", priority="낮음", due_date="2026-10-10"),
    todo(4, "끝난 일", completed=True, completed_at="2026-10-11T03:00:00+00:00"),
]


def open_kanban(page: Page) -> None:
    page.get_by_test_id("view-toggle").get_by_role("button", name="칸반").click()
    expect(page.get_by_test_id("kanban")).to_be_visible()


def column(page: Page, status: str):
    return page.locator(f"[data-testid=kanban-column][data-status={status}]")


def column_titles(page: Page, status: str):
    return column(page, status).locator(".kanban-card-title")


def card(page: Page, title: str):
    return page.get_by_test_id("kanban-card").filter(has=page.locator(".kanban-card-title", has_text=title))


def server_todo(page: Page, todo_id: int) -> dict:
    return next(t for t in page.request.get("/todos").json() if t["id"] == todo_id)


def test_kanban_columns_and_card_content(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    expect(page.get_by_test_id("kanban")).to_be_hidden()
    open_kanban(page)
    expect(page.get_by_test_id("view-toggle").get_by_role("button", name="칸반")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("kanban-column")).to_have_count(2)
    expect(column_titles(page, "active")).to_have_text(["보고서", "주간 회의", "밀린 일"])
    expect(column_titles(page, "completed")).to_have_text(["끝난 일"])
    expect(page.get_by_test_id("kanban-count")).to_have_text(["(3)", "(1)"])

    report = card(page, "보고서")
    expect(report).to_contain_text("우선순위 높음")
    expect(report).to_contain_text("마감 2026-10-15")
    expect(report.get_by_test_id("dday")).to_have_text("D-3")
    expect(report.get_by_test_id("todo-tag")).to_have_text(["#학교"])
    expect(report.get_by_test_id("subtask-progress")).to_have_text("1/2 (50%)")
    expect(report.get_by_test_id("favorite-toggle")).to_have_text("★")
    expect(card(page, "주간 회의").get_by_test_id("recurrence-badge")).to_have_text("🔁 매주")
    expect(card(page, "밀린 일")).to_contain_text("⚠ 기한 초과")                  # 색 외에도 글자로 표시
    expect(card(page, "끝난 일").locator(".kanban-card-title")).to_have_class(re.compile(r"\bdone\b"))
    expect(page.locator(ITEM)).to_have_count(0)                                  # 칸반 보기에는 항목 목록이 없다
    expect(page.get_by_test_id("bulk-bar")).to_be_hidden()
    assert stats(page) == ("4", "1", "3", "1", "25%")

    page.get_by_test_id("view-toggle").get_by_role("button", name="목록").click()
    expect(page.locator(ITEM)).to_have_count(4)
    expect(page.get_by_test_id("kanban-card")).to_have_count(0)                  # 숨은 칸반 카드는 남기지 않는다


def test_kanban_drag_and_drop_both_ways(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    card(page, "보고서").drag_to(column(page, "completed"))
    expect(column_titles(page, "completed")).to_have_text(["보고서", "끝난 일"])
    expect(column_titles(page, "active")).to_have_text(["주간 회의", "밀린 일"])
    expect(page.get_by_test_id("stat-completed")).to_have_text("2")
    saved = server_todo(page, 1)
    assert saved["completed"] is True and saved["completed_at"] is not None
    # 기존 PUT 전체 교체여도 다른 필드는 그대로
    assert (saved["priority"], saved["due_date"], saved["tags"], saved["favorite"], len(saved["subtasks"])) == \
        ("높음", "2026-10-15", ["학교"], True, 2)

    card(page, "보고서").drag_to(column(page, "active"))
    expect(column_titles(page, "active")).to_have_text(["보고서", "주간 회의", "밀린 일"])
    expect(page.get_by_test_id("stat-completed")).to_have_text("1")
    assert server_todo(page, 1)["completed_at"] is None


def test_kanban_drop_on_same_column_does_nothing(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    puts = []
    page.on("request", lambda r: puts.append(r.url) if r.method == "PUT" else None)
    card(page, "보고서").drag_to(column(page, "active"))
    page.wait_for_timeout(300)
    assert puts == []
    expect(column_titles(page, "active")).to_have_text(["보고서", "주간 회의", "밀린 일"])


def test_kanban_keyboard_move_buttons(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    move = page.get_by_role("button", name="밀린 일 완료로 이동")                   # 키보드·스크린리더용 이름
    move.focus()
    page.keyboard.press("Enter")
    expect(column_titles(page, "completed")).to_have_text(["밀린 일", "끝난 일"])
    back = page.get_by_role("button", name="밀린 일 진행 중으로 이동")
    expect(back).to_be_focused()                                                 # 옮긴 카드의 버튼으로 포커스가 따라간다
    expect(page.get_by_test_id("kanban-status")).to_have_text("'밀린 일'을(를) 완료(으)로 옮겼습니다.")
    page.keyboard.press("Space")
    expect(column_titles(page, "active")).to_have_text(["보고서", "주간 회의", "밀린 일"])
    expect(page.get_by_role("button", name="밀린 일 완료로 이동")).to_be_focused()


def test_kanban_failure_rolls_back(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    page.route("**/todos/1", lambda route: route.fulfill(status=500, body="boom")
               if route.request.method == "PUT" else route.continue_())
    card(page, "보고서").drag_to(column(page, "completed"))
    expect(page.locator("#error")).to_contain_text("상태 변경 실패")
    expect(column_titles(page, "active")).to_have_text(["보고서", "주간 회의", "밀린 일"])   # 원래 열로 돌아온다
    expect(column_titles(page, "completed")).to_have_text(["끝난 일"])
    expect(page.get_by_test_id("kanban-status")).to_contain_text("원래 열로 되돌렸습니다")
    expect(page.get_by_test_id("stat-completed")).to_have_text("1")
    assert server_todo(page, 1)["completed"] is False

    card(page, "보고서").get_by_test_id("kanban-move").click()                    # 버튼으로 옮겨도 같다
    expect(page.locator("#error")).to_contain_text("상태 변경 실패")
    expect(column_titles(page, "active")).to_have_text(["보고서", "주간 회의", "밀린 일"])
    expect(card(page, "보고서").get_by_test_id("kanban-move")).to_be_enabled()


def test_kanban_no_duplicate_requests(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    puts = []
    page.on("request", lambda r: puts.append(r.url) if r.method == "PUT" else None)
    # 같은 순간 두 번 눌러도 요청은 한 번 (처리 중인 카드는 무시)
    card(page, "보고서").get_by_test_id("kanban-move").evaluate("b => { b.click(); b.click(); }")
    expect(column_titles(page, "completed")).to_have_text(["보고서", "끝난 일"])
    page.wait_for_load_state("networkidle")
    assert len(puts) == 1
    assert server_todo(page, 1)["completed"] is True


def test_kanban_recurring_creates_next_card(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    card(page, "주간 회의").drag_to(column(page, "completed"))
    expect(column_titles(page, "completed")).to_have_text(["주간 회의", "끝난 일"])
    expect(column_titles(page, "active")).to_have_text(["보고서", "밀린 일", "주간 회의"])   # 다음 일정은 목록 끝
    nxt = column(page, "active").get_by_test_id("kanban-card").last
    expect(nxt).to_contain_text("마감 2026-10-19")
    expect(nxt.get_by_test_id("recurrence-badge")).to_have_text("🔁 매주")
    expect(nxt.get_by_test_id("todo-tag")).to_have_text(["#팀"])
    expect(page.get_by_test_id("stat-total")).to_have_text("5")


def test_kanban_filters_and_sort(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    page.locator("#search").fill("보고")
    expect(page.get_by_test_id("kanban-card")).to_have_count(1)
    expect(column(page, "completed")).to_contain_text("항목 없음")
    page.locator("#search").fill("")

    page.get_by_test_id("favorite-filter").click()
    expect(column_titles(page, "active")).to_have_text(["보고서"])
    page.get_by_test_id("favorite-filter").click()

    page.get_by_test_id("tag-filter").select_option("팀")
    expect(column_titles(page, "active")).to_have_text(["주간 회의"])
    page.get_by_test_id("tag-filter").select_option("")

    page.locator("[data-filter=active]").click()
    expect(column(page, "completed")).to_contain_text("상태 필터로 숨김")
    expect(column(page, "completed").get_by_test_id("kanban-card")).to_have_count(0)
    expect(column_titles(page, "active")).to_have_count(3)
    page.locator("[data-filter=completed]").click()
    expect(column(page, "active")).to_contain_text("상태 필터로 숨김")
    expect(column_titles(page, "completed")).to_have_text(["끝난 일"])
    page.locator("[data-filter=all]").click()

    page.locator("#sort").select_option("due-date")                              # 카드 순서는 지금 고른 정렬
    expect(column_titles(page, "active")).to_have_text(["밀린 일", "주간 회의", "보고서"])
    page.locator("#sort").select_option("priority")
    expect(column_titles(page, "active")).to_have_text(["보고서", "주간 회의", "밀린 일"])


def test_kanban_changes_reflect_in_other_views(page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(page)
    open_kanban(page)
    card(page, "보고서").get_by_test_id("kanban-move").click()
    expect(column_titles(page, "completed")).to_have_text(["보고서", "끝난 일"])
    card(page, "주간 회의").get_by_test_id("favorite-toggle").click()           # 카드의 즐겨찾기도 기존 PUT
    expect(card(page, "주간 회의").get_by_test_id("favorite-toggle")).to_have_text("★")
    assert server_todo(page, 2)["favorite"] is True
    assert server_todo(page, 2)["recurrence"] == "weekly"

    page.get_by_test_id("view-toggle").get_by_role("button", name="목록").click()
    expect(item(page, "보고서").locator(DONE_BOX)).to_be_checked()
    expect(item(page, "보고서").locator(".meta")).to_contain_text("완료:")

    open_calendar(page)
    expect(chips(page, "2026-10-15")).to_have_class(re.compile(r"\bdone\b"))

    open_stats(page)
    expect(page.get_by_test_id("dash-completed")).to_have_text("2")
    assert stats(page)[1] == "2"


@pytest.fixture
def mobile_page(browser, base_url):
    context = browser.new_context(base_url=base_url, timezone_id="Asia/Seoul", viewport={"width": 375, "height": 800},
                                  has_touch=True, is_mobile=True)
    page = context.new_page()
    page.clock.set_fixed_time(f"{TODAY}T12:00:00")
    yield page
    context.close()


def test_kanban_mobile_touch(mobile_page: Page, seed):
    seed(KANBAN_TODOS)
    open_app(mobile_page)
    mobile_page.get_by_test_id("view-toggle").get_by_role("button", name="칸반").tap()
    expect(mobile_page.get_by_test_id("kanban")).to_be_visible()
    active, done_col = column(mobile_page, "active").bounding_box(), column(mobile_page, "completed").bounding_box()
    assert done_col["y"] >= active["y"] + active["height"]                     # 좁은 화면에서는 세로로 쌓인다
    assert mobile_page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    card(mobile_page, "보고서").get_by_test_id("kanban-move").tap()             # 터치는 이동 버튼으로
    expect(column_titles(mobile_page, "completed")).to_have_text(["보고서", "끝난 일"])
    assert mobile_page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


# ---------- 6.0.0 3단계: 마감일 알림 센터 ----------

NOTIFY_KEY = "todo.v6.notifications.read"
NOTIFY_TODOS = [
    todo(1, "내일 마감 일", due_date="2026-10-13"),
    todo(2, "오늘 마감 일", due_date="2026-10-12"),
    todo(3, "지난 일", due_date="2026-10-10"),
    todo(4, "완료한 일", due_date="2026-10-12", completed=True),       # 완료 — 대상 아님
    todo(5, "마감 없음"),                                              # 마감일 없음 — 대상 아님
    todo(6, "모레 일", due_date="2026-10-14"),                         # 이틀 뒤 — 대상 아님
    todo(7, "오래된 일", due_date="2026-10-01"),
]
NOTIFY_ORDER = ["오래된 일", "지난 일", "오늘 마감 일", "내일 마감 일"]   # 기한 초과(오래된 순) → 오늘 → 내일


def bell(page: Page):
    return page.get_by_test_id("notification-bell")


def notify_badge(page: Page):
    return page.get_by_test_id("notification-badge")


def notify_item(page: Page, title: str):
    return page.get_by_test_id("notification-item").filter(has=page.locator(".notify-title", has_text=title))


def notify_titles(page: Page):
    return page.locator("[data-testid=notification-item] .notify-title")


def open_notifications(page: Page) -> None:
    if page.get_by_test_id("notification-panel").is_hidden():
        bell(page).click()
    expect(page.get_by_test_id("notification-panel")).to_be_visible()


def read_all(page: Page) -> None:
    open_notifications(page)
    page.get_by_test_id("notification-read-all").click()
    expect(notify_badge(page)).to_be_hidden()


def stored_keys(page: Page) -> list[str]:
    return page.evaluate(f"JSON.parse(localStorage.getItem('{NOTIFY_KEY}') || '[]')")


def test_notify_categories_order_and_badge(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    expect(notify_badge(page)).to_have_text("4")                                  # 앱을 열면 바로 보인다
    expect(bell(page)).to_have_attribute("aria-label", "알림, 읽지 않은 알림 4개")
    expect(page.get_by_test_id("notification-panel")).to_be_hidden()
    open_notifications(page)
    expect(notify_titles(page)).to_have_text(NOTIFY_ORDER)
    expect(page.get_by_test_id("notification-item").locator(".notify-category")).to_have_text(
        ["기한 초과", "기한 초과", "오늘 마감", "내일 마감"])
    expect(notify_item(page, "지난 일")).to_contain_text("마감 2026-10-10 (D+2)")
    expect(notify_item(page, "내일 마감 일")).to_contain_text("마감 2026-10-13 (D-1)")
    expect(page.get_by_test_id("notification-item").filter(has_text="새 알림")).to_have_count(4)
    for title in ("완료한 일", "마감 없음", "모레 일"):
        expect(notify_item(page, title)).to_have_count(0)


def test_notify_compute_boundaries(page: Page, seed):
    open_app(page)
    keys = page.evaluate("""() => {
      const t = (id, due, completed = false) => ({ id, title: `t${id}`, due_date: due, completed });
      const list = [t(1, "2027-01-01"), t(2, "2026-12-31"), t(3, "2026-12-30"), t(4, "2027-01-02"),
                    t(5, "2026-12-31", true), t(6, null), t(7, "2026-12-31"), t(2, "2026-12-31")];
      return {
        yearEnd: computeNotifications(list, "2026-12-31").map((n) => n.key),
        leap: computeNotifications([t(1, "2028-02-29"), t(2, "2028-03-01")], "2028-02-28").map((n) => n.key),
        monthEnd: computeNotifications([t(1, "2026-11-01"), t(2, "2026-10-30")], "2026-10-31").map((n) => n.key),
      };
    }""")
    # 연말·다음 해, 완료·마감일 없음·이틀 뒤 제외, 같은 할 일이 두 번 와도 키는 하나
    assert keys["yearEnd"] == ["3|2026-12-30|overdue", "2|2026-12-31|today", "7|2026-12-31|today",
                               "1|2027-01-01|tomorrow"]
    assert keys["leap"] == ["1|2028-02-29|tomorrow"]
    assert keys["monthEnd"] == ["2|2026-10-30|overdue", "1|2026-11-01|tomorrow"]


def test_notify_read_persists_after_reload(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    open_notifications(page)
    notify_item(page, "오늘 마감 일").click()                                     # 누르면 읽음
    expect(notify_badge(page)).to_have_text("3")
    open_notifications(page)
    expect(notify_item(page, "오늘 마감 일")).to_have_class(re.compile(r"\bread\b"))
    expect(notify_item(page, "오늘 마감 일")).not_to_contain_text("새 알림")
    assert stored_keys(page) == ["2|2026-10-12|today"]

    page.reload()
    expect(notify_badge(page)).to_have_text("3")                                  # 새로고침 후에도 유지
    read_all(page)
    expect(bell(page)).to_have_attribute("aria-label", "알림, 읽지 않은 알림 없음")
    expect(page.get_by_test_id("notification-read-all")).to_be_disabled()
    page.reload()
    expect(page.get_by_test_id("stat-total")).to_have_text("7")
    expect(notify_badge(page)).to_be_hidden()
    assert sorted(stored_keys(page)) == sorted(["1|2026-10-13|tomorrow", "2|2026-10-12|today",
                                                "3|2026-10-10|overdue", "7|2026-10-01|overdue"])


def test_notify_no_duplicates_across_rerenders(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    open_notifications(page)
    for view in ("캘린더", "칸반", "통계", "목록"):                               # 여러 번 다시 그려도
        page.get_by_test_id("view-toggle").get_by_role("button", name=view).click()
    page.locator("#search").fill("일")
    page.locator("#sort").select_option("priority")
    page.locator("#search").fill("")
    expect(page.get_by_test_id("notification-item")).to_have_count(4)             # 알림은 그대로 4개
    expect(notify_badge(page)).to_have_text("4")
    read_all(page)
    page.get_by_test_id("notification-read-all").click(force=True)               # 다시 눌러도
    page.locator("[data-filter=active]").click()
    keys = stored_keys(page)
    assert len(keys) == len(set(keys)) == 4                                      # 키가 중복 저장되지 않는다


def test_notify_complete_delete_and_undo_cleanup(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    read_all(page)
    item(page, "오늘 마감 일").locator(DONE_BOX).check()                          # 완료하면 알림이 사라진다
    expect(notify_item(page, "오늘 마감 일")).to_have_count(0)
    expect(page.get_by_test_id("notification-item")).to_have_count(3)
    page.wait_for_function(f"!localStorage.getItem('{NOTIFY_KEY}').includes('2|2026-10-12|today')")

    delete_item(page, "지난 일")                                                  # 삭제하면 관련 알림을 정리한다
    expect(notify_item(page, "지난 일")).to_have_count(0)
    page.wait_for_function(f"!localStorage.getItem('{NOTIFY_KEY}').includes('3|2026-10-10|overdue')")
    expect(notify_badge(page)).to_be_hidden()

    page.get_by_test_id("undo-button").click()                                   # 되살리면 새 알림으로 다시 보인다
    expect(notify_item(page, "지난 일")).to_have_count(1)
    expect(notify_badge(page)).to_have_text("1")
    expect(notify_item(page, "지난 일")).to_contain_text("새 알림")


def test_notify_due_date_change_is_new_alert(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    read_all(page)
    item(page, "내일 마감 일").get_by_role("button", name="Edit").click()
    page.get_by_test_id("edit-form").get_by_label("마감일").fill("2026-10-12")
    page.get_by_test_id("edit-save").click()
    expect(notify_badge(page)).to_have_text("1")                                  # 분류가 바뀌면 새 알림
    expect(notify_item(page, "내일 마감 일")).to_have_attribute("data-category", "today")
    expect(notify_item(page, "내일 마감 일")).to_contain_text("새 알림")
    assert "1|2026-10-13|tomorrow" not in stored_keys(page)


@pytest.fixture
def clock_page(browser, base_url):
    # 타이머까지 가짜 시계로 돌리는 페이지 (자정 넘김 테스트용)
    context = browser.new_context(base_url=base_url, timezone_id="Asia/Seoul")
    yield context.new_page()
    context.close()


def test_notify_midnight_rollover(clock_page: Page, seed):
    clock_page.clock.install(time=dt(2026, 10, 12, 23, 59, 30, tzinfo=KST_ZONE))
    seed(NOTIFY_TODOS)
    open_app(clock_page)
    expect(notify_badge(clock_page)).to_have_text("4")
    read_all(clock_page)
    clock_page.clock.run_for("01:00")                                            # 한국 자정을 넘긴다
    # 10-13: 내일→오늘, 오늘→기한 초과, 모레→내일 은 새 알림. 이미 기한 초과였던 둘은 읽은 그대로
    expect(notify_badge(clock_page)).to_have_text("3")
    expect(notify_titles(clock_page)).to_have_text(["오래된 일", "지난 일", "오늘 마감 일", "내일 마감 일", "모레 일"])
    expect(clock_page.get_by_test_id("notification-item").locator(".notify-category")).to_have_text(
        ["기한 초과", "기한 초과", "기한 초과", "오늘 마감", "내일 마감"])
    expect(clock_page.get_by_test_id("notification-live")).to_have_text("읽지 않은 마감일 알림 3개")
    # 다른 화면도 같은 날짜로 다시 계산된다
    expect(item(clock_page, "내일 마감 일").get_by_test_id("dday")).to_have_text("오늘 마감")
    expect(clock_page.get_by_test_id("stat-overdue")).to_have_text("3")


def test_notify_recalculates_on_tab_return(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    expect(notify_badge(page)).to_have_text("4")
    page.clock.set_fixed_time("2026-10-13T09:00:00")                             # 탭이 백그라운드인 동안 날짜가 바뀜
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(notify_badge(page)).to_have_text("5")
    expect(page.get_by_test_id("stat-overdue")).to_have_text("3")


def test_notify_opens_todo_and_clears_hiding_filters(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    page.locator("#search").fill("없는 검색어")
    page.get_by_test_id("view-toggle").get_by_role("button", name="칸반").click()
    open_notifications(page)
    notify_item(page, "지난 일").click()
    expect(page.get_by_test_id("view-toggle").get_by_role("button", name="목록")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#search")).to_have_value("")                            # 가리던 필터를 푼다
    target = page.locator(f"{ITEM}[data-id='3']")
    expect(target).to_be_focused()
    expect(target).to_have_class(re.compile(r"\bflash\b"))
    expect(page.get_by_test_id("notification-panel")).to_be_hidden()
    expect(notify_badge(page)).to_have_text("3")

    page.locator("#search").fill("일")                                           # 이미 보이면 필터는 그대로
    open_notifications(page)
    notify_item(page, "내일 마감 일").click()
    expect(page.locator("#search")).to_have_value("일")
    expect(page.locator(f"{ITEM}[data-id='1']")).to_be_focused()


def test_notify_keyboard_and_screen_reader(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    expect(page.get_by_test_id("notification-live")).to_have_text("읽지 않은 마감일 알림 4개")
    bell(page).focus()
    page.keyboard.press("Enter")
    expect(bell(page)).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_test_id("notification-item").first).to_be_focused()       # 열면 첫 알림으로
    page.keyboard.press("Tab")
    expect(page.get_by_test_id("notification-item").nth(1)).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("notification-panel")).to_be_hidden()
    expect(bell(page)).to_be_focused()                                           # 닫으면 종으로 돌아온다
    expect(bell(page)).to_have_attribute("aria-expanded", "false")

    page.keyboard.press("Enter")
    page.keyboard.press("Enter")                                                 # 첫 알림을 키보드로 연다
    expect(page.locator(f"{ITEM}[data-id='7']")).to_be_focused()
    expect(page.get_by_test_id("notification-live")).to_have_text("읽지 않은 마감일 알림 3개")


def test_notify_empty_state(page: Page, seed):
    seed([todo(1, "마감 없음"), todo(2, "완료", due_date="2026-10-01", completed=True)])
    open_app(page)
    expect(notify_badge(page)).to_be_hidden()
    open_notifications(page)
    expect(page.get_by_test_id("notification-panel")).to_contain_text("알림이 없습니다.")
    expect(page.get_by_test_id("notification-read-all")).to_be_disabled()


def test_notify_corrupt_storage_is_ignored(page: Page, seed):
    seed(NOTIFY_TODOS)
    page.add_init_script(f"localStorage.setItem('{NOTIFY_KEY}', '{{broken')")
    open_app(page)
    expect(notify_badge(page)).to_have_text("4")                                  # 깨진 값은 읽지 않음으로 시작
    expect(page.locator("#error")).to_be_hidden()
    read_all(page)
    assert len(stored_keys(page)) == 4                                           # 올바른 값으로 다시 저장된다


def test_notify_works_when_storage_unavailable(page: Page, seed):
    seed(NOTIFY_TODOS)
    page.add_init_script("""
      Storage.prototype.getItem = () => { throw new Error("blocked"); };
      Storage.prototype.setItem = () => { throw new Error("blocked"); };
    """)
    open_app(page)
    expect(notify_badge(page)).to_have_text("4")
    read_all(page)                                                               # 이 화면에서는 읽음이 유지된다
    page.locator("#search").fill("일")
    expect(notify_badge(page)).to_be_hidden()
    expect(page.locator("#error")).to_be_hidden()


def test_notify_load_failure_keeps_read_state(page: Page, seed):
    seed(NOTIFY_TODOS)
    page.add_init_script(f"""if (!sessionStorage.getItem('seeded')) {{
      sessionStorage.setItem('seeded', '1');
      localStorage.setItem('{NOTIFY_KEY}', JSON.stringify(['3|2026-10-10|overdue']));
    }}""")
    page.route("**/todos", lambda route: route.fulfill(status=500, body="down")
               if route.request.method == "GET" else route.continue_())
    page.goto("/")
    expect(page.locator("#error")).to_contain_text("목록 불러오기 실패")
    assert stored_keys(page) == ["3|2026-10-10|overdue"]                         # 목록을 못 받으면 지우지 않는다
    page.unroute("**/todos")
    page.reload()
    expect(notify_badge(page)).to_have_text("3")
    open_notifications(page)
    expect(notify_item(page, "지난 일")).to_have_class(re.compile(r"\bread\b"))


def test_notify_matches_other_views(page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(page)
    open_notifications(page)
    overdue = page.locator("[data-testid=notification-item][data-category=overdue]")
    expect(overdue).to_have_count(2)
    expect(page.get_by_test_id("stat-overdue")).to_have_text("2")                # 통계 바와 같은 기준
    expect(page.locator(ITEM).filter(has_text="⚠ 기한 초과")).to_have_count(2)  # 목록
    page.get_by_test_id("view-toggle").get_by_role("button", name="칸반").click()
    expect(page.locator(".kanban-card.kanban-overdue")).to_have_count(2)         # 칸반
    card(page, "내일 마감 일").get_by_test_id("kanban-move").click()             # 칸반에서 완료하면 알림도 빠진다
    expect(notify_item(page, "내일 마감 일")).to_have_count(0)
    open_calendar(page)
    expect(chips(page, "2026-10-10")).to_have_class(re.compile(r"\boverdue\b"))  # 캘린더


def test_notify_overdue_matches_dashboard(page: Page, seed, live_clock):
    # 대시보드는 서버 실제 시계 기준이므로 데이터도 지금 한국 날짜로 만든다
    seed([todo(1, "어제", due_date=due(-1)), todo(2, "오늘", due_date=due(0)),
          todo(3, "내일", due_date=due(1)), todo(4, "그제", due_date=due(-2))])
    open_app(page)
    open_stats(page)
    expect(page.get_by_test_id("dash-overdue")).to_have_text("2")
    open_notifications(page)
    expect(page.locator("[data-testid=notification-item][data-category=overdue]")).to_have_count(2)
    expect(page.locator("[data-testid=notification-item][data-category=today]")).to_have_count(1)
    expect(page.locator("[data-testid=notification-item][data-category=tomorrow]")).to_have_count(1)


def test_notify_mobile(mobile_page: Page, seed):
    seed(NOTIFY_TODOS)
    open_app(mobile_page)
    bell(mobile_page).tap()
    expect(mobile_page.get_by_test_id("notification-panel")).to_be_visible()
    assert mobile_page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    notify_item(mobile_page, "오늘 마감 일").tap()
    expect(mobile_page.locator(f"{ITEM}[data-id='2']")).to_be_in_viewport()
    expect(notify_badge(mobile_page)).to_have_text("3")
