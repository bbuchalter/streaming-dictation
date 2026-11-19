import asyncio
import hashlib
import hmac
import json
import os
import re
import time
import modal

app = modal.App("streaming-dictation")

# Clients older than this epoch need to reload to obtain streaming credentials.
MIN_CLIENT_EPOCH = 3

# Seconds /stream waits for the token as its first text frame. This is what
# bounds how much of the function timeout below (10800s) a client that connects
# and then says nothing can pin: without it, one silent socket holds a container
# and a @modal.concurrent slot for the full three hours. Named rather than
# inlined so the pair stays greppable together.
AUTH_TIMEOUT_SECONDS = 10

SESSION_TTL_SECONDS = 4 * 60 * 60
DRAIN_TIMEOUT_SECONDS = 5
MAX_PENDING_HANDSHAKES = 4
MAX_AUDIO_FRAME_BYTES = 64 * 1024

image = modal.Image.debian_slim(python_version="3.11").pip_install_from_requirements(
    "requirements.txt"
)


class RateLimit:
    """A per-container token bucket used only on the ASGI event loop."""

    def __init__(self, burst, per_second):
        self.burst = self.tokens = burst
        self.per_second = per_second
        self.updated = time.monotonic()

    def allow(self):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.per_second)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


def session_signature(secret, expiry):
    message = f"streaming-dictation/session/v1:{expiry}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def valid_session(secret, ticket):
    if not ticket or not re.fullmatch(r"[0-9]{1,12}\.[a-f0-9]{64}", ticket):
        return False
    expiry, signature = ticket.split(".")
    return int(expiry) > time.time() and hmac.compare_digest(
        signature, session_signature(secret, expiry)
    )


# Only these reviewed substitutions may alter the words sent to the display.
TRANSCRIPT_ALIASES = {
    ("tick", "not", "han"): ("thich", "nhat", "hanh"),
    ("tik", "nat", "han"): ("thich", "nhat", "hanh"),
    ("tay",): ("thay",),
}


def words(text):
    return re.findall(r"\w+", text.casefold())


def correct_aliases(tokens):
    normalized = []
    offset = 0
    while offset < len(tokens):
        for alias, canonical in TRANSCRIPT_ALIASES.items():
            if tuple(tokens[offset:offset + len(alias)]) == alias:
                normalized.extend(canonical)
                offset += len(alias)
                break
        else:
            normalized.append(tokens[offset])
            offset += 1
    return normalized


SYSTEM_PROMPT = """You are a transcription editor for live Buddhist Dharma talks in the Plum Village tradition of Thich Nhat Hanh.
You receive raw speech-to-text output and clean it up.

Rules:
- Fix punctuation and capitalization
- Preserve every word in order, including filler words. Only adjust punctuation and capitalization or correct the listed mishearings.
- If a sentence is cut off at the end, include it as-is
- Use the provided context for continuity — do not repeat words that were already in the previous segment
- CRITICAL: Output ONLY the cleaned text. Never add commentary, explanations, or conversational responses. If the input is unclear, output your best guess at the cleaned text anyway.

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
- 8 consciousnesses: cakshur (eye), shrotra (ear), ghrana (nose), jihva (tongue), kaya (body), mano-vijnana (mind/6th), klishta-manovijnana (defiled mental/7th), alaya-vijnana (store/8th)
- "Alaya" or "Alaya Vijnana" (store consciousness), "Manas" (grasping mind), "Bija" (seeds)
- Three natures: "Parikalpita" (imagined), "Paratantra" (dependent), "Parinishpanna" (consummate)
- "Ashraya-paravrtti" (transformation at the base), "Vasana" (habit energy)
- "Cittamatra" (mind-only), "Vijnaptimatrata" (manifestation only), "Vijnanavada"

51 MENTAL FORMATIONS (Caitta) — Vasubandhu's list, frequently taught by TNH:
- Universal: Sparsha (contact), Manaskara (attention), Samjna (perception), Cetana (volition)
- Wholesome: Shraddha (faith), Hri (self-respect), Apatrapya (decorum), Alobha, Advesha, Amoha, Prasrabdhi (tranquility), Apramada (conscientiousness), Ahimsa, Abhaya (non-fear)
- Root afflictions: Klesha, Pratigha (anger), Mana (pride), Vicikitsa (doubt), Drishti (wrong view)
- Secondary afflictions: Krodha (rage), Upanaha (resentment), Mraksha (concealment), Irshya (envy), Matsarya (miserliness), Styana (lethargy), Auddhatya (restlessness), Mushitasmritita (forgetfulness), Vikshepa (distraction)
- Changeable: Kaukritya (regret), Middha (drowsiness), Vitarka (initial thought), Vichara (sustained thought)

12 LINKS OF DEPENDENT ORIGINATION (Dvadasha Nidana):
- Avidya/Avijja (ignorance), Samskara (formations), Vijnana (consciousness), Namarupa (name-and-form), Shadayatana/Salayatana (six sense bases), Sparsha/Phassa (contact), Vedana (feeling), Trishna/Tanha (craving), Upadana (clinging), Bhava (becoming), Jati (birth), Jaramarana (aging-and-death)

PARAMITAS:
- Six: Dana (generosity), Sila (ethics), Kshanti/Khanti (patience), Virya (energy), Dhyana (meditation), Prajna (wisdom)
- Additional: Upaya (skillful means), Pranidhana (aspiration/vow), Bala (power), Jnana (knowledge)

37 WINGS OF AWAKENING:
- Satipatthana (four foundations), Sammappadhana (four right efforts), Iddhipada (four bases of power)
- Indriya (five faculties), Bala (five powers), Bojjhanga (seven factors), Magganga (path factors)
- Contemplations: Kayanupassana (body), Vedananupassana (feelings), Cittanupassana (mind), Dhammanupassana (dharmas)
- Eightfold Path: Samma Ditthi, Samma Sankappa, Samma Vaca, Samma Kammanta, Samma Ajiva, Samma Vayama, Samma Sati, Samma Samadhi

ABHIDHARMA:
- "Abhidharma", "Abhidharmakosa" (Vasubandhu), "Abhidharmasamuccaya" (Asanga)
- Samskrita (conditioned), Asamskrita (unconditioned), Hetu (cause), Pratyaya (condition), Phala (result)
- "Dharma-lakshana" (characteristics), "Svabhava" (self-nature)

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
- Key texts: "Trimsika" (Thirty Verses), "Vimsatika" (Twenty Verses), "Yogacharabhumi", "Mahayanasamgraha", "Bodhicharyavatara"

SUTRA CHARACTERS:
- Disciples: Shariputra, Mahakashyapa, Mahamaudgalyayana, Subhuti, Ananda, Rahula, Mahaprajapati, Yashodhara
- Lotus Sutra: Prabhutaratna, Vishishtacharitra, Bhaishajyaraja, Gadgadasvara
- Avatamsaka: Sudhana, Gandavyuha

PRACTICE TERMS:
- "Gatha/Gathas" (mindfulness poems), "Touching the Earth", "Beginning Anew"
- "Dharma sharing", "Dharma rain", "Dharmacharya" (Dharma teacher)
- "Inviting the Bell", "Noble Silence", "Lazy Day"
- "Walking Meditation", "Sitting Meditation", "Deep Relaxation"
- "Lamp Transmission", "Rains Retreat", "Pebble Meditation", "Flower Watering"

COMMUNITY:
- "Order of Interbeing" (Tiep Hien), "Parallax Press", "The Mindfulness Bell"
- "Wake Up" (young practitioners), "ARISE Sangha", "Earth Holder Sangha"

HISTORICAL FIGURES:
- Asanga (Yogacara co-founder), Dignaga, Dharmakirti, Kumarajiva (translator)
- Buddhaghosa, Bodhidharma, Zhiyi (Tiantai), Shantideva, Chandrakirti, Atisha, Milarepa

VIETNAMESE TERMS:
- "Thien" (Zen), "Lam Te" (Linji school), "Lieu Quan" (sub-lineage)
- "An Lac" (peace), "Chanh Niem" (right mindfulness), "Vo Thuong" (impermanence), "Vo Nga" (non-self)
- "Bo Tat" (Bodhisattva), "Quan The Am" (Avalokiteshvara), "Tam Bao" (Three Jewels)
- "Truc Lam" (Bamboo Forest school), "Cong Phu" (daily liturgy)

JAPANESE ZEN TERMS:
- Kinhin (walking meditation), Dokusan (private interview), Teisho (dharma talk)
- Roshi (master), Zendo (hall), Zafu (cushion), Gassho (palms together), Mokugyo (wooden fish)

TIBETAN TERMS:
- Tonglen, Dzogchen, Rinpoche, Tulku, Bardo, Vajra, Vajrayana, Rigpa, Mahamudra"""


frontend_settings = {
    key: os.environ[key]
    for key in ("DICTATION_FRONTEND_ORIGIN", "DICTATION_ALLOW_LOCALHOST")
    if key in os.environ
}
secrets = [
    modal.Secret.from_name("streaming-dictation-auth"),
    modal.Secret.from_name("streaming-dictation-anthropic"),
    modal.Secret.from_name("streaming-dictation-deepgram"),
]
if frontend_settings:
    secrets.append(modal.Secret.from_dict(frontend_settings))


@app.cls(
    image=image,
    secrets=secrets,
    scaledown_window=60,
    # 3 hours. The clock starts when the operator presses Start, which is before
    # the talk begins — mic checks, waiting for the room to settle, a false start.
    # Sized at exactly the expected two hours it had no margin at all, and the
    # failure it aimed at was the socket dying during the closing minutes.
    # AUTH_TIMEOUT_SECONDS above is what keeps a silent client from pinning this.
    timeout=10800,
)
@modal.concurrent(max_inputs=10)
class StreamingDictation:
    @modal.enter()
    def setup_client(self):
        import anthropic
        self.client = anthropic.Anthropic(timeout=4.0, max_retries=0)

    @modal.asgi_app()
    def web(self):
        import websockets
        from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware

        web_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        # Fail fast: web() runs once per container, so a missing or empty secret
        # becomes a visible crash loop instead of a per-request KeyError raised
        # after ws.accept() has already succeeded.
        expected_token = os.environ["BEARER_TOKEN"]
        if not expected_token.strip():
            raise RuntimeError("BEARER_TOKEN is set but empty")
        # The client sends passwordInput.value.trim(), and the comparison below is
        # exact. A secret stored with a trailing newline (echo adds one; so does
        # copy-paste) would therefore reject every correct password with a crisp
        # "Invalid password." Crash loudly at startup instead of lying per login.
        if expected_token != expected_token.strip():
            raise RuntimeError("BEARER_TOKEN has leading or trailing whitespace")

        # Same treatment for the Deepgram key. Read inside the handler it raised
        # KeyError after accept(), after auth and after the client had been told
        # "authenticated" — no error frame, close 1011, reconnect forever: the exact
        # undiagnosable shape of the incident this work exists to remove, for the
        # very upstream that caused it.
        deepgram_key = os.environ["DEEPGRAM_API_KEY"]
        if not deepgram_key.strip():
            raise RuntimeError("DEEPGRAM_API_KEY is set but empty")

        secret = expected_token.encode("utf-8")
        login_limit = RateLimit(burst=10, per_second=0.5)
        socket_limit = RateLimit(burst=10, per_second=1)
        pending_handshakes = 0
        allowed_origins = {os.getenv("DICTATION_FRONTEND_ORIGIN", "https://bbuchalter.github.io")}
        if os.getenv("DICTATION_ALLOW_LOCALHOST") == "1":
            allowed_origins.add("http://localhost:8080")

        web_app.add_middleware(
            CORSMiddleware,
            allow_origins=sorted(allowed_origins),
            allow_methods=["GET"],
            allow_headers=["Authorization", "X-Client-Version"],
            allow_credentials=False,
            max_age=600,
        )

        def build_deepgram_url():
            return (
                "wss://api.deepgram.com/v1/listen"
                "?model=nova-3"
                "&language=en"
                "&punctuate=true"
                "&smart_format=true"
            )

        def polish_text(client, raw: str, context: str) -> str:
            if not raw.strip():
                return ""
            user_message = (
                f"<context>{context}</context>\n"
                f"<raw>{raw}</raw>\n"
                f"Output ONLY the cleaned version of the text in <raw>. "
                f"No commentary, no explanations, no prefixes. Just the cleaned text."
            )
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                polished = response.content[0].text.strip()
                raw_words = words(raw)
                polished_words = words(polished)
                if (
                    polished
                    and len(polished) <= len(raw) + 2 * len(raw_words) + 4
                    and polished_words in (raw_words, correct_aliases(raw_words))
                ):
                    return polished
            except Exception:
                pass
            return raw

        @web_app.get("/auth")
        async def auth(response: Response, authorization: str = Header(default="")):
            if not login_limit.allow():
                raise HTTPException(status_code=429, detail="Too many login attempts")
            scheme, _, tok = authorization.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(tok.encode("utf-8"), secret):
                raise HTTPException(status_code=401, detail="invalid token")
            expiry = int(time.time()) + SESSION_TTL_SECONDS
            response.headers["Cache-Control"] = "no-store"
            return {
                "ok": True,
                "min_client_epoch": MIN_CLIENT_EPOCH,
                "session": f"{expiry}.{session_signature(secret, expiry)}",
            }

        @web_app.websocket("/stream")
        async def stream(ws: WebSocket):
            nonlocal pending_handshakes
            origin = ws.headers.get("origin")
            if (
                "token" in ws.query_params
                or (origin is not None and origin not in allowed_origins)
                or pending_handshakes >= MAX_PENDING_HANDSHAKES
                or not socket_limit.allow()
            ):
                await ws.close()
                return

            pending_handshakes += 1
            try:
                await ws.accept()
                try:
                    first = await asyncio.wait_for(ws.receive(), timeout=AUTH_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    await ws.close(code=4002, reason="Auth timeout")
                    return
                if first["type"] == "websocket.disconnect":
                    return
                if not valid_session(secret, first.get("text")):
                    await ws.close(code=4001, reason="Unauthorized")
                    return
            finally:
                pending_handshakes -= 1

            await ws.send_json({"type": "status", "data": "authenticated"})

            # Connect to Deepgram
            deepgram_url = build_deepgram_url()
            try:
                stt_ws = await websockets.connect(
                    deepgram_url,
                    additional_headers={"Authorization": f"Token {deepgram_key}"},
                )
            except Exception:
                await ws.send_json({"type": "error", "data": "Deepgram connection failed"})
                # 4003 = upstream permanently failed. A revoked key is not fixed by
                # retrying. Today's client still reconnects on this code; Group A
                # finding 6 teaches it to stop. The error frame above is what makes
                # the real cause visible in the meantime.
                await ws.close(code=4003, reason="Upstream unavailable")
                return

            context = ""
            browser_done = asyncio.Event()

            async def forward_audio():
                try:
                    while True:
                        data = await ws.receive()
                        if data["type"] == "websocket.disconnect":
                            break
                        if audio := data.get("bytes"):
                            if len(audio) > MAX_AUDIO_FRAME_BYTES:
                                await ws.close(code=1009)
                                return
                            await stt_ws.send(audio)
                        elif "text" in data and data["text"] == "EOS":
                            await stt_ws.send(json.dumps({"type": "CloseStream"}))
                            browser_done.set()
                            break
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def process_transcripts():
                nonlocal context
                try:
                    async for message in stt_ws:
                        msg = json.loads(message)
                        if msg.get("type") == "Results" and msg.get("is_final"):
                            alternatives = msg.get("channel", {}).get("alternatives", [])
                            if alternatives:
                                raw_text = alternatives[0].get("transcript", "")
                                if raw_text.strip():
                                    polished = await asyncio.to_thread(
                                        polish_text, self.client, raw_text, context
                                    )
                                    if polished:
                                        await ws.send_json({"type": "text", "data": polished})
                                        context = " ".join(raw_text.split()[-50:])
                except websockets.exceptions.ConnectionClosed:
                    if not browser_done.is_set():
                        try:
                            await ws.send_json({"type": "status", "data": "disconnected"})
                        except Exception:
                            pass
                except Exception:
                    pass

            tasks = []
            try:
                await ws.send_json({"type": "status", "data": "listening"})
                audio_task = asyncio.create_task(forward_audio())
                transcript_task = asyncio.create_task(process_transcripts())
                tasks.extend((audio_task, transcript_task))
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if browser_done.is_set() and not transcript_task.done():
                    await asyncio.wait({transcript_task}, timeout=DRAIN_TIMEOUT_SECONDS)
                if browser_done.is_set():
                    await ws.close(code=1000)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await stt_ws.close()

        return web_app
