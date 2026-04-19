import asyncio
import json
import modal

app = modal.App("streaming-dictation")

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "anthropic",
    "fastapi",
    "websockets",
)

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


@app.cls(
    image=image,
    secrets=[
        modal.Secret.from_name("streaming-dictation-auth"),
        modal.Secret.from_name("streaming-dictation-anthropic"),
        modal.Secret.from_name("streaming-dictation-deepgram"),
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
            # Auth — must accept before we can close with a code
            await ws.accept()
            token = ws.query_params.get("token", "")
            expected = os.environ["BEARER_TOKEN"]
            if token != expected:
                await ws.close(code=4001, reason="Unauthorized")
                return

            # Connect to Deepgram
            deepgram_url = build_deepgram_url()
            deepgram_key = os.environ["DEEPGRAM_API_KEY"]
            try:
                stt_ws = await websockets.connect(
                    deepgram_url,
                    additional_headers={"Authorization": f"Token {deepgram_key}"},
                )
            except Exception as e:
                await ws.send_json({"type": "error", "data": f"Deepgram connection failed: {type(e).__name__}: {e}"})
                await ws.close()
                return

            await ws.send_json({"type": "status", "data": "listening"})

            context = ""
            browser_done = asyncio.Event()

            async def forward_audio():
                try:
                    while True:
                        data = await ws.receive()
                        if data["type"] == "websocket.disconnect":
                            break
                        if "bytes" in data and data["bytes"]:
                            await stt_ws.send(data["bytes"])
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

            try:
                await asyncio.gather(forward_audio(), process_transcripts())
            finally:
                try:
                    await stt_ws.close()
                except Exception:
                    pass

        return web_app
