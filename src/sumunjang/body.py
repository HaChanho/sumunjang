"""요청 본문 마스킹 — 아는 자리를 가리는 게 아니라, 모르는 자리를 남기지 않는다.

처음에는 프로토콜마다 "여기와 여기를 가린다"는 목록을 두었다. 그것은 화이트리스트처럼
보이지만 실제로는 블랙리스트다. **아는 자리만 가린다는 것은 모르는 자리는 샌다는
뜻이다.** 리뷰에서 그 대가가 드러났다 — Anthropic 의 `document` 블록(사용자가 문서를
첨부하는 정식 경로), `tools[].description`, `metadata.user_id`, OpenAI 의
`prediction.content`, `messages[].name`, 최상위 `user`, `file.file_data` 가 전부
원문 그대로 업스트림에 도달했다. 게이트웨이 경로에는 "모르면 차단"을 적용해 놓고
본문 필드에는 "모르면 통과"를 쓰고 있었다.

그래서 뒤집는다. **본문의 모든 값을 가리고, 가리면 안 되는 자리만 예외로 둔다.**
새 API 필드가 생겨도 자동으로 보호된다.

## 예외를 판정하는 법 — 얕은 신호를 믿지 않는다

예외를 키 이름이나 값 접두사만으로 판정하다 세 번 뚫렸다.

  - `signature` 라는 키면 어디서든 통과했다 → `metadata.audit.signature` 로 유출
  - `type: "base64"` 라고 **적어 두기만** 하면 무엇이든 통과했다 → 평문 통과
  - `data:` 로 시작하고 `;base64,` 를 포함하면 통과했다 → 아무 글 앞에 그 접두사만
    붙이면 영원히 안 가려졌다

셋의 공통점은 **공격자가 그 신호를 직접 쓸 수 있다**는 것이다. 그러면 그것은 예외
조건이 아니라 우회 스위치다.

그래서 예외는 두 가지를 함께 본다.

  **자리** — 부모 블록이 정말 그 블록인가 (`type` 이 `thinking` 인 곳의 `signature`)
  **값**  — 정말 그 모양인가 (base64 알파벳으로만 이루어졌는가)

둘 다 맞아야 건너뛴다. 하나라도 어긋나면 가린다.
"""

from __future__ import annotations

import re
from typing import Any

from .mask import Session, mask

# base64 알파벳(표준·URL 안전)과 패딩만으로 이루어졌는지. `type: base64` 라고 적어
# 두기만 하면 평문이 통과하던 구멍을 막는다. 짧은 값은 우연히 통과할 수 있으므로
# 길이 하한을 둔다 — 짧으면 가려도 손해가 없다.
_BASE64_ONLY = re.compile(r"[A-Za-z0-9+/=_-]{32,}\Z")

# data: URI 중 실제로 base64 로 인코딩된 것. 형태를 통째로 확인한다. 접두사만 보면
# `data:text/plain;base64, 주민번호 …` 가 통과한다.
_BASE64_DATA_URI = re.compile(r"data:[\w.+/-]*(?:;[\w-]+=[\w-]+)*;base64,[A-Za-z0-9+/=]+\Z")


def _is_opaque(key: str, value: str, parent: dict) -> bool:
    """이 값을 가리지 않고 그대로 두어야 하는가.

    자리와 값을 함께 본다. 어느 한쪽만 보면 우회 스위치가 된다.
    """
    block_type = parent.get("type")

    # 추론 블록은 통째로 둔다. 모델이 만든 글이라 사용자 원문이 있을 수 없고
    # (인바운드가 모두 가려진 상태이므로 모델은 원문을 본 적이 없다), 서명이
    # 본문을 보증하므로 한 글자만 바꿔도 다음 턴이 거부된다. 본문을 가리면서
    # 서명만 남기면 서명이 보증하지 못하는 본문이 되어 오히려 요청이 깨진다.
    if block_type in ("thinking", "redacted_thinking") and key in (
        "thinking",
        "signature",
        "data",
    ):
        return True

    # base64 첨부의 알맹이. 형제 필드가 base64 라고 **적혀 있기만** 한 것으로는
    # 부족하고, 값이 실제로 base64 여야 한다.
    if key == "data" and block_type == "base64":
        return bool(_BASE64_ONLY.match(value))

    # data: URI 는 주소가 아니라 알맹이다. 다만 base64 로 인코딩된 것만이다 —
    # `data:image/svg+xml,<svg>…</svg>` 는 평문이고 그 안에 글자가 들어간다.
    # 자리도 함께 본다. 첨부를 가리키는 자리가 아니면 그냥 텍스트다.
    if key in ("url", "file_data") and _BASE64_DATA_URI.match(value):
        return True

    return False


def mask_everything(node: Any, session: Session, parent: dict | None = None, key: str = "") -> Any:
    """본문을 재귀로 훑어 모든 값을 가린다.

    사전은 키까지 바꿔야 하므로 새로 만들고, 나머지는 제자리에서 바꾼다.
    호출자가 미리 깊은 복사를 해 두므로 원본 요청은 그대로 남는다.
    """
    if isinstance(node, dict):
        # 키도 사람이 쓸 수 있다. 도구 입력 스키마의 속성 이름이 그렇다.
        return {
            (
                mask(child_key, session) if isinstance(child_key, str) else child_key
            ): mask_everything(value, session, node, child_key)
            for child_key, value in node.items()
        }

    if isinstance(node, list):
        return [mask_everything(item, session, parent, key) for item in node]

    if isinstance(node, str):
        if parent is not None and _is_opaque(key, node, parent):
            return node
        return mask(node, session)

    # 숫자도 훑는다. CSV·로그를 JSON 으로 옮겨 붙이면 식별자가 숫자형으로 들어온다 —
    # {"rrn": 8803121068011} 이 그대로 나가던 자리다. 가려야 할 것이 있으면 문자열로
    # 바뀌는데, 그래서 업스트림이 거부한다면 그것은 눈에 보이는 실패다.
    # bool 은 int 의 하위형이라 먼저 걸러야 True 가 "True" 로 바뀌지 않는다.
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        가린것 = mask(str(node), session)
        return 가린것 if 가린것 != str(node) else node

    return node
