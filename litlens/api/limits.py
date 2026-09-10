"""Bound requests before multipart parsing spools them to disk."""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from litlens.config import settings


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = (
            settings.max_upload_bytes + 1024 * 1024
            if scope["path"] == "/api/v1/documents/upload"
            else 64 * 1024
        )
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = limit + 1
        if length > limit:
            return await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                scope, receive, send
            )
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                # Raised inside the route parser, handled by FastAPI's exception middleware.
                raise HTTPException(413, "Request body too large")
            return message

        await self.app(scope, limited_receive, send)
