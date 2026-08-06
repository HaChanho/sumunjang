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
