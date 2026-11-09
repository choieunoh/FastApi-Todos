"""JMeter 결과(.jtl, CSV) 판정 — JMeter 는 샘플이 실패해도 종료 코드 0 으로 끝나므로 Jenkins 가 이 스크립트로 판정한다.

사용: python3 jmeter/check_results.py <results.jtl> --expected <샘플 수> [--max-error-rate 0]
- 결과 파일이 없거나, 헤더에 필요한 열이 없거나, 샘플 수가 기대값과 다르거나, 실패율이 기준을 넘으면 exit 1.
- 출력은 라벨·응답 코드·건수뿐이다(응답 본문·요청 본문은 출력하지 않는다).
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REQUIRED = ("label", "responseCode", "success")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jtl", type=Path)
    parser.add_argument("--expected", type=int, required=True, help="기대 샘플 수 (THREADS x LOOPS x 샘플러 수)")
    parser.add_argument("--max-error-rate", type=float, default=0.0, help="허용 실패율 %% (기본 0)")
    args = parser.parse_args()

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

    total = len(rows)
    failed = [r for r in rows if r["success"].strip().lower() != "true"]
    by_label = Counter((r["label"], r["responseCode"], r["success"]) for r in rows)
    for (label, code, ok), n in sorted(by_label.items()):
        print(f"  {label:<14} code={code:<4} success={ok:<5} x{n}")

    rate = (len(failed) / total * 100) if total else 100.0
    print(f"JMETER_SUMMARY: samples={total} expected={args.expected} failed={len(failed)} error_rate={rate:.2f}%")
    problems = []
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
