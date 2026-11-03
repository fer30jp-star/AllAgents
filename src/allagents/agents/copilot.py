"""GitHub Copilot CLI (EXPERIMENTAL, desactivado por defecto).

La cuenta con la que se desarrolló está en Copilot Free y GitHub rechaza el
CLI ("Access denied by policy settings"), así que su confinamiento NO se ha
podido verificar como el de agy y codex. Antes de quitarlo de ALLAGENTS_DISABLE,
repite las pruebas de SECURITY.md (lectura fuera de la carpeta y comandos).

El CLI solo acepta el prompt por argumento, así que hay límite de tamaño.
"""

from __future__ import annotations

from ..clients.cli import MAX_ARGV_PROMPT_BYTES, find_bin, isolated_workspace, run_cli
from ..config import Settings
from ..security import check_model_name
from ..files import build_prompt
from .base import CLOUD_TOOL, TIER_SUBSCRIPTION, AgentError, AgentResult, delegate

NAME = "copilot"
LOCAL = False
PRIORITY = 90
TIER = TIER_SUBSCRIPTION
ROLE = "experimental; dudas del ecosistema GitHub (Actions, workflows)"


def _bin(settings: Settings) -> str:
    return find_bin(settings.get("COPILOT_BIN"), "copilot", "COPILOT_BIN")


def status(settings: Settings) -> str | None:
    try:
        _bin(settings)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def register(server, settings: Settings) -> None:
    default_model = settings.get("COPILOT_MODEL")

    @server.tool(name="ask_copilot", annotations=CLOUD_TOOL)
    async def ask_copilot(prompt: str, files: list[str] | None = None, model: str = "") -> str:
        """GitHub Copilot vía su CLI (experimental).

        Úsala para: dudas de código ligadas al ecosistema GitHub (Actions, workflows,
        configuración de repositorios). Para lo demás hay opciones mejores.
        NO la uses con datos sensibles. Límite: ~120 KB entre prompt y archivos (~30 KB en Windows).

        Args:
            prompt: la tarea, completa y autocontenida.
            files: rutas ABSOLUTAS de archivos de texto a incluir (el servidor los lee).
            model: opcional (p. ej. "auto").
        """
        use_model = model or default_model

        async def call(loaded):
            check_model_name(use_model)  # va como argumento del CLI
            full = build_prompt(prompt, loaded)
            if len(full.encode()) > MAX_ARGV_PROMPT_BYTES:
                raise AgentError("prompt demasiado grande para Copilot CLI; usa ask_agy")
            argv = [_bin(settings), "-p", full, "-s", "--no-color",
                    "--deny-tool", "shell", "--deny-tool", "write"]
            if use_model:
                argv += ["--model", use_model]
            async with isolated_workspace() as ws:
                res = await run_cli(argv, cwd=ws, timeout_s=settings.timeout_s)
            if res.returncode != 0:
                raise AgentError((res.stdout + res.stderr)[-600:].strip())
            return AgentResult(agent=NAME, model=use_model or "auto", text=res.stdout,
                               duration_s=res.duration_s)

        return await delegate(NAME, settings, local=LOCAL, files=files, call=call)
