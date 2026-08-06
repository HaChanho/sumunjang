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

        masked = mask_request(body, session)

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

        await _send_json(send, upstream.status_code, restore_response(payload, session))

    return app
