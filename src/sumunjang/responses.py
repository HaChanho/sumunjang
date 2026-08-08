"""OpenAI Responses API 본문 변환.

세 번째 프로토콜이다. 앞의 둘을 붙일 때 "새 프로토콜을 붙이는 일이 표에 줄 하나를
더하는 일이 되도록 둔다"고 적었는데, 이 파일이 그 말이 사실인지 확인하는 자리다.
마스킹은 한 줄도 새로 쓰지 않았다 — `mask_everything` 은 자리 목록이 아니라 본문
전체를 훑으므로 프로토콜을 모른다. 새로 정해야 하는 것은 **복원할 자리** 하나뿐이다.

## 왜 필요한가

Codex CLI 는 2026년 2월 `wire_api = "chat"` 지원을 완전히 제거했다. 지금은
`/v1/responses` 만 두드린다. 실제로 물려 보니 404 가 났다 — "모르면 차단" 이
설계대로 동작한 것이지만, 그 결과 수문장은 "OpenAI 호환" 을 표방하면서
OpenAI 의 대표 코딩 에이전트를 통째로 못 받는 상태였다. OpenAI 자신의 마이그레이션
안내가 이렇게 적고 있다 — "조직이 LLM 프록시나 게이트웨이를 쓴다면 그것이
Responses API 를 지원하는지 확인하라."

## Chat Completions 와 무엇이 다른가

  system 프롬프트   chat 은 messages 의 한 항목, 여기서는 `instructions` 필드
  대화 기록        `messages[]` 가 아니라 `input[]` 항목 배열
  텍스트 블록      입력은 `input_text`, 출력은 `output_text`
  도구 결과        `role: "tool"` 메시지가 아니라 `function_call_output` 항목
  응답 본문        `choices[].message` 가 아니라 `output[]` 항목 배열

가리는 자리와 복원하는 자리에 대한 **판단**은 세 프로토콜에서 같다. 다른 것은
그 자리가 어디냐뿐이다.
"""

from __future__ import annotations

import copy
import json

from .body import mask_everything
from .mask import Session, restore


def count_masked(body: dict) -> list[str]:
    """이 본문에서 가려진 자리의 카테고리 목록."""
    from .mask import placeholders_in

    return placeholders_in(json.dumps(body, ensure_ascii=False))


def mask_request(body: dict, session: Session) -> dict:
    """요청 본문의 마스킹 사본을 돌려준다. 원본은 그대로 둔다.

    앞의 두 프로토콜과 같은 함수를 쓴다. 자리 목록을 두었다면 여기서 `input[]`,
    `instructions`, `function_call_output.output` 을 하나씩 적어야 했고, 빠뜨린
    자리가 그대로 유출이 됐을 것이다. OpenAI 쪽에서만 `prediction.content` ·
    `messages[].name` · `user` · `file_data` 가 샜던 것이 목록을 두 벌 관리한
    대가였다. 세 벌이 되면 세 배가 된다.
    """
    return mask_everything(copy.deepcopy(body), session)


def restore_response(body: dict, session: Session) -> dict:
    """응답 본문의 가명 표시를 원문으로 되돌린 사본을 돌려준다.

    되돌리는 자리는 `output[]` 항목의 `content[]` 안뿐이다. 이 한 줄이 세 가지를
    동시에 정한다.

      복원한다      `output_text` · `refusal` — 사람이 화면에서 읽는 자리
      복원 안 한다  `function_call.arguments` — 도구에게 건네는 인자.
                    복원하면 모델이 부르는 **모든** 도구가 원문을 받는데,
                    프록시는 그 도구가 로컬인지 원격인지 알 방법이 없다.
      복원 안 한다  `reasoning.summary[]` — 추론 요약. Anthropic 쪽에서 thinking
                    블록을 되돌리지 않는 것과 같은 판단이다.

    뒤의 둘이 `content` 가 아니라 각각 `arguments` · `summary` 를 쓰기 때문에,
    "content 안만 본다" 는 규칙 하나로 셋이 갈린다. 자리를 따로 세지 않아도
    되는 것은 우연이 아니라 이 API 의 모양이 그렇게 생겼기 때문이다.

    추론 요약이 가명 표시인 채로 화면에 남는 것은 **눈에 보이는 흠**이고,
    도구 인자를 되돌리는 것은 **보이지 않는 유출**이다. 둘 중 하나를 골라야 한다면
    보이는 쪽이다.
    """
    restored = copy.deepcopy(body)

    출력 = restored.get("output")
    if not isinstance(출력, list):
        return restored

    for item in 출력:
        if not isinstance(item, dict):
            continue
        parts = item.get("content")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            # text 와 refusal 둘 다 사람이 읽는 자리다. 거절 메시지에만 가명
            # 표시가 남으면 사용자는 무슨 말인지 알 수 없다.
            for field in ("text", "refusal"):
                if isinstance(part.get(field), str):
                    part[field] = restore(part[field], session)

    return restored
