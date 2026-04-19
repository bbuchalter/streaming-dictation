import modal

app = modal.App("streaming-dictation")

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "anthropic",
    "fastapi",
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
    ],
    scaledown_window=60,
)
@modal.concurrent(max_inputs=10)
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
                try:
                    with self.client.messages.stream(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=256,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": user_message}],
                    ) as stream:
                        for text in stream.text_stream:
                            if text:
                                yield f"data: {text}\n\n"
                except Exception:
                    pass
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return web_app
