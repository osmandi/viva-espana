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
import csv
import json
import os
import re
import sys
import time
from pathlib import Path, PurePosixPath

import tiktoken
from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    BM25SimilarityAlgorithm,
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob.aio import BlobServiceClient
from openai import AsyncAzureOpenAI, RateLimitError

FUENTES_CSV = "guias-turisticas/fuentes.csv"  # metadatos de cada guía
INDEX_CTX = "guias-ctx"
INDEX_BASE = "guias-base"

EMBEDDING_DEPLOYMENT = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = 1536
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")

MAX_TOKENS = 600  # tamaño máximo de un fragmento (sin contar el contexto)
OVERLAP_TOKENS = 80  # solapamiento entre fragmentos consecutivos de una sección
MIN_TOKENS = 30  # los fragmentos más cortos se descartan (cabeceras sueltas)
# Contexto del LLM: el documento completo se envía una vez por grupo de fragmentos y el
# modelo devuelve un contexto para cada uno (JSON). Enviarlo una vez por fragmento
# multiplicaba los tokens por ~15 y agotaba los tokens por minuto del despliegue.
CONTEXT_GROUP_SIZE = 12  # fragmentos por llamada
CONTEXT_TOKENS_PER_ITEM = 120  # salida máxima por contexto
MAX_FULL_DOC_TOKENS = 100_000  # documentos mayores: índice de títulos + extracto del grupo
CONTEXT_WINDOW_TOKENS = 20_000  # tamaño del extracto en documentos mayores
OUTLINE_MAX_TOKENS = 600  # índice de títulos del documento
CONTEXT_TPM = int(os.getenv("CONTEXT_TPM", "120000"))  # por debajo de la capacidad (150K TPM)
CONTEXT_RETRIES = 6
EMBED_BATCH = 16
UPLOAD_BATCH = 100
LLM_CONCURRENCY = 4

enc = tiktoken.get_encoding("cl100k_base")  # tokenizador de text-embedding-3-small


def n_tokens(text: str) -> int:
    return len(enc.encode(text))


# ---------------------------------------------------------------------------
# Metadatos de las guías (fuentes.csv)
# ---------------------------------------------------------------------------


def stem_key(nombre: str) -> str:
    """Clave de cruce: nombre sin extensión (el CSV lista .pdf y en Blob están los .md)."""
    return PurePosixPath(nombre).stem.casefold()


def load_fuentes(path: str) -> dict[str, dict]:
    """Lee fuentes.csv y lo indexa por nombre de archivo sin extensión."""
    if not Path(path).exists():
        print(f"aviso: no existe {path}; se indexa sin metadatos", file=sys.stderr)
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        fuentes = {}
        for row in csv.DictReader(f):
            row = {k.strip(): (v or "").strip() for k, v in row.items()}
            fuentes[stem_key(row["archivo"])] = row
    return fuentes


def metadata_fields(meta: dict | None) -> dict:
    """Campos de metadatos que se guardan en cada fragmento del índice."""
    if not meta:
        return {}
    return {
        "fuente_titulo": meta["titulo"],
        "fuente_url": meta["url"],
        "organismo": meta["organismo"],
        "anio": int(meta["anio"]) if meta["anio"].isdigit() else None,
        "region": meta["region"],
        "tipo": meta["tipo"],
        "publico": meta["publico"],
    }


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

CONTEXT_PROMPT = """{guia}
<documento>
{documento}
</documento>
Estos son fragmentos de ese documento, numerados:
<fragmentos>
{fragmentos}
</fragmentos>
Para CADA fragmento escribe una o dos frases breves que lo sitúen dentro del documento \
(lugar, tema, sección) para mejorar su búsqueda. Devuelve un contexto por cada id."""

# Structured outputs: obliga a devolver exactamente esta forma de JSON
CONTEXT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "contextos",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "contextos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "contexto": {"type": "string"}},
                        "required": ["id", "contexto"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["contextos"],
            "additionalProperties": False,
        },
    },
}


class TokenRateLimiter:
    """Limita los tokens enviados por minuto para no provocar errores 429."""

    def __init__(self, tokens_per_minute: int) -> None:
        self.limit = tokens_per_minute
        self.sent: list[tuple[float, int]] = []  # (instante, tokens) del último minuto
        self.lock = asyncio.Lock()

    async def acquire(self, tokens: int) -> None:
        tokens = min(tokens, self.limit)
        async with self.lock:
            while True:
                now = time.monotonic()
                self.sent = [(t, n) for t, n in self.sent if now - t < 60]
                if sum(n for _, n in self.sent) + tokens <= self.limit:
                    self.sent.append((now, tokens))
                    return
                await asyncio.sleep(60 - (now - self.sent[0][0]) + 0.1)


def outline(markdown: str) -> str:
    """Índice de títulos del documento (# a ###), recortado a OUTLINE_MAX_TOKENS."""
    titles = [line.strip() for line in markdown.splitlines() if HEADER_RE.match(line)]
    return enc.decode(enc.encode("\n".join(titles))[:OUTLINE_MAX_TOKENS])


def excerpt(doc_tokens: list[int], center: int) -> str:
    """Extracto de CONTEXT_WINDOW_TOKENS tokens centrado en la posición del fragmento."""
    start = max(0, min(center - CONTEXT_WINDOW_TOKENS // 2, len(doc_tokens) - CONTEXT_WINDOW_TOKENS))
    return enc.decode(doc_tokens[start : start + CONTEXT_WINDOW_TOKENS])


class Clients:
    def __init__(self) -> None:
        self.credential = DefaultAzureCredential()
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            azure_ad_token_provider=get_bearer_token_provider(
                self.credential, "https://cognitiveservices.azure.com/.default"
            ),
            api_version="2025-04-01-preview",
            max_retries=3,  # reintentos rápidos del SDK; los 429 largos los gestiona contextualizar
        )
        self.llm_slots = asyncio.Semaphore(LLM_CONCURRENCY)
        self.llm_tpm = TokenRateLimiter(CONTEXT_TPM)
        self.context_failures = 0  # fragmentos que quedaron sin contexto tras los reintentos

    async def close(self) -> None:
        await self.openai.close()
        await self.credential.close()


async def contextualizar_grupo(c: Clients, guia: str, documento: str, fragmentos: list[str]) -> list[str]:
    """Pide al LLM un contexto por fragmento en una sola llamada. Devuelve "" en los que fallen."""
    contextos = [""] * len(fragmentos)
    pendientes = list(range(len(fragmentos)))
    error: Exception | None = None

    for intento in range(1, CONTEXT_RETRIES + 1):
        lista = "\n\n".join(f"[{i + 1}]\n{fragmentos[i]}" for i in pendientes)
        prompt = CONTEXT_PROMPT.format(guia=guia, documento=documento, fragmentos=lista)
        max_tokens = CONTEXT_TOKENS_PER_ITEM * len(pendientes) + 200
        # Azure cuenta en el límite los tokens de entrada más max_tokens
        await c.llm_tpm.acquire(n_tokens(prompt) + max_tokens)
        try:
            async with c.llm_slots:
                r = await c.openai.chat.completions.create(
                    model=CHAT_DEPLOYMENT,
                    messages=[{"role": "user", "content": prompt}],
                    response_format=CONTEXT_SCHEMA,
                    max_tokens=max_tokens,
                    temperature=0,
                )
            items = json.loads(r.choices[0].message.content or "{}")["contextos"]
            for item in items:
                i = item["id"] - 1
                if i in pendientes and item["contexto"].strip():
                    contextos[i] = item["contexto"].strip()
            # Si el modelo omitió algún id, se reintenta solo con esos
            pendientes = [i for i in pendientes if not contextos[i]]
            if not pendientes:
                return contextos
        except RateLimitError as e:
            error = e
            retry_after = e.response.headers.get("retry-after")
            await asyncio.sleep(float(retry_after) if retry_after else min(60, 5 * 2**intento))
        except (json.JSONDecodeError, KeyError, TypeError) as e:  # respuesta truncada o mal formada
            error = e

    # Un fragmento sin contexto es mejor que abortar el archivo; se avisa al final
    c.context_failures += len(pendientes)
    motivo = type(error).__name__ if error else "contextos omitidos por el modelo"
    print(f"    aviso: {len(pendientes)} fragmentos sin contexto ({motivo})", file=sys.stderr)
    return contextos


async def contextualizar_documento(c: Clients, markdown: str, fragmentos: list[str], meta: dict | None) -> list[str]:
    """Contexto de todos los fragmentos de un documento, en grupos de CONTEXT_GROUP_SIZE."""
    guia = f"Guía: {meta['titulo']} ({meta['region']})" if meta else ""
    limpio = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    doc_tokens = enc.encode(limpio)
    completo = len(doc_tokens) <= MAX_FULL_DOC_TOKENS

    def documento_para(grupo: range) -> str:
        if completo:
            return limpio
        # Documento muy grande: índice de títulos + extracto centrado en el grupo
        pos = limpio.find(fragmentos[grupo[0]][:120])
        centro = len(enc.encode(limpio[:pos])) if pos >= 0 else len(doc_tokens) * grupo[0] // len(fragmentos)
        return f"Índice de títulos:\n{outline(limpio)}\n\nExtracto:\n{excerpt(doc_tokens, centro)}"

    grupos = [range(i, min(i + CONTEXT_GROUP_SIZE, len(fragmentos))) for i in range(0, len(fragmentos), CONTEXT_GROUP_SIZE)]
    resultados = await asyncio.gather(
        *(contextualizar_grupo(c, guia, documento_para(g), [fragmentos[i] for i in g]) for g in grupos)
    )
    return [ctx for grupo in resultados for ctx in grupo]


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
            # Campos de texto para BM25, con el analizador de español (plurales, acentos, raíces)
            SearchableField(name="title", type=SearchFieldDataType.STRING, analyzer_name="es.microsoft"),
            SearchableField(name="chunk", type=SearchFieldDataType.STRING, analyzer_name="es.microsoft"),
            SimpleField(name="chunk_raw", type=SearchFieldDataType.STRING),  # fragmento original
            # Metadatos de fuentes.csv: filtrables (y facetables) para acotar las búsquedas
            SearchableField(name="fuente_titulo", type=SearchFieldDataType.STRING, analyzer_name="es.microsoft"),
            SimpleField(name="fuente_url", type=SearchFieldDataType.STRING),
            SimpleField(name="organismo", type=SearchFieldDataType.STRING, filterable=True, facetable=True),
            SimpleField(name="anio", type=SearchFieldDataType.INT32, filterable=True, sortable=True, facetable=True),
            SearchableField(name="region", type=SearchFieldDataType.STRING, filterable=True, facetable=True),
            SimpleField(name="tipo", type=SearchFieldDataType.STRING, filterable=True, facetable=True),
            SimpleField(name="publico", type=SearchFieldDataType.STRING, filterable=True, facetable=True),
            SearchField(
                name="text_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.SINGLE),
                searchable=True,
                vector_search_dimensions=EMBEDDING_DIMENSIONS,
                vector_search_profile_name="perfil-vec",
            ),
        ],
        # BM25 es el algoritmo por defecto; se declara para fijar sus parámetros
        similarity=BM25SimilarityAlgorithm(k1=1.2, b=0.75),
        vector_search=VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="hnsw",
                    parameters=HnswParameters(m=4, ef_construction=400, ef_search=500, metric="cosine"),
                )
            ],
            profiles=[VectorSearchProfile(name="perfil-vec", algorithm_configuration_name="hnsw")],
        ),
        # Semantic ranker: reordena los mejores resultados por significado (query_type="semantic")
        semantic_search=SemanticSearch(
            default_configuration_name="semantica",
            configurations=[
                SemanticConfiguration(
                    name="semantica",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        content_fields=[SemanticField(field_name="chunk")],
                        keywords_fields=[
                            SemanticField(field_name="fuente_titulo"),
                            SemanticField(field_name="region"),
                        ],
                    ),
                )
            ],
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


async def ingest_document(
    c: Clients, search: SearchClient, nombre: str, markdown: str, con_contexto: bool, meta: dict | None
):
    fragmentos = chunk_document(markdown)
    if not fragmentos:
        print(f"  {nombre}: sin fragmentos útiles, se omite")
        return

    if con_contexto:
        contextos = await contextualizar_documento(c, markdown, [f["text"] for f in fragmentos], meta)
    else:
        contextos = [""] * len(fragmentos)

    # Cabecera de la guía (metadatos) + ruta de títulos + contexto del LLM + fragmento
    cabecera = (
        f"Guía: {meta['titulo']} · {meta['region']} · {meta['tipo']}" if meta else ""
    )
    textos = [
        "\n\n".join(p for p in (cabecera, f["title"], ctx, f["text"]) if p)
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
            **metadata_fields(meta),
        }
        for i, f in enumerate(fragmentos)
    ]
    for i in range(0, len(docs), UPLOAD_BATCH):
        await search.upload_documents(docs[i : i + UPLOAD_BATCH])
    print(f"  {nombre}: {len(docs)} fragmentos indexados" + ("" if meta else " (SIN metadatos)"))


def show_dry_run(nombre: str, markdown: str, meta: dict | None) -> None:
    fragmentos = chunk_document(markdown)
    sizes = [n_tokens(f["text"]) for f in fragmentos]
    print(f"\n{nombre}: {len(fragmentos)} fragmentos"
          + (f" (tokens: min {min(sizes)}, máx {max(sizes)})" if sizes else ""))
    print("  metadatos: " + (json.dumps(metadata_fields(meta), ensure_ascii=False) if meta else "NO ENCONTRADOS en fuentes.csv"))
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
    p.add_argument("--fuentes", default=FUENTES_CSV, metavar="CSV", help=f"CSV con los metadatos de las guías (por defecto: {FUENTES_CSV})")
    p.add_argument("--evaluar", metavar="PREGUNTAS.JSON", help="compara guias-base y guias-ctx con las preguntas del archivo")
    args = p.parse_args()

    fuentes = {} if args.evaluar else load_fuentes(args.fuentes)

    if args.dry_run:
        credential = DefaultAzureCredential()
        try:
            for nombre, md in await read_documents(args, credential):
                show_dry_run(nombre, md, fuentes.get(stem_key(nombre)))
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
                await ingest_document(
                    c, search, nombre, md, not args.sin_contexto, fuentes.get(stem_key(nombre))
                )
        if c.context_failures:
            print(
                f"\nATENCIÓN: {c.context_failures} fragmentos se indexaron SIN contexto "
                "(cuota o errores del modelo). Vuelve a ejecutar el script para esos archivos "
                "antes de evaluar: la comparación con guias-base no sería válida.",
                file=sys.stderr,
            )
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
