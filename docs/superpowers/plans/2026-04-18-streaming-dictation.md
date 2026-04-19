# Streaming Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web-based closed-captioning app that streams mic audio to Rev.ai for speech-to-text, polishes transcripts through a self-hosted Mistral 7B on Modal, and displays polished captions in real time.

**Architecture:** Browser captures mic audio via Web Audio API, streams PCM16 over WebSocket to Rev.ai. Final transcript segments are POSTed to a Modal endpoint running Mistral 7B Q4, which streams polished tokens back via SSE. The browser appends polished tokens to a fullscreen caption display.

**Tech Stack:** Vanilla HTML/CSS/JS (single file), Rev.ai Streaming API (WebSocket), Modal (Python, FastAPI, llama-cpp-python), Mistral 7B Instruct Q4 GGUF

---

## File Structure

```
streaming-dictation/
├── index.html              # Complete frontend app (single file)
├── .env.example            # Template for environment variables
├── .gitignore              # Ignore .env, __pycache__, etc.
├── modal_app.py            # Modal endpoint: LLM polish via SSE
├── create_vocabulary.py    # One-time script: create Rev.ai custom vocabulary
├── manifest.json           # PWA manifest (from caption app)
├── service-worker.js       # PWA service worker (from caption app)
├── icon-192.png            # PWA icon (from caption app)
└── icon-512.png            # PWA icon (from caption app)
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `.gitignore`
- Create: `.env.example`

- [ ] **Step 1: Create `.gitignore`**

```
.env
__pycache__/
*.pyc
.modal/
```

- [ ] **Step 2: Create `.env.example`**

```
REVAI_ACCESS_TOKEN=your-rev-ai-access-token-here
MODAL_POLISH_URL=https://your-modal-app--polish.modal.run
MODAL_BEARER_TOKEN=your-modal-bearer-token-here
```

- [ ] **Step 3: Create `.env` with real tokens**

Copy `.env.example` to `.env` and fill in the Rev.ai access token. The Modal URL and bearer token will be filled in after Task 3.

```bash
cp .env.example .env
# Edit .env with your Rev.ai token
```

Note: `.env` is for local reference only — the frontend reads these values from `sessionStorage` (entered by the user) or from constants in the HTML. The `.env` file is not loaded at runtime.

- [ ] **Step 4: Commit**

```bash
git add .gitignore .env.example
git commit -m "feat: add project scaffolding with gitignore and env template"
```

---

### Task 2: Modal LLM Polish Endpoint

**Files:**
- Create: `modal_app.py`

This is the backend: a Modal app hosting Mistral 7B Q4 that receives raw transcript text and streams polished tokens back via SSE.

- [ ] **Step 1: Install Modal CLI and authenticate**

```bash
pip install modal
modal token set --token-id ak-lkVCQMe9GchSIp3oOYY1Tm --token-secret as-MXtBiQrnfXlBaLW58zQe5L
```

Verify with:
```bash
modal profile current
```
Expected: shows your Modal username.

- [ ] **Step 2: Create the Modal secret for the bearer token**

```bash
modal secret create streaming-dictation-auth BEARER_TOKEN=changeme-to-a-real-secret
```

Pick a real secret value — this is what you'll enter in the browser password field. Replace `changeme-to-a-real-secret` with your chosen password.

- [ ] **Step 3: Write `modal_app.py`**

```python
import modal

app = modal.App("streaming-dictation")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "llama-cpp-python",
        "fastapi",
        "huggingface-hub",
    )
    .run_commands(
        "huggingface-cli download TheBloke/Mistral-7B-Instruct-v0.2-GGUF"
        " mistral-7b-instruct-v0.2.Q4_K_M.gguf"
        " --local-dir /models"
        " --local-dir-use-symlinks False"
    )
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
  that were already in the previous segment

Previous context: {context}
Raw transcription: {raw}
Return only the cleaned text, nothing else."""


@app.cls(
    image=image,
    gpu="T4",
    secrets=[modal.Secret.from_name("streaming-dictation-auth")],
    container_idle_timeout=300,
    allow_concurrent_inputs=1,
)
class PolishModel:
    @modal.enter()
    def load_model(self):
        from llama_cpp import Llama

        self.llm = Llama(
            model_path="/models/mistral-7b-instruct-v0.2.Q4_K_M.gguf",
            n_ctx=2048,
            n_gpu_layers=-1,
        )

    @modal.fastapi_endpoint(method="POST")
    def polish(self, request: dict):
        import os
        from fastapi import HTTPException, Request
        from fastapi.responses import StreamingResponse

        # This method receives the parsed JSON body as `request` dict.
        # For auth, we need the raw request — use a different approach.
        # See Step 4 for the corrected version with auth.
        raw = request.get("raw", "")
        context = request.get("context", "")

        if not raw.strip():
            def empty():
                yield "data: [DONE]\n\n"
            return StreamingResponse(empty(), media_type="text/event-stream")

        prompt = SYSTEM_PROMPT.format(context=context, raw=raw)
        messages = [
            {"role": "user", "content": prompt}
        ]

        def generate():
            response = self.llm.create_chat_completion(
                messages=messages,
                max_tokens=256,
                stream=True,
            )
            for chunk in response:
                delta = chunk["choices"][0]["delta"]
                if "content" in delta:
                    text = delta["content"]
                    if text:
                        yield f"data: {text}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 4: Add bearer token authentication**

The `@modal.fastapi_endpoint` approach doesn't give us access to request headers directly when using a `dict` body. Switch to an `@modal.asgi_app()` approach with a full FastAPI app for proper auth:

Replace the entire `modal_app.py` with:

```python
import modal

app = modal.App("streaming-dictation")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "llama-cpp-python",
        "fastapi",
        "huggingface-hub",
    )
    .run_commands(
        "huggingface-cli download TheBloke/Mistral-7B-Instruct-v0.2-GGUF"
        " mistral-7b-instruct-v0.2.Q4_K_M.gguf"
        " --local-dir /models"
        " --local-dir-use-symlinks False"
    )
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
  that were already in the previous segment

Previous context: {context}
Raw transcription: {raw}
Return only the cleaned text, nothing else."""


@app.cls(
    image=image,
    gpu="T4",
    secrets=[modal.Secret.from_name("streaming-dictation-auth")],
    container_idle_timeout=300,
    allow_concurrent_inputs=1,
)
class PolishModel:
    @modal.enter()
    def load_model(self):
        from llama_cpp import Llama

        self.llm = Llama(
            model_path="/models/mistral-7b-instruct-v0.2.Q4_K_M.gguf",
            n_ctx=2048,
            n_gpu_layers=-1,
        )

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
            # Validate bearer token
            expected = os.environ["BEARER_TOKEN"]
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {expected}":
                raise HTTPException(status_code=401, detail="Unauthorized")

            if not body.raw.strip():
                def empty():
                    yield "data: [DONE]\n\n"
                return StreamingResponse(empty(), media_type="text/event-stream")

            prompt = SYSTEM_PROMPT.format(context=body.context, raw=body.raw)
            messages = [{"role": "user", "content": prompt}]

            def generate():
                response = self.llm.create_chat_completion(
                    messages=messages,
                    max_tokens=256,
                    stream=True,
                )
                for chunk in response:
                    delta = chunk["choices"][0]["delta"]
                    if "content" in delta:
                        text = delta["content"]
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

- [ ] **Step 5: Test locally with `modal serve`**

```bash
modal serve modal_app.py
```

Expected: Modal prints a URL like `https://<username>--streaming-dictation-polishmodel-web.modal.run`. It will take a few minutes on first run as it downloads the model.

Test with curl (replace the URL and token):

```bash
curl -X POST https://<your-url>/polish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer changeme-to-a-real-secret" \
  -d '{"raw": "so when we practice metta meditation we start with loving kindness toward ourselves", "context": ""}' \
  --no-buffer
```

Expected: SSE stream of polished tokens, ending with `data: [DONE]`.

Test auth rejection:

```bash
curl -X POST https://<your-url>/polish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer wrong-token" \
  -d '{"raw": "test", "context": ""}' \
  -w "\n%{http_code}"
```

Expected: `401` status code.

- [ ] **Step 6: Deploy to production**

```bash
modal deploy modal_app.py
```

Expected: prints the permanent production URL. Save this URL — it goes in the frontend config.

- [ ] **Step 7: Commit**

```bash
git add modal_app.py
git commit -m "feat: add Modal LLM polish endpoint with Mistral 7B"
```

---

### Task 3: Rev.ai Custom Vocabulary

**Files:**
- Create: `create_vocabulary.py`

A one-time script to create a custom vocabulary in Rev.ai for Buddhist terminology.

- [ ] **Step 1: Write `create_vocabulary.py`**

```python
"""One-time script to create a Rev.ai custom vocabulary for Buddhist terminology."""

import json
import os
import sys
import urllib.request

REVAI_TOKEN = os.environ.get("REVAI_ACCESS_TOKEN")
if not REVAI_TOKEN:
    print("Set REVAI_ACCESS_TOKEN environment variable")
    sys.exit(1)

# Buddhist/Pali/Sanskrit terminology
# Each entry can be a simple string or {"phrase": "...", "weight": N}
# Weight ranges from 1-5, default is 1. Higher = stronger bias.
PHRASES = [
    # Core concepts
    "Dharma", "Dhamma", "Sangha", "Buddha",
    "Sutta", "Sutra", "Vinaya", "Abhidhamma",
    # Meditation
    "Vipassana", "Samatha", "Jhana", "Samadhi",
    "Satipatthana", "Anapanasati", "Metta", "Karuna",
    "Mudita", "Upekkha",
    # Key teachings
    "Dukkha", "Anatta", "Anicca", "Nibbana", "Nirvana",
    "Sila", "Panna", "Prajna",
    # Practice terms
    "Dana", "Bhavana", "Sati", "Mindfulness",
    "Bodhisattva", "Tathagata", "Bhikkhu", "Bhikkhuni",
    "Sangha", "Arhat", "Arahant",
    # Common Pali phrases
    "Sadhu", "Namo Tassa", "Buddham Saranam Gacchami",
    # Path factors
    "Samma", "Right View", "Right Intention",
    "Noble Eightfold Path", "Four Noble Truths",
    "Dependent Origination", "Paticca Samuppada",
    # Hindrances and factors
    "Kilesa", "Nivarana", "Bojjhanga",
    "Khanda", "Skandha", "Vedana", "Sanna", "Sankhara", "Vinnana",
]

url = "https://api.rev.ai/speechtotext/v1/vocabularies"
headers = {
    "Authorization": f"Bearer {REVAI_TOKEN}",
    "Content-Type": "application/json",
}
data = json.dumps({
    "custom_vocabularies": [{"phrases": PHRASES}],
}).encode("utf-8")

req = urllib.request.Request(url, data=data, headers=headers, method="POST")

try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        print(f"Vocabulary created!")
        print(f"  ID: {result['id']}")
        print(f"  Status: {result['status']}")
        print(f"  Created: {result.get('created_on', 'N/A')}")
        print(f"\nSave this ID — you'll need it for the frontend config.")
        print(f"Check status: curl -H 'Authorization: Bearer $REVAI_ACCESS_TOKEN' "
              f"https://api.rev.ai/speechtotext/v1/vocabularies/{result['id']}")
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode('utf-8')}")
    sys.exit(1)
```

- [ ] **Step 2: Run the script to create the vocabulary**

```bash
REVAI_ACCESS_TOKEN=020Ukq9BiI9c7k51wPFDoWe605MGfnfttjZtNzJYpQvo6LPH0716RSzvu0Ae-fcnLYL277Ur5IqCUDRRqMdqbfqLFxLAQ python create_vocabulary.py
```

Expected: prints a vocabulary ID. Save this — it goes in the frontend.

Note: vocabulary creation is async. Check the status endpoint until `status` is `"complete"` before using it in streaming sessions. This typically takes a few seconds.

- [ ] **Step 3: Commit**

```bash
git add create_vocabulary.py
git commit -m "feat: add Rev.ai custom vocabulary script for Buddhist terminology"
```

---

### Task 4: Frontend — Static Caption Display

**Files:**
- Create: `index.html`

Build the frontend in stages. This task creates the base UI (adapted from the caption app) with the password gate and config, but no audio or Rev.ai integration yet.

- [ ] **Step 1: Create `index.html` with base UI and password gate**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="theme-color" content="#000000">
  <title>Streaming Dictation</title>
  <style>
    *, *::before, *::after {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }

    html, body {
      height: 100%;
      background: #000000;
      color: #FFFFFF;
      font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif;
      overflow: hidden;
    }

    .app {
      display: flex;
      flex-direction: column;
      height: 100vh;
    }

    /* Password gate */
    .auth-gate {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100vh;
      gap: 12px;
    }

    .auth-gate input {
      background: #1a1a1a;
      color: #FFFFFF;
      border: 1px solid #333333;
      border-radius: 6px;
      padding: 12px 16px;
      font-size: 16px;
      font-family: inherit;
      width: 280px;
      outline: none;
    }

    .auth-gate input:focus {
      border-color: #2563eb;
    }

    .auth-gate button {
      background: #2563eb;
      color: #FFFFFF;
      border: none;
      padding: 10px 24px;
      border-radius: 6px;
      font-size: 16px;
      cursor: pointer;
      font-family: inherit;
    }

    .auth-gate button:hover {
      background: #1d4ed8;
    }

    /* Caption display */
    .caption-display {
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      padding: 24px 32px;
      overflow: hidden;
      position: relative;
    }

    .caption-display::before {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 40%;
      background: linear-gradient(to bottom, #000000 0%, transparent 100%);
      pointer-events: none;
      z-index: 1;
    }

    .caption-text {
      font-size: var(--caption-size, 5vw);
      font-weight: 700;
      line-height: 1.5;
      word-wrap: break-word;
      overflow-wrap: break-word;
      white-space: pre-wrap;
    }

    /* Toolbar */
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      background: #111111;
      border-top: 1px solid #333333;
    }

    .toolbar-spacer {
      flex: 1;
    }

    .toolbar button {
      background: #333333;
      color: #FFFFFF;
      border: none;
      padding: 6px 14px;
      border-radius: 4px;
      font-size: 14px;
      cursor: pointer;
      font-family: inherit;
    }

    .toolbar button:hover {
      background: #444444;
    }

    .toolbar .btn-start {
      background: #16a34a;
    }

    .toolbar .btn-start:hover {
      background: #15803d;
    }

    .toolbar .btn-stop {
      background: #dc2626;
    }

    .toolbar .btn-stop:hover {
      background: #b91c1c;
    }

    .toolbar .btn-fullscreen {
      background: #2563eb;
    }

    .toolbar .btn-fullscreen:hover {
      background: #1d4ed8;
    }

    /* Status indicator */
    .status {
      font-size: 13px;
      color: #888888;
    }

    .status.listening {
      color: #16a34a;
    }

    .status.error {
      color: #dc2626;
    }
  </style>
</head>
<body>
  <!-- Auth gate -->
  <div class="auth-gate" id="authGate">
    <input type="password" id="passwordInput" placeholder="Enter password" autofocus>
    <button id="authSubmit">Connect</button>
  </div>

  <!-- Main app (hidden until authenticated) -->
  <div class="app" id="app" style="display: none;">
    <div class="caption-display" id="captionDisplay">
      <div class="caption-text" id="captionText"></div>
    </div>

    <div class="toolbar" id="toolbar">
      <button class="btn-start" id="btnMic">Start</button>
      <span class="status" id="status">Ready</span>
      <button class="btn-clear" id="btnClear">Clear</button>
      <button class="btn-export" id="btnExport">Export</button>
      <div class="toolbar-spacer"></div>
      <button id="btnSizeDown">A-</button>
      <button id="btnSizeUp">A+</button>
      <button class="btn-fullscreen" id="btnFullscreen">Fullscreen</button>
    </div>
  </div>

  <script>
    (function () {
      // ── Config ──
      // Rev.ai access token and custom vocabulary ID.
      // For personal use, these are stored as constants.
      const REVAI_ACCESS_TOKEN = '020Ukq9BiI9c7k51wPFDoWe605MGfnfttjZtNzJYpQvo6LPH0716RSzvu0Ae-fcnLYL277Ur5IqCUDRRqMdqbfqLFxLAQ';
      const REVAI_VOCAB_ID = ''; // Fill in after running create_vocabulary.py
      const MODAL_POLISH_URL = ''; // Fill in after deploying modal_app.py

      const BUFFER_LIMIT = 5000;
      const STORAGE_KEY = 'dictation-transcript';

      // ── DOM ──
      const authGate = document.getElementById('authGate');
      const passwordInput = document.getElementById('passwordInput');
      const authSubmit = document.getElementById('authSubmit');
      const appEl = document.getElementById('app');
      const captionText = document.getElementById('captionText');
      const captionDisplay = document.getElementById('captionDisplay');
      const btnMic = document.getElementById('btnMic');
      const btnClear = document.getElementById('btnClear');
      const btnExport = document.getElementById('btnExport');
      const btnFullscreen = document.getElementById('btnFullscreen');
      const btnSizeDown = document.getElementById('btnSizeDown');
      const btnSizeUp = document.getElementById('btnSizeUp');
      const statusEl = document.getElementById('status');

      // ── State ──
      let sessionToken = sessionStorage.getItem('sessionToken') || '';
      let isRecording = false;
      let currentContext = '';
      let transcript = localStorage.getItem(STORAGE_KEY) || '';

      // ── Font size ──
      const SIZES = [3, 4, 5, 6, 7, 8, 10];
      let sizeIndex = 2;

      function applySize() {
        document.documentElement.style.setProperty('--caption-size', SIZES[sizeIndex] + 'vw');
      }

      // ── Auth ──
      function authenticate() {
        const token = passwordInput.value.trim();
        if (!token) return;
        sessionToken = token;
        sessionStorage.setItem('sessionToken', token);
        showApp();
      }

      function showApp() {
        authGate.style.display = 'none';
        appEl.style.display = 'flex';
        renderDisplay();
      }

      authSubmit.addEventListener('click', authenticate);
      passwordInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') authenticate();
      });

      // Auto-login if token exists
      if (sessionToken) {
        showApp();
      }

      // ── Caption display ──
      function renderDisplay() {
        // Show the tail of the transcript that fits the buffer
        const tail = transcript.length > BUFFER_LIMIT
          ? transcript.slice(transcript.length - BUFFER_LIMIT)
          : transcript;
        captionText.textContent = tail;
        captionDisplay.scrollTop = captionDisplay.scrollHeight;
      }

      function appendText(text) {
        if (!text) return;
        // Add a space before appending if transcript doesn't end with whitespace
        if (transcript.length > 0 && !transcript.endsWith(' ') && !transcript.endsWith('\n')) {
          transcript += ' ';
        }
        transcript += text;
        localStorage.setItem(STORAGE_KEY, transcript);
        renderDisplay();
      }

      // ── Toolbar: Clear ──
      btnClear.addEventListener('click', (e) => {
        e.stopPropagation();
        transcript = '';
        currentContext = '';
        localStorage.removeItem(STORAGE_KEY);
        captionText.textContent = '';
      });

      // ── Toolbar: Export ──
      btnExport.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!transcript) return;
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        const timestamp = [
          now.getFullYear(), pad(now.getMonth() + 1), pad(now.getDate()),
          '-', pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds())
        ].join('');
        const blob = new Blob([transcript], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `dharma-talk-${timestamp}.txt`;
        a.click();
        URL.revokeObjectURL(url);
      });

      // ── Toolbar: Fullscreen ──
      btnFullscreen.addEventListener('click', (e) => {
        e.stopPropagation();
        if (document.fullscreenElement) {
          document.exitFullscreen();
        } else {
          document.documentElement.requestFullscreen();
        }
      });

      document.addEventListener('fullscreenchange', () => {
        btnFullscreen.textContent = document.fullscreenElement ? 'Exit Fullscreen' : 'Fullscreen';
      });

      // ── Toolbar: Font size ──
      btnSizeDown.addEventListener('click', (e) => {
        e.stopPropagation();
        if (sizeIndex > 0) { sizeIndex--; applySize(); }
      });

      btnSizeUp.addEventListener('click', (e) => {
        e.stopPropagation();
        if (sizeIndex < SIZES.length - 1) { sizeIndex++; applySize(); }
      });

      // ── Status ──
      function setStatus(text, cls) {
        statusEl.textContent = text;
        statusEl.className = 'status' + (cls ? ' ' + cls : '');
      }

      // ── Mic button (placeholder — wired up in Task 5) ──
      btnMic.addEventListener('click', () => {
        // Will be replaced with real start/stop logic
        setStatus('Not yet implemented');
      });

      // ── Polish endpoint ──
      async function polishText(raw) {
        if (!raw.trim()) return;

        try {
          const resp = await fetch(MODAL_POLISH_URL + '/polish', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${sessionToken}`,
            },
            body: JSON.stringify({ raw, context: currentContext }),
          });

          if (!resp.ok) {
            console.error('Polish failed:', resp.status);
            // Fall back to raw text
            appendText(raw);
            return;
          }

          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let polished = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const text = decoder.decode(value, { stream: true });
            const lines = text.split('\n');

            for (const line of lines) {
              if (!line.startsWith('data: ')) continue;
              const data = line.slice(6);
              if (data === '[DONE]') continue;
              polished += data;
              appendText(data);
            }
          }

          // Update context: last ~50 words
          const words = polished.split(/\s+/);
          currentContext = words.slice(-50).join(' ');
        } catch (err) {
          console.error('Polish error:', err);
          // Fall back to raw text
          appendText(raw);
        }
      }

      // Expose polishText for Task 5 to use
      window.__polishText = polishText;
      window.__setStatus = setStatus;
      window.__appendText = appendText;

    })();
  </script>
</body>
</html>
```

- [ ] **Step 2: Test the password gate**

```bash
cd /Users/brian.buchalter/workspace/streaming-dictation
python -m http.server 8080
```

Open `http://localhost:8080` in Chrome. Verify:
- Password field appears
- Entering a password and clicking Connect (or pressing Enter) shows the main app
- Refreshing the page auto-logs in (sessionStorage persists)
- Closing and reopening the tab requires re-entering the password

- [ ] **Step 3: Test the caption display**

Open the browser console and run:

```javascript
window.__appendText("So when we look at the Metta Sutta, we see that the practice of loving kindness begins with ourselves.");
```

Verify: text appears in large white font at the bottom of the screen. Run it multiple times — text should accumulate.

Test clear and export buttons work.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat: add frontend with password gate, caption display, and polish client"
```

---

### Task 5: Frontend — Rev.ai Audio Streaming

**Files:**
- Modify: `index.html` (add audio capture and Rev.ai WebSocket integration)

This task wires up the mic to Rev.ai and connects final transcripts to the polish pipeline.

- [ ] **Step 1: Add audio capture and Rev.ai WebSocket code**

In `index.html`, find the comment `// ── Mic button (placeholder — wired up in Task 5) ──` and replace everything from that comment through the `window.__appendText = appendText;` line (but keep the closing `})();`) with:

```javascript
      // ── Rev.ai Streaming ──
      let audioContext = null;
      let revaiSocket = null;
      let scriptNode = null;
      let micStream = null;

      function buildRevaiUrl() {
        const params = new URLSearchParams({
          access_token: REVAI_ACCESS_TOKEN,
          content_type: `audio/x-raw;layout=interleaved;rate=16000;format=S16LE;channels=1`,
          remove_disfluencies: 'true',
        });
        if (REVAI_VOCAB_ID) {
          params.set('custom_vocabulary_id', REVAI_VOCAB_ID);
        }
        return `wss://api.rev.ai/speechtotext/v1/stream?${params}`;
      }

      function startRecording() {
        if (isRecording) return;
        isRecording = true;
        btnMic.textContent = 'Stop';
        btnMic.className = 'btn-stop';
        setStatus('Connecting...', '');

        audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

        revaiSocket = new WebSocket(buildRevaiUrl());
        revaiSocket.onopen = onRevaiOpen;
        revaiSocket.onclose = onRevaiClose;
        revaiSocket.onmessage = onRevaiMessage;
        revaiSocket.onerror = (e) => {
          console.error('Rev.ai WebSocket error:', e);
          setStatus('Connection error', 'error');
        };
      }

      function stopRecording() {
        isRecording = false;
        btnMic.textContent = 'Start';
        btnMic.className = 'btn-start';
        setStatus('Ready', '');

        if (revaiSocket && revaiSocket.readyState === WebSocket.OPEN) {
          revaiSocket.send('EOS');
          revaiSocket.close();
        }
        revaiSocket = null;

        if (scriptNode) {
          scriptNode.disconnect();
          scriptNode = null;
        }

        if (micStream) {
          micStream.getTracks().forEach(t => t.stop());
          micStream = null;
        }

        if (audioContext) {
          audioContext.close();
          audioContext = null;
        }
      }

      function onRevaiOpen() {
        setStatus('Listening...', 'listening');

        navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
          micStream = stream;

          // Resample to 16kHz if needed — AudioContext was created with sampleRate: 16000
          // but some browsers may ignore the requested sample rate.
          const source = audioContext.createMediaStreamSource(stream);
          scriptNode = audioContext.createScriptProcessor(4096, 1, 1);

          scriptNode.addEventListener('audioprocess', (e) => {
            if (!revaiSocket || revaiSocket.readyState !== WebSocket.OPEN) return;
            if (audioContext.state === 'suspended' || audioContext.state === 'closed') return;

            const inputData = e.inputBuffer.getChannelData(0);

            // Convert float32 [-1, 1] to PCM S16LE
            const output = new DataView(new ArrayBuffer(inputData.length * 2));
            for (let i = 0; i < inputData.length; i++) {
              const multiplier = inputData[i] < 0 ? 0x8000 : 0x7fff;
              output.setInt16(i * 2, (inputData[i] * multiplier) | 0, true);
            }

            // Trim trailing silence
            const intData = new Int16Array(output.buffer);
            let end = intData.length;
            while (end > 0 && intData[end - 1] === 0) { end--; }
            if (end > 0) {
              revaiSocket.send(intData.slice(0, end));
            }
          });

          source.connect(scriptNode);
          scriptNode.connect(audioContext.destination);
        }).catch((err) => {
          console.error('Mic access denied:', err);
          setStatus('Mic access denied', 'error');
          stopRecording();
        });
      }

      function onRevaiClose(event) {
        if (isRecording) {
          // Unexpected close — try to indicate the issue
          setStatus(`Disconnected (${event.code})`, 'error');
          stopRecording();
        }
      }

      function onRevaiMessage(event) {
        const data = JSON.parse(event.data);

        switch (data.type) {
          case 'connected':
            setStatus('Listening...', 'listening');
            break;

          case 'partial':
            // Ignored — we only display polished finals
            break;

          case 'final':
            // Extract text from elements
            const text = data.elements
              .map(el => el.value)
              .join('');

            if (text.trim()) {
              polishText(text);
            }
            break;
        }
      }

      // ── Mic button ──
      btnMic.addEventListener('click', () => {
        if (isRecording) {
          stopRecording();
        } else {
          startRecording();
        }
      });
```

- [ ] **Step 2: Remove the temporary window exports**

Delete these lines that were at the end of the IIFE (they were for manual testing in Task 4):

```javascript
      // Expose polishText for Task 5 to use
      window.__polishText = polishText;
      window.__setStatus = setStatus;
      window.__appendText = appendText;
```

- [ ] **Step 3: Test Rev.ai connection (without polish)**

Temporarily modify the `'final'` case in `onRevaiMessage` to display raw text directly (for testing without Modal):

Open `http://localhost:8080`, enter any password, click Start. Speak into the mic. After a moment, you should see the status change to "Listening..." and shortly after, transcript text should appear.

If you haven't deployed Modal yet, the raw text will appear as a fallback (the polish function falls back to `appendText(raw)` on error).

- [ ] **Step 4: Test full pipeline (with Modal deployed)**

Ensure `MODAL_POLISH_URL` is set to your deployed Modal URL in the config section of `index.html`. Enter the bearer token as the password. Click Start and speak.

Verify:
- Status shows "Listening..."
- After speaking, polished text appears within ~2-3 seconds
- Buddhist terms are correctly capitalized
- Filler words are removed
- Text accumulates correctly across multiple segments
- Clear button works
- Export button downloads the full transcript
- Fullscreen works

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat: add Rev.ai audio streaming and connect to polish pipeline"
```

---

### Task 6: PWA Assets

**Files:**
- Create: `manifest.json`
- Create: `service-worker.js`
- Copy: `icon-192.png`, `icon-512.png` from `~/workspace/caption/`

Optional but nice-to-have: makes the app installable on mobile/desktop.

- [ ] **Step 1: Copy icon assets from the caption project**

```bash
cp /Users/brian.buchalter/workspace/caption/icon-192.png /Users/brian.buchalter/workspace/streaming-dictation/
cp /Users/brian.buchalter/workspace/caption/icon-512.png /Users/brian.buchalter/workspace/streaming-dictation/
```

- [ ] **Step 2: Create `manifest.json`**

```json
{
  "name": "Streaming Dictation",
  "short_name": "Dictation",
  "start_url": ".",
  "display": "standalone",
  "background_color": "#000000",
  "theme_color": "#000000",
  "icons": [
    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

- [ ] **Step 3: Create `service-worker.js`**

```javascript
const CACHE_NAME = 'dictation-v1';
const ASSETS = ['/', '/index.html', '/manifest.json', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)));
});

self.addEventListener('fetch', (e) => {
  // Only cache GET requests for static assets — never cache API calls
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
```

- [ ] **Step 4: Add PWA meta tags to `index.html`**

In the `<head>` section of `index.html`, after the `<title>` tag, add:

```html
  <link rel="manifest" href="manifest.json">
  <link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
  <link rel="apple-touch-icon" href="icon-192.png">
```

At the very end of the `<script>` block (before the closing `</script>` tag), add:

```javascript
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('service-worker.js');
    }
```

- [ ] **Step 5: Commit**

```bash
git add manifest.json service-worker.js icon-192.png icon-512.png index.html
git commit -m "feat: add PWA manifest, service worker, and icons"
```

---

### Task 7: End-to-End Smoke Test

No new files. This task verifies the full pipeline works together.

- [ ] **Step 1: Ensure Modal is deployed**

```bash
modal deploy modal_app.py
```

Confirm the URL is set in `index.html`'s `MODAL_POLISH_URL` constant.

- [ ] **Step 2: Ensure Rev.ai vocabulary is ready**

```bash
curl -H "Authorization: Bearer $REVAI_ACCESS_TOKEN" \
  https://api.rev.ai/speechtotext/v1/vocabularies/<your-vocab-id>
```

Confirm `status` is `"complete"`. Confirm the vocabulary ID is set in `index.html`'s `REVAI_VOCAB_ID` constant.

- [ ] **Step 3: Run the full test**

```bash
cd /Users/brian.buchalter/workspace/streaming-dictation
python -m http.server 8080
```

Open `http://localhost:8080` in Chrome. Enter your Modal bearer token as the password. Click Start.

**Test checklist:**
- [ ] Mic permission prompt appears and can be granted
- [ ] Status shows "Connecting..." then "Listening..."
- [ ] Speaking produces polished captions within ~2-3 seconds
- [ ] Buddhist terms are correctly handled (say "metta", "vipassana", "dukkha")
- [ ] Filler words are removed
- [ ] Text accumulates correctly over multiple utterances
- [ ] Pausing speech and resuming works
- [ ] Stop button stops the stream cleanly
- [ ] Start button reconnects successfully
- [ ] Clear button clears the display and transcript
- [ ] Export button downloads a .txt file with the full transcript
- [ ] Fullscreen mode works
- [ ] Font size A-/A+ buttons work
- [ ] Closing tab and reopening requires re-entering password
- [ ] Refreshing page preserves the session (no re-auth needed)
- [ ] Wrong password returns 401 from Modal (polished text falls back to raw)

- [ ] **Step 4: Commit any fixes**

If any issues were found and fixed during testing:

```bash
git add -A
git commit -m "fix: address issues found during end-to-end testing"
```
