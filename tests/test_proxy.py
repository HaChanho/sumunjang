"""프록시 E2E 테스트.

업스트림은 실제 ASGI 앱(에코 fixture)으로 띄운다. API 키도 네트워크도 필요 없다.
"""

import asyncio
import json

import httpx

from sumunjang.proxy import create_app


class UpstreamRecorder:
    """업스트림이 실제로 받은 본문을 기록하는 에코 서버."""

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

        # 사용자 메시지를 그대로 되읊는 응답 — placeholder가 원형으로 돌아온 상황을 흉내낸다.
        echoed = self.received["messages"][0]["content"]
        payload = json.dumps(
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"확인했습니다: {echoed}"}],
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


async def _roundtrip(request_body: dict) -> tuple[dict, dict]:
    """프록시를 통해 한 번 왕복하고 (업스트림이 받은 것, 사용자가 받은 것)을 돌려준다."""
    upstream = UpstreamRecorder()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=upstream), base_url="http://upstream"
    ) as upstream_client:
        app = create_app(upstream_base_url="http://upstream", client=upstream_client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy"
        ) as proxy_client:
            response = await proxy_client.post("/v1/messages", json=request_body)

    return upstream.received, response.json()


def test_업스트림은_원문을_보지_못하고_사용자는_원문을_돌려받는다():
    """이 프로젝트의 핵심 계약."""
    body = {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "결제자 900101-1234568 조회해줘"}],
    }

    upstream_seen, user_saw = asyncio.run(_roundtrip(body))

    # 서버로 나간 요청에는 원문이 없다
    assert "900101-1234568" not in json.dumps(upstream_seen, ensure_ascii=False)
    assert "[주민등록번호_1]" in json.dumps(upstream_seen, ensure_ascii=False)

    # 사용자 화면에는 원문이 돌아온다
    assert "900101-1234568" in user_saw["content"][0]["text"]


def test_개인정보가_없으면_본문이_그대로_전달된다():
    body = {"model": "claude-test", "messages": [{"role": "user", "content": "안녕하세요"}]}

    upstream_seen, user_saw = asyncio.run(_roundtrip(body))

    assert upstream_seen["messages"][0]["content"] == "안녕하세요"
    assert "안녕하세요" in user_saw["content"][0]["text"]


def test_요청마다_무엇을_가렸는지_기록한다():
    """사용자와 심사자가 직접 확인할 수 있는 물증. 기록에는 가려진 본문만 남는다."""
    records: list[dict] = []

    async def scenario():
        upstream = UpstreamRecorder()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=upstream), base_url="http://upstream"
        ) as upstream_client:
            app = create_app(
                upstream_base_url="http://upstream",
                client=upstream_client,
                on_request=records.append,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as proxy_client:
                await proxy_client.post(
                    "/v1/messages",
                    json={
                        "model": "claude-test",
                        "messages": [
                            {"role": "user", "content": "결제자 900101-1234568 연락처 010-1234-5678"}
                        ],
                    },
                )

    asyncio.run(scenario())

    assert len(records) == 1
    record = records[0]
    assert record["masked_count"] == 2
    assert sorted(record["categories"]) == ["PHONE", "RRN"]
    # 기록에 남는 본문은 이미 가려진 것이어야 한다 — 로그가 새 유출 경로가 되면 안 된다
    assert "900101-1234568" not in json.dumps(record["upstream_body"], ensure_ascii=False)


def test_같은_값이_반복돼도_이번_요청에서_가린_건수를_보고한다():
    """세션에 이미 등록된 값이라고 0건으로 보고하면 작동을 멈춘 것처럼 보인다.

    실제 왕복에서 드러난 결함이다. Claude Code는 매 요청마다 같은 시스템 프롬프트를
    보내는데, 두 번째 요청부터 "가린 항목 0건"으로 찍혔다.
    """
    records: list[dict] = []
    body = {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "연락처 010-1234-5678"}],
    }

    async def scenario():
        upstream = UpstreamRecorder()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=upstream), base_url="http://upstream"
        ) as upstream_client:
            app = create_app(
                upstream_base_url="http://upstream",
                client=upstream_client,
                on_request=records.append,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as proxy_client:
                await proxy_client.post("/v1/messages", json=body)
                await proxy_client.post("/v1/messages", json=body)

    asyncio.run(scenario())

    assert len(records) == 2
    # 두 번째 요청도 같은 값을 가렸다. 새로 등록되지 않았을 뿐이다.
    assert records[1]["masked_count"] == 1
    assert records[1]["categories"] == ["PHONE"]
    assert records[1]["new_count"] == 0


def test_thinking과_도구호출_블록도_delta로_흘려보낸다():
    """실제 왕복에서 드러난 결함.

    Anthropic SSE 규약은 content_block_start 에 빈 껍데기를 보내고 내용을 전부
    delta 로 흘린다. 클라이언트는 start 를 무시하고 delta 만 누적하므로,
    start 에만 내용을 담으면 thinking 이 빈 채로 남고 다음 턴에 400 을 맞는다.
    """
    from sumunjang.proxy import _as_sse_events

    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "속으로 생각한 내용", "signature": "sig-abc"},
            {"type": "text", "text": "답변입니다"},
            {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"path": "/tmp/a"}},
        ],
    }

    events = _as_sse_events(message)
    by_name: dict[str, list[dict]] = {}
    for name, data in events:
        by_name.setdefault(name, []).append(data)

    starts = by_name["content_block_start"]
    deltas = by_name["content_block_delta"]

    # start 는 빈 껍데기여야 한다
    assert starts[0]["content_block"] == {"type": "thinking", "thinking": "", "signature": ""}
    assert starts[1]["content_block"] == {"type": "text", "text": ""}
    assert starts[2]["content_block"]["input"] == {}

    # 내용은 delta 로 간다
    delta_types = [d["delta"]["type"] for d in deltas]
    assert "thinking_delta" in delta_types
    assert "signature_delta" in delta_types
    assert "text_delta" in delta_types
    assert "input_json_delta" in delta_types

    thinking_delta = next(d for d in deltas if d["delta"]["type"] == "thinking_delta")
    assert thinking_delta["delta"]["thinking"] == "속으로 생각한 내용"

    signature_delta = next(d for d in deltas if d["delta"]["type"] == "signature_delta")
    assert signature_delta["delta"]["signature"] == "sig-abc"

    input_delta = next(d for d in deltas if d["delta"]["type"] == "input_json_delta")
    assert json.loads(input_delta["delta"]["partial_json"]) == {"path": "/tmp/a"}


async def _stream_roundtrip(request_body: dict) -> tuple[dict, str]:
    """스트리밍 요청을 왕복하고 (업스트림이 받은 것, 사용자가 받은 SSE 원문)."""
    upstream = UpstreamRecorder()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=upstream), base_url="http://upstream"
    ) as upstream_client:
        app = create_app(upstream_base_url="http://upstream", client=upstream_client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy"
        ) as proxy_client:
            response = await proxy_client.post("/v1/messages", json=request_body)

    return upstream.received, response.text


def test_스트리밍_요청은_복원된_내용을_SSE로_돌려준다():
    """Claude Code는 스트리밍으로 호출한다.

    조각난 응답에서 placeholder를 정확히 복원하는 것은 신뢰하기 어려우므로,
    업스트림에는 통짜로 요청해 복원한 뒤 SSE 형태로 다시 흘려보낸다.
    """
    body = {
        "model": "claude-test",
        "stream": True,
        "messages": [{"role": "user", "content": "결제자 900101-1234568 조회"}],
    }

    upstream_seen, sse_text = asyncio.run(_stream_roundtrip(body))

    # 업스트림에는 스트리밍을 요청하지 않는다
    assert upstream_seen.get("stream") is not True
    assert "900101-1234568" not in json.dumps(upstream_seen, ensure_ascii=False)

    # 사용자에게는 SSE 이벤트로, 원문이 복원된 채 도착한다
    assert "event: message_start" in sse_text
    assert "event: content_block_delta" in sse_text
    assert "event: message_stop" in sse_text
    assert "900101-1234568" in sse_text


# ── 경계 게이트웨이가 다뤄야 할 다른 경로들 ────────────────────────────────
# Claude Code 는 /v1/messages 만 부르지 않는다. 토큰 계산과 모델 목록도 부른다.
# 전부 404 로 막으면 안전하긴 하지만 도구가 제 기능을 못 한다.


class PathRecorder:
    """어떤 경로로 무엇이 왔는지 기록하는 업스트림."""

    def __init__(self, payload: dict | None = None) -> None:
        self.path: str | None = None
        self.method: str | None = None
        self.received: dict | None = None
        self._payload = payload or {"input_tokens": 42}

    async def __call__(self, scope, receive, send):
        self.path, self.method = scope["path"], scope["method"]
        chunks = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        raw = b"".join(chunks)
        self.received = json.loads(raw) if raw else None

        body = json.dumps(self._payload).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


async def _call(method: str, path: str, body: dict | None = None, payload: dict | None = None):
    upstream = PathRecorder(payload)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=upstream), base_url="http://upstream"
    ) as upstream_client:
        app = create_app(upstream_base_url="http://upstream", client=upstream_client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy"
        ) as proxy_client:
            response = await proxy_client.request(method, path, json=body)
    return upstream, response


def test_토큰_계산_요청도_마스킹해서_넘긴다():
    """본문 모양이 /v1/messages 와 같다. 막으면 도구가 문맥 길이를 못 재고,
    그냥 통과시키면 원문이 그대로 나간다. 같은 변환을 적용한다."""
    upstream, response = asyncio.run(
        _call(
            "POST",
            "/v1/messages/count_tokens",
            {"model": "claude-opus-4", "messages": [{"role": "user", "content": "고객 900101-1234568"}]},
        )
    )

    assert response.status_code == 200
    assert upstream.path == "/v1/messages/count_tokens"
    wire = json.dumps(upstream.received, ensure_ascii=False)
    assert "900101-1234568" not in wire
    assert "주민등록번호_1" in wire


def test_모델_목록은_그대로_통과시킨다():
    """개인정보가 실릴 수 없는 읽기 전용 경로다. 막을 이유가 없다."""
    upstream, response = asyncio.run(_call("GET", "/v1/models", payload={"data": []}))

    assert response.status_code == 200
    assert upstream.path == "/v1/models"
    assert upstream.method == "GET"


def test_허용_목록에_없는_경로는_업스트림에_닿지_않는다():
    """모르는 경로를 그냥 흘려보내면 마스킹을 거치지 않은 본문이 나간다.
    경계 게이트웨이의 기본값은 통과가 아니라 차단이어야 한다."""
    upstream, response = asyncio.run(
        _call("POST", "/v1/some_new_endpoint", {"secret": "고객 900101-1234568"})
    )

    assert response.status_code == 404
    assert upstream.path is None, "업스트림에 요청이 닿았다"


def test_마스킹이_깨지면_업스트림에_아무것도_보내지_않는다(monkeypatch):
    """탐지·마스킹에서 예외가 나면 조용히 원문을 흘려보내는 대신 요청을 버린다.

    보안 도구의 실패 모드는 통과가 아니라 차단이어야 한다.
    """
    from sumunjang.mask import Session

    def 폭발(*args, **kwargs):
        raise RuntimeError("마스킹 중 예외")

    # 마스킹 도중 무엇이 터지든 상관없다는 것을 시험한다. 특정 모듈 속성을
    # 갈아끼우면 `from X import y` 가 이미 붙잡은 참조를 비껴가므로, 호출
    # 시점에 조회되는 메서드를 터뜨린다.
    monkeypatch.setattr(Session, "placeholder_for", 폭발)

    upstream, response = asyncio.run(
        _call("POST", "/v1/messages", {"messages": [{"role": "user", "content": "고객 900101-1234568"}]})
    )

    assert response.status_code == 500
    assert upstream.path is None, "마스킹이 깨졌는데 업스트림에 요청이 갔다"


# ── OpenAI 호환 경로 ──────────────────────────────────────────────────────
# 마스킹 계층은 프로토콜과 무관하다. 본문 모양과 SSE 형식만 다르다.


class OpenAIUpstream:
    """OpenAI 응답 모양으로 되읊는 업스트림."""

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

        echoed = self.received["messages"][0]["content"]
        payload = json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"확인했습니다: {echoed}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
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


async def _openai_roundtrip(body: dict):
    upstream = OpenAIUpstream()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=upstream), base_url="http://upstream"
    ) as upstream_client:
        app = create_app(upstream_base_url="http://upstream", client=upstream_client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy"
        ) as proxy_client:
            response = await proxy_client.post("/v1/chat/completions", json=body)
    return upstream, response


def test_OpenAI_경로도_업스트림에는_가명_사용자에게는_원문():
    upstream, response = asyncio.run(
        _openai_roundtrip(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "고객 900101-1234568 조회"}]}
        )
    )

    나간본문 = json.dumps(upstream.received, ensure_ascii=False)
    assert "900101-1234568" not in 나간본문
    assert "주민등록번호_1" in 나간본문

    돌아온것 = response.json()["choices"][0]["message"]["content"]
    assert "900101-1234568" in 돌아온것


def test_OpenAI_스트리밍은_복원된_내용을_data_줄로_돌려준다():
    """OpenAI SSE 는 event 줄이 없고 data 줄만 있으며 [DONE] 으로 끝난다."""
    upstream, response = asyncio.run(
        _openai_roundtrip(
            {
                "model": "gpt-4o",
                "stream": True,
                "messages": [{"role": "user", "content": "고객 900101-1234568 조회"}],
            }
        )
    )

    assert "stream" not in upstream.received, "업스트림에는 통짜로 요청해야 한다"
    assert response.headers["content-type"].startswith("text/event-stream")

    본문 = response.text
    assert 본문.rstrip().endswith("data: [DONE]")
    assert "event:" not in 본문, "OpenAI SSE 에는 event 줄이 없다"

    조각들 = [
        json.loads(line[len("data: ") :])
        for line in 본문.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    # 사용량 청크는 choices 가 빈 배열이다 — OpenAI 규약대로다.
    모인글 = "".join(
        c["choices"][0]["delta"].get("content", "") for c in 조각들 if c["choices"]
    )
    assert "900101-1234568" in 모인글
    마지막_내용청크 = [c for c in 조각들 if c["choices"]][-1]
    assert 마지막_내용청크["choices"][0]["finish_reason"] == "stop"


def test_OpenAI_스트리밍이_거절과_사용량을_잃지_않는다():
    """거절을 빠뜨리면 거절 응답이 내용 없는 빈 스트림이 된다."""
    upstream = OpenAIUpstream()

    async def 거절응답(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        payload = json.dumps({
            "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
            "choices": [{"index": 0, "message": {
                "role": "assistant", "content": None, "refusal": "그 요청은 도와드릴 수 없습니다"},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": payload})

    async def 왕복():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=거절응답), base_url="http://upstream"
        ) as up:
            app = create_app(upstream_base_url="http://upstream", client=up)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as proxy_client:
                return await proxy_client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o", "stream": True,
                          "messages": [{"role": "user", "content": "안녕"}]},
                )

    본문 = asyncio.run(왕복()).text

    assert "그 요청은 도와드릴 수 없습니다" in 본문
    assert "total_tokens" in 본문
    assert 본문.rstrip().endswith("data: [DONE]")


def test_헤더에_개인정보가_있으면_요청을_보내지_않는다():
    """본문만 전수 검사하고 헤더를 흘려보내고 있었다.

    가려서 보내지 않고 거부하는 이유는 가명 표시가 한글이라 HTTP 헤더로
    인코딩되지 않기 때문이다. 경로·쿼리와 같은 판단이다 — 보낼 수 없으면
    보내지 않는다.
    """
    upstream, response = asyncio.run(
        _call(
            "POST",
            "/v1/messages",
            {"messages": [{"role": "user", "content": "안녕"}]},
        )
    )
    assert response.status_code == 200  # 정상 요청은 통과한다

    받은것 = {}

    async def 시나리오():
        recorder = PathRecorder({"content": []})

        async def 기록(scope, receive, send):
            받은것.update({k.decode(): v.decode() for k, v in scope["headers"]})
            await recorder(scope, receive, send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=기록), base_url="http://upstream"
        ) as up:
            app = create_app(upstream_base_url="http://upstream", client=up)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as proxy_client:
                return await proxy_client.post(
                    "/v1/messages",
                    json={"messages": [{"role": "user", "content": "안녕"}]},
                    headers={"x-api-key": "sk-ant-key", "x-customer-email": "hong@example.com"},
                )

    거부됨 = asyncio.run(시나리오())

    assert 거부됨.status_code == 400
    assert not 받은것, "헤더에 개인정보가 있는데 업스트림에 요청이 갔다"


def test_자격증명과_협상_헤더는_그대로_넘긴다():
    """훼손하면 요청 자체가 성립하지 않는 자리다."""
    받은것 = {}

    async def 시나리오():
        recorder = PathRecorder({"content": []})

        async def 기록(scope, receive, send):
            받은것.update({k.decode(): v.decode() for k, v in scope["headers"]})
            await recorder(scope, receive, send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=기록), base_url="http://upstream"
        ) as up:
            app = create_app(upstream_base_url="http://upstream", client=up)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as proxy_client:
                await proxy_client.post(
                    "/v1/messages",
                    json={"messages": [{"role": "user", "content": "안녕"}]},
                    headers={"x-api-key": "sk-ant-real-key", "anthropic-version": "2023-06-01"},
                )

    asyncio.run(시나리오())

    assert 받은것["x-api-key"] == "sk-ant-real-key"
    assert 받은것["anthropic-version"] == "2023-06-01"
