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


def test_주소_안의_개인정보도_가린다():
    """한때 url 을 예외로 뒀다가 되돌렸다.

    "가리면 첨부가 깨진다" 는 이유였는데 그 판단이 틀렸다. 깨지는 것은 눈에 보이고
    유출은 보이지 않는다. 주소 안의 개인정보는 업스트림이 그 주소를 가져가는 순간
    그대로 넘어가므로, 가려서 요청이 실패하는 편이 안전한 실패다.
    """
    body = {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "url", "url": f"https://x.example.com/{원문}/a.png"}}]}]}

    assert 원문 not in _나간본문(anthropic_mask, body)


def test_data_URI_는_가리지_않는다():
    """data: 는 주소가 아니라 알맹이를 담은 덩어리다. 가리면 첨부가 훼손된다."""
    덩어리 = "data:image/png;base64," + "iVBORw0KGgoAAAANSUhEUg" * 4
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": 덩어리}}]}]}

    나간것 = openai_mask(body, Session())

    assert 나간것["messages"][0]["content"][0]["image_url"]["url"] == 덩어리


def test_사전의_키도_가린다():
    """도구 입력 스키마의 속성 이름처럼 키에도 사람이 쓴 값이 들어간다."""
    body = {"messages": [], "tools": [{"input_schema": {"properties": {원문: {"type": "string"}}}}]}

    assert 원문 not in _나간본문(anthropic_mask, body)


def test_식별자에_개인정보가_들어_있으면_가리되_짝은_유지한다():
    """한때 식별자를 예외로 뒀다. 예외 칸 자체가 문제를 둘 만들었다.

    우회 통로가 됐고(식별자에 개인정보를 넣으면 그대로 나간다), id 는 가리고
    tool_use_id 는 안 가려 오히려 짝이 어긋났다. 전부 가리면 같은 값은 같은
    가명을 받으므로 짝이 맞고 통로도 사라진다.
    """
    아이디 = f"toolu_{원문}"
    body = {"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": 아이디, "name": "Read", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": 아이디, "content": "결과"}]},
    ]}

    나간것 = anthropic_mask(body, Session())

    호출 = 나간것["messages"][0]["content"][0]["id"]
    결과 = 나간것["messages"][1]["content"][0]["tool_use_id"]
    assert 원문 not in 호출
    assert 호출 == 결과, "짝이 어긋나면 업스트림이 요청을 거부한다"


def test_평문_data_URI_안의_개인정보도_가린다():
    """data: 가 언제나 base64 인 것은 아니다.

    data:image/svg+xml,<svg>...</svg> 는 평문이고 그 안에 글자가 들어간다.
    `;base64,` 가 붙은 것만 알맹이로 보고 건너뛴다.
    """
    평문 = f"data:image/svg+xml,<svg><text>{원문}</text></svg>"
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": 평문}}]}]}

    assert 원문 not in _나간본문(openai_mask, body)


def test_구조를_가리키는_값은_손상되지_않는다():
    """모델명·역할·식별자는 마스킹해도 무해하다 — 개인정보처럼 생기지 않았으므로
    탐지기가 반응하지 않는다. 그래도 그대로인지 확인해 둔다."""
    body = {"model": "claude-opus-4", "max_tokens": 1024,
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01ABC", "content": "결과"}]}]}

    나간것 = anthropic_mask(body, Session())

    assert 나간것["model"] == "claude-opus-4"
    assert 나간것["messages"][0]["content"][0]["tool_use_id"] == "toolu_01ABC"


# ── 예외 판정은 자리와 값을 함께 본다 ──────────────────────────────────
# 얕은 신호(키 이름, 값 접두사)만으로 판정하다 세 번 뚫렸다. 공격자가 그 신호를
# 직접 쓸 수 있으면 그것은 예외 조건이 아니라 우회 스위치다.

우회스위치 = {
    "data: 접두사만 붙이기": {"messages": [{"role": "user", "content": f"data:text/plain;base64, 주민등록번호 {원문}"}]},
    "signature 라는 키를 아무 데나": {"messages": [], "metadata": {"audit": {"signature": f"홍길동 {원문}"}}},
    "type 을 base64 라고 적어두기": {"messages": [{"role": "user", "content": [
        {"type": "document", "source": {"type": "base64", "media_type": "text/plain", "data": f"주민번호 {원문}"}}]}]},
}


@pytest.mark.parametrize("수법", 우회스위치)
def test_얕은_신호로_마스킹을_건너뛸_수_없다(수법):
    assert 원문 not in _나간본문(anthropic_mask, 우회스위치[수법]), f"{수법} 으로 우회됨"


def test_숫자로_들어온_식별자도_가린다():
    """CSV·로그를 JSON 으로 옮겨 붙이면 식별자가 숫자형으로 들어온다.

    가려야 할 것이 있으면 문자열로 바뀐다. 그래서 업스트림이 거부한다면 그것은
    눈에 보이는 실패다.
    """
    유효한_주민등록번호 = 8803121000068
    body = {"messages": [], "metadata": {"v": 유효한_주민등록번호}}

    assert str(유효한_주민등록번호) not in _나간본문(anthropic_mask, body)


def test_구조를_이루는_숫자는_그대로_둔다():
    """개인정보처럼 생기지 않은 숫자는 탐지기가 반응하지 않으므로 형이 유지된다."""
    body = {"messages": [], "max_tokens": 1024, "temperature": 0.7, "stream": True}

    나간것 = anthropic_mask(body, Session())

    assert 나간것["max_tokens"] == 1024
    assert 나간것["temperature"] == 0.7
    assert 나간것["stream"] is True


def test_추론_블록은_본문과_서명을_함께_보존한다():
    """서명이 본문을 보증한다. 본문만 가리고 서명을 남기면 서명이 보증하지 못하는
    본문이 되어 다음 턴이 거부된다.

    추론은 모델이 만든 글이라 사용자 원문이 있을 수 없다 — 인바운드가 모두 가려진
    상태이므로 모델은 원문을 본 적이 없다. 모델이 스스로 만들어낸 메일 주소가
    개인정보 규칙에 걸리는 것이 문제였다.
    """
    서명 = "ErUBCkYIBBgCKkBw" * 8
    생각 = "보고서를 noreply@github.com 로 보낸다고 했다"
    body = {"messages": [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": 생각, "signature": 서명}]}]}

    블록 = anthropic_mask(body, Session())["messages"][0]["content"][0]

    assert 블록["thinking"] == 생각
    assert 블록["signature"] == 서명
