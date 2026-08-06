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
