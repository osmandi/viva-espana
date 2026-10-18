"""Herramienta de búsqueda en las guías turísticas, compartida por el chat (/chat) y la voz (/voice).

El modelo decide cuándo llamarla, con qué consulta y con qué filtros (región, tipo,
público). Cada turno usa un RegistroFuentes que numera los fragmentos de todas las
búsquedas de forma continua ([1]..[5], [6]..[10]...) para poder citar las fuentes.
"""

import asyncio
import json
import logging
from itertools import zip_longest

from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AsyncAzureOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger("uvicorn.error")

TOOL_NAME = "buscar_en_guias"
TOP = 5  # fragmentos por búsqueda
CANDIDATES = 50  # vecinos más cercanos que aporta la parte vectorial a la fusión RRF
FILTROS = ("region", "tipo", "publico")
REGION_GENERAL = "España"  # guías nacionales: también cubren cada región


class Fuente(BaseModel):
    """Fragmento de una guía turística usado para generar la respuesta."""

    id: int = Field(description="Número con el que la respuesta cita el fragmento, p. ej. [1]")
    guia: str | None = Field(None, description="Título de la guía")
    seccion: str | None = Field(None, description="Sección de la guía")
    url: str | None = Field(None, description="URL de la guía original")
    organismo: str | None = None
    anio: int | None = None
    region: str | None = None


def fuente(n: int, f: dict) -> Fuente:
    return Fuente(
        id=n,
        guia=f.get("fuente_titulo"),
        seccion=f.get("title"),
        url=f.get("fuente_url"),
        organismo=f.get("organismo"),
        anio=f.get("anio"),
        region=f.get("region"),
    )


def construir_contexto(fragmentos: list[dict], inicio: int = 1) -> str:
    """Fragmentos numerados desde `inicio`, con su guía y sección."""
    bloques = []
    for i, f in enumerate(fragmentos, start=inicio):
        cabecera = f"[{i}] Guía: {f.get('fuente_titulo') or 'desconocida'}"
        if f.get("region"):
            cabecera += f" ({f['region']})"
        if f.get("title"):
            cabecera += f" | Sección: {f['title']}"
        bloques.append(f"{cabecera}\n{f['chunk_raw']}")
    return "\n\n".join(bloques)


class BuscadorGuias:
    """Búsqueda híbrida (BM25 + vectorial, fusionadas con RRF) con filtros por metadatos."""

    def __init__(self, openai: AsyncAzureOpenAI, search: SearchClient, embedding_deployment: str) -> None:
        self.openai = openai
        self.search = search
        self.embedding_deployment = embedding_deployment
        self.valores: dict[str, list[str]] = {}  # valores válidos de cada filtro (facets)
        self._lock = asyncio.Lock()

    async def cargar_valores(self) -> None:
        """Lee del índice los valores existentes de region/tipo/publico (una vez).

        Así la herramienta ofrece al modelo solo valores reales, y se actualizan solos
        al indexar guías nuevas. Si falla, la herramienta funciona sin listas cerradas.
        """
        if self.valores:
            return
        async with self._lock:
            if self.valores:
                return
            try:
                r = await self.search.search("*", facets=[f"{c},count:100" for c in FILTROS], top=0)
                facets = await r.get_facets() or {}
                self.valores = {c: sorted(v["value"] for v in facets.get(c, [])) for c in FILTROS}
            except Exception:
                logger.exception("tools: no se pudieron leer los valores de los filtros")

    def _normalizar(self, campo: str, valor) -> str | None:
        """Devuelve el valor canónico del filtro, o None si no existe (se ignora el filtro)."""
        if not isinstance(valor, str) or not valor.strip():
            return None
        validos = self.valores.get(campo)
        if not validos:
            return valor.strip()
        return next((v for v in validos if v.casefold() == valor.strip().casefold()), None)

    def _filtro(self, region=None, tipo=None, publico=None) -> str | None:
        """Filtro OData. region incluye siempre las guías nacionales (region='España')."""
        partes = []
        esc = lambda v: v.replace("'", "''")  # escape OData: los valores los genera el modelo
        if region := self._normalizar("region", region):
            if region == REGION_GENERAL:
                partes.append(f"region eq '{esc(region)}'")
            else:
                # Una guía nacional (region='España') también puede hablar de esa región
                partes.append(f"(region eq '{esc(region)}' or region eq '{REGION_GENERAL}')")
        if tipo := self._normalizar("tipo", tipo):
            partes.append(f"tipo eq '{esc(tipo)}'")
        if publico := self._normalizar("publico", publico):
            partes.append(f"publico eq '{esc(publico)}'")
        return " and ".join(partes) or None

    async def _hibrida(self, consulta: str, vector: list[float], filtro: str | None) -> list[dict]:
        resultados = await self.search.search(
            search_text=consulta,  # BM25
            vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=CANDIDATES, fields="text_vector")],
            filter=filtro,
            select=["chunk_id", "title", "chunk_raw", "fuente_titulo", "fuente_url", "organismo", "anio", "region"],
            top=TOP,
        )
        return [r async for r in resultados]

    async def buscar(self, consulta: str, region=None, tipo=None, publico=None) -> list[dict]:
        """Búsqueda híbrida. region es un filtro estricto; tipo y publico, filtros suaves.

        tipo y publico describen la guía entera, no cada fragmento: la sección de comida de
        la guía de Valencia pertenece a una guía de tipo 'urbano'. Filtrar estrictamente por
        tipo='gastronomía' la excluiría. Por eso, si se piden, se combinan dos búsquedas
        (con todos los filtros y solo con la región) alternando sus resultados.
        """
        emb = await self.openai.embeddings.create(model=self.embedding_deployment, input=consulta)
        vector = emb.data[0].embedding
        solo_region = self._filtro(region)
        completo = self._filtro(region, tipo, publico)
        if completo == solo_region:
            return await self._hibrida(consulta, vector, completo)

        especificos, generales = await asyncio.gather(
            self._hibrida(consulta, vector, completo), self._hibrida(consulta, vector, solo_region)
        )
        mezcla, vistos = [], set()
        for par in zip_longest(especificos, generales):
            for f in par:
                if f and f["chunk_id"] not in vistos:
                    vistos.add(f["chunk_id"])
                    mezcla.append(f)
        return mezcla[:TOP]

    # --- Definición de la herramienta -------------------------------------------------

    def _parametros(self) -> dict:
        def campo(nombre: str, descripcion: str) -> dict:
            p = {"type": "string", "description": descripcion}
            if self.valores.get(nombre):
                p["enum"] = self.valores[nombre]
            return p

        return {
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "Qué buscar, como frase autocontenida (incluye el lugar si se conoce).",
                },
                "region": campo(
                    "region",
                    "Opcional. Solo si el usuario menciona claramente una región. Las guías generales "
                    f"de '{REGION_GENERAL}' se incluyen siempre.",
                ),
                "tipo": campo(
                    "tipo",
                    "Opcional. Prioriza guías de este tipo de turismo, sin excluir las demás. "
                    "Solo si el usuario lo pide claramente.",
                ),
                "publico": campo(
                    "publico",
                    "Opcional. Prioriza guías para este público, sin excluir las demás. "
                    "Solo si el usuario lo menciona.",
                ),
            },
            "required": ["consulta"],
        }

    DESCRIPCION = (
        "Busca información en las guías turísticas oficiales de España. Devuelve fragmentos "
        "numerados para citar. Puedes llamarla varias veces: con otra consulta si no encuentras "
        "lo que buscas, o una vez por cada parte de una pregunta compuesta."
    )

    def tool_chat(self) -> dict:
        """Formato de Chat Completions."""
        return {
            "type": "function",
            "function": {"name": TOOL_NAME, "description": self.DESCRIPCION, "parameters": self._parametros()},
        }

    def tool_realtime(self) -> dict:
        """Formato de la Realtime API (sin el objeto `function` anidado)."""
        return {"type": "function", "name": TOOL_NAME, "description": self.DESCRIPCION, "parameters": self._parametros()}


class RegistroFuentes:
    """Fragmentos recuperados durante un turno (chat) o una llamada (voz), numerados en orden."""

    def __init__(self, buscador: BuscadorGuias) -> None:
        self.buscador = buscador
        self.fragmentos: list[dict] = []

    async def ejecutar(self, llamadas: list[tuple[str, str]]) -> list[tuple[str, str, list[Fuente]]]:
        """Ejecuta en paralelo varias llamadas (id, argumentos JSON) a la herramienta.

        Devuelve, por llamada: (id, texto para el modelo, fuentes nuevas). La numeración se
        asigna después, en el orden de las llamadas, para que sea determinista.
        """

        async def una(argumentos: str):
            try:
                args = json.loads(argumentos or "{}")
                consulta = str(args.get("consulta", "")).strip()
                if not consulta:
                    return "ERROR: falta la consulta"
                filtros = {k: args.get(k) for k in FILTROS if args.get(k)}
                logger.info("tools: %s %r %s", TOOL_NAME, consulta, filtros or "")
                return await self.buscador.buscar(consulta, **filtros)
            except Exception as e:
                logger.exception("tools: error en %s", TOOL_NAME)
                return f"ERROR al consultar las guías ({type(e).__name__})"

        resultados = await asyncio.gather(*(una(args) for _, args in llamadas))
        salida = []
        for (call_id, _), resultado in zip(llamadas, resultados):
            if isinstance(resultado, str):  # error
                salida.append((call_id, resultado, []))
                continue
            if not resultado:
                salida.append((call_id, "SIN RESULTADOS", []))
                continue
            inicio = len(self.fragmentos) + 1
            self.fragmentos.extend(resultado)
            fuentes = [fuente(i, f) for i, f in enumerate(resultado, start=inicio)]
            salida.append((call_id, construir_contexto(resultado, inicio), fuentes))
        return salida
