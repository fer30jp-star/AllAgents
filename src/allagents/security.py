"""Controles de seguridad compartidos.

- Qué archivos se pueden enviar a un agente (carpetas permitidas, archivos
  sensibles bloqueados, carpetas solo-locales, enlaces simbólicos resueltos).
- Redacción de secretos en cualquier texto que salga del servidor.
- Entorno mínimo para los procesos hijos (los CLIs no heredan claves).
- Validación de URLs: HTTPS para la nube, solo loopback para Ollama.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import osutil
from .config import Settings


class SecurityError(Exception):
    """Una petición viola la política de seguridad; el mensaje explica por qué."""


# Nombres de archivo que nunca se envían, estén donde estén.
SENSITIVE_NAME_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.ppk",
    ".netrc", "_netrc", ".pgpass", ".git-credentials", ".npmrc", ".pypirc",
    "credentials", "credentials.*", "secrets.*", "*.keystore",
    "auth.json", "token.json", "known_hosts", "authorized_keys",
)
# Excepciones explícitas a los patrones anteriores.
SENSITIVE_NAME_EXCEPTIONS = (".env.example", ".env.sample", ".env.template")

# Carpetas (relativas al HOME) con credenciales de herramientas.
SENSITIVE_HOME_DIRS = (
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store",
    ".codex", ".copilot", ".gemini", ".claude", ".config/gh", ".config/gcloud",
    ".local/share/keyrings", ".mozilla", ".config/google-chrome",
    ".config/BraveSoftware", ".zen",
)

# En Windows las credenciales viven en el perfil (AppData), no en carpetas ocultas.
_WINDOWS_ROAMING_DIRS = (
    "GitHub CLI", "gcloud", "Mozilla", "Antigravity", "Code/User/globalStorage",
    "Microsoft/Credentials", "Microsoft/Protect", "Microsoft/Crypto",
    "Microsoft/SystemCertificates", "Microsoft/Vault",
)
_WINDOWS_LOCAL_DIRS = (
    "Google/Chrome/User Data", "Microsoft/Edge/User Data", "BraveSoftware",
    "Mozilla", "Microsoft/Credentials", "Microsoft/Vault", "agy",
)


def sensitive_dirs() -> tuple[Path, ...]:
    """Carpetas de credenciales que nunca se envían, como rutas absolutas."""
    home = Path.home().resolve()
    dirs = [home / d for d in SENSITIVE_HOME_DIRS]
    if osutil.IS_WINDOWS:
        # Resueltas como la ruta pedida: así un nombre corto 8.3 o una unión no las esquiva.
        roaming = Path(os.environ.get("APPDATA", home / "AppData/Roaming")).resolve()
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")).resolve()
        dirs += [roaming / d for d in _WINDOWS_ROAMING_DIRS]
        dirs += [local / d for d in _WINDOWS_LOCAL_DIRS]
    return tuple(dirs)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/]{12,}=*"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),            # OpenAI / DeepSeek
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}"),              # Groq
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),        # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),           # Google
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)

REDACTED = "[REDACTADO]"


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Quita de ``text`` los secretos conocidos y los que tienen forma de clave."""
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + REDACTED, text)
    return text


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[… respuesta recortada: {len(text) - limit} caracteres omitidos]"


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _is_sensitive_name(name: str) -> bool:
    if name in SENSITIVE_NAME_EXCEPTIONS:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_NAME_PATTERNS)


def _reject_if_sensitive(path: Path, raw_path: str) -> None:
    if any(_is_sensitive_name(part) for part in path.parts):
        raise SecurityError(f"archivo sensible bloqueado: {raw_path}")
    for sensitive in sensitive_dirs():
        if _is_within(path, sensitive):
            raise SecurityError(f"carpeta de credenciales bloqueada: {raw_path}")


def check_file_access(raw_path: str, settings: Settings, *, local_agent: bool) -> Path:
    """Valida que ``raw_path`` se pueda enviar al agente; devuelve la ruta real.

    La ruta pedida se revisa antes de tocar el disco (así un rechazo no revela
    si el archivo existe) y la real, tras resolver enlaces simbólicos, otra vez:
    un enlace no sirve para escapar de las carpetas permitidas ni para disfrazar
    un archivo sensible.
    """
    requested = Path(raw_path).expanduser()
    if not requested.is_absolute():
        raise SecurityError(f"usa rutas absolutas: {raw_path!r}")
    if osutil.IS_WINDOWS and ":" in str(requested)[2:]:
        # "archivo:flujo" son flujos alternativos de NTFS: otra forma de nombrar datos.
        raise SecurityError(f"flujos alternativos de NTFS no permitidos: {raw_path}")
    _reject_if_sensitive(Path(os.path.normpath(requested)), raw_path)
    try:
        real = requested.resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        raise SecurityError(f"no existe: {raw_path}") from None
    _reject_if_sensitive(real, raw_path)
    if not real.is_file():
        raise SecurityError(f"no es un archivo: {raw_path}")

    if settings.env_file.exists() and real == settings.env_file.resolve():
        raise SecurityError("el .env del servidor nunca se envía")

    if not any(_is_within(real, allowed) for allowed in settings.allowed_dirs):
        allowed = ", ".join(str(p) for p in settings.allowed_dirs) or "(ninguna)"
        raise SecurityError(f"fuera de ALLAGENTS_ALLOWED_DIRS ({allowed}): {raw_path}")
    if not local_agent and any(_is_within(real, d) for d in settings.local_only_dirs):
        raise SecurityError(
            f"{raw_path} está en ALLAGENTS_LOCAL_ONLY_DIRS: solo puede enviarse a ask_ollama")
    return real


def child_env(extra_path: tuple[str, ...] = ()) -> dict[str, str]:
    """Entorno mínimo para procesos hijos: sin claves de API ni tokens.

    Solo pasan las variables que los CLIs necesitan para arrancar y encontrar su
    sesión (lista según el sistema, en ``osutil``) y un PATH fijo.
    """
    env = {k: os.environ[k] for k in osutil.CHILD_ENV_KEYS if k in os.environ}
    env.update({k: v for k, v in os.environ.items() if k.startswith("LC_")})
    env["PATH"] = os.pathsep.join(dict.fromkeys([*extra_path, *osutil.default_bin_dirs()]))
    env["NO_COLOR"] = "1"
    return env


_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


def check_model_name(model: str) -> str:
    """Valida un nombre de modelo que va como argumento de un CLI (nunca empieza por -)."""
    if model and not _MODEL_RE.match(model):
        raise SecurityError(f"nombre de modelo no válido: {model!r}")
    return model


def require_https(url: str) -> str:
    if urlsplit(url).scheme != "https":
        raise SecurityError(f"solo se permiten URLs https para servicios en la nube: {url}")
    return url.rstrip("/")


def require_loopback(url: str) -> str:
    """Acepta solo URLs a la propia máquina (Ollama no debe salir a la red)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https"):
        raise SecurityError(f"URL no válida: {url}")
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError:
            raise SecurityError(
                f"OLLAMA_URL debe apuntar a esta máquina (127.0.0.1, ::1 o localhost): {url}"
            ) from None
    return url.rstrip("/")
