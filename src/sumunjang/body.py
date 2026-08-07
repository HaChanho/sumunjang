"""요청 본문 마스킹 — 아는 자리를 가리는 게 아니라, 모르는 자리를 남기지 않는다.

처음에는 프로토콜마다 "여기와 여기를 가린다"는 목록을 두었다. 그것은 화이트리스트처럼
보이지만 실제로는 블랙리스트다. **아는 자리만 가린다는 것은 모르는 자리는 샌다는
뜻이다.** 리뷰에서 그 대가가 드러났다 — Anthropic 의 `document` 블록(사용자가 문서를
첨부하는 정식 경로), `tools[].description`, `metadata.user_id`, OpenAI 의
`prediction.content`, `messages[].name`, 최상위 `user`, `file.file_data` 가 전부
원문 그대로 업스트림에 도달했다. 게이트웨이 경로에는 "모르면 차단"을 적용해 놓고
본문 필드에는 "모르면 통과"를 쓰고 있었다.

그래서 뒤집는다. **본문의 모든 문자열을 가리고, 가리면 안 되는 자리만 예외로 둔다.**
새 API 필드가 생겨도 자동으로 보호된다.

구조를 가리키는 값(모델명·역할·식별자)까지 훑지만 손상되지 않는다. 탐지기는 한국
개인정보처럼 생긴 것에만 반응하므로 `claude-opus-4` 나 `toolu_01ABC` 에는 아무 일도
일어나지 않는다. 즉 이 방식의 비용은 훑는 시간뿐이다.
"""

from __future__ import annotations

from typing import Any

from .mask import Session, mask

# 마스킹에서 제외하는 자리. 딱 둘이다.
#
#   signature   추론 블록의 서명. 사람이 쓸 수 없고, 한 글자만 바뀌어도 다음 턴이 400 이 된다.
#   data        base64 로 실린 첨부의 알맹이. 형제 필드 type 이 base64 이거나 data: URI 가
#               `;base64,` 를 달고 있을 때만이다. data: 가 언제나 base64 인 것은 아니다 —
#               `data:image/svg+xml,<svg>...</svg>` 는 평문이고, 그 안에 개인정보가 들어간다.
#
# 한때 url·id·tool_use_id·tool_call_id 도 여기 있었다. 이유는 "가리면 짝이 어긋나거나
# 첨부가 깨진다" 였는데, 예외 칸 자체가 두 가지를 만들었다. 우회 통로가 되고(식별자에
# 개인정보를 넣으면 그대로 나간다), id 는 가리고 tool_use_id 는 안 가려 오히려 짝이
# 어긋났다. 전부 가리면 같은 값은 같은 가명을 받으므로 짝이 맞고 통로도 사라진다.
#
# 식별자에 개인정보가 들어 있어 업스트림이 거부한다면 그것은 **눈에 보이는 실패**다.
# 유출은 보이지 않는다.
_OPAQUE_KEYS = frozenset({"signature"})


def _is_opaque(key: str, value: str, parent: dict) -> bool:
    if key in _OPAQUE_KEYS:
        return True
    if key == "data" and parent.get("type") == "base64":
        return True
    return value.startswith("data:") and ";base64," in value[:64]


def mask_everything(node: Any, session: Session, parent: dict | None = None, key: str = "") -> Any:
    """본문을 재귀로 훑어 모든 문자열을 가린다.

    사전은 키까지 바꿔야 하므로 새로 만들고, 나머지는 제자리에서 바꾼다.
    호출자가 미리 깊은 복사를 해 두므로 원본 요청은 그대로 남는다.
    """
    if isinstance(node, dict):
        # 키도 사람이 쓸 수 있다. 도구 입력 스키마의 속성 이름이 그렇다.
        return {
            mask(child_key, session) if isinstance(child_key, str) else child_key:
                mask_everything(value, session, node, child_key)
            for child_key, value in node.items()
        }

    if isinstance(node, list):
        return [mask_everything(item, session, parent, key) for item in node]

    if isinstance(node, str):
        if parent is not None and _is_opaque(key, node, parent):
            return node
        return mask(node, session)

    return node
