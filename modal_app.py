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
