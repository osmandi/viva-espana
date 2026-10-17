from fastapi import FastAPI
import anyio
from fastapi.responses import StreamingResponse

app = FastAPI()

async def llm_router():
    for token in "This is a simple message where I was in multiple times".split():
        yield f"{token} "
        await anyio.sleep(0.3)  # simula la latencia entre tokens

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

@app.get("/chat")
async def chat():
    return StreamingResponse(
        llm_router(),
        media_type="text/plain",
        headers={"X-Accel-Buffering": "no"},  # evita el buffering en nginx
    )

@app.get("/health")
async def health():
    return {"status": "ok"}
