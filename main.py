import os
from pathlib import PurePosixPath

from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient
from fastapi import FastAPI, File, HTTPException, UploadFile
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

# Document Intelligence para extraer texto de PDFs (mismo credential de Entra ID)
DOCUMENT_INTELLIGENCE_ENDPOINT = os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"]
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB
docintel_client = DocumentIntelligenceClient(
    endpoint=DOCUMENT_INTELLIGENCE_ENDPOINT, credential=credential
)

# Blob Storage donde se guarda el Markdown extraído (autenticación con Entra ID)
STORAGE_ACCOUNT_NAME = os.environ["STORAGE_ACCOUNT_NAME"]
STORAGE_CONTAINER = os.getenv("STORAGE_CONTAINER", "extraido")
blob_service = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
    credential=credential,
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


@app.post("/pdf/extract")
async def extract_pdf(file: UploadFile = File(...)):
    """Extrae el texto de un PDF en Markdown (modelo prebuilt-layout)."""
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="El archivo debe ser un PDF")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío")
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail=f"El PDF supera los {MAX_PDF_BYTES // (1024 * 1024)} MB")

    poller = await docintel_client.begin_analyze_document(
        "prebuilt-layout",
        body=content,
        content_type="application/pdf",
        output_content_format="markdown",
    )
    result = await poller.result()

    # Guarda el Markdown junto a su nombre de origen; si se re-sube el PDF, se sobrescribe
    blob_name = f"{PurePosixPath(file.filename or 'documento').stem}.md"
    blob = blob_service.get_blob_client(STORAGE_CONTAINER, blob_name)
    await blob.upload_blob(
        result.content.encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type="text/markdown; charset=utf-8"),
    )

    return {
        "filename": file.filename,
        "pages": len(result.pages or []),
        "blob": blob.url
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
