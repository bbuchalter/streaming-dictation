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
