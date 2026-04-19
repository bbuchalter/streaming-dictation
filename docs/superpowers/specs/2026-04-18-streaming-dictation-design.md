# Streaming Dictation: Real-Time Closed Captioning for Dharma Talks

## Overview

A web-based closed-captioning app for live Buddhist Dharma talks. A speaker runs the app on a dedicated screen visible to the audience. The browser captures mic audio, sends it to a self-hosted transcription pipeline on Modal, and displays polished captions in real time.

The UI reuses the existing `~/workspace/caption` project — large white text on a black background with fullscreen, font sizing, clear, and export controls.

## Architecture

```
Browser (Client)                       Modal (GPU Backend)
┌──────────────────────┐              ┌──────────────────────┐
│                      │              │                      │
│  Mic (continuous)    │              │  POST /transcribe    │
│       │              │              │       │              │
│  MediaRecorder       │   HTTP POST  │  faster-whisper      │
│  (5s timeslice)  ────┼─────────────▶│  (large-v3)          │
│       │              │              │       │              │
│  Sequential queue    │   JSON resp  │  LLM polish          │
│  ◀───────────────────┼──────────────│  (Mistral 7B)        │
│       │              │              │       │              │
│  Caption display     │              │  Return polished     │
│                      │              │  text + context      │
└──────────────────────┘              └──────────────────────┘
```

Three components, no intermediate backend:

1. **Browser client** — captures audio, sends chunks, displays captions
2. **Modal endpoint** — runs STT + LLM polish on a single GPU
3. **Caption display** — adapted from the existing caption project

## Browser Client

### Audio Capture

The mic runs continuously via `MediaRecorder` with a `timeslice` of 5000ms. This fires a `dataavailable` event every 5 seconds without interrupting the mic stream. Each event yields an audio blob (webm/opus format).

### Request Pipeline

Each audio chunk is POSTed to the Modal endpoint as `multipart/form-data`:
- `audio`: the webm blob
- `context`: the last ~50 words of the previous chunk's polished output (for boundary smoothing)

Responses are processed in order via a sequential queue — if chunk 3 returns before chunk 2, it waits. This prevents out-of-order text on the display.

### Authentication

On first visit, the app shows a password field. The entered password is stored in `sessionStorage` and sent as a `Authorization: Bearer <token>` header on every request. This prevents unauthorized GPU usage. The token persists across page reloads but clears when the tab is closed.

### Error Handling

If a request fails (network error, timeout, 500), skip that chunk and continue. A subtle visual indicator (e.g., a brief red dot) signals a dropped chunk. A 5-second gap is acceptable; stopping captioning is not.

### UI Changes from Existing Caption App

- Replace the textarea input with a Start/Stop mic button in the toolbar
- Add a status indicator: "Ready", "Listening...", "Processing..."
- Keep all existing controls: fullscreen, font sizing (A-/A+), clear, export
- The textarea remains hidden as a potential manual fallback

### State

Minimal client state:
- `isRecording`: boolean
- `currentContext`: string (tail of last polished response)
- `requestQueue`: ordered queue of pending/completed chunk responses
- `sessionToken`: string (from password entry, stored in sessionStorage)

## Modal Endpoint

### Models

Both models run on a single A10G GPU (24GB VRAM):

- **faster-whisper large-v3** (~3GB VRAM) — best accuracy for speech-to-text
- **Mistral 7B Instruct, Q4 quantized** (~5GB VRAM) — lightweight LLM for transcript polish

Models are downloaded at image build time via `Image.run_commands()`, not on cold start.

### Endpoint: `POST /transcribe`

**Request:** `multipart/form-data`
- `audio`: webm/opus audio blob (~5 seconds)
- `context` (optional): string, last ~50 words of previous polished output

**Response:** JSON
```json
{
  "raw": "so when we look at the meta sutta we see that...",
  "polished": "So when we look at the Metta Sutta, we see that...",
  "context": "we look at the Metta Sutta, we see that..."
}
```

- `raw`: unmodified faster-whisper output (for debugging)
- `polished`: LLM-cleaned text for display
- `context`: tail of polished text for the client to send with the next chunk

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
- Use the provided context to smooth over chunk boundaries
  (avoid repeating words that were already in the previous chunk)

Previous context: {context}
Raw transcription: {raw}
Return only the cleaned text, nothing else.
```

### Processing Pipeline

1. Receive audio blob
2. Decode webm to PCM audio (ffmpeg, bundled in the Modal image)
3. Run faster-whisper large-v3 transcription
4. If raw transcript is empty (silence), return `{"raw": "", "polished": "", "context": previous_context}`
5. Format LLM prompt with raw transcript + context
6. Run Mistral 7B inference
7. Extract last ~50 words as new context
8. Return JSON response

## Deployment & Operations

### Modal Configuration

- Single `modal.py` file
- GPU: A10G (24GB VRAM, ~$1.10/hr)
- Container timeout: 5 minutes idle before scale-to-zero
- Concurrency: 1 (single speaker, sequential chunks)
- Models baked into image for fast cold starts (~2-3s)
- One Modal secret: the bearer token

### Web Client Hosting

Static files — serve from anywhere:
- GitHub Pages
- Cloudflare Pages
- Local `python -m http.server` during development

### Development Workflow

- `modal serve modal.py` — local dev with hot reload
- `modal deploy modal.py` — production deployment
- Edit system prompt in `modal.py` to update vocabulary corrections

### Cost

A 1-hour Dharma talk costs approximately $1.10 (A10G GPU time). Scales to zero between talks.

## Chunk Boundary Strategy

Word splitting at 5-second chunk boundaries is handled by two mechanisms:

1. **Whisper robustness** — faster-whisper tends to drop partial words at boundaries rather than hallucinate. The next chunk picks up the missing word naturally.
2. **LLM context window** — each request includes the last ~50 words from the previous chunk's polished output. The LLM uses this to smooth over boundaries, avoid word repetition, and complete partial thoughts.

No overlapping audio buffers or complex ring buffer logic needed.

## Out of Scope

- Multi-user accounts or authentication system (single shared password)
- Transcript storage backend (localStorage + file export is sufficient)
- Custom vocabulary editing UI (edit the system prompt in `modal.py`)
- Mobile mic testing
- Speaker diarization (single speaker assumed)
- Translation

## Technology Summary

| Component | Technology |
|-----------|-----------|
| Audio capture | MediaRecorder API (timeslice) |
| Audio format | webm/opus |
| Transport | HTTP POST (fetch), JSON response |
| STT model | faster-whisper large-v3 |
| LLM model | Mistral 7B Instruct (Q4) |
| GPU platform | Modal (A10G) |
| Auth | Bearer token (shared secret) |
| Frontend | Vanilla HTML/CSS/JS (single file) |
| Hosting (frontend) | Static file server |
