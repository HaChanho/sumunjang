"""Anthropic Messages API 본문 변환.

나가는 요청에서 텍스트를 찾아 마스킹하고, 돌아온 응답에서 원문을 복구한다.
마스킹 범위를 명시적으로 정해 두고, 범위 밖은 손대지 않는다.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .body import mask_everything
from .mask import Session, restore


def count_masked(body: dict) -> list[str]:
    """이 본문에서 가려진 자리의 카테고리 목록.

    세션에 이미 등록된 값인지와 무관하게, 이번 요청에서 실제로 가려진 것을 센다.
    """
    from .mask import placeholders_in

    return placeholders_in(json.dumps(body, ensure_ascii=False))


def mask_request(body: dict, session: Session) -> dict:
    """요청 본문의 마스킹 사본을 돌려준다. 원본은 그대로 둔다.

    본문의 모든 문자열을 훑는다. 자리 목록을 두지 않는 이유는 body.py 에 적었다 —
    아는 자리만 가린다는 것은 모르는 자리는 샌다는 뜻이기 때문이다.
    """
    return mask_everything(copy.deepcopy(body), session)


def restore_response(body: dict, session: Session) -> dict:
    """응답 본문의 placeholder를 원문으로 되돌린 사본을 돌려준다."""
    restored = copy.deepcopy(body)

    for block in restored.get("content", []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            block["text"] = restore(block["text"], session)

    return restored
