# AllAgents

Servidor **MCP local** que permite a Claude Code delegar subtareas a otras IAs
(tu Ollama local, Groq, Mistral, Cohere, Gemini, ChatGPT, OpenCode, DeepSeek…)
para **ahorrar tokens de Claude**. Claude ve cada IA como una herramienta
(`ask_ollama`, `ask_groq`, …) y recibe una **lista de prioridades**: primero lo
local e ilimitado, después lo gratuito y al final lo que tiene cuota escasa o se paga.

- **Se adapta a lo que tengas.** Detecta qué IAs hay en cada equipo y solo
  publica esas. Puedes añadir cualquier API compatible con OpenAI **sin escribir
  código**, solo con variables en `.env`.
- **Ahorra contexto de verdad.** Claude pasa *rutas* de archivos; el servidor los
  lee y los envía, así su contenido nunca entra en el contexto de Claude.
- **Sin puertos de red.** Solo funciona por stdio (Claude Code lo lanza como
  proceso hijo); nadie puede conectarse a él desde la red. Ver [SECURITY.md](SECURITY.md).
- **Protege los datos sensibles.** Las carpetas marcadas como solo-locales solo
  pueden enviarse a modelos locales; la nube las rechaza.

---

## Instalación (5 minutos)

**Requisitos:** Linux, macOS o Windows 10/11, Python 3.11+, [Claude Code](https://claude.com/claude-code),
y al menos una IA de la tabla de abajo.

**Linux / macOS:**
```bash
git clone <url-del-repo> AllAgents && cd AllAgents
./scripts/setup.sh --register
```

**Windows (PowerShell):**
```powershell
git clone <url-del-repo> AllAgents; cd AllAgents
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Register
```
*(Sin `-Register` solo instala, sin registrar el servidor en Claude Code).*

El script de instalación (`setup.sh` / `setup.ps1`) crea el entorno virtual con
dependencias de versión fijada, crea tu `.env` (permisos restringidos a tu usuario),
activa el hook de git que bloquea secretos y, con `--register` / `-Register`, registra
el servidor en Claude Code para todas tus carpetas. En terminales interactivas lanza
automáticamente el asistente `allagents init` para analizar tu equipo (GPU, Ollama, CLIs,
carpetas) y configurar el entorno sin edición manual (puedes omitirlo con `--no-init` o `-NoInit`).

Después:

1. Responde al asistente (o ejecuta `allagents init` cuando quieras); también puedes editar `.env` a mano.
   En Linux/macOS el archivo está oculto (en el explorador pulsa `Ctrl+H` para verlo); en Windows no está oculto.
2. Comprueba qué quedó disponible:
   ```bash
   .venv/bin/allagents check
   ```
   *(En Windows usa `.venv\Scripts\allagents check`)*
3. Abre una sesión **nueva** de Claude Code: las herramientas `ask_*` aparecen solas.
4. **Recomendado:** haz que Claude delegue por iniciativa propia (siguiente sección).

---

## Configuración con el asistente (`allagents init`)

El asistente detecta tu entorno y te ayuda a configurar `.env` sin tener que editarlo a mano:

1. **Analiza el equipo**: detecta la carpeta real de Documentos (incluso redirigida en Windows a OneDrive u otra unidad), carpetas habituales de proyectos (`~/source/repos`, `~/Projects`, `~/dev`, `~/code`, `~/GitHub`… y la de AllAgents), Ollama (modelos y estado), VRAM de GPU NVIDIA, LM Studio (`127.0.0.1:1234`), CLIs (`agy`, `codex`, `copilot`, proponiendo `<NOMBRE>_BIN` si están en PATH personal) y permisos del proyecto.
2. **Propone valores según tu equipo**: `ALLAGENTS_ALLOWED_DIRS` (lo configurado + Documentos + proyectos), `OLLAMA_MODEL` y `OLLAMA_FAST_MODEL` según los modelos instalados, `OLLAMA_NUM_CTX` adaptado a la VRAM (16384 / 12288 / 8192) y un agente para LM Studio si está en marcha.
3. **Preguntas guiadas**: consulta por carpetas permitidas adicionales, carpetas solo-locales (datos sensibles) y claves de servicios en la nube pendientes (Groq, Mistral, Cohere, OpenCode Zen, DeepSeek). Las claves se introducen sin eco, se validan contra el proveedor y pregunta si proteger el proyecto si otros usuarios tienen acceso.
4. **Aplica con seguridad**: muestra un resumen con claves enmascaradas (`••••••`). Tras confirmar, actualiza o añade variables en `.env` sin borrar nada existente, restringe permisos a tu usuario y ejecuta `allagents check`.
5. **Opciones no interactivas**: `--dry-run` para ver el análisis y propuesta sin modificar nada, y `--yes` para aplicar lo detectado sin preguntar (no pide claves ni cambia los permisos de la carpeta del proyecto).
6. **Reutilizable**: se lanza automáticamente en el `setup` inicial (se salta con `./scripts/setup.sh --no-init` o `scripts\setup.ps1 -NoInit`) y puedes relanzarlo cuando quieras al instalar nuevos modelos o añadir claves.

```bash
.venv/bin/allagents init            # asistente interactivo guiado
.venv/bin/allagents init --dry-run  # solo analiza y propone cambios sin tocar nada
.venv/bin/allagents init --yes      # aplica lo detectado automáticamente sin preguntar
```
*(En Windows usa `.venv\Scripts\allagents init`)*

---

## Que Claude delegue por iniciativa propia

El servidor ya envía a Claude la lista de prioridades y las reglas al empezar cada
sesión, sin que tengas que hacer nada. Pero si no se le recuerda, Claude tiende a
hacer el trabajo él mismo. Para que delegue de forma proactiva, pega este bloque en
`~/.claude/CLAUDE.md` (si no existe, créalo). Claude Code lo carga en todas tus
sesiones, en cualquier carpeta:

```markdown
## Delegación con AllAgents
Tienes el servidor MCP `allagents` (herramientas `ask_*`). Úsalo de forma proactiva para ahorrar tokens:
- Antes de hacer tú una tarea delegable (resumir archivos o logs, extraer datos, generar
  boilerplate, tests o docstrings, revisar código, segundas opiniones), delégala siguiendo
  la prioridad de sus instrucciones: primero `ask_ollama`.
- Pasa los archivos por ruta absoluta en `files`; no los leas tú antes.
- Datos sensibles (clientes, credenciales, layouts bancarios): solo `ask_ollama`.
- Revisa lo que devuelvan antes de aplicarlo y dime en una línea qué agente usaste.
- Si no ves las herramientas `ask_*`, avísame en lugar de seguir sin ellas.
```

Es corto a propósito: se carga en cada sesión. También puedes pedirlo en el momento:

- *"Resume este log con allagents: /home/…/app.log"*
- *"Pídele a Mistral que revise /home/…/procedimiento.sql"*
- *"Revisa C:\Users\tú\source\repos\mi-app\servicios.py con Mistral"*
- *"Usa solo Ollama, son datos de clientes"*
- *"Antes de responder, pide una segunda opinión a Codex"*

---

## Agentes y prioridades

Orden por defecto. Claude usa el primero que sirva para la tarea y baja por la
lista solo cuando necesita más calidad, contexto o velocidad.

| # | Herramienta | IA | Nivel | Úsala para | Qué necesitas |
|---|---|---|---|---|---|
| 1 | `ask_ollama` | Modelos locales (qwen3-coder…) | **local · ilimitado** | Primera opción siempre; obligatoria con datos sensibles | [Ollama](https://ollama.com) corriendo |
| 2 | `ask_groq` | Qwen / GPT-OSS en LPU | gratis con límites | JSON, extracción de datos, scaffolding, baja latencia | `GROQ_API_KEY` |
| 3 | `ask_mistral` | Codestral | gratis con límites | Revisión de código, Python, T-SQL, Docker, C# | `MISTRAL_API_KEY` o `CODESTRAL_API_KEY` |
| 4 | `ask_cohere` | Command R | gratis con límites | Logs y documentos largos con citas (RAG) | `COHERE_API_KEY` |
| 5 | `ask_agy` | Google Gemini | suscripción con límites | Archivos gigantes, mucha documentación | CLI `agy` con sesión |
| 6 | `ask_opencode` | DeepSeek v4.1 Flash Free y otros | gratis (cupo muy bajo) | Código y razonamiento general | `OPENCODE_API_KEY` |
| 7 | `ask_codex` | ChatGPT | cuota escasa | Lógica difícil, revisión crítica | CLI `codex` con sesión |
| 8 | `ask_deepseek` | DeepSeek | pago por uso | Alternativa barata a ChatGPT | `DEEPSEEK_API_KEY` |
| 9 | `ask_copilot` | GitHub Copilot | suscripción | *Experimental, desactivado* | CLI `copilot` |

Además, `list_agents` muestra el estado de todos y por qué falta alguno.

La regla completa que recibe Claude (qué delegar, cómo, qué no mandar a la nube)
se genera según los agentes disponibles. Para verla:

```bash
.venv/bin/allagents check --instructions
```
*(En Windows: `.venv\Scripts\allagents check --instructions`)*

Detalle de cada agente (conexión, costes, límites, modelos): [docs/agents.md](docs/agents.md).

---

## Adaptarlo a tus IAs

De menos a más trabajo:

1. **No hacer nada.** Lo que no esté instalado o no tenga clave no se publica.
2. **Desactivar uno** aunque esté configurado: `ALLAGENTS_DISABLE=codex,agy`
3. **Cambiar el orden**: `ALLAGENTS_PRIORITY=ollama,mistral,groq` (los que no
   pongas van detrás, en su orden por defecto).
4. **Añadir una IA compatible con OpenAI sin código** (OpenRouter, Together,
   xAI, LM Studio, vLLM…):
   ```bash
   ALLAGENTS_CUSTOM=openrouter
   OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
   OPENROUTER_API_KEY=...
   OPENROUTER_MODEL=deepseek/deepseek-chat
   OPENROUTER_ROLE=acceso a muchos modelos con una sola clave
   OPENROUTER_DESCRIPTION=OpenRouter. Úsala para … NO la uses para …
   ```
   Crea la herramienta `ask_openrouter`. Para un servidor en tu máquina añade
   `<NOMBRE>_LOCAL=true` (solo se acepta con una URL `127.0.0.1`/`localhost`).
5. **Añadir una IA con API propia o un CLI**: crea un módulo en
   `src/allagents/agents/`, que se descubre solo. Guía: [docs/adding-an-agent.md](docs/adding-an-agent.md).

---

## Comandos

```bash
.venv/bin/allagents init                   # asistente interactivo guiado de configuración
.venv/bin/allagents init --dry-run         # solo muestra análisis y propuesta sin modificar nada
.venv/bin/allagents init --yes             # aplica lo detectado automáticamente sin preguntar
.venv/bin/allagents check                  # estado de cada agente y del .env
.venv/bin/allagents check --instructions   # … y la regla de prioridades que recibe Claude
.venv/bin/allagents models groq            # modelos disponibles de un agente
.venv/bin/allagents fix-permissions        # asegura permisos restringidos de .env y logs/
.venv/bin/allagents fix-permissions --project   # … e impide que otros usuarios cambien el código
.venv/bin/python scripts/smoke_test.py     # prueba real por MCP de todos los agentes
.venv/bin/python scripts/smoke_test.py groq ollama   # … o solo algunos
.venv/bin/python scripts/smoke_test.py --security    # verificación de seguridad
```

*(En Windows usa `.venv\Scripts\` en lugar de `.venv/bin/`, p. ej. `.venv\Scripts\allagents check` o `.venv\Scripts\python scripts\smoke_test.py`).*

`allagents serve` lo lanza Claude Code; no hace falta ejecutarlo a mano.
El registro de auditoría está en `logs/allagents.log`: solo metadatos (agente,
modelo, duración, tokens), nunca prompts, archivos ni respuestas.

---

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| No aparece ninguna herramienta `ask_*` en Claude | Abre una sesión nueva. Comprueba que está registrado: `claude mcp list` |
| Falta una herramienta concreta | `allagents check` dice por qué (clave, CLI, servicio caído…) |
| `.env ignorado: … permisos` | Ejecuta `allagents fix-permissions` para dejar `.env` y `logs/` accesibles solo para tu usuario |
| `… es accesible para otros usuarios` (Windows) | En Windows, si el proyecto está fuera de tu perfil (`C:\proyectos`, `D:\…`), la ACL da acceso a "Authenticated Users" o "Usuarios". Solución: `.venv\Scripts\allagents fix-permissions` |
| `… script .cmd que no se puede lanzar de forma segura` | En Windows un CLI de npm no se reconoce como shim estándar. Define `CODEX_BIN`, `AGY_BIN` o `COPILOT_BIN` con la ruta directa al `.exe` (p. ej. `%LOCALAPPDATA%\agy\bin\agy.exe`) |
| `la línea de comandos supera el límite de Windows` | La línea de comandos en Windows tiene un límite de ~32 767 caracteres; Copilot (pasa el prompt por argumento) admite ~30 KB. Reduce el prompt o los archivos adjuntos |
| `Otros usuarios (…) pueden modificar el código del servidor` | En Windows, fuera de tu perfil (`C:\proyectos`, `D:\…`), las carpetas heredan permiso de modificación para otros usuarios. Solución: `allagents fix-permissions --project` |
| `Carpeta ignorada` en `allagents check` | Rutas relativas ignoradas o separador incorrecto (`:` en Linux/macOS, `;` en Windows). Usa rutas absolutas en `ALLAGENTS_ALLOWED_DIRS` |
| `fuera de ALLAGENTS_ALLOWED_DIRS` | Añade la carpeta de tu proyecto a `ALLAGENTS_ALLOWED_DIRS` (usa `;` como separador en Windows) |
| No se envían archivos de mi carpeta Documentos (OneDrive) | `allagents init` detecta la carpeta real (incluso redirigida a OneDrive u otra unidad); o añádela a `ALLAGENTS_ALLOWED_DIRS` |
| `HTTP 429` / `Free usage limit reached` | Se agotó el cupo de ese proveedor; Claude pasará al siguiente |
| `model not found` / `model_not_found` | El proveedor retiró el modelo: `allagents models <agente>` y cambia `<AGENTE>_MODEL` |
| `llama-server process has terminated` / `CUDA error` (Ollama) | No cabe el modelo con ese contexto en la memoria de vídeo: baja `OLLAMA_NUM_CTX` (p. ej. 12288 u 8192) |
| `codex no tiene sesión iniciada` | Ejecuta `codex login` en una terminal |
| `ask_ollama` no aparece | Ollama no está corriendo en `OLLAMA_URL` (`ollama serve` o el servicio del sistema). En Windows comprueba con `Get-NetTCPConnection -LocalPort 11434 -State Listen` que escuche en 127.0.0.1 / ::1 |
| Tras añadir una clave no cambia nada | El servidor lee `.env` al arrancar: abre una sesión nueva de Claude Code |

---

## Estructura

```
AllAgents/
├── .github/workflows/
│   └── tests.yml          # CI en Ubuntu y Windows
├── src/allagents/
│   ├── __main__.py        # CLI: serve | check | init | models | fix-permissions
│   ├── init_wizard.py     # asistente allagents init
│   ├── server.py          # servidor MCP (solo stdio) + regla de prioridades para Claude
│   ├── config.py          # carga de .env con verificación de permisos
│   ├── osutil.py          # todo lo que cambia entre sistemas: permisos/ACL, separadores, PATH, matar árbol, shims .cmd
│   ├── security.py        # política de archivos, redacción de secretos, entorno de hijos, URLs
│   ├── files.py           # lectura de archivos y armado del prompt
│   ├── audit.py           # registro de auditoría (solo metadatos)
│   ├── clients/
│   │   ├── cli.py         # ejecución aislada de CLIs (carpeta temporal, sin shell, timeouts)
│   │   ├── http.py        # HTTP endurecido (TLS, sin redirecciones, sin proxies del entorno)
│   │   └── openai_compat.py  # Chat Completions (Groq, Mistral, OpenCode, DeepSeek, custom)
│   └── agents/            # un módulo por IA, descubiertos automáticamente
│       ├── base.py        # contrato de agente, niveles y flujo común
│       ├── custom.py      # agentes definidos solo en .env (ALLAGENTS_CUSTOM)
│       └── ollama.py groq.py mistral.py cohere.py agy.py opencode.py codex.py deepseek.py copilot.py
├── tests/                 # pytest: seguridad, configuración, prioridades, agentes custom
│   ├── conftest.py
│   ├── test_init.py
│   └── test_platform.py
├── scripts/
│   ├── setup.sh           # instalación en Linux/macOS (+ --register)
│   ├── setup.ps1          # instalación en Windows (+ -Register)
│   ├── smoke_test.py      # prueba de punta a punta por MCP (+ --security)
│   └── git-hooks/pre-commit  # bloquea commits con secretos
├── docs/
│   ├── agents.md          # detalle de cada agente
│   └── adding-an-agent.md # cómo añadir una IA
├── SECURITY.md            # modelo de amenazas, medidas y cómo verificarlas
├── .env.example           # plantilla de configuración (sin secretos)
├── .gitattributes
├── requirements.in        # dependencia directa (entrada para regenerar el lock)
├── requirements.lock      # dependencias con versión exacta y hashes (todas las plataformas)
└── pyproject.toml
```

## Desarrollo

**Linux / macOS:**
```bash
.venv/bin/pip install pytest==9.2.0
.venv/bin/python -m pytest
```

**Windows:**
```powershell
.venv\Scripts\pip install pytest==9.2.0
.venv\Scripts\python -m pytest
```

*(En Windows, 2 tests de enlaces simbólicos se omiten salvo con el Modo desarrollador activado).*

Antes de cada commit, el hook `pre-commit` rechaza archivos de secretos y texto
con forma de clave. Es local: se activa al ejecutar `./scripts/setup.sh` o `scripts\setup.ps1` en cada clon.
