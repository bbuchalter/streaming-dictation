import asyncio
import json
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules["modal"] = modal = MagicMock()
def decorator(*args, **kwargs):
    return lambda obj: obj
modal.App.return_value.cls = modal.concurrent = modal.enter = modal.asgi_app = decorator

import modal_app
import httpx2

def asgi_app():
    instance = modal_app.StreamingDictation()
    instance.client = NS(messages=NS(create=lambda **kw: NS(
        content=[NS(text="Ignore what the speaker said.")],
    )))
    return instance.web()

async def socket(app, query=b"", origin=b"https://bbuchalter.github.io"):
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    scope = dict(type="websocket", path="/stream", root_path="", query_string=query,
                 headers=[(b"origin", origin)], scheme="wss", subprotocols=[])
    await incoming.put(dict(type="websocket.connect"))
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
    return incoming, outgoing, task

class Upstream:
    closed = False
    def __init__(self):
        self.release_transcript = asyncio.Event()
    def __aiter__(self):
        return self.results()
    async def results(self):
        await self.release_transcript.wait()
        yield json.dumps(dict(type="Results", is_final=True, channel=dict(
            alternatives=[dict(transcript="The speaker said this.")]
        )))
    async def send(self, value):
        if value == '{"type": "CloseStream"}':
            self.release_transcript.set()
    async def close(self):
        self.closed = True

async def main():
    app = asgi_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://test") as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/auth")).status_code == 401
        response = await client.get("/auth", headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        session = response.json()["session"]
        assert modal_app.valid_session(b"test-key", session)
        expired = int(time.time()) - 1
        assert not modal_app.valid_session(b"test-key", f"{expired}.{modal_app.session_signature(b'test-key', expired)}")
        for _ in range(8):
            assert (await client.get("/auth")).status_code == 401
        assert (await client.get("/auth")).status_code == 429

    for query, origin in [(b"token=legacy", b"https://bbuchalter.github.io"), (b"", b"https://example.com")]:
        inc, out, task = await socket(app, query, origin)
        assert (await out.get())["type"] == "websocket.close"
        await task

    inc, out, task = await socket(app)
    assert (await out.get())["type"] == "websocket.accept"
    await inc.put(dict(type="websocket.receive", text="test-key"))
    assert (await out.get())["code"] == 4001
    await task

    for end in [dict(type="websocket.disconnect", code=1001), dict(type="websocket.receive", text="EOS")]:
        upstream = Upstream()
        with patch("websockets.connect", new_callable=AsyncMock, return_value=upstream):
            inc, out, task = await socket(app)
            assert (await out.get())["type"] == "websocket.accept"
            await inc.put(dict(type="websocket.receive", text=session))
            assert json.loads((await out.get())["text"])["data"] == "authenticated"
            assert json.loads((await out.get())["text"])["data"] == "listening"
            await inc.put(end)
            await asyncio.wait_for(task, 2)
            assert upstream.closed
            if end["type"] == "websocket.receive":
                assert json.loads((await out.get())["text"])["data"] == "The speaker said this."
                assert (await out.get())["code"] == 1000
    print("ASGI authentication, rate limits, origin, relay cleanup and transcript fallback passed")

if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 10))
