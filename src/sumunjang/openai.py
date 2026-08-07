"""OpenAI Chat Completions API 본문 변환.

마스킹·복원 계층은 프로토콜과 무관하다. 이 모듈이 하는 일은 하나뿐이다 —
**이 본문의 어느 자리에 사람이 쓴 텍스트가 들어 있는가.**

Anthropic 쪽(`anthropic.py`)과 나란히 읽으면 차이가 그대로 보인다.

  system 프롬프트   Anthropic 은 별도 필드, OpenAI 는 messages 의 한 항목
  도구 실행 결과    Anthropic 은 tool_result 블록, OpenAI 는 role="tool" 메시지
  도구 호출        Anthropic 은 tool_use.input, OpenAI 는 tool_calls[].function.arguments
  응답 본문        Anthropic 은 content[], OpenAI 는 choices[].message

가리는 자리와 복원하는 자리에 대한 판단은 두 프로토콜에서 같다.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .body import mask_everything
from .mask import Session, restore


def count_masked(body: dict) -> list[str]:
    """이 본문에서 가려진 자리의 카테고리 목록."""
    from .mask import placeholders_in

    return placeholders_in(json.dumps(body, ensure_ascii=False))


def mask_request(body: dict, session: Session) -> dict:
    """요청 본문의 마스킹 사본을 돌려준다. 원본은 그대로 둔다.

    Anthropic 쪽과 같은 함수를 쓴다. 마스킹 범위를 자리 목록이 아니라 "모든
    문자열" 로 잡으면 프로토콜별로 다를 것이 없어진다 — 자리 목록을 두었을 때
    OpenAI 쪽에서만 prediction.content·messages[].name·user·file_data 가 샜던
    것이 바로 목록을 두 벌 관리한 대가였다.
    """
    return mask_everything(copy.deepcopy(body), session)


def restore_response(body: dict, session: Session) -> dict:
    """응답 본문의 가명 표시를 원문으로 되돌린 사본을 돌려준다.

    사람이 읽는 자리(message.content)만 되돌린다. 도구에게 건네는 인자
    (tool_calls[].function.arguments)는 건드리지 않는다 — 복원하면 모델이 부르는
    모든 도구가 원문을 받는데, 프록시는 그 도구가 로컬인지 원격인지 알 방법이
    없다. 모르면 복원하지 않는다.
    """
    restored = copy.deepcopy(body)

    for choice in restored.get("choices", []):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        # content 와 refusal 둘 다 사람이 읽는 자리다. 거절 메시지에만 가명
        # 표시가 남으면 사용자가 무슨 말인지 알 수 없다.
        for field in ("content", "refusal"):
            if isinstance(message.get(field), str):
                message[field] = restore(message[field], session)

    return restored
