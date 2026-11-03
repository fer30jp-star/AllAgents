# Agentes

Detalle de cada IA integrada, en su orden de prioridad por defecto. La regla
de **cuándo** usar cada una vive en el docstring de su herramienta
(`src/allagents/agents/<agente>.py`), que es lo que Claude lee. Aquí está el
resto: cómo se conecta, qué necesitas, cuánto cuesta y sus límites.

Todas las herramientas aceptan `prompt` (la tarea, autocontenida) y `files`
(rutas absolutas que el servidor lee y adjunta). La mayoría acepta `model`
para cambiar de modelo en una llamada concreta.

## Niveles

| Nivel | Significado | Agentes |
|---|---|---|
| local · ilimitado | Corre en tu máquina; gratis y privado | ollama (y custom con `_LOCAL=true`) |
| gratis con límites | Plan gratuito o de prueba con límites por minuto o por mes | groq, mistral, cohere, opencode |
| suscripción con límites | Usa una suscripción que ya pagas, con cuotas | agy, copilot |
| cuota escasa | Suscripción valiosa: resérvala para lo difícil | codex |
| pago por uso | Se cobra por token | deepseek |

El orden se puede cambiar con `ALLAGENTS_PRIORITY` (ver README).

---

## 1. ask_ollama — local

- **Necesitas:** [Ollama](https://ollama.com) corriendo en `OLLAMA_URL` (por
  defecto `http://127.0.0.1:11434`; solo se aceptan direcciones locales) y los
  modelos descargados: `ollama pull qwen3-coder:30b` y `ollama pull qwen2.5-coder:7b`.
- **Coste:** gratis, sin límites. Los datos no salen del equipo.
- **Modelos:** `OLLAMA_MODEL` (calidad) y `OLLAMA_FAST_MODEL` (con `fast=True`).
- **Elegir modelo según tu equipo:** los modelos *MoE* (como `qwen3-coder:30b`)
  van mucho más rápido que los *densos* del mismo tamaño cuando no caben en la
  GPU, porque en cada token solo leen una parte de sus pesos. Referencia medida
  en un Ryzen 7 5800X3D con 64 GB DDR4 y ~2 GB de VRAM libre:

  | Modelo | Tipo | Generación |
  |---|---|---|
  | qwen3-coder:30b | MoE (~18 GB, casi todo en RAM) | 17 tokens/s |
  | qwen2.5-coder:32b | Denso (~20 GB) | 1,7 tokens/s |

  Con poca RAM, usa modelos de 7–8B.
- **Contexto:** `OLLAMA_NUM_CTX` (16384 por defecto). Más contexto consume más memoria de
  vídeo: si el modelo casi llena la GPU, Ollama puede caerse (`llama-server process has
  terminated … CUDA error`); bájalo a 12288 u 8192. `allagents init` propone un valor
  según cuánto del modelo cabe en la VRAM (medido: un modelo de 18 GB en una GPU de
  16 GB funciona con 12288).
- Si Ollama no responde al arrancar, la herramienta no se publica.

## 2. ask_groq — Groq

- **Necesitas:** `GROQ_API_KEY` ([console.groq.com/keys](https://console.groq.com/keys)).
- **Conexión:** `https://api.groq.com/openai/v1` (compatible con OpenAI).
- **Coste:** plan gratuito con límites de peticiones y tokens por minuto.
- **Modelos:** `allagents models groq`. Groq ya no ofrece Llama ni Mixtral para
  chat (noviembre 2026). Medido con una extracción a JSON:

  | Modelo | Tiempo | Tokens de salida |
  |---|---|---|
  | `qwen/qwen3.8-27b` (por defecto) | 0,30 s | 60 |
  | `openai/gpt-oss-20b` | 0,77 s | 325 (incluye razonamiento) |
  | `openai/gpt-oss-120b` | 0,88 s | 264 (incluye razonamiento) |

  Los tres dieron el JSON correcto. Qwen es el predeterminado porque gasta
  menos del cupo por minuto; `gpt-oss-120b` para tareas más complejas.
- **Extra:** `json_mode=True` fuerza salida JSON válida.

## 3. ask_mistral — Codestral

- **Necesitas:** `CODESTRAL_API_KEY` ([codestral.mistral.ai](https://codestral.mistral.ai),
  endpoint dedicado) o `MISTRAL_API_KEY` ([console.mistral.ai](https://console.mistral.ai/api-keys)).
  Si están las dos, se usa la de Codestral.
- **Coste:** plan gratuito con límites; después, pago por uso.
- **Modelos:** `allagents models mistral`. Por defecto `codestral-latest`.

## 4. ask_cohere — Command R

- **Necesitas:** `COHERE_API_KEY` ([dashboard.cohere.com](https://dashboard.cohere.com/api-keys)).
- **Conexión:** API v2 (`https://api.cohere.com/v2/chat`).
- **RAG:** cada archivo de `files` se envía como documento separado y la
  respuesta indica qué documentos se citaron.
- **Coste:** clave de prueba gratuita con límite mensual; producción de pago.
- **Modelos:** `allagents models cohere`. Por defecto `command-r-08-2024` (128k);
  `command-a-03-2025` admite 256k.

## 5. ask_agy — Google Gemini

- **Necesitas:** el CLI `agy` (Google Antigravity) con la sesión iniciada
  (ejecútalo una vez a mano). Se busca en el PATH o en `AGY_BIN`.
- **Conexión:** `agy` en modo print, con el prompt por stdin (stream-json).
- **Coste:** incluido en tu suscripción de Google, con sus límites.
- **Modelos:** `agy models`. Por defecto `gemini-3.8-flash-medium`.
- **Nota:** agy añade ~7000 tokens propios a cada llamada (su prompt de
  sistema). No importa en tareas grandes, pero no lo uses para preguntas mínimas.
- **Confinamiento:** SECURITY.md §4.

## 6. ask_opencode — OpenCode Zen (modelos gratuitos)

- **Necesitas:** `OPENCODE_API_KEY` ([opencode.ai/auth](https://opencode.ai/auth)).
- **Conexión:** API HTTP de Zen (`https://opencode.ai/zen/v1`, compatible con
  OpenAI). **No** se usa el CLI `opencode`: ver SECURITY.md §4.
- **Coste:** gratis, pero el cupo es **muy bajo** y compartido por todos los
  modelos gratuitos de la cuenta (en las pruebas se agotó tras ~10 llamadas).
  Por eso tiene prioridad baja.
- **Modelos:** `allagents models opencode`. Por defecto `deepseek-v4.1-flash-free`;
  otros gratuitos: `glm-5.3-flash-free`, `mimo-v2.6-flash-free`,
  `nemotron-3.5-lightning-free`.

## 7. ask_codex — ChatGPT

- **Necesitas:** `npm i -g @openai/codex` y `codex login` con tu cuenta de
  ChatGPT. Se busca en el PATH o en `CODEX_BIN`.
- **Coste:** incluido en tu plan de ChatGPT, con límites de uso. Es la más
  valiosa: Claude la reserva para lo difícil.
- **Modelos:** por defecto el de tu cuenta; `CODEX_MODEL` para fijar otro.
- **Confinamiento:** SECURITY.md §4.

## 8. ask_deepseek — DeepSeek

- **Necesitas:** `DEEPSEEK_API_KEY` ([platform.deepseek.com](https://platform.deepseek.com/api_keys)).
- **Conexión:** `https://api.deepseek.com` (compatible con OpenAI).
- **Coste:** pago por uso, muy barato.
- **Datos:** se procesan y guardan en China. Nunca datos sensibles.
- **Modelos:** `deepseek-chat` (por defecto) y `deepseek-reasoner`.

## 9. ask_copilot — GitHub Copilot (experimental)

- **Necesitas:** `npm i -g @github/copilot` y un plan de Copilot que incluya el
  CLI (Copilot Free no lo incluye: responde "Access denied by policy settings";
  en organizaciones, el administrador debe habilitar la política "Copilot CLI").
- **Estado:** desactivado por defecto (`ALLAGENTS_DISABLE=copilot`). Su
  confinamiento no se ha verificado; antes de activarlo, sigue SECURITY.md §4.

## Agentes personalizados

Cualquier API compatible con OpenAI se añade solo con variables en `.env`
(`ALLAGENTS_CUSTOM`). Ver README → "Adaptarlo a tus IAs" y `.env.example`.
