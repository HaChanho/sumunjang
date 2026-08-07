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


def test_report는_채점표와_한계를_함께_낸다(tmp_path):
    """수치만 내놓으면 자체 채점을 객관 지표처럼 읽게 된다. 한계를 같이 적는다."""
    (tmp_path / "doc.txt").write_text(
        "domain: 테스트\n---\n연락처 {{PHONE:010-1234-5678}}",
        encoding="utf-8",
    )

    code, out, _ = _run(["report", str(tmp_path)])

    assert code == 0
    assert "PHONE" in out
    assert "재현율" in out
    assert "자체 제작" in out


def test_report는_여러_골든셋을_구분해_채점한다(tmp_path):
    """쉬운 셋과 어려운 셋을 한 표에 합치면 점수의 의미를 설명할 수 없다."""
    easy, hard = tmp_path / "easy", tmp_path / "hard"
    for directory, body in ((easy, "연락처 {{PHONE:010-1234-5678}}"), (hard, "직통 {{PHONE:+82-10-2255-8830}}")):
        directory.mkdir()
        (directory / "doc.txt").write_text(f"domain: 테스트\n---\n{body}", encoding="utf-8")

    code, out, _ = _run(["report", str(easy), str(hard)])

    assert code == 0
    assert "easy" in out and "hard" in out
    assert out.count("| **전체** |") == 2


def test_report는_선언된_한계를_0점으로_공표한다(tmp_path):
    """못 잡는다고 선언한 것을 숨기지 않고 점수로 낸다.

    만점 셋만 내놓으면 "쉬운 것만 골랐다" 는 반문에 답할 수 없다. 0점 셋을
    나란히 공개하는 것이 답이다.
    """
    gaps = tmp_path / "gaps"
    gaps.mkdir()
    (gaps / "doc.txt").write_text(
        "domain: 한계\n---\n어제 {{NAME:김수현}} 책임이랑 통화했습니다",
        encoding="utf-8",
    )

    code, out, _ = _run(["report", str(gaps)])

    assert code == 0
    assert "NAME" in out
    assert "0.000" in out
