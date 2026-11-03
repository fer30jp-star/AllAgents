"""allagents init: sugerencias, preguntas y escritura del .env (sin red ni terminal)."""

import os
from pathlib import Path

import pytest

from allagents import init_wizard as wiz
from allagents import osutil
from allagents.config import Settings, load_settings

GB = 2**30
# Clave falsa armada en tiempo de ejecución (el hook pre-commit rechaza literales con forma de clave).
FAKE_KEY = "gsk" + "_" + "x" * 24


class ScriptedConsole(wiz.Console):
    """Responde con una lista fija y guarda todo lo que se muestra."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.output: list[str] = []

    def say(self, text=""):
        self.output.append(text)

    def ask(self, question, default=""):
        self.output.append(question)
        return self.answers.pop(0) or default

    def secret(self, question):
        self.output.append(question)
        return self.answers.pop(0)

    def confirm(self, question, default=False):
        self.output.append(question)
        answer = self.answers.pop(0)
        return default if answer == "" else answer


# --- Edición del .env -----------------------------------------------------------

def test_update_env_replaces_in_place_and_keeps_comments():
    text = "# Groq\nGROQ_API_KEY=\nGROQ_MODEL=qwen\n"
    out = wiz.update_env_text(text, {"GROQ_API_KEY": "abc", "OLLAMA_NUM_CTX": "8192"})
    lines = out.splitlines()
    assert lines[:3] == ["# Groq", "GROQ_API_KEY=abc", "GROQ_MODEL=qwen"]
    assert lines[-1] == "OLLAMA_NUM_CTX=8192"
    assert any("allagents init" in line for line in lines)


def test_update_env_keeps_windows_line_endings_and_quotes_when_needed():
    out = wiz.update_env_text("A=1\r\nB=2\r\n", {"B": "valor #raro"})
    assert out == 'A=1\r\nB="valor #raro"\r\n'


def test_update_env_does_not_touch_commented_examples():
    out = wiz.update_env_text("# OLLAMA_URL=http://x\n", {"OLLAMA_URL": "http://127.0.0.1:11434"})
    assert out.startswith("# OLLAMA_URL=http://x\n")
    assert out.rstrip().endswith("OLLAMA_URL=http://127.0.0.1:11434")


# --- Elección de modelos y contexto ----------------------------------------------

MODELS = [wiz.OllamaModel("gemma4:26b", 18 * GB), wiz.OllamaModel("qwen3-coder:30b", 18 * GB),
          wiz.OllamaModel("qwen2.5-coder:7b", 5 * GB), wiz.OllamaModel("nomic-embed-text", 1 * GB)]


def test_keeps_configured_model_if_installed():
    assert wiz.choose_quality_model(MODELS, "gemma4:26b") == "gemma4:26b"


def test_prefers_largest_code_model_and_skips_embeddings():
    assert wiz.choose_quality_model(MODELS, "no-instalado") == "qwen3-coder:30b"
    assert wiz.choose_fast_model(MODELS, "qwen3-coder:30b", "no-instalado") == "qwen2.5-coder:7b"


def test_fast_model_falls_back_to_quality_when_nothing_smaller():
    only_big = MODELS[:2]
    assert wiz.choose_fast_model(only_big, "qwen3-coder:30b", "qwen2.5-coder:7b") == "qwen3-coder:30b"


@pytest.mark.parametrize("size,vram,expected", [
    (5 * GB, 16 * GB, 16384), (12 * GB, 16 * GB, 12288), (18 * GB, 16 * GB, 12288),
    (30 * GB, 16 * GB, 8192), (18 * GB, None, None)])
def test_num_ctx_depends_on_how_much_of_the_model_fits(size, vram, expected):
    assert wiz.suggest_num_ctx(size, vram) == expected


# --- Propuesta completa ------------------------------------------------------------

def _machine(tmp_path, **kw):
    docs = tmp_path / "Documentos"
    docs.mkdir(exist_ok=True)
    base = dict(documents=docs, code_dirs=[], ollama_installed=True, ollama_running=True,
                ollama_models=MODELS[:2], vram=16 * GB)
    base.update(kw)
    return wiz.Machine(**base)


def test_suggestion_for_a_gpu_that_barely_fits_the_model(tmp_path):
    plan = wiz.suggest(_machine(tmp_path), Settings())
    assert plan.changes["ALLAGENTS_ALLOWED_DIRS"] == str((tmp_path / "Documentos").resolve())
    assert plan.changes["OLLAMA_FAST_MODEL"] == "qwen3-coder:30b"
    assert plan.changes["OLLAMA_NUM_CTX"] == "12288"
    assert "OLLAMA_MODEL" not in plan.changes          # el de por defecto está instalado
    assert any("ollama pull" in note for note in plan.notes)


def test_existing_allowed_dirs_are_kept(tmp_path):
    mine = tmp_path / "mio"
    mine.mkdir()
    plan = wiz.suggest(_machine(tmp_path), Settings(values={"ALLAGENTS_ALLOWED_DIRS": str(mine)}))
    assert plan.changes["ALLAGENTS_ALLOWED_DIRS"].split(os.pathsep)[0] == str(mine.resolve())


def test_lmstudio_and_clis_outside_fixed_path_are_configured(tmp_path):
    m = _machine(tmp_path, ollama_running=False, lmstudio_models=["qwen2.5-7b-instruct"],
                 clis_outside_path={"codex": "/opt/nvm/bin/codex"}, project_writable_by="Usuarios")
    plan = wiz.suggest(m, Settings())
    assert plan.changes["ALLAGENTS_CUSTOM"] == "lmstudio"
    assert plan.changes["LMSTUDIO_LOCAL"] == "true"
    assert plan.changes["LMSTUDIO_BASE_URL"].startswith("http://127.0.0.1")
    assert plan.changes["CODEX_BIN"] == "/opt/nvm/bin/codex"
    assert plan.protect_project


# --- Preguntas por servicios en la nube ----------------------------------------------

def _no_validation(service, key):
    return None


def test_cloud_key_is_stored_masked_and_never_printed():
    # groq: sí + clave; el resto: no.
    console = ScriptedConsole([True, FAKE_KEY, False, False, False, False])
    plan = wiz.Plan()
    wiz.ask_cloud_services(console, Settings(), plan, validate=_no_validation)
    assert plan.changes == {"GROQ_API_KEY": FAKE_KEY}
    assert plan.secrets == {"GROQ_API_KEY"}
    wiz._summary(plan, console)
    assert not any(FAKE_KEY in line for line in console.output)


def test_rejected_key_is_dropped_unless_confirmed():
    console = ScriptedConsole([True, FAKE_KEY, False, False, False, False, False])
    plan = wiz.Plan()
    wiz.ask_cloud_services(console, Settings(), plan, validate=lambda s, k: "HTTP 401")
    assert "GROQ_API_KEY" not in plan.changes


def test_keys_with_spaces_are_rejected_and_configured_services_skipped():
    settings = Settings(values={"MISTRAL_API_KEY": "ya-tengo-una"})
    # groq: sí + clave inválida; mistral no se pregunta; cohere/opencode/deepseek: no.
    console = ScriptedConsole([True, "clave con espacios", False, False, False])
    plan = wiz.Plan()
    wiz.ask_cloud_services(console, settings, plan, validate=_no_validation)
    assert plan.changes == {}
    assert any("Mistral" in line and "ya configurado" in line for line in console.output)


# --- Flujo completo -------------------------------------------------------------------

def test_full_run_writes_a_private_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(wiz, "load_settings",
                        lambda: load_settings({"ALLAGENTS_ENV_FILE": str(env)}))
    monkeypatch.setattr(wiz, "detect", lambda settings: _machine(tmp_path))
    extra = tmp_path / "proyectos"
    extra.mkdir()
    answers = [str(extra), "",                      # carpetas extra, ninguna solo-local
               True, FAKE_KEY, False, False, False, False,   # solo Groq
               True]                                # aplicar
    console = ScriptedConsole(answers)
    assert wiz.run(console, validate=_no_validation) == 0

    assert osutil.private_file_problem(env) is None
    settings = load_settings({"ALLAGENTS_ENV_FILE": str(env)})
    assert settings.env_error is None
    assert settings.get("GROQ_API_KEY") == FAKE_KEY
    assert settings.get("OLLAMA_NUM_CTX") == "12288"
    assert Path(extra).resolve() in settings.allowed_dirs
    assert not any(FAKE_KEY in line for line in console.output)


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(wiz, "load_settings",
                        lambda: load_settings({"ALLAGENTS_ENV_FILE": str(env)}))
    monkeypatch.setattr(wiz, "detect", lambda settings: _machine(tmp_path))
    assert wiz.run(ScriptedConsole([]), dry_run=True) == 0
    assert not env.exists()


def test_assume_yes_never_touches_project_permissions(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(wiz, "load_settings",
                        lambda: load_settings({"ALLAGENTS_ENV_FILE": str(env)}))
    monkeypatch.setattr(wiz, "detect",
                        lambda settings: _machine(tmp_path, project_writable_by="Usuarios"))
    called = []
    monkeypatch.setattr(osutil, "restrict_tree", lambda root: called.append(root))
    assert wiz.run(ScriptedConsole([]), assume_yes=True) == 0
    assert called == [] and env.exists()
