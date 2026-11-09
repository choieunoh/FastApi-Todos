"""(7.0.0 Phase 3-2) PostgreSQL 테스트 공용 하네스 — tests/test_db_postgres.py 에서 옮겼다(본문 그대로).

- 대상은 db.TEST_DB_TARGET(todo_test_user@127.0.0.1:5433/todo_test, PostgreSQL 18.x) 하나뿐이다.
  운영 DB·개발 DB(todo_dev)·PostgreSQL 16(5432)에는 연결하지 않는다.
- R-19 게이트 판정은 db.verify_test_server·db.verify_test_schema(db.py)가 한다. 이 모듈은 호출만 하고 조건을 바꾸지 않는다.
- 이 모듈은 import 만으로 DB 에 연결하거나 스키마를 바꾸지 않는다. reset_test_schema 는 fixture 가 부를 때만 실행된다.
- _LIVE_SERVERS 는 같은 pytest 프로세스 안에서만 유효한 보조 방어다(R-19 게이트를 대신하지 않는다).
- 이름이 test_ 로 시작하지 않아 pytest 가 테스트로 수집하지 않는다.
"""
import base64
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

import auth
import db
from conftest import free_port

APP_DIR = Path(__file__).resolve().parent.parent
TEST_URL = os.environ.get("TEST_DATABASE_URL")

CONNECT_TIMEOUT = 10                              # 초 — 별도 연결의 접속 대기 상한 (libpq connect_timeout)
BACKEND_RELEASE_TIMEOUT = 10                      # 초 — 닫은 연결의 서버 백엔드가 사라질 때까지 기다리는 상한
_UNFINISHED_WORKERS: list[threading.Thread] = []  # join 뒤에도 살아 있는 작업 스레드 — 있으면 연결 수 확인을 하지 않는다


def wait_until_single_backend(engine) -> None:
    # (3-1B-2) fresh_schema 의 게이트 "앞"에서 읽기만 하며 기다린다 — 이 DB 의 연결 수(numbackends)가 점검 연결 1개로
    # 돌아올 때까지 BACKEND_RELEASE_TIMEOUT 안에서만. 대기 종료는 R-19 통과가 아니다(바로 뒤 게이트가 다시 판정한다).
    # 같은 역할의 다른 세션이 보이면 기다리지 않고 멈추고, 추가 백엔드의 정체는 추정하지 않는다. 쓰기는 하지 않는다.
    if any(t.is_alive() for t in _UNFINISHED_WORKERS):
        pytest.exit("끝나지 않은 작업 스레드가 있어 연결 수를 확인할 수 없습니다 — 이후 PostgreSQL 테스트를 진행하지 않습니다",
                    returncode=3)
    start = time.monotonic()
    while True:
        try:
            with engine.connect() as conn:                                   # 풀의 점검 연결 1개 — 매번 새 트랜잭션
                backends = conn.execute(text(db.TEST_SCHEMA_SQL["database_backends"])).scalar_one()
                others = conn.execute(text(db.TEST_SCHEMA_SQL["other_sessions"])).scalar_one()
                conn.rollback()                                              # 읽기만 — 스냅숏을 다음 반복으로 넘기지 않는다
        except SQLAlchemyError as exc:
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            safe = sqlstate if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum() else None
            pytest.exit(f"테스트 DB 보호: 게이트 전 연결 수 확인 실패 ({type(exc).__name__}, sqlstate={safe})", returncode=2)
        elapsed = time.monotonic() - start
        if type(backends) is not int or type(others) is not int:
            pytest.exit("테스트 DB 보호: 게이트 전 연결 수를 확인할 수 없음", returncode=2)
        if others != 0:
            pytest.exit(f"테스트 DB 보호: 같은 역할의 다른 세션 {others}개 (DB 연결 수 {backends}, {elapsed:.2f}초) "
                        "— 기다리지 않고 멈춘다", returncode=2)
        if backends == 1:
            return
        if elapsed >= BACKEND_RELEASE_TIMEOUT:
            pytest.exit(f"테스트 DB 보호: DB 연결 수 {backends} 가 {BACKEND_RELEASE_TIMEOUT}초 안에 1로 돌아오지 않음 "
                        f"(다른 세션 0, {elapsed:.2f}초) — 추가 백엔드의 정체는 확인하지 않음", returncode=2)
        time.sleep(0.05)


def nullpool_engine():
    # 두 번째 이후 연결용. 풀에 남기지 않고(NullPool) 접속 대기에 상한을 둔다. URL·대상 검사는 그대로다
    engine = db.make_engine(db.check_test_database_url(TEST_URL), poolclass=NullPool)

    @event.listens_for(engine, "do_connect")
    def _connect_timeout(dialect, conn_rec, cargs, cparams):
        cparams["connect_timeout"] = CONNECT_TIMEOUT

    return engine


def wait_for_backends_released(engine) -> None:
    # 별도 연결을 모두 닫은 "뒤"에 부른다. 다음 테스트의 R-19 게이트와 같은 조건(이 DB 연결 = 점검 연결 1개,
    # 다른 세션 0)으로 돌아올 때까지 제한 시간 동안 읽기만 한다. 게이트 자체는 바꾸지 않고, 세션을 끊지 않는다.
    # 끝나지 않은 작업 스레드가 있으면 그 연결이 언제 열리고 닫힐지 알 수 없으므로 확인하지 않고 세션을 멈춘다
    if any(t.is_alive() for t in _UNFINISHED_WORKERS):
        pytest.exit("끝나지 않은 작업 스레드가 있어 연결 수를 확인할 수 없습니다 — 이후 PostgreSQL 테스트를 진행하지 않습니다",
                    returncode=3)
    deadline = time.monotonic() + BACKEND_RELEASE_TIMEOUT
    while True:
        with engine.connect() as conn:                                       # 풀의 점검 연결 1개 — 매번 새 트랜잭션
            backends = conn.execute(text(db.TEST_SCHEMA_SQL["database_backends"])).scalar_one()
            others = conn.execute(text(db.TEST_SCHEMA_SQL["other_sessions"])).scalar_one()
            conn.rollback()
        if backends == 1 and others == 0:
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"닫은 연결이 {BACKEND_RELEASE_TIMEOUT}초 안에 정리되지 않음 (DB 연결 수 {backends}, 다른 세션 {others})")
        time.sleep(0.05)


def open_verified_engine() -> Engine:
    # (engine fixture 본문) URL(대상 고정) → 실제 서버(버전·주소·포트·DB·계정) 순서로 확인한 뒤에만 엔진을 돌려준다
    url = db.check_test_database_url(TEST_URL, os.environ.get("DATABASE_URL"))
    engine = db.make_engine(url)
    try:
        with engine.connect() as conn:
            db.verify_test_server(conn)
            db.verify_test_schema(conn)          # R-19 — 읽기 전용, 처음 지우기 전에 한 번 더 일찍 멈춘다
            conn.rollback()
    except (db.DatabaseConfigError, SQLAlchemyError) as e:
        engine.dispose()
        pytest.exit(f"테스트 DB 보호: {e}", returncode=2)   # 다른 테스트도 진행하지 않는다 (확인할 수 없어도 멈춘다)
    return engine


@contextmanager
def alembic_config() -> Iterator[Config]:
    # module 범위에서 쓰는 alembic_cfg 와 같은 설정 — env.py 가 읽는 DATABASE_URL 과 작업 폴더를 이 블록 안에서만 바꾼다
    if not TEST_URL:
        pytest.fail("TEST_DATABASE_URL 이 없어 Alembic 설정을 만들 수 없습니다")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", TEST_URL)
        mp.chdir(APP_DIR)
        yield Config(str(APP_DIR / "alembic.ini"))


# (3-2) before_drop 이 있을 때(UI 종료 reset)만 잠그는 표 — 고정 문장·고정 순서(이름순), 식별자를 조합하지 않는다
LOCK_TABLES_SQL = ("LOCK TABLE public.alembic_version, public.auth_failures, public.data_migrations, public.sessions, "
                   "public.subtasks, public.task_templates, public.todos, public.users IN ACCESS EXCLUSIVE MODE")
# (3-2 D안) UI 종료 reset 은 CASCADE 대신 알려진 8개 표만 RESTRICT 로 지우고, 빈 public 도 RESTRICT 로 지운다 —
# 예상 밖 객체·의존이 있으면 실패(rollback)해서 아무것도 지우지 않는다. CASCADE 로 다시 시도하지 않는다
DROP_KNOWN_TABLES_SQL = ("DROP TABLE public.alembic_version, public.auth_failures, public.data_migrations, "
                         "public.sessions, public.subtasks, public.task_templates, public.todos, public.users RESTRICT")
DROP_EMPTY_SCHEMA_SQL = "DROP SCHEMA public RESTRICT"


class ResetBlocked(Exception):
    # (3-2) 삭제 전 중단 — 고정 이벤트명과 안전한 값(검증한 SQLSTATE·표별 개수)만 담는다. SQL 원문·DETAIL·바인드 값 없음
    def __init__(self, event: str, detail: object, returncode: int) -> None:
        super().__init__(event)
        self.event, self.detail, self.returncode = event, detail, returncode


def _safe_sqlstate(exc: BaseException) -> str | None:
    state = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return state if isinstance(state, str) and len(state) == 5 and state.isalnum() else None


def reset_test_schema(engine: Engine, alembic_cfg: Config, *,
                      before_drop: Callable[[Connection], list[str]] | None = None) -> None:
    # (fresh_schema 본문) 빈 스키마에서 head 까지 올린다. 지우기 직전에 같은 연결·트랜잭션에서 서버와 스키마 내용(R-19)을
    # 다시 확인한다. 하나라도 어긋나거나 확인할 수 없으면 지우지 않고 세션 전체를 멈춘다.
    # (3-2) 같은 프로세스가 띄운 테스트 서버가 정리 확인 전이면 대기·게이트 전에 멈춘다(보조 방어, 게이트를 대신하지 않음)
    # (3-2) before_drop(UI 종료 reset 만): 게이트 뒤 같은 트랜잭션에서 8개 표를 잠그고, 잠금 아래에서 게이트를 다시 통과한 뒤
    # 콜백(읽기만, 트랜잭션 제어 없음)이 문제를 하나도 돌려주지 않을 때만 지운다. None 이면 실행 문장은 기존과 같다
    if _LIVE_SERVERS:
        pytest.exit(f"테스트 DB 보호: 정리를 확인하지 못한 테스트 서버(PID {sorted(_LIVE_SERVERS)})가 있어 "
                    "스키마를 초기화하지 않는다", returncode=3)
    wait_until_single_backend(engine)                                        # (3-1B-2) 읽기 전용 사전 대기 — 판정은 아래 게이트
    try:
        with engine.begin() as conn:
            db.verify_test_server(conn)
            db.verify_test_schema(conn)
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))   # 다른 세션이 잡고 있으면 기다리지 않고 실패
            if before_drop is not None:
                try:
                    conn.execute(text(LOCK_TABLES_SQL))                       # 쓰기 경쟁 차단 (lock_timeout 5초 안)
                    conn.execute(text("SELECT pg_stat_clear_snapshot()"))     # 통계·세션 정보 캐시만 비운다(데이터 스냅숏 아님)
                except SQLAlchemyError as exc:
                    raise ResetBlocked("reset_lock_failed", _safe_sqlstate(exc), 2) from None
                db.verify_test_schema(conn)                                   # 잠금 아래 재검사 — 구조·소유자·연결 수 1
                try:
                    problems = before_drop(conn)                              # 같은 연결·트랜잭션에서 출처 확인
                except SQLAlchemyError as exc:
                    raise ResetBlocked("reset_origin_query_failed", _safe_sqlstate(exc), 2) from None
                if problems:
                    raise ResetBlocked("reset_origin_mismatch", problems, 3)  # with 안에서 → rollback, DROP 미도달
                try:
                    conn.execute(text(DROP_KNOWN_TABLES_SQL))                 # 알려진 8개 표만 (RESTRICT)
                    conn.execute(text(DROP_EMPTY_SCHEMA_SQL))                 # 남은 객체가 있으면 거부 (RESTRICT)
                except SQLAlchemyError as exc:
                    raise ResetBlocked("reset_restrict_drop_failed", _safe_sqlstate(exc), 2) from None
            else:
                conn.execute(text("DROP SCHEMA public CASCADE"))                # 기본 경로(fresh_schema·시작 reset) 그대로
            conn.execute(text("CREATE SCHEMA public"))
    except ResetBlocked as e:
        pytest.exit(f"테스트 DB 보호: {e.event} — 삭제하지 않는다 ({e.detail})", returncode=e.returncode)
    except (db.DatabaseConfigError, SQLAlchemyError) as e:
        pytest.exit(f"테스트 DB 보호: {e}", returncode=2)
    command.upgrade(alembic_cfg, "head")                                     # 별도 연결·트랜잭션(T2) — 실패해도 T1 은 되돌릴 수 없다


USER_TABLES = ("users", "sessions", "todos", "subtasks", "task_templates", "auth_failures", "data_migrations")


def user_table_counts(engine: Engine) -> dict[str, int]:
    # 사용자 데이터 7개 표의 행 수 — 읽기만 한다(개수만, 내용은 읽지 않는다)
    with engine.connect() as conn:
        counts = {t: int(conn.execute(text(f'SELECT count(*) FROM public."{t}"')).scalar_one()) for t in USER_TABLES}
        conn.rollback()
    return counts


# ---------- 테스트 서버 (Phase 2-F 에서 옮김) ----------

SERVER_KEY_BYTES = secrets.token_bytes(32)                       # 이 실행에서만 쓰는 합성 키 (출력하지 않는다)
SERVER_KEY = base64.urlsafe_b64encode(SERVER_KEY_BYTES).rstrip(b"=").decode("ascii")
SERVER_FAILURE_KEY = auth.FailureKey(SERVER_KEY_BYTES)
SERVER_START_TIMEOUT = 30                                        # 초 — 기동·준비 확인 상한
SERVER_START_ATTEMPTS = 3                                        # 포트 충돌 등으로 바로 죽으면 새 포트로 다시 (상한)
SERVER_STOP_TIMEOUT = 10                                         # 초 — 정상 종료 대기, 강제 종료 뒤 확인 대기
SERVER_ENV_DROP = ("TEST_DATABASE_URL", "DATABASE_URL", "AUTH_FAILURE_KEY", "APP_ENV", "COOKIE_SECURE",
                   "REGISTRATION_ENABLED", "FORWARDED_ALLOW_IPS")
_STARTED_WORKER = re.compile(r"Started server process \[(\d+)\]")
_STARTED_PARENT = re.compile(r"Started parent process \[(\d+)\]")
_ACCESS = re.compile(r'ACCESS pid=(\d+) \S+ "(\w+) (\S+) [^"]*" (\d{3})')

SERVER_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "%(levelprefix)s [%(process)d] %(message)s",
                    "use_colors": False},
        "access": {"()": "uvicorn.logging.AccessFormatter",
                   "fmt": 'ACCESS pid=%(process)d %(client_addr)s "%(request_line)s" %(status_code)s',
                   "use_colors": False},
    },
    "handlers": {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        "todo.auth": {"handlers": ["default"], "level": "WARNING", "propagate": False},
    },
}


def server_database_url() -> str:
    # 자식 프로세스의 DATABASE_URL — 고정 대상 검사를 통과한 todo_test URL 만 (값은 출력하지 않는다)
    url = db.check_test_database_url(TEST_URL)
    assert (url.username, url.host, url.port, url.database) == (db.TEST_DB_USER, db.TEST_DB_HOST,
                                                                 db.TEST_DB_PORT, db.TEST_DB_NAME)
    return url.render_as_string(hide_password=False)


@dataclass
class Server:
    proc: subprocess.Popen
    port: int
    workers: int
    folder: Path
    worker_pids: list[int] = field(default_factory=list)
    parent_pid: int | None = None
    created: str | None = None                     # 감독(Popen) 프로세스의 생성 시각 (UTC ISO) — 식별에 쓴다
    identities: dict[int, str] = field(default_factory=dict)   # 확인된 워커 PID → 생성 시각

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def log_text(self) -> str:
        parts = []
        for name in ("stdout.log", "stderr.log"):
            path = self.folder / name
            parts.append(path.read_text(encoding="utf-8", errors="replace") if path.exists() else "")
        return "\n".join(parts)

    def access(self) -> list[tuple[int, str, str, int]]:
        # (워커 PID, 메서드, 경로, 상태) — 접근 로그에서만 읽는다
        return [(int(p), m, path, int(s)) for p, m, path, s in _ACCESS.findall(self.log_text())]

    def collect_pids(self) -> None:
        text = self.log_text()
        self.worker_pids = sorted({int(p) for p in _STARTED_WORKER.findall(text)})
        parents = {int(p) for p in _STARTED_PARENT.findall(text)}
        self.parent_pid = next(iter(parents)) if len(parents) == 1 else None


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    created: str                                   # UTC ISO 8601 ('o' 형식, 같은 형식끼리 문자열 비교 가능)
    cmd: str


_PROCESS_QUERY = (
    "$ErrorActionPreference='Stop'; "
    "Get-CimInstance Win32_Process -Filter \"Name='{name}'\" | ForEach-Object {{ [pscustomobject]@{{ "
    "pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId; "
    "created=$_.CreationDate.ToUniversalTime().ToString('o'); cmd=[string]$_.CommandLine }} }} "
    "| ConvertTo-Json -Compress"
)


def _process_table() -> dict[int, ProcInfo] | None:
    # 이 python 실행 파일과 같은 이름의 프로세스 메타데이터 (Windows CIM, 외부 패키지 없음). 확인할 수 없으면 None
    if os.name != "nt":
        return None
    script = _PROCESS_QUERY.format(name=Path(sys.executable).name.replace("'", ""))
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    raw = done.stdout.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    rows = data if isinstance(data, list) else [data]
    table = {}
    for row in rows:
        try:
            info = ProcInfo(int(row["pid"]), int(row["ppid"]), str(row["created"]), str(row.get("cmd") or ""))
        except (KeyError, TypeError, ValueError):
            return None
        table[info.pid] = info
    return table


def _is_supervisor(info: ProcInfo, server: Server) -> bool:
    # 이 테스트가 띄운 uvicorn 명령(같은 포트)이고, 처음 기록한 생성 시각과 같아야 한다
    return (info.pid == server.proc.pid and server.created is not None and info.created == server.created
            and "-m uvicorn main:app" in info.cmd and f"--port {server.port}" in info.cmd)


def _is_own_child(info: ProcInfo, server: Server) -> bool:
    # 부모 PID 하나만으로 정하지 않는다: 부모 PID + multiprocessing spawn 명령의 parent_pid + 감독 이후 생성
    return (server.created is not None and info.ppid == server.proc.pid and info.created >= server.created
            and "spawn_main" in info.cmd and f"parent_pid={server.proc.pid}" in info.cmd)


def _children(server: Server, table: dict[int, ProcInfo]) -> tuple[dict[int, ProcInfo], list[int]]:
    # (확인된 자식, 감독이 만든 것으로 보이지만 명령이 달라 확인되지 않는 PID)
    own, uncertain = {}, []
    for info in table.values():
        if info.pid == server.proc.pid or info.ppid != server.proc.pid:
            continue
        if server.created is not None and info.created < server.created:
            continue                                                 # 예전에 같은 PID 번호를 쓰던 부모의 자식 — 무관
        if _is_own_child(info, server):
            own[info.pid] = info
        else:
            uncertain.append(info.pid)
    return own, sorted(uncertain)


def _pid_present(pid: int) -> bool:
    # 프로세스 표를 얻지 못했을 때의 보조 확인 (종료 판단에만 쓰고, 이것으로 강제 종료하지 않는다)
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True                                                   # 모르면 남아 있다고 본다
    return f'"{pid}"' in out


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


_LIVE_SERVERS: dict[int, "Server"] = {}         # 감독 PID → 서버. 정리를 확인하기 전까지 살아 있다고 본다
EXTRA_ENV_KEYS = frozenset({"TODO_FILE", "TEMPLATE_FILE"})        # start_server 가 받는 추가 환경변수 (DB·키 관련은 받지 않는다)


def _check_extra_env(extra_env: Mapping[str, str] | None) -> None:
    if extra_env and set(extra_env) - EXTRA_ENV_KEYS:
        raise ValueError("start_server 가 받지 않는 환경변수: " + ", ".join(sorted(set(extra_env) - EXTRA_ENV_KEYS)))


def _start_once(folder: Path, workers: int, extra_env: Mapping[str, str] | None = None) -> Server:
    _check_extra_env(extra_env)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "log.json").write_text(json.dumps(SERVER_LOG_CONFIG), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in SERVER_ENV_DROP}
    env.update(APP_ENV="test", AUTH_FAILURE_KEY=SERVER_KEY, DATABASE_URL=server_database_url(),
               TODO_FILE=str(folder / "todo.json"), TEMPLATE_FILE=str(folder / "task_templates.json"))
    env.update(extra_env or {})                                      # 허용된 키(TODO_FILE·TEMPLATE_FILE)만
    port = free_port()
    with open(folder / "stdout.log", "wb") as out, open(folder / "stderr.log", "wb") as err:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
             "--workers", str(workers), "--no-proxy-headers", "--log-config", str(folder / "log.json")],
            cwd=APP_DIR, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    server = Server(proc=proc, port=port, workers=workers, folder=folder)
    _LIVE_SERVERS[proc.pid] = server                                 # Popen 직후 등록 — 정리가 확인될 때까지 살아 있다고 본다
    return server


def _record_identity(server: Server) -> None:
    # Popen 직후 감독의 생성 시각을 기록한다 (이 테스트가 띄운 명령인지도 함께). 확인하지 못하면 created 는 None
    table = _process_table()
    info = (table or {}).get(server.proc.pid)
    if info is not None and "-m uvicorn main:app" in info.cmd and f"--port {server.port}" in info.cmd:
        server.created = info.created


def _wait_ready(server: Server) -> str | None:
    # 준비되면 None, 아니면 이유. 워커 수만큼 PID 가 로그에 나오고 /auth/me 가 401 이어야 준비된 것
    deadline = time.monotonic() + SERVER_START_TIMEOUT
    while time.monotonic() < deadline:
        if server.proc.poll() is not None:
            return f"서버 프로세스가 바로 끝남 (exit {server.proc.returncode})"
        server.collect_pids()
        if len(server.worker_pids) >= server.workers:
            try:
                if httpx.get(server.base + "/auth/me", timeout=2).status_code == 401:
                    return None
            except httpx.HTTPError:
                pass
        time.sleep(0.2)
    return "준비 시간 초과 또는 워커 PID 수집 실패"


def _verify_workers(server: Server) -> str | None:
    # 로그의 워커 PID 가 모두 이 테스트가 띄운 프로세스인지 확인하고 생성 시각을 기록한다. 아니면 이유
    if os.name != "nt":
        return None
    table = _process_table()
    if table is None or server.created is None:
        return "프로세스 식별 정보(CIM)를 얻지 못함"
    for pid in server.worker_pids:
        info = table.get(pid)
        if info is None:
            return f"워커 PID {pid} 가 프로세스 목록에 없음"
        ok = _is_supervisor(info, server) if pid == server.proc.pid else _is_own_child(info, server)
        if not ok:
            return f"워커 PID {pid} 를 이 테스트의 프로세스로 확인하지 못함"
        server.identities[pid] = info.created
    return None


def start_server(folder: Path, workers: int, extra_env: Mapping[str, str] | None = None) -> Server:
    # 상한 있는 재시도. Popen 이후 어떤 예외(BaseException 포함)가 나도 그 시도의 프로세스를 정리하고 원래 예외를 다시 올린다.
    # 정리하지 못하면 다음 fresh_schema 가 돌지 않게 세션을 멈춘다
    reasons = []
    for attempt in range(SERVER_START_ATTEMPTS):
        server = _start_once(folder / f"try{attempt}", workers, extra_env)
        try:
            _record_identity(server)
            reason = _wait_ready(server) or _verify_workers(server)
        except BaseException:
            try:
                problems = stop_server(server)
            except BaseException as cleanup_error:                     # noqa: BLE001
                pytest.exit(f"기동 중 예외 뒤 서버 정리도 실패 ({type(cleanup_error).__name__}) — 로그: {server.folder}",
                            returncode=3)
            if problems:
                pytest.exit(f"기동 중 예외 뒤 서버를 정리하지 못했습니다 — {problems} (로그: {server.folder})",
                            returncode=3)
            raise
        if reason is None:
            return server
        reasons.append(reason)
        problems = stop_server(server)
        if problems:
            pytest.exit(f"기동 실패한 서버를 정리하지 못했습니다 — {problems} (로그: {server.folder})", returncode=3)
    pytest.fail(f"uvicorn 서버 기동 실패 ({SERVER_START_ATTEMPTS}회): {reasons} (로그: {folder})")


def stop_server(server: Server) -> list[str]:
    # 정상 종료(CTRL_BREAK) → 감독은 Popen 핸들로 종료(핸들이 열려 있어 PID 재사용 없음) → 자식은 식별을 확인한 것만
    # 하나씩 강제 종료 → 감독·자식 PID 와 포트가 사라졌는지 확인. 문제 목록을 돌려준다(빈 목록이면 정리됨).
    # taskkill /T 는 부모 PID 번호만 보고 트리를 만들므로 쓰지 않는다. 식별이 불확실한 프로세스는 죽이지 않고 문제로 보고한다
    problems: list[str] = []
    try:
        server.collect_pids()
    except OSError:
        problems.append("서버 로그를 읽지 못함")
    tracked: dict[int, str] = dict(server.identities)                   # PID → 생성 시각 (확인된 것만)
    table = _process_table()
    if table is not None:
        own, _ = _children(server, table)
        tracked.update({pid: info.created for pid, info in own.items()})   # 로그에 아직 안 나온 워커 포함
    # (3-1B) Windows 다중 워커 서버에는 콘솔 신호를 보내지 않는다 — 감독이 워커 PID(그룹 대표가 아님)에
    # CTRL_BREAK 를 다시 보내면 같은 콘솔의 pytest 까지 끝날 수 있다(exit 0xC000013A). 이 경우 정상 종료 단계를
    # 건너뛰고 아래의 기존 경로(핸들 기반 감독 종료 → 식별을 확인한 워커만 PID 별 종료 → PID·포트 확인)만 쓴다
    console_signal_ok = not (os.name == "nt" and server.workers > 1)
    if console_signal_ok and server.proc.poll() is None:
        try:
            if os.name == "nt":
                server.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                server.proc.terminate()
            server.proc.wait(SERVER_STOP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if server.proc.poll() is None:
        server.proc.kill()                                             # 핸들 기반 — 우리 프로세스만
        try:
            server.proc.wait(SERVER_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            problems.append(f"감독 PID {server.proc.pid} 종료 안 됨")
    deadline = time.monotonic() + SERVER_STOP_TIMEOUT
    uncertain: list[int] = []
    while True:
        table = _process_table()
        if table is not None:
            own, uncertain = _children(server, table)
            tracked.update({pid: info.created for pid, info in own.items()})
            alive = sorted(pid for pid, created in tracked.items()
                           if pid != server.proc.pid and pid in table and table[pid].created == created)
            for pid in alive:                                          # 직전에 PID·생성 시각을 다시 확인한 것만
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)
        else:
            alive = sorted(pid for pid in tracked if pid != server.proc.pid and _pid_present(pid))
        port_open = _port_open(server.port)
        if not alive and not uncertain and not port_open and server.proc.poll() is not None:
            break
        if time.monotonic() >= deadline:
            problems += [f"남은 워커 PID {alive}"] * bool(alive)
            problems += [f"식별되지 않은 자식 PID {uncertain} (강제 종료하지 않음)"] * bool(uncertain)
            problems += [f"포트 {server.port} 열림"] * port_open
            if table is None and alive:
                problems.append("프로세스 식별 정보(CIM)를 얻지 못해 강제 종료하지 않음")
            break
        time.sleep(0.5)
    if not problems:
        _LIVE_SERVERS.pop(server.proc.pid, None)                     # 정리를 확인한 서버만 목록에서 뺀다
    return problems


# ---------- (Phase 3-2) test_ui.py 용 PostgreSQL UI 서버 — 시작 0행 게이트·가입·로그인·종료 A′ ----------
# - 시작: 대상·서버·스키마 게이트(open_verified_engine) → 사용자 표 7개 0행(아니면 초기화하지 않고 멈춘다) → reset 1회.
# - 계정: 합성 사용자 가입 → 가입 세션 로그아웃 → 로그인 1회. 단계마다 기록해 종료 때 기대 행 수를 정한다.
# - 종료(A′): 서버 정리 확인 → _LIVE_SERVERS 비어 있음 → 연결 정리 → 읽기 전용 게이트 → 출처 확인(정확히 일치)
#   → reset 1회 → 0행 확인. 하나라도 실패하면 DROP 하지 않고 멈춘다(자동 재시도·수동 DELETE·추가 reset 없음).
# - (before_drop) 종료 reset 은 8개 표를 잠근 같은 트랜잭션에서 게이트·출처를 다시 확인한 뒤 지운다. 남는 위험:
#   잠금은 새 public 객체 생성(DDL)을 막지 못하고, T1(DROP·CREATE)과 T2(Alembic upgrade) 사이도 별도 연결이다.

UI_HTTP_TIMEOUT = 30.0                           # 초 — 가입·로그인은 Argon2 를 거친다
UI_CLIENT_IP = "127.0.0.1"                       # 하네스 서버는 --no-proxy-headers — TCP 상대 주소가 그대로 기록된다
# 단계별 사용자 표 7개의 정확한 기대 행 수 (3-2 는 할 일·템플릿이 JSON 이라 나머지 표는 0)
UI_EXPECTED_ROWS: dict[tuple[str, ...], dict[str, int]] = {
    (): {},
    ("registered",): {"users": 1, "sessions": 1, "auth_failures": 1},
    ("registered", "logged_out"): {"users": 1, "auth_failures": 1},
    ("registered", "logged_out", "logged_in"): {"users": 1, "sessions": 1, "auth_failures": 1},
}
# 출처 확인 — 읽기 전용 SELECT 만 (tests/test_db_schema.py 가 쓰기 문장이 없는지 확인한다). 값은 bind 로만 넘긴다
UI_ORIGIN_SQL = {
    "user": "SELECT id FROM public.users WHERE username = :username",
    "register_session": "SELECT count(*) FROM public.sessions WHERE user_id = :user_id",
    "login_session": "SELECT count(*) FROM public.sessions WHERE user_id = :user_id AND token_hash = :token_hash",
    "register_record": (
        "SELECT count(*) FROM public.auth_failures a JOIN public.users u ON a.created_at = u.created_at "
        "WHERE u.id = :user_id AND a.kind = 'register' AND a.username_key IS NULL AND a.client_ip = :client_ip "
        "AND a.created_at BETWEEN :t0 AND :t1"),
}


@dataclass
class UiAccount:
    # 합성 계정과 단계 기록 — 비밀값은 repr 에 넣지 않는다(실패 출력에 나오지 않게)
    username: str = field(default_factory=lambda: f"ui32_{secrets.token_hex(4)}")
    password: str = field(default_factory=lambda: secrets.token_urlsafe(18), repr=False)
    steps: list[str] = field(default_factory=list)
    token: str | None = field(default=None, repr=False)          # 지금 유효한 세션 토큰 (가입 → 로그인으로 바뀐다)
    csrf: str | None = field(default=None, repr=False)
    t0: datetime | None = field(default=None, repr=False)        # 가입 요청 직전·직후 (서버와 같은 OS 시계, UTC)
    t1: datetime | None = field(default=None, repr=False)

    def expected_rows(self) -> dict[str, int] | None:
        # 기록한 단계에 맞는 7개 표 기대 행 수. 단계 기록이 알려진 순서가 아니면 None (판정할 수 없음)
        extra = UI_EXPECTED_ROWS.get(tuple(self.steps))
        return None if extra is None else {t: extra.get(t, 0) for t in USER_TABLES}


@dataclass(frozen=True)
class UiSession:
    base: str
    cookie: dict = field(repr=False)                              # 브라우저 컨텍스트에 넣을 로그인 쿠키

    def storage_state(self) -> dict:
        return {"cookies": [dict(self.cookie)], "origins": []}


def _ui_post(server: Server, path: str, *, body: dict | None = None, token: str | None = None,
             csrf: str | None = None) -> httpx.Response:
    # 요청마다 새 연결 — Origin 은 서버 주소, 쿠키·CSRF 는 명시한 것만 보낸다
    headers = {"Origin": server.base, "Connection": "close"}
    if token is not None:
        headers["Cookie"] = f"{auth.COOKIE_NAME_DEV}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return httpx.post(server.base + path, json=body, headers=headers, timeout=UI_HTTP_TIMEOUT)


def _ui_session_token(res: httpx.Response, step: str) -> tuple[str, str]:
    token = res.cookies.get(auth.COOKIE_NAME_DEV)
    csrf = res.json().get("csrf_token") if res.headers.get("content-type", "").startswith("application/json") else None
    if not token or not csrf:
        pytest.fail(f"PostgreSQL UI 준비: {step} 응답에 세션 쿠키·CSRF 가 없음")
    return token, csrf


def ui_register_logout_login(server: Server, account: UiAccount) -> None:
    # 가입 1회 → 가입 세션 로그아웃 1회 → 로그인 1회. 성공한 단계만 account.steps 에 남긴다(상태 코드만 보고한다)
    creds = {"username": account.username, "password": account.password}
    account.t0 = datetime.now(timezone.utc)
    res = _ui_post(server, "/auth/register", body=creds)
    account.t1 = datetime.now(timezone.utc)
    if res.status_code != 201:
        pytest.fail(f"PostgreSQL UI 준비: 가입 응답 {res.status_code}")
    account.token, account.csrf = _ui_session_token(res, "가입")
    account.steps.append("registered")

    res = _ui_post(server, "/auth/logout", token=account.token, csrf=account.csrf)
    if res.status_code != 204:
        pytest.fail(f"PostgreSQL UI 준비: 가입 세션 로그아웃 응답 {res.status_code}")
    account.token = account.csrf = None
    account.steps.append("logged_out")

    res = _ui_post(server, "/auth/login", body=creds)                   # 쿠키 없이 — 새 세션 1개만 생긴다
    if res.status_code != 200:
        pytest.fail(f"PostgreSQL UI 준비: 로그인 응답 {res.status_code}")
    account.token, account.csrf = _ui_session_token(res, "로그인")
    account.steps.append("logged_in")


def ui_browser_cookie(account: UiAccount) -> dict:
    # Playwright storage_state 형식의 로그인 쿠키 (값은 출력하지 않는다). 브라우저 세션 쿠키 — 만료 판정은 서버가 한다
    return {"name": auth.COOKIE_NAME_DEV, "value": account.token, "domain": UI_CLIENT_IP, "path": "/",
            "expires": -1, "httpOnly": True, "secure": False, "sameSite": "Lax"}


def verify_gate_readonly(engine: Engine) -> None:
    # 읽기 전용 트랜잭션에서 서버·스키마 게이트(R-19)만 — 실패하거나 확인할 수 없으면 멈춘다
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            db.verify_test_server(conn)
            db.verify_test_schema(conn)
            conn.rollback()
    except (db.DatabaseConfigError, SQLAlchemyError) as e:
        pytest.exit(f"테스트 DB 보호: 종료 전 게이트 실패 — {e}", returncode=2)


def ui_origin_problems(conn: Connection, account: UiAccount) -> list[str]:
    # 남은 행이 이 모듈이 만든 것과 정확히 같은지 주어진 연결에서 읽기만 해서 확인한다. 문제 목록(표 이름·개수만)을 돌려준다.
    # reset_test_schema 의 before_drop 으로도 쓰이므로 commit·rollback·새 연결·SET TRANSACTION 을 하지 않는다
    expected = account.expected_rows()
    if expected is None:
        return ["단계 기록을 판정할 수 없음"]
    problems: list[str] = []
    actual = {t: int(conn.execute(text(f'SELECT count(*) FROM public."{t}"')).scalar_one()) for t in USER_TABLES}
    if actual != expected:
        problems.append(f"행 수 {actual} ≠ 기대 {expected}")
    if account.steps:
        ids = conn.execute(text(UI_ORIGIN_SQL["user"]), {"username": account.username}).scalars().all()
        if len(ids) != 1:
            problems.append(f"합성 사용자 행 {len(ids)}개")
        else:
            user_id = ids[0]
            if account.steps[-1] == "registered":
                sessions = conn.execute(text(UI_ORIGIN_SQL["register_session"]), {"user_id": user_id}).scalar_one()
            elif account.steps[-1] == "logged_in":
                sessions = conn.execute(text(UI_ORIGIN_SQL["login_session"]), {
                    "user_id": user_id, "token_hash": auth.hash_token(account.token)}).scalar_one()
            else:
                sessions = 0
            if sessions != expected["sessions"]:
                problems.append(f"이 계정의 세션 {sessions}개 ≠ 기대 {expected['sessions']}")
            record = conn.execute(text(UI_ORIGIN_SQL["register_record"]), {
                "user_id": user_id, "client_ip": UI_CLIENT_IP, "t0": account.t0, "t1": account.t1}).scalar_one()
            if record != 1:
                problems.append(f"가입 기록(종류·IP·생성 시각 일치) {record}개")
    return problems


def verify_ui_origin(engine: Engine, account: UiAccount) -> list[str]:
    # 잠금 없이 미리 하는 출처 확인 — 읽기 전용 트랜잭션에서 ui_origin_problems 를 부르고 되돌린다
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        problems = ui_origin_problems(conn, account)
        conn.rollback()
    return problems


def _stop_ui_server(server: Server) -> list[str]:
    # 정리 중 예외도 문제로 기록한다 — 다시 던지지 않는다(원래 실패를 가리지 않게)
    try:
        return stop_server(server)
    except BaseException as exc:                                         # noqa: BLE001
        return [f"정리 중 예외 {type(exc).__name__}"]


def _finish_ui_session(engine: Engine, account: UiAccount, problems: list[str], cause: str) -> None:
    # 종료 A′ — 아래가 모두 통과할 때만 reset_test_schema 1회. 실패하면 DROP 없이 멈춘다
    if problems or _LIVE_SERVERS:
        pytest.exit(f"테스트 DB 보호: UI 서버 정리를 확인하지 못해 스키마를 초기화하지 않는다 — {problems}{cause}",
                    returncode=3)
    try:
        wait_for_backends_released(engine)
    except pytest.fail.Exception as exc:
        pytest.exit(f"테스트 DB 보호: 서버 종료 뒤 DB 연결이 정리되지 않아 초기화하지 않는다 — {exc}{cause}", returncode=3)
    verify_gate_readonly(engine)
    origin = verify_ui_origin(engine, account)
    if origin:
        pytest.exit(f"테스트 DB 보호: 남은 데이터의 출처를 확인하지 못해 삭제하지 않는다 — {origin}{cause}", returncode=3)
    with alembic_config() as cfg:                                        # 잠금 아래에서 같은 출처 확인을 한 번 더 (before_drop)
        reset_test_schema(engine, cfg, before_drop=lambda conn: ui_origin_problems(conn, account))
    left = {t: n for t, n in user_table_counts(engine).items() if n}
    if left:
        pytest.fail(f"종료 초기화 뒤 사용자 데이터가 남음 {left}")


@contextmanager
def ui_server_session(folder: Path, todo_file: Path, template_file: Path) -> Iterator[UiSession]:
    # test_ui.py 모듈 하나의 PostgreSQL UI 서버(workers=1). 할 일·템플릿은 3-2 동안 JSON(seed) 그대로다
    if not TEST_URL:
        pytest.fail("TEST_DATABASE_URL 이 없어 PostgreSQL UI 테스트를 시작할 수 없습니다 (skip 하지 않는다)")
    if os.environ.get("DATABASE_URL"):
        pytest.exit("테스트 DB 보호: DATABASE_URL 이 설정되어 있어 PostgreSQL UI 테스트를 시작하지 않는다", returncode=2)
    engine = open_verified_engine()                                      # 대상 고정·서버·스키마 게이트
    try:
        before = {t: n for t, n in user_table_counts(engine).items() if n}
        if before:
            pytest.exit(f"테스트 DB 보호: 시작 전 사용자 데이터가 있어 초기화하지 않는다 {before}", returncode=3)
        with alembic_config() as cfg:
            reset_test_schema(engine, cfg)                               # 시작 reset 1회
        after = {t: n for t, n in user_table_counts(engine).items() if n}
        if after:
            pytest.exit(f"테스트 DB 보호: 시작 초기화 뒤 사용자 데이터가 있음 {after}", returncode=3)
        account = UiAccount()
        server = start_server(folder, 1, extra_env={"TODO_FILE": str(todo_file), "TEMPLATE_FILE": str(template_file)})
        setup_error: BaseException | None = None
        problems: list[str] = []
        try:
            try:
                ui_register_logout_login(server, account)
            except BaseException as exc:                                 # noqa: BLE001 — 서버를 내린 뒤 다시 던진다
                setup_error = exc
            if setup_error is None:
                yield UiSession(base=server.base, cookie=ui_browser_cookie(account))
        finally:
            problems += _stop_ui_server(server)                          # 정상·실패 모두 서버를 내린다
        cause = f" (준비 단계 오류 {type(setup_error).__name__})" if setup_error is not None else ""
        _finish_ui_session(engine, account, problems, cause)
        if setup_error is not None:
            raise setup_error
    finally:
        engine.dispose()                                                 # pytest.exit·fail 이어도 엔진은 닫는다
