# Deepgram Nova-3 STT Swap

## Overview

Replace Rev.ai with Deepgram Nova-3 as the speech-to-text provider. This is a backend-only change to `modal_app.py` — the frontend is unchanged. Deepgram accepts WebM/Opus natively, so the audio forwarding pipeline stays the same (binary frames forwarded as-is).

## Changes to `modal_app.py`

### Secret

Replace `streaming-dictation-revai` with `streaming-dictation-deepgram` (containing `DEEPGRAM_API_KEY`).

### WebSocket URL

Rev.ai: `wss://api.rev.ai/speechtotext/v1/stream?access_token=...&content_type=audio/webm;codecs=opus&remove_disfluencies=true&custom_vocabulary_id=...`

Deepgram: `wss://api.deepgram.com/v1/listen?model=nova-3&language=en&punctuate=true&smart_format=true&utterance_end_ms=1000`

No `encoding` or `sample_rate` params — Deepgram auto-detects from WebM container metadata.

### Authentication

Rev.ai: `access_token` query parameter.
Deepgram: `Authorization: Token <api-key>` header on WebSocket connection.

### Audio Forwarding

Unchanged. Opus binary frames forwarded as-is.

### Transcript Message Parsing

Rev.ai format:
```json
{"type": "final", "elements": [{"value": "hello "}, {"value": "world"}]}
```
Extract: `"".join(el["value"] for el in msg["elements"])`

Deepgram format:
```json
{"type": "Results", "is_final": true, "channel": {"alternatives": [{"transcript": "hello world"}]}}
```
Extract: `msg["channel"]["alternatives"][0]["transcript"]`

### Handshake

Rev.ai sends `{"type": "connected"}` after connection. Deepgram sends a metadata message. Adjust the initial check — or simply skip waiting for a specific handshake and send `{"type":"status","data":"listening"}` to the browser immediately after the WebSocket connects successfully.

### Close Stream

Rev.ai: send text frame `"EOS"`.
Deepgram: send JSON text frame `{"type": "CloseStream"}`.

## What Does NOT Change

- `index.html` — completely unchanged
- System prompt — unchanged
- Claude Haiku polish flow — unchanged
- Browser auth flow — unchanged
- Audio capture (MediaRecorder Opus) — unchanged
- Reconnection/buffering — unchanged
- Word-by-word streaming display — unchanged

## Cost

Deepgram Nova-3: ~$0.0043/minute = ~$0.26/hr (similar to Rev.ai at ~$0.20/hr).
