import os

import pytest

requests = pytest.importorskip("requests")   # requirements-dev.txt 에 있다

pytestmark = pytest.mark.integration          # 기본 pytest 실행에서는 제외된다

BASE_URL = os.environ.get("INTEGRATION_BASE_URL", "http://163.239.77.78:5014")


def test_todo_integration():
    # 1. To-Do 추가
    create_response = requests.post(
        f"{BASE_URL}/todos",
        json={"title": "integration test todo"},
    )
    assert create_response.status_code == 201

    created_todo = create_response.json()
    todo_id = created_todo["id"]

    # 2. To-Do 조회
    get_response = requests.get(f"{BASE_URL}/todos")
    assert get_response.status_code == 200

    todos = get_response.json()
    assert any(todo["id"] == todo_id for todo in todos)

    # 3. To-Do 수정
    update_response = requests.put(
        f"{BASE_URL}/todos/{todo_id}",
        json={"title": "updated integration todo"},
    )
    assert update_response.status_code == 200

    # 4. To-Do 삭제
    delete_response = requests.delete(
        f"{BASE_URL}/todos/{todo_id}"
    )
    assert delete_response.status_code == 204


def test_create_todo_without_title():
    response = requests.post(
        f"{BASE_URL}/todos",
        json={},
    )
    assert response.status_code == 422


def test_delete_nonexistent_todo():
    response = requests.delete(
        f"{BASE_URL}/todos/99999999"
    )
    assert response.status_code == 404
