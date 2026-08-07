"""유출 속성 테스트 — 이 파일 하나가 제품의 유일한 약속을 검사한다.

`report` 의 재현율·정밀도는 **스팬이 정확히 일치했는지**를 센다. 그래서
"절반만 가렸다"는 재현율을 떨어뜨리긴 해도, *남은 절반이 평문으로 새어나갔다*는
사실 자체는 어느 지표에도 나타나지 않는다. 실제로 겹치는 탐지에서 카드번호
뒷 8자리가 그대로 남는 결함이 있었고, 골든셋 채점은 그것을 잡지 못했다.

여기서 검사하는 명제는 하나다.

    마스킹을 거친 결과에는 정답 값이 **부분 문자열로도** 남아 있지 않다.

카테고리를 맞췄는지, 좌표가 정확한지는 묻지 않는다. 원문이 나갔는지만 묻는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from sumunjang.anthropic import mask_request
from sumunjang.goldenset import Document, load_directory
from sumunjang.mask import Session, mask

GOLDENSET = Path(__file__).resolve().parent.parent / "goldenset"


def _secrets_of(document: Document) -> list[str]:
    """문서의 정답 스팬이 가리키는 원문 값들."""
    return [document.text[span.start : span.end] for span in document.spans]


def _documents() -> list[Document]:
    documents = load_directory(GOLDENSET)
    assert documents, f"골든셋이 비어 있다: {GOLDENSET}"
    return documents


def test_골든셋_전_문서에서_마스킹_결과에_원문이_남지_않는다():
    residue: list[tuple[str, str]] = []

    for document in _documents():
        masked = mask(document.text, Session())
        residue.extend(
            (document.doc_id, secret) for secret in _secrets_of(document) if secret in masked
        )

    assert residue == [], f"마스킹 결과에 원문이 남았다: {residue}"


def test_업스트림으로_나가는_요청_본문에_원문이_남지_않는다():
    """마스킹 계층이 아니라 **실제로 전송되는 JSON**을 검사한다.

    본문 어딘가에 우리가 손대지 않는 필드가 생기면 mask() 는 멀쩡한데 요청은
    새어나간다. 그래서 문자열이 아니라 직렬화된 본문 전체를 본다.
    """
    residue: list[tuple[str, str]] = []

    for document in _documents():
        body = {
            "model": "claude-opus-4",
            "max_tokens": 1024,
            "system": document.text,
            "messages": [
                {"role": "user", "content": document.text},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": document.text},
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [{"type": "text", "text": document.text}],
                        },
                    ],
                },
            ],
        }

        wire = json.dumps(mask_request(body, Session()), ensure_ascii=False)
        residue.extend(
            (document.doc_id, secret) for secret in _secrets_of(document) if secret in wire
        )

    assert residue == [], f"업스트림 본문에 원문이 남았다: {residue}"


def test_어느_정답값도_빈_문자열이_아니다():
    """앞의 두 테스트가 공허하게 통과하지 않는지 확인한다.

    정답 값이 빈 문자열이면 `secret in masked` 는 언제나 참이 되고, 스팬이
    비어 있으면 언제나 거짓이 된다. 어느 쪽이든 그물이 뚫린 채로 초록불이 켜진다.
    """
    for document in _documents():
        secrets = _secrets_of(document)
        assert secrets, f"{document.doc_id}: 정답 스팬이 없다"
        assert all(secrets), f"{document.doc_id}: 빈 정답 값이 있다"
