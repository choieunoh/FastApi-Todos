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
    assert add_form_values(page)[0] == "" and add_form_values(page)[6] == ""   # 추가 후 폼이 비워진다
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
    assert "prompt" not in dialogs and dialogs == []


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
