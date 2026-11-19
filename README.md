# Streaming Dictation

Real-time closed captioning for live Buddhist Dharma talks. A speaker runs the app on a dedicated screen visible to the audience. The browser captures mic audio, streams it to a server for speech-to-text, polishes punctuation, capitalization, and approved terminology with an LLM, and displays the captions word by word.

**Live app:** https://bbuchalter.github.io/streaming-dictation/

## How it works

1. Browser captures mic audio as Opus (~24kbps) via MediaRecorder
2. Audio streams over a single WebSocket to a Modal server
3. Modal proxies the audio to Deepgram Nova-3 for speech-to-text
4. Transcript segments are polished by Claude Haiku (punctuation, capitalization, and approved terminology corrections)
5. Polished text streams back to the browser word by word

Provider API keys stay server-side. The browser exchanges the shared password for
a four-hour streaming credential and keeps it in memory. Reloading requires
signing in again. Captions stay in memory until cleared or the app is closed.
Use Export to keep a copy.

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
- Random, high-entropy bearer token for auth (stored as `BEARER_TOKEN` in Modal secret `streaming-dictation-auth`)

### Local development

```bash
# Run the Modal endpoint with hot reload
DICTATION_ALLOW_LOCALHOST=1 python -m modal serve modal_app.py

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

### Security rollout

Deploy the backend and frontend together between talks. Reload installed PWAs
before rotating `BEARER_TOKEN`, then enter the new value on the updated client.
Older clients send the password in the WebSocket URL. Review historical ingress
logs and restrict access to any credential-bearing records.

To isolate browser data, publish the frontend on a dedicated HTTPS origin and
set `DICTATION_FRONTEND_ORIGIN` to that exact origin when deploying the backend.
Once set, the old GitHub Pages origin loses browser access to the backend.

The application limits login requests and pending WebSocket handshakes per
container. Apply deployment-wide rate limits at ingress where available.
`requirements.txt` pins the application Python dependencies. Inventory and
scan Python and Debian packages from the exact deployed image after deploying.
The runtime uses a digest-pinned official Python image. Update the digest for
new CPython patches. Set `MODAL_FORCE_BUILD=1` when rebuilding to pick up newer
Debian packages from APT without changing the base digest. Packaging tools are
removed after the application dependencies pass `pip check`.

### Automated security updates

Dependabot opens security PRs for the Python pins. GitHub Actions installs the
proposed pins on Python 3.11, checks OSV, and tests authentication and streaming
with mocked providers. Automatic merge is limited to a verified Dependabot PR
that changes one pin within its current major version and fixes an OSV match.
Other updates require review.

The production deployment and weekly image audit use the
`streaming-dictation-production` GitHub environment, restricted to `main`.
Configure its `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` for a deployment
identity with Contributor access to the Modal `main` environment. Then set the
repository variable `MODAL_AUTOMATION_ENABLED` to `true` and run **Deploy Modal
backend** manually to verify the connection. The scheduled audit inventories the
deployed Debian and Python packages in an isolated sandbox, checks OSV, and flags
when the interpreter is behind on patch releases.

## Cost

~$0.30/hr for a live talk (Deepgram STT + Modal compute + Claude Haiku API).
