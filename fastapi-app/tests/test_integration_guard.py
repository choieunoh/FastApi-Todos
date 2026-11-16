"""(8.0.0 3단계) 통합 테스트 대상 주소 필수화의 DB 없는 검증 — 외부 서버에 요청을 보내지 않는다.

- INTEGRATION_BASE_URL 이 없거나 빈 값·형식 오류면 실패하고, 이전 외부 서버 주소로 자동 대체하지 않는다.
- 주소 없이 `pytest -m integration` 을 돌려도 모든 테스트가 요청 전에 실패한다 — 자식 프로세스에서 소켓 연결을 막고
  연결 시도가 0건인지 기록으로 확인한다.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import test_integration as ti

APP_DIR = Path(__file__).resolve().parent.parent

# 자식 pytest 에 -p 로 넣는 플러그인: 모든 소켓 연결(DNS 조회 포함)을 막고 시도를 파일에 남긴다
BLOCKER = '''
import os, socket
LOG = os.environ["NET_BLOCK_LOG"]
def _record(kind, target):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{kind} {target!r}\\n")
def _connect(self, address):
    _record("connect", address)
    raise OSError("network blocked by test")
def _getaddrinfo(host, *args, **kwargs):
    _record("getaddrinfo", host)
    raise OSError("network blocked by test")
socket.socket.connect = _connect
socket.socket.connect_ex = _connect
socket.getaddrinfo = _getaddrinfo
socket.create_connection = lambda address, *a, **k: _connect(None, address)
'''


@pytest.mark.parametrize("environ", [{}, {"INTEGRATION_BASE_URL": ""}, {"INTEGRATION_BASE_URL": "   "},
                                     {"INTEGRATION_BASE_URL": "163.239.77.78:5014"},
                                     {"INTEGRATION_BASE_URL": "ftp://127.0.0.1:21"},
                                     {"INTEGRATION_BASE_URL": "http://127.0.0.1:8000/todos?x=1"}],
                         ids=["missing", "empty", "blank", "no-scheme", "wrong-scheme", "with-path"])
def test_base_url_is_required_and_validated(environ):
    with pytest.raises(pytest.fail.Exception, match="INTEGRATION_BASE_URL"):
        ti.require_base_url(environ)


@pytest.mark.parametrize("value, expected", [("http://127.0.0.1:51234", "http://127.0.0.1:51234"),
                                             (" http://127.0.0.1:51234/ ", "http://127.0.0.1:51234"),
                                             ("https://example.test", "https://example.test")])
def test_base_url_explicit_value_is_used_as_is(value, expected):
    assert ti.require_base_url({"INTEGRATION_BASE_URL": value}) == expected


def test_no_fallback_address_in_integration_module():
    source = (APP_DIR / "tests" / "test_integration.py").read_text(encoding="utf-8")
    assert "163.239" not in source and "5014" not in source
    assert 'environ.get(BASE_URL_ENV)' in source and "os.environ.get(\"INTEGRATION_BASE_URL\"," not in source


def test_integration_run_without_url_sends_no_request(tmp_path):
    (tmp_path / "netblock.py").write_text(BLOCKER, encoding="utf-8")
    log = tmp_path / "net.log"
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("PG") and k not in ("INTEGRATION_BASE_URL", "TEST_DATABASE_URL", "DATABASE_URL")}
    env.update(INTEGRATION_USERNAME="synthetic-user", INTEGRATION_PASSWORD="synthetic-password-1",   # 주소만 빠진 상태
               NET_BLOCK_LOG=str(log), PYTHONPATH=os.pathsep.join([str(tmp_path), env.get("PYTHONPATH", "")]))
    res = subprocess.run([sys.executable, "-m", "pytest", "-p", "netblock", "-p", "no:cacheprovider", "-q",
                          "-m", "integration", "tests/test_integration.py"],
                         cwd=APP_DIR, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=120)
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "9 errors" in out and "passed" not in out                     # 9개 모두 준비 단계(fixture)에서 실패
    assert "INTEGRATION_BASE_URL" in out
    assert not log.exists() or log.read_text(encoding="utf-8") == ""     # 연결·이름 조회 시도 0건
    assert "synthetic-password-1" not in out                             # 비밀번호는 출력되지 않는다
