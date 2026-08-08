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
from sumunjang.responses import mask_request as responses_mask

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


# Responses API 의 자리들. 실제 Codex CLI 0.144.1 이 보낸 요청에서 최상위 키를
# 그대로 옮겨 왔다 — 상상해서 적으면 진짜와 다른 것을 시험하게 된다. 실제로
# 도구 이름이 shell 이 아니라 exec_command 였고, instructions 는 20,751 자였다.
RESPONSES_자리 = {
    "instructions": {"instructions": f"담당 {원문}", "input": []},
    "input[].content[].text": {"input": [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": f"주민번호 {원문}"}]}]},
    "function_call_output.output": {"input": [
        {"type": "function_call_output", "call_id": "c1", "output": f"user_rrn={원문}"}]},
    "요청의 function_call.arguments": {"input": [
        {"type": "function_call", "call_id": "c1", "name": "w", "arguments": f'{{"t":"{원문}"}}'}]},
    "input 이 맨 문자열": {"input": f"주민번호 {원문} 조회"},
    "tools[].description": {"input": [], "tools": [
        {"type": "function", "name": "exec_command", "description": f"고객 {원문} 조회"}]},
    "tools[].parameters": {"input": [], "tools": [{"type": "function", "name": "x", "parameters": {
        "type": "object", "properties": {"cmd": {"type": "string", "description": f"예: {원문}"}}}}]},
    "prompt_cache_key": {"input": [], "prompt_cache_key": 원문},
    "client_metadata": {"input": [], "client_metadata": {"session": 원문}},
    "metadata": {"input": [], "metadata": {"user": 원문}},
    "text.format 스키마": {"input": [], "text": {"format": {
        "type": "json_schema", "name": "r", "schema": {"properties": {"a": {"description": 원문}}}}}},
    "모르는 항목 타입": {"input": [{"type": "훗날_생길_항목", "text": f"주민번호 {원문}"}]},
    "모르는 최상위 필드": {"input": [], "훗날_생길_필드": f"주민번호 {원문}"},
    # 아래 셋은 예외를 노린 우회다. 예외 판정은 네 라운드 연속 뚫린 자리라,
    # 프로토콜이 바뀌면 같은 수법을 다시 시험한다. 프로토콜마다 "추론 블록" 을
    # 부르는 이름이 다르다는 점(thinking ↔ reasoning)이 특히 위험하다 —
    # 예외 목록이 이름으로 판정하면 새 이름은 예외가 아니고, 반대로 새 이름을
    # 예외에 넣으면 요청자가 그 이름을 적어 넣는 것으로 끝난다.
    "추론인 척하는 reasoning 항목": {"input": [{"type": "reasoning", "id": "rs_1",
        "encrypted_content": f"주민번호 {원문}",
        "summary": [{"type": "summary_text", "text": f"주민번호 {원문}"}]}]},
    "input_file 에 실린 평문 data URI": {"input": [{"type": "message", "role": "user", "content": [
        {"type": "input_file", "file_data": f"data:text/plain;base64,주민번호 {원문}"}]}]},
    "input_image 의 image_url": {"input": [{"type": "message", "role": "user", "content": [
        {"type": "input_image", "image_url": f"data:image/png;base64,{원문}"}]}]},
}


@pytest.mark.parametrize("자리", ANTHROPIC_자리)
def test_Anthropic_본문의_어느_자리에도_원문이_남지_않는다(자리):
    assert 원문 not in _나간본문(anthropic_mask, ANTHROPIC_자리[자리]), f"{자리} 에서 유출"


@pytest.mark.parametrize("자리", OPENAI_자리)
def test_OpenAI_본문의_어느_자리에도_원문이_남지_않는다(자리):
    assert 원문 not in _나간본문(openai_mask, OPENAI_자리[자리]), f"{자리} 에서 유출"


@pytest.mark.parametrize("자리", RESPONSES_자리)
def test_Responses_본문의_어느_자리에도_원문이_남지_않는다(자리):
    """세 번째 프로토콜도 같은 검사를 받는다.

    프로토콜이 늘 때 이 표를 함께 늘리지 않으면, 마스킹이 프로토콜과 무관하다는
    주장이 검사 없이 남는다. 새 프로토콜은 표에 줄 하나라는 말은 **시험도 줄
    하나**라는 뜻이어야 한다.
    """
    assert 원문 not in _나간본문(responses_mask, RESPONSES_자리[자리]), f"{자리} 에서 유출"


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


def test_우리가_내보낸_추론_블록만_예외로_둔다():
    """예외 판정 근거는 요청자가 쓸 수 없는 것이어야 한다.

    `type: "thinking"` 은 요청 본문에 그냥 적으면 되는 값이다. 그것을 방아쇠로
    삼으면 예외가 아니라 우회 스위치가 된다 — 아무 글에 그 껍데기만 씌우면
    영원히 안 가려진다. 위조할 수 없는 유일한 신호는 우리가 그 서명을 내보낸
    적이 있는가다.
    """
    서명 = "ErUBCkYIBBgCKkBw" * 8
    생각 = "보고서를 noreply@github.com 로 보낸다고 했다"
    body = {"messages": [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": 생각, "signature": 서명}]}]}

    # 우리가 내보낸 적 없는 서명 — 추론인 척하는 텍스트다. 가린다.
    session = Session()
    블록 = anthropic_mask(body, session)["messages"][0]["content"][0]
    assert 블록["thinking"] != 생각

    # 우리가 내보낸 짝 — 진짜 추론이다. 본문과 서명을 함께 보존한다.
    session = Session()
    session.remember_thinking(서명, 생각)
    블록 = anthropic_mask(body, session)["messages"][0]["content"][0]
    assert 블록["thinking"] == 생각
    assert 블록["signature"] == 서명

    # 진짜 서명에 본문만 갈아 끼운 것 — 서명만 대조하면 통과하던 자리다.
    session = Session()
    session.remember_thinking(서명, 생각)
    갈아낀것 = {"messages": [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": f"주민등록번호 {원문}", "signature": 서명}]}]}
    assert 원문 not in _나간본문(anthropic_mask, 갈아낀것)


def test_추론인_척하는_껍데기로_마스킹을_건너뛸_수_없다():
    body = {"messages": [{"role": "user", "content": [
        {"type": "thinking", "thinking": f"주민등록번호 {원문}", "signature": "지어낸서명"}]}]}

    assert 원문 not in _나간본문(anthropic_mask, body)


def test_base64_알파벳을_흉내낸_개인정보는_건너뛰지_않는다():
    """예외 근거는 "디코드되는가" 가 아니라 "개인정보가 보이지 않는가" 다.

    base64 알파벳은 숫자와 영문자를 전부 포함한다. 그래서 구분자 없이 적은 한국
    식별자와 API 키는 **인코딩 없이도** 조건을 만족한다 —
    `AKIAIOSFODNN7EXAMPLE…` 은 그 자체가 유효한 base64 다. "제대로 인코딩해
    그림이라고 주장해야 한다" 던 전제가 성립하지 않았다.

    탐지되는지는 요청자가 흉내낼 수 없다.
    """
    import base64

    for 흉내 in (
        "AKIAIOSFODNN7EXAMPLE" + "A" * 24,      # AWS 키
        "8503121000003" + "A" * 31,             # 붙여쓴 주민등록번호
        "4111111111111111" + "A" * 28,          # 카드번호
    ):
        본문 = {"messages": [], "a": {"url": f"data:image/png;base64,{흉내}"}}
        assert 흉내 not in _나간본문(anthropic_mask, 본문), f"흉내로 통과됨: {흉내[:24]}"

        블록 = {"messages": [{"role": "user", "content": [
            {"type": "document", "source": {
                "type": "base64", "media_type": "image/png", "data": 흉내}}]}]}
        assert 흉내 not in _나간본문(anthropic_mask, 블록), f"흉내로 통과됨: {흉내[:24]}"

    # 진짜 그림 알맹이는 훼손하면 안 된다.
    진짜 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(60))).decode()
    그림 = {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": 진짜}}]}]}
    나간것 = anthropic_mask(그림, Session())
    assert 나간것["messages"][0]["content"][0]["source"]["data"] == 진짜



def test_선언한_미디어_타입의_실제_시작_바이트가_있어야_건너뛴다():
    """예외 근거를 "탐지 규칙에 안 걸리는가" 로 두면 탐지기의 한계가 그대로
    예외의 한계가 된다. 매직 넘버는 그 고리를 끊는다."""
    import base64

    진짜 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(60))).decode()
    가짜 = base64.b64encode(b"NOTPNG!!" + bytes(range(60))).decode()

    def 첨부(알맹이):
        return {"messages": [{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": 알맹이}}]}]}

    보존 = anthropic_mask(첨부(진짜), Session())
    assert 보존["messages"][0]["content"][0]["source"]["data"] == 진짜

    # PNG 라 주장하지만 PNG 가 아닌 것은 예외가 아니다 — 알맹이로 취급하지 않는다.
    from sumunjang.body import _opaque_base64

    assert not _opaque_base64(가짜, "image/png")
    assert _opaque_base64(진짜, "image/png")


def test_추론_블록의_형제_키에_얹은_값은_예외가_아니다():
    """예외를 받는 값과 검증하는 값이 같아야 한다.

    형제 키의 값으로 검증하면 둘이 갈라진다 — 진짜 (서명, 본문) 짝을 그대로
    두고 다른 키에 원문을 얹으면 그 값만 마스킹을 건너뛰었다. 프록시가
    클라이언트에게 직접 건네준 값만 있으면 되는 우회라 특별한 권한도 필요 없다.

    서명이 보증하는 것은 그 본문이지 블록 안의 아무 키가 아니다.
    """
    서명, 생각 = "ErUBCkYIBBgCKkBw" * 8, "원래 생각"

    session = Session()
    session.remember_thinking(서명, 생각)

    얹은것 = {"messages": [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": 생각, "signature": 서명,
         "data": f"주민등록번호는 {원문} 입니다"}]}]}

    assert 원문 not in _나간본문(anthropic_mask, 얹은것)

    # 진짜 짝은 그대로 보존된다.
    나간것 = anthropic_mask(
        {"messages": [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": 생각, "signature": 서명}]}]},
        session,
    )
    블록 = 나간것["messages"][0]["content"][0]
    assert 블록["thinking"] == 생각 and 블록["signature"] == 서명
