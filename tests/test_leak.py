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

ROOT = Path(__file__).resolve().parent.parent

# 탐지한다고 주장하는 범위의 골든셋만 넣는다. goldenset-gaps/ 는 못 잡는다고
# 선언한 것들이라 여기 넣으면 이 파일이 영구히 붉은 채로 남는다 — 그쪽은
# report 가 0점으로 공표하는 방식으로 정직함을 지킨다.
GOLDENSETS = (ROOT / "goldenset", ROOT / "goldenset-hard")


def _secrets_of(document: Document) -> list[str]:
    """문서의 정답 스팬이 가리키는 원문 값들."""
    return [document.text[span.start : span.end] for span in document.spans]


def _documents() -> list[Document]:
    documents = [doc for directory in GOLDENSETS for doc in load_directory(directory)]
    assert documents, f"골든셋이 비어 있다: {GOLDENSETS}"
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


def test_OpenAI_본문에도_원문이_남지_않는다():
    """프로토콜이 늘면 검사도 늘어야 한다.

    이 파일이 마스킹 함수가 아니라 **와이어로 나가는 바이트**를 검사 대상으로
    잡아 둔 덕에, 새 프로토콜을 붙일 때 본문 모양만 바꿔 같은 보증을 받는다.
    """
    from sumunjang.openai import mask_request as openai_mask_request

    residue: list[tuple[str, str]] = []

    for document in _documents():
        body = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": document.text},
                {"role": "user", "content": [{"type": "text", "text": document.text}]},
                {"role": "tool", "tool_call_id": "call_1", "content": document.text},
            ],
        }

        wire = json.dumps(openai_mask_request(body, Session()), ensure_ascii=False)
        residue.extend(
            (document.doc_id, secret) for secret in _secrets_of(document) if secret in wire
        )

    assert residue == [], f"OpenAI 본문에 원문이 남았다: {residue}"


def test_말뭉치가_그물_노릇을_할_수_있는_상태다():
    """앞의 두 테스트가 공허하게 통과하지 않는지 확인한다.

    정답 값이 빈 문자열이면 `secret in masked` 는 언제나 참이고, 정답이 하나도
    없으면 언제나 거짓이다. 어느 쪽이든 그물이 뚫린 채로 초록불이 켜진다.

    정답이 0건인 문서는 정상이다 — 오탐 함정 문서가 그렇다. 오히려 그런 문서가
    하나도 없으면 정밀도를 물어볼 기회 자체가 없다는 뜻이라 함께 확인한다.
    """
    documents = _documents()
    with_spans = [d for d in documents if d.spans]
    without_spans = [d for d in documents if not d.spans]

    assert with_spans, "정답이 있는 문서가 하나도 없다"
    assert without_spans, "오탐 함정 문서가 없다 — 정밀도를 물어볼 기회가 없다"
    for document in with_spans:
        assert all(_secrets_of(document)), f"{document.doc_id}: 빈 정답 값이 있다"


def _우회표기(값: str) -> dict[str, str]:
    """같은 값을 눈에는 같아 보이게 다르게 쓴 것들."""
    import re
    import unicodedata

    표기 = {
        "제로폭 삽입": 값[:3] + "\u200b" + 값[3:],
        "보이지 않는 구분자": 값[:2] + "\u2063" + 값[2:],
        "soft hyphen": 값[:4] + "\u00ad" + 값[4:],
        "자모 분해(NFD)": unicodedata.normalize("NFD", 값),
    }

    # 전각은 숫자로 이루어진 식별자에만 의미가 있다. API 키를 전각으로 바꾸면
    # 그것은 더 이상 작동하는 키가 아니므로 가려서 보호할 것이 없다.
    if re.fullmatch(r"[\d+\-. ]+", 값):
        표기["전각"] = 값.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    return 표기


def test_우회_표기로_써도_원문이_남지_않는다():
    """보이지 않는 문자·자모 분해·전각은 전부 같은 값을 다르게 쓴 것이다.

    목록으로 막다 계속 뚫려서 유니코드 범주로 막았다. 그 보증을 골든셋 전체에
    걸어 회귀를 감시한다.
    """
    residue: list[tuple[str, str, str]] = []

    for document in _documents():
        for secret in _secrets_of(document):
            for 방식, 변형 in _우회표기(secret).items():
                masked = mask(f"담당: {변형}", Session())
                # 변형된 표기가 통째로 남았는지 본다.
                if 변형 in masked:
                    residue.append((document.doc_id, 방식, secret))

    assert residue == [], f"우회 표기가 가려지지 않았다: {residue[:5]}"


def test_원문이_부분적으로도_남지_않는다():
    """"절반만 가림" 은 통째 잔존 검사를 그냥 통과한다.

    정규화 지도가 시작만 기록하던 시절 "김수현" 이 "[이름_1]ᅧᆫ" 으로 남았고,
    `secret in masked` 검사는 이것을 초록불로 통과시켰다. 가명 표시를 걷어낸
    나머지에 원문 조각이 있는지를 본다.
    """
    import re

    조각남: list[tuple[str, str]] = []

    for document in _documents():
        masked = mask(document.text, Session())
        # 가명 표시를 지운 자리에 원문 조각이 남아 있으면 안 된다.
        남은것 = re.sub(r"\[[^\]]+_\d+\]", "", masked)
        for secret in _secrets_of(document):
            # 값의 뒤쪽 절반이 남아 있으면 부분 유출이다.
            뒤쪽 = secret[len(secret) // 2 :]
            if len(뒤쪽) >= 4 and 뒤쪽 in 남은것:
                조각남.append((document.doc_id, secret))

    assert 조각남 == [], f"원문 조각이 남았다: {조각남}"


# ── 우회 표기 퍼징 ────────────────────────────────────────────────────────
# 이 세션에서 "값이 반쯤 남는" 결함이 세 번 재발했다 — 겹침 해소, 정규화 지도,
# 결합 기호. 매번 다른 계층이었고 매번 손으로 만든 예시로는 못 찾았다.
# 무작위로 우회 표기를 섞어 넣어 회귀를 감시한다.

_끼울것 = [
    "\u200b", "\u2063", "\u00ad", "\u3164", "\u0301", "\u20e3", "\ufeff",
    "\u00a0", "\u034f", "\u2800", "\u2007", "\u115f", "\u202f", "\u2028",
    "\u200d",
]

_표본 = [
    ("900101-1234568", ""), ("010-3782-4419", ""), ("kim@example.co.kr", ""),
    ("4915-0000-0000-0006", ""), ("8803121000068", ""), ("315-82-00005", ""),
    ("sk-ant-api03-" + "A" * 24, ""), ("M123A4567", "여권번호: "),
    ("110-234-567890", "계좌: "), ("11-23-456789-70", "면허: "), ("최윤서", "성명: "),
]

_꼬리 = ["로그", "\n", "값", "확인", "입니다", " 처리"]


def test_끼워_넣는_모든_문자가_보이지_않는_것으로_판정된다():
    """퍼징이 공허하게 통과하지 않도록 재료부터 검사한다.

    실제로 이 검사가 없을 때 일반 공백이 섞여 들어가, 정상 동작을 결함으로
    세 번 오독했다. 일반 공백은 진짜 다른 글자이므로 탐지하지 않는 것이 맞다.
    """
    from sumunjang.detect import _invisible

    보이는것 = [hex(ord(c)) for c in _끼울것 if not _invisible(c)]
    assert not 보이는것, f"보이는 문자가 섞였다: {보이는것}"


def test_우회_표기를_무작위로_섞어도_원문이_남지_않는다():
    import random
    import re
    import unicodedata

    random.seed(20260807)
    잔존 = []

    for _ in range(3000):
        값, 앵커 = random.choice(_표본)
        자리 = sorted(random.sample(range(1, len(값)), random.randint(0, min(3, len(값) - 1))))
        변형, 이전 = "", 0
        for i in 자리:
            변형 += 값[이전:i] + random.choice(_끼울것)
            이전 = i
        변형 += 값[이전:]
        if random.random() < 0.4:
            변형 = unicodedata.normalize("NFD", 변형)

        본문 = 앵커 + 변형 + random.choice(_꼬리)
        가림 = mask(본문, Session())
        남은것 = re.sub(r"\[[^\]]+_\d+\]", "", 가림)
        뒤절반 = 값[len(값) // 2 :]
        if len(뒤절반) >= 3 and 뒤절반 in 남은것:
            잔존.append((본문, 가림))

    assert 잔존 == [], f"우회 표기로 원문이 남았다: {잔존[:3]}"
