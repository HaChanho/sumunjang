"""OpenAI Responses API 본문 변환 테스트.

이 경로가 필요한 이유는 실왕복이 알려줬다. Codex CLI 0.144.1 을 수문장에 물렸더니
`wire_api = "chat"` 은 지원이 끊겼다며 시작조차 안 했고, `responses` 로 바꾸니
`/v1/responses` 로 두드려 404 를 받았다. OpenAI 는 2026년 2월 Codex 에서
chat/completions 지원을 완전히 제거했고, 마이그레이션 안내에 이렇게 적었다 —
"조직이 LLM 프록시나 게이트웨이를 쓴다면 그것이 Responses API 를 지원하는지
확인하라." 수문장이 정확히 그 게이트웨이다.

Chat Completions 와 모양이 다르다.

  system 프롬프트   chat 은 messages 의 한 항목, responses 는 `instructions` 필드
  대화 기록        `messages[]` 가 아니라 `input[]` 항목 배열
  텍스트 블록      `{"type":"text"}` 가 아니라 입력은 `input_text`, 출력은 `output_text`
  도구 결과        `role:"tool"` 메시지가 아니라 `function_call_output` 항목
  응답 본문        `choices[].message` 가 아니라 `output[]` 항목 배열

가리는 자리와 복원하는 자리에 대한 **판단**은 세 프로토콜에서 같다. 다른 것은
그 자리가 어디냐뿐이다.
"""

import asyncio
import json

import httpx

from sumunjang.mask import Session
from sumunjang.proxy import create_app
from sumunjang.responses import mask_request, restore_response


def test_사용자_입력을_마스킹한다():
    session = Session()
    body = {
        "model": "gpt-5",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "고객 900101-1234568"}],
            }
        ],
    }

    masked = mask_request(body, session)

    assert masked["input"][0]["content"][0]["text"] == "고객 [주민등록번호_1]"


def test_instructions_도_마스킹한다():
    """responses 는 system 프롬프트를 별도 필드로 둔다.

    도구가 사용자 이메일을 여기에 심는다. Anthropic 실왕복에서 가려진 8건 중
    한 건이 정확히 그 경로였다 — 사용자가 붙여넣은 적 없는 값이 시스템
    프롬프트로 흘러들었다.

    처음에는 `담당자는 김수현입니다` 로 썼다가 실패했다. 앵커 없는 문장 속
    이름은 못 잡는다고 README 에 선언해 둔 자리다. 새 프로토콜에서도 같은
    규칙이 적용된다는 뜻이니 실패한 쪽이 옳았다.
    """
    session = Session()
    body = {"instructions": "사용자 이메일: hong@daehan-tech.co.kr", "input": []}

    masked = mask_request(body, session)

    assert masked["instructions"] == "사용자 이메일: [이메일_1]"


def test_도구_실행_결과를_마스킹한다():
    """사용자가 붙여넣지 않아도 원문이 흘러드는 가장 중요한 경로."""
    session = Session()
    body = {
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "user_rrn=900101-1234568",
            }
        ]
    }

    masked = mask_request(body, session)

    assert masked["input"][0]["output"] == "user_rrn=[주민등록번호_1]"


def test_원본_요청을_바꾸지_않는다():
    session = Session()
    body = {"instructions": "사용자 이메일: hong@daehan-tech.co.kr", "input": []}

    mask_request(body, session)

    assert body["instructions"] == "사용자 이메일: hong@daehan-tech.co.kr"


def test_응답의_사람이_읽는_자리를_복원한다():
    session = Session()
    mask_request(
        {"input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "고객 900101-1234568"}]}]},
        session,
    )

    복원 = restore_response(
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "[주민등록번호_1] 확인했습니다"}],
                }
            ]
        },
        session,
    )

    assert 복원["output"][0]["content"][0]["text"] == "900101-1234568 확인했습니다"


def test_도구에게_건네는_인자는_복원하지_않는다():
    """chat 쪽 tool_calls 와 같은 판단이다.

    복원하면 모델이 부르는 모든 도구가 원문을 받는데, 프록시는 그 도구가
    로컬인지 원격인지 알 방법이 없다. 모르면 복원하지 않는다.
    """
    session = Session()
    mask_request(
        {"input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "고객 900101-1234568"}]}]},
        session,
    )

    복원 = restore_response(
        {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": '{"rrn": "[주민등록번호_1]"}',
                }
            ]
        },
        session,
    )

    assert 복원["output"][0]["arguments"] == '{"rrn": "[주민등록번호_1]"}'


class _업스트림:
    """responses 모양으로 답하는 에코 업스트림."""

    def __init__(self) -> None:
        self.received: dict | None = None

    async def __call__(self, scope, receive, send):
        chunks = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        self.received = json.loads(b"".join(chunks))

        payload = json.dumps(
            {
                "id": "resp_test",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "확인했습니다"}],
                    }
                ],
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def test_responses_경로가_마스킹돼_전달된다():
    """이것이 RED 의 핵심이다. 지금은 모르는 경로라 404 로 막힌다.

    막히는 것 자체는 설계대로다(모르면 차단). 문제는 수문장이 "OpenAI 호환" 을
    표방하면서 OpenAI 의 대표 코딩 에이전트를 통째로 못 받는다는 것이다.
    """
    업스트림 = _업스트림()

    async def 시나리오():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=업스트림), base_url="http://upstream"
        ) as upstream_client:
            app = create_app(upstream_base_url="http://upstream", client=upstream_client)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as proxy_client:
                return await proxy_client.post(
                    "/v1/responses",
                    json={
                        "model": "gpt-5",
                        "instructions": "사용자 이메일: hong@daehan-tech.co.kr",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "결제자 900101-1234568 조회"}
                                ],
                            }
                        ],
                    },
                )

    응답 = asyncio.run(시나리오())

    assert 응답.status_code == 200
    나간것 = json.dumps(업스트림.received, ensure_ascii=False)
    assert "900101-1234568" not in 나간것
    assert "hong@daehan-tech.co.kr" not in 나간것
    assert "[이메일_1]" in 나간것
    assert "[주민등록번호_1]" in 나간것
