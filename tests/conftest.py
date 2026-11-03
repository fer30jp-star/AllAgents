"""Ayudas comunes para que los tests valgan igual en Linux/macOS y Windows."""

import subprocess
from pathlib import Path

import pytest

from allagents import osutil


def make_world_readable(path: Path) -> None:
    """Da lectura a otros usuarios: chmod 644 o, en Windows, permiso para Todos."""
    if osutil.IS_WINDOWS:
        subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:R"],
                       check=True, capture_output=True)
    else:
        path.chmod(0o644)


def make_private(path: Path) -> None:
    osutil.restrict_to_user(path)


def symlink_or_skip(link: Path, target: Path) -> None:
    """En Windows crear enlaces exige modo desarrollador o administrador."""
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"no se pueden crear enlaces simbólicos aquí: {exc}")
