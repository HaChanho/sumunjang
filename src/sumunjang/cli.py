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

    proxy = sub.add_parser("proxy", help="AI API 경계 게이트웨이를 띄운다")
    proxy.add_argument("--port", type=int, default=4000)
    proxy.add_argument("--host", default="127.0.0.1")
    proxy.add_argument(
        "--upstream",
        default="https://api.anthropic.com",
        help="업스트림 API 주소",
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

    if args.command == "proxy":
        import uvicorn

        from .proxy import create_app

        print(
            f"수문장이 {args.host}:{args.port} 에서 문을 지킵니다.\n"
            f"  export ANTHROPIC_BASE_URL=http://{args.host}:{args.port}",
            file=stderr,
        )
        uvicorn.run(create_app(upstream_base_url=args.upstream), host=args.host, port=args.port)
        return 0

    return 2


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


# restore 는 세션 매핑이 필요해 CLI 단독으로는 의미가 없다.
# 프록시가 세션을 들고 있으므로, 파일 기반 복원은 매핑 저장 기능과 함께 추가한다.
__all__ = ["run", "main", "restore"]


if __name__ == "__main__":
    main()
