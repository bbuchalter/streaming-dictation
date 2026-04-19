# Claude Haiku Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the self-hosted Mistral 7B GPU inference in `modal_app.py` with a thin proxy that calls the Claude Haiku API, improving latency, quality, and operational simplicity.

**Architecture:** Modal becomes a CPU-only proxy — it validates the bearer token, calls the Anthropic Messages API with streaming, and relays tokens back to the browser as SSE events. Same auth, same SSE format, no frontend changes.

**Tech Stack:** Modal (Python, FastAPI, anthropic SDK), Claude Haiku (`claude-haiku-4-5-20251001`)

---

## File Structure

```
streaming-dictation/
├── modal_app.py              # REWRITE: Claude Haiku proxy (was Mistral 7B inference)
├── .env.example              # MODIFY: add ANTHROPIC_API_KEY
├── index.html                # UNCHANGED
├── create_vocabulary.py      # UNCHANGED
├── manifest.json             # UNCHANGED
├── service-worker.js         # UNCHANGED
├── icon-192.png              # UNCHANGED
├── icon-512.png              # UNCHANGED
└── .gitignore                # UNCHANGED
```

---

### Task 1: Create Modal Secret for Anthropic API Key

**Files:**
- None (Modal CLI operation)

- [ ] **Step 1: Create the `streaming-dictation-anthropic` secret**

```bash
modal secret create streaming-dictation-anthropic ANTHROPIC_API_KEY=<your-anthropic-api-key>
```

Replace `<your-anthropic-api-key>` with your actual Anthropic API key from https://console.anthropic.com/settings/keys.

Expected output: `Created secret 'streaming-dictation-anthropic'`

- [ ] **Step 2: Verify both secrets exist**

```bash
modal secret list
```

Expected: both `streaming-dictation-auth` and `streaming-dictation-anthropic` appear in the list.

---

### Task 2: Rewrite `modal_app.py` as Claude Haiku Proxy

**Files:**
- Rewrite: `modal_app.py`

- [ ] **Step 1: Replace `modal_app.py` with the Claude Haiku proxy**

Replace the entire file with:

```python
import modal

app = modal.App("streaming-dictation")

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "anthropic",
    "fastapi",
)

SYSTEM_PROMPT = """You are a transcription editor for live Buddhist Dharma talks.
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
  that were already in the previous segment"""


@app.cls(
    image=image,
    secrets=[
        modal.Secret.from_name("streaming-dictation-auth"),
        modal.Secret.from_name("streaming-dictation-anthropic"),
    ],
    container_idle_timeout=60,
    allow_concurrent_inputs=10,
)
class PolishModel:
    @modal.enter()
    def setup_client(self):
        import anthropic

        self.client = anthropic.Anthropic()

    @modal.asgi_app()
    def web(self):
        import os
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import StreamingResponse
        from pydantic import BaseModel

        web_app = FastAPI()

        web_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

        class PolishRequest(BaseModel):
            raw: str
            context: str = ""

        @web_app.post("/polish")
        async def polish(request: Request, body: PolishRequest):
            expected = os.environ["BEARER_TOKEN"]
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {expected}":
                raise HTTPException(status_code=401, detail="Unauthorized")

            if not body.raw.strip():
                def empty():
                    yield "data: [DONE]\n\n"
                return StreamingResponse(empty(), media_type="text/event-stream")

            user_message = f"Previous context: {body.context}\nRaw transcription: {body.raw}\nReturn only the cleaned text, nothing else."

            def generate():
                with self.client.messages.stream(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                ) as stream:
                    for text in stream.text_stream:
                        if text:
                            yield f"data: {text}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return web_app
```

Key changes from the old file:
- **Image:** `anthropic` + `fastapi` only (was `llama-cpp-python` + `fastapi` + `huggingface-hub` + model download)
- **No GPU:** removed `gpu="T4"` (CPU-only proxy)
- **Secrets:** added `streaming-dictation-anthropic` alongside existing `streaming-dictation-auth`
- **Container config:** `container_idle_timeout=60` (was 300), `allow_concurrent_inputs=10` (was 1)
- **`@modal.enter()`:** creates an `anthropic.Anthropic()` client (was loading Llama model from disk)
- **Inference:** `self.client.messages.stream()` (was `self.llm.create_chat_completion()`)
- **System prompt:** sent as the `system` parameter; context + raw sent as the `user` message (was all concatenated into one user message)
- **SSE format:** identical `data: text\n\n` events + `data: [DONE]\n\n` — frontend needs no changes

- [ ] **Step 2: Test locally with `modal serve`**

```bash
modal serve modal_app.py
```

Expected: Modal prints a URL like `https://<username>--streaming-dictation-polishmodel-web.modal.run`. No model download step — the image build should be fast (just pip installing two packages).

- [ ] **Step 3: Test with curl — successful polish**

Replace the URL with the one from Step 2:

```bash
curl -X POST https://<your-url>/polish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-bearer-token>" \
  -d '{"raw": "so when we practice metta meditation um we start with loving kindness toward ourselves you know", "context": ""}' \
  --no-buffer
```

Expected: SSE stream of polished tokens. The output should:
- Capitalize "Metta" 
- Remove "um" and "you know"
- Fix punctuation
- End with `data: [DONE]`

- [ ] **Step 4: Test with curl — auth rejection**

```bash
curl -X POST https://<your-url>/polish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer wrong-token" \
  -d '{"raw": "test", "context": ""}' \
  -w "\n%{http_code}"
```

Expected: `401` status code with `{"detail":"Unauthorized"}`.

- [ ] **Step 5: Test with curl — empty input**

```bash
curl -X POST https://<your-url>/polish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-bearer-token>" \
  -d '{"raw": "  ", "context": ""}' \
  --no-buffer
```

Expected: just `data: [DONE]` (empty input returns immediately).

- [ ] **Step 6: Commit**

```bash
git add modal_app.py
git commit -m "feat: replace Mistral 7B with Claude Haiku API proxy

Remove self-hosted LLM inference (GPU, model download, llama-cpp-python).
Modal now acts as a thin CPU-only proxy to the Anthropic Messages API.
Same auth, same SSE response format — no frontend changes needed."
```

---

### Task 3: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add `ANTHROPIC_API_KEY` to `.env.example`**

Replace the contents of `.env.example` with:

```
REVAI_ACCESS_TOKEN=your-rev-ai-access-token-here
MODAL_POLISH_URL=https://your-modal-app--streaming-dictation-polishmodel-web.modal.run
MODAL_BEARER_TOKEN=your-modal-bearer-token-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore: add ANTHROPIC_API_KEY to .env.example"
```

---

### Task 4: Deploy and End-to-End Test

**Files:**
- None (deployment and testing)

- [ ] **Step 1: Deploy to production**

```bash
modal deploy modal_app.py
```

Expected: prints the permanent production URL. Note whether this URL changed from the previous deployment — if the app name (`streaming-dictation`) and class name (`PolishModel`) are unchanged, the URL should be the same.

- [ ] **Step 2: Verify `MODAL_POLISH_URL` in `index.html`**

Open `index.html` and check that the `MODAL_POLISH_URL` constant on line 203 matches the deployed URL. If the URL changed, update it. If `MODAL_POLISH_URL` is still empty (`''`), set it now to the URL from Step 1.

- [ ] **Step 3: Run the full end-to-end test**

```bash
cd /Users/brian.buchalter/workspace/streaming-dictation
python -m http.server 8080
```

Open `http://localhost:8080` in Chrome. Enter your Modal bearer token as the password. Click Start.

**Test checklist:**
- [ ] Status shows "Connecting..." then "Listening..."
- [ ] Speaking produces polished captions within ~1-2 seconds
- [ ] Buddhist terms are correctly capitalized (say "metta", "vipassana", "dukkha")
- [ ] Filler words are removed (say "um", "you know", "like")
- [ ] Text accumulates correctly over multiple utterances
- [ ] Stop button stops the stream cleanly
- [ ] Start button reconnects successfully
- [ ] Clear and Export buttons work
- [ ] Wrong password returns 401 (polished text falls back to raw)

- [ ] **Step 4: Commit any fixes found during testing**

If any issues were found and fixed:

```bash
git add -A
git commit -m "fix: address issues found during end-to-end testing"
```

If no issues found, skip this step.
