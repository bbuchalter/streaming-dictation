# Server-Side Audio Routing: Design Revision

## Overview

Redesign the streaming dictation app so that the browser sends mic audio to Modal over a single WebSocket. Modal routes the audio server-to-server to Rev.ai, receives transcript finals, polishes them with Claude Haiku, and sends polished text back to the browser as JSON frames on the same WebSocket. This removes all API keys from the client and improves resilience on poor connections via Opus compression, local buffering, and automatic reconnection.

## Architecture

```
Browser                              Modal                         External
┌─────────────────┐           ┌─────────────────────┐
│                 │           │                     │
│  Mic → Opus ────┼──WS──────▶  Audio router        │
│  (MediaRecorder)│  binary   │     │                │
│                 │  frames   │     ▼                │
│                 │           │  Rev.ai WS ─────────┼──▶ Rev.ai STT
│                 │           │     │                │
│                 │           │  transcript final    │
│                 │           │     │                │
│                 │           │     ▼                │
│                 │           │  Claude Haiku ───────┼──▶ Anthropic API
│                 │           │     │                │
│                 │◀──WS──────┤  polished text       │
│  Caption display│  JSON     │  (JSON text frame)   │
│                 │  frames   │                     │
└─────────────────┘           └─────────────────────┘
```

**Single WebSocket, bidirectional:**
- Browser sends: Opus-encoded audio as binary frames (~24kbps)
- Modal sends: JSON text frames with polished text and status messages
- Auth via `token` query parameter on WebSocket connect
- All API keys (Rev.ai, Anthropic) stay server-side

## Browser Client

### Audio Capture

Replace the current `ScriptProcessorNode` → PCM16 conversion with `MediaRecorder` using Opus codec. This is simpler code and produces ~24kbps instead of ~256kbps (10x compression).

```javascript
const mediaRecorder = new MediaRecorder(micStream, {
  mimeType: 'audio/webm;codecs=opus',
  audioBitsPerSecond: 24000,
});
mediaRecorder.ondataavailable = (e) => {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(e.data);
  }
};
mediaRecorder.start(250); // send a chunk every 250ms
```

### Audio Level Meter

The audio level meter continues to work via an `AnalyserNode` connected to the mic stream (independent of MediaRecorder). The AnalyserNode computes frequency/amplitude data for the visual meter without touching the recording pipeline.

### WebSocket Protocol (Browser Side)

**Connect:** `wss://<modal-url>/stream?token=<bearer-token>`

**Send (browser → Modal):**
- Binary frames: Opus audio chunks (from MediaRecorder `ondataavailable`)
- Text frame: `"EOS"` to signal end of session

**Receive (Modal → browser):**
- `{"type":"text","data":"polished words here"}` — polished transcript segment, append to caption display
- `{"type":"status","data":"listening"}` — connection established, Rev.ai ready
- `{"type":"status","data":"disconnected"}` — Rev.ai dropped, Modal reconnecting
- `{"type":"error","data":"message"}` — error condition

### Authentication

On password entry, the browser opens a WebSocket to Modal. If the token is invalid, Modal closes the WebSocket with code 4001. If valid, Modal sends `{"type":"status","data":"listening"}` and the app shows the main UI.

This replaces the HTTP-based token verification — the WebSocket connection itself is the auth check.

### Reconnection with Buffering

On WebSocket drop:
1. Queue audio chunks in a local buffer (capped at ~10 seconds of Opus data, ~30KB)
2. Show "Reconnecting..." status
3. Attempt reconnect with exponential backoff: 1s, 2s, 4s, max 8s
4. On reconnect, flush the buffer (send queued chunks in order), then resume live streaming
5. After 3 failed reconnects, stop recording and show error

Audio older than the buffer cap is lost — acceptable for live captioning.

### What Gets Removed from the Frontend

- `REVAI_ACCESS_TOKEN` and `REVAI_VOCAB_ID` constants
- All Rev.ai WebSocket code (`buildRevaiUrl`, `onRevaiOpen`, `onRevaiMessage`, `onRevaiClose`)
- PCM16 audio encoding (`ScriptProcessorNode`, `DataView`, S16LE conversion)
- The `polishText()` function (fetch + SSE parsing)
- The `verifyToken()` function (HTTP-based auth check)
- `MODAL_POLISH_URL` constant (replaced by WebSocket URL)

### What Stays

- All UI controls: start/stop, clear, export, fullscreen, font sizing
- Audio level meter (rewired to `AnalyserNode`)
- `sessionStorage` token persistence
- `localStorage` transcript persistence
- Caption display logic (`appendText`, `renderDisplay`)
- CSS unchanged

### State

Minimal client state:
- `isRecording`: boolean
- `currentContext`: string (last ~50 words of polished output — still tracked client-side for display, but no longer sent to Modal; Modal tracks its own context server-side)
- `sessionToken`: string (from password entry, stored in sessionStorage)
- `ws`: WebSocket instance
- `mediaRecorder`: MediaRecorder instance
- `audioBuffer`: array of Blob chunks (for reconnection buffering)

## Modal Endpoint

### WebSocket `/stream` Handler

The Modal FastAPI app gets a new WebSocket endpoint at `/stream`. This is the core of the redesign.

**Lifecycle:**

1. Accept WebSocket connection from browser
2. Validate `token` query param against `BEARER_TOKEN` env var. If invalid, close with code 4001.
3. Open a WebSocket to Rev.ai server-to-server:
   - URL: `wss://api.rev.ai/speechtotext/v1/stream`
   - Params: `access_token`, `content_type=audio/webm;codecs=opus`, `remove_disfluencies=true`, `custom_vocabulary_id`
4. Send `{"type":"status","data":"listening"}` to browser
5. Run two concurrent async tasks:
   - **Audio forwarder:** Read binary frames from browser WebSocket, forward to Rev.ai WebSocket
   - **Transcript processor:** Read messages from Rev.ai WebSocket, process `final` elements → polish with Claude Haiku → send `{"type":"text","data":"..."}` to browser
6. On browser `EOS` text frame: send `EOS` to Rev.ai, wait for final transcripts, close
7. On browser disconnect: close Rev.ai connection, clean up
8. On Rev.ai disconnect: send `{"type":"status","data":"disconnected"}` to browser, attempt server-side reconnect to Rev.ai

### Polish (Inline)

When a `final` transcript arrives from Rev.ai:

1. Extract text from the `elements` array
2. Call `self.client.messages.create()` (non-streaming) with the system prompt, raw text, and accumulated context
3. Send polished result as `{"type":"text","data":"..."}` to browser
4. Update server-side context (last ~50 words of polished output)

Non-streaming is used because segments are short (~50 tokens). The full response arrives in ~0.3-0.5s. Streaming token-by-token over the WebSocket adds complexity for negligible UX benefit.

Context is tracked server-side per WebSocket session — the handler maintains a `context` string (last ~50 words of polished output) that persists for the duration of the connection. The browser does not send context.

### LLM System Prompt

Same system prompt as the current implementation (Plum Village tradition, comprehensive Buddhist terminology). No changes.

### Existing `/polish` Endpoint

Keep the existing HTTP `/polish` endpoint during development for testing. Remove it once the WebSocket flow is verified end-to-end.

## Deployment & Operations

### Modal Configuration

- Single `modal_app.py` file
- CPU-only (no GPU) — audio forwarding + API proxy
- Image: `debian_slim` + `anthropic` + `fastapi` + `websockets`
- Container idle timeout: 60 seconds (container stays alive for talk duration)
- Concurrency: 10 (one WebSocket per session, allows multiple concurrent sessions)
- Cold start: ~0.5s (just Python startup)
- Three Modal secrets:
  - `streaming-dictation-auth` — bearer token for browser auth
  - `streaming-dictation-anthropic` — Anthropic API key
  - `streaming-dictation-revai` — Rev.ai access token and vocabulary ID

### Cost

A 1-hour Dharma talk:
- Rev.ai streaming STT: ~$0.20/hr
- Modal compute (CPU, long-lived container): ~$0.05/hr
- Claude Haiku API: ~$0.01-0.02/hr (~10K tokens/hr)
- **Total: ~$0.27/hr**

### Latency Budget

| Step | Time |
|------|------|
| Audio upload (browser → Modal, Opus ~24kbps) | ~0.05-0.1s per chunk |
| Audio relay (Modal → Rev.ai, same cloud) | <5ms |
| Rev.ai STT (to final) | ~0.5-1.0s after utterance |
| Claude Haiku polish (non-streaming) | ~0.3-0.7s |
| Polished text back to browser (WS text frame) | <50ms |
| **Total: utterance to polished caption** | **~1.0-1.7s** |

### Reliability

- **Opus compression:** 10x less bandwidth than PCM16, works on poor connections
- **Client-side buffering:** survives brief dropouts (~10 seconds of audio)
- **Client reconnection:** exponential backoff, 3 retries before giving up
- **Server-side Rev.ai reconnect:** Modal reconnects to Rev.ai without involving the browser
- **Graceful degradation:** if polish fails, the segment is skipped (same as current behavior)

## Out of Scope

- Changing the LLM model or system prompt (unchanged from current)
- Changing the Rev.ai custom vocabulary (unchanged)
- Multi-user support
- Recording/playback of audio
- Client-side fallback to direct Rev.ai connection

## Technology Summary

| Component | Technology |
|-----------|-----------|
| Audio capture | MediaRecorder (Opus codec) |
| Audio format | WebM/Opus ~24kbps |
| Browser → server transport | WebSocket (binary + JSON text frames) |
| Server → Rev.ai transport | WebSocket (server-to-server) |
| STT service | Rev.ai Reverb (streaming WebSocket) |
| STT custom vocab | Rev.ai custom vocabulary |
| LLM model | Claude Haiku (`claude-haiku-4-5-20251001`) via Anthropic API |
| LLM transport | Anthropic Messages API (non-streaming) |
| Server platform | Modal (CPU-only, no GPU) |
| Auth | Bearer token via WebSocket query param |
| Frontend | Vanilla HTML/CSS/JS (single file) |
