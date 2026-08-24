# Auth Protocol Redesign: Cheap Login, Honest Errors

## Overview

Split authentication away from the transcription stream. Today the login gate authenticates by
opening a `/stream` WebSocket and waiting for the server to reach `listening` — which happens only
after Deepgram connects. That makes every page load open a billable Deepgram streaming session, and
it makes any backend failure indistinguishable from a wrong password.

This design adds a cheap `GET /auth` endpoint for the login gate, moves the bearer token out of the
WebSocket URL and into the first frame of the connection, and gives the server distinct close codes
so the client can tell "wrong password" from "upstream is broken."

Because GitHub Pages caches `index.html` for 10 minutes and an already-open tab never re-fetches it
at all, the server accepts both the old and new handshakes for a transition period. Deploying the
server cannot break a client that has not reloaded.

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


RECORDING (new clients send the token as the first frame; legacy query param still accepted)

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

# LEGACY PATH: token in the query parameter, for clients cached before this change.
# See "Backward compatibility" below. Remove only once no stale client can remain.
legacy = ws.query_params.get("token")
if legacy is not None:
    if not hmac.compare_digest(legacy, expected_token):
        await ws.close(code=4001, reason="Unauthorized")
        return
else:
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
| `/stream?token=<valid>` with no first frame (legacy client) | reaches `status: listening` |
| `/stream?token=<wrong>` (legacy client) | close `4001` |

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

### Client cache behavior (measured 2026-08-24)

GitHub Pages serves `index.html` with `cache-control: max-age=600`, and Pages provides no way to set
custom headers. That fixed 10-minute window governs how fast a client can pick up a new frontend:

| Client state | Picks up the new `index.html`? |
|--------------|-------------------------------|
| First visit, or HTTP cache expired | Yes, immediately |
| Reload within 10 minutes of a prior load | No — served from the HTTP cache, up to 10 minutes stale |
| Tab already open | **Never**, unbounded, until something triggers a reload |

The last row is the operationally significant one. This is a captioning app left open on a screen for
the length of a talk, so a long-lived tab running old JavaScript is the normal case rather than an
edge case. The operator cannot be relied on to hard-refresh every client.

The service worker is *not* a factor. All five entries in `ASSETS` (`service-worker.js:2`) are
root-absolute and resolve against the Pages domain root rather than `/streaming-dictation/`; all five
return 404, and no root Pages site exists. `cache.addAll()` therefore rejects, `install` fails, the
worker never activates, and its `fetch` handler never intercepts a request. This changes once Group C
fixes finding 10, at which point this section must be revisited — a working cache reintroduces
staleness that only a network-first navigation strategy can bound.

### Backward compatibility

Because a stale client can persist indefinitely, the server accepts the bearer token from **either**
the legacy `token` query parameter or the new first text frame. This is a requirement, not an
optimization: without it, an old client against the new server reproduces the exact bug this design
exists to remove.

Failure mode if dual support were omitted: the old client connects with `?token=…` and then waits for
messages. The new server waits 10 seconds for a first frame, receives nothing, and closes `4002`. The
old client's `onclose` (`index.html:521-527`) returns early only for `4001`, so it falls through to
its own 10-second timer and renders "Invalid password." An open tab that is mid-recording fares worse:
`4002` is not `4001` and `isRecording` is true, so `attemptReconnect()` loops forever at 8-second
intervals, silently, during a talk.

Dual support is transparent to old clients:

- The new `authenticated` status falls through both branches of the old client's `status` switch as a
  no-op, because it matches neither `listening` nor `disconnected`.
- `verifyToken()` still receives the `listening` message it waits for, so login continues to work.
- The old `onopen` audio-buffer flush behaves exactly as it does today.

An earlier draft of this spec rejected dual support under YAGNI, on the assumption that the operator
controls when clients refresh. That assumption is false, and the rejection was wrong.

### Deployment order

1. `modal deploy modal_app.py` — the new server serves both client generations, so nothing breaks
2. `python3 check_login.py` — validates both the new and the legacy paths against the deployed server
3. Commit and push `index.html`
4. Reload any open tab at leisure; there is no forced-refresh requirement and no broken window

The toolbar version label (`v2026.04.19a`, commit 339acd4) gets bumped so a glance at a tab reveals
which client generation it is running. This is the only practical way to tell a stale tab from a
current one.

### Retiring the legacy path

The legacy query-parameter branch is dead code once every client has reloaded, and it keeps the token
in URLs (finding 7) for any client still using it. Retire it in a separate commit, gated on:

- every device the operator uses has been reloaded and shows the bumped version label, and
- no talk is in progress.

Until that commit lands, finding 7 is only partially resolved: new clients keep the token out of the
URL, legacy clients do not. This is a deliberate trade of complete hygiene for zero downtime.

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
| 7 | Bearer token travels in the URL query string | Partial: new clients use the first WebSocket frame and an `Authorization` header; the legacy query-param branch stays until retired (see Deployment) |
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
