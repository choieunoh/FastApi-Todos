"""(8.0.0 3단계) JMeter·Jenkins 로그인 절차의 정적 검증 — DB·JMeter·Docker 없이 돈다(V8_SPEC §42).

- 계획 2개(fastapi_test_plan.jmx, experiments/concurrency_readonly.jmx): 스레드마다 로그인 1회(Once Only) → csrf_token 추출 →
  실패하면 스레드 중지, 측정 샘플러만 InfluxDB 리스너 범위 안, 쓰기에 X-CSRF-Token, 마지막 반복에 로그아웃.
- 자격 증명: 속성 파일(600)·`-q` 로만 넘기고 명령줄 `-J` 에 넣지 않는다. 값은 계획·스크립트·출력에 없다.
- 판정: check_results.py·analyze_jtl.py 가 AUTH 샘플을 측정 샘플과 따로 센다.
실제 JMeter 실행·InfluxDB 분리 실측·Jenkins 빌드는 별도 승인 사항이다(여기서 하지 않는다).
"""
import csv
import importlib.util
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
JMETER = ROOT / "jmeter"
PLANS = {"main": JMETER / "fastapi_test_plan.jmx", "experiment": JMETER / "experiments" / "concurrency_readonly.jmx"}
MEASURED = {"main": ["GET /", "POST /todos", "GET /todos"], "experiment": ["GET /", "GET /todos"]}
AUTH_LABELS = ["AUTH POST /auth/login", "AUTH POST /auth/logout"]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def children(hash_tree: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    # JMX 의 hashTree 는 (요소, 그 요소의 hashTree) 가 번갈아 온다
    kids = list(hash_tree)
    return [(kids[i], kids[i + 1]) for i in range(0, len(kids), 2)]


def thread_group(kind: str) -> list[tuple[ET.Element, ET.Element]]:
    plan_tree = children(ET.parse(PLANS[kind]).getroot().find("hashTree"))[0][1]
    (tg, tg_tree), = [(e, t) for e, t in children(plan_tree) if e.tag == "ThreadGroup"]
    return children(tg_tree)


def named(pairs, tag: str):
    return [(e, t) for e, t in pairs if e.tag == tag]


def prop(el: ET.Element, name: str) -> str | None:
    found = el.find(f"*[@name='{name}']")
    return None if found is None else (found.text or "")


@pytest.mark.parametrize("kind", PLANS)
def test_plan_logs_in_once_and_scopes_metrics_to_measured_samplers(kind):
    body = thread_group(kind)
    tags = [e.tag for e, _ in body]
    assert "CookieManager" in tags
    (cookie, _), = named(body, "CookieManager")
    assert prop(cookie, "CookieManager.clearEachIteration") == "false"          # 세션을 반복 사이에 유지
    # 로그인: Once Only 안에 샘플러 1개, 200 확인, csrf 추출, 실패하면 스레드 중지
    (once, once_tree), = named(body, "OnceOnlyController")
    (login, login_tree), = children(once_tree)
    assert login.get("testname") == "AUTH POST /auth/login"
    assert (prop(login, "HTTPSampler.method"), prop(login, "HTTPSampler.path")) == ("POST", "/auth/login")
    login_body = login.find(".//stringProp[@name='Argument.value']").text
    assert "props.get('AUTH_USERNAME')" in login_body and "props.get('AUTH_PASSWORD')" in login_body
    assert "JsonOutput.toJson" in login_body                                    # 값은 JSON 문자열로 이스케이프
    parts = {e.tag: e for e, _ in children(login_tree)}
    assert prop(parts["ResponseAssertion"], "Asserion.test_strings") is not None
    assert parts["ResponseAssertion"].find(".//stringProp").text == "200"
    assert prop(parts["JSONPostProcessor"], "JSONPostProcessor.referenceNames") == "csrf"
    assert prop(parts["JSONPostProcessor"], "JSONPostProcessor.jsonPathExprs") == "$.csrf_token"
    assert parts["ResultAction"].find("intProp[@name='OnError.action']").text == "1"   # 1 = Stop Thread
    # 측정: "Measured" 안에 리스너 1개 + 측정 샘플러(v7 라벨 그대로)
    (measured, measured_tree), = named(body, "GenericController")
    assert measured.get("testname") == "Measured"
    inner = children(measured_tree)
    assert [e.tag for e, _ in inner][0] == "BackendListener"
    assert [e.get("testname") for e, _ in inner if e.tag == "HTTPSamplerProxy"] == MEASURED[kind]
    regex = inner[0][0].find(".//elementProp[@name='samplersRegex']/stringProp[@name='Argument.value']").text
    assert all(re.search(regex, lbl) for lbl in MEASURED[kind]) and not any(re.search(regex, a) for a in AUTH_LABELS)
    # Backend Listener 는 계획 전체에서 이것 하나뿐 (최상위 등 AUTH 를 볼 수 있는 자리에 없다)
    assert len(list(ET.parse(PLANS[kind]).getroot().iter("BackendListener"))) == 1
    # 로그아웃: 마지막 반복의 If Controller 안, CSRF 헤더, 204
    (cond, cond_tree), = named(body, "IfController")
    # (2026-11-16) 스레드 그룹 이름에 기대는 __jm__<이름>__idx 는 JMX 의 반복 컨트롤러에 이름이 없으면 null 이다(로컬 JMeter
    # 실측: NullPointerException → 로그아웃 0/10). 이름과 무관한 vars.getIteration()(1부터)을 쓴다. 기본 반복 수는 계획마다 그대로
    default_loops = {"main": "1", "experiment": "50"}[kind]
    assert prop(cond, "IfController.condition") == (
        "${__groovy(vars.getIteration() == (props.get('LOOPS') ?: '" + default_loops + "').toInteger())}")
    assert prop(cond, "IfController.useExpression") == "true"
    loop = ET.parse(PLANS[kind]).getroot().find(".//elementProp[@name='ThreadGroup.main_controller']")
    assert prop(loop, "LoopController.loops") == "${__P(LOOPS," + default_loops + ")}"   # 조건식 기본값과 같은 반복 수
    assert "__jm__" not in PLANS[kind].read_text(encoding="utf-8")
    (logout, logout_tree), = children(cond_tree)
    assert logout.get("testname") == "AUTH POST /auth/logout"
    headers = [(h.find("stringProp[@name='Header.name']").text, h.find("stringProp[@name='Header.value']").text)
               for h in logout_tree.iter("elementProp") if h.get("elementType") == "Header"]
    assert headers == [("X-CSRF-Token", "${csrf}")]
    assert tags.index("OnceOnlyController") < tags.index("GenericController") < tags.index("IfController")


@pytest.mark.parametrize("kind", PLANS)
def test_plan_writes_carry_csrf_and_no_origin_or_secret(kind):
    root = ET.parse(PLANS[kind]).getroot()
    text = PLANS[kind].read_text(encoding="utf-8")
    assert "Origin" not in re.findall(r'Header\.name">([^<]+)<', text)          # 로그인은 Origin 없이(허용)
    assert "__P(AUTH" not in text                                              # -J 로 받는 형태를 두지 않는다
    if kind == "main":
        tree = thread_group(kind)
        (measured, measured_tree), = named(tree, "GenericController")
        post = [t for e, t in children(measured_tree) if e.get("testname") == "POST /todos"][0]
        assert ("X-CSRF-Token", "${csrf}") in [
            (h.find("stringProp[@name='Header.name']").text, h.find("stringProp[@name='Header.value']").text)
            for h in post.iter("elementProp") if h.get("elementType") == "Header"]
    else:
        methods = {(e.get("testname"), prop(e, "HTTPSampler.method")) for e in root.iter("HTTPSamplerProxy")}
        assert methods == {("GET /", "GET"), ("GET /todos", "GET"), (AUTH_LABELS[0], "POST"), (AUTH_LABELS[1], "POST")}


# ---------- 자격 증명 속성 파일 ----------

@pytest.fixture(scope="module")
def props_module():
    return load("auth_properties", JMETER / "auth_properties.py")


@pytest.mark.parametrize("value", ['p"a\\ss=wo:rd#!', " leading space", "한글비번🙂", "line\nbreak\ttab", "plain-Pass_123"])
def test_auth_properties_escape_round_trip(props_module, value):
    escaped = props_module.escape(value)
    assert escaped.isascii() and "\n" not in escaped
    assert props_module.unescape(escaped) == value


def test_auth_properties_write_is_600_new_file_and_prints_no_values(props_module, tmp_path, monkeypatch, capsys):
    path = tmp_path / "auth.properties"
    monkeypatch.setenv("JM_AUTH_USER", "jm-user")
    monkeypatch.setenv("JM_AUTH_PASS", "S3cret=\"x\"")
    assert props_module.main(["write", str(path)]) == 0
    assert props_module.read(str(path)) == {"AUTH_USERNAME": "jm-user", "AUTH_PASSWORD": "S3cret=\"x\""}
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600
    assert props_module.main(["write", str(path)]) == 1                       # 덮어쓰지 않는다
    out = capsys.readouterr().out
    assert "S3cret" not in out and "jm-user" not in out


def test_auth_properties_without_env_and_tty_fails(props_module, tmp_path, monkeypatch):
    monkeypatch.delenv("JM_AUTH_USER", raising=False)
    monkeypatch.delenv("JM_AUTH_PASS", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert props_module.main(["write", str(tmp_path / "a.properties")]) == 1
    assert not (tmp_path / "a.properties").exists()


# ---------- 판정 스크립트 ----------

def write_jtl(path: Path, rows: list[tuple[str, str, bool]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timeStamp", "elapsed", "label", "responseCode", "success"])
        for i, (label, code, ok) in enumerate(rows):
            w.writerow([1_700_000_000_000 + i * 10, 5, label, code, "true" if ok else "false"])
    return path


def main_rows(threads: int = 2, login_ok: bool = True, logout_ok: bool = True) -> list[tuple[str, str, bool]]:
    rows = []
    for _ in range(threads):
        rows.append(("AUTH POST /auth/login", "200" if login_ok else "401", login_ok))
        rows += [("GET /", "200", True), ("POST /todos", "201", True), ("GET /todos", "200", True)]
        rows.append(("AUTH POST /auth/logout", "204" if logout_ok else "403", logout_ok))
    return rows


@pytest.fixture(scope="module")
def check_module():
    return load("check_results", JMETER / "check_results.py")


def test_check_results_counts_auth_separately(check_module, tmp_path, capsys):
    jtl = write_jtl(tmp_path / "r.jtl", main_rows())
    assert check_module.main([str(jtl), "--expected", "6", "--expected-auth", "4"]) == 0
    out = capsys.readouterr().out
    assert "JMETER_SUMMARY: samples=6 expected=6 failed=0" in out and "JMETER_AUTH: samples=4 expected=4 failed=0" in out
    assert check_module.main([str(jtl), "--expected", "6"]) == 1                     # v7 방식이면 AUTH 가 섞여 10 != 6


@pytest.mark.parametrize("rows, args, reason", [
    (main_rows(logout_ok=False), ["--expected", "6", "--expected-auth", "4"], "인증 샘플 실패"),
    (main_rows()[:-1], ["--expected", "6", "--expected-auth", "4"], "인증 샘플 수 3 != 기대 4"),
    ([r for r in main_rows() if r[0] != "GET /todos"], ["--expected", "6", "--expected-auth", "4"], "샘플 수 4 != 기대 6"),
], ids=["auth-failed", "auth-missing", "measured-short"])
def test_check_results_fails_on_auth_or_measured_problems(check_module, tmp_path, capsys, rows, args, reason):
    jtl = write_jtl(tmp_path / "r.jtl", rows)
    assert check_module.main([str(jtl), *args]) == 1
    assert reason in capsys.readouterr().out


def test_analyze_jtl_excludes_auth_from_metrics(tmp_path, capsys):
    analyze = load("analyze_jtl", JMETER / "experiments" / "analyze_jtl.py")
    rows = []
    for _ in range(3):
        rows.append(("AUTH POST /auth/login", "200", True))
        rows += [("GET /", "200", True), ("GET /todos", "200", True)] * 2
        rows.append(("AUTH POST /auth/logout", "204", True))
    m = analyze.analyze(write_jtl(tmp_path / "u3.jtl", rows))
    assert m["samples"] == 12 and sum(m["_auth_labels"].values()) == 6
    assert analyze.judge(m, 12, None, 6) == []
    assert any("인증 샘플 수" in p for p in analyze.judge(m, 12, None, 4))
    assert analyze.judge(m, 12, None) == []                                         # 인증 기대값을 안 주면 검사하지 않는다


# ---------- Jenkins 사본·실험 스크립트 ----------

@pytest.mark.parametrize("path", [ROOT / "ci" / "Jenkinsfile.team3004", JMETER / "experiments" / "run_concurrency_experiment.sh"])
def test_scripts_pass_credentials_only_by_protected_file(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"-J\s*AUTH|-JAUTH|AUTH_PASSWORD=", text)                # 명령줄 -J 없음
    # JM_AUTH_PASS 값 대입은 (C안) generated 모드의 셸 안 무작위 생성 한 곳만 허용 — 고정 값·명령줄 대입 없음
    assert [m.group(0) for m in re.finditer(r"JM_AUTH_PASS=[^\n]{0,20}", text)] in (
        [], ['JM_AUTH_PASS="$(openssl rand -bas'])
    assert "-q /secrets/auth.properties" in text and "/secrets/auth.properties:ro" in text
    assert "--expected-auth" in text
    assert re.search(r"stat -c %a [^\n]*600", text)                                # 권한 600 확인
    # (2026-11-16) jmeter.log 판정: ERROR·FATAL 에 더해 WARN 으로만 남는 Groovy 오류도 실패
    assert " (ERROR|FATAL) |Error running groovy script" in text


def test_log_failure_pattern_catches_groovy_warning():
    pattern = re.compile(r" (ERROR|FATAL) |Error running groovy script")
    warn = "2026-11-15 23:59:18,869 WARN o.a.j.f.Groovy: Error running groovy script"      # 로컬 실측 줄
    assert pattern.search(warn) and pattern.search("x ERROR o.a.j.Y: z") and not pattern.search("x INFO o.a.j.Y: z")


# ---------- (2026-11-16 C안) Jenkins 사본 — 빌드 전용 일회용 v8 대상 ----------
# 실제 Jenkins·Docker·InfluxDB 없이 사본 텍스트만 검사한다. 이전 test_jenkinsfile_binds_credentials_and_removes_file 의
# 확인(Credentials 바인딩 유지·700/600·워크스페이스 밖·정리·AUTH 쿼리·작은따옴표 sh)은 아래 테스트들이 이어서 본다.

JENKINSFILE = ROOT / "ci" / "Jenkinsfile.team3004"


def jenkinsfile() -> str:
    return JENKINSFILE.read_text(encoding="utf-8")


def stage_text(name: str) -> str:
    text = jenkinsfile()
    start = text.index(f"stage('{name}')")
    ends = [i for i in (text.find("\n        stage('", start + 1), text.find("\n    post {", start + 1)) if i != -1]
    return text[start:min(ends)] if ends else text[start:]                              # 마지막 단계는 파이프라인 post 앞까지


def groovy_sh_blocks(text: str) -> list[str]:
    # ''' … ''' (작은따옴표 Groovy 문자열) 중 '#!/bin/bash' 로 시작하는 블록 → Groovy 이스케이프(\\ → \, \' → ')를 풀어 셸 원문으로
    blocks = []
    for raw in re.findall(r"'''(.*?)'''", text, re.S):
        if not raw.startswith("#!/bin/bash"):
            continue
        out, i = [], 0
        while i < len(raw):
            if raw[i] == "\\" and i + 1 < len(raw) and raw[i + 1] in "\\'":
                out.append(raw[i + 1])
                i += 2
            else:
                out.append(raw[i])
                i += 1
        blocks.append("".join(out))
    return blocks


def test_jenkins_checkout_uses_allowed_branch_and_keeps_checks():
    text = jenkinsfile()
    assert "CHECKOUT_BRANCH    = 'feature/v8.0.0'" in text
    assert "git branch: env.CHECKOUT_BRANCH, url: 'https://github.com/choieunoh/FastApi-Todos.git'" in text
    assert "git branch: 'main'" not in text
    verify = stage_text("Verify Checkout")
    assert '[[ "$CHECKOUT_BRANCH" =~ $ALLOWED_BRANCHES ]]' in verify
    assert "ALLOWED_BRANCHES   = '^(main|feature/v8\\\\.0\\\\.0)$'" in text           # main 과 v8 브랜치만
    assert 'git rev-parse "refs/remotes/origin/${CHECKOUT_BRANCH}"' in verify
    assert "refs/remotes/origin/main" not in verify                                    # origin/main 고정 비교 없음
    assert 'git merge-base --is-ancestor "$REQUIRED_COMMIT" HEAD' in verify            # REQUIRED_COMMIT 유지
    assert '[ -z "$(git status --porcelain --untracked-files=no)" ]' in verify        # 깨끗한 작업 트리 유지
    assert "sys.version_info >= (3, 8)" in verify                                     # 호스트 python3 3.8+


def test_jenkins_has_no_jmeter_run_against_team_server():
    text = jenkinsfile()
    assert re.findall(r'-JTARGET_HOST="?([^"\s\\]+)', text) == ["${TARGET}"]          # JMeter 대상 지정은 한 곳뿐
    jm = stage_text("Run JMeter Load Test (ephemeral v8)")
    assert 'TARGET="${EPH_ID}-app"' in jm and '-JTARGET_PORT="${EPH_APP_PORT}"' in jm
    assert "TEAM_HOST" not in jm and "TEAM_PORT" not in jm
    assert "stage('Run JMeter Load Test')" not in text                                 # 팀 서버 대상 JMeter 단계 제거
    team = stage_text("Verify Team Deployment")                                        # 팀 서버는 GET 확인만
    assert re.findall(r"curl [^\n]*", team) and not re.search(r"-X\s*(POST|PUT|DELETE)|--data|-d ", team)
    assert "TEAM_APP_VERSION   = '7.0.0'" in text


def test_jenkins_isolation_gate_runs_before_jmeter():
    text = jenkinsfile()
    order = [text.index(f"stage('{n}')") for n in ("Build Team Image", "Start Ephemeral v8 Target",
                                                    "Prepare Test Account", "Isolation Gate",
                                                    "Run JMeter Load Test (ephemeral v8)")]
    assert order == sorted(order)
    gate = stage_text("Isolation Gate")
    for needle in ('[[ "$TARGET" =~ ^todo-eph-team3004-[0-9]+-app$ ]]',             # 대상은 이 빌드의 일회용 앱
                   '[[ "$TARGET" != *"$TEAM_HOST"* ]]',                               # 팀 서버 주소 아님
                   '"DATABASE_URL=postgresql://eph_admin@${EPH_ID}-pg:5432/todo_eph"',  # DB 는 일회용 PG
                   '"TODO_FILE=/app/data/todo.json"', "'{{len .Mounts}}' \"${EPH_ID}-app\")\" = 0",   # 실제 JSON 마운트 없음
                   '[ -z "$(docker port "$c")" ]',                                     # 호스트 포트 공개 없음
                   "{{.Internal}}", '"$CI_BUILD_LABEL"'):                              # 내부 네트워크·이 빌드 라벨
        assert needle in gate, needle
    code = "\n".join(groovy_sh_blocks(gate))                                           # 주석이 아닌 셸 코드만
    assert code and "PGPASSWORD" not in code and "AUTH_FAILURE_KEY" not in code        # 비밀 환경변수는 읽지 않는다


def test_jenkins_ephemeral_resources_are_build_scoped_and_unpublished():
    text = jenkinsfile()
    start = stage_text("Start Ephemeral v8 Target")
    assert 'EPH_ID             = "todo-eph-team3004-${BUILD_NUMBER}"' in text
    assert 'CI_BUILD_LABEL     = "${BUILD_TAG}"' in text
    assert 'docker network create --internal "${LBL[@]}" "${EPH_ID}-net"' in start
    assert start.count('"${LBL[@]}"') >= 5                                              # net·pg·migrate·app·probe 에 라벨
    joined = "\n".join(groovy_sh_blocks(start)).replace("\\\n", " ")                    # 셸 줄 이음을 합친 뒤 docker run 명령만
    runs = [c for c in joined.splitlines() if "docker run" in c]
    assert len(runs) >= 4 and not any(re.search(r"\s(-p|--publish|-P)(\s|=)", c) for c in runs)   # 호스트 포트 공개 없음
    assert " -v " not in start and "--mount" not in start                               # 볼륨·bind 마운트 없음
    assert "--tmpfs /app/data" in start and "--tmpfs /var/lib/postgresql" in start
    assert start.index("alembic upgrade head") < start.index('docker run -d --name "${EPH_ID}-app"')   # 마이그레이션 뒤 앱
    assert "재사용하지 않는다" in start                                                   # 같은 이름이 있으면 멈춤
    assert re.search(r"postgresql://eph_admin@\$\{EPH_ID\}-pg", start) and ":${" not in start.split("DB_URL=")[1].split("\n")[0]


def test_jenkins_cleanup_only_removes_this_builds_labelled_resources():
    text = jenkinsfile()
    cleanup = re.search(r"CLEANUP_EPH_SH = '''(.*?)'''", text, re.S).group(1)
    assert '"jm-eph-team3004-${BUILD_NUMBER}" "${EPH_ID}-probe" "${EPH_ID}-app" "${EPH_ID}-migrate" "${EPH_ID}-pg"' in cleanup
    # 컨테이너·네트워크 삭제가 각각 바로 앞의 라벨 비교 안에 있어야 한다(라벨이 다르면 지우지 않는다)
    assert 'if [ "$lab" = "$CI_BUILD_LABEL" ]; then\n    docker rm -f -v "$c"' in cleanup
    assert 'if [ "$lab" = "$CI_BUILD_LABEL" ]; then\n    docker network rm "${EPH_ID}-net"' in cleanup
    assert cleanup.count('if [ "$lab" = "$CI_BUILD_LABEL" ]; then') == 2 and cleanup.count("EPH_CLEANUP_SKIP") == 2
    assert cleanup.count("docker rm -f") == 1 and cleanup.count("docker network rm") == 1
    assert "prune" not in cleanup and "docker volume rm" not in cleanup
    assert '/jm-auth-${BUILD_NUMBER}' in cleanup and '/eph-team3004-${BUILD_NUMBER}' in cleanup
    post = text[text.rindex("\n    post {"):]
    assert "sh CLEANUP_EPH_SH" in post and "always" in post                            # 성공·실패·중단 모두


def test_jenkins_account_modes_and_secret_handling():
    text = jenkinsfile()
    acct = stage_text("Prepare Test Account")
    assert "env.JM_AUTH_MODE == 'credentials'" in acct and "env.JM_AUTH_MODE == 'generated'" in acct
    assert "withCredentials([usernamePassword(credentialsId: env.JM_AUTH_CRED_ID," in acct   # 바인딩 경로 유지
    assert "usernameVariable: 'JM_AUTH_USER', passwordVariable: 'JM_AUTH_PASS'" in acct
    assert "JM_AUTH_MODE       = 'generated'" in text
    prep = re.search(r"PREPARE_ACCOUNT_SH = '''(.*?)'''", text, re.S).group(1)
    assert "mkdir -m 700" in prep and "umask 077" in prep and "${WORKSPACE_TMP:?}/jm-auth-" in prep
    assert "os.O_EXCL, 0o600" in prep and 'rm -f "$AUTHDIR/register.json"' in prep
    assert "--data @/secrets/register.json" in prep and "register.json:ro" in prep
    assert "unset JM_AUTH_USER JM_AUTH_PASS" in prep
    for block in groovy_sh_blocks(text):                                                # 값이 출력·명령줄로 새지 않게
        assert not re.search(r"echo[^\n]*\$\{?(JM_AUTH_PASS|JM_AUTH_USER|PGPASSWORD|AUTH_FAILURE_KEY)", block)
        assert "set -x" not in block and "-JAUTH" not in block
    assert 'sh """' not in text and "sh \"#!" not in text                               # Groovy 보간 없는 작은따옴표 sh


def test_jenkins_judgement_and_influx_scope():
    jm = stage_text("Run JMeter Load Test (ephemeral v8)")
    assert '--expected "$EXPECTED_MEASURED"' in jm and '--expected-auth "$EXPECTED_AUTH"' in jm
    assert "EXPECTED_MEASURED=$(( JM_THREADS * JM_LOOPS * JM_SAMPLERS ))" in jm
    assert '"AUTH POST /auth/login": "200", "AUTH POST /auth/logout": "204"' in jm
    assert "JM_LOG_FAIL=' (ERROR|FATAL) |Error running groovy script'" in jm
    assert '-H 127.0.0.1 -P 9 -N "${TARGET}|influxdb"' in jm                          # 다른 주소로는 요청 불가
    assert 'docker network connect "$JMETER_NET" "$JMC"' in jm
    assert "foreign" in jm                                                              # JTL URL 이 모두 일회용 앱
    assert 'JM_APP_NAME        = "fastapi-todos-eph-team3004-${BUILD_NUMBER}"' in jenkinsfile()
    assert "time >= ${RUN_START_NS} AND time <= ${RUN_END_NS}" in jm                  # 이 빌드 구간만
    assert "INFLUX_QUERY_FAIL" in jm and "|| { echo \"JMETER_FAIL: InfluxDB 쿼리 실패" in jm   # 쿼리 실패 ≠ 0건
    assert '\\\\"transaction\\\\" =~ /^AUTH /' in jm                                    # AUTH 0건
    assert '[ "${s:-0}" = "$EXPECTED_MEASURED" ]' in jm                               # all/all 합계 = 측정 30


def test_jenkins_shell_blocks_parse():
    import shutil
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash 없음 — 셸 문법 검사를 할 수 없다")                          # 조건부(CI 이미지·개발 PC 에는 있다)
    blocks = groovy_sh_blocks(jenkinsfile())
    assert len(blocks) >= 10
    for i, block in enumerate(blocks):
        res = subprocess.run([bash, "-n"], input=block, capture_output=True, text=True, encoding="utf-8")
        assert res.returncode == 0, (i, block.splitlines()[1:3], res.stderr[:300])
