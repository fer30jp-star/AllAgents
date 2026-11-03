"""Registro de auditoría: qué se delegó, a quién, cuánto tardó y si falló.

Solo metadatos. Nunca se registran prompts, contenido de archivos,
respuestas ni claves. La carpeta y el archivo solo son accesibles para el
usuario (permisos 700/600 o, en Windows, ACL privada).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import osutil

_logger = logging.getLogger("allagents.audit")


def setup(log_file: Path) -> None:
    if _logger.handlers:
        return
    log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_file.touch(mode=0o600, exist_ok=True)
    if osutil.IS_WINDOWS:
        # En Windows mode= no protege nada: se revisa la ACL y, si hace falta, se
        # restringe la carpeta (lo que se cree dentro, incluidas las rotaciones, la hereda).
        if osutil.private_file_problem(log_file):
            osutil.restrict_to_user(log_file.parent)
            osutil.restrict_to_user(log_file)
    else:
        osutil.restrict_to_user(log_file)
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def record(agent: str, *, ok: bool, duration_s: float, model: str = "", files: int = 0,
           input_chars: int = 0, input_tokens: int | None = None,
           output_tokens: int | None = None, error: str = "") -> None:
    fields = [f"agent={agent}", f"ok={ok}", f"dur={duration_s:.1f}s", f"model={model or '-'}",
              f"files={files}", f"in_chars={input_chars}"]
    if input_tokens is not None:
        fields.append(f"in_tok={input_tokens}")
    if output_tokens is not None:
        fields.append(f"out_tok={output_tokens}")
    if error:
        fields.append(f"error={error[:200]!r}")
    _logger.info(" ".join(fields))
