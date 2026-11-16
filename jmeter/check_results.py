"""JMeter 결과(.jtl, CSV) 판정 — JMeter 는 샘플이 실패해도 종료 코드 0 으로 끝나므로 Jenkins 가 이 스크립트로 판정한다.

사용: python3 jmeter/check_results.py <results.jtl> --expected <측정 샘플 수> [--expected-auth <인증 샘플 수>] [--max-error-rate 0]
- 결과 파일이 없거나, 헤더에 필요한 열이 없거나, 샘플 수가 기대값과 다르거나, 실패율이 기준을 넘으면 exit 1.
- (8.0.0) --expected-auth 를 주면 라벨이 'AUTH ' 로 시작하는 샘플(로그인·로그아웃)을 측정 샘플과 따로 센다(V8_SPEC §42.2):
  인증 샘플은 정확히 그 수이고 모두 성공해야 하며, --expected·실패율은 측정 샘플만으로 판정한다.
  주지 않으면 v7 과 같이 모든 샘플을 측정 샘플로 센다(인증 샘플이 섞이면 샘플 수가 맞지 않아 실패한다).
- 출력은 라벨·응답 코드·건수뿐이다(응답 본문·요청 본문은 출력하지 않는다).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REQUIRED = ("label", "responseCode", "success")
AUTH_PREFIX = "AUTH "


def ok(row: dict) -> bool:
    return row["success"].strip().lower() == "true"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jtl", type=Path)
    parser.add_argument("--expected", type=int, required=True, help="기대 측정 샘플 수 (THREADS x LOOPS x 샘플러 수)")
    parser.add_argument("--expected-auth", type=int, default=None,
                        help="(8.0.0) 기대 인증 샘플 수 (라벨 'AUTH ...', 예: THREADS x 2 = 로그인 + 로그아웃)")
    parser.add_argument("--max-error-rate", type=float, default=0.0, help="허용 실패율 %% (기본 0, 측정 샘플 기준)")
    args = parser.parse_args(argv)

    if not args.jtl.is_file() or args.jtl.stat().st_size == 0:
        print(f"JMETER_CHECK_FAIL: 결과 파일이 없거나 비어 있음 ({args.jtl})")
        return 1
    with args.jtl.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            print(f"JMETER_CHECK_FAIL: CSV 헤더에 {missing} 열이 없음 (jmeter.save.saveservice.output_format=csv 필요)")
            return 1
        rows = list(reader)

    split = args.expected_auth is not None
    auth = [r for r in rows if split and r["label"].startswith(AUTH_PREFIX)]
    measured = [r for r in rows if not (split and r["label"].startswith(AUTH_PREFIX))]
    by_label = Counter((r["label"], r["responseCode"], r["success"]) for r in rows)
    for (label, code, success), n in sorted(by_label.items()):
        print(f"  {label:<22} code={code:<4} success={success:<5} x{n}")

    total = len(measured)
    failed = [r for r in measured if not ok(r)]
    rate = (len(failed) / total * 100) if total else 100.0
    print(f"JMETER_SUMMARY: samples={total} expected={args.expected} failed={len(failed)} error_rate={rate:.2f}%")
    problems = []
    if split:
        auth_failed = [r for r in auth if not ok(r)]
        print(f"JMETER_AUTH: samples={len(auth)} expected={args.expected_auth} failed={len(auth_failed)} (지표·실패율에서 제외)")
        if len(auth) != args.expected_auth:
            problems.append(f"인증 샘플 수 {len(auth)} != 기대 {args.expected_auth}")
        if auth_failed:
            problems.append(f"인증 샘플 실패 {len(auth_failed)}건 (로그인 실패 스레드는 측정을 하지 않는다)")
    if total != args.expected:
        problems.append(f"샘플 수 {total} != 기대 {args.expected}")
    if rate > args.max_error_rate:
        problems.append(f"실패율 {rate:.2f}% > 허용 {args.max_error_rate}%")
    if problems:
        print("JMETER_CHECK_FAIL: " + "; ".join(problems))
        return 1
    print("JMETER_CHECK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
