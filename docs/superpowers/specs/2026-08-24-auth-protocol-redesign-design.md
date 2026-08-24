# Auth Protocol Redesign and Client Upgrade Path

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

Scope is findings 1, 2, 7, 8, 9, 10, and 11 of the twelve from the 2026-08-24 investigation, plus a
client version handshake that makes staleness observable and self-correcting.

Findings 10 and 11 — the broken service worker — were originally deferred to a separate sub-project.
They are included here because they turn out to *be* the upgrade path: a client's registration of
`service-worker.js` is the only channel through which a browser running frozen JavaScript can be
given new capability. Deferring them is what would leave today's installed clients permanently
un-upgradeable. Findings 3, 4, 5, 6 (Group A) and 12 (Group D) remain out of scope.

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
    allow_headers=["Authorization", "X-Client-Version"],
    allow_credentials=False,
    max_age=600,
)
```

Every argument is load-bearing against Starlette 1.6.0 defaults (`allow_origins=()`,
`allow_methods=('GET',)`, `allow_headers=()`, `allow_credentials=False`):

- `allow_origins` — an origin is scheme plus host. A path suffix silently matches nothing.
- `allow_headers` — the CORS safelist is `{Accept, Accept-Language, Content-Language, Content-Type}`.
  Neither `Authorization` nor `X-Client-Version` is in it, so omitting either breaks the preflight.
  This is the cost of keeping the token out of the URL.
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
MIN_CLIENT_EPOCH = 1   # raise only after Pages is confirmed serving the newer build

@web_app.get("/auth")
def auth(
    authorization: str = Header(default=""),
    x_client_version: str = Header(default="?"),
):
    scheme, _, tok = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(tok, expected_token):
        print(f"auth rejected client_epoch={x_client_version}")
        raise HTTPException(status_code=401, detail="invalid token")
    print(f"auth ok client_epoch={x_client_version}")
    return {"ok": True, "min_client_epoch": MIN_CLIENT_EPOCH}
```

No Deepgram connection, no Anthropic client use, no WebSocket. The only cost is a container wake.

The `print` calls land in `modal app logs`, which is what makes client staleness observable at all.

**Why a *minimum* epoch and not the current one.** The deployment order deploys the server before
pushing the client, so for a period the server is newer than what GitHub Pages serves. If the server
advertised "the current version is N" while Pages still served N-1, every client — including healthy
ones — would see a mismatch, reload, receive N-1 again, and reload forever. Advertising a floor that
is raised only after Pages is confirmed updated makes that loop structurally impossible.

**Why an integer epoch and not the version string.** `MIN_CLIENT_EPOCH` is a monotonically increasing
integer so the comparison is unambiguous. Comparing `v2026.08.24a`-style labels lexicographically
happens to work today but silently breaks the first time the format changes or a suffix passes `z`.
The human-readable label stays for display; the epoch is what code compares.

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

### Version handshake and upgrade prompt

The client carries two constants: an integer epoch for comparison and a label for humans.

```javascript
const CLIENT_EPOCH  = 2;                // bump on every client change that matters
const VERSION_LABEL = 'v2026.08.24a';   // display only
```

`checkAuth` sends the epoch and reads the server's floor:

```javascript
const res = await fetch(MODAL_HTTP_URL + '/auth', {
  headers: { Authorization: 'Bearer ' + token, 'X-Client-Version': String(CLIENT_EPOCH) },
  signal: ctrl.signal,
});
// on 200:
const body = await res.json().catch(() => ({}));
if (typeof body.min_client_epoch === 'number' && CLIENT_EPOCH < body.min_client_epoch) {
  maybeOfferUpgrade(body.min_client_epoch);
}
```

```javascript
function maybeOfferUpgrade(min) {
  if (isRecording) return;                          // never interrupt a talk
  const key = 'upgradeOffered:' + min;
  if (sessionStorage.getItem(key)) return;          // one attempt, then stop asking
  showUpgradeBanner(() => {
    sessionStorage.setItem(key, '1');
    location.replace(location.pathname + '?v=' + min);
  });
}
```

Three deliberate constraints:

- **Never auto-reload.** A reload destroys the page, the `MediaRecorder`, and the WebSocket. Firing
  one mid-talk would end an in-progress recording, which is strictly worse than running stale code.
  The banner is an offer, and it is suppressed entirely while `isRecording`.
- **Cache-bust explicitly.** A bare `location.reload()` can be served from a still-fresh
  `max-age=600` entry, which would present the banner again immediately. `?v=<epoch>` guarantees a
  network fetch. Building it from `location.pathname` rather than `location.href` keeps the query
  string from accumulating across successive upgrades.
- **One attempt per epoch.** The `sessionStorage` guard means that even if the floor is raised before
  Pages is updated — operator error the min-epoch design already guards against — a client offers the
  reload once and then stops, rather than looping.

#### What this does not solve

This mechanism is invisible to the clients that are stale *right now*. Today's client authenticates
over the WebSocket and never requests `/auth`, so it never receives `min_client_epoch`. The banner
protects against future staleness; it cannot participate in this transition.

For the current transition there are two levers:

1. **Observability.** A legacy query-parameter handshake is itself proof of a stale client. The server
   logs it, so `modal app logs` answers "is anything old still out there?" without any client
   cooperation. This is what turns the legacy-branch retirement gate from a guess into a check.
2. **The one channel old clients do read.** An old client renders any `{"type": "error"}` frame
   through `setStatus` (`index.html:513-515`). The server can therefore send a plain-text upgrade
   notice to legacy-handshake connections and it will appear on screen. The wart is cosmetic: the old
   client prefixes it with "Error: ". The tradeoff is that it overwrites the status line for the rest
   of a talk, so it is recommended but easy to omit — a legacy client is by definition already in the
   state we want corrected, and the captions, not the status line, are what matter during a talk.

Manual remediation of the operator's own handful of devices remains the primary path for this one
transition. The logs tell you if you missed one.

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

## Service Worker

This section exists because the service worker is the upgrade path. A browser running frozen
JavaScript re-executes `navigator.serviceWorker.register('service-worker.js')` on every page load
(`index.html:623`), and that request goes to GitHub Pages, which we control. The top-level worker
script also bypasses the HTTP cache on update checks — `updateViaCache` defaults to `'imports'` — so a
corrected worker is picked up on the very next navigation rather than up to 600 seconds later.

The page's code is frozen. The worker it registers is not. That asymmetry is the whole mechanism.

### The two defects

`service-worker.js:2` lists root-absolute paths:

```javascript
const ASSETS = ['/', '/index.html', '/manifest.json', '/icon-192.png', '/icon-512.png'];
```

The site is served from `/streaming-dictation/`, so all five resolve against the domain root and all
five return 404 (measured; no root Pages site exists). `cache.addAll()` rejects as a unit, `install`
fails, and the worker never activates.

`service-worker.js:12` is cache-first with no invalidation against a static `CACHE_NAME`:

```javascript
caches.match(e.request).then((cached) => cached || fetch(e.request))
```

Fixing only the paths would therefore be *worse* than the status quo: a working install would pin a
standalone window to the first `index.html` it ever cached, permanently, with no address bar to escape
through. The two defects must be fixed together.

### Replacement

```javascript
const CACHE  = 'dictation-e2';   // bump in lockstep with CLIENT_EPOCH in index.html
const ASSETS = ['./', './index.html', './manifest.json', './icon-192.png', './icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // Individually, not addAll: one missing asset must not fail the whole install.
    await Promise.allSettled(ASSETS.map((u) => c.add(new Request(u, { cache: 'reload' }))));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (new URL(req.url).origin !== self.location.origin) return;  // never touch modal.run

  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      try {
        const fresh = await fetch(req.url, { cache: 'reload' });   // bypass the 600s HTTP cache
        // waitUntil, not fire-and-forget: the worker may be terminated as soon as
        // respondWith settles, which would abandon the write mid-flight.
        e.waitUntil(caches.open(CACHE).then((c) => c.put('./', fresh.clone())));
        return fresh;
      } catch (_) {
        return (await caches.match('./')) || Response.error();     // offline fallback
      }
    })());
    return;
  }

  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
```

Each departure from the original earns its place:

- **Relative paths** resolve against the worker's own scope, `/streaming-dictation/`, which is what
  the manifest already does correctly.
- **Per-asset `cache.add` under `Promise.allSettled`** means a single 404 degrades offline coverage
  instead of disabling the worker entirely. This is a direct response to how the original failed: one
  bad path took down the whole mechanism silently.
- **`cache: 'reload'` on precache fetches** avoids seeding the cache from a stale HTTP entry.
- **Versioned `CACHE` plus purge-on-activate** removes the stranding failure mode and cleans up the
  existing empty `dictation-v1`.
- **`skipWaiting()` + `clients.claim()`** let the corrected worker take control on first install
  rather than waiting for every window to close — which, for a standalone PWA, might be never.
- **Network-first navigation with `cache: 'reload'`** eliminates the 600-second staleness window
  entirely for controlled clients, while the cache fallback preserves offline launch.
- **The origin guard** keeps the worker away from the Modal endpoint. The WebSocket is not a `fetch`
  and would not be intercepted regardless, but `/auth` is a cross-origin `fetch` and must not be
  cached or interfered with.

### What today's clients actually get, and when

| Event | Result |
|-------|--------|
| Next navigation (cold PWA launch, reload, new tab) | Fetches the corrected worker, installs successfully, activates, claims the page. HTML for *this* navigation may still be stale, since the worker was not yet controlling it. |
| Navigation after that | Served network-first. Always current. The 600-second window is gone for good. |

So: **one navigation to become upgradeable, two to be guaranteed current.** That is the honest bound,
and it is a bounded wait rather than the "never" an un-repaired worker implies.

### Why the worker does not force a reload

Once activated it could call `WindowClient.navigate()` and refresh open windows outright. It must not.
A navigation destroys the page, the `MediaRecorder`, and the WebSocket, so forcing one mid-talk would
end a recording in progress — strictly worse than running stale code for another hour. The worker
cannot see `isRecording`, and plumbing that through `postMessage` would mean the old, frozen client
would have to cooperate, which it cannot.

Upgrades are therefore taken at the next natural navigation, or offered through the banner described
above. Never imposed.

### Version coupling

`CACHE` in `service-worker.js` and `CLIENT_EPOCH` in `index.html` must be bumped together. They live
in different files with no shared source, which is a genuine footgun, so `check_login.py` asserts that
the epoch implied by the deployed worker's cache name matches the deployed client's `CLIENT_EPOCH`.

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
| `GET /auth` with `X-Client-Version` below the floor | `200` with `min_client_epoch` above the sent value |
| `OPTIONS /auth` preflight requesting `X-Client-Version` | `200`, header listed in `Access-Control-Allow-Headers` |
| Every path in the deployed worker's `ASSETS`, fetched | `200` (this is the check the original bug would have failed) |
| `CACHE` epoch in deployed `service-worker.js` vs `CLIENT_EPOCH` in deployed `index.html` | equal |

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
- `manifest.json`, which already uses correct relative paths and needs no edit.
- The set of files the service worker precaches; only how it fetches and invalidates them changes.

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
edge case. The operator cannot be relied on to refresh every client by hand.

The table above describes the situation *today*, with no service worker active — verified in a real
browser: `getRegistrations()` empty, `controller` null, and the `dictation-v1` cache present but
empty because `caches.open()` succeeds before `addAll()` rejects.

The Service Worker section replaces that table. Once a client is controlled by the corrected worker,
navigations are served network-first with `cache: 'reload'`, so the 600-second window closes and the
only remaining question is when a client next navigates:

| Client state, after the worker is installed | Picks up new `index.html`? |
|---------------------------------------------|----------------------------|
| Any navigation — cold PWA launch, reload, new tab | Yes, always current; the HTTP cache is bypassed |
| Window open and never navigating again | No — and no mechanism can safely force it (see the Service Worker section) |

The residual unreachable case is therefore narrower than the HTTP-cache analysis suggests: not "any
open window for up to 10 minutes and possibly forever," but "a window that never navigates again,"
which cannot be remediated without destroying an in-progress recording.

### Installed PWA clients

The app is installable: `manifest.json` declares `name`, `short_name`, `start_url: "."`,
`display: standalone`, and 192/512 icons, all over HTTPS. Note the contrast with the service worker —
the manifest uses *relative* paths, so they resolve correctly against `/streaming-dictation/`, while
`ASSETS` uses root-absolute paths and does not. The PWA is fine; only the caching is broken.

An installed PWA is still an ordinary browser context, so launching it navigates to `start_url` and
that navigation goes through the HTTP cache. A cold launch more than 10 minutes after the last one
therefore picks up new code. Two things make PWA clients the *worst* case for staleness anyway:

1. **Launching often resumes rather than navigates.** On iOS and Android, reopening an installed app
   commonly restores a backgrounded instance, which keeps the old JavaScript in memory indefinitely.
   This is the open-tab problem without a visible tab to suggest closing.
2. **`display: standalone` removes the address bar and the reload button.** A user in a stale
   standalone window has no obvious way to force a refresh, so the population least able to
   self-remediate is also the population most likely to be stale.

Measured on the live site in a real browser: `navigator.serviceWorker.getRegistrations()` returns an
empty array and `navigator.serviceWorker.controller` is `null`, so no worker is active and no fetch
interception occurs. The `dictation-v1` cache *does* exist but is empty — `caches.open()` in the
`install` handler succeeds before `cache.addAll(ASSETS)` rejects. So today there is no service-worker
layer of staleness on top of the HTTP cache.

Installed PWAs are consequently the strongest argument for the dual-protocol support described below,
not an exception to it.

An earlier draft of this section overstated the problem in one direction and understated it in
another. Overstated: iOS and Android routinely evict backgrounded web apps, so a cold relaunch — and
therefore a navigation — happens far more often in practice than "indefinitely" implies. Understated:
the draft treated the broken service worker as merely harmless, when in fact repairing it is the
mechanism that makes these clients upgradeable at all. See the Service Worker section.

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

1. `modal deploy modal_app.py` with `MIN_CLIENT_EPOCH` left at its current value — the new server
   serves both client generations, so nothing breaks
2. `python3 check_login.py` — validates both the new and the legacy paths against the deployed server
3. Commit and push `index.html` (with `CLIENT_EPOCH` incremented) and `service-worker.js` (with
   `CACHE` bumped to match) in the same push, so the navigation that updates a client's protocol also
   installs its future upgrade capability
4. Confirm `https://bbuchalter.github.io/streaming-dictation/` actually serves the new build. GitHub
   Pages caches for 600 seconds, so this is not immediate; check the version label on the auth gate.
   Note that a client's *first* navigation after this push installs the worker but may still receive
   stale HTML; the navigation after that is guaranteed current
5. Only then raise `MIN_CLIENT_EPOCH` to the new value and `modal deploy` again. This arms the upgrade
   banner. Doing it before step 4 is what the min-epoch design and the `sessionStorage` guard exist to
   survive, but the ordering avoids relying on either
6. Watch `modal app logs` for legacy handshakes and for `client_epoch` values below the floor

There is no forced-refresh requirement and no window in which the app is broken. Steps 5 and 6 are
about closing out staleness, not about restoring service.

The toolbar version label (`v2026.04.19a`, commit 339acd4) gets bumped so a glance reveals which
client generation a tab or window is running.

It also has to **move**. The label sits at `index.html:222`, inside `<div id="app">`, which is
`display: none` until a login succeeds (`index.html:207`, revealed at `index.html:329-330`). A stale
client that cannot log in therefore cannot show its version — exactly the situation where the version
is the thing you need. The label must also render on the auth gate (`index.html:201`), where it is
visible before authentication.

### Retiring the legacy path

The legacy query-parameter branch is dead code once every client has reloaded, and it keeps the token
in URLs (finding 7) for any client still using it. Retire it in a separate commit, gated on an
observable condition rather than an assumption:

- `modal app logs` shows no legacy query-parameter handshake over a window that plausibly covers every
  installed PWA and long-lived tab — a few weeks of normal use, including at least one talk, and
- no talk is in progress at the moment of the deploy.

An earlier draft gated this on "every device has been reloaded and shows the bumped version label,"
which is not checkable: installed PWA instances cannot be enumerated, and a backgrounded standalone
window can resume weeks later. Without server-side logging of the handshake generation, the legacy
branch would have become permanent by default and finding 7 would have stayed partial forever.

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
| 10 | Root-absolute `ASSETS` paths break service worker installation | Relative paths, plus per-asset `cache.add` so one failure cannot fail the install |
| 11 | Cache-first with no invalidation would strand users on stale HTML | Network-first navigation with `cache: 'reload'`, versioned cache, `skipWaiting()` + `clients.claim()` |

## Out of Scope

Deferred to their own sub-projects. Findings 10 and 11 were previously listed here and have been
pulled into this design; see the Overview for why.

- **Group A — diagnosability** (findings 3, 4, 5, 6): mid-session `4001` kills the session silently;
  the heartbeat sends a `PING` the server never reads, so it cannot detect a black-holed connection;
  five blanket `except Exception: pass` handlers on the server; unlimited reconnection masks
  permanent faults. Group A consumes the `4003` close code introduced here.
- **Group D — housekeeping** (finding 12): `.env` carries dead `MODAL_POLISH_URL` and
  `REVAI_ACCESS_TOKEN` entries from the pre-Deepgram architecture, plus a `MODAL_BEARER_TOKEN` that no
  committed code reads.
