"""``allagents init``: analiza el equipo, pregunta qué servicios tienes y propone
una configuración que puedes aceptar y aplicar.

Qué hace, en orden:
1. Detecta lo que ya hay: Ollama (si corre, qué modelos tiene, cuánta memoria de
   vídeo hay), LM Studio, los CLIs agy/codex/copilot, la carpeta Documentos real y
   carpetas habituales de proyectos, y si otros usuarios pueden tocar el proyecto.
2. Pregunta por los servicios en la nube que no se pueden detectar (Groq,
   Mistral…). Las claves se escriben sin eco y se prueban contra el proveedor.
3. Muestra todos los cambios (las claves, enmascaradas) y solo los aplica si los
   aceptas. Nunca borra nada del ``.env``: cambia o añade líneas.

Las preguntas pasan por ``Console`` para poder probar el flujo sin terminal.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import osutil
from .agents import all_agents
from .config import PROJECT_ROOT, Settings, load_settings
from .security import redact


# --------------------------------------------------------------------------
# Entrada/salida (sustituible en los tests)
# --------------------------------------------------------------------------

class Console:
    def say(self, text: str = "") -> None:
        print(text)

    def ask(self, question: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        answer = input(f"{question}{hint}: ").strip()
        return answer or default

    def secret(self, question: str) -> str:
        return getpass.getpass(f"{question} (no se muestra al escribir): ").strip()

    def confirm(self, question: str, default: bool = False) -> bool:
        hint = "S/n" if default else "s/N"
        while True:
            answer = input(f"{question} [{hint}]: ").strip().lower()
            if not answer:
                return default
            if answer in ("s", "si", "sí", "y", "yes"):
                return True
            if answer in ("n", "no"):
                return False


# --------------------------------------------------------------------------
# 1) Detección
# --------------------------------------------------------------------------

@dataclass
class OllamaModel:
    name: str
    size: int  # bytes en disco (aprox. lo que ocupa en memoria)


@dataclass
class Machine:
    documents: Path | None = None
    code_dirs: list[Path] = field(default_factory=list)
    ollama_installed: bool = False
    ollama_running: bool = False
    ollama_models: list[OllamaModel] = field(default_factory=list)
    vram: int | None = None
    lmstudio_models: list[str] = field(default_factory=list)
    clis: dict[str, str | None] = field(default_factory=dict)        # nombre -> ruta
    clis_outside_path: dict[str, str] = field(default_factory=dict)  # solo en el PATH del usuario
    codex_logged_in: bool = False
    project_writable_by: str | None = None


_CODE_DIR_NAMES = ("source/repos", "Projects", "projects", "Proyectos", "proyectos", "dev",
                   "code", "src", "git", "GitHub", "repos", "workspace")


def _get_json(url: str, timeout: float = 2) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # sin proxies: es local
    try:
        with opener.open(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def detect(settings: Settings) -> Machine:
    m = Machine()
    home = Path.home()
    m.documents = osutil.documents_dir()
    candidates = [home / name for name in _CODE_DIR_NAMES]
    if m.documents:
        candidates += [m.documents / "GitHub", m.documents / "Projects", m.documents / "Proyectos"]
    # La carpeta donde está AllAgents suele ser la de los demás proyectos.
    parent = PROJECT_ROOT.parent
    if parent != home and parent.parent != parent:  # ni el HOME ni la raíz de una unidad
        candidates.append(parent)
    m.code_dirs = list(dict.fromkeys(p.resolve() for p in candidates if p.is_dir()))

    url = settings.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    m.ollama_installed = bool(shutil.which("ollama")) or _get_json(f"{url}/api/version") is not None
    tags = _get_json(f"{url}/api/tags")
    if tags is not None:
        m.ollama_running = True
        m.ollama_models = [OllamaModel(t["name"], int(t.get("size", 0)))
                           for t in tags.get("models", []) if "name" in t]
    m.vram = osutil.gpu_memory_bytes()

    lms = _get_json("http://127.0.0.1:1234/v1/models", timeout=1)
    if lms:
        m.lmstudio_models = [x["id"] for x in lms.get("data", []) if "id" in x]

    from .clients.cli import find_bin
    for name in ("agy", "codex", "copilot"):
        try:
            m.clis[name] = find_bin(settings.get(f"{name.upper()}_BIN"), name, f"{name.upper()}_BIN")
        except Exception:  # noqa: BLE001 — no instalado o fuera del PATH fijo
            m.clis[name] = None
            found = shutil.which(name)  # PATH completo del usuario (nvm, volta, scoop…)
            if found:
                m.clis_outside_path[name] = found
    m.codex_logged_in = (home / ".codex" / "auth.json").exists()  # solo existencia; no se lee
    m.project_writable_by = osutil.others_can_write(PROJECT_ROOT / "src" / "allagents" / "server.py")
    return m


# --------------------------------------------------------------------------
# 2) Sugerencias (funciones puras, probadas en tests/test_init.py)
# --------------------------------------------------------------------------

def _is_chat_model(name: str) -> bool:
    return not any(word in name.lower() for word in ("embed", "rerank", "bge-", "nomic"))


def _coder_first(models: list[OllamaModel]) -> list[OllamaModel]:
    return sorted(models, key=lambda x: ("code" not in x.name.lower(), -x.size, len(x.name)))


def choose_quality_model(models: list[OllamaModel], current: str) -> str | None:
    chat = [x for x in models if _is_chat_model(x.name)]
    if not chat:
        return None
    if any(x.name == current for x in chat):
        return current
    return _coder_first(chat)[0].name   # el mayor, prefiriendo los de código


def choose_fast_model(models: list[OllamaModel], quality: str, current: str) -> str | None:
    chat = [x for x in models if _is_chat_model(x.name)]
    if any(x.name == current for x in chat):
        return current
    size = next((x.size for x in chat if x.name == quality), 0)
    smaller = [x for x in chat if x.size < size * 0.6]
    if not smaller:
        return quality   # no hay uno pequeño: mejor el mismo que uno que no existe
    return sorted(smaller, key=lambda x: ("code" not in x.name.lower(), -x.size))[0].name


def suggest_num_ctx(model_size: int, vram: int | None) -> int | None:
    """Contexto prudente según cuánto del modelo cabe en la memoria de vídeo.

    El contexto reserva memoria extra en la GPU; si el modelo ya casi la llena,
    Ollama puede caerse al cargarlo ("llama-server process has terminated").
    """
    if not vram or not model_size:
        return None
    # Medido: qwen3-coder:30b (18 GB) en una GPU de 16 GB (proporción 1,13) funciona
    # con 12288 y se cae a veces con 16384. Si no cabe entero, Ollama reparte capas a la CPU.
    ratio = model_size / vram
    if ratio < 0.6:
        return 16384
    if ratio < 1.25:
        return 12288
    return 8192


@dataclass
class Plan:
    changes: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    secrets: set[str] = field(default_factory=set)   # claves cuyo valor no se muestra
    protect_project: bool = False


def suggest(m: Machine, settings: Settings) -> Plan:
    plan = Plan()
    # Carpetas permitidas: lo que ya hubiera, Documentos y carpetas de proyectos.
    current, _ = osutil.split_path_list(settings.get("ALLAGENTS_ALLOWED_DIRS"))
    wanted = list(current) + ([m.documents] if m.documents else []) + m.code_dirs
    wanted = list(dict.fromkeys(p.resolve() for p in wanted))
    joined = os.pathsep.join(str(p) for p in wanted)
    if wanted and joined != os.pathsep.join(str(p) for p in current):
        plan.changes["ALLAGENTS_ALLOWED_DIRS"] = joined

    if m.ollama_running and m.ollama_models:
        quality = choose_quality_model(m.ollama_models, settings.get("OLLAMA_MODEL", "qwen3-coder:30b"))
        if quality:
            fast = choose_fast_model(m.ollama_models, quality,
                                     settings.get("OLLAMA_FAST_MODEL", "qwen2.5-coder:7b"))
            # Se compara con el valor efectivo (el del .env o el de por defecto).
            for key, value, default in (("OLLAMA_MODEL", quality, "qwen3-coder:30b"),
                                        ("OLLAMA_FAST_MODEL", fast, "qwen2.5-coder:7b")):
                if value and value != settings.get(key, default):
                    plan.changes[key] = value
            if fast == quality:
                plan.notes.append("No hay un modelo pequeño para fast=True; si quieres uno: "
                                  "ollama pull qwen2.5-coder:7b")
            size = next(x.size for x in m.ollama_models if x.name == quality)
            ctx = suggest_num_ctx(size, m.vram)
            if ctx and str(ctx) != settings.get("OLLAMA_NUM_CTX", "16384"):
                plan.changes["OLLAMA_NUM_CTX"] = str(ctx)
                plan.notes.append(f"OLLAMA_NUM_CTX={ctx}: {quality} ocupa {size / 2**30:.0f} GB y la "
                                  f"GPU tiene {m.vram / 2**30:.0f} GB; más contexto podría no caber.")

    if m.lmstudio_models and "lmstudio" not in settings.custom_agents:
        plan.changes["ALLAGENTS_CUSTOM"] = ",".join([*settings.custom_agents, "lmstudio"])
        plan.changes.update({
            "LMSTUDIO_BASE_URL": "http://127.0.0.1:1234/v1",
            "LMSTUDIO_MODEL": m.lmstudio_models[0],
            "LMSTUDIO_LOCAL": "true",
            "LMSTUDIO_ROLE": "modelo local de LM Studio, alternativa a Ollama",
        })

    for name, path in m.clis_outside_path.items():
        plan.changes[f"{name.upper()}_BIN"] = path
    plan.protect_project = bool(m.project_writable_by)
    return plan


# --------------------------------------------------------------------------
# 3) Servicios en la nube (no se pueden detectar: se pregunta)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CloudService:
    agent: str
    label: str
    key: str
    url: str
    note: str


CLOUD_SERVICES = (
    CloudService("groq", "Groq", "GROQ_API_KEY", "https://console.groq.com/keys",
                 "gratis con límites; el más rápido"),
    CloudService("mistral", "Mistral (Codestral)", "MISTRAL_API_KEY",
                 "https://console.mistral.ai/api-keys", "gratis con límites; revisión de código"),
    CloudService("cohere", "Cohere", "COHERE_API_KEY", "https://dashboard.cohere.com/api-keys",
                 "gratis con límites; documentos largos con citas"),
    CloudService("opencode", "OpenCode Zen", "OPENCODE_API_KEY", "https://opencode.ai/auth",
                 "modelos gratuitos con cupo muy bajo"),
    CloudService("deepseek", "DeepSeek", "DEEPSEEK_API_KEY",
                 "https://platform.deepseek.com/api_keys", "pago por uso; datos en China"),
)


def _validate_key(service: CloudService, key: str) -> str | None:
    """None si la clave funciona (lista los modelos del proveedor); si no, el motivo."""
    agent = next((a for a in all_agents(Settings()) if a.NAME == service.agent), None)
    if agent is None or not hasattr(agent, "models"):
        return None
    try:
        asyncio.run(agent.models(Settings(values={service.key: key})))
    except Exception as exc:  # noqa: BLE001
        return redact(str(exc) or type(exc).__name__, (key,))[:200]
    return None


def ask_cloud_services(console: Console, settings: Settings, plan: Plan,
                       validate=_validate_key) -> None:
    console.say("\nServicios en la nube (no se pueden detectar). Responde solo por los que tengas:")
    for svc in CLOUD_SERVICES:
        configured = settings.get(svc.key) or (svc.agent == "mistral" and settings.get("CODESTRAL_API_KEY"))
        if configured:
            console.say(f"  ✅ {svc.label}: ya configurado")
            continue
        if not console.confirm(f"  ¿Tienes cuenta de {svc.label}? ({svc.note})"):
            continue
        console.say(f"     Crea o copia tu clave en {svc.url}")
        key = console.secret(f"     {svc.key}")
        if not key:
            continue
        if not re.fullmatch(r"[\x21-\x7e]{8,512}", key):
            console.say("     ❌ La clave tiene espacios o caracteres no válidos; se omite.")
            continue
        problem = validate(svc, key)
        if problem:
            console.say(f"     ⚠️  El proveedor rechazó la clave o no respondió: {problem}")
            if not console.confirm("     ¿Guardarla igualmente?"):
                continue
        else:
            console.say("     ✅ Clave verificada")
        plan.changes[svc.key] = key
        plan.secrets.add(svc.key)


# --------------------------------------------------------------------------
# 4) Aplicar al .env
# --------------------------------------------------------------------------

def _format_value(value: str) -> str:
    # Mismo criterio que parse_env_file: sin comillas, " #" empezaría un comentario.
    if " #" in value or value != value.strip():
        return f'"{value}"'
    return value


def update_env_text(text: str, changes: dict[str, str]) -> str:
    """Cambia el valor de las claves en su línea actual o las añade al final.

    Conserva comentarios, orden y finales de línea; nunca borra líneas.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pending = dict(changes)
    for i, line in enumerate(lines):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in pending:
            key = match.group(1)
            lines[i] = f"{key}={_format_value(pending.pop(key))}"
    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"# --- Añadido por allagents init ({date.today().isoformat()}) ---")
        lines += [f"{k}={_format_value(v)}" for k, v in pending.items()]
    return newline.join(lines) + newline


def apply(plan: Plan, settings: Settings, console: Console) -> None:
    env_file = settings.env_file
    if env_file.exists():
        text = env_file.read_text(encoding="utf-8-sig")
    else:
        example = PROJECT_ROOT / ".env.example"
        text = example.read_text(encoding="utf-8") if example.exists() else ""
    new_text = update_env_text(text, plan.changes)
    # Primero el archivo vacío y privado; después las claves. Así nunca hay un
    # instante en que estén en un archivo con los permisos heredados de la carpeta.
    env_file.touch(exist_ok=True)
    osutil.restrict_to_user(env_file)
    with open(env_file, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)
    console.say(f"✅ Guardado en {env_file} (solo tu usuario puede leerlo)")
    if plan.protect_project:
        osutil.restrict_tree(PROJECT_ROOT)
        osutil.restrict_to_user(env_file)
        console.say(f"✅ Solo tu usuario puede modificar {PROJECT_ROOT}")


# --------------------------------------------------------------------------
# Flujo completo
# --------------------------------------------------------------------------

def _report(m: Machine, console: Console) -> None:
    console.say("Análisis del equipo")
    console.say(f"  Documentos: {m.documents or 'no encontrada'}")
    if m.code_dirs:
        console.say("  Carpetas de proyectos: " + ", ".join(map(str, m.code_dirs)))
    if m.ollama_running:
        names = ", ".join(x.name for x in m.ollama_models) or "ninguno (ollama pull <modelo>)"
        console.say(f"  ✅ Ollama en marcha. Modelos: {names}")
    elif m.ollama_installed:
        console.say("  ⚠️  Ollama está instalado pero no responde: ábrelo (o `ollama serve`) "
                    "y repite `allagents init` para elegir modelos")
    else:
        console.say("  ❌ Ollama no está instalado (https://ollama.com): es la opción local e ilimitada")
    if m.vram:
        console.say(f"  GPU: {m.vram / 2**30:.0f} GB de memoria de vídeo")
    if m.lmstudio_models:
        console.say(f"  ✅ LM Studio en marcha: {', '.join(m.lmstudio_models[:5])}")
    hints = {"agy": "Gemini con tu suscripción de Google (Antigravity)",
             "codex": "ChatGPT con tu suscripción (npm i -g @openai/codex)",
             "copilot": "GitHub Copilot (experimental, desactivado por defecto)"}
    for name, path in m.clis.items():
        if path:
            extra = ""
            if name == "codex" and not m.codex_logged_in:
                extra = " — sin sesión: ejecuta `codex login`"
            console.say(f"  ✅ {name}: {path}{extra}")
        elif name in m.clis_outside_path:
            console.say(f"  ✅ {name}: {m.clis_outside_path[name]} (se fijará {name.upper()}_BIN)")
        else:
            console.say(f"  ·  {name} no instalado — {hints[name]}")
    if m.project_writable_by:
        console.say(f"  ⚠️  Otros usuarios ({m.project_writable_by}) pueden modificar {PROJECT_ROOT}")


def _summary(plan: Plan, console: Console) -> None:
    console.say("\nCambios propuestos en .env:")
    if not plan.changes:
        console.say("  (ninguno)")
    for key, value in plan.changes.items():
        shown = "•••••• (clave nueva)" if key in plan.secrets else value
        console.say(f"  {key}={shown}")
    if plan.protect_project:
        console.say(f"  + quitar a otros usuarios el permiso de modificar {PROJECT_ROOT}")
    for note in plan.notes:
        console.say(f"  ℹ️  {note}")


def run(console: Console | None = None, *, dry_run: bool = False, assume_yes: bool = False,
        validate=_validate_key) -> int:
    console = console or Console()
    settings = load_settings()
    if settings.env_error:
        console.say(f"⚠️  {settings.env_error}")
        if dry_run or not (assume_yes or console.confirm("¿Restringir sus permisos ahora?", True)):
            console.say("Sin eso no se puede usar tu .env actual.")
            return 1
        osutil.restrict_to_user(settings.env_file)
        settings = load_settings()

    machine = detect(settings)
    _report(machine, console)
    plan = suggest(machine, settings)
    if assume_yes and plan.protect_project:
        # Cambiar los permisos de la carpeta del proyecto se pregunta siempre.
        plan.protect_project = False
        plan.notes.append("Para proteger la carpeta del proyecto: allagents fix-permissions --project")

    if not dry_run and not assume_yes:
        extra = console.ask(f"\n¿Otras carpetas desde las que enviar archivos? "
                            f"(rutas absolutas separadas por '{os.pathsep}', vacío = ninguna)")
        paths, ignored = osutil.split_path_list(extra)
        for entry in ignored:
            console.say(f"  ⚠️  Ignorada (no es una ruta absoluta): {entry}")
        if paths:
            base = plan.changes.get("ALLAGENTS_ALLOWED_DIRS", settings.get("ALLAGENTS_ALLOWED_DIRS"))
            current, _ = osutil.split_path_list(base)
            plan.changes["ALLAGENTS_ALLOWED_DIRS"] = os.pathsep.join(
                str(p) for p in dict.fromkeys([*current, *paths]))
        local_only = console.ask("¿Carpetas con datos sensibles que solo puede ver la IA local? "
                                 "(vacío = ninguna)")
        lo_paths, _ = osutil.split_path_list(local_only)
        if lo_paths:
            plan.changes["ALLAGENTS_LOCAL_ONLY_DIRS"] = os.pathsep.join(map(str, lo_paths))
        ask_cloud_services(console, settings, plan, validate=validate)
        if plan.protect_project:
            plan.protect_project = console.confirm(
                "¿Impedir que otros usuarios modifiquen la carpeta del proyecto?", True)

    _summary(plan, console)
    if dry_run:
        console.say("\n(--dry-run: no se ha cambiado nada)")
        return 0
    if not plan.changes and not plan.protect_project:
        console.say("\nNo hay nada que cambiar.")
        return 0
    if not (assume_yes or console.confirm("\n¿Aplicar estos cambios?", True)):
        console.say("No se ha cambiado nada.")
        return 0
    apply(plan, settings, console)
    console.say("Abre una sesión nueva de Claude Code para que use la nueva configuración.")
    return 0


def interactive_ok() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
