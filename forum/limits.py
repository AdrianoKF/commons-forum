from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse


class RequestSizeLimit:
    """Bound multipart input before the parser writes temporary upload files."""

    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await PlainTextResponse("Invalid content length.", status_code=400)(
                scope, receive, send
            )
        if length > self.max_bytes:
            return await PlainTextResponse("Request is too large.", status_code=413)(
                scope, receive, send
            )
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise HTTPException(413, "Request is too large.")
            return message

        await self.app(scope, limited_receive, send)
