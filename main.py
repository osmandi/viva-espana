import os

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from openai import AsyncAzureOpenAI
from pydantic import BaseModel, Field

app = FastAPI()

# Configuración del despliegue en Microsoft Foundry (ver `terraform output foundry_endpoint`)
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")

# Autenticación con Microsoft Entra ID (sin claves): usa `az login` en local
credential = DefaultAzureCredential()
client = AsyncAzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    azure_ad_token_provider=get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    ),
    api_version="2025-04-01-preview",
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="Mensaje del usuario")


async def llm_router(message: str):
    stream = await client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,  # nombre del despliegue, no del modelo
        messages=[{"role": "user", "content": message}],
        stream=True,
    )
    async for chunk in stream:
        # Algunos chunks (p. ej. filtros de contenido) llegan sin choices
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


@app.get("/")
def read_root():
    return {"message": "Hello, World!"}


@app.post("/chat")
async def chat(request: ChatRequest):
    return StreamingResponse(
        llm_router(request.message),
        media_type="text/plain",
        headers={"X-Accel-Buffering": "no"},  # evita el buffering en nginx
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
