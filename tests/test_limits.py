import asyncio

from litlens.api.limits import BodyLimit


def test_rejects_large_declared_request_before_reading():
    async def run():
        messages = []

        async def downstream(*args):
            raise AssertionError("Must not parse oversized body")

        async def receive():
            raise AssertionError("Must not read oversized body")

        async def send(message):
            messages.append(message)

        await BodyLimit(downstream)(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/search",
                "headers": [(b"content-length", b"1000000")],
            },
            receive,
            send,
        )
        assert messages[0]["status"] == 413

    asyncio.run(run())
