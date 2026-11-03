#!/usr/bin/env bash
# Instala AllAgents: entorno virtual, dependencias fijadas, .env privado y
# hook de git que impide subir secretos. Se puede ejecutar varias veces.
#   ./scripts/setup.sh              instala
#   ./scripts/setup.sh --register   instala y lo registra en Claude Code
#   ./scripts/setup.sh --no-init    no lanza el asistente de configuración
set -euo pipefail

REGISTER=0
INIT=1
for arg in "$@"; do
  case "$arg" in
    --register) REGISTER=1 ;;
    --no-init) INIT=0 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077

echo "==> Entorno virtual"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.lock
.venv/bin/pip install -q --no-deps -e .

echo "==> Archivo .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Creado .env a partir de .env.example (rellena tus claves)."
fi
chmod 600 .env
mkdir -p logs && chmod 700 logs

echo "==> Hook de git contra secretos"
if [ -d .git ] && command -v git >/dev/null; then
  git config core.hooksPath scripts/git-hooks
  chmod +x scripts/git-hooks/pre-commit
  echo "    Activado (scripts/git-hooks/pre-commit)."
else
  echo "    Sin git o aún no es un repositorio; vuelve a ejecutar este script tras instalar git o 'git init'."
fi

echo
if [ "$INIT" -eq 1 ] && [ -t 0 ] && [ -t 1 ]; then
  # Analiza el equipo, pregunta por tus servicios y propone el .env (luego hace check).
  echo "==> Configuración (allagents init; Ctrl+C para saltarla)"
  .venv/bin/allagents init || true
else
  .venv/bin/allagents check
  echo "Para configurarlo con un asistente: .venv/bin/allagents init"
fi
echo

SERVE=("$ROOT/.venv/bin/allagents" serve)
if [ "$REGISTER" -eq 1 ]; then
  echo "==> Registro en Claude Code"
  if ! command -v claude >/dev/null; then
    echo "    No se encontró el comando 'claude'. Instala Claude Code y repite con --register."
  elif claude mcp get allagents >/dev/null 2>&1; then
    echo "    Ya estaba registrado ('claude mcp get allagents' para verlo)."
  else
    claude mcp add allagents --scope user -- "${SERVE[@]}"
  fi
else
  echo "Para registrarlo en Claude Code (todas tus carpetas):"
  echo "  ./scripts/setup.sh --register"
fi
echo "Abre una sesión nueva de Claude Code para ver las herramientas ask_*."
