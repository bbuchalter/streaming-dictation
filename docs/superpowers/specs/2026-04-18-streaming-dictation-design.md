# Streaming Dictation: Real-Time Closed Captioning for Dharma Talks

## Overview

A web-based closed-captioning app for live Buddhist Dharma talks. A speaker runs the app on a dedicated screen visible to the audience. The browser captures mic audio, streams it to Rev.ai for speech-to-text, buffers the raw transcript, sends it through Claude Haiku (via a thin Modal proxy) for vocabulary polish, and displays the polished captions.

The UI reuses the existing `~/workspace/caption` project — large white text on a black background with fullscreen, font sizing, clear, and export controls.

## Architecture

```
Browser                        Rev.ai                Modal (API Proxy)
┌────────────────┐        ┌──────────────┐       ┌──────────────────┐
│                │        │              │       │                  │
│  Mic ──────────┼──WS───▶│  Streaming   │       │  POST /polish    │
│  (continuous)  │        │  STT         │       │       │          │
│                │        │  (Reverb)    │       │  Claude Haiku    │
│                │◀──WS───│              │       │  (Anthropic API) │
│  Buffer finals │        └──────────────┘       │       │          │
│       │        │                               │  SSE response    │
│       ▼        │                               │  (token stream)  │
│  POST /polish  ├──HTTP POST + context────────▶│                  │
│                │                               │                  │
│  ◀─────────────┼──────────SSE tokens───────────│                  │
│       │        │                               └──────────────────┘
│       ▼        │
│  Caption       │
│  Display       │
└────────────────┘
```

**Two external services, browser orchestrates:**

1. **Rev.ai** — streaming speech-to-text via WebSocket
2. **Modal** — thin API proxy that forwards polish requests to Claude Haiku (Anthropic API) and streams tokens back. No GPU, no self-hosted model.
3. **Browser** — captures audio, connects to both services, displays polished captions

There is no intermediate backend server. The browser talks directly to Rev.ai and Modal.

## Browser Client

### Audio Capture & Streaming

The browser captures mic audio and streams it directly to Rev.ai over a WebSocket connection. Rev.ai expects raw PCM audio (16-bit, 16kHz, mono). The Web Audio API's `AudioWorklet` or `ScriptProcessorNode` captures mic input and resamples/encodes it for Rev.ai's format.

### Transcript Flow

1. Rev.ai streams back two types of transcript elements:
   - `partial` — tentative, frequently revised (ignored)
   - `final` — committed, will not change (used)
2. When a `final` element arrives, the browser sends it to Modal for LLM polish
3. Modal streams back polished tokens via SSE
4. Polished tokens are appended to the caption display as they arrive

Only polished text is shown to the audience. There is no raw-then-replace behavior.

### Rev.ai Custom Vocabulary

Rev.ai supports up to 6,000 custom phrases. On WebSocket connection, the browser sends the custom vocabulary list as a connection parameter. This list contains Pali/Sanskrit Buddhist terminology:

```json
{
  "custom_vocabulary_id": "<pre-created-vocab-id>"
}
```

The vocabulary is created ahead of time via Rev.ai's REST API and referenced by ID during streaming sessions.

Example terms: Dharma, Dhamma, Sutta, Metta, Vipassana, Sangha, Dukkha, Anatta, Anicca, Jhana, Samadhi, Panna, Sila, Nibbana, Satipatthana, Anapanasati, Bodhisattva, Tathagata, Bhikkhu, Dana, Karuna, Mudita, Upekkha, etc.

### Authentication

On first visit, the app shows a password field. The entered password is stored in `sessionStorage` and sent as:
- `Authorization: Bearer <token>` header on Modal requests
- Rev.ai uses its own API token, stored as a constant in the client code (acceptable for personal use; the Rev.ai token is rate-limited and scoped to STT only)

The session password prevents unauthorized API usage via the Modal proxy. It persists across page reloads but clears when the tab is closed.

### Error Handling

- **Rev.ai WebSocket drops:** Attempt automatic reconnection with exponential backoff. Show "Reconnecting..." status. Audio during the gap is lost — acceptable.
- **Modal request fails:** Skip that segment, continue with the next. Show a subtle indicator (brief red dot) for dropped segments.
- **Silence:** Rev.ai sends no finals during silence. No action needed.

### UI Changes from Existing Caption App

- Replace the textarea input with a Start/Stop mic button in the toolbar
- Add a status indicator: "Ready", "Connecting...", "Listening...", "Reconnecting..."
- Keep all existing controls: fullscreen, font sizing (A-/A+), clear, export
- Remove the textarea entirely (no manual input mode)

### State

Minimal client state:
- `isRecording`: boolean
- `currentContext`: string (last ~50 words of polished output, for LLM context)
- `sessionToken`: string (from password entry, stored in sessionStorage)
- `revaiSocket`: WebSocket instance
- `pendingPolish`: number (count of in-flight polish requests, for status display)

## Modal Endpoint

### Model

**Claude Haiku** (`claude-haiku-4-5-20251001`) via the Anthropic Messages API. Modal acts as a thin proxy — no GPU, no self-hosted model. The endpoint validates the bearer token, forwards the request to the Anthropic API with streaming enabled, and relays tokens back as SSE.

### Endpoint: `POST /polish`

**Request:** JSON
```json
{
  "raw": "so when we look at the meta sutta we see that",
  "context": "...the practice of loving kindness. So when we look at the"
}
```

- `raw`: the final transcript segment from Rev.ai
- `context`: last ~50 words of previously polished output (for continuity)

**Response:** Server-Sent Events (SSE) streaming polished tokens

```
data: So
data: when
data: we
data: look
data: at
data: the
data: Metta
data: Sutta,
data: we
data: see
data: that
data: [DONE]
```

Each SSE `data` event contains one or more tokens. The client appends them to the display as they arrive. The final `[DONE]` event signals completion.

**Auth:** Validates `Authorization: Bearer <token>` header against a Modal secret. Returns 401 if invalid.

### LLM System Prompt

```
You are a transcription editor for live Buddhist Dharma talks.
You receive raw speech-to-text output and clean it up.

Rules:
- Fix punctuation and capitalization
- Remove filler words (um, uh, like, you know)
- Correct Buddhist terminology (e.g., "dharma" → "Dharma",
  "sutta" not "sutra" unless the speaker uses Mahayana terms,
  "metta", "vipassana", "sangha", "dukkha", "anatta",
  "anicca", "jhana", "samadhi", "panna", "sila", etc.)
- Preserve the speaker's words faithfully — do not restructure sentences
- If a sentence is cut off at the end, include it as-is
- Use the provided context for continuity — do not repeat words
  that were already in the previous segment

Previous context: {context}
Raw transcription: {raw}
Return only the cleaned text, nothing else.
```

### Processing Pipeline

1. Validate bearer token
2. Parse JSON body (`raw` and `context`)
3. If `raw` is empty, return empty SSE stream with `[DONE]`
4. Call `anthropic.Anthropic().messages.create()` with the system prompt, `raw`, `context`, `model="claude-haiku-4-5-20251001"`, and `stream=True`
5. Iterate over stream events; for each `content_block_delta` with text, emit an SSE `data:` event
6. Send `data: [DONE]` event

## Deployment & Operations

### Modal Configuration

- Single `modal_app.py` file
- CPU-only (no GPU) — the endpoint is a thin proxy to the Anthropic API
- Image: `debian_slim` + `anthropic` + `fastapi` (tiny, fast builds)
- Container idle timeout: 60 seconds (cheap CPU, no reason to keep warm long)
- Concurrency: 10 (no GPU contention, just HTTP forwarding)
- Cold start: ~0.5s (just Python startup, no model loading)
- Two Modal secrets:
  - `streaming-dictation-auth` — bearer token for browser auth (existing)
  - `streaming-dictation-anthropic` — Anthropic API key (new)

### Rev.ai Setup

- Create a Rev.ai account (free tier: 5 hours)
- Generate an API access token
- Create a custom vocabulary via the REST API with Buddhist terminology
- Store the access token and vocabulary ID in the client code

### Web Client Hosting

Static files — serve from anywhere:
- GitHub Pages
- Cloudflare Pages
- Local `python -m http.server` during development

### Development Workflow

- `modal serve modal_app.py` — local dev with hot reload for the proxy endpoint
- `modal deploy modal_app.py` — production deployment
- Edit system prompt in `modal_app.py` to update vocabulary corrections
- Update Rev.ai custom vocabulary via their REST API as needed
- No model downloads, GPU provisioning, or image rebuilds for model changes — switching Claude model versions is a one-line config change

### Cost

A 1-hour Dharma talk:
- Rev.ai streaming STT: ~$0.20/hr
- Modal compute (CPU): ~$0.01/hr
- Claude Haiku API: ~$0.01-0.02/hr (~10K tokens/hr)
- **Total: ~$0.22/hr**

Scales to zero between talks (Modal). Rev.ai and Claude API are pay-per-use.

### Latency Budget

| Step | Time |
|------|------|
| Rev.ai streaming STT (to final) | ~0.5-1.0s after utterance |
| Network: browser → Modal | ~0.1s |
| Claude Haiku first token | ~0.1-0.2s |
| Claude Haiku full response (~50 tokens) | ~0.3-0.5s |
| **Total: utterance to first polished word** | **~0.7-1.3s** |
| **Total: utterance to full polished segment** | **~1.0-1.7s** |

First request adds ~0.5s for Modal cold start (CPU-only container, no model loading).

## Out of Scope

- Multi-user accounts or authentication system (single shared password for Modal)
- Transcript storage backend (localStorage + file export is sufficient)
- Custom vocabulary editing UI (use Rev.ai REST API directly)
- Mobile mic testing
- Speaker diarization (single speaker assumed)
- Translation
- Raw transcript display or raw-then-polish display mode

## Technology Summary

| Component | Technology |
|-----------|-----------|
| Audio capture | Web Audio API (AudioWorklet) |
| Audio format | PCM 16-bit 16kHz mono |
| STT service | Rev.ai Reverb (streaming WebSocket) |
| STT custom vocab | Rev.ai custom vocabulary (up to 6,000 phrases) |
| LLM model | Claude Haiku (`claude-haiku-4-5-20251001`) via Anthropic API |
| LLM transport | HTTP POST → SSE response |
| LLM proxy | Modal (CPU-only, no GPU) |
| Auth (proxy) | Bearer token (shared secret) |
| Auth (STT) | Rev.ai API token |
| Frontend | Vanilla HTML/CSS/JS (single file) |
| Hosting (frontend) | Static file server |
