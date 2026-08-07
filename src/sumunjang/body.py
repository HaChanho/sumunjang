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

## 예외를 판정하는 법 — 요청자가 쓸 수 있는 신호는 근거가 아니다

예외 판정 근거를 네 번 갈아엎었고, 매번 같은 이유로 뚫렸다.

  - `signature` 라는 **키 이름**이면 어디서든 통과 → metadata.audit.signature 로 유출
  - `type: "base64"` 라고 **적어 두기만** 하면 통과 → 평문 통과
  - `data:` 로 **시작하기만** 하면 통과 → 아무 글에 접두사만 붙이면 영원히 안 가려짐
  - `type: "thinking"` 이라고 **적어 두기만** 하면 통과 → 추론인 척하는 껍데기로 유출

넷의 공통점은 하나다. **판정 근거를 요청자가 본문에 직접 쓸 수 있었다.** 그러면
그것은 예외 조건이 아니라 우회 스위치다.

그래서 근거를 요청자 바깥에서 찾는다.

  **출처** — 우리가 그 값을 내보낸 적이 있는가. 추론 블록은 서명을 대조한다.
            요청자는 우리가 내보낸 적 없는 서명을 만들어낼 수 없다.
  **값**  — 정말 그 모양이면서 글자로 읽을 수 없는 것인가. base64 첨부는 알파벳
            조건에 더해 미디어 타입이 그림·소리·PDF 인 것만 건너뛴다.

둘 중 하나도 아니면 가린다.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from .detect import detect
from .mask import Session, mask

# 글자로 읽을 수 없는 첨부의 미디어 타입. 여기에 해당해야만 알맹이를 건너뛴다.
_BINARY_MEDIA = ("image/", "audio/", "video/", "application/pdf")

# 짧은 값은 우연히 base64 로 읽힐 수 있다. 짧으면 가려도 손해가 없으므로 하한을 둔다.
_MIN_ATTACHMENT_BYTES = 32

# data: URI 중 실제로 base64 로 인코딩된 것. 형태를 통째로 확인한다. 접두사만 보면
# `data:text/plain;base64, 주민번호 …` 가 통과한다.
_BASE64_DATA_URI = re.compile(
    r"data:(?P<media>[\w.+/-]*)(?:;[\w-]+=[\w-]+)*;base64,(?P<payload>[A-Za-z0-9+/=]+)\Z"
)



def _opaque_base64(value: str, media_type: str) -> bool:
    """이 값을 base64 첨부의 알맹이로 보아 건너뛸 것인가.

    **모양을 흉내내는 정규식으로는 판정할 수 없다.** 한때 base64 알파벳을
    정규식으로 확인했는데, URL 안전 알파벳이 하이픈과 밑줄을 포함하는 바람에
    `880312-1068011-880312-1068011-…` 처럼 하이픈으로 끊어 쓴 한국 식별자가
    전부 "base64 모양" 으로 판정됐다. 흉내낼 수 있는 조건은 조건이 아니다.

    그래서 실제로 디코드해 본다. 덤으로 미디어 타입도 본다 — 글이 담긴
    첨부(text/*, application/json …)는 건너뛰지 않고 그대로 검사한다.
    건너뛰는 것은 그림·소리·PDF 처럼 글자로 읽을 수 없는 것뿐이다.
    """
    if not media_type.startswith(_BINARY_MEDIA):
        return False
    try:
        풀린것 = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(풀린것) < _MIN_ATTACHMENT_BYTES:
        return False

    # 마지막이자 가장 중요한 관문 — **이 값에서 개인정보가 보이면 알맹이가 아니다.**
    #
    # 디코드되는지만 물었다가 뚫렸다. base64 알파벳은 숫자와 영문자를 전부
    # 포함하므로, 구분자 없이 적은 한국 식별자와 API 키는 **인코딩 없이도**
    # 조건을 만족한다. `AKIAIOSFODNN7EXAMPLE…` 은 그 자체가 유효한 base64 다.
    # "제대로 인코딩해 그림이라고 주장해야 한다" 던 전제가 성립하지 않았다.
    #
    # 탐지되는지는 요청자가 흉내낼 수 없다. 개인정보처럼 보이면 그 순간 예외가
    # 아니다. 진짜 그림의 base64 가 탐지 규칙에 걸릴 확률은 낮고, 걸린다면
    # 가려서 첨부가 깨지는 쪽이 안전한 실패다.
    return not detect(value)


def _is_opaque(key: str, value: str, parent: dict, session: Session) -> bool:
    """이 값을 가리지 않고 그대로 두어야 하는가.

    **판정 근거는 요청자가 쓸 수 없는 것이어야 한다.** 자리(부모의 type)만 보다
    세 번 뚫렸다. `type: "thinking"` 도 `type: "base64"` 도 요청 본문에 그냥
    적으면 되는 값이라, 그것을 방아쇠로 삼으면 예외가 아니라 우회 스위치가 된다.

    그래서 둘 중 하나여야 한다.

      **출처** — 우리가 그 값을 내보낸 적이 있는가 (추론 서명)
      **값**  — 정말 그 모양이면서, 글자로 읽을 수 없는 것인가 (base64 첨부)
    """
    block_type = parent.get("type")

    # 추론 블록. 서명을 우리가 내보낸 적이 있어야만 예외다. 요청자가 지어낸
    # 서명이면 그 블록은 추론이 아니라 그냥 텍스트이므로 가린다.
    #
    # 진짜 추론 블록을 예외로 두는 이유는 둘이다. 모델이 만든 글이라 사용자
    # 원문이 있을 수 없고(인바운드가 모두 가려진 상태라 모델은 원문을 본 적이
    # 없다), 서명이 본문을 보증하므로 본문만 가리면 서명이 보증하지 못하는
    # 본문이 되어 다음 턴이 거부된다.
    if block_type in ("thinking", "redacted_thinking") and key in (
        "thinking",
        "signature",
        "data",
    ):
        서명 = parent.get("signature") or ""
        본문 = parent.get("thinking") or parent.get("data") or ""
        return (
            isinstance(서명, str)
            and isinstance(본문, str)
            and session.emitted_thinking(서명, 본문)
        )

    if key == "data" and block_type == "base64":
        return _opaque_base64(value, str(parent.get("media_type", "")))

    # data: URI 는 주소가 아니라 알맹이다. 부모의 type 을 보지 않는 이유는 OpenAI
    # 모양(`{"image_url": {"url": "data:…"}}`)에서 url 의 부모에 type 이 없기
    # 때문이다. 대신 값 쪽 조건이 무겁다 — 실제로 디코드돼야 하고 미디어 타입이
    # 글자로 읽을 수 없는 것이어야 한다. 요청자가 이 둘을 모두 만족시키려면
    # 개인정보를 제대로 base64 로 인코딩해 그림이라고 주장해야 하는데, 그것은
    # 이미 선언한 한계(첨부 파일 안은 보지 않는다)와 같은 자리다.
    if key in ("url", "file_data"):
        매치 = _BASE64_DATA_URI.match(value)
        return bool(매치) and _opaque_base64(매치.group("payload"), 매치.group("media"))

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
        if parent is not None and _is_opaque(key, node, parent, session):
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
