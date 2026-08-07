"""마스킹 범위 테스트 — 본문의 어느 자리든 원문이 남으면 안 된다.

리뷰에서 드러난 결함의 근본 원인은 마스킹이 "아는 자리만 가린다"는 것이었다.
그것은 곧 "모르는 자리는 샌다"는 뜻이다. 게이트웨이 경로에는 "모르면 차단"을
적용해 놓고 본문 필드에는 "모르면 통과"를 쓰고 있었다.

이 파일은 그 뒤집기를 고정한다. 새 API 필드가 생겨도 자동으로 보호되어야 한다.
"""

import json

import pytest

from sumunjang.anthropic import mask_request as anthropic_mask
from sumunjang.mask import Session
from sumunjang.openai import mask_request as openai_mask

원문 = "900101-1234568"


def _나간본문(masker, body) -> str:
    return json.dumps(masker(body, Session()), ensure_ascii=False)


ANTHROPIC_자리 = {
    "document 블록": {"messages": [{"role": "user", "content": [
        {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": f"주민번호 {원문}"}}]}]},
    "search_result 블록": {"messages": [{"role": "user", "content": [
        {"type": "search_result", "content": [{"type": "text", "text": f"주민번호 {원문}"}]}]}]},
    "모르는 블록 타입": {"messages": [{"role": "user", "content": [
        {"type": "훗날_생길_블록", "text": f"주민번호 {원문}"}]}]},
    "tools[].description": {"messages": [], "tools": [
        {"name": "lookup", "description": f"고객 {원문} 조회"}]},
    "tools[].input_schema": {"messages": [], "tools": [{"name": "x", "input_schema": {
        "type": "object", "properties": {"q": {"type": "string", "description": f"예: {원문}"}}}}]},
    "metadata.user_id": {"messages": [], "metadata": {"user_id": 원문}},
    "요청의 tool_use.input": {"messages": [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {"content": f"주민번호 {원문}"}}]}]},
}

OPENAI_자리 = {
    "prediction.content": {"messages": [], "prediction": {"type": "content", "content": f"주민번호 {원문}"}},
    "messages[].name": {"messages": [{"role": "user", "name": 원문, "content": "안녕"}]},
    "최상위 user": {"messages": [], "user": 원문},
    "tools[].function.description": {"messages": [], "tools": [
        {"type": "function", "function": {"name": "f", "description": f"고객 {원문}"}}]},
    "file.file_data": {"messages": [{"role": "user", "content": [
        {"type": "file", "file": {"filename": f"{원문}.pdf", "file_data": f"주민번호 {원문}"}}]}]},
    "요청의 tool_calls.arguments": {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "w", "arguments": f'{{"t":"{원문}"}}'}}]}]},
}


@pytest.mark.parametrize("자리", ANTHROPIC_자리)
def test_Anthropic_본문의_어느_자리에도_원문이_남지_않는다(자리):
    assert 원문 not in _나간본문(anthropic_mask, ANTHROPIC_자리[자리]), f"{자리} 에서 유출"


@pytest.mark.parametrize("자리", OPENAI_자리)
def test_OpenAI_본문의_어느_자리에도_원문이_남지_않는다(자리):
    assert 원문 not in _나간본문(openai_mask, OPENAI_자리[자리]), f"{자리} 에서 유출"


def test_훼손되면_요청이_깨지는_자리는_건드리지_않는다():
    """추론 서명과 base64 덩어리는 한 글자만 바뀌어도 요청이 거부된다.

    이 자리들은 사람이 쓴 텍스트가 아니라 불투명한 덩어리다. 마스킹의 예외는
    여기까지이며, 그 밖의 모든 문자열은 가린다.
    """
    서명 = "ErUBCkYIBBgCKkBw" * 8
    base64덩어리 = "iVBORw0KGgoAAAANSUhEUg" * 4
    body = {
        "messages": [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "생각 중", "signature": 서명}]},
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64덩어리}}]},
        ]
    }

    나간것 = json.loads(json.dumps(anthropic_mask(body, Session()), ensure_ascii=False))

    assert 나간것["messages"][0]["content"][0]["signature"] == 서명
    assert 나간것["messages"][1]["content"][0]["source"]["data"] == base64덩어리


def test_짝이_어긋나면_요청이_깨지는_식별자는_가리지_않는다():
    """프로토콜이 만든 불투명 토큰에서의 패턴 일치는 정의상 오탐이다.

    tool_use_id 안의 숫자열이 우연히 주민등록번호 검증식을 통과하면, 가리는 순간
    tool_use 와 tool_result 의 짝이 어긋나 업스트림이 요청을 거부한다.
    이득은 0이고 손해는 확실하다. url 도 같다 — 가려진 주소는 해석되지 않는다.
    """
    아이디 = "toolu_9001011234568"
    주소 = "https://cdn.example.com/a@b.co/x.png"
    body = {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": 아이디, "content": "결과"},
        {"type": "image", "source": {"type": "url", "url": 주소}},
    ]}]}

    나간것 = anthropic_mask(body, Session())

    블록 = 나간것["messages"][0]["content"]
    assert 블록[0]["tool_use_id"] == 아이디
    assert 블록[1]["source"]["url"] == 주소


def test_구조를_가리키는_값은_손상되지_않는다():
    """모델명·역할·식별자는 마스킹해도 무해하다 — 개인정보처럼 생기지 않았으므로
    탐지기가 반응하지 않는다. 그래도 그대로인지 확인해 둔다."""
    body = {"model": "claude-opus-4", "max_tokens": 1024,
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01ABC", "content": "결과"}]}]}

    나간것 = anthropic_mask(body, Session())

    assert 나간것["model"] == "claude-opus-4"
    assert 나간것["messages"][0]["content"][0]["tool_use_id"] == "toolu_01ABC"
