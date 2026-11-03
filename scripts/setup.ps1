# Instala AllAgents: entorno virtual, dependencias fijadas, .env privado y
# hook de git que impide subir secretos. Se puede ejecutar varias veces.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Register
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoInit   (sin asistente)

param(
    [switch]$Register,
    [switch]$NoInit
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Entorno virtual"
if (-not (Test-Path -LiteralPath ".venv")) {
    $hasPy = Get-Command py -ErrorAction SilentlyContinue
    if ($hasPy) {
        $verCheck = (& py -3 -c "import sys; print(sys.version_info >= (3, 11))") -join ""
        if ($LASTEXITCODE -ne 0 -or $verCheck.Trim() -ne "True") {
            throw "Python >= 3.11 no encontrado con 'py -3'. Se requiere Python 3.11 o superior."
        }
        & py -3 -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            throw "Error al crear el entorno virtual con 'py -3 -m venv .venv'."
        }
    } else {
        $hasPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $hasPython) {
            throw "No se encontró el comando 'py' ni 'python'. Instala Python >= 3.11."
        }
        $verCheck = (& python -c "import sys; print(sys.version_info >= (3, 11))") -join ""
        if ($LASTEXITCODE -ne 0 -or $verCheck.Trim() -ne "True") {
            throw "Python >= 3.11 no encontrado con 'python'. Se requiere Python 3.11 o superior."
        }
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            throw "Error al crear el entorno virtual con 'python -m venv .venv'."
        }
    }
}

$venvPython = "$Root\.venv\Scripts\python.exe"
& $venvPython -m pip install -q --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Error al actualizar pip en el entorno virtual."
}

& $venvPython -m pip install -q -r requirements.lock
if ($LASTEXITCODE -ne 0) {
    throw "Error al instalar dependencias desde requirements.lock."
}

& $venvPython -m pip install -q --no-deps -e .
if ($LASTEXITCODE -ne 0) {
    throw "Error al instalar el paquete en modo editable (-e .)."
}

Write-Host "==> Archivo .env"
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "    Creado .env a partir de .env.example (rellena tus claves)."
}
if (-not (Test-Path -LiteralPath "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

$allagentsExe = "$Root\.venv\Scripts\allagents.exe"
& $allagentsExe fix-permissions
if ($LASTEXITCODE -ne 0) {
    throw "Error al ejecutar 'allagents fix-permissions'."
}

Write-Host "==> Hook de git contra secretos"
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd -and (Test-Path -LiteralPath ".git")) {
    & git config core.hooksPath scripts/git-hooks
    if ($LASTEXITCODE -ne 0) {
        throw "Error al configurar core.hooksPath de git."
    }
    Write-Host "    Activado (scripts/git-hooks/pre-commit; Git for Windows lo ejecuta con su bash)."
} else {
    Write-Host "    Aún no es un repositorio git; vuelve a ejecutar este script tras 'git init'."
}

Write-Host ""
if (-not $NoInit -and -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) {
    # Analiza el equipo, pregunta por tus servicios y propone el .env (luego hace check).
    Write-Host "==> Configuración (allagents init; Ctrl+C para saltarla)"
    & $allagentsExe init
} else {
    & $allagentsExe check
    if ($LASTEXITCODE -ne 0) {
        throw "Error al ejecutar 'allagents check'."
    }
    Write-Host "Para configurarlo con un asistente: .venv\Scripts\allagents init"
}
Write-Host ""

if ($Register) {
    Write-Host "==> Registro en Claude Code"
    $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claudeCmd) {
        Write-Host "    No se encontró el comando 'claude'. Instala Claude Code y repite con -Register."
    } else {
        # Con "Stop", PowerShell 5.1 convierte en excepción el stderr de un programa
        # nativo; aquí un error solo significa "aún no está registrado".
        $prevPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & claude mcp get allagents *> $null
        $registered = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevPreference
        if ($registered) {
            Write-Host "    Ya estaba registrado ('claude mcp get allagents' para verlo)."
        } else {
            & claude mcp add allagents --scope user -- "$Root\.venv\Scripts\allagents.exe" serve
            if ($LASTEXITCODE -ne 0) {
                throw "Error al registrar allagents en Claude Code."
            }
        }
    }
} else {
    Write-Host "Para registrarlo en Claude Code (todas tus carpetas):"
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Register"
}

Write-Host "Abre una sesión nueva de Claude Code para ver las herramientas ask_*."
