import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent       # fastapi-app/
REAL_TODO_FILE = APP_DIR / "todo.json"
REAL_TEMPLATE_FILE = APP_DIR / "task_templates.json"


def file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


REAL_TODO_HASH = file_hash(REAL_TODO_FILE)             # 테스트 시작 전 실제 데이터의 지문
REAL_TEMPLATE_HASH = file_hash(REAL_TEMPLATE_FILE)     # 없으면 None — 테스트가 만들어서도 안 된다


@pytest.fixture(scope="session", autouse=True)
def protect_real_todo_file():
    # 어떤 테스트도 실제 todo.json 을 바꾸면 안 된다 (A22)
    yield
    assert file_hash(REAL_TODO_FILE) == REAL_TODO_HASH, "실제 todo.json 이 테스트 중에 바뀌었습니다"
    assert file_hash(REAL_TEMPLATE_FILE) == REAL_TEMPLATE_HASH, "실제 task_templates.json 이 테스트 중에 바뀌었습니다"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def ui_data_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("ui") / "todo.json"
    path.write_text("[]", encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def ui_template_file(ui_data_file) -> Path:
    return ui_data_file.parent / "task_templates.json"


@pytest.fixture(scope="session")
def base_url(ui_data_file, ui_template_file):
    # UI 테스트용 서버 — 임시 TODO_FILE 로 uvicorn 을 띄운다 (pytest-playwright 의 base_url 을 덮어쓴다)
    port = free_port()
    env = {**os.environ, "TODO_FILE": str(ui_data_file), "TEMPLATE_FILE": str(ui_template_file)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{url}/todos", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("UI 테스트 서버가 뜨지 않았습니다")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def seed(ui_data_file, ui_template_file):
    # 서버는 요청마다 파일을 읽으므로 파일을 직접 써서 데이터를 준비한다 (템플릿은 seed.templates)
    def write(todos: list[dict]) -> None:
        ui_data_file.write_text(json.dumps(todos, ensure_ascii=False), encoding="utf-8")

    def write_templates(templates: list[dict]) -> None:
        ui_template_file.write_text(json.dumps(templates, ensure_ascii=False), encoding="utf-8")

    write([])
    write_templates([])
    write.templates = write_templates
    return write
