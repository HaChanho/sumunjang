"""Anthropic Messages API 본문 변환.

나가는 요청에서 텍스트를 찾아 마스킹하고, 돌아온 응답에서 원문을 복구한다.
마스킹 범위를 명시적으로 정해 두고, 범위 밖은 손대지 않는다.
"""

from __future__ import annotations

import copy
from typing import Any

from .mask import Session, mask, restore


def _mask_content(content: Any, session: Session) -> Any:
    """content 필드는 문자열이거나 블록 배열이다. 둘 다 처리한다."""
    if isinstance(content, str):
        return mask(content, session)

    if isinstance(content, list):
        return [_mask_block(block, session) for block in content]

    return content


def _mask_block(block: Any, session: Session) -> Any:
    if not isinstance(block, dict):
        return block

    kind = block.get("type")

    if kind == "text" and isinstance(block.get("text"), str):
        block["text"] = mask(block["text"], session)

    elif kind == "tool_result":
        # 파일·명령 실행 결과가 담기는 자리. 사용자가 붙여넣지 않아도 원문이 흐른다.
        block["content"] = _mask_content(block.get("content"), session)

    # tool_use.input 은 모델이 만든 값이다. 인바운드 텍스트가 모두 마스킹된 상태라면
    # 모델은 원문을 본 적이 없으므로 여기에 원문 개인정보가 있을 수 없다.

    return block


def mask_request(body: dict, session: Session) -> dict:
    """요청 본문의 마스킹 사본을 돌려준다. 원본은 그대로 둔다."""
    masked = copy.deepcopy(body)

    if isinstance(masked.get("system"), str):
        masked["system"] = mask(masked["system"], session)
    elif isinstance(masked.get("system"), list):
        masked["system"] = [_mask_block(block, session) for block in masked["system"]]

    for message in masked.get("messages", []):
        if isinstance(message, dict):
            message["content"] = _mask_content(message.get("content"), session)

    return masked


def restore_response(body: dict, session: Session) -> dict:
    """응답 본문의 placeholder를 원문으로 되돌린 사본을 돌려준다."""
    restored = copy.deepcopy(body)

    for block in restored.get("content", []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            block["text"] = restore(block["text"], session)

    return restored
