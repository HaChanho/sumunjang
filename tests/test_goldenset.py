"""골든셋 파서·채점 테스트.

정답 좌표를 손으로 세면 한 글자만 밀려도 채점 전체가 틀어진다.
사람은 마커로 "여기가 개인정보다"만 표시하고, 좌표는 파서가 만든다.
"""

from sumunjang.goldenset import Span, load_directory, parse_annotated, score


def test_마커_표기를_텍스트와_정답_스팬으로_푼다():
    document = parse_annotated("고객 {{RRN:900101-1234568}} 확인 바랍니다")

    assert document.text == "고객 900101-1234568 확인 바랍니다"
    assert document.spans == [Span("RRN", 3, 17)]
    assert document.text[3:17] == "900101-1234568"


def test_마커가_여러_개여도_좌표가_어긋나지_않는다():
    document = parse_annotated("{{PHONE:010-1234-5678}} 및 {{EMAIL:a@b.com}} 참고")

    assert document.text == "010-1234-5678 및 a@b.com 참고"
    assert [s.category for s in document.spans] == ["PHONE", "EMAIL"]
    for span in document.spans:
        assert document.text[span.start : span.end]


def test_전부_맞추면_재현율과_정밀도가_1이_된다():
    truth = [Span("RRN", 0, 14)]
    found = [Span("RRN", 0, 14)]

    result = score(truth, found)

    assert result["RRN"]["recall"] == 1.0
    assert result["RRN"]["precision"] == 1.0


def test_놓치면_재현율이_떨어진다():
    truth = [Span("RRN", 0, 14), Span("PHONE", 20, 33)]
    found = [Span("RRN", 0, 14)]

    result = score(truth, found)

    assert result["PHONE"]["recall"] == 0.0
    assert result["PHONE"]["missed"] == 1


def test_잘못_잡으면_정밀도가_떨어진다():
    truth = [Span("RRN", 0, 14)]
    found = [Span("RRN", 0, 14), Span("CARD", 20, 39)]

    result = score(truth, found)

    assert result["CARD"]["precision"] == 0.0
    assert result["CARD"]["false_positive"] == 1


def test_골든셋_디렉토리에서_문서를_읽는다(tmp_path):
    (tmp_path / "sample.txt").write_text(
        "domain: 테스트\n---\n고객 {{RRN:900101-1234568}} 확인",
        encoding="utf-8",
    )

    documents = load_directory(tmp_path)

    assert len(documents) == 1
    assert documents[0].domain == "테스트"
    assert documents[0].doc_id == "sample"
    assert documents[0].text == "고객 900101-1234568 확인"
    assert documents[0].spans == [Span("RRN", 3, 17)]
