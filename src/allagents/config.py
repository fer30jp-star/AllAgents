"""Carga y valida la configuración.

Fuentes, de menor a mayor prioridad:
  1. Valores por defecto de cada módulo.
  2. El archivo ``.env`` del proyecto (o el indicado en ``ALLAGENTS_ENV_FILE``).
  3. Variables de entorno del proceso.

El ``.env`` se ignora por completo si otros usuarios pueden leerlo o si no
pertenece al usuario actual (permisos POSIX o ACL de NTFS, ver ``osutil``): así
una clave nunca se usa desde un archivo expuesto. No se usa python-dotenv para
no añadir dependencias.

Las listas de carpetas se separan con ``:`` en Linux/macOS y con ``;`` en Windows.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import osutil

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_LOG_FILE = PROJECT_ROOT / "logs" / "allagents.log"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Variables cuyo valor es un secreto y debe redactarse en errores y logs.
_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")


def parse_env_file(text: str) -> dict[str, str]:
    """Interpreta un .env sencillo: ``CLAVE=valor``, comentarios y comillas."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def env_file_problem(path: Path) -> str | None:
    """Devuelve el motivo por el que ``path`` no es seguro, o None si lo es."""
    return osutil.private_file_problem(path)


def _expand_paths(raw: str) -> tuple[Path, ...]:
    return osutil.split_path_list(raw)[0]


def _default_allowed_dirs() -> str:
    # La carpeta Documentos real (en Windows puede estar redirigida a OneDrive u otra
    # unidad) más los nombres habituales.
    docs = osutil.documents_dir()
    candidates = [str(docs)] if docs else []
    return os.pathsep.join(dict.fromkeys([*candidates, "~/Documentos", "~/Documents"]))


_DEFAULT_ALLOWED_DIRS = _default_allowed_dirs()


@dataclass(frozen=True)
class Settings:
    values: dict[str, str] = field(default_factory=dict)
    env_file: Path = DEFAULT_ENV_FILE
    env_error: str | None = None

    def get(self, key: str, default: str = "") -> str:
        value = self.values.get(key, "")
        return value if value else default

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default

    def get_path(self, key: str, default: str) -> Path:
        return Path(self.get(key, default)).expanduser()

    @property
    def secrets(self) -> tuple[str, ...]:
        """Valores secretos cargados, para redactarlos de cualquier salida."""
        return tuple(v for k, v in self.values.items()
                     if k.endswith(_SECRET_SUFFIXES) and len(v) >= 8)

    @property
    def allowed_dirs(self) -> tuple[Path, ...]:
        return _expand_paths(self.get("ALLAGENTS_ALLOWED_DIRS", _DEFAULT_ALLOWED_DIRS))

    @property
    def local_only_dirs(self) -> tuple[Path, ...]:
        return _expand_paths(self.get("ALLAGENTS_LOCAL_ONLY_DIRS"))

    def ignored_dir_entries(self) -> tuple[str, ...]:
        """Entradas de las listas de carpetas que se descartaron (relativas o mal separadas)."""
        return (osutil.split_path_list(self.get("ALLAGENTS_ALLOWED_DIRS", _DEFAULT_ALLOWED_DIRS))[1]
                + osutil.split_path_list(self.get("ALLAGENTS_LOCAL_ONLY_DIRS"))[1])

    @property
    def priority(self) -> tuple[str, ...]:
        """Orden de preferencia de ALLAGENTS_PRIORITY; vacío = el de cada módulo."""
        raw = self.get("ALLAGENTS_PRIORITY")
        return tuple(a.strip().lower() for a in raw.split(",") if a.strip())

    @property
    def custom_agents(self) -> tuple[str, ...]:
        """Agentes compatibles con OpenAI definidos solo en .env (ALLAGENTS_CUSTOM)."""
        raw = self.get("ALLAGENTS_CUSTOM")
        return tuple(a.strip().lower() for a in raw.split(",") if a.strip())

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key)
        if not value:
            return default
        return value.strip().lower() in ("1", "true", "yes", "si", "sí")

    @property
    def disabled_agents(self) -> frozenset[str]:
        # Copilot va desactivado por defecto: su confinamiento no está verificado.
        raw = self.values.get("ALLAGENTS_DISABLE", "copilot")
        return frozenset(a.strip().lower() for a in raw.split(",") if a.strip())

    @property
    def max_file_bytes(self) -> int:
        return self.get_int("ALLAGENTS_MAX_FILE_BYTES", 2_000_000)

    @property
    def max_total_bytes(self) -> int:
        return self.get_int("ALLAGENTS_MAX_TOTAL_BYTES", 6_000_000)

    @property
    def max_output_chars(self) -> int:
        return self.get_int("ALLAGENTS_MAX_OUTPUT_CHARS", 20_000)

    @property
    def timeout_s(self) -> int:
        return self.get_int("ALLAGENTS_TIMEOUT_S", 300)

    @property
    def log_file(self) -> Path:
        return self.get_path("ALLAGENTS_LOG_FILE", str(DEFAULT_LOG_FILE))


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    environ = dict(os.environ if environ is None else environ)
    env_file = Path(environ.get("ALLAGENTS_ENV_FILE", DEFAULT_ENV_FILE)).expanduser()

    file_values: dict[str, str] = {}
    env_error: str | None = None
    if env_file.exists():
        env_error = env_file_problem(env_file)
        if env_error is None:
            file_values = parse_env_file(env_file.read_text(encoding="utf-8-sig"))

    # Las variables de entorno con valor ganan al .env.
    values = {**file_values, **{k: v for k, v in environ.items() if v}}
    return Settings(values=values, env_file=env_file, env_error=env_error)
