"""Todo lo que cambia entre Linux/macOS y Windows, en un solo sitio.

- Permisos privados de archivos: bits POSIX (``chmod 600``) o listas de acceso
  NTFS (ACL) en Windows.
- Separador de listas de rutas (``:`` o ``;``).
- PATH y variables mínimas para los procesos hijos.
- Cómo matar a un agente y a todos sus hijos.
- Cómo lanzar sin ``cmd.exe`` los CLIs que npm instala como ``.cmd``.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# Límite práctico de un argumento con el prompt. Linux: MAX_ARG_STRLEN = 128 KiB
# por argumento. Windows: 32 767 caracteres para TODA la línea de comandos.
MAX_ARGV_PROMPT_BYTES = 30_000 if IS_WINDOWS else 120_000


# --------------------------------------------------------------------------
# Listas de rutas en variables (ALLAGENTS_ALLOWED_DIRS, …)
# --------------------------------------------------------------------------

def split_path_list(raw: str) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Separa una lista de carpetas por ``os.pathsep``.

    Devuelve (rutas válidas, entradas ignoradas). Se ignoran las relativas: se
    resolverían contra la carpeta actual, que no controla el usuario. En Windows
    también las que llevan ``:`` fuera de la unidad (p. ej. una lista copiada de
    Linux con ``:``), que nunca coincidirían con nada.
    """
    paths: list[Path] = []
    ignored: list[str] = []
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        path = Path(entry).expanduser()
        bad_colon = IS_WINDOWS and ":" in str(path)[2:]
        if not path.is_absolute() or bad_colon:
            ignored.append(entry)
            continue
        paths.append(path.resolve())
    return tuple(paths), tuple(ignored)


# --------------------------------------------------------------------------
# Permisos privados
# --------------------------------------------------------------------------

def private_file_problem(path: Path) -> str | None:
    """Motivo por el que ``path`` lo pueden leer o cambiar otros usuarios, o None."""
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        return f"{path} no es un archivo normal"
    if IS_WINDOWS:
        return _windows_acl_problem(path)
    if st.st_uid != os.getuid():
        return f"{path} no pertenece a tu usuario"
    if st.st_mode & 0o077:
        return (f"{path} tiene permisos {oct(st.st_mode & 0o777)} (otros usuarios "
                f"pueden leerlo); ejecuta: chmod 600 {path}")
    return None


def restrict_to_user(path: Path) -> None:
    """Deja ``path`` (archivo o carpeta) accesible solo para el usuario actual."""
    if not IS_WINDOWS:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        return
    # (OI)(CI): en carpetas, lo que se cree dentro hereda el mismo permiso.
    perm = "(OI)(CI)F" if path.is_dir() else "F"
    # Se usan SID y no nombres: "Administradores"/"Administrators" depende del idioma.
    # Usuario actual y SYSTEM; se quita la herencia (y con ella Authenticated Users…).
    result = subprocess.run(
        [_system32("icacls.exe"), str(path), "/inheritance:r",
         "/grant:r", f"*{_current_user_sid()}:{perm}", f"*S-1-5-18:{perm}"],
        capture_output=True, text=True, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise OSError(f"icacls falló en {path}: {(result.stdout + result.stderr).strip()}")
    # /grant:r no borra los permisos explícitos de otros (p. ej. Everyone): se quitan.
    user = _current_user_sid()
    others = sorted({sid for _, _, _, sid in (_read_acl(path)[2] or [])
                     if sid and sid not in (user, "S-1-5-18")})
    if others:
        subprocess.run([_system32("icacls.exe"), str(path), "/remove", *(f"*{s}" for s in others)],
                       capture_output=True, text=True, creationflags=_NO_WINDOW, check=True)


def restrict_command(path: Path) -> str:
    """Comando que el usuario puede copiar para proteger ``path``."""
    if IS_WINDOWS:
        return r".venv\Scripts\allagents fix-permissions"
    return f"chmod 600 {path}"


def _system32(exe: str) -> str:
    # Ruta absoluta: así nadie puede suplantarlo con otro icacls/taskkill en el PATH.
    return str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / exe)


_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW

# SID que pueden tener acceso a un archivo privado, además del propio usuario:
# SYSTEM y Administradores (el equivalente a root, que siempre puede leerlo todo).
_TRUSTED_SIDS = {"S-1-5-18", "S-1-5-32-544"}
_OWNER_SIDS = {"S-1-3-4", "S-1-3-0"}
# Cualquier derecho de lectura, escritura o de cambiar los permisos.
_RISKY_MASK = (0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0100   # leer/escribir datos y atributos
               | 0x00040000 | 0x00080000                     # WRITE_DAC, WRITE_OWNER
               | 0x10000000 | 0x40000000 | 0x80000000       # GENERIC_ALL/WRITE/READ
               | 0x02000000)                                 # MAXIMUM_ALLOWED
_INHERIT_ONLY = 0x08


def _windows_acl_problem(path: Path) -> str | None:
    """Revisa la ACL de ``path`` con la API de Windows (ctypes, sin dependencias)."""
    try:
        user, owner, aces = _read_acl(path)
    except OSError as exc:
        return f"no se pudieron leer los permisos de {path}: {exc}"
    fix = restrict_command(path)
    if owner != user and owner not in _TRUSTED_SIDS:
        return f"{path} no pertenece a tu usuario (propietario {owner})"
    if aces is None:
        return f"{path} no tiene lista de acceso: cualquiera puede leerlo; ejecuta: {fix}"
    for ace_type, flags, mask, sid in aces:
        if flags & _INHERIT_ONLY or ace_type in (1, 6, 10, 12):   # ACE de denegación
            continue
        if sid in _OWNER_SIDS:   # "Owner Rights"/"Creator Owner" = el propietario
            sid = owner
        if sid is None:   # tipo de ACE que no sabemos interpretar: se rechaza
            return f"{path} tiene permisos que no se pueden verificar; ejecuta: {fix}"
        if mask & _RISKY_MASK and sid != user and sid not in _TRUSTED_SIDS:
            return (f"{path} es accesible para otros usuarios ({_sid_label(sid)}); "
                    f"ejecuta: {fix}")
    return None


_WRITE_MASK = (0x0002 | 0x0004 | 0x0040 | 0x00010000     # escribir, añadir, borrar
               | 0x00040000 | 0x00080000                # WRITE_DAC, WRITE_OWNER
               | 0x10000000 | 0x40000000 | 0x02000000)  # GENERIC_ALL/WRITE, MAXIMUM_ALLOWED


def others_can_write(path: Path) -> str | None:
    """Quién más puede modificar ``path`` (p. ej. el código del servidor), o None."""
    if not IS_WINDOWS:
        mode = path.stat().st_mode
        return "grupo u otros usuarios" if mode & 0o022 else None
    try:
        user, _, aces = _read_acl(path)
    except OSError:
        return None
    if aces is None:
        return "cualquiera (sin lista de acceso)"
    for ace_type, flags, mask, sid in aces:
        if flags & _INHERIT_ONLY or ace_type in (1, 6, 10, 12) or sid is None:
            continue
        if mask & _WRITE_MASK and sid != user and sid not in _TRUSTED_SIDS | _OWNER_SIDS:
            return _sid_label(sid)
    return None


def restrict_tree(root: Path) -> None:
    """Quita a los demás usuarios el permiso de modificar ``root`` y su contenido."""
    if IS_WINDOWS:
        restrict_to_user(root)   # los hijos heredan la nueva ACL de la carpeta
        return
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            if not p.is_symlink():
                os.chmod(p, p.stat().st_mode & ~0o022)
    os.chmod(root, root.stat().st_mode & ~0o022)


def _sid_label(sid: str) -> str:
    known = {"S-1-1-0": "Everyone/Todos", "S-1-5-11": "Authenticated Users",
             "S-1-5-32-545": "Usuarios", "S-1-5-32-546": "Invitados"}
    return known.get(sid, sid)


def _current_user_sid() -> str:
    return _read_acl(None)[0]


def _read_acl(path: Path | None):
    """(SID del usuario, SID del propietario, [(tipo, flags, máscara, SID)] o None)."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(ctypes.c_void_p)]

    def sid_str(psid) -> str:
        out = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return out.value
        finally:
            kernel32.LocalFree(ctypes.cast(out, ctypes.c_void_p))

    # Usuario del proceso.
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008,  # TOKEN_QUERY
                                     ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        user = sid_str(ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0])
    finally:
        kernel32.CloseHandle(token)
    if path is None:
        return user, None, None

    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    err = advapi32.GetNamedSecurityInfoW(
        str(path), 1, 0x1 | 0x4,  # SE_FILE_OBJECT, OWNER | DACL
        ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if err:
        raise ctypes.WinError(err)
    try:
        owner_sid = sid_str(owner)
        if not dacl.value:
            return user, owner_sid, None
        ace_count = ctypes.cast(dacl, ctypes.POINTER(ctypes.c_ushort))[2]  # ACL.AceCount
        aces = []
        for i in range(ace_count):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, i, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            raw = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))
            ace_type, flags = raw[0], raw[1]
            mask = ctypes.cast(ace, ctypes.POINTER(ctypes.c_uint32))[1]
            # Tipos 0 y 9 (permitir / permitir con callback): el SID va tras la máscara.
            sid = sid_str(ace.value + 8) if ace_type in (0, 1, 9, 10) else None
            aces.append((ace_type, flags, mask, sid))
        return user, owner_sid, aces
    finally:
        kernel32.LocalFree(sd)


# --------------------------------------------------------------------------
# Procesos hijos
# --------------------------------------------------------------------------

_POSIX_CHILD_KEYS = (
    "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TZ",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
)
# Sin SYSTEMROOT muchos programas de Windows no arrancan (sockets, criptografía);
# sin APPDATA/LOCALAPPDATA los CLIs no encuentran su sesión iniciada.
_WINDOWS_CHILD_KEYS = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "OS",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "USERNAME", "USERDOMAIN",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "TZ",
)
CHILD_ENV_KEYS = _WINDOWS_CHILD_KEYS if IS_WINDOWS else _POSIX_CHILD_KEYS


def default_bin_dirs() -> list[str]:
    """Carpetas donde se buscan los CLIs (PATH fijo: no se hereda el del usuario)."""
    home = Path.home()
    if not IS_WINDOWS:
        return [str(home / ".local/bin"), str(home / ".npm-global/bin"),
                "/usr/local/bin", "/usr/bin", "/bin"]
    env = os.environ
    local = Path(env.get("LOCALAPPDATA", home / "AppData/Local"))
    roaming = Path(env.get("APPDATA", home / "AppData/Roaming"))
    program_files = Path(env.get("PROGRAMFILES", r"C:\Program Files"))
    system_root = Path(env.get("SYSTEMROOT", r"C:\Windows"))
    return [str(p) for p in (
        local / "agy" / "bin",            # Antigravity CLI
        roaming / "npm",                  # npm install -g (codex, copilot)
        home / ".local" / "bin",
        local / "Volta" / "bin", home / "scoop" / "shims",
        program_files / "nodejs",
        system_root / "System32", system_root,
        system_root / "System32" / "WindowsPowerShell" / "v1.0",
    )]


def popen_kwargs() -> dict:
    """Opciones para lanzar un agente en su propio grupo de procesos."""
    if IS_WINDOWS:
        # Grupo propio (no recibe el Ctrl+C de la consola) y sin ventana emergente.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW}
    return {"start_new_session": True}


def kill_tree(pid: int) -> None:
    """Mata al proceso ``pid`` y a todos sus descendientes."""
    if IS_WINDOWS:
        subprocess.run([_system32("taskkill.exe"), "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=_NO_WINDOW)
        return
    import signal
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# npm/pnpm crean shims .cmd que llaman a: "%dp0%\node_modules\...\cli.js" %*
_SHIM_TARGET_RE = re.compile(r'"%~?dp0%\\?([^"%]+\.(?:js|cjs|mjs|exe))"', re.IGNORECASE)


def resolve_launcher(exe: str) -> list[str]:
    """Convierte un ejecutable en el inicio de un argv que no pasa por ``cmd.exe``.

    En Windows, lanzar un ``.cmd``/``.bat`` con argumentos los hace pasar por
    ``cmd.exe``, que interpreta ``&``, ``|`` o ``%`` aunque vayan entre comillas:
    un argumento podría ejecutar otro comando. Por eso los shims de npm se
    traducen a ``node.exe <script>`` (o al ``.exe`` real), y cualquier otro
    ``.cmd``/``.bat`` se rechaza.
    """
    path = Path(exe)
    if not IS_WINDOWS or path.suffix.lower() not in (".cmd", ".bat"):
        return [exe]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OSError(f"no se pudo leer {path}: {exc}") from None
    # El shim también menciona "%dp0%\node.exe" (el intérprete), que no es el destino.
    matches = [m for m in _SHIM_TARGET_RE.findall(text) if m.lower() != "node.exe"]
    if not matches:
        raise OSError(f"{path} es un script .cmd que no se puede lanzar de forma segura; "
                      "indica en la variable *_BIN la ruta del .exe real")
    target = (path.parent / matches[-1]).resolve()
    if not target.is_file():
        raise OSError(f"el shim {path} apunta a {target}, que no existe")
    if target.suffix.lower() == ".exe":
        return [str(target)]
    node = path.parent / "node.exe"
    if not node.is_file():
        import shutil
        found = shutil.which("node", path=os.pathsep.join(default_bin_dirs()))
        if not found:
            raise OSError(f"{path} necesita Node.js y no se encontró node.exe")
        node = Path(found)
    return [str(node), str(target)]


def is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    if IS_WINDOWS:
        return path.suffix.lower() in (".exe", ".com", ".cmd", ".bat")
    return os.access(path, os.X_OK)


# --------------------------------------------------------------------------
# Detección del equipo (para ``allagents init``)
# --------------------------------------------------------------------------

def documents_dir() -> Path | None:
    """Carpeta Documentos real del usuario (en Windows puede estar en OneDrive)."""
    if IS_WINDOWS:
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            folder_id = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")  # FOLDERID_Documents
            guid = (ctypes.c_byte * 16).from_buffer_copy(folder_id.bytes_le)
            out = ctypes.c_wchar_p()
            shell32 = ctypes.WinDLL("shell32")
            shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                     wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                try:
                    return Path(out.value)
                finally:
                    ctypes.WinDLL("ole32").CoTaskMemFree(out)
        except OSError:
            pass
    else:
        try:  # Linux con xdg-user-dirs: respeta el idioma ("Documentos")
            found = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True,
                                   text=True, timeout=5).stdout.strip()
            if found and Path(found) != Path.home():
                return Path(found)
        except (OSError, subprocess.SubprocessError):
            pass
    for name in ("Documents", "Documentos"):
        if (Path.home() / name).is_dir():
            return Path.home() / name
    return None


def gpu_memory_bytes() -> int | None:
    """Memoria de vídeo de la GPU NVIDIA más grande, o None si no se puede saber."""
    import shutil
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=_NO_WINDOW).stdout
        values = [int(v) for v in out.split() if v.strip().isdigit()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return max(values) * 1024 * 1024 if values else None


def utf8_console() -> None:
    """Salida en UTF-8 aunque Windows redirija a cp1252 (emojis de ``check``)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
