"""Anthropic Messages API 요청·응답 본문 변환 테스트."""

from sumunjang.anthropic import mask_request, restore_response
from sumunjang.mask import Session, mask


def test_사용자_메시지의_문자열_본문을_마스킹한다():
    session = Session()
    body = {"messages": [{"role": "user", "content": "고객 900101-1234568 확인"}]}

    masked = mask_request(body, session)

    assert masked["messages"][0]["content"] == "고객 [주민등록번호_1] 확인"


def test_system_프롬프트도_마스킹한다():
    session = Session()
    body = {"system": "담당자 연락처는 010-1234-5678", "messages": []}

    masked = mask_request(body, session)

    assert masked["system"] == "담당자 연락처는 [전화번호_1]"


def test_블록_형식_본문의_텍스트를_마스킹한다():
    session = Session()
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "메일 kim@example.com"}]}
        ]
    }

    masked = mask_request(body, session)

    assert masked["messages"][0]["content"][0]["text"] == "메일 [이메일_1]"


def test_tool_result_본문을_마스킹한다():
    """파일·명령 실행 결과가 흐르는 경로. 사용자가 직접 붙여넣지 않아도 유출된다."""
    session = Session()
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "로그: 결제자 900101-1234568",
                    }
                ],
            }
        ]
    }

    masked = mask_request(body, session)

    assert masked["messages"][0]["content"][0]["content"] == "로그: 결제자 [주민등록번호_1]"


def test_원본_요청_객체를_바꾸지_않는다():
    """호출자가 원문을 계속 쓸 수 있어야 한다."""
    session = Session()
    body = {"messages": [{"role": "user", "content": "연락처 010-1234-5678"}]}

    mask_request(body, session)

    assert body["messages"][0]["content"] == "연락처 010-1234-5678"


def test_응답_본문의_placeholder를_복원한다():
    session = Session()
    body = {"messages": [{"role": "user", "content": "연락처 010-1234-5678"}]}
    mask_request(body, session)

    response = {"content": [{"type": "text", "text": "[전화번호_1] 로 보냈습니다"}]}

    restored = restore_response(response, session)

    assert restored["content"][0]["text"] == "010-1234-5678 로 보냈습니다"


def test_tool_use_의_가명_표시는_복원하지_않는다():
    """의도된 동작이다. 지나가다 빠뜨린 것이 아니다.

    tool_use.input 은 모델이 도구에게 건네는 인자다. 여기를 복원하면 모델이
    부르는 **모든 도구**가 원문을 받는다. Write·Bash 는 사용자 기계에서 도니
    괜찮지만, WebFetch 나 MCP 서버 호출은 다른 네트워크 출구다. 프록시는 도구
    이름만 알 뿐 그것이 로컬인지 원격인지 알 방법이 없다.

    모르면 복원하지 않는다 — 경계 게이트웨이의 기본값과 같은 방향이다.
    대가로 에이전트가 파일을 쓸 때 가명 표시가 그대로 박힐 수 있다.
    """
    session = Session()
    mask("담당자: 김수현", session)

    body = {
        "content": [
            {"type": "text", "text": "[이름_1] 정보를 저장하겠습니다"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Write",
                "input": {"file_path": "/tmp/out.md", "content": "담당자: [이름_1]"},
            },
        ]
    }

    restored = restore_response(body, session)

    # 사람이 읽는 자리는 복원한다
    assert restored["content"][0]["text"] == "김수현 정보를 저장하겠습니다"
    # 도구에게 건네는 자리는 건드리지 않는다
    assert restored["content"][1]["input"]["content"] == "담당자: [이름_1]"
