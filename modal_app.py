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
