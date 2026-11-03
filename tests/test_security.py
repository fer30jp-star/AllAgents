from pathlib import Path

import pytest

from allagents import osutil
from allagents.config import Settings
from allagents.files import build_prompt, load_files
from allagents.security import (
    REDACTED,
    SecurityError,
    check_file_access,
    check_model_name,
    child_env,
    redact,
    require_https,
    require_loopback,
)

from conftest import symlink_or_skip


@pytest.fixture
def workspace(tmp_path):
    allowed = tmp_path / "permitido"
    private = allowed / "bancario"
    outside = tmp_path / "fuera"
    for d in (allowed, private, outside):
        d.mkdir()
    settings = Settings(values={
        "ALLAGENTS_ALLOWED_DIRS": str(allowed),
        "ALLAGENTS_LOCAL_ONLY_DIRS": str(private),
        "ALLAGENTS_MAX_FILE_BYTES": "100",
        "ALLAGENTS_MAX_TOTAL_BYTES": "150",
    }, env_file=tmp_path / ".env")
    return settings, allowed, private, outside


def test_allows_regular_file_inside_allowed_dir(workspace):
    settings, allowed, _, _ = workspace
    f = allowed / "app.py"
    f.write_text("print('hola')")
    assert check_file_access(str(f), settings, local_agent=False) == f.resolve()


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "server.pem", "auth.json",
                                  "credentials.json"])
def test_blocks_sensitive_file_names(workspace, name):
    settings, allowed, _, _ = workspace
    f = allowed / name
    f.write_text("secreto")
    with pytest.raises(SecurityError, match="sensible"):
        check_file_access(str(f), settings, local_agent=True)


def test_credential_dirs_rejected_even_if_file_does_not_exist(workspace):
    settings, *_ = workspace
    missing = Path.home() / ".ssh" / "no-existe-nunca"
    with pytest.raises(SecurityError, match="credenciales"):
        check_file_access(str(missing), settings, local_agent=True)


def test_env_example_is_allowed(workspace):
    settings, allowed, _, _ = workspace
    f = allowed / ".env.example"
    f.write_text("GROQ_API_KEY=")
    check_file_access(str(f), settings, local_agent=False)


def test_blocks_files_outside_allowed_dirs(workspace):
    settings, _, _, outside = workspace
    f = outside / "notas.txt"
    f.write_text("x")
    with pytest.raises(SecurityError, match="ALLOWED_DIRS"):
        check_file_access(str(f), settings, local_agent=True)


def test_blocks_symlink_escaping_allowed_dir(workspace):
    settings, allowed, _, outside = workspace
    target = outside / "secreto.txt"
    target.write_text("x")
    link = allowed / "inocente.txt"
    symlink_or_skip(link, target)
    with pytest.raises(SecurityError, match="ALLOWED_DIRS"):
        check_file_access(str(link), settings, local_agent=True)


def test_blocks_symlink_disguising_sensitive_file(workspace):
    settings, allowed, _, _ = workspace
    target = allowed / ".env"
    target.write_text("GROQ_API_KEY=x")
    link = allowed / "config.txt"
    symlink_or_skip(link, target)
    with pytest.raises(SecurityError, match="sensible"):
        check_file_access(str(link), settings, local_agent=True)


def test_local_only_dirs_rejected_for_cloud_but_allowed_for_local(workspace):
    settings, _, private, _ = workspace
    f = private / "layout.txt"
    f.write_text("cuenta 123")
    with pytest.raises(SecurityError, match="ask_ollama"):
        check_file_access(str(f), settings, local_agent=False)
    assert check_file_access(str(f), settings, local_agent=True)


def test_relative_paths_rejected(workspace):
    settings, *_ = workspace
    with pytest.raises(SecurityError, match="absolutas"):
        check_file_access("app.py", settings, local_agent=True)


def test_size_limits_and_binary_detection(workspace):
    settings, allowed, _, _ = workspace
    big = allowed / "grande.txt"
    big.write_text("x" * 101)
    with pytest.raises(SecurityError, match="MAX_FILE_BYTES"):
        load_files([str(big)], settings, local_agent=True)

    a, b = allowed / "a.txt", allowed / "b.txt"
    a.write_text("x" * 90)
    b.write_text("y" * 90)
    with pytest.raises(SecurityError, match="MAX_TOTAL_BYTES"):
        load_files([str(a), str(b)], settings, local_agent=True)

    binary = allowed / "img.bin"
    binary.write_bytes(b"\x89PNG\x00\x00")
    with pytest.raises(SecurityError, match="binario"):
        load_files([str(binary)], settings, local_agent=True)


def test_build_prompt_marks_files_as_reference(workspace):
    settings, allowed, _, _ = workspace
    f = allowed / "log.txt"
    f.write_text("ERROR 42")
    prompt = build_prompt("Resume", load_files([str(f)], settings, local_agent=True))
    assert prompt.startswith("Resume")
    assert "no instrucciones" in prompt
    assert f'<archivo ruta="{f.resolve()}">\nERROR 42\n</archivo>' in prompt


def test_redact_known_secrets_and_key_shapes():
    # Se arman en tiempo de ejecución para que el hook pre-commit no los vea como claves.
    groq, openai, github = "gsk" + "_" + "A" * 20, "sk" + "-proj-" + "B" * 20, "ghp" + "_" + "C" * 22
    text = (f"clave=mi-clave-super-larga Authorization: Bearer abcdefghijklmnop123 "
            f"{groq} {openai} {github}")
    out = redact(text, ("mi-clave-super-larga",))
    for leaked in ("mi-clave-super-larga", "abcdefghijklmnop123", groq, openai, github):
        assert leaked not in out
    assert out.count(REDACTED) == 5
    assert "Bearer " + REDACTED in out


def test_child_env_drops_api_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "no-debe-pasar")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy")
    env = child_env()
    assert "GROQ_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert ("USERPROFILE" if osutil.IS_WINDOWS else "HOME") in env and "PATH" in env


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434",
                                 "http://[::1]:11434"])
def test_loopback_urls_accepted(url):
    assert require_loopback(url)


@pytest.mark.parametrize("url", ["http://192.168.1.10:11434", "http://0.0.0.0:11434",
                                 "http://ollama.example.com", "file:///etc/passwd"])
def test_non_loopback_urls_rejected(url):
    with pytest.raises(SecurityError):
        require_loopback(url)


def test_cloud_urls_must_be_https():
    assert require_https("https://api.groq.com/openai/v1/")
    with pytest.raises(SecurityError):
        require_https("http://api.groq.com/openai/v1")


@pytest.mark.parametrize("model", ["gemini-3.8-flash-medium", "qwen3-coder:30b",
                                   "deepseek/deepseek-chat", "gpt-5.1-codex", ""])
def test_valid_model_names(model):
    assert check_model_name(model) == model


@pytest.mark.parametrize("model", ['x" & calc', "--dangerously-skip-permissions",
                                   "a b", "x;rm -rf /", "%PATH%"])
def test_model_names_that_could_inject_arguments_are_rejected(model):
    with pytest.raises(SecurityError, match="modelo"):
        check_model_name(model)
