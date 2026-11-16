"""(8.0.0) JMeter 로그인용 임시 속성 파일 — 비밀번호를 명령줄(-J)·콘솔·저장소에 넣지 않기 위해 쓴다(V8_SPEC §42.1).

사용
  # 쓰기: 값은 환경변수(Jenkins Credentials 바인딩 JM_AUTH_USER / JM_AUTH_PASS) 또는 대화형 입력(getpass)으로만 받는다
  python3 jmeter/auth_properties.py write <출력 경로>
  # (실험 스크립트용) 같은 파일로 로그인해 할 일 개수만 출력한다 — 읽기 전용(GET /todos), 끝나면 로그아웃
  python3 jmeter/auth_properties.py todo-count <http://호스트:포트> <속성 파일>

규칙
- 새 파일만 만든다(이미 있으면 실패). 권한은 처음부터 600 (os.open O_CREAT|O_EXCL, mode 0o600). 부모 디렉터리는 호출자가 만든다.
- 키는 AUTH_USERNAME·AUTH_PASSWORD. 값은 java.util.Properties 형식으로 이스케이프한다(\\, 줄바꿈, 앞 공백, = : # !,
  ASCII 밖 문자는 \\uXXXX). JMeter 는 `-q <파일>` 로 읽고 계획은 props.get() 으로 쓴다.
- 이 스크립트는 값·파일 내용·응답 본문을 출력하지 않는다(경로·개수·상태 코드만).
"""
from __future__ import annotations

import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

KEYS = ("AUTH_USERNAME", "AUTH_PASSWORD")
ENV = {"AUTH_USERNAME": "JM_AUTH_USER", "AUTH_PASSWORD": "JM_AUTH_PASS"}


def escape(value: str) -> str:
    # java.util.Properties.store 와 같은 규칙(값 쪽). ASCII 만 남겨 파일 인코딩과 무관하게 읽힌다
    out = []
    for i, ch in enumerate(value):
        if ch == "\\":
            out.append("\\\\")
        elif ch in "\n\r\t\f":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\f": "\\f"}[ch])
        elif ch == " " and i == 0:
            out.append("\\ ")
        elif ch in "=:#!":
            out.append("\\" + ch)
        elif 0x20 <= ord(ch) <= 0x7E:
            out.append(ch)
        else:
            for unit in ch.encode("utf-16-be").hex(" ", 2).split():   # BMP 밖 문자는 대리 쌍 두 개
                out.append("\\u" + unit.upper())
    return "".join(out)


def unescape(text: str) -> str:
    # escape() 의 역 (이 모듈이 쓴 파일을 읽는 데만 쓴다)
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch != "\\" or i + 1 == len(text):
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt == "u":
            out.append(chr(int(text[i + 2:i + 6], 16)))
            i += 6
            continue
        out.append({"n": "\n", "r": "\r", "t": "\t", "f": "\f"}.get(nxt, nxt))
        i += 2
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16")   # 대리 쌍을 한 글자로 합친다


def read(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="ascii") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            values[key] = unescape(value)
    missing = [k for k in KEYS if not values.get(k)]
    if missing:
        raise ValueError(f"속성 파일에 {missing} 값이 없음")
    return values


def write(path: str, values: dict[str, str]) -> None:
    if any(not values.get(k) for k in KEYS):
        raise ValueError("아이디·비밀번호가 비어 있음")
    body = "".join(f"{k}={escape(values[k])}\n" for k in KEYS)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # 이미 있으면 FileExistsError
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
        f.write(body)
    if os.name == "posix":
        os.chmod(path, 0o600)                                         # umask 와 무관하게 600


def values_from_env_or_prompt() -> dict[str, str]:
    values = {k: os.environ.get(env, "") for k, env in ENV.items()}
    if not all(values.values()) and not sys.stdin.isatty():
        raise ValueError("JM_AUTH_USER·JM_AUTH_PASS 환경변수가 없고 대화형 입력도 아님 (Jenkins 는 Credentials 바인딩 확인)")
    if not values["AUTH_USERNAME"]:
        values["AUTH_USERNAME"] = input("테스트 계정 아이디: ")
    if not values["AUTH_PASSWORD"]:
        values["AUTH_PASSWORD"] = getpass.getpass("테스트 계정 비밀번호(화면에 표시되지 않음): ")
    return values


def todo_count(base: str, path: str) -> int:
    values = read(path)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def send(method: str, url: str, body: dict | None = None, csrf: str | None = None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = json.dumps(body).encode() if body is not None else None
        return opener.open(urllib.request.Request(base + url, data=data, headers=headers, method=method), timeout=10)

    with send("POST", "/auth/login", {"username": values["AUTH_USERNAME"], "password": values["AUTH_PASSWORD"]}) as res:
        csrf = json.load(res)["csrf_token"]
    try:
        with send("GET", "/todos") as res:
            return len(json.load(res))
    finally:
        send("POST", "/auth/logout", csrf=csrf).close()


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "write":
        try:
            write(argv[1], values_from_env_or_prompt())
        except FileExistsError:
            print(f"AUTH_PROPS_FAIL: 이미 있는 파일은 덮어쓰지 않음 ({argv[1]})")
            return 1
        except ValueError as e:
            print(f"AUTH_PROPS_FAIL: {e}")
            return 1
        print(f"AUTH_PROPS_OK: {argv[1]} (권한 600, 값은 출력하지 않음)")
        return 0
    if len(argv) == 3 and argv[0] == "todo-count":
        try:
            print(todo_count(argv[1], argv[2]))
        except urllib.error.HTTPError as e:
            print(f"?(HTTP {e.code})")
        except (OSError, ValueError, KeyError):
            print("?")
        return 0
    print(__doc__.split("규칙")[0].strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
