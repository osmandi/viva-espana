"""Ingesta de las guías turísticas en Azure AI Search.

Lee los .md del contenedor de Blob Storage, los divide en fragmentos, (opcional) les
añade contexto generado por un LLM, calcula los embeddings y los sube al índice.

    uv run --env-file .env python ingest.py                      # con contexto -> guias-ctx
    uv run --env-file .env python ingest.py --sin-contexto       # sin contexto -> guias-base
    uv run --env-file .env python ingest.py --dry-run            # solo muestra los fragmentos
    uv run --env-file .env python ingest.py --evaluar preguntas.json

Autenticación: Microsoft Entra ID (az login). No usa claves.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

import tiktoken
from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob.aio import BlobServiceClient
from openai import AsyncAzureOpenAI

INDEX_CTX = "guias-ctx"
INDEX_BASE = "guias-base"

EMBEDDING_DEPLOYMENT = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = 1536
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")

MAX_TOKENS = 600  # tamaño máximo de un fragmento (sin contar el contexto)
OVERLAP_TOKENS = 80  # solapamiento entre fragmentos consecutivos de una sección
MIN_TOKENS = 30  # los fragmentos más cortos se descartan (cabeceras sueltas)
MAX_DOC_TOKENS = 60_000  # tope del documento que se envía al LLM para el contexto
EMBED_BATCH = 16
UPLOAD_BATCH = 100
LLM_CONCURRENCY = 8

enc = tiktoken.get_encoding("cl100k_base")  # tokenizador de text-embedding-3-small


def n_tokens(text: str) -> int:
    return len(enc.encode(text))


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

HEADER_RE = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")


def split_sections(markdown: str) -> list[tuple[str, str]]:
    """Divide por encabezados # a ###. Devuelve (ruta_de_títulos, texto)."""
    markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)  # PageBreak, etc.
    path: list[str] = []
    buf: list[str] = []
    sections: list[tuple[str, str]] = []
    in_code = False

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append((" > ".join(path), text))
        buf.clear()

    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADER_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path[:] = path[: level - 1] + [m.group(2)]
        else:
            buf.append(line)
    flush()
    return sections


def split_blocks(text: str) -> list[str]:
    """Párrafos y tablas como bloques indivisibles (una tabla = un bloque)."""
    blocks: list[str] = []
    for raw in re.split(r"\n\s*\n", text):
        block = raw.strip()
        if block:
            blocks.append(block)
    return blocks


def hard_split(block: str) -> list[str]:
    """Parte por tokens un bloque que no cabe en un fragmento."""
    tokens = enc.encode(block)
    step = MAX_TOKENS - OVERLAP_TOKENS
    return [enc.decode(tokens[i : i + MAX_TOKENS]) for i in range(0, len(tokens), step)]


def chunk_section(text: str) -> list[str]:
    """Agrupa bloques hasta MAX_TOKENS, con solapamiento entre fragmentos."""
    blocks: list[str] = []
    for b in split_blocks(text):
        blocks.extend(hard_split(b) if n_tokens(b) > MAX_TOKENS else [b])

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for b in blocks:
        t = n_tokens(b)
        if current and size + t > MAX_TOKENS:
            chunks.append("\n\n".join(current))
            tail = current[-1]
            # El último bloque pasa al siguiente fragmento como solapamiento
            current, size = ([tail], n_tokens(tail)) if n_tokens(tail) <= OVERLAP_TOKENS else ([], 0)
        current.append(b)
        size += t
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_document(markdown: str) -> list[dict]:
    out = []
    for title, text in split_sections(markdown):
        for piece in chunk_section(text):
            if n_tokens(piece) >= MIN_TOKENS:
                out.append({"title": title, "text": piece})
    return out


# ---------------------------------------------------------------------------
# Contexto (LLM) y embeddings
# ---------------------------------------------------------------------------

CONTEXT_PROMPT = """<documento>
{documento}
</documento>
Aquí está un fragmento de ese documento:
<fragmento>
{fragmento}
</fragmento>
Escribe una o dos frases breves que sitúen este fragmento dentro del documento \
(lugar, tema, sección) para mejorar su búsqueda. Responde solo con ese contexto."""


class Clients:
    def __init__(self) -> None:
        self.credential = DefaultAzureCredential()
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            azure_ad_token_provider=get_bearer_token_provider(
                self.credential, "https://cognitiveservices.azure.com/.default"
            ),
            api_version="2025-04-01-preview",
            max_retries=8,  # reintenta 429/5xx respetando Retry-After
        )
        self.llm_slots = asyncio.Semaphore(LLM_CONCURRENCY)

    async def close(self) -> None:
        await self.openai.close()
        await self.credential.close()


async def contextualizar(c: Clients, documento: str, fragmento: str) -> str:
    # El documento va primero y es idéntico en todas las llamadas del mismo archivo:
    # permite el prompt caching de Azure OpenAI.
    async with c.llm_slots:
        try:
            r = await c.openai.chat.completions.create(
                model=CHAT_DEPLOYMENT,
                messages=[
                    {
                        "role": "user",
                        "content": CONTEXT_PROMPT.format(documento=documento, fragmento=fragmento),
                    }
                ],
                max_tokens=150,
                temperature=0,
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as e:  # un fragmento sin contexto es mejor que abortar el archivo
            print(f"    aviso: sin contexto para un fragmento ({type(e).__name__})", file=sys.stderr)
            return ""


async def embed(c: Clients, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        r = await c.openai.embeddings.create(
            model=EMBEDDING_DEPLOYMENT, input=texts[i : i + EMBED_BATCH]
        )
        vectors.extend(d.embedding for d in r.data)
    return vectors


# ---------------------------------------------------------------------------
# Índice de AI Search
# ---------------------------------------------------------------------------


def index_definition(name: str) -> SearchIndex:
    return SearchIndex(
        name=name,
        fields=[
            SimpleField(name="chunk_id", type=SearchFieldDataType.STRING, key=True),
            SimpleField(name="parent_id", type=SearchFieldDataType.STRING, filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.STRING),
            SearchableField(name="chunk", type=SearchFieldDataType.STRING),  # texto indexado
            SimpleField(name="chunk_raw", type=SearchFieldDataType.STRING),  # fragmento original
            SearchField(
                name="text_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.SINGLE),
                searchable=True,
                vector_search_dimensions=EMBEDDING_DIMENSIONS,
                vector_search_profile_name="perfil-vec",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
            profiles=[VectorSearchProfile(name="perfil-vec", algorithm_configuration_name="hnsw")],
        ),
    )


async def ensure_index(index_client: SearchIndexClient, name: str, recrear: bool) -> None:
    if recrear:
        try:
            await index_client.delete_index(name)
            print(f"Índice '{name}' eliminado")
        except ResourceNotFoundError:
            pass
    await index_client.create_or_update_index(index_definition(name))


# ---------------------------------------------------------------------------
# Lectura de documentos
# ---------------------------------------------------------------------------


async def read_documents(args, credential) -> list[tuple[str, str]]:
    """Devuelve [(nombre, markdown)] desde una carpeta local o desde Blob Storage."""
    if args.local:
        paths = sorted(Path(args.local).glob("*.md"))
        docs = [(p.name, p.read_text(encoding="utf-8")) for p in paths]
    else:
        account = os.environ["STORAGE_ACCOUNT_NAME"]
        container = os.getenv("STORAGE_CONTAINER", "extraido")
        async with BlobServiceClient(
            f"https://{account}.blob.core.windows.net", credential=credential
        ) as blobs:
            cc = blobs.get_container_client(container)
            docs = []
            async for b in cc.list_blobs():
                if b.name.lower().endswith(".md"):
                    data = await (await cc.download_blob(b.name)).readall()
                    docs.append((PurePosixPath(b.name).name, data.decode("utf-8")))
            docs.sort()
    if args.archivo:
        docs = [d for d in docs if d[0] == args.archivo]
    if args.max_docs:
        docs = docs[: args.max_docs]
    return docs


# ---------------------------------------------------------------------------
# Ingesta
# ---------------------------------------------------------------------------


async def ingest_document(c: Clients, search: SearchClient, nombre: str, markdown: str, con_contexto: bool):
    fragmentos = chunk_document(markdown)
    if not fragmentos:
        print(f"  {nombre}: sin fragmentos útiles, se omite")
        return

    if con_contexto:
        documento = enc.decode(enc.encode(markdown)[:MAX_DOC_TOKENS])
        contextos = await asyncio.gather(*(contextualizar(c, documento, f["text"]) for f in fragmentos))
    else:
        contextos = [""] * len(fragmentos)

    # Prefijo estructural (ruta de títulos) + contexto del LLM + fragmento
    textos = [
        "\n\n".join(p for p in (f["title"], ctx, f["text"]) if p)
        for f, ctx in zip(fragmentos, contextos)
    ]
    vectores = await embed(c, textos)

    # Reindexado limpio: elimina lo anterior de este archivo
    viejos = [
        {"chunk_id": d["chunk_id"]}
        async for d in await search.search("*", filter=f"parent_id eq '{nombre}'", select=["chunk_id"])
    ]
    if viejos:
        await search.delete_documents(viejos)

    docs = [
        {
            "chunk_id": f"{re.sub(r'[^A-Za-z0-9_=-]', '_', nombre)}-{i}",
            "parent_id": nombre,
            "title": f["title"],
            "chunk": textos[i],
            "chunk_raw": f["text"],
            "text_vector": vectores[i],
        }
        for i, f in enumerate(fragmentos)
    ]
    for i in range(0, len(docs), UPLOAD_BATCH):
        await search.upload_documents(docs[i : i + UPLOAD_BATCH])
    print(f"  {nombre}: {len(docs)} fragmentos indexados")


def show_dry_run(nombre: str, markdown: str) -> None:
    fragmentos = chunk_document(markdown)
    sizes = [n_tokens(f["text"]) for f in fragmentos]
    print(f"\n{nombre}: {len(fragmentos)} fragmentos"
          + (f" (tokens: min {min(sizes)}, máx {max(sizes)})" if sizes else ""))
    for i, f in enumerate(fragmentos):
        preview = " ".join(f["text"].split())[:90]
        print(f"  [{i:02d}] {sizes[i]:>4} tok | {f['title'] or '(sin título)'} | {preview}…")


# ---------------------------------------------------------------------------
# Evaluación
# ---------------------------------------------------------------------------


async def evaluar(c: Clients, path: str, k: int = 5) -> None:
    preguntas = json.loads(Path(path).read_text(encoding="utf-8"))
    endpoint = os.environ["SEARCH_ENDPOINT"]
    aciertos = {INDEX_BASE: 0, INDEX_CTX: 0}

    clients = {n: SearchClient(endpoint, n, c.credential) for n in aciertos}
    try:
        for q in preguntas:
            texto, esperado = q["pregunta"], q["esperado"].lower()
            vector = (await embed(c, [texto]))[0]
            fila = []
            for name, sc in clients.items():
                results = await sc.search(
                    search_text=texto,
                    vector_queries=[
                        VectorizedQuery(vector=vector, k_nearest_neighbors=30, fields="text_vector")
                    ],
                    select=["title", "chunk_raw"],
                    top=k,
                )
                hit = False
                async for r in results:
                    if esperado in f"{r['title']} {r['chunk_raw']}".lower():
                        hit = True
                        break
                aciertos[name] += hit
                fila.append(f"{name}: {'OK' if hit else '--'}")
            print(f"  {' | '.join(fila)} | {texto}")
    finally:
        for sc in clients.values():
            await sc.close()

    total = len(preguntas)
    print(f"\nAciertos en el top {k} de {total} preguntas:")
    for name, n in aciertos.items():
        print(f"  {name}: {n}/{total} ({100 * n / total:.0f} %)")


# ---------------------------------------------------------------------------


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sin-contexto", action="store_true", help="omite el contexto del LLM e indexa en guias-base")
    p.add_argument("--archivo", help="procesa solo este .md (por nombre)")
    p.add_argument("--max-docs", type=int, help="limita el número de documentos")
    p.add_argument("--recrear", action="store_true", help="borra y recrea el índice antes de indexar")
    p.add_argument("--dry-run", action="store_true", help="solo muestra los fragmentos, sin llamar a OpenAI ni a Search")
    p.add_argument("--local", metavar="CARPETA", help="lee los .md de una carpeta local en lugar de Blob Storage")
    p.add_argument("--evaluar", metavar="PREGUNTAS.JSON", help="compara guias-base y guias-ctx con las preguntas del archivo")
    args = p.parse_args()

    if args.dry_run:
        credential = DefaultAzureCredential()
        try:
            for nombre, md in await read_documents(args, credential):
                show_dry_run(nombre, md)
        finally:
            await credential.close()
        return

    c = Clients()
    try:
        if args.evaluar:
            await evaluar(c, args.evaluar)
            return

        index_name = INDEX_BASE if args.sin_contexto else INDEX_CTX
        endpoint = os.environ["SEARCH_ENDPOINT"]
        docs = await read_documents(args, c.credential)
        if not docs:
            print("No hay documentos .md que procesar")
            return

        print(f"Índice: {index_name} | contexto: {'no' if args.sin_contexto else 'sí'} | documentos: {len(docs)}")
        async with SearchIndexClient(endpoint, c.credential) as index_client:
            await ensure_index(index_client, index_name, args.recrear)
        async with SearchClient(endpoint, index_name, c.credential) as search:
            for nombre, md in docs:
                await ingest_document(c, search, nombre, md, not args.sin_contexto)
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
