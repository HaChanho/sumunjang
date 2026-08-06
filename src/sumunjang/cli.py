"""명령줄 인터페이스.

표준 라이브러리 argparse만 쓴다. 종료 코드는 CI 게이트로 쓸 수 있게 정한다.
  0 통과 / 1 개인정보 검출 / 2 잘못된 사용 / 3 리소스 오류
"""

from __future__ import annotations

import argparse
import sys
from typing import IO

from .detect import detect
from .mask import Session, mask, restore


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

    mask_cmd = sub.add_parser("mask", help="개인정보를 가명값으로 바꾼다")
    mask_cmd.add_argument("source", help="파일 경로 또는 - (표준 입력)")

    report = sub.add_parser("report", help="골든셋으로 탐지 성능을 채점한다")
    report.add_argument("directory", nargs="?", default="goldenset", help="골든셋 디렉토리")
    report.add_argument("--json", action="store_true", help="JSON으로 출력")

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
            print(
                f"{finding.category}\t{finding.start}-{finding.end}\t"
                f"{text[finding.start : finding.end]}",
                file=stdout,
            )
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
        import json as json_module

        from .goldenset import Span, load_directory, score

        try:
            documents = load_directory(args.directory)
        except OSError as exc:
            print(f"골든셋을 읽지 못했습니다: {exc}", file=stderr)
            return 3

        if not documents:
            print(f"골든셋 문서가 없습니다: {args.directory}", file=stderr)
            return 3

        truth: list[Span] = []
        found: list[Span] = []
        for document in documents:
            truth.extend(document.spans)
            found.extend(
                Span(f.category, f.start, f.end) for f in detect(document.text)
            )

        result = score(truth, found)
        expected = sum(m["expected"] for m in result.values())
        detected = sum(m["detected"] for m in result.values())
        hit = sum(m["hit"] for m in result.values())

        if args.json:
            print(
                json_module.dumps(
                    {
                        "documents": len(documents),
                        "categories": result,
                        "total": {"expected": expected, "detected": detected, "hit": hit},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=stdout,
            )
            return 0

        print(f"# 수문장 탐지 성능 자체 평가\n", file=stdout)
        print(f"- 문서 {len(documents)}건, 정답 {expected}건", file=stdout)
        print(f"- 재현 방법: `sumunjang report {args.directory}`\n", file=stdout)
        print("| 카테고리 | 정답 | 탐지 | 적중 | 재현율 | 정밀도 |", file=stdout)
        print("|---|---:|---:|---:|---:|---:|", file=stdout)
        for category, metric in result.items():
            print(
                f"| {category} | {metric['expected']} | {metric['detected']} | "
                f"{metric['hit']} | {metric['recall']:.3f} | {metric['precision']:.3f} |",
                file=stdout,
            )
        if expected and detected:
            print(
                f"| **전체** | {expected} | {detected} | {hit} | "
                f"{hit / expected:.3f} | {hit / detected:.3f} |",
                file=stdout,
            )

        print(
            "\n## 이 수치의 한계\n"
            "- 골든셋은 개발자가 자체 제작한 합성 문서다. 도구를 만든 사람이 정답도 만들었으므로\n"
            "  이 점수는 독립적인 성능 증명이 아니라 회귀를 감시하는 기준선이다.\n"
            "- 규칙에 없는 표기·문맥 의존 개인정보(이름, 자유서술 주소)는 정답에 포함되지 않았다.\n"
            "  현재 탐지 범위 자체의 한계이며 점수에 반영되지 않는다.",
            file=stdout,
        )
        return 0

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
