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

from .mask import Session, mask, restore


def _mask_content(content: Any, session: Session) -> Any:
    """content 는 문자열이거나 블록 배열이다. 멀티모달 요청에서 배열이 된다."""
    if isinstance(content, str):
        return mask(content, session)

    if isinstance(content, list):
        return [_mask_block(block, session) for block in content]

    return content


def _mask_block(block: Any, session: Session) -> Any:
    if isinstance(block, dict) and isinstance(block.get("text"), str):
        # 텍스트 블록만 손댄다. image_url 같은 다른 블록은 그대로 둔다.
        block["text"] = mask(block["text"], session)
    return block


def count_masked(body: dict) -> list[str]:
    """이 본문에서 가려진 자리의 카테고리 목록."""
    from .mask import placeholders_in

    return placeholders_in(json.dumps(body, ensure_ascii=False))


def mask_request(body: dict, session: Session) -> dict:
    """요청 본문의 마스킹 사본을 돌려준다. 원본은 그대로 둔다.

    role 을 가리지 않고 모든 메시지를 훑는다. system·user·assistant·tool 어디에나
    원문이 실릴 수 있고, 특히 role="tool" 은 파일·명령 실행 결과가 담기는 자리라
    사용자가 붙여넣지 않아도 원문이 흘러든다.
    """
    masked = copy.deepcopy(body)

    for message in masked.get("messages", []):
        if isinstance(message, dict):
            message["content"] = _mask_content(message.get("content"), session)

    # tool_calls[].function.arguments 는 모델이 만든 값이다. 인바운드 텍스트가
    # 모두 마스킹된 상태라면 모델은 원문을 본 적이 없으므로 원문이 있을 수 없다.

    return masked


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
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            message["content"] = restore(message["content"], session)

    return restored
