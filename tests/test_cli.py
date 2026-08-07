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


def test_scan은_기본적으로_찾은_값을_출력하지_않는다():
    """scan 은 CI 게이트로 쓰인다. 찾은 값을 찍으면 개인정보가 빌드 로그에
    영구히 남는다 — 개인정보를 막겠다는 도구가 새 유출 경로를 만드는 셈이다."""
    code, out, _ = _run(["scan", "-"], "연락처 010-1234-5678")

    assert code == 1
    assert "PHONE" in out
    assert "010-1234-5678" not in out

    _, 값보임, _ = _run(["scan", "-", "--show-values"], "연락처 010-1234-5678")
    assert "010-1234-5678" in 값보임


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


def test_설치본에서도_기본_골든셋을_찾는다(tmp_path, monkeypatch):
    """`pip install sumunjang` 한 사람이 report 를 칠 수 있어야 한다.

    저장소를 클론하지 않고도 README 의 수치를 재현할 수 있어야 "재현 가능한
    자체 평가" 라는 말이 성립한다. 작업 디렉토리에 골든셋이 없으면 패키지에
    함께 실린 사본을 쓴다.
    """
    from sumunjang.cli import default_goldensets

    monkeypatch.chdir(tmp_path)

    for path in default_goldensets():
        assert path.is_dir(), f"패키지에 골든셋이 실려 있지 않다: {path}"


def test_작업_디렉토리의_골든셋이_패키지_사본보다_앞선다(tmp_path, monkeypatch):
    """저장소 안에서는 저장소의 셋을 채점해야 한다. 고친 것이 바로 보여야 하므로."""
    from sumunjang.cli import default_goldensets

    (tmp_path / "goldenset").mkdir()
    monkeypatch.chdir(tmp_path)

    assert default_goldensets()[0] == tmp_path / "goldenset"
