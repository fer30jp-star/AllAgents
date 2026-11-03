"""Ejecución segura de CLIs de IA (agy, codex, copilot).

- Sin shell: los argumentos van como lista, no hay inyección de comandos.
- Cada llamada corre en una carpeta temporal vacía (privada del usuario) que
  se borra al terminar; es el único "espacio de trabajo" que ve el agente.
- Entorno mínimo (sin claves de API) y grupo de procesos propio para poder
  matar al agente y a todos sus hijos si se pasa del tiempo.
- En Windows, los shims ``.cmd`` de npm se lanzan como ``node.exe script.js``:
  nunca a través de ``cmd.exe``, que reinterpretaría los argumentos.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from .. import osutil
from ..osutil import MAX_ARGV_PROMPT_BYTES  # noqa: F401 — lo usan los agentes
from ..security import child_env


class CliError(Exception):
    pass


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


@asynccontextmanager
async def isolated_workspace() -> AsyncIterator[Path]:
    # En Windows un archivo aún abierto por un hijo impide borrar la carpeta: no es
    # motivo para fallar una respuesta que ya llegó.
    with tempfile.TemporaryDirectory(prefix="allagents-", ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


async def run_cli(argv: list[str], *, cwd: Path, timeout_s: int,
                  stdin_text: str | None = None) -> CliResult:
    start = time.monotonic()
    try:
        launcher = osutil.resolve_launcher(argv[0])
    except OSError as exc:
        raise CliError(str(exc)) from None
    try:
        proc = await asyncio.create_subprocess_exec(
            *launcher, *argv[1:],
            cwd=cwd,
            env=_workspace_env(cwd),
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **osutil.popen_kwargs(),
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 206:  # ERROR_FILENAME_EXCED_RANGE
            raise CliError("la línea de comandos supera el límite de Windows (32 767 "
                           "caracteres); reduce el prompt o los archivos") from None
        if isinstance(exc, FileNotFoundError):
            raise CliError(f"no se encontró el ejecutable {argv[0]}") from None
        raise CliError(f"no se pudo lanzar {Path(argv[0]).name}: {exc}") from None

    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin_text.encode() if stdin_text is not None else None),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        _kill_group(proc)
        await proc.wait()
        raise CliError(f"{Path(argv[0]).name} superó el tiempo máximo de {timeout_s}s") from None
    except asyncio.CancelledError:
        # Claude canceló la llamada: no dejar al agente corriendo en segundo plano.
        _kill_group(proc)
        raise

    return CliResult(
        returncode=proc.returncode or 0,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        duration_s=time.monotonic() - start,
    )


def _workspace_env(cwd: Path) -> dict[str, str]:
    """Entorno del hijo con su carpeta temporal DENTRO de su espacio de trabajo.

    agy deja leer la carpeta temporal del sistema aunque tenga prohibido salir de
    su espacio de trabajo (verificado en Windows, 2026-11-02). Ahí hay archivos de
    otros programas y los espacios de trabajo de otras llamadas, así que el hijo
    recibe como temporal una subcarpeta vacía que se borra con el resto.
    """
    env = child_env()
    tmp = cwd / ".tmp"
    tmp.mkdir(exist_ok=True)
    for key in ("TEMP", "TMP") if osutil.IS_WINDOWS else ("TMPDIR",):
        env[key] = str(tmp)
    return env


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    osutil.kill_tree(proc.pid)


def find_bin(configured: str, name: str, setting: str) -> str:
    """Ruta del CLI: la de ``setting`` si está definida; si no, se busca en el PATH."""
    if configured:
        path = Path(configured).expanduser()
        if not osutil.is_executable(path):
            raise CliError(f"{setting}={configured} no es un ejecutable")
        return str(path)
    found = shutil.which(name, path=child_env()["PATH"])
    if not found:
        raise CliError(f"no se encontró '{name}' en el PATH (instálalo o define {setting})")
    return found
