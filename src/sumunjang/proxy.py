"""AI API 경계 게이트웨이.

들어온 요청을 마스킹해 업스트림에 넘기고, 돌아온 응답에서 원문을 복구한다.
웹 프레임워크 없이 순수 ASGI로 구현한다 — 개인정보가 지나가는 경로의
서드파티 코드를 최소로 두기 위해서다.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .anthropic import mask_request, restore_response
from .mask import Session

ANTHROPIC_API = "https://api.anthropic.com"

# 업스트림으로 넘기지 않는 헤더. 홉 단위 정보와 길이는 새로 계산된다.
_SKIP_HEADERS = {b"host", b"content-length", b"connection", b"accept-encoding"}


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            break
    return b"".join(chunks)


def _as_sse_events(message: dict) -> list[tuple[str, dict]]:
    """완성된 응답을 Anthropic SSE 이벤트 열로 펼친다.

    조각난 스트림에서 placeholder를 복원하는 것은 신뢰하기 어렵다. placeholder가
    청크 경계에서 잘리거나 모델이 형태를 바꾸면 복원이 깨진다. 그래서 업스트림에는
    통짜로 요청해 복원을 끝낸 뒤, 클라이언트에게만 스트리밍처럼 보이게 다시 흘린다.
    """
    blocks = message.get("content", [])
    events: list[tuple[str, dict]] = [
        (
            "message_start",
            {"type": "message_start", "message": {**message, "content": []}},
        )
    ]

    for index, block in enumerate(blocks):
        events.append(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {**block, "text": ""} if block.get("type") == "text" else block,
                },
            )
        )
        if block.get("type") == "text":
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": block.get("text", "")},
                    },
                )
            )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))

    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": message.get("stop_reason"),
                    "stop_sequence": message.get("stop_sequence"),
                },
                "usage": message.get("usage", {}),
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return events


async def _send_sse(send, message: dict) -> None:
    payload = b"".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
        for name, data in _as_sse_events(message)
    )
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"cache-control", b"no-cache"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _send_json(send, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_app(upstream_base_url: str = ANTHROPIC_API, client: httpx.AsyncClient | None = None):
    """게이트웨이 ASGI 앱을 만든다.

    client 를 주입하면 테스트에서 업스트림을 에코 서버로 바꿔 끼울 수 있다.
    """
    session = Session()
    owns_client = client is None

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return

        if scope["path"] != "/v1/messages" or scope["method"] != "POST":
            await _send_json(
                send,
                404,
                {"type": "error", "error": {"type": "not_found", "message": "지원하지 않는 경로"}},
            )
            return

        raw = await _read_body(receive)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            await _send_json(
                send,
                400,
                {"type": "error", "error": {"type": "invalid_request", "message": "JSON 파싱 실패"}},
            )
            return

        wants_stream = bool(body.get("stream"))
        masked = mask_request(body, session)
        # 업스트림에는 통짜로 요청한다. 복원을 끝낸 뒤 클라이언트에게만 스트리밍으로 보인다.
        masked.pop("stream", None)

        headers = {
            key.decode(): value.decode()
            for key, value in scope.get("headers", [])
            if key.lower() not in _SKIP_HEADERS
        }

        http = client or httpx.AsyncClient(timeout=600.0)
        try:
            upstream = await http.post(
                f"{upstream_base_url}/v1/messages", json=masked, headers=headers
            )
        except httpx.HTTPError as exc:
            # 게이트웨이 자체 오류임을 헤더로 구분해 알린다.
            await _send_json(
                send,
                502,
                {
                    "type": "error",
                    "error": {"type": "upstream_error", "message": f"업스트림 연결 실패: {exc}"},
                },
            )
            return
        finally:
            if owns_client:
                await http.aclose()

        try:
            payload = upstream.json()
        except ValueError:
            # JSON이 아니면 손대지 않고 그대로 흘려보낸다.
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": [(b"content-type", b"application/octet-stream")],
                }
            )
            await send({"type": "http.response.body", "body": upstream.content})
            return

        restored = restore_response(payload, session)

        if wants_stream and upstream.status_code == 200:
            await _send_sse(send, restored)
            return

        await _send_json(send, upstream.status_code, restored)

    return app
