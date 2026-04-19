# Server-Side Audio Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move audio routing from browser to Modal so the browser sends Opus audio over a single WebSocket, Modal proxies to Rev.ai server-to-server, polishes with Claude Haiku, and streams polished text back — removing all API keys from the client.

**Architecture:** Browser captures mic audio via MediaRecorder (Opus ~24kbps), sends binary frames over a single WebSocket to Modal. Modal maintains a server-side WebSocket to Rev.ai, forwards audio, receives transcript finals, polishes them with Claude Haiku (non-streaming), and sends polished text back as JSON text frames. Reconnection with local buffering handles poor connectivity.

**Tech Stack:** Modal (Python, FastAPI, websockets, anthropic), MediaRecorder (Opus), vanilla JS

---

## File Structure

```
streaming-dictation/
├── modal_app.py              # REWRITE: WebSocket /stream endpoint + Rev.ai proxy + inline polish
├── index.html                # REWRITE: JS only — MediaRecorder Opus + single WebSocket + reconnection
├── .env.example              # MODIFY: remove REVAI tokens, simplify
└── (all other files unchanged)
```

---

### Task 1: Rewrite Modal Backend with WebSocket `/stream` Endpoint

**Files:**
- Rewrite: `modal_app.py`

This is the core backend change. The file gets a new WebSocket `/stream` endpoint that:
1. Authenticates the browser via query param
2. Connects to Rev.ai server-to-server
3. Forwards audio binary frames from browser → Rev.ai
4. Processes transcript finals → Claude Haiku polish → sends polished text back to browser
5. Handles disconnection and cleanup

The existing `/polish` HTTP endpoint is kept as a fallback during development.

- [ ] **Step 1: Replace `modal_app.py` with the new implementation**

Replace the entire file with:

```python
import asyncio
import json
import modal

app = modal.App("streaming-dictation")

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "anthropic",
    "fastapi",
    "websockets",
)

# System prompt is long — read from the existing file at runtime.
# We keep it as a module-level constant (unchanged from current).
SYSTEM_PROMPT = """You are a transcription editor for live Buddhist Dharma talks in the Plum Village tradition of Thich Nhat Hanh.
You receive raw speech-to-text output and clean it up.

Rules:
- Fix punctuation and capitalization
- Remove filler words (um, uh, like, you know)
- Preserve the speaker's words faithfully — do not restructure sentences
- If a sentence is cut off at the end, include it as-is
- Use the provided context for continuity — do not repeat words that were already in the previous segment

Correct Buddhist terminology using these guidelines:

NAMES AND HONORIFICS:
- "Thich Nhat Hanh" or "Thay" (not "Tick Not Han", "Tik Nat Han", etc.)
- "Su Ong" (grandfather teacher), "Su Co" (sister/nun), "Su Chu" (young monk)
- "Sister Chan Khong", "Brother Phap Huu", "Brother Phap Linh", "Brother Phap Dung"
- Dharma name prefixes: "Chan" (True), "Phap" (Dharma), "Nghiem" (Adornment), "Troi" (Sky), "Trang" (Adornment)
- Historical figures: Nagarjuna, Vasubandhu, Shakyamuni, Huineng, Linji

PLACES:
- "Plum Village" (Lang Mai), "Blue Cliff Monastery", "Deer Park Monastery" (Loc Uyen), "Magnolia Grove Monastery"
- Hamlets: "Upper Hamlet" (Xom Thuong), "Lower Hamlet" (Xom Ha), "New Hamlet" (Xom Moi)
- Temples: "Phap Van" (Dharma Cloud), "Cam Lo" (Dharma Nectar), "Son Ha"
- "Tu Hieu" (TNH's root temple), "EIAB" (European Institute of Applied Buddhism)

THICH NHAT HANH'S KEY CONCEPTS:
- "Interbeing" (not "inter-being" or "inner being")
- "Engaged Buddhism", "Manifestation Only" (not "Consciousness Only")
- "Five Mindfulness Trainings", "Fourteen Mindfulness Trainings"
- "Fourfold Sangha", "Three Doors of Liberation"

MAHAYANA TERMS (this tradition uses Sanskrit over Pali):
- "Sunyata" (emptiness), "Prajnaparamita" (perfection of wisdom)
- "Bodhichitta" (mind of love/awakening), "Tathata" (suchness)
- "Dharmadhatu", "Tathagatagarbha" (Buddha-nature)
- Three Bodies: "Dharmakaya", "Sambhogakaya", "Nirmanakaya", "Sanghakaya"
- Bodhisattvas: "Avalokiteshvara", "Manjushri", "Samantabhadra", "Kshitigarbha", "Sadaparibhuta", "Maitreya"

YOGACARA/CONSCIOUSNESS TERMS:
- "Alaya" or "Alaya Vijnana" (store consciousness)
- "Manas" (grasping mind), "Bija" (seeds), "Vijnana" (consciousness)

CORE BUDDHIST TERMS (always capitalize):
- Dharma, Sangha, Buddha, Sutra, Sutta, Vinaya
- Dukkha, Anatta, Anicca, Nirvana/Nibbana, Samsara, Karma
- Metta/Maitri, Karuna, Mudita, Upekkha/Upeksha
- Vipassana, Samatha, Jhana, Samadhi, Sila, Prajna/Panna
- Skandha/Khanda, Vedana, Sanna, Sankhara

SUTRAS:
- "Heart Sutra", "Diamond Sutra", "Lotus Sutra", "Avatamsaka Sutra"
- "Anapanasati Sutta", "Satipatthana Sutta", "Bhaddekaratta Sutta"
- "Lankavatara Sutra", "Vimalakirti Sutra", "Platform Sutra"

PRACTICE TERMS:
- "Gatha/Gathas" (mindfulness poems), "Touching the Earth", "Beginning Anew"
- "Dharma sharing", "Dharma rain", "Dharmacharya" (Dharma teacher)
- "Inviting the Bell", "Noble Silence", "Lazy Day"
- "Walking Meditation", "Sitting Meditation", "Deep Relaxation"

COMMUNITY:
- "Order of Interbeing" (Tiep Hien), "Parallax Press", "The Mindfulness Bell"
- "Wake Up" (young practitioners), "ARISE Sangha", "Earth Holder Sangha"

VIETNAMESE TERMS:
- "Thien" (Zen), "Lam Te" (Linji school), "Lieu Quan" (sub-lineage)"""


@app.cls(
    image=image,
    secrets=[
        modal.Secret.from_name("streaming-dictation-auth"),
        modal.Secret.from_name("streaming-dictation-anthropic"),
        modal.Secret.from_name("streaming-dictation-revai"),
    ],
    scaledown_window=60,
)
@modal.concurrent(max_inputs=10)
class StreamingDictation:
    @modal.enter()
    def setup_client(self):
        import anthropic

        self.client = anthropic.Anthropic()

    @modal.asgi_app()
    def web(self):
        import os
        import websockets
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware

        web_app = FastAPI()

        web_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        def build_revai_url():
            params = {
                "access_token": os.environ["REVAI_ACCESS_TOKEN"],
                "content_type": "audio/webm;codecs=opus",
                "remove_disfluencies": "true",
            }
            vocab_id = os.environ.get("REVAI_VOCAB_ID", "")
            if vocab_id:
                params["custom_vocabulary_id"] = vocab_id
            query = "&".join(f"{k}={v}" for k, v in params.items())
            return f"wss://api.rev.ai/speechtotext/v1/stream?{query}"

        async def polish_text(client, raw: str, context: str) -> str:
            """Call Claude Haiku to polish a transcript segment."""
            if not raw.strip():
                return ""
            user_message = (
                f"Previous context: {context}\n"
                f"Raw transcription: {raw}\n"
                f"Return only the cleaned text, nothing else."
            )
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                return response.content[0].text
            except Exception:
                return ""

        @web_app.websocket("/stream")
        async def stream(ws: WebSocket):
            # Auth: validate token query param
            token = ws.query_params.get("token", "")
            expected = os.environ["BEARER_TOKEN"]
            if token != expected:
                await ws.close(code=4001, reason="Unauthorized")
                return

            await ws.accept()

            # Connect to Rev.ai server-to-server
            revai_url = build_revai_url()
            try:
                revai_ws = await websockets.connect(revai_url)
            except Exception as e:
                await ws.send_json({"type": "error", "data": f"Rev.ai connection failed: {e}"})
                await ws.close()
                return

            # Wait for Rev.ai connected message
            try:
                init_msg = await asyncio.wait_for(revai_ws.recv(), timeout=10)
                init_data = json.loads(init_msg)
                if init_data.get("type") == "connected":
                    await ws.send_json({"type": "status", "data": "listening"})
            except Exception:
                await ws.send_json({"type": "error", "data": "Rev.ai handshake failed"})
                await ws.close()
                await revai_ws.close()
                return

            context = ""
            browser_done = asyncio.Event()

            async def forward_audio():
                """Read binary/text frames from browser, forward to Rev.ai."""
                try:
                    while True:
                        data = await ws.receive()
                        if data["type"] == "websocket.disconnect":
                            break
                        if "bytes" in data and data["bytes"]:
                            await revai_ws.send(data["bytes"])
                        elif "text" in data and data["text"] == "EOS":
                            await revai_ws.send("EOS")
                            browser_done.set()
                            break
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def process_transcripts():
                """Read transcript messages from Rev.ai, polish, send to browser."""
                nonlocal context
                try:
                    async for message in revai_ws:
                        msg = json.loads(message)
                        if msg.get("type") == "final":
                            raw_text = "".join(
                                el.get("value", "") for el in msg.get("elements", [])
                            )
                            if raw_text.strip():
                                polished = await asyncio.to_thread(
                                    polish_text, self.client, raw_text, context
                                )
                                if polished:
                                    await ws.send_json({"type": "text", "data": polished})
                                    words = polished.split()
                                    context = " ".join(words[-50:])
                except websockets.exceptions.ConnectionClosed:
                    if not browser_done.is_set():
                        try:
                            await ws.send_json({"type": "status", "data": "disconnected"})
                        except Exception:
                            pass
                except Exception:
                    pass

            # Run both tasks concurrently
            try:
                await asyncio.gather(forward_audio(), process_transcripts())
            finally:
                try:
                    await revai_ws.close()
                except Exception:
                    pass

        return web_app
```

Key design decisions in this implementation:
- **`polish_text` runs in a thread** via `asyncio.to_thread()` because the `anthropic` SDK's `messages.create()` is synchronous. This prevents blocking the async event loop while waiting for Claude Haiku.
- **`browser_done` event** coordinates shutdown: when the browser sends `EOS`, the audio forwarder signals the transcript processor to expect Rev.ai to finish up.
- **`forward_audio` uses `ws.receive()`** (raw ASGI) instead of `ws.receive_bytes()` to handle both binary frames (audio) and text frames (EOS) on the same connection.
- **Class renamed** from `PolishModel` to `StreamingDictation` — more accurate for the new role. Note: this changes the Modal URL from `polishmodel-web` to `streamingdictation-web`.
- **`/polish` endpoint removed** — the WebSocket handles everything. If you need the old endpoint during development, keep it temporarily.

- [ ] **Step 2: Verify the system prompt is complete**

The system prompt in the code above is abbreviated for plan readability. The actual `modal_app.py` has a much longer prompt with extensive terminology (Yogacara, 51 mental formations, 12 links, Paramitas, 37 wings, Abhidharma, sutra characters, Vietnamese/Japanese/Tibetan terms, etc.). **Do not truncate it.** When writing the file, preserve the full `SYSTEM_PROMPT` from the current `modal_app.py` — only the code around it changes.

- [ ] **Step 3: Test locally with `modal serve`**

```bash
python -m modal serve modal_app.py
```

Expected: Modal prints a URL. The image build should be fast (pip install of three packages, no model download). Note the URL — it will end in `streamingdictation-web.modal.run` (class name changed).

- [ ] **Step 4: Test WebSocket auth rejection**

Use `websocat` or a quick Python script to test auth:

```bash
python3 -c "
import asyncio, websockets
async def test():
    try:
        async with websockets.connect('wss://<your-url>/stream?token=wrong') as ws:
            msg = await ws.recv()
            print(msg)
    except websockets.exceptions.ConnectionClosed as e:
        print(f'Closed: code={e.code} reason={e.reason}')
asyncio.run(test())
"
```

Expected: `Closed: code=4001 reason=Unauthorized`

- [ ] **Step 5: Test WebSocket auth success**

```bash
python3 -c "
import asyncio, websockets
async def test():
    async with websockets.connect('wss://<your-url>/stream?token=thankyoudearthay') as ws:
        msg = await ws.recv()
        print(msg)
        await ws.send('EOS')
asyncio.run(test())
"
```

Expected: `{"type": "status", "data": "listening"}`

- [ ] **Step 6: Commit**

```bash
git add modal_app.py
git commit -m "feat: add WebSocket /stream endpoint with server-side Rev.ai proxy

Replace the HTTP /polish endpoint with a WebSocket /stream endpoint.
Modal now accepts audio from the browser, proxies it to Rev.ai
server-to-server, polishes transcript finals with Claude Haiku, and
sends polished text back over the same WebSocket. All API keys
stay server-side. Adds websockets dependency and streaming-dictation-revai
secret."
```

---

### Task 2: Rewrite Frontend Audio and Transport

**Files:**
- Rewrite: `index.html` (JavaScript only — HTML structure and CSS unchanged)

This replaces all audio capture, transport, and polish code with a single WebSocket connection to Modal using MediaRecorder for Opus audio capture.

- [ ] **Step 1: Replace the `<script>` block in `index.html`**

Replace everything between `<script>` and `</script>` (inclusive) with:

```html
  <script>
    (function () {
      // ── Config ──
      const MODAL_WS_URL = 'wss://bbuchalter--streaming-dictation-streamingdictation-web.modal.run';

      const BUFFER_LIMIT = 5000;
      const STORAGE_KEY = 'dictation-transcript';
      const MAX_RECONNECT_ATTEMPTS = 3;
      const AUDIO_BUFFER_MAX = 40; // ~10 seconds at 250ms chunks

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
      const levelMeter = document.getElementById('levelMeter');
      const levelFill = document.getElementById('levelFill');
      const btnFullscreen = document.getElementById('btnFullscreen');
      const btnSizeDown = document.getElementById('btnSizeDown');
      const btnSizeUp = document.getElementById('btnSizeUp');
      const statusEl = document.getElementById('status');

      // ── State ──
      let sessionToken = sessionStorage.getItem('sessionToken') || '';
      let isRecording = false;
      let transcript = localStorage.getItem(STORAGE_KEY) || '';
      let ws = null;
      let mediaRecorder = null;
      let micStream = null;
      let audioCtx = null;
      let analyser = null;
      let levelRAF = null;
      let audioBuffer = [];
      let reconnectAttempts = 0;
      let reconnectTimer = null;
      let intentionalStop = false;

      // ── Font size ──
      const SIZES = [3, 4, 5, 6, 7, 8, 10];
      let sizeIndex = 2;

      function applySize() {
        document.documentElement.style.setProperty('--caption-size', SIZES[sizeIndex] + 'vw');
      }

      // ── Status ──
      function setStatus(text, cls) {
        statusEl.textContent = text;
        statusEl.className = 'status' + (cls ? ' ' + cls : '');
      }

      // ── Auth ──
      async function authenticate() {
        const token = passwordInput.value.trim();
        if (!token) return;
        authSubmit.disabled = true;
        authSubmit.textContent = 'Verifying...';

        try {
          const valid = await verifyToken(token);
          if (valid) {
            sessionToken = token;
            sessionStorage.setItem('sessionToken', token);
            showApp();
          } else {
            sessionStorage.removeItem('sessionToken');
            authSubmit.textContent = 'Invalid password';
            setTimeout(() => { authSubmit.textContent = 'Connect'; authSubmit.disabled = false; }, 2000);
          }
        } catch (err) {
          authSubmit.textContent = 'Connection error';
          setTimeout(() => { authSubmit.textContent = 'Connect'; authSubmit.disabled = false; }, 2000);
        }
      }

      function verifyToken(token) {
        return new Promise((resolve) => {
          const testWs = new WebSocket(MODAL_WS_URL + '/stream?token=' + encodeURIComponent(token));
          testWs.onmessage = (e) => {
            try {
              const msg = JSON.parse(e.data);
              if (msg.type === 'status' && msg.data === 'listening') {
                testWs.send('EOS');
                testWs.close();
                resolve(true);
              }
            } catch (_) {}
          };
          testWs.onclose = (e) => {
            if (e.code === 4001) resolve(false);
          };
          testWs.onerror = () => resolve(false);
          setTimeout(() => { testWs.close(); resolve(false); }, 10000);
        });
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

      if (sessionToken) {
        verifyToken(sessionToken).then((valid) => {
          if (valid) showApp();
          else { sessionStorage.removeItem('sessionToken'); sessionToken = ''; }
        }).catch(() => { sessionStorage.removeItem('sessionToken'); sessionToken = ''; });
      }

      // ── Caption display ──
      function renderDisplay() {
        const tail = transcript.length > BUFFER_LIMIT
          ? transcript.slice(transcript.length - BUFFER_LIMIT)
          : transcript;
        captionText.textContent = tail;
        captionDisplay.scrollTop = captionDisplay.scrollHeight;
      }

      function appendText(text) {
        if (!text) return;
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

      // ── Audio level meter via AnalyserNode ──
      function startLevelMeter(stream) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const source = audioCtx.createMediaStreamSource(stream);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);

        const dataArray = new Uint8Array(analyser.frequencyBinCount);

        function updateMeter() {
          analyser.getByteFrequencyData(dataArray);
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
          const avg = sum / dataArray.length;
          const pct = Math.min(100, avg * 1.5);
          levelFill.style.width = pct + '%';
          levelFill.classList.toggle('loud', pct > 70);
          levelRAF = requestAnimationFrame(updateMeter);
        }
        updateMeter();
      }

      function stopLevelMeter() {
        if (levelRAF) { cancelAnimationFrame(levelRAF); levelRAF = null; }
        if (audioCtx) { audioCtx.close(); audioCtx = null; }
        analyser = null;
        levelFill.style.width = '0%';
      }

      // ── WebSocket connection ──
      function connectWebSocket() {
        ws = new WebSocket(MODAL_WS_URL + '/stream?token=' + encodeURIComponent(sessionToken));

        ws.onopen = () => {
          reconnectAttempts = 0;
          // Flush buffered audio chunks
          while (audioBuffer.length > 0) {
            const chunk = audioBuffer.shift();
            if (ws.readyState === WebSocket.OPEN) ws.send(chunk);
          }
        };

        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data);
            switch (msg.type) {
              case 'status':
                if (msg.data === 'listening') {
                  setStatus('Listening...', 'listening');
                } else if (msg.data === 'disconnected') {
                  setStatus('Server reconnecting...', '');
                }
                break;
              case 'text':
                appendText(msg.data);
                break;
              case 'error':
                console.error('Server error:', msg.data);
                setStatus('Error: ' + msg.data, 'error');
                break;
            }
          } catch (_) {}
        };

        ws.onclose = (e) => {
          if (intentionalStop || e.code === 4001) return;
          if (isRecording) {
            attemptReconnect();
          }
        };

        ws.onerror = () => {};
      }

      function attemptReconnect() {
        if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
          setStatus('Connection lost', 'error');
          stopRecording();
          return;
        }
        reconnectAttempts++;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 8000);
        setStatus(`Reconnecting (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`, '');
        reconnectTimer = setTimeout(connectWebSocket, delay);
      }

      // ── Recording ──
      function startRecording() {
        if (isRecording) return;
        isRecording = true;
        intentionalStop = false;
        reconnectAttempts = 0;
        audioBuffer = [];
        btnMic.textContent = 'Stop';
        btnMic.className = 'btn-stop';
        setStatus('Connecting...', '');
        levelMeter.classList.add('active');

        navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
          micStream = stream;
          startLevelMeter(stream);

          mediaRecorder = new MediaRecorder(stream, {
            mimeType: 'audio/webm;codecs=opus',
            audioBitsPerSecond: 24000,
          });

          mediaRecorder.ondataavailable = (e) => {
            if (e.data.size === 0) return;
            if (ws && ws.readyState === WebSocket.OPEN) {
              ws.send(e.data);
            } else {
              // Buffer audio during reconnection
              audioBuffer.push(e.data);
              if (audioBuffer.length > AUDIO_BUFFER_MAX) audioBuffer.shift();
            }
          };

          mediaRecorder.start(250);
          connectWebSocket();
        }).catch((err) => {
          console.error('Mic access denied:', err);
          setStatus('Mic access denied', 'error');
          stopRecording();
        });
      }

      function stopRecording() {
        intentionalStop = true;
        isRecording = false;
        btnMic.textContent = 'Start';
        btnMic.className = 'btn-start';
        setStatus('Ready', '');
        levelMeter.classList.remove('active');

        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
          mediaRecorder.stop();
        }
        mediaRecorder = null;

        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send('EOS');
          ws.close();
        }
        ws = null;

        if (micStream) {
          micStream.getTracks().forEach(t => t.stop());
          micStream = null;
        }

        stopLevelMeter();
        audioBuffer = [];
      }

      // ── Mic button ──
      btnMic.addEventListener('click', () => {
        if (isRecording) {
          stopRecording();
        } else {
          startRecording();
        }
      });

    })();

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('service-worker.js');
    }
  </script>
```

What changed vs the old script:
- **Config:** `MODAL_WS_URL` replaces `REVAI_ACCESS_TOKEN`, `REVAI_VOCAB_ID`, and `MODAL_POLISH_URL`
- **Auth:** `verifyToken()` opens a test WebSocket instead of HTTP fetch to `/polish`
- **Audio:** `MediaRecorder` (Opus) replaces `ScriptProcessorNode` + PCM16 conversion
- **Transport:** Single `connectWebSocket()` replaces separate Rev.ai WS + `polishText()` fetch/SSE
- **Level meter:** `AnalyserNode` + `requestAnimationFrame` replaces inline RMS in `audioprocess`
- **Reconnection:** `attemptReconnect()` with exponential backoff + `audioBuffer` for chunk queuing
- **Removed:** `buildRevaiUrl`, `onRevaiOpen`, `onRevaiMessage`, `onRevaiClose`, `polishText`, all PCM16 code, `currentContext` (context is now server-side)

- [ ] **Step 2: Test the auth gate in a browser**

```bash
cd /Users/brian.buchalter/workspace/streaming-dictation && python -m http.server 8080
```

Open `http://localhost:8080/index.html`. Verify:
- Wrong password → "Invalid password" (WebSocket closed with 4001)
- Correct password → app loads with Start button

Note: this requires Modal to be running (`modal serve modal_app.py` from Task 1).

- [ ] **Step 3: Test recording flow**

Click Start, grant mic permission, speak. Verify:
- Level meter responds to voice
- Status shows "Listening..."
- Polished captions appear within ~1-2 seconds
- Buddhist terms are correctly capitalized
- Stop button stops cleanly

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat: rewrite frontend for single WebSocket transport

Replace Rev.ai client-side WebSocket + HTTP polish with a single
WebSocket to Modal. Audio captured via MediaRecorder (Opus ~24kbps)
instead of ScriptProcessorNode (PCM16 ~256kbps). Add reconnection
with exponential backoff and local audio buffering. Remove all API
keys from client code."
```

---

### Task 3: Clean Up and Deploy

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Update `.env.example`**

Replace the contents with:

```
MODAL_BEARER_TOKEN=your-modal-bearer-token-here
```

The Rev.ai and Anthropic keys are now Modal secrets — they don't belong in the client-side env file. The only value a developer needs locally is the bearer token (for entering in the browser).

- [ ] **Step 2: Deploy to production**

```bash
python -m modal deploy modal_app.py
```

Expected: prints the production URL. Note that the URL will change because the class was renamed from `PolishModel` to `StreamingDictation` — the new URL will be `https://bbuchalter--streaming-dictation-streamingdictation-web.modal.run`.

- [ ] **Step 3: Update `MODAL_WS_URL` in `index.html` if needed**

After deploy, verify the URL in the `MODAL_WS_URL` constant matches the deployed URL (with `wss://` scheme). Update if different.

- [ ] **Step 4: End-to-end test with deployed Modal**

Open `http://localhost:8080/index.html` in Chrome.

**Test checklist:**
- [ ] Wrong password rejected (WebSocket 4001)
- [ ] Correct password accepted, app loads
- [ ] Status shows "Connecting..." then "Listening..."
- [ ] Level meter responds to voice
- [ ] Speaking produces polished captions within ~1-2 seconds
- [ ] Buddhist terms correctly handled (say "metta", "vipassana", "thay")
- [ ] Filler words removed (say "um", "you know")
- [ ] Text accumulates over multiple utterances
- [ ] Stop button stops cleanly, status returns to "Ready"
- [ ] Start again reconnects successfully
- [ ] Clear and Export buttons work
- [ ] Fullscreen and font sizing work
- [ ] Page refresh with valid token auto-logs in

- [ ] **Step 5: Commit any fixes and clean up**

```bash
git add .env.example index.html
git commit -m "chore: simplify .env.example, finalize deployment URL"
```
