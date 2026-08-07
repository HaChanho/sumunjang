"""OpenAI Chat Completions 본문 변환 테스트.

마스킹·복원 계층은 프로토콜과 무관하다. 여기서 시험하는 것은 "본문의 어느
자리에 사람이 쓴 텍스트가 들어 있는가" 하나뿐이다.
"""

from sumunjang.mask import Session, mask
from sumunjang.openai import mask_request, restore_response


def test_사용자_메시지를_마스킹한다():
    session = Session()
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "고객 900101-1234568"}]}

    masked = mask_request(body, session)

    assert masked["messages"][0]["content"] == "고객 [주민등록번호_1]"


def test_system_메시지도_마스킹한다():
    """OpenAI 는 system 을 별도 필드가 아니라 messages 의 한 항목으로 둔다."""
    session = Session()
    body = {"messages": [{"role": "system", "content": "담당자: 김수현"}]}

    masked = mask_request(body, session)

    assert masked["messages"][0]["content"] == "담당자: [이름_1]"


def test_블록_배열_본문도_마스킹한다():
    """멀티모달 요청에서는 content 가 배열이다. 텍스트 블록만 손댄다."""
    session = Session()
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "연락처 010-1234-5678"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            }
        ]
    }

    masked = mask_request(body, session)

    blocks = masked["messages"][0]["content"]
    assert blocks[0]["text"] == "연락처 [전화번호_1]"
    assert blocks[1]["image_url"]["url"] == "https://example.com/a.png"


def test_도구_실행_결과를_마스킹한다():
    """Anthropic 의 tool_result 에 해당하는 자리다.

    사용자가 붙여넣지 않아도 파일·명령 결과로 원문이 흘러드는 경로이므로
    가장 중요하다.
    """
    session = Session()
    body = {
        "messages": [
            {"role": "tool", "tool_call_id": "call_1", "content": "주민등록번호: 900101-1234568"}
        ]
    }

    masked = mask_request(body, session)

    assert masked["messages"][0]["content"] == "주민등록번호: [주민등록번호_1]"


def test_원본_요청을_바꾸지_않는다():
    session = Session()
    body = {"messages": [{"role": "user", "content": "고객 900101-1234568"}]}

    mask_request(body, session)

    assert body["messages"][0]["content"] == "고객 900101-1234568"


def test_응답의_가명_표시를_복원한다():
    session = Session()
    mask("고객 900101-1234568", session)
    body = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "[주민등록번호_1] 확인"}}]}

    restored = restore_response(body, session)

    assert restored["choices"][0]["message"]["content"] == "900101-1234568 확인"


def test_도구_호출_인자는_복원하지_않는다():
    """Anthropic 의 tool_use.input 과 같은 판단이다.

    여기를 복원하면 모델이 부르는 모든 도구가 원문을 받는다. 프록시는 그 도구가
    로컬인지 원격인지 알 방법이 없으므로, 모르면 복원하지 않는다.
    """
    session = Session()
    mask("담당자: 김수현", session)
    body = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "[이름_1] 저장합니다",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "write", "arguments": '{"text": "[이름_1]"}'},
                        }
                    ],
                },
            }
        ]
    }

    restored = restore_response(body, session)

    message = restored["choices"][0]["message"]
    assert message["content"] == "김수현 저장합니다"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"text": "[이름_1]"}'
