# Streaming Dictation

Real-time closed captioning for live Buddhist Dharma talks. A speaker runs the app on a dedicated screen visible to the audience. The browser captures mic audio, streams it to a server for speech-to-text, polishes the transcript with an LLM for correct terminology, and displays the captions word by word.

**Live app:** https://bbuchalter.github.io/streaming-dictation/

## How it works

1. Browser captures mic audio as Opus (~24kbps) via MediaRecorder
2. Audio streams over a single WebSocket to a Modal server
3. Modal proxies the audio to Deepgram Nova-3 for speech-to-text
4. Transcript segments are polished by Claude Haiku (punctuation, filler removal, Buddhist terminology correction)
5. Polished text streams back to the browser word by word

All API keys stay server-side. The browser only needs the Modal endpoint URL and a password.

## Architecture

```
Browser (Opus audio) ──WebSocket──> Modal (CPU proxy)
                                      ├──> Deepgram Nova-3 (STT)
                                      └──> Claude Haiku (polish)
                    <──WebSocket──  polished text (JSON)
```

## Usage

1. Open the app and enter the password
2. Click **Start** to begin captioning
3. Speak — polished captions appear in ~1-2 seconds
4. Use **A-/A+** to adjust font size, **Fullscreen** for presentation
5. Click **Export** to download the transcript as a text file

## Development

### Prerequisites

- [Modal](https://modal.com) account with CLI authenticated
- Deepgram API key (stored as Modal secret `streaming-dictation-deepgram`)
- Anthropic API key (stored as Modal secret `streaming-dictation-anthropic`)
- Bearer token for auth (stored as Modal secret `streaming-dictation-auth`)

### Local development

```bash
# Run the Modal endpoint with hot reload
python -m modal serve modal_app.py

# Serve the frontend
python -m http.server 8080
# Open http://localhost:8080/index.html
```

### Deploy

```bash
# Deploy the Modal backend
python -m modal deploy modal_app.py

# Frontend is served via GitHub Pages (auto-deploys on push to main)
git push origin main
```

## Cost

~$0.30/hr for a live talk (Deepgram STT + Modal compute + Claude Haiku API).
