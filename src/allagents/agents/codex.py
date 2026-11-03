"""OpenAI Codex CLI: modelos GPT con la suscripción de ChatGPT del usuario.

Confinamiento verificado (2026-11-02): el modo ``-s read-only`` por sí solo
permite LEER cualquier archivo del equipo (leyó /etc/hostname). Por eso se
desactivan también sus herramientas de terminal, navegador, apps y búsqueda
web: así solo ve el prompt. Además:
- carpeta temporal vacía como directorio de trabajo; ``--ephemeral`` (no
  guarda la sesión); el prompt va por stdin.
"""

from __future__ import annotations

from ..clients.cli import find_bin, isolated_workspace, run_cli
from ..config import Settings
from ..security import check_model_name
from ..files import build_prompt
from .base import CLOUD_TOOL, TIER_SCARCE, AgentError, AgentResult, delegate

NAME = "codex"
LOCAL = False
PRIORITY = 70
TIER = TIER_SCARCE
ROLE = "máxima calidad: lógica difícil, depuración razonada, revisión crítica de diseño"

DISABLED_FEATURES = ("shell_tool", "unified_exec", "apps", "browser_use", "in_app_browser",
                     "js_repl_tools_only")


def _bin(settings: Settings) -> str:
    return find_bin(settings.get("CODEX_BIN"), "codex", "CODEX_BIN")


def status(settings: Settings) -> str | None:
    try:
        _bin(settings)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def register(server, settings: Settings) -> None:
    default_model = settings.get("CODEX_MODEL")

    @server.tool(name="ask_codex", annotations=CLOUD_TOOL)
    async def ask_codex(prompt: str, files: list[str] | None = None, model: str = "") -> str:
        """ChatGPT (modelos GPT de OpenAI) vía Codex CLI. Usa la suscripción de ChatGPT
        del usuario, que tiene límites de uso: no la gastes en tareas masivas.

        Especialidad: razonamiento y programación de alta calidad.

        Úsala para:
        - Problemas difíciles de lógica, algoritmos o depuración razonada.
        - Revisión crítica de un diseño o de un diff antes de aplicarlo.
        - Segundas opiniones cuando la calidad importa más que la velocidad.

        NO la uses con datos sensibles (salen a OpenAI): para eso, ask_ollama.
        Corre aislada: sin terminal ni acceso a tus archivos; solo ve `prompt` y `files`.

        Args:
            prompt: la tarea, completa y autocontenida.
            files: rutas ABSOLUTAS de archivos de texto a incluir (el servidor los lee).
            model: opcional; por defecto el de la cuenta de ChatGPT.
        """
        use_model = model or default_model

        async def call(loaded):
            check_model_name(use_model)  # va como argumento del CLI
            async with isolated_workspace() as ws:
                out_file = ws / "respuesta.txt"
                argv = [_bin(settings), "exec", "--skip-git-repo-check", "--ephemeral",
                        "-s", "read-only", "-C", str(ws), "--color", "never",
                        "-c", 'web_search="disabled"', "-o", str(out_file)]
                for feature in DISABLED_FEATURES:
                    argv += ["--disable", feature]
                if use_model:
                    argv += ["-m", use_model]
                argv.append("-")  # el prompt llega por stdin
                res = await run_cli(argv, cwd=ws, timeout_s=settings.timeout_s,
                                    stdin_text=build_prompt(prompt, loaded))
                text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
            if res.returncode != 0 or not text:
                if "401 Unauthorized" in res.stderr:
                    raise AgentError("codex no tiene sesión iniciada con ChatGPT: ejecuta "
                                     "`codex login` en una terminal y vuelve a intentarlo")
                raise AgentError(res.stderr[-600:].strip() or f"codex salió con código {res.returncode}")
            model_line = next((ln.split(":", 1)[1].strip() for ln in res.stderr.splitlines()
                               if ln.startswith("model:")), use_model)
            return AgentResult(agent=NAME, model=model_line, text=text, duration_s=res.duration_s)

        return await delegate(NAME, settings, local=LOCAL, files=files, call=call)
