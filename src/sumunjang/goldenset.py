"""골든셋 파서와 채점기.

정답 문서는 원문에 마커를 넣어 작성한다.

    고객 {{RRN:900101-1234568}} 확인

파서가 마커를 풀어 텍스트와 좌표를 함께 만들기 때문에, 사람이 좌표를 셀 일이 없다.
정답 데이터의 신뢰도가 곧 리포트의 신뢰도이므로 이 과정을 손에 맡기지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MARKER = re.compile(r"\{\{([A-Z_]+):(.*?)\}\}", re.S)


@dataclass(frozen=True)
class Span:
    """정답·탐지 한 건.

    doc_id 가 키의 일부다. 이것이 없으면 서로 다른 문서의 같은 좌표가 한 건으로
    합쳐진다. 실제로 goldenset-gaps 의 두 문서가 같은 좌표를 가져 정답 18건이
    17건으로 줄고 공표한 재현율이 틀렸다. 더 나쁜 것은 가짜 적중이다 — 문서 B 의
    순수 오탐이 문서 A 의 정답과 좌표가 같으면 맞힌 것으로 계산된다.
    """

    category: str
    start: int
    end: int
    doc_id: str = ""


@dataclass
class Document:
    text: str
    spans: list[Span] = field(default_factory=list)
    doc_id: str = ""
    domain: str = ""


def parse_annotated(annotated: str, doc_id: str = "", domain: str = "") -> Document:
    """마커가 들어간 원문을 평문 텍스트와 정답 스팬으로 푼다."""
    pieces: list[str] = []
    spans: list[Span] = []
    cursor = 0
    length = 0

    for match in _MARKER.finditer(annotated):
        plain = annotated[cursor : match.start()]
        pieces.append(plain)
        length += len(plain)

        value = match.group(2)
        spans.append(
            Span(category=match.group(1), start=length, end=length + len(value), doc_id=doc_id)
        )
        pieces.append(value)
        length += len(value)
        cursor = match.end()

    pieces.append(annotated[cursor:])
    return Document(text="".join(pieces), spans=spans, doc_id=doc_id, domain=domain)


def load_directory(directory) -> list[Document]:
    """골든셋 디렉토리의 문서를 파일명 순으로 읽는다.

    파일 형식:
        domain: 업무메일
        ---
        본문 (마커 포함)
    """
    from pathlib import Path

    documents = []
    for path in sorted(Path(directory).glob("*.txt")):
        raw = path.read_text(encoding="utf-8")
        domain = ""
        body = raw
        if "\n---\n" in raw:
            header, body = raw.split("\n---\n", 1)
            for line in header.splitlines():
                if line.startswith("domain:"):
                    domain = line.split(":", 1)[1].strip()
        documents.append(parse_annotated(body.strip(), doc_id=path.stem, domain=domain))
    return documents


def score(truth: list[Span], found: list[Span]) -> dict[str, dict]:
    """카테고리별 재현율·정밀도.

    스팬이 정확히 일치할 때만 맞은 것으로 센다. 부분 일치를 인정하면
    "절반만 가린" 결과가 성공으로 계산되어 지표가 실제보다 좋아 보인다.
    """
    truth_set = {(s.doc_id, s.category, s.start, s.end) for s in truth}
    found_set = {(s.doc_id, s.category, s.start, s.end) for s in found}

    categories = {s.category for s in truth} | {s.category for s in found}
    report: dict[str, dict] = {}

    for category in sorted(categories):
        expected = {s for s in truth_set if s[1] == category}
        actual = {s for s in found_set if s[1] == category}
        hit = expected & actual
        missed = expected - actual
        false_positive = actual - expected

        report[category] = {
            "expected": len(expected),
            "detected": len(actual),
            "hit": len(hit),
            "missed": len(missed),
            "false_positive": len(false_positive),
            "recall": len(hit) / len(expected) if expected else 0.0,
            "precision": len(hit) / len(actual) if actual else 0.0,
        }

    return report
