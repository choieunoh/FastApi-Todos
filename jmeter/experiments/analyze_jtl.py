"""읽기 전용 동시성 실험의 JMeter 결과(.jtl, CSV) 분석 — 표준 라이브러리만 사용한다.

사용
  # 실험 1개 판정 + 요약 CSV 에 한 줄 기록 (판정 실패면 exit 1)
  python3 analyze_jtl.py check --label u1 --users 1 --jtl u1/results.jtl --expected 100 [--max-avg-ms 1000] --csv summary.csv
  # 요약 CSV → Markdown 표
  python3 analyze_jtl.py table --csv summary.csv --md summary.md

판정(check) — 하나라도 어기면 실패
  - 결과 파일이 있고 CSV 헤더에 timeStamp, elapsed, label, responseCode, success 열이 있다
  - 전체 샘플 수 == --expected (u1=100, u3=300, u5=500)
  - 라벨은 'GET /', 'GET /todos' 두 개뿐이고 각각 expected/2 건 (읽기 전용 계획인지 확인 — POST 가 섞이면 실패)
  - 실패 샘플 0건, 응답 코드는 모두 200
  - --max-avg-ms 를 주면 전체 평균 응답 시간이 그 값 이하

계산
  - 평균·최소·최대 응답 시간(ms) = elapsed 기준
  - p95 = nearest-rank (정렬한 값의 ceil(0.95·n) 번째)
  - 실제 처리량(요청/초) = 샘플 수 / ((마지막 샘플의 timeStamp+elapsed) − (첫 샘플의 timeStamp)) 초
출력에는 라벨·코드·숫자만 있다(응답 본문·요청 본문은 읽지 않는다).
"""
import argparse
import csv
import math
import sys
from collections import Counter
from pathlib import Path

REQUIRED = ("timeStamp", "elapsed", "label", "responseCode", "success")
LABELS = ("GET /", "GET /todos")
FIELDS = ("label", "users", "samples", "expected", "success", "failure", "error_rate_pct",
          "avg_ms", "p95_ms", "min_ms", "max_ms", "throughput_rps", "duration_s",
          "avg_ms_get_root", "avg_ms_get_todos", "p95_ms_get_root", "p95_ms_get_todos", "status")


def p95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.95 * len(ordered))) - 1]


def analyze(jtl: Path) -> dict:
    if not jtl.is_file() or jtl.stat().st_size == 0:
        raise ValueError(f"결과 파일이 없거나 비어 있음: {jtl}")
    with jtl.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV 헤더에 {missing} 열이 없음 (jmeter.save.saveservice.output_format=csv 필요)")
        rows = [r for r in reader]
    if not rows:
        raise ValueError("샘플이 0건")
    elapsed = [int(r["elapsed"]) for r in rows]
    start = min(int(r["timeStamp"]) for r in rows)
    end = max(int(r["timeStamp"]) + int(r["elapsed"]) for r in rows)
    duration = max((end - start) / 1000.0, 0.001)
    ok = [r for r in rows if r["success"].strip().lower() == "true"]
    by_label = {lbl: [int(r["elapsed"]) for r in rows if r["label"] == lbl] for lbl in LABELS}
    return {
        "samples": len(rows),
        "success": len(ok),
        "failure": len(rows) - len(ok),
        "error_rate_pct": round((len(rows) - len(ok)) / len(rows) * 100, 2),
        "avg_ms": round(sum(elapsed) / len(elapsed), 1),
        "p95_ms": p95(elapsed),
        "min_ms": min(elapsed),
        "max_ms": max(elapsed),
        "throughput_rps": round(len(rows) / duration, 2),
        "duration_s": round(duration, 1),
        "avg_ms_get_root": round(sum(by_label["GET /"]) / len(by_label["GET /"]), 1) if by_label["GET /"] else "",
        "avg_ms_get_todos": round(sum(by_label["GET /todos"]) / len(by_label["GET /todos"]), 1) if by_label["GET /todos"] else "",
        "p95_ms_get_root": p95(by_label["GET /"]) if by_label["GET /"] else "",
        "p95_ms_get_todos": p95(by_label["GET /todos"]) if by_label["GET /todos"] else "",
        "_labels": Counter(r["label"] for r in rows),
        "_codes": Counter((r["label"], r["responseCode"]) for r in rows),
    }


def judge(m: dict, expected: int, max_avg_ms: float | None) -> list[str]:
    problems = []
    if m["samples"] != expected:
        problems.append(f"샘플 수 {m['samples']} != 기대 {expected}")
    extra = sorted(set(m["_labels"]) - set(LABELS))
    if extra:
        problems.append(f"읽기 전용 계획에 없는 라벨 {extra}")
    for lbl in LABELS:
        if m["_labels"].get(lbl, 0) != expected // 2:
            problems.append(f"'{lbl}' {m['_labels'].get(lbl, 0)}건 != 기대 {expected // 2}")
    if m["failure"]:
        problems.append(f"실패 샘플 {m['failure']}건 (오류율 {m['error_rate_pct']}%)")
    bad_codes = {k: v for k, v in m["_codes"].items() if k[1] != "200"}
    if bad_codes:
        problems.append(f"200 이 아닌 응답 {dict(bad_codes)}")
    if max_avg_ms is not None and m["avg_ms"] > max_avg_ms:
        problems.append(f"평균 응답 시간 {m['avg_ms']}ms > 한도 {max_avg_ms}ms")
    return problems


def write_row(csv_path: Path, row: dict) -> None:
    rows = []
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("label") != row["label"]]   # 같은 라벨은 새 값으로 교체
    rows.append({k: row.get(k, "") for k in FIELDS})
    rows.sort(key=lambda r: int(r["users"]) if str(r["users"]).isdigit() else 0)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def cmd_check(a: argparse.Namespace) -> int:
    try:
        m = analyze(Path(a.jtl))
    except (ValueError, KeyError) as e:
        print(f"ANALYZE_FAIL[{a.label}]: {e}")
        if a.csv:
            write_row(Path(a.csv), {"label": a.label, "users": a.users, "expected": a.expected, "status": "FAIL"})
        return 1
    problems = judge(m, a.expected, a.max_avg_ms)
    status = "FAIL" if problems else "OK"
    print(f"RESULT[{a.label}] users={a.users} samples={m['samples']}/{a.expected} success={m['success']} "
          f"failure={m['failure']} error_rate={m['error_rate_pct']}% avg={m['avg_ms']}ms p95={m['p95_ms']}ms "
          f"min={m['min_ms']}ms max={m['max_ms']}ms throughput={m['throughput_rps']}req/s duration={m['duration_s']}s")
    print(f"  per-label: GET / avg={m['avg_ms_get_root']}ms p95={m['p95_ms_get_root']}ms | "
          f"GET /todos avg={m['avg_ms_get_todos']}ms p95={m['p95_ms_get_todos']}ms")
    print(f"  codes: {dict(sorted(m['_codes'].items()))}")
    if a.csv:
        write_row(Path(a.csv), {**m, "label": a.label, "users": a.users, "expected": a.expected, "status": status})
    if problems:
        print(f"ANALYZE_FAIL[{a.label}]: " + "; ".join(problems))
        return 1
    print(f"ANALYZE_OK[{a.label}]")
    return 0


def cmd_table(a: argparse.Namespace) -> int:
    path = Path(a.csv)
    if not path.is_file():
        print(f"TABLE_FAIL: {path} 없음")
        return 1
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    head = ("| 실험 | 동시 사용자 | 요청 수 | 성공 | 실패 | 오류율(%) | 평균(ms) | p95(ms) | 처리량(req/s) | 소요(s) | "
            "GET / 평균(ms) | GET /todos 평균(ms) | 판정 |")
    sep = "|" + "---|" * 13
    lines = [head, sep]
    for r in rows:
        lines.append(f"| {r['label']} | {r['users']} | {r['samples']}/{r['expected']} | {r['success']} | {r['failure']} | "
                     f"{r['error_rate_pct']} | {r['avg_ms']} | {r['p95_ms']} | {r['throughput_rps']} | {r['duration_s']} | "
                     f"{r['avg_ms_get_root']} | {r['avg_ms_get_todos']} | {r['status']} |")
    text = "\n".join(lines) + "\n"
    if a.md:
        Path(a.md).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="읽기 전용 동시성 실험 JTL 분석")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--label", required=True)
    c.add_argument("--users", type=int, required=True)
    c.add_argument("--jtl", required=True)
    c.add_argument("--expected", type=int, required=True)
    c.add_argument("--max-avg-ms", type=float, default=None)
    c.add_argument("--csv", default=None)
    t = sub.add_parser("table")
    t.add_argument("--csv", required=True)
    t.add_argument("--md", default=None)
    a = parser.parse_args()
    return cmd_check(a) if a.cmd == "check" else cmd_table(a)


if __name__ == "__main__":
    sys.exit(main())
