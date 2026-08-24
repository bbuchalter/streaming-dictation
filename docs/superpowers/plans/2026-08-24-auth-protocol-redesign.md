# Auth Protocol Redesign and Client Upgrade Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate authentication from the transcription stream so login is cheap and backend failures stop masquerading as a wrong password, and repair the service worker so clients can be upgraded at all.

**Architecture:** A new `GET /auth` endpoint authenticates the login gate over HTTP, where real status codes exist, instead of opening a billable Deepgram WebSocket session. The `/stream` WebSocket moves its token from the URL query string into the first text frame while continuing to accept the legacy query parameter, so deploying the server cannot break a client that has not reloaded. A corrected service worker serves navigations network-first, which is what makes already-installed clients upgradeable.

**Tech Stack:** Python 3.11 on Modal (FastAPI ASGI app, `websockets`), vanilla browser JavaScript in a single `index.html`, static hosting on GitHub Pages, Deepgram Nova-3 for STT, Claude Haiku for polish.

**Spec:** `docs/superpowers/specs/2026-08-24-auth-protocol-redesign-design.md`

## Global Constraints

- Threat model is **cost control**, not access control: a single shared secret, `hmac.compare_digest` for comparison, no rate limiting, no session issuance.
- CORS is browser-enforced, not server-enforced. The bearer token is the access control. Never treat CORS as security.
- No secret value, and no fingerprint of one, may appear in committed code, comments, or docs. `check_login.py` reads the token from `.env` and never prints it.
- `.env` is gitignored and must stay that way.
- The server must accept **both** the legacy query-parameter handshake and the new first-frame handshake for the entire life of this plan. Removing the legacy branch is explicitly a later, separately-gated commit.
- Never auto-reload a client. A reload destroys the page, the `MediaRecorder`, and the WebSocket, ending an in-progress recording. Upgrades are offered, never imposed, and suppressed entirely while `isRecording`.
- `CLIENT_EPOCH` in `index.html` and `CACHE` in `service-worker.js` must be bumped together. Current epoch is `1` (implicit, pre-change); this plan ships epoch `2`.
- `MIN_CLIENT_EPOCH` on the server stays at `1` until GitHub Pages is confirmed serving epoch `2`. Raising it early is what would make every healthy client reload into the older build.
- Endpoint hostname does not change: `bbuchalter--streaming-dictation-streamingdictation-web.modal.run`.
- Deepgram parameters, the polish prompt, audio capture, and the caption UI are out of scope. Do not touch `build_deepgram_url()`, `SYSTEM_PROMPT`, `MediaRecorder` setup, or `AUDIO_BUFFER_MAX`.

## Development Loop

There is no local test runner and no unit test framework in this repository. Verification is a single integration harness, `check_login.py`, run against the deployed Modal endpoint.

Iterate with `modal deploy modal_app.py`, which took under 15 seconds in every observed run. **Deploying mid-plan is safe**: because the server accepts both handshakes from Task 3 onward and only adds a route in Task 2, every intermediate server state still serves today's clients correctly. There is no state in this plan where a deploy breaks the live app.

Client changes (`index.html`, `service-worker.js`) are committed but **not pushed** until Task 8. Test them locally with `python3 -m http.server 8000` against the deployed server. This is why `http://localhost:8000` appears in `allow_origins` — the spec left it out, but without it there is no way to verify a client change before publishing it to real users.

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `check_login.py` | create | Integration harness. Every check in the spec's Testing table. Reads `.env`, prints pass/fail, exits non-zero on failure. |
| `modal_app.py` | modify | Fail-fast secret validation, tightened CORS, new `GET /auth`, dual-path `/stream` handshake, close codes, client-version logging. |
| `index.html` | modify | `checkAuth` replaces `verifyToken`, first-frame WS auth, version constants, upgrade banner, version label on the auth gate. |
| `service-worker.js` | rewrite | Relative asset paths, resilient install, versioned cache, network-first navigation. |

---

### Task 1: Integration harness (all checks, expected red)

**Files:**
- Create: `check_login.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `python3 check_login.py [--only modal|pages|all]`. Exits `0` when every selected check passes, `1` otherwise. Later tasks are verified exclusively by named checks from this harness: `http_auth_valid`, `http_auth_wrong`, `http_auth_missing`, `http_auth_preflight`, `http_auth_floor`, `ws_frame_valid`, `ws_frame_wrong`, `ws_frame_absent`, `ws_legacy_valid`, `ws_legacy_wrong`, `pages_sw_assets`, `pages_epoch_match`.

- [ ] **Step 1: Write the harness**

Create `check_login.py`:

```python
#!/usr/bin/env python3
"""Integration smoke test for the streaming-dictation auth path.

Runs against the deployed Modal endpoint and the published GitHub Pages site.
Reads the bearer token from .env. Never prints the token.
"""
import argparse
import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import websockets

MODAL_HOST = "bbuchalter--streaming-dictation-streamingdictation-web.modal.run"
HTTP_BASE = f"https://{MODAL_HOST}"
WS_BASE = f"wss://{MODAL_HOST}"
PAGES_ORIGIN = "https://bbuchalter.github.io"
PAGES_BASE = "https://bbuchalter.github.io/streaming-dictation/"
WRONG = "definitely-not-the-password-xyz"

results = []


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")


def load_token():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("MODAL_BEARER_TOKEN="):
                    tok = line.split("=", 1)[1].strip()
                    if tok:
                        return tok
    except FileNotFoundError:
        pass
    sys.exit("MODAL_BEARER_TOKEN not found or empty in .env")


def http(url, method="GET", headers=None, timeout=90):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode()
    except Exception as e:
        return None, {}, f"{type(e).__name__}: {e}"


# ---------------- Modal endpoint checks ----------------

def check_auth_valid(tok):
    st, _, body = http(f"{HTTP_BASE}/auth", headers={"Authorization": f"Bearer {tok}",
                                                    "X-Client-Version": "2"})
    record("http_auth_valid", st == 200, f"expected 200, got {st} ({body[:80]})")


def check_auth_wrong(_tok):
    st, _, _ = http(f"{HTTP_BASE}/auth", headers={"Authorization": f"Bearer {WRONG}"})
    record("http_auth_wrong", st == 401, f"expected 401, got {st}")


def check_auth_missing(_tok):
    st, _, _ = http(f"{HTTP_BASE}/auth")
    record("http_auth_missing", st == 401, f"expected 401, got {st}")


def check_auth_preflight(_tok):
    st, hdrs, _ = http(
        f"{HTTP_BASE}/auth",
        method="OPTIONS",
        headers={
            "Origin": PAGES_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-client-version",
        },
    )
    acao = hdrs.get("access-control-allow-origin", "")
    acah = hdrs.get("access-control-allow-headers", "").lower()
    ok = st == 200 and acao in (PAGES_ORIGIN, "*") and "authorization" in acah and "x-client-version" in acah
    record("http_auth_preflight", ok, f"status={st} ACAO={acao!r} ACAH={acah!r}")


def check_auth_floor(tok):
    st, _, body = http(f"{HTTP_BASE}/auth", headers={"Authorization": f"Bearer {tok}",
                                                    "X-Client-Version": "2"})
    floor = None
    if st == 200:
        try:
            floor = json.loads(body).get("min_client_epoch")
        except ValueError:
            pass
    record("http_auth_floor", isinstance(floor, int), f"min_client_epoch={floor!r} (status {st})")


# ---------------- WebSocket checks ----------------

async def ws_probe(url, first_frame, recv_timeout):
    """Return ('listening', None) | ('closed', code) | ('error', text)."""
    try:
        async with websockets.connect(url, open_timeout=120) as ws:
            if first_frame is not None:
                await ws.send(first_frame)
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                    msg = json.loads(raw)
                    if msg.get("type") == "status" and msg.get("data") == "listening":
                        await ws.send("EOS")
                        return "listening", None
            except websockets.ConnectionClosed as e:
                return "closed", e.code
            except asyncio.TimeoutError:
                return "error", "recv timeout"
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}"


async def check_ws(tok):
    outcome, info = await ws_probe(f"{WS_BASE}/stream", tok, 90)
    record("ws_frame_valid", outcome == "listening", f"got {outcome} {info}")

    outcome, info = await ws_probe(f"{WS_BASE}/stream", WRONG, 60)
    record("ws_frame_wrong", outcome == "closed" and info == 4001, f"got {outcome} {info}")

    outcome, info = await ws_probe(f"{WS_BASE}/stream", None, 30)
    record("ws_frame_absent", outcome == "closed" and info == 4002, f"got {outcome} {info}")

    q = f"{WS_BASE}/stream?token={urllib.parse.quote(tok)}"
    outcome, info = await ws_probe(q, None, 90)
    record("ws_legacy_valid", outcome == "listening", f"got {outcome} {info}")

    q = f"{WS_BASE}/stream?token={urllib.parse.quote(WRONG)}"
    outcome, info = await ws_probe(q, None, 60)
    record("ws_legacy_wrong", outcome == "closed" and info == 4001, f"got {outcome} {info}")


# ---------------- GitHub Pages checks ----------------

def check_pages_sw_assets():
    st, _, src = http(f"{PAGES_BASE}service-worker.js")
    if st != 200:
        record("pages_sw_assets", False, f"could not fetch service-worker.js (status {st})")
        return
    m = re.search(r"const ASSETS\s*=\s*\[(.*?)\]", src, re.S)
    if not m:
        record("pages_sw_assets", False, "ASSETS array not found in deployed worker")
        return
    paths = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
    bad = []
    for p in paths:
        target = urllib.parse.urljoin(PAGES_BASE, p)
        code, _, _ = http(target)
        if code != 200:
            bad.append(f"{p}->{code}")
    record("pages_sw_assets", not bad, f"{len(paths)} assets checked; failures: {bad or 'none'}")


def check_pages_epoch_match():
    _, _, sw = http(f"{PAGES_BASE}service-worker.js")
    _, _, html = http(f"{PAGES_BASE}index.html")
    m_sw = re.search(r"dictation-e(\d+)", sw or "")
    m_html = re.search(r"CLIENT_EPOCH\s*=\s*(\d+)", html or "")
    sw_e = m_sw.group(1) if m_sw else None
    html_e = m_html.group(1) if m_html else None
    record("pages_epoch_match", sw_e is not None and sw_e == html_e,
           f"worker cache epoch={sw_e!r} client epoch={html_e!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["modal", "pages", "all"], default="all")
    args = ap.parse_args()
    tok = load_token()

    if args.only in ("modal", "all"):
        print("Modal endpoint:")
        for fn in (check_auth_valid, check_auth_wrong, check_auth_missing,
                   check_auth_preflight, check_auth_floor):
            fn(tok)
        asyncio.run(check_ws(tok))

    if args.only in ("pages", "all"):
        print("GitHub Pages:")
        check_pages_sw_assets()
        check_pages_epoch_match()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and confirm the expected checks fail**

Run: `python3 check_login.py`

Expected: exit `1`. These must FAIL, and their failure is what the rest of the plan fixes:

- `http_auth_valid`, `http_auth_wrong`, `http_auth_missing`, `http_auth_preflight`, `http_auth_floor` — `/auth` does not exist yet, so expect `404`
- `ws_frame_valid` — today's server reads the query parameter, finds it empty, and closes `4001`
- `ws_frame_absent` — expect `4001` rather than `4002`, because there is no auth timeout yet
- `pages_sw_assets` — the five root-absolute paths return `404`
- `pages_epoch_match` — neither constant exists yet

These must PASS already, and must keep passing through every later task. They are the backward-compatibility guarantee:

- `ws_legacy_valid` — reaches `listening`
- `ws_legacy_wrong` — closes `4001`

If `ws_legacy_valid` fails at this step, stop: something is wrong with the environment or the Deepgram key, not with this plan.

- [ ] **Step 3: Commit**

```bash
git add check_login.py
git commit -m "test: add integration harness for auth path

Covers the spec's Testing table: /auth status codes and preflight,
first-frame and legacy WebSocket handshakes, service worker asset
reachability, and client/worker epoch agreement.

Currently red except the two legacy WebSocket checks, which are the
backward-compatibility guarantee and must stay green throughout."
```

---

### Task 2: Server — fail-fast secret, tightened CORS, `GET /auth`

**Files:**
- Modify: `modal_app.py:149-162` (inside `web()`, before the route definitions)

**Interfaces:**
- Consumes: `check_login.py` from Task 1.
- Produces: `GET /auth` returning `{"ok": true, "min_client_epoch": int}` on success and `401` otherwise. Module-level constant `MIN_CLIENT_EPOCH = 1`. A closure variable `expected_token` available to the `/stream` handler in Task 3.

- [ ] **Step 1: Confirm the target checks are red**

Run: `python3 check_login.py --only modal`
Expected: `http_auth_valid`, `http_auth_wrong`, `http_auth_missing`, `http_auth_preflight`, `http_auth_floor` all FAIL with `404`.

- [ ] **Step 2: Add the module-level floor constant**

In `modal_app.py`, immediately after `app = modal.App("streaming-dictation")` on line 5:

```python
# Clients older than this are asked to reload. Raise only after GitHub Pages is
# confirmed serving the newer build — see the plan's Task 8.
MIN_CLIENT_EPOCH = 1
```

- [ ] **Step 3: Add the imports `web()` needs**

`modal_app.py:152` currently reads:

```python
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
```

Replace with:

```python
        import hmac
        from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
```

`CORSMiddleware` is already imported at `modal_app.py:153`; leave that line alone.

- [ ] **Step 4: Validate the secret at container construction**

Immediately after `web_app = FastAPI()` (`modal_app.py:155`), insert:

```python
        # Fail fast: web() runs once per container, so a missing or empty secret
        # becomes a visible crash loop instead of a per-request KeyError raised
        # after ws.accept() has already succeeded.
        expected_token = os.environ["BEARER_TOKEN"]
        if not expected_token.strip():
            raise RuntimeError("BEARER_TOKEN is set but empty")
```

- [ ] **Step 5: Tighten the existing CORS middleware**

Replace `modal_app.py:157-162`:

```python
        web_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
```

with:

```python
        web_app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "https://bbuchalter.github.io",   # scheme+host only — never a path
                "http://localhost:8000",          # python3 -m http.server, for testing client changes
            ],
            allow_methods=["GET"],
            allow_headers=["Authorization", "X-Client-Version"],
            allow_credentials=False,
            max_age=600,
        )
```

`Authorization` and `X-Client-Version` must be listed explicitly: the CORS safelist is only
`{Accept, Accept-Language, Content-Language, Content-Type}`, so omitting either breaks the preflight.

- [ ] **Step 6: Add the `/auth` route**

Insert immediately before the `@web_app.websocket("/stream")` decorator (currently `modal_app.py:193`):

```python
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

The `print` calls are the observability that makes the legacy branch retirable later; they land in
`modal app logs`.

- [ ] **Step 7: Deploy and verify green**

```bash
modal deploy modal_app.py
python3 check_login.py --only modal
```

Expected: all five `http_auth_*` checks PASS. `ws_frame_valid` and `ws_frame_absent` still FAIL (Task 3). `ws_legacy_valid` and `ws_legacy_wrong` still PASS.

- [ ] **Step 8: Verify the fail-fast path actually fails loudly**

This guards against a silent regression in the thing Task 2 exists to fix. Temporarily change the
validation line to reference a name that does not exist:

```python
        expected_token = os.environ["BEARER_TOKEN_TYPO"]
```

Run `modal deploy modal_app.py` then `python3 check_login.py --only modal`. Expected: checks fail, and
`modal app logs streaming-dictation` shows a `KeyError` at container start rather than a silent
`401`. Then revert the line to `os.environ["BEARER_TOKEN"]`, redeploy, and confirm the `http_auth_*`
checks are green again before committing.

- [ ] **Step 9: Commit**

```bash
git add modal_app.py
git commit -m "feat: add GET /auth endpoint with fail-fast secret validation

Login no longer needs a WebSocket, so it no longer opens a billable
Deepgram streaming session on every page load and login attempt.
HTTP status codes replace WebSocket close-code inference, which is what
made a revoked Deepgram key present as 'Invalid password'.

BEARER_TOKEN is now read at container construction, so a missing secret
is a visible crash loop rather than a per-request KeyError after accept().

CORS was already installed with wildcards; this narrows it to the Pages
origin plus localhost:8000 for testing client changes pre-publish."
```

---

### Task 3: Server — dual-path `/stream` handshake

**Files:**
- Modify: `modal_app.py:193-214` (the `stream` handler's auth prologue and Deepgram failure path)

**Interfaces:**
- Consumes: `expected_token` closure variable and `MIN_CLIENT_EPOCH` from Task 2.
- Produces: `/stream` accepting the token as either the first text frame or the legacy `token` query parameter. Emits `{"type":"status","data":"authenticated"}` after a successful handshake. Close codes: `4001` unauthorized, `4002` malformed or absent handshake, `4003` upstream permanently failed.

- [ ] **Step 1: Confirm the target checks are red**

Run: `python3 check_login.py --only modal`
Expected: `ws_frame_valid` FAILS (closes `4001`), `ws_frame_absent` FAILS (closes `4001`, not `4002`). `ws_legacy_valid` and `ws_legacy_wrong` PASS.

- [ ] **Step 2: Replace the auth prologue**

`modal_app.py:195-201` currently reads:

```python
            # Auth — must accept before we can close with a code
            await ws.accept()
            token = ws.query_params.get("token", "")
            expected = os.environ["BEARER_TOKEN"]
            if token != expected:
                await ws.close(code=4001, reason="Unauthorized")
                return
```

Replace with:

```python
            # Auth — must accept before we can close with a code
            await ws.accept()

            # LEGACY PATH: token in the query parameter. Clients cached before this
            # change send it this way, and an installed PWA can stay stale
            # indefinitely, so this branch cannot be removed on a deploy boundary.
            # Retire it only per the spec's "Retiring the legacy path" gate.
            legacy = ws.query_params.get("token")
            if legacy is not None:
                if not hmac.compare_digest(legacy, expected_token):
                    await ws.close(code=4001, reason="Unauthorized")
                    return
                print("stream auth ok via legacy query param — stale client")
            else:
                try:
                    first = await asyncio.wait_for(ws.receive(), timeout=10)
                except asyncio.TimeoutError:
                    # Without this, a client that connects and says nothing pins a
                    # container for the full 7200s function timeout.
                    await ws.close(code=4002, reason="Auth timeout")
                    return
                tok = first.get("text") if isinstance(first, dict) else None
                if tok is None:
                    await ws.close(code=4002, reason="Expected token as first text frame")
                    return
                if not hmac.compare_digest(tok, expected_token):
                    await ws.close(code=4001, reason="Unauthorized")
                    return
                print("stream auth ok via first frame")

            await ws.send_json({"type": "status", "data": "authenticated"})
```

`hmac` is already imported by Task 2 Step 3.

- [ ] **Step 3: Give the Deepgram failure path a distinct close code**

`modal_app.py` currently ends the Deepgram failure branch with:

```python
            except Exception as e:
                await ws.send_json({"type": "error", "data": f"Deepgram connection failed: {type(e).__name__}: {e}"})
                await ws.close()
                return
```

Replace the `await ws.close()` line so the branch reads:

```python
            except Exception as e:
                await ws.send_json({"type": "error", "data": f"Deepgram connection failed: {type(e).__name__}: {e}"})
                # 4003 = upstream permanently failed. A revoked key is not fixed by
                # retrying. Today's client still reconnects on this code; Group A
                # finding 6 teaches it to stop. The error frame above is what makes
                # the real cause visible in the meantime.
                await ws.close(code=4003, reason="Upstream unavailable")
                return
```

- [ ] **Step 4: Notify legacy clients over the only channel they read**

An old client renders any `{"type": "error"}` frame through `setStatus` (`index.html:513-515`); it has
no other channel we can reach. Immediately after the `print("stream auth ok via legacy query param — stale client")` line, add:

```python
                await ws.send_json({
                    "type": "error",
                    "data": "This page is out of date — please reload it.",
                })
```

This is a deliberate, self-contained step. It squats on the status line for the rest of a session, and
the old client prefixes it with "Error: ". If that proves more annoying than useful, revert this step
alone; nothing else depends on it.

- [ ] **Step 5: Deploy and verify green**

```bash
modal deploy modal_app.py
python3 check_login.py --only modal
```

Expected: all ten Modal checks PASS. In particular `ws_legacy_valid` must still PASS — that is the
backward-compatibility guarantee, and if it regresses, stop and fix before continuing.

- [ ] **Step 6: Confirm the auth timeout does not leak containers**

Run: `python3 check_login.py --only modal` and watch `modal app logs streaming-dictation`.
Expected: the `ws_frame_absent` probe closes after roughly 10 seconds, not after 7200.

- [ ] **Step 7: Commit**

```bash
git add modal_app.py
git commit -m "feat: accept WebSocket token as first frame, keep legacy query param

New clients send the bearer token as the first text frame, keeping it out
of URLs and access logs. The legacy query parameter is still accepted
because GitHub Pages caches index.html for 600s and an already-open tab
or installed PWA may never re-fetch it, so a deploy must not assume the
client has reloaded.

Adds a 10s auth timeout so a silent client cannot pin a container for the
full 7200s function timeout, and close code 4003 so a permanently failed
upstream is distinguishable from a rejected token.

Legacy connections are logged, which is what makes retiring the legacy
branch a checkable condition rather than an assumption."
```

---

### Task 4: Client — `checkAuth` replaces `verifyToken`

**Files:**
- Modify: `index.html:229` (add `MODAL_HTTP_URL`, `AUTH_TIMEOUT_MS`), `index.html:284-305` (`authenticate`), `index.html:307-326` (delete `verifyToken`, add `checkAuth`), `index.html:339-344` (session re-verify)

**Interfaces:**
- Consumes: `GET /auth` from Task 2.
- Produces: `async function checkAuth(token) -> {ok: boolean, reason?: string}`. `verifyToken` no longer exists.

- [ ] **Step 1: Add the derived HTTP base and the timeout constant**

After `index.html:229` (`const MODAL_WS_URL = ...`), insert:

```javascript
      const MODAL_HTTP_URL = MODAL_WS_URL.replace(/^wss:/, 'https:');  // one source of truth
      const AUTH_TIMEOUT_MS = 20000;   // cold start measured at 3.9-5.0s; 10s was too tight
```

- [ ] **Step 2: Replace `verifyToken` with `checkAuth`**

Delete the whole `verifyToken` function (`index.html:307-326`) and put this in its place:

```javascript
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
          return {
            ok: false,
            reason: e.name === 'AbortError'
              ? 'Timed out waking the server — try again'
              : 'Network error: ' + e.message,
          };
        } finally {
          clearTimeout(timer);
        }
      }
```

- [ ] **Step 3: Rewrite `authenticate` to surface the real reason**

Replace `index.html:284-305` (the whole `authenticate` function) with:

```javascript
      async function authenticate() {
        const token = passwordInput.value.trim();
        if (!token) return;
        authSubmit.disabled = true;
        authSubmit.textContent = 'Verifying...';
        // A cold container takes seconds; say so rather than looking hung.
        const waking = setTimeout(() => { authSubmit.textContent = 'Waking server...'; }, 2000);

        const result = await checkAuth(token);
        clearTimeout(waking);

        if (result.ok) {
          sessionToken = token;
          sessionStorage.setItem('sessionToken', token);
          showApp();
          return;
        }
        sessionStorage.removeItem('sessionToken');
        authSubmit.textContent = result.reason;
        setTimeout(() => { authSubmit.textContent = 'Connect'; authSubmit.disabled = false; }, 3000);
      }
```

- [ ] **Step 4: Point the session re-verify at `checkAuth`**

Replace `index.html:339-344`:

```javascript
      if (sessionToken) {
        verifyToken(sessionToken).then((valid) => {
          if (valid) showApp();
          else { sessionStorage.removeItem('sessionToken'); sessionToken = ''; }
        }).catch(() => { sessionStorage.removeItem('sessionToken'); sessionToken = ''; });
      }
```

with:

```javascript
      if (sessionToken) {
        checkAuth(sessionToken).then((result) => {
          if (result.ok) showApp();
          else { sessionStorage.removeItem('sessionToken'); sessionToken = ''; }
        });
      }
```

`checkAuth` never rejects, so the `.catch` is dead code and is removed.

- [ ] **Step 5: Confirm no reference to `verifyToken` survives**

Run: `grep -n "verifyToken" index.html`
Expected: no output.

- [ ] **Step 6: Verify in a real browser against the deployed server**

```bash
python3 -m http.server 8000
```

Load `http://localhost:8000/index.html` and check three things:

1. The correct password (from `.env`) logs in.
2. A wrong password shows exactly `Invalid password`.
3. Login no longer opens a Deepgram session. Confirm in `modal app logs streaming-dictation`: an
   `auth ok client_epoch=?` line appears with **no** accompanying `stream auth` line. That absence is
   the cost fix (finding 9) working.

- [ ] **Step 7: Commit**

```bash
git add index.html
git commit -m "fix: authenticate over HTTP so backend failures stop reading as bad passwords

verifyToken opened a /stream WebSocket and waited for 'listening', which
the server sends only after Deepgram connects. It handled close code 4001
and nothing else, so a revoked Deepgram key closed with 1000, fell
through to a 10s timer, and rendered 'Invalid password' while the server's
actual error frame was discarded.

checkAuth calls GET /auth instead. HTTP status codes make 401 and 503 and
a network error distinguishable, the timeout goes to 20s because a cold
start measured 3.9-5.0s, and login no longer opens a billable Deepgram
session."
```

---

### Task 5: Client — first-frame WebSocket auth

**Files:**
- Modify: `index.html:487` (the `new WebSocket(...)` URL), `index.html:489-497` (`onopen`), `index.html:503-509` (the `status` case in `onmessage`)

**Interfaces:**
- Consumes: the dual-path `/stream` handshake from Task 3.
- Produces: the recording WebSocket authenticating by first frame. The buffered-audio flush is triggered by `listening`, not by `onopen`.

- [ ] **Step 1: Drop the token from the URL**

`index.html:487` currently reads:

```javascript
        ws = new WebSocket(MODAL_WS_URL + '/stream?token=' + encodeURIComponent(sessionToken));
```

Replace with:

```javascript
        ws = new WebSocket(MODAL_WS_URL + '/stream');   // token goes in the first frame
```

- [ ] **Step 2: Send the token in `onopen` and stop flushing there**

Replace `index.html:489-497` (the whole `ws.onopen` handler):

```javascript
        ws.onopen = () => {
          reconnectAttempts = 0;
          startHeartbeat();
          // Flush buffered audio chunks
          while (audioBuffer.length > 0) {
            const chunk = audioBuffer.shift();
            if (ws.readyState === WebSocket.OPEN) ws.send(chunk);
          }
        };
```

with:

```javascript
        ws.onopen = () => {
          reconnectAttempts = 0;
          startHeartbeat();
          ws.send(sessionToken);   // first text frame is the bearer token
          // Do NOT flush audio here. The server has not entered its receive loop
          // until it reports 'listening', so anything sent now would sit in the
          // socket buffer and arrive late. The flush happens on 'listening'.
        };
```

- [ ] **Step 3: Flush on `listening`**

The `status` case in `onmessage` (`index.html:503-509`) currently reads:

```javascript
              case 'status':
                if (msg.data === 'listening') {
                  setStatus('Listening...', 'listening');
                } else if (msg.data === 'disconnected') {
                  setStatus('Server reconnecting...', '');
                }
                break;
```

Replace with:

```javascript
              case 'status':
                if (msg.data === 'authenticated') {
                  setStatus('Authenticated, connecting...', '');
                } else if (msg.data === 'listening') {
                  setStatus('Listening...', 'listening');
                  // Deepgram is live and the server's receive loop is running, so
                  // buffered audio can safely go out now.
                  while (audioBuffer.length > 0) {
                    const chunk = audioBuffer.shift();
                    if (ws.readyState === WebSocket.OPEN) ws.send(chunk);
                  }
                } else if (msg.data === 'disconnected') {
                  setStatus('Server reconnecting...', '');
                }
                break;
```

- [ ] **Step 4: Verify a normal recording session**

With `python3 -m http.server 8000` running, load `http://localhost:8000/index.html`, log in, press
Start, and speak. Confirm captions appear. In `modal app logs streaming-dictation`, confirm the line
`stream auth ok via first frame` and **not** `via legacy query param`.

- [ ] **Step 5: Verify a mid-recording reconnect still flushes correctly**

This is the one talk-critical path this plan touches. While recording, open DevTools and run
`ws.close()` in the console to force a reconnect. Confirm: the status shows the reconnect, then
`Listening...` returns, and captions resume without duplicating or dropping a whole utterance. Buffered
audio is capped at `AUDIO_BUFFER_MAX` (40 chunks, about 10 seconds), so a slow handshake trims the
oldest audio rather than growing without bound.

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "refactor: send WebSocket bearer token as the first frame

Keeps the token out of URLs and therefore out of proxy and CDN access
logs and browser history. The server still accepts the legacy query
parameter, so this does not require every client to have reloaded.

The buffered-audio flush moves from onopen to the 'listening' status.
At onopen the server has not yet entered forward_audio() and is not
calling ws.receive(), so audio sent then would sit in the socket buffer
and arrive late. 'listening' means Deepgram is connected and the read
loop is running. This matters most on mid-talk reconnects."
```

---

### Task 6: Client — version handshake, upgrade banner, visible version label

**Files:**
- Modify: `index.html:201-204` (auth gate markup), `index.html:222` (toolbar label), `index.html:229` area (constants), `checkAuth` from Task 4

**Interfaces:**
- Consumes: `min_client_epoch` from the `/auth` response (Task 2).
- Produces: `const CLIENT_EPOCH = 2`, `const VERSION_LABEL = 'v2026.08.24a'`, `function maybeOfferUpgrade(min)`, `function showUpgradeBanner(onReload)`.

- [ ] **Step 1: Add the version constants**

After the `AUTH_TIMEOUT_MS` line added in Task 4 Step 1, insert:

```javascript
      const CLIENT_EPOCH  = 2;                // integer, compared by code
      const VERSION_LABEL = 'v2026.08.24a';   // string, shown to humans
```

An integer is compared rather than the label because lexicographic comparison of
`v2026.08.24a`-style strings works today and breaks silently the first time the format changes or a
suffix passes `z`.

- [ ] **Step 2: Add the banner markup**

Immediately after `<body>` (`index.html:199`), insert:

```html
  <div id="upgradeBanner" style="display:none;position:fixed;top:0;left:0;right:0;z-index:999;
       background:#332900;color:#ffd24d;font-size:14px;padding:10px 14px;text-align:center">
    A newer version of this page is available.
    <button id="upgradeReload" style="margin-left:10px">Reload</button>
  </div>
```

- [ ] **Step 3: Make the version visible before login**

The label at `index.html:222` sits inside `<div id="app">`, which is `display:none` until login
succeeds — so a client that cannot log in cannot show its version, which is exactly when the version
matters. Add it to the auth gate too. Replace `index.html:201-204`:

```html
  <div class="auth-gate" id="authGate">
    <input type="password" id="passwordInput" placeholder="Enter password" autofocus>
    <button id="authSubmit">Connect</button>
  </div>
```

with:

```html
  <div class="auth-gate" id="authGate">
    <input type="password" id="passwordInput" placeholder="Enter password" autofocus>
    <button id="authSubmit">Connect</button>
    <span id="authVersion" style="font-size:10px;color:#555;margin-top:10px"></span>
  </div>
```

Then replace the hardcoded label at `index.html:222`:

```html
      <span style="font-size:10px;color:#555;margin-left:8px">v2026.04.19a</span>
```

with:

```html
      <span id="toolbarVersion" style="font-size:10px;color:#555;margin-left:8px"></span>
```

and populate both from the single constant, next to the other DOM wiring near `index.html:334`:

```javascript
      document.getElementById('authVersion').textContent = VERSION_LABEL;
      document.getElementById('toolbarVersion').textContent = VERSION_LABEL;
```

- [ ] **Step 4: Report the epoch and read the floor in `checkAuth`**

In the `fetch` call added in Task 4 Step 2, replace the `headers` line:

```javascript
            headers: { Authorization: 'Bearer ' + token },
```

with:

```javascript
            headers: {
              Authorization: 'Bearer ' + token,
              'X-Client-Version': String(CLIENT_EPOCH),
            },
```

and replace the `200` branch:

```javascript
          if (res.status === 200) return { ok: true };
```

with:

```javascript
          if (res.status === 200) {
            const body = await res.json().catch(() => ({}));
            if (typeof body.min_client_epoch === 'number' && CLIENT_EPOCH < body.min_client_epoch) {
              maybeOfferUpgrade(body.min_client_epoch);
            }
            return { ok: true };
          }
```

- [ ] **Step 5: Add the upgrade offer**

Next to `checkAuth`, add:

```javascript
      function maybeOfferUpgrade(min) {
        // Never interrupt a talk: a reload destroys the page, the MediaRecorder,
        // and the WebSocket, ending an in-progress recording.
        if (isRecording) return;
        const key = 'upgradeOffered:' + min;
        if (sessionStorage.getItem(key)) return;   // ask once, then stop
        showUpgradeBanner(() => {
          sessionStorage.setItem(key, '1');
          // Explicit cache-bust: a bare reload can be served from a still-fresh
          // max-age=600 entry, which would just show this banner again.
          // Built from pathname so the query string does not accumulate.
          location.replace(location.pathname + '?v=' + min);
        });
      }

      function showUpgradeBanner(onReload) {
        const banner = document.getElementById('upgradeBanner');
        banner.style.display = 'block';
        document.getElementById('upgradeReload').addEventListener('click', onReload, { once: true });
      }
```

- [ ] **Step 6: Verify the banner stays hidden when the client is current**

`MIN_CLIENT_EPOCH` is still `1` on the server and `CLIENT_EPOCH` is `2`, so `2 < 1` is false and no
banner should appear. With `python3 -m http.server 8000`, load `http://localhost:8000/index.html`, log
in, and confirm: no banner, and the version label `v2026.08.24a` is visible **both** on the auth gate
before login and in the toolbar after.

- [ ] **Step 7: Verify the banner appears when the client is behind**

Temporarily change `CLIENT_EPOCH` to `0` in `index.html`, reload, and log in. Expected: the banner
appears. Click Reload and confirm the URL gains `?v=1` and the banner does not reappear on the
resulting load. Then set `CLIENT_EPOCH` back to `2` and confirm the banner is gone again.

Also confirm `modal app logs streaming-dictation` shows `auth ok client_epoch=0` during the test —
that is the observability which makes retiring the legacy branch a checkable condition.

- [ ] **Step 8: Commit**

```bash
git add index.html
git commit -m "feat: report client epoch to /auth and offer a cache-busting reload

The client sends X-Client-Version so the server can log which generation
is connecting, which is what turns 'has every client reloaded?' from an
assumption into something checkable in modal app logs.

/auth returns a minimum acceptable epoch rather than the current one: the
server deploys ahead of GitHub Pages, so advertising 'current' would make
every healthy client reload into the older build and loop forever.

The reload is offered, never forced, and suppressed while recording,
because a reload would end an in-progress talk. A sessionStorage guard
bounds it to one offer per epoch.

The version label also moves onto the auth gate. It previously sat inside
the login-gated app div, so a client that could not log in could not show
its version — precisely when it is needed."
```

---

### Task 7: Service worker — the upgrade path

**Files:**
- Rewrite: `service-worker.js` (all 14 lines)

**Interfaces:**
- Consumes: `CLIENT_EPOCH = 2` from Task 6, mirrored as the cache name suffix.
- Produces: a worker that installs successfully under `/streaming-dictation/` and serves navigations network-first.

- [ ] **Step 1: Confirm the Pages checks are red**

Run: `python3 check_login.py --only pages`
Expected: `pages_sw_assets` FAILS with five `404`s, `pages_epoch_match` FAILS. These stay red until
Task 8 publishes, because they read the deployed site.

- [ ] **Step 2: Rewrite the worker**

Replace the entire contents of `service-worker.js`:

```javascript
// Bump CACHE in lockstep with CLIENT_EPOCH in index.html.
const CACHE  = 'dictation-e2';
// Relative, so they resolve against this worker's scope (/streaming-dictation/).
// The previous version used root-absolute paths, which 404'd and disabled the
// whole worker. manifest.json already gets this right.
const ASSETS = ['./', './index.html', './manifest.json', './icon-192.png', './icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // Individually rather than addAll: one missing asset must degrade offline
    // coverage, not fail the install and disable the worker entirely.
    await Promise.allSettled(ASSETS.map((u) => c.add(new Request(u, { cache: 'reload' }))));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    // Take control of already-open windows. For a standalone PWA, waiting for
    // every window to close could mean waiting forever.
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
        // cache:'reload' bypasses the 600s GitHub Pages HTTP cache, so a
        // controlled client is never stale.
        const fresh = await fetch(req.url, { cache: 'reload' });
        // waitUntil, not fire-and-forget: the worker can be terminated as soon as
        // respondWith settles, which would abandon the write mid-flight.
        e.waitUntil(caches.open(CACHE).then((c) => c.put('./', fresh.clone())));
        return fresh;
      } catch (_) {
        return (await caches.match('./')) || Response.error();   // offline fallback
      }
    })());
    return;
  }

  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
```

- [ ] **Step 3: Verify install succeeds locally**

With `python3 -m http.server 8000` running, load `http://localhost:8000/index.html`, then in the
DevTools console run:

```javascript
(async () => {
  const regs = await navigator.serviceWorker.getRegistrations();
  const names = await caches.keys();
  const entries = names.length ? (await (await caches.open(names[0])).keys()).map(r => r.url) : [];
  console.log({ regs: regs.length, controller: !!navigator.serviceWorker.controller, names, entries });
})();
```

Expected: `regs` is `1`, `controller` is `true`, `names` contains `dictation-e2`, and `entries` lists
five URLs. Before this change the equivalent probe on the live site returned `regs: 0`,
`controller: false`, and an **empty** `dictation-v1` cache — that empty cache is the signature of
`caches.open()` succeeding before `addAll()` rejected.

Note: the local origin is `localhost:8000`, so the precached URLs will be under that origin. This step
verifies install mechanics; Task 8 verifies the real paths on Pages.

- [ ] **Step 4: Verify network-first navigation**

Reload the page, then in DevTools Network confirm the document request was served from the network
rather than the HTTP cache. Then stop the `http.server` process and reload again: the page should
still render from the cache fallback.

- [ ] **Step 5: Commit**

```bash
git add service-worker.js
git commit -m "fix: repair service worker and serve navigations network-first

ASSETS used root-absolute paths, which resolve against the Pages domain
root rather than /streaming-dictation/. All five returned 404, addAll
rejected as a unit, install failed, and the worker never activated. The
dictation-v1 cache existed but was empty, because caches.open() succeeds
before addAll() rejects.

This is the upgrade path, not incidental cleanup: a browser running
frozen JavaScript still re-runs register() on every load, and the worker
script bypasses the HTTP cache on update checks. The page's code is
frozen; the worker it registers is not.

Fixing the paths alone would have been worse than the bug. Cache-first
with a static cache name would pin a standalone PWA to the first
index.html it ever cached, permanently, with no address bar to escape
through. Navigations are now network-first with cache:'reload', the cache
is versioned and purged on activate, and install tolerates a missing
asset instead of disabling itself."
```

---

### Task 8: Publish and arm the upgrade floor

**Files:**
- Modify: `modal_app.py` (`MIN_CLIENT_EPOCH`)

**Interfaces:**
- Consumes: every preceding task.
- Produces: a fully deployed system; `check_login.py` green end to end.

- [ ] **Step 1: Confirm the server is current and green**

```bash
modal deploy modal_app.py
python3 check_login.py --only modal
```

Expected: all ten Modal checks PASS.

- [ ] **Step 2: Publish the client**

`index.html` and `service-worker.js` must go out in the same push, so that the navigation which
updates a client's protocol also installs its future upgrade capability.

```bash
git push origin main
```

- [ ] **Step 3: Wait for Pages, then verify**

GitHub Pages caches for 600 seconds and a build takes a moment, so this is not immediate.

```bash
python3 check_login.py --only pages
```

Expected: `pages_sw_assets` PASSES with five reachable assets, `pages_epoch_match` PASSES with both
epochs reading `2`. Re-run until green; if `pages_epoch_match` reports mismatched values, one of the
two files did not publish and the constants have drifted.

- [ ] **Step 4: Confirm a real browser picks it up**

Load `https://bbuchalter.github.io/streaming-dictation/` and confirm the auth gate shows
`v2026.08.24a` before login. Recall the two-navigation rule: the first navigation after publishing
installs the worker but may still receive stale HTML, so if you see the old label, reload once more.

- [ ] **Step 5: Arm the floor**

Only now that Pages is confirmed serving epoch `2`, raise the floor in `modal_app.py`:

```python
MIN_CLIENT_EPOCH = 2
```

```bash
modal deploy modal_app.py
python3 check_login.py
```

Expected: all twelve checks PASS. Doing this before Step 3 is green is what the min-epoch design and
the `sessionStorage` guard exist to survive, but the ordering means neither has to.

- [ ] **Step 6: Commit**

```bash
git add modal_app.py
git commit -m "chore: raise MIN_CLIENT_EPOCH to 2 now that Pages serves epoch 2

Arms the upgrade banner for any client still on epoch 1. Deliberately
sequenced after confirming GitHub Pages actually serves the new build:
raising the floor while Pages still served the older client would tell
every healthy client it was stale."
git push origin main
```

- [ ] **Step 7: Watch for stale clients**

```bash
modal app logs streaming-dictation
```

Look for `stream auth ok via legacy query param — stale client` and for `client_epoch=1`. Their
absence over a few weeks of normal use, including at least one talk, is the gate for retiring the
legacy query-parameter branch — a separate commit, explicitly out of scope for this plan.

---

## Deferred

Not in this plan, tracked from the 2026-08-24 investigation:

- **Findings 3, 4, 5, 6 (Group A — diagnosability):** mid-session `4001` silently kills the session; the heartbeat sends a `PING` the server never reads, so it cannot detect a black-holed connection; five blanket `except Exception: pass` handlers on the server; unlimited reconnection masks permanent faults. Finding 6 consumes the `4003` close code this plan introduces — until then a `4003` close still falls through to `attemptReconnect()` and retries forever, though the error frame makes the cause visible.
- **Finding 12 (Group D — housekeeping):** `.env` carries dead `MODAL_POLISH_URL` and `REVAI_ACCESS_TOKEN` entries from the pre-Deepgram architecture, plus a `MODAL_BEARER_TOKEN` that no committed code reads — though `check_login.py` now does, so document it rather than delete it.
- **Retiring the legacy query-parameter branch**, gated on Task 8 Step 7 showing no legacy handshakes.
