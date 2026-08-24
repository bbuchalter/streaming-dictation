# Auth Protocol Redesign: Cheap Login, Honest Errors

## Overview

Split authentication away from the transcription stream. Today the login gate authenticates by
opening a `/stream` WebSocket and waiting for the server to reach `listening` — which happens only
after Deepgram connects. That makes every page load open a billable Deepgram streaming session, and
it makes any backend failure indistinguishable from a wrong password.

This design adds a cheap `GET /auth` endpoint for the login gate, moves the bearer token out of the
WebSocket URL and into the first frame of the connection, and gives the server distinct close codes
so the client can tell "wrong password" from "upstream is broken."

Scope is Group B of the twelve findings from the 2026-08-24 investigation: findings 1, 2, 7, 8, 9.
Groups A, C, and D are explicitly out of scope (see the last section).

## Background: the incident this comes from

On 2026-08-24 the app could not be logged into. The password was correct and the Modal secret was
correct. The Deepgram API key had been revoked, so the server failed at

```
Deepgram connection failed: InvalidStatus: server rejected WebSocket connection: HTTP 401
```

and closed the socket with code `1000`. The client's `verifyToken()` handled only close code `4001`,
so the promise stayed pending until its 10-second timer fired and resolved `false`, rendering
"Invalid password." The server had already sent the real diagnosis as a JSON `error` frame; the login
gate discarded it.

Two lessons drive this design:

1. **WebSocket close codes are an impoverished error vocabulary.** HTTP status codes are not. Move
   the login check to HTTP and the entire class of bug disappears rather than getting patched.
2. **Login must not depend on the transcription backend.** Authentication and speech-to-text are
   separate concerns that were accidentally coupled.

## Threat model

Cost control: keep strangers off the Deepgram and Anthropic bill. A single shared secret is
appropriate. Constant-time comparison because it is nearly free. No rate limiting and no session
issuance — there is no requirement that justifies them yet.

CORS is browser cooperation, not access control. The bearer token is the access control. A `curl`
request from any origin will still be served if it carries the right token; that is expected and
acceptable under this threat model.

## Architecture

```
LOGIN (new — no WebSocket, no Deepgram, no cost)

Browser                               Modal
┌──────────────────┐          ┌────────────────────────┐
│ password field   │          │                        │
│      │           │          │  CORSMiddleware        │
│      ▼           │ OPTIONS  │        │               │
│  checkAuth() ────┼─preflight┼───▶    ▼               │
│                  │ (cached  │  GET /auth             │
│                  │  600s)   │   compare_digest       │
│                  │          │        │               │
│                  │◀─200/401─┼────────┘               │
│  show app / show │          │                        │
│  the real reason │          │                        │
└──────────────────┘          └────────────────────────┘


RECORDING (token moves out of the URL into the first frame)

Browser                               Modal                      External
┌──────────────────┐          ┌────────────────────────┐
│ WS /stream       │          │  accept()              │
│  (no token in    ├──────────▶                        │
│   the URL)       │          │                        │
│  send "<token>" ─┼──text────▶  recv, 10s timeout     │
│                  │  frame   │  compare_digest        │
│                  │          │        │               │
│                  │◀─status:─┼─ authenticated         │
│                  │          │        │               │
│                  │          │        ▼               │
│                  │          │  Deepgram WS ──────────┼──▶ Deepgram
│                  │◀─status:─┼─ listening             │
│  flush audio ────┼──binary──▶  forward_audio         │
│  buffer, stream  │  frames  │  process_transcripts   │
│                  │◀─text:───┼─ polished text         │
└──────────────────┘          └────────────────────────┘
```

## Modal Endpoint

### Fail-fast secret validation

Read and validate `BEARER_TOKEN` at the top of `web()`, before any route is defined:

```python
expected_token = os.environ["BEARER_TOKEN"]
if not expected_token.strip():
    raise RuntimeError("BEARER_TOKEN is set but empty")
```

`web()` runs once per container when the ASGI app is constructed, so a missing or empty secret
becomes a container crash loop visible in Modal's logs instead of a `KeyError` raised per-request
after `accept()` has already succeeded.

This lives in `web()` rather than `@modal.enter()` for two reasons: `web()` is where the closure over
`expected_token` is needed, and it avoids depending on the ordering between `@modal.enter()` and the
`@modal.asgi_app()` constructor.

### CORS

```python
from fastapi.middleware.cors import CORSMiddleware

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://bbuchalter.github.io"],  # scheme+host only — never a path
    allow_methods=["GET"],
    allow_headers=["Authorization"],
    allow_credentials=False,
    max_age=600,
)
```

Every argument is load-bearing against Starlette 1.6.0 defaults (`allow_origins=()`,
`allow_methods=('GET',)`, `allow_headers=()`, `allow_credentials=False`):

- `allow_origins` — an origin is scheme plus host. A path suffix silently matches nothing.
- `allow_headers` — the CORS safelist is `{Accept, Accept-Language, Content-Language, Content-Type}`.
  `Authorization` is not in it, so omitting this breaks the preflight. This is the cost of keeping
  the token out of the URL.
- `allow_credentials=False` — authentication uses a header, not a cookie.

No image change is required: `fastapi` is already installed (`modal_app.py:9`) and
`fastapi.middleware.cors.CORSMiddleware` re-exports Starlette's.

Verified against a deployed throwaway Modal app: Modal's proxy passes `OPTIONS` through to the ASGI
app with no Modal-specific configuration, and — critically — Starlette attaches
`Access-Control-Allow-Origin` to the `401` response as well as the `200`. Without that, the browser
would block JS from reading the status and a wrong password would surface as
`TypeError: Failed to fetch`, reproducing the original masking bug in a new form.

### Endpoint: `GET /auth`

```python
@web_app.get("/auth")
def auth(authorization: str = Header(default="")):
    scheme, _, tok = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(tok, expected_token):
        raise HTTPException(status_code=401, detail="invalid token")
    return {"ok": True}
```

No Deepgram connection, no Anthropic client use, no WebSocket. The only cost is a container wake.

### WebSocket `/stream` handshake

The `token` query parameter is removed. The first text frame carries the token.

```python
await ws.accept()
try:
    first = await asyncio.wait_for(ws.receive(), timeout=10)
except asyncio.TimeoutError:
    await ws.close(code=4002, reason="Auth timeout")
    return

tok = first.get("text") if isinstance(first, dict) else None
if tok is None:
    await ws.close(code=4002, reason="Expected token as first text frame")
    return
if not hmac.compare_digest(tok, expected_token):
    await ws.close(code=4001, reason="Unauthorized")
    return

await ws.send_json({"type": "status", "data": "authenticated"})
```

The 10-second auth timeout is new and necessary. Without it a client that connects and sends nothing
pins a container for the full `timeout=7200` configured at `modal_app.py:139`.

Deepgram connection follows, unchanged in substance, except that failure now closes with `4003` after
sending the existing JSON `error` frame.

### Close codes

| Code | Meaning | Retry? |
|------|---------|--------|
| `4001` | Token rejected | No — terminal |
| `4002` | No token sent within 10s, or first frame was not text | No — terminal |
| `4003` | Upstream (Deepgram) permanently failed | Should not — but see the note below |
| `1000` | Normal close (client sent `EOS`) | N/A |

`4003` is a deliberate seam for Group A finding 6. A revoked Deepgram key is not fixed by retrying.

**Interim behavior, stated plainly:** this design adds `4003` on the server but does not teach the
client to honor it. `ws.onclose` at `index.html:521-527` returns early only for `4001`, so a `4003`
close still falls through to `attemptReconnect()` and retries forever with an 8-second backoff. What
the operator *does* get immediately is the truth on screen, because the server sends its JSON `error`
frame before closing and the recording path already renders it via `setStatus` at
`index.html:513-515`. So after this change a revoked Deepgram key shows the real Deepgram error and
then retries pointlessly; Group A finding 6 stops the pointless retrying.

## Browser Client

### Login

`verifyToken()` is deleted, not patched. Its replacement never opens a WebSocket:

```javascript
const MODAL_HTTP_URL = MODAL_WS_URL.replace(/^wss:/, 'https:');  // single source of truth
const AUTH_TIMEOUT_MS = 20000;

async function checkAuth(token) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), AUTH_TIMEOUT_MS);
  try {
    const res = await fetch(MODAL_HTTP_URL + '/auth', {
      headers: { Authorization: 'Bearer ' + token },
      signal: ctrl.signal,
    });
    if (res.status === 200) return { ok: true };
    if (res.status === 401) return { ok: false, reason: 'Invalid password' };
    return { ok: false, reason: `Server error (HTTP ${res.status})` };
  } catch (e) {
    return { ok: false, reason: e.name === 'AbortError'
      ? 'Timed out waking the server — try again'
      : 'Network error: ' + e.message };
  } finally {
    clearTimeout(timer);
  }
}
```

Deriving `MODAL_HTTP_URL` from `MODAL_WS_URL` keeps one endpoint constant in the file rather than two
that can drift.

`AUTH_TIMEOUT_MS` is 20 seconds, replacing the 10 seconds at `index.html:324`. Measured cold start to
`listening` was 3.9–5.0 seconds; `/auth` is cheaper because it skips Deepgram, but a preflight plus
container wake needs headroom. `scaledown_window=60` means most real logins are cold.

Both call sites move to `checkAuth`: the submit handler (`index.html:284`) and the `sessionStorage`
re-verification on page load (`index.html:339`).

While the request is in flight, the button shows "Waking server…" after roughly 2 seconds so a cold
start does not read as a hang.

### Recording handshake

`onopen` currently flushes `audioBuffer` immediately (`index.html:489-500`). New behavior: `onopen`
sends the token and flushes nothing.

**The flush moves to the `listening` message, not `authenticated`.** At `authenticated` the server has
not yet entered `forward_audio()` and is therefore not calling `ws.receive()`; audio sent then would
sit in the socket buffer and arrive late. `listening` means Deepgram is connected and the read loop is
running. `AUDIO_BUFFER_MAX = 40` (about 10 seconds) already bounds what a slow handshake can cost.

This is the only change in this design that touches working, talk-critical code. It matters most on
mid-talk reconnects, where `attemptReconnect()` re-enters `connectWebSocket()` with a populated
buffer.

### Error surfacing

| Failure | Signal | What the user sees |
|---------|--------|--------------------|
| Wrong password at login | `401` | "Invalid password" |
| Server cold | slow `200` | "Waking server…" then success |
| Modal unavailable | `5xx` | "Server error (HTTP 503)" |
| CORS misconfigured | `fetch` throws | "Network error: …" |
| Login timeout | `AbortError` | "Timed out waking the server — try again" |
| Wrong token on `/stream` | close `4001` | terminal, no reconnect |
| Client never sends token | close `4002` | terminal |
| Deepgram key revoked | `error` frame + close `4003` | the actual Deepgram error text |

The last row is the incident that motivated this work. It would have read
`Deepgram connection failed: HTTP 401` instead of "Invalid password."

## Testing

This repository has no test infrastructure. This design adds `check_login.py`, a committed
integration smoke test run against the deployed endpoint:

| Check | Expected |
|-------|----------|
| `GET /auth` with valid token | `200` |
| `GET /auth` with wrong token | `401` |
| `GET /auth` with no `Authorization` header | `401` |
| `OPTIONS /auth` preflight from the Pages origin | `200` with `Access-Control-Allow-Origin` |
| `/stream` with valid token as first frame | reaches `status: listening` |
| `/stream` with wrong token | close `4001` |
| `/stream` with no first frame | close `4002` within ~10s |

The `/auth` checks fail with `404` against the server as it stands today, so this is genuinely
test-first: write the checks, watch them fail, implement, watch them pass.

The script reads the token from `MODAL_BEARER_TOKEN` in `.env` (gitignored) and depends only on
`websockets` plus stdlib `urllib`. It never prints the token.

## What Does NOT Change

- Deepgram model, parameters, and audio format. `build_deepgram_url()` is untouched.
- The Claude Haiku polish pipeline, `SYSTEM_PROMPT`, and the 50-word rolling context.
- `MediaRecorder` capture, Opus bitrate, chunk cadence, and `AUDIO_BUFFER_MAX`.
- The caption display, font sizing, fullscreen, export, and `localStorage` transcript persistence.
- The heartbeat and reconnection *mechanism* (Group A revises its behavior, not this design).
- The endpoint hostname, so `MODAL_WS_URL` needs no edit.
- `sessionStorage` as the place the token is cached after a successful login.

## Deployment & Operations

The client (GitHub Pages, push-to-deploy) and the server (`modal deploy`) ship independently, and
this changes the protocol on both sides. There is therefore a window in which they disagree.

Order:

1. `modal deploy modal_app.py`
2. `python3 check_login.py` — validates the new server before the client goes out
3. Commit and push `index.html`
4. Hard-refresh the browser tab

The live page is broken between steps 1 and 3, roughly a minute. **Do not deploy during a talk.**

This is safe from stale clients because the service worker currently caches nothing: `ASSETS` in
`service-worker.js:2` lists root-absolute paths that 404 under `/streaming-dictation/`, so `addAll`
rejects and installation fails. That is finding 10, fixed in Group C — after which this deployment
order will need revisiting, because a working cache changes the stale-client analysis.

Dual-protocol support on the server (accept the token from either the query parameter or the first
frame) was considered and rejected under YAGNI. It would eliminate the window at the cost of about
three lines plus a follow-up removal commit. Deploy timing is under the operator's control, and the
operator is the only user.

The toolbar version label (`v2026.04.19a`, commit 339acd4) gets bumped so a glance at a tab reveals
which client it is running.

## Cost

Removes one billable Deepgram streaming session per page load and per login attempt. Previously every
visit to the page opened a real Deepgram connection — including failed logins and the automatic
`sessionStorage` re-verification on load. After this change, Deepgram is contacted only when
recording actually starts.

Adds one CORS preflight per login, cached by the browser for 600 seconds. Modal container wake cost
is unchanged.

## Findings Addressed

| # | Finding | How |
|---|---------|-----|
| 1 | `verifyToken` reports every non-4001 failure as "Invalid password" | Deleted; HTTP status codes replace close-code inference |
| 2 | 10-second login timeout is shorter than a cold start | 20s, plus a "Waking server…" indicator |
| 7 | Bearer token travels in the URL query string | Moved to the first WebSocket frame and to an `Authorization` header |
| 8 | `os.environ["BEARER_TOKEN"]` read inside the request handler | Read and validated at container construction |
| 9 | Login check opens a billable Deepgram session | `GET /auth` touches no upstream service |

## Out of Scope

Deferred to their own sub-projects, in this order:

- **Group A — diagnosability** (findings 3, 4, 5, 6): mid-session `4001` kills the session silently;
  the heartbeat sends a `PING` the server never reads, so it cannot detect a black-holed connection;
  five blanket `except Exception: pass` handlers on the server; unlimited reconnection masks
  permanent faults. Group A consumes the `4003` close code introduced here.
- **Group C — service worker** (findings 10, 11): root-absolute `ASSETS` paths break installation on
  GitHub Pages, and cache-first with no invalidation would strand users on a stale `index.html` once
  installation works. These must ship together.
- **Group D — housekeeping** (finding 12): `.env` carries dead `MODAL_POLISH_URL` and
  `REVAI_ACCESS_TOKEN` entries from the pre-Deepgram architecture, plus a `MODAL_BEARER_TOKEN` that no
  committed code reads.
