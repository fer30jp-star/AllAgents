"""Ollama local: los datos nunca salen del equipo.

Solo se aceptan URLs de loopback (ver security.require_loopback), así que
aunque alguien cambie OLLAMA_URL, el servidor no enviará datos a otra máquina.
"""

from __future__ import annotations

import time
import urllib.request

from ..clients.http import HttpError, get_json, post_json
from ..config import Settings
from ..files import build_prompt
from ..security import SecurityError, require_loopback
from .base import LOCAL_TOOL, TIER_LOCAL, AgentError, AgentResult, delegate

NAME = "ollama"
LOCAL = True
PRIORITY = 10
TIER = TIER_LOCAL
ROLE = ("PRIMERA OPCIÓN para todo lo que pueda resolver; OBLIGATORIA con datos sensibles. "
        "Tareas mecánicas, borradores, resúmenes, código sencillo")


def _url(settings: Settings) -> str:
    return require_loopback(settings.get("OLLAMA_URL", "http://127.0.0.1:11434"))


def _reachable(url: str) -> bool:
    # Sin proxies: es una conexión a la propia máquina.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{url}/api/version", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def status(settings: Settings) -> str | None:
    try:
        url = _url(settings)
    except SecurityError as exc:
        return str(exc)
    if not _reachable(url):
        return f"Ollama no responde en {url} (¿está instalado y corriendo?)"
    return None


async def models(settings: Settings) -> list[str]:
    data = await get_json(f"{_url(settings)}/api/tags", headers={}, timeout_s=10)
    return sorted(m["name"] for m in data.get("models", []))


def register(server, settings: Settings) -> None:
    quality = settings.get("OLLAMA_MODEL", "qwen3-coder:30b")
    fast_model = settings.get("OLLAMA_FAST_MODEL", "qwen2.5-coder:7b")
    num_ctx = settings.get_int("OLLAMA_NUM_CTX", 16384)

    @server.tool(name="ask_ollama", annotations=LOCAL_TOOL)
    async def ask_ollama(prompt: str, files: list[str] | None = None, fast: bool = False) -> str:
        """IA LOCAL (Ollama en esta máquina): gratis, sin límites y los datos NO salen del equipo.

        Úsala para:
        - CUALQUIER dato sensible: credenciales, datos de clientes, layouts bancarios con
          datos reales, logs con datos personales. Es la única herramienta que acepta
          archivos de ALLAGENTS_LOCAL_ONLY_DIRS.
        - Tareas mecánicas y repetitivas: formatear, renombrar, docstrings, tests sencillos,
          traducir comentarios, convertir entre formatos.
        - Borradores y resúmenes de archivos de tamaño medio.

        Evítala para razonamiento profundo o respuestas muy largas con prisa: genera
        ~17 tokens/s con el modelo de calidad (unos 60 s por cada 1000 tokens).

        Args:
            prompt: la tarea, completa y autocontenida (el modelo no ve tu conversación).
            files: rutas ABSOLUTAS de archivos de texto a incluir; el servidor los lee
                por ti, así no gastas contexto leyéndolos.
            fast: True usa el modelo pequeño (más rápido, menos capaz). Por defecto se usa
                el modelo de calidad (qwen3-coder:30b).
        """
        model = fast_model if fast else quality

        async def call(loaded):
            payload = {"model": model, "stream": False, "options": {"num_ctx": num_ctx},
                       "messages": [{"role": "user", "content": build_prompt(prompt, loaded)}]}
            start = time.monotonic()
            try:
                data = await post_json(f"{_url(settings)}/api/chat", payload, headers={},
                                       timeout_s=settings.timeout_s)
            except HttpError as exc:
                text = str(exc)
                if any(s in text for s in ("llama-server process has terminated", "CUDA error",
                                           "out of memory", "memory")):
                    # Medido: un modelo de 18 GB en una GPU de 16 GB se cae con num_ctx=16384.
                    text += (f" — Ollama no pudo cargar {model} con num_ctx={num_ctx}: suele ser "
                             "falta de memoria de vídeo. Baja OLLAMA_NUM_CTX (p. ej. 12288 u 8192).")
                raise AgentError(text) from None
            if "error" in data:
                raise AgentError(data["error"])
            return AgentResult(agent=NAME, model=model,
                               text=(data.get("message") or {}).get("content", ""),
                               duration_s=time.monotonic() - start,
                               input_tokens=data.get("prompt_eval_count"),
                               output_tokens=data.get("eval_count"))

        return await delegate(NAME, settings, local=LOCAL, files=files, call=call)
