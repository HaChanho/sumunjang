"""AI API 경계 게이트웨이.

들어온 요청을 마스킹해 업스트림에 넘기고, 돌아온 응답에서 원문을 복구한다.
웹 프레임워크 없이 순수 ASGI로 구현한다 — 개인정보가 지나가는 경로의
서드파티 코드를 최소로 두기 위해서다.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import sys
from urllib.parse import unquote
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .anthropic import count_masked, mask_request, restore_response
from .mask import Session, SessionFull, mask
from .openai import count_masked as openai_count_masked
from .openai import mask_request as openai_mask_request
from .openai import restore_response as openai_restore_response

ANTHROPIC_API = "https://api.anthropic.com"

# 업스트림으로 넘기지 않는 헤더. 홉 단위 정보와 길이는 새로 계산된다.
_SKIP_HEADERS = {b"host", b"content-length", b"connection", b"accept-encoding"}

# 업스트림 응답에서 클라이언트에게 그대로 넘겨야 하는 헤더.
# 전부 버렸더니 429 의 retry-after 까지 사라져 SDK 의 백오프가 근거 없는
# 지수 백오프로 떨어지고, request-id 가 없어 장애 신고 추적이 끊겼다.
# 프록시를 끼웠다는 이유로 재시도 품질과 추적성이 조용히 나빠지면 안 된다.
_FORWARD_RESPONSE_HEADERS = ("retry-after", "request-id", "x-request-id")
_FORWARD_HEADER_PREFIXES = ("anthropic-ratelimit-", "x-ratelimit-", "openai-")


def _forwarded_headers(upstream) -> list[tuple[bytes, bytes]]:
    return [
        (name.encode(), value.encode())
        for name, value in upstream.headers.items()
        if name.lower() in _FORWARD_RESPONSE_HEADERS
        or name.lower().startswith(_FORWARD_HEADER_PREFIXES)
    ]

# 경로마다 다르게 다룬다. 기본값은 통과가 아니라 차단이다 — 경계 게이트웨이가
# 모르는 경로를 흘려보내면 마스킹을 거치지 않은 본문이 그대로 나간다.
#
# 개인정보가 실릴 수 없는 읽기 전용 경로. 본문이 없고 경로에 식별자만 온다.
# 경로 구분자까지 봐야 한다 — startswith 만 쓰면 /v1/models_backup 같은 미지
# 경로가 통과해 "모르면 차단" 이 무너진다.
# 요청 본문 크기 상한. 개수 상한만으로는 값 하나가 거대하면 막지 못한다.
_MAX_BODY_BYTES = 32 * 1024 * 1024

_PASSTHROUGH_EXACT = "/v1/models"
_PASSTHROUGH_PREFIX = "/v1/models/"


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
        kind = block.get("type")

        # start 는 빈 껍데기여야 한다. 클라이언트는 start 의 내용을 무시하고 delta 만
        # 누적하므로, 여기에 내용을 담으면 그대로 유실된다. thinking 이 빈 채로 남아
        # 다음 턴 요청이 400 으로 거부되는 문제를 실제 왕복에서 만났다.
        if kind == "thinking":
            shell = {"type": "thinking", "thinking": "", "signature": ""}
        elif kind == "text":
            shell = {"type": "text", "text": ""}
        elif kind == "tool_use":
            shell = {**block, "input": {}}
        else:
            shell = block

        events.append(
            (
                "content_block_start",
                {"type": "content_block_start", "index": index, "content_block": shell},
            )
        )

        deltas: list[dict] = []
        if kind == "thinking":
            deltas.append({"type": "thinking_delta", "thinking": block.get("thinking", "")})
            deltas.append({"type": "signature_delta", "signature": block.get("signature", "")})
        elif kind == "text":
            deltas.append({"type": "text_delta", "text": block.get("text", "")})
        elif kind == "tool_use":
            deltas.append(
                {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False),
                }
            )

        for delta in deltas:
            events.append(
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": index, "delta": delta},
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


def _anthropic_sse(message: dict) -> bytes:
    return b"".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
        for name, data in _as_sse_events(message)
    )


def _openai_sse(message: dict) -> bytes:
    """완성된 chat.completion 을 chat.completion.chunk 열로 펼친다.

    Anthropic 과 형식이 다르다. event 줄이 없고 data 줄만 있으며, 마지막에
    `data: [DONE]` 이 온다. 청크마다 완성본이 아니라 **증분(delta)** 이 실린다.
    """
    본 = {
        key: message[key]
        for key in ("id", "model", "created", "system_fingerprint")
        if message.get(key) is not None
    }
    본["object"] = "chat.completion.chunk"

    chunks: list[dict] = []
    for choice in message.get("choices", []):
        index = choice.get("index", 0)
        answer = choice.get("message", {})

        def 조각(delta: dict, finish=None) -> dict:
            return {**본, "choices": [{"index": index, "delta": delta, "finish_reason": finish}]}

        chunks.append(조각({"role": answer.get("role", "assistant")}))
        if answer.get("content"):
            chunks.append(조각({"content": answer["content"]}))
        # 거절(refusal)을 빠뜨리면 거절 응답이 내용 없는 빈 스트림이 된다.
        # 사용자는 왜 답이 안 왔는지 알 수 없다.
        if answer.get("refusal"):
            chunks.append(조각({"refusal": answer["refusal"]}))
        for n, call in enumerate(answer.get("tool_calls") or []):
            chunks.append(조각({"tool_calls": [{"index": n, **call}]}))
        chunks.append(조각({}, choice.get("finish_reason")))

    # 사용량 청크. 통짜 응답에는 usage 가 있으므로 스트림에서도 흘려보낸다 —
    # 버리면 토큰 수를 세는 클라이언트가 비용을 알 수 없다.
    if message.get("usage"):
        chunks.append({**본, "choices": [], "usage": message["usage"]})

    payload = b"".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode() for chunk in chunks
    )
    return payload + b"data: [DONE]\n\n"


async def _send_sse(send, payload: bytes, extra_headers=()) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"cache-control", b"no-cache"),
                *extra_headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _send_json(send, status: int, payload: Any, extra_headers=()) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *extra_headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _passthrough(
    send, client, owns_client, upstream_base_url, path, headers, query: str = ""
) -> None:
    """마스킹 없이 그대로 넘긴다. 본문이 없는 읽기 전용 경로에만 쓴다.

    개인정보가 실릴 수 없는 경로여야 한다. 판단이 서지 않는 경로는 여기가 아니라
    404 로 보낸다 — 게이트웨이의 기본값은 통과가 아니라 차단이다.
    """
    http = client or httpx.AsyncClient(timeout=600.0)
    try:
        # 쿼리를 버리면 /v1/models?limit=100 의 페이지네이션이 사라진다.
        target = f"{upstream_base_url}{path}" + (f"?{query}" if query else "")
        upstream = await http.get(target, headers=headers)
    except httpx.HTTPError as exc:
        await _send_json(
            send,
            502,
            {"type": "error", "error": {"type": "upstream_error", "message": f"업스트림 연결 실패: {exc}"}},
        )
        return
    finally:
        if owns_client:
            await http.aclose()

    await send(
        {
            "type": "http.response.start",
            "status": upstream.status_code,
            "headers": [
                (b"content-type", upstream.headers.get("content-type", "application/json").encode()),
                *_forwarded_headers(upstream),
            ],
        }
    )
    await send({"type": "http.response.body", "body": upstream.content})


@dataclass(frozen=True)
class _Protocol:
    """한 프로토콜의 본문 변환과 SSE 형식.

    마스킹·복원 계층 자체는 프로토콜과 무관하다. 프로토콜마다 다른 것은
    "본문의 어느 자리에 사람이 쓴 텍스트가 있는가" 와 스트리밍 형식뿐이다.
    새 프로토콜을 붙이는 일이 이 표에 줄 하나를 더하는 일이 되도록 둔다.
    """

    mask: Callable[[dict, Session], dict]
    restore: Callable[[dict, Session], dict]
    count: Callable[[dict], list[str]]
    sse: Callable[[dict], bytes] | None


_ANTHROPIC = _Protocol(mask_request, restore_response, count_masked, _anthropic_sse)
_OPENAI = _Protocol(
    openai_mask_request, openai_restore_response, openai_count_masked, _openai_sse
)

# 마스킹해서 넘기는 경로. 기본값은 통과가 아니라 차단이므로, 여기 없는 경로는
# 업스트림에 닿지 않는다.
#
# count_tokens 는 messages 와 본문 모양이 같아 같은 변환을 쓴다. 막아 두면 도구가
# 문맥 길이를 재지 못하고, 그냥 통과시키면 원문이 그대로 나간다. 마스킹한 본문의
# 토큰 수가 실제로 나가는 본문의 토큰 수이므로 오히려 정확하다.
_PROTOCOLS = {
    "/v1/messages": _ANTHROPIC,
    "/v1/messages/count_tokens": _ANTHROPIC,
    "/v1/chat/completions": _OPENAI,
}


def create_app(
    upstream_base_url: str = ANTHROPIC_API,
    client: httpx.AsyncClient | None = None,
    on_request=None,
):
    """게이트웨이 ASGI 앱을 만든다.

    client 를 주입하면 테스트에서 업스트림을 에코 서버로 바꿔 끼울 수 있다.
    on_request 는 요청마다 무엇을 가렸는지 받아보는 콜백이다.
    """
    # 세션을 인증 자격 단위로 나눈다.
    #
    # 하나를 공유했더니 다른 대화의 개인정보가 주입됐다. placeholder 이름이
    # [주민등록번호_1] 처럼 1부터 세는 예측 가능한 값이라, 무관한 응답에 그
    # 문자열이 들어 있기만 하면 남의 원문으로 복원됐다. 모델이 읽은 웹페이지나
    # 문서에 그 문자열을 심어 두면 되므로 인젝션 경로이기도 하다.
    #
    # 같은 자격의 여러 대화는 여전히 한 세션을 쓴다. 그것은 의도다 — 같은 값에
    # 같은 이름을 주어야 모델이 문맥을 잃지 않는다.
    sessions: dict[str, Session] = {}
    owns_client = client is None

    # 세션 사전 자체에도 상한을 둔다. 자격증명마다 세션이 하나씩 생기므로
    # 상한이 없으면 서로 다른 키로 두드리는 것만으로 메모리를 밀어낼 수 있다.
    MAX_SESSIONS = 64

    def session_for(headers: dict) -> Session:
        credential = headers.get("x-api-key") or headers.get("authorization") or ""
        # 자격증명 자체를 키로 쓰지 않는다. 해시만 남기면 메모리 덤프에
        # API 키가 남지 않는다.
        key = hashlib.sha256(credential.encode()).hexdigest()
        if key not in sessions and len(sessions) >= MAX_SESSIONS:
            raise SessionFull(
                f"세션 수 상한 {MAX_SESSIONS}개에 도달했습니다. 프록시를 다시 띄워 주세요."
            )
        return sessions.setdefault(key, Session())

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return

        path, method = scope["path"], scope["method"]
        headers = {
            key.decode(): value.decode()
            for key, value in scope.get("headers", [])
            if key.lower() not in _SKIP_HEADERS
        }

        # 점 구간을 먼저 접는다. /v1/models/../../v1/organizations/me 는 접두사
        # 검사를 통과하지만 http 클라이언트가 정규화해 전혀 다른 엔드포인트를
        # 사용자 키로 호출한다. "모르면 차단" 이 경로 문법으로 뚫리면 안 된다.
        normalized = posixpath.normpath(path)
        if method == "GET" and (
            normalized == _PASSTHROUGH_EXACT or normalized.startswith(_PASSTHROUGH_PREFIX)
        ):
            # 본문이 없다는 것이 경로·쿼리에 개인정보가 없다는 뜻은 아니다.
            # /v1/models/900101-1234568?email=... 은 그대로 나갈 수 있다.
            try:
                passthrough_session = session_for(headers)
            except SessionFull as exc:
                # POST 는 429 를 내는데 GET 만 예외가 앱 밖으로 터지면 안 된다.
                await _send_json(
                    send,
                    429,
                    {"type": "error", "error": {"type": "session_full", "message": str(exc)}},
                )
                return
            # 퍼센트 인코딩을 먼저 푼다. /v1/models?note=900101%2D1234568 은
            # 그대로 보면 개인정보로 보이지 않지만 업스트림은 원문으로 읽는다.
            # 푼 뒤 가려야 할 것이 나오면 요청 자체를 버린다 — 다시 인코딩해
            # 보내는 것보다 거부가 안전하고, 모델 목록 조회에 개인정보가
            # 실릴 이유가 없다.
            raw_query = scope.get("query_string", b"").decode()
            풀린것 = unquote(unquote(normalized + "?" + raw_query))
            if mask(풀린것, passthrough_session) != 풀린것:
                await _send_json(
                    send,
                    400,
                    {
                        "type": "error",
                        "error": {
                            "type": "pii_in_path",
                            "message": "경로나 쿼리에 개인정보가 있어 요청을 보내지 않았습니다",
                        },
                    },
                )
                return
            query = raw_query
            await _passthrough(
                send,
                client,
                owns_client,
                upstream_base_url,
                normalized,
                headers,
                query,
            )
            return

        protocol = _PROTOCOLS.get(path)
        if protocol is None or method != "POST":
            await _send_json(
                send,
                404,
                {"type": "error", "error": {"type": "not_found", "message": "지원하지 않는 경로"}},
            )
            return

        raw = await _read_body(receive)
        if len(raw) > _MAX_BODY_BYTES:
            await _send_json(
                send,
                413,
                {"type": "error", "error": {"type": "too_large", "message": "본문이 너무 큽니다"}},
            )
            return

        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            await _send_json(
                send,
                400,
                {"type": "error", "error": {"type": "invalid_request", "message": "JSON 파싱 실패"}},
            )
            return

        try:
            # dict 가 아닌 본문(배열·문자열·null)도 여기서 걸린다. 밖에 두면
            # AttributeError 가 ASGI 핸들러 밖으로 터져 설계한 응답 대신
            # 서버의 맨 500 이 나간다.
            session = session_for(headers)
            before = len(session)
            wants_stream = bool(body["stream"]) if "stream" in body else False
            masked = protocol.mask(body, session)
            # 업스트림에는 통짜로 요청한다. 복원을 끝낸 뒤 클라이언트에게만
            # 스트리밍으로 보인다. stream_options 도 함께 뗀다 — OpenAI 는
            # stream 이 참일 때만 이 필드를 허용하므로 남겨 두면 400 이 된다.
            #
            # 이 두 줄이 try 밖에 있던 동안, 사전이 아닌 본문(배열·문자열)에서
            # pop 이 터져 설계한 500 대신 서버의 맨 500 이 나갔다. 주석은
            # "여기서 걸린다" 고 적혀 있었지만 실제로는 한 줄 아래에서 터졌다.
            masked.pop("stream", None)
            masked.pop("stream_options", None)
        except SessionFull as exc:
            print(f"[수문장] {exc}", file=sys.stderr, flush=True)
            await _send_json(
                send,
                429,
                {"type": "error", "error": {"type": "session_full", "message": str(exc)}},
            )
            return
        except Exception as exc:  # noqa: BLE001 — 무엇이 터지든 나가면 안 된다
            # 보안 도구의 실패 모드는 통과가 아니라 차단이다. 마스킹이 깨졌는데
            # 요청을 그대로 흘려보내면 도구가 있는 편이 없는 편보다 위험해진다.
            # 원문이 담길 수 있으므로 예외 메시지는 응답에 싣지 않는다.
            print(
                f"[수문장] 마스킹 실패로 요청을 버렸습니다: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            await _send_json(
                send,
                500,
                {
                    "type": "error",
                    "error": {"type": "masking_failed", "message": "마스킹에 실패해 요청을 보내지 않았습니다"},
                },
            )
            return


        if on_request is not None:
            # 이번 요청에서 실제로 가려진 자리를 센다. 세션에 이미 등록된 값이라고
            # 0건으로 보고하면 작동을 멈춘 것처럼 보인다 — 실제 왕복에서 겪은 문제다.
            masked_here = protocol.count(masked)
            on_request(
                {
                    "masked_count": len(masked_here),
                    "categories": masked_here,
                    "new_count": len(session) - before,
                    # 이미 가려진 본문이다. 기록이 새로운 유출 경로가 되어서는 안 된다.
                    "upstream_body": masked,
                }
            )

        http = client or httpx.AsyncClient(timeout=600.0)
        try:
            upstream = await http.post(
                f"{upstream_base_url}{path}", json=masked, headers=headers
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
                    "headers": [
                        (
                            b"content-type",
                            upstream.headers.get("content-type", "application/octet-stream").encode(),
                        ),
                        *_forwarded_headers(upstream),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": upstream.content})
            return

        if not isinstance(payload, dict):
            # 사전이 아닌 JSON(배열·문자열·숫자·null)은 우리가 다룰 모양이 아니다.
            # 요청 경로에만 fail-closed 를 걸어 두고 응답 경로를 비워 뒀더니,
            # 앞단에 게이트웨이가 끼어 그런 본문이 오면 예외가 ASGI 밖으로
            # 터져 연결이 끊겼다. 손대지 않고 그대로 흘려보낸다.
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": [
                        (b"content-type", upstream.headers.get("content-type", "application/json").encode()),
                        *_forwarded_headers(upstream),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": upstream.content})
            return

        # 업스트림이 내려보낸 추론 서명을 기억해 둔다. 다음 턴에 클라이언트가
        # 그 블록을 되돌려보낼 때, 그것이 진짜 추론인지 판정하는 유일한 근거다.
        for block in payload.get("content", []):
            if isinstance(block, dict):
                서명 = block.get("signature")
                본문 = block.get("thinking") or block.get("data")
                if isinstance(서명, str) and isinstance(본문, str):
                    session.remember_thinking(서명, 본문)

        restored = protocol.restore(payload, session)

        if wants_stream and upstream.status_code == 200 and protocol.sse is not None:
            # 스트리밍이 도구의 기본 경로다. 여기만 헤더를 빠뜨리면 정작 가장
            # 많이 쓰는 길에서 retry-after 와 request-id 가 사라진다.
            await _send_sse(send, protocol.sse(restored), _forwarded_headers(upstream))
            return

        await _send_json(
            send, upstream.status_code, restored, _forwarded_headers(upstream)
        )

    return app
