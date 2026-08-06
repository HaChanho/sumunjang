"""CLI 테스트. 스트림을 주입해 실제 입출력 경로를 검증한다."""

import io

from sumunjang.cli import run


def _run(args, stdin_text=""):
    out, err = io.StringIO(), io.StringIO()
    code = run(args, stdin=io.StringIO(stdin_text), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_scan은_개인정보를_찾으면_1로_끝난다():
    """pre-commit 훅이나 CI에서 게이트로 쓸 수 있어야 한다."""
    code, out, _ = _run(["scan", "-"], "연락처 010-1234-5678")

    assert code == 1
    assert "PHONE" in out


def test_scan은_개인정보가_없으면_0으로_끝난다():
    code, _, _ = _run(["scan", "-"], "특이사항 없음")

    assert code == 0


def test_mask는_마스킹된_텍스트를_출력한다():
    code, out, _ = _run(["mask", "-"], "결제자 900101-1234568")

    assert code == 0
    assert out.strip() == "결제자 [주민등록번호_1]"
    assert "900101" not in out
