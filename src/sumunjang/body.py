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

# 마스킹에서 제외하는 자리. 기준은 하나다 — **사람이 쓸 수 없는 자리이면서,
# 훼손되면 요청 자체가 깨지는 값.** 그 밖의 모든 문자열은 가린다.
#
#   signature      추론 블록의 서명. 한 글자만 바뀌어도 다음 턴이 400 이 된다.
#   tool_use_id    도구 호출과 결과를 잇는 토큰. 프로토콜이 만들며 사람이 쓰지 않는다.
#   tool_call_id   같음(OpenAI 쪽 이름).
#   data           base64 첨부의 알맹이. 형제 필드 type 이 base64 일 때만 건너뛴다 —
#                  type 이 text 인 document 블록의 data 는 사람이 쓴 글이므로 가린다.
#
# 한때 url 과 id 도 여기 있었다. "가리면 첨부가 깨진다" 는 이유였는데 그것이 틀렸다.
# **깨지는 것은 눈에 보이고 유출은 보이지 않는다.** 주소 안의 개인정보는 업스트림이
# 그 주소를 가져가는 순간 그대로 넘어가므로, 가려서 요청이 실패하는 편이 안전한
# 실패다. id 는 metadata.id 처럼 사람이 채우는 자리에도 쓰이는 흔한 이름이라
# 통째로 빼면 구멍이 된다. 같은 값은 같은 가명을 받으므로 짝은 어긋나지 않는다.
_OPAQUE_KEYS = frozenset({"signature", "tool_use_id", "tool_call_id"})

# data: URI 는 주소가 아니라 알맹이를 담은 덩어리다. 가리면 첨부가 훼손된다.
_DATA_URI = "data:"


def _is_opaque(key: str, value: str, parent: dict) -> bool:
    if key in _OPAQUE_KEYS:
        return True
    if key == "data" and parent.get("type") == "base64":
        return True
    # https:// 주소는 가린다. data: URI 만 예외다.
    return key == "url" and value.startswith(_DATA_URI)


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
