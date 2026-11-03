"""Punto de entrada: ``allagents [serve|init|check|models <agente>|fix-permissions]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from . import audit, osutil
from .agents import all_agents
from .config import PROJECT_ROOT, load_settings
from .server import agent_status, build_instructions, build_server


def _serve() -> int:
    if sys.stdin.isatty():
        print("Este comando lo lanza Claude Code (MCP por stdio). Para probar la "
              "configuración usa: allagents check", file=sys.stderr)
        return 2
    settings = load_settings()
    audit.setup(settings.log_file)
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # sin una línea por petición
    # Único transporte permitido: stdio. No se abre ningún puerto de red.
    build_server(settings).run(transport="stdio")
    return 0


def _check() -> int:
    settings = load_settings()
    if not settings.env_file.exists():
        print(f"⚠️  No existe {settings.env_file} (cópialo de .env.example)")
    elif settings.env_error:
        print(f"❌ .env ignorado: {settings.env_error}")
    else:
        print(f"✅ .env cargado de {settings.env_file} (permisos seguros)")
    print("Carpetas permitidas para `files`:", ", ".join(map(str, settings.allowed_dirs)) or "ninguna")
    print("Carpetas solo-locales:", ", ".join(map(str, settings.local_only_dirs)) or "ninguna")
    for entry in settings.ignored_dir_entries():
        print(f"⚠️  Carpeta ignorada (usa rutas absolutas separadas por '{os.pathsep}'): {entry}")
    who = osutil.others_can_write(Path(__file__).resolve().parent / "server.py")
    if who:
        print(f"⚠️  Otros usuarios ({who}) pueden modificar el código del servidor, que "
              "corre con tus permisos. Protégelo con: allagents fix-permissions --project")
    print("\nAgentes en orden de prioridad:")
    agents = all_agents(settings)
    available = []
    for agent in agents:
        reason = agent_status(agent, settings)
        if reason is None:
            available.append(agent)
        mark = "✅" if reason is None else "❌"
        print(f"{mark} ask_{agent.NAME:<10} [{agent.TIER}] {reason or 'disponible'}")
    if "--instructions" in sys.argv:
        print("\nInstrucciones que recibe Claude:\n" + build_instructions(available))
    return 0


def _models(name: str) -> int:
    settings = load_settings()
    agents = all_agents(settings)
    agent = next((a for a in agents if a.NAME == name), None)
    if agent is None or not hasattr(agent, "models"):
        listable = ", ".join(a.NAME for a in agents if hasattr(a, "models"))
        print(f"Agente desconocido o sin listado de modelos. Opciones: {listable}", file=sys.stderr)
        return 2
    reason = agent_status(agent, settings)
    if reason:
        print(f"ask_{name} no disponible: {reason}", file=sys.stderr)
        return 1
    for model in asyncio.run(agent.models(settings)):
        print(model)
    return 0


def _fix_permissions(project: bool) -> int:
    """Deja el .env y la carpeta de logs accesibles solo para el usuario actual.

    Con ``project``, además impide que otros usuarios modifiquen el proyecto (su
    código se ejecuta con tus permisos). Va primero: el .env y logs/ quedan después
    con su permiso propio, más estricto.
    """
    settings = load_settings()
    status = 0
    steps = [(osutil.restrict_tree, PROJECT_ROOT, "Solo tu usuario puede modificar")] if project else []
    steps += [(osutil.restrict_to_user, path, "Solo tu usuario puede acceder a")
              for path in (settings.env_file, settings.log_file.parent)]
    for action, path, done in steps:
        if not path.exists():
            continue
        try:
            action(path)
            print(f"✅ {done} {path}")
        except OSError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            status = 1
    return status


def _init(dry_run: bool, assume_yes: bool) -> int:
    from . import init_wizard
    if not (dry_run or assume_yes or init_wizard.interactive_ok()):
        print("allagents init hace preguntas: ejecútalo en una terminal, o usa --dry-run (solo "
              "muestra la propuesta) o --yes (aplica lo detectado sin preguntar).", file=sys.stderr)
        return 2
    try:
        status = init_wizard.run(dry_run=dry_run, assume_yes=assume_yes)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado: no se ha cambiado nada.", file=sys.stderr)
        return 130
    if status == 0 and not dry_run:
        print()
        _check()
    return status


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)  # todo lo que cree el servidor (logs, temporales) es privado
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] != "serve":
        osutil.utf8_console()  # en serve (por defecto), stdout es el canal MCP: no se toca
    parser = argparse.ArgumentParser(
        prog="allagents", description="Servidor MCP local para delegar tareas a otras IAs.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="inicia el servidor MCP por stdio (lo usa Claude Code)")
    init = sub.add_parser("init", help="analiza el equipo, pregunta qué servicios tienes y "
                                       "propone una configuración para aceptar y aplicar")
    init.add_argument("--dry-run", action="store_true",
                      help="solo muestra lo detectado y la propuesta; no pregunta ni cambia nada")
    init.add_argument("--yes", action="store_true",
                      help="aplica lo detectado sin preguntar (no pide claves ni toca permisos del proyecto)")
    check = sub.add_parser("check", help="muestra qué agentes están disponibles")
    check.add_argument("--instructions", action="store_true",
                       help="muestra también las instrucciones de prioridad que recibe Claude")
    models = sub.add_parser("models", help="lista los modelos de un agente")
    models.add_argument("agent")
    fix = sub.add_parser("fix-permissions",
                         help="deja .env y logs/ accesibles solo para tu usuario (chmod o ACL)")
    fix.add_argument("--project", action="store_true",
                     help="impide también que otros usuarios modifiquen la carpeta del proyecto")
    args = parser.parse_args(argv)

    if args.command == "init":
        return _init(args.dry_run, args.yes)
    if args.command == "check":
        return _check()
    if args.command == "models":
        return _models(args.agent)
    if args.command == "fix-permissions":
        return _fix_permissions(args.project)
    return _serve()


if __name__ == "__main__":
    sys.exit(main())
