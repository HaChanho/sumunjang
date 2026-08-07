"""명령줄 인터페이스.

표준 라이브러리 argparse만 쓴다. 종료 코드는 CI 게이트로 쓸 수 있게 정한다.
  0 통과 / 1 개인정보 검출 / 2 잘못된 사용 / 3 리소스 오류
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import IO

from .detect import detect
from .mask import Session, mask, restore


# 채점 대상 골든셋. 셋을 나눠 두는 이유는 점수의 의미가 서로 다르기 때문이다.
#   goldenset       회귀 기준선. 여기가 깨지면 이미 되던 것이 망가진 것이다.
#   goldenset-hard  표기 변형·오탐 함정. 새로 만든 규칙을 시험한다.
#   goldenset-gaps  못 잡는다고 선언한 것들. 0점으로 나오는 것이 정상이다.
# 한 표에 합치면 "쉬운 것만 골랐다" 는 반문에도, "왜 점수가 낮냐" 는 반문에도
# 답할 수 없다. 나눠서 각각의 의미를 붙인다.
DEFAULT_GOLDENSETS = ("goldenset", "goldenset-hard", "goldenset-gaps")


def default_goldensets() -> list[Path]:
    """기본 골든셋이 실제로 놓여 있는 자리를 찾는다.

    같은 파일이 배포 형태에 따라 세 군데에 놓인다.

      1. 작업 디렉토리 — 저장소 안에서 작업할 때. 고친 것이 바로 채점돼야 하므로
         가장 앞에 둔다.
      2. 패키지 디렉토리 — `pip install sumunjang` 한 경우. wheel 에 함께 싣는다
         (pyproject 의 force-include). 저장소를 클론하지 않고도 README 의 수치를
         재현할 수 있어야 "재현 가능한 자체 평가" 라는 말이 성립한다.
      3. 패키지의 조부모 디렉토리 — `pip install -e .` 로 소스에서 실행할 때.
         src/sumunjang/cli.py 의 두 단계 위가 저장소 루트다.
    """
    package = Path(__file__).resolve().parent
    bases = (Path.cwd(), package, package.parents[1])
    found = []
    for name in DEFAULT_GOLDENSETS:
        for base in bases:
            if (base / name).is_dir():
                found.append(base / name)
                break
    return found


def _read_input(source: str, stdin: IO[str]) -> str:
    if source == "-":
        return stdin.read()
    with open(source, encoding="utf-8") as handle:
        return handle.read()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sumunjang",
        description="AI로 나가는 텍스트에서 한국 개인정보를 막는 수문장",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="개인정보를 찾기만 한다 (CI 게이트용)")
    scan.add_argument("source", help="파일 경로 또는 - (표준 입력)")
    scan.add_argument(
        "--show-values",
        action="store_true",
        help="찾은 값을 그대로 출력한다 (기본은 카테고리·좌표만)",
    )

    mask_cmd = sub.add_parser("mask", help="개인정보를 가명값으로 바꾼다")
    mask_cmd.add_argument("source", help="파일 경로 또는 - (표준 입력)")

    report = sub.add_parser("report", help="골든셋으로 탐지 성능을 채점한다")
    report.add_argument(
        "directories",
        nargs="*",
        default=list(DEFAULT_GOLDENSETS),
        help=f"골든셋 디렉토리 (기본: {' '.join(DEFAULT_GOLDENSETS)})",
    )
    report.add_argument("--json", action="store_true", help="JSON으로 출력")
    report.add_argument(
        "--min-recall",
        type=float,
        metavar="비율",
        help="셋별 재현율이 이 값 아래로 내려가면 종료 코드 1 (CI 회귀 게이트)",
    )
    report.add_argument(
        "--min-precision",
        type=float,
        metavar="비율",
        help="셋별 정밀도가 이 값 아래로 내려가면 종료 코드 1",
    )
    report.add_argument(
        "--only",
        metavar="이름",
        action="append",
        help="임계값을 적용할 셋 이름 (여러 번 지정 가능). 생략하면 전부",
    )

    proxy = sub.add_parser("proxy", help="AI API 경계 게이트웨이를 띄운다")
    proxy.add_argument("--port", type=int, default=4000)
    proxy.add_argument("--host", default="127.0.0.1")
    proxy.add_argument(
        "--upstream",
        default="https://api.anthropic.com",
        help="업스트림 API 주소",
    )
    proxy.add_argument(
        "--dump",
        metavar="파일",
        help="업스트림으로 나간 본문을 기록한다 (이미 가려진 본문이므로 원문은 남지 않는다)",
    )

    return parser


def _score_directory(directory: str) -> tuple[int, dict[str, dict]]:
    """디렉토리 하나를 채점해 (문서 수, 카테고리별 지표) 를 돌려준다."""
    from .goldenset import Span, load_directory, score

    documents = load_directory(directory)
    truth: list[Span] = []
    found: list[Span] = []
    for document in documents:
        truth.extend(document.spans)
        found.extend(
            Span(f.category, f.start, f.end, document.doc_id) for f in detect(document.text)
        )

    return len(documents), score(truth, found)


def _totals(result: dict[str, dict]) -> tuple[int, int, int]:
    return (
        sum(m["expected"] for m in result.values()),
        sum(m["detected"] for m in result.values()),
        sum(m["hit"] for m in result.values()),
    )


def _print_table(directory: str, count: int, result: dict[str, dict], stdout: IO[str]) -> None:
    expected, detected, hit = _totals(result)
    print(f"\n## {Path(directory).name}\n", file=stdout)
    print(f"- 문서 {count}건, 정답 {expected}건", file=stdout)
    print(f"- 재현 방법: `sumunjang report {directory}`\n", file=stdout)
    print("| 카테고리 | 정답 | 탐지 | 적중 | 오탐 | 재현율 | 정밀도 |", file=stdout)
    print("|---|---:|---:|---:|---:|---:|---:|", file=stdout)
    for category, metric in result.items():
        print(
            f"| {category} | {metric['expected']} | {metric['detected']} | {metric['hit']} | "
            f"{metric['false_positive']} | {metric['recall']:.3f} | {metric['precision']:.3f} |",
            file=stdout,
        )
    if expected:
        # 정밀도의 분모는 탐지 건수다. 한 건도 탐지하지 않았다면 정밀도는 정의되지
        # 않으므로 1.000 이 아니라 0.000 으로 적는다 — 아무것도 안 한 것을 만점으로
        # 읽게 두면 안 된다.
        print(
            f"| **전체** | {expected} | {detected} | {hit} | {detected - hit} | "
            f"{hit / expected:.3f} | {hit / detected if detected else 0.0:.3f} |",
            file=stdout,
        )


_LIMITS = """
## 이 수치의 한계

- 골든셋은 개발자가 자체 제작한 합성 문서다. 도구를 만든 사람이 정답도 만들었으므로
  이 점수는 독립적인 성능 증명이 아니라 회귀를 감시하는 기준선이다.
- 셋마다 의미가 다르다. `goldenset` 은 회귀 기준선이고, `goldenset-hard` 는 표기
  변형과 오탐 함정을 시험하며, `goldenset-gaps` 는 **못 잡는다고 선언한 것들**이라
  점수가 낮은 것이 정상이다. 만점 셋만 내놓으면 "쉬운 것만 골랐다" 는 반문에
  답할 수 없으므로 0점 셋을 나란히 공개한다.
- 스팬이 정확히 일치할 때만 적중으로 센다. 부분 일치를 인정하면 "절반만 가린"
  결과가 성공으로 계산된다. 다만 남은 절반이 실제로 새어나갔는지는 이 표가 아니라
  누출 속성 테스트(`tests/test_leak.py`)가 검사한다.
"""


def _report(args, stdout: IO[str], stderr: IO[str]) -> int:
    import json as json_module

    explicit = args.directories != list(DEFAULT_GOLDENSETS)
    targets = [str(d) for d in (args.directories if explicit else default_goldensets())]
    if not targets:
        print(f"골든셋 디렉토리가 없습니다: {' '.join(args.directories)}", file=stderr)
        return 3

    scored = []
    for directory in targets:
        try:
            count, result = _score_directory(directory)
        except OSError as exc:
            print(f"골든셋을 읽지 못했습니다: {exc}", file=stderr)
            return 3
        if not count:
            print(f"골든셋 문서가 없습니다: {directory}", file=stderr)
            return 3
        scored.append((directory, count, result))

    if args.json:
        print(
            json_module.dumps(
                {
                    Path(directory).name: {
                        "documents": count,
                        "categories": result,
                        "total": dict(
                            zip(("expected", "detected", "hit"), _totals(result))
                        ),
                    }
                    for directory, count, result in scored
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=stdout,
        )
        return 0

    print("# 수문장 탐지 성능 자체 평가", file=stdout)
    for directory, count, result in scored:
        _print_table(directory, count, result, stdout)
    print(_LIMITS, file=stdout)

    return _check_thresholds(args, scored, stderr)


def _check_thresholds(args, scored, stderr: IO[str]) -> int:
    """공표한 수치 아래로 내려가면 실패한다.

    점수를 찍기만 하면 회귀를 아무도 못 본다. README 에 적은 값이 곧 계약이므로
    CI 가 그 계약을 지키는지 확인해야 한다. gaps 셋은 낮은 것이 정상이라 --only
    로 대상을 고른다.
    """
    if args.min_recall is None and args.min_precision is None:
        return 0

    실패 = []
    for directory, _, result in scored:
        이름 = Path(directory).name
        if args.only and 이름 not in args.only:
            continue
        expected, detected, hit = _totals(result)
        재현율 = hit / expected if expected else 0.0
        정밀도 = hit / detected if detected else 0.0
        if args.min_recall is not None and 재현율 < args.min_recall:
            실패.append(f"{이름}: 재현율 {재현율:.3f} < {args.min_recall:.3f}")
        if args.min_precision is not None and 정밀도 < args.min_precision:
            실패.append(f"{이름}: 정밀도 {정밀도:.3f} < {args.min_precision:.3f}")

    for 줄 in 실패:
        print(f"기준 미달: {줄}", file=stderr)
    return 1 if 실패 else 0


def run(
    argv: list[str],
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    if args.command == "scan":
        try:
            text = _read_input(args.source, stdin)
        except OSError as exc:
            print(f"입력을 읽지 못했습니다: {exc}", file=stderr)
            return 3

        findings = detect(text)
        for finding in findings:
            # 기본은 카테고리와 좌표만 낸다. scan 은 CI 게이트로 쓰이는데,
            # 찾은 값을 그대로 찍으면 개인정보가 빌드 로그에 영구히 남는다.
            # 개인정보를 막겠다는 도구가 새 유출 경로를 만드는 셈이다.
            값 = f"\t{text[finding.start : finding.end]}" if args.show_values else ""
            print(f"{finding.category}\t{finding.start}-{finding.end}{값}", file=stdout)
        print(f"총 {len(findings)}건", file=stderr)
        return 1 if findings else 0

    if args.command == "mask":
        try:
            text = _read_input(args.source, stdin)
        except OSError as exc:
            print(f"입력을 읽지 못했습니다: {exc}", file=stderr)
            return 3

        session = Session()
        print(mask(text, session), file=stdout)
        print(f"{len(session)}건 가림", file=stderr)
        return 0

    if args.command == "report":
        return _report(args, stdout, stderr)

    if args.command == "proxy":
        import json

        import uvicorn

        from .proxy import create_app

        def report(record: dict) -> None:
            summary = ", ".join(record["categories"]) if record["categories"] else "없음"
            print(f"[수문장] 가린 항목 {record['masked_count']}건: {summary}", file=stderr, flush=True)
            if args.dump:
                with open(args.dump, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record["upstream_body"], ensure_ascii=False) + "\n")

        print(
            f"수문장이 {args.host}:{args.port} 에서 문을 지킵니다.\n"
            f"  export ANTHROPIC_BASE_URL=http://{args.host}:{args.port}",
            file=stderr,
        )
        uvicorn.run(
            create_app(upstream_base_url=args.upstream, on_request=report),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
        return 0

    return 2


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


# restore 는 세션 매핑이 필요해 CLI 단독으로는 의미가 없다.
# 프록시가 세션을 들고 있으므로, 파일 기반 복원은 매핑 저장 기능과 함께 추가한다.
__all__ = ["run", "main", "restore"]


if __name__ == "__main__":
    main()
