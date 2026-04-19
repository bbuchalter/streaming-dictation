# Streaming Dictation

Real-time closed captioning for live Buddhist Dharma talks. Captures mic audio in the browser, transcribes via Rev.ai, polishes transcripts through a self-hosted LLM on Modal, and displays polished captions on a fullscreen display.

## Architecture

```
Browser ──WebSocket──▶ Rev.ai (streaming STT)
                              │
                        raw transcript
                              │
Browser ──HTTP POST──▶ Modal (Mistral 7B, LLM polish)
                              │
                        SSE polished tokens
                              │
Browser ◀─────────────────────┘
         caption display
```

## Setup

### 1. Rev.ai Custom Vocabulary

The `create_vocabulary.py` script creates a custom vocabulary in Rev.ai so the STT engine better recognizes Buddhist/Pali/Sanskrit terminology (Dharma, Vipassana, Metta, Dukkha, etc.).

```bash
REVAI_ACCESS_TOKEN=<your-token> python create_vocabulary.py
```

This POSTs the phrase list to Rev.ai's REST API and prints a vocabulary ID. Update `REVAI_VOCAB_ID` in `index.html` with the returned ID.

To update the vocabulary later, edit the `PHRASES` list in the script, re-run it, and update the ID in `index.html`. Rev.ai supports up to 6,000 phrases.

### 2. Modal LLM Endpoint

Deploy the polish endpoint (Mistral 7B Q4 on a T4 GPU):

```bash
modal deploy modal_app.py
```

Update `MODAL_POLISH_URL` in `index.html` with the printed URL.

The bearer token for auth is stored as a Modal secret (`streaming-dictation-auth`). To change it:

```bash
modal secret create streaming-dictation-auth BEARER_TOKEN=<new-password>
```

### 3. Run

Serve the static files:

```bash
python -m http.server 8080
```

Open `http://localhost:8080`, enter the bearer token as the password, and click Start.

## Cost

~$0.80/hr during a talk (Rev.ai $0.20/hr + Modal T4 $0.60/hr). Scales to zero between talks.
