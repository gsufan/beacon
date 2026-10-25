[English](../en/model-servers.md) | **Español**

# Servidores de modelos

**Ollama es el servidor por defecto y el recomendado**, también para Docker (el compose lo levanta por ti). Todo este proyecto se construyó y se midió con él, y no hace falta nada más.

Se puede elegir otro servidor con las claves `provider` y `host` de `ai_provider` en `config.yaml`, o en Configuración en la interfaz web:

| `provider` | Servidor | Dirección por defecto | Estado |
|---|---|---|---|
| `ollama` | [Ollama](https://ollama.com) | `http://localhost:11434` | Por defecto, recomendado, probado |
| `lmstudio` | LM Studio | `http://localhost:1234/v1` | Opcional, mismo adaptador que la API compatible con OpenAI |
| `llamacpp` | Servidor de llama.cpp | `http://localhost:8080/v1` | Opcional, mismo adaptador |
| `vllm` | vLLM | `http://localhost:8000/v1` | Opcional, mismo adaptador |
| `openai` | Cualquier API compatible con OpenAI | — | Opcional; ver [servidores de modelos externos](modelos-externos.md) |

```yaml
ai_provider:
  provider: lmstudio
  host: http://localhost:1234/v1
  embedding_model: nomic-embed-text-v1.5      # el nombre que usa el servidor para el modelo cargado
  llm_model: meta-llama-3-8b-instruct
  llm_num_ctx: 8192                           # Beacon arma el prompt para esta ventana
```

## Qué se probó

- **Ollama** con su API nativa: todo (tests unitarios, las mediciones de este proyecto, Docker).
- **El adaptador compatible con OpenAI**: tests unitarios contra servidores simulados y una corrida real contra el endpoint compatible con OpenAI del propio Ollama (`http://localhost:11434/v1`): listado de modelos, embeddings, chat con y sin streaming, un modelo inexistente y una pregunta completa sobre un índice existente.
- **LM Studio, llama.cpp y vLLM** **no** se han probado: implementan la misma API, así que deberían funcionar, pero no está verificado en esos servidores.
- **Las APIs de pago** **no están probadas**; ver [servidores de modelos externos](modelos-externos.md).

## A tener en cuenta

- Los nombres de modelo son los que cada servidor usa para sus modelos cargados; `beacon doctor` lista lo que ofrece el servidor.
- El servidor necesita un modelo de embeddings cargado además del de lenguaje.
- Cambiar el modelo de embeddings reconstruye el índice en el siguiente `beacon sync` (los vectores de modelos distintos no son comparables).
- Las mediciones de calidad de este proyecto corresponden a `llama3:8b` y `qwen3-embedding:0.6b` con Ollama; con otros modelos, repite `tools/eval_answers.py` y `tools/eval_retrieval.py`.
- El servidor debe estar en esta máquina o en una red privada. Cualquier otro es un servidor externo y exige consentimiento, un límite de gasto y precios.
