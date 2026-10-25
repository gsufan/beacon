[Español](../es/servidores-de-modelos.md) | **English**

# Model servers

**Ollama is the default and the recommended server**, including for Docker (the compose file starts it for you). Everything in this project was built and measured with it, and nothing else is needed.

Other servers can be selected through the `provider` and `host` keys of `ai_provider` in `config.yaml`, or in Settings in the web interface:

| `provider` | Server | Default host | Status |
|---|---|---|---|
| `ollama` | [Ollama](https://ollama.com) | `http://localhost:11434` | Default, recommended, tested |
| `lmstudio` | LM Studio | `http://localhost:1234/v1` | Optional, same adapter as the OpenAI-compatible API |
| `llamacpp` | llama.cpp server | `http://localhost:8080/v1` | Optional, same adapter |
| `vllm` | vLLM | `http://localhost:8000/v1` | Optional, same adapter |
| `openai` | Any other OpenAI-compatible server on your machine or network | `http://localhost:8000/v1` | Optional, same adapter |

```yaml
ai_provider:
  provider: lmstudio
  host: http://localhost:1234/v1
  embedding_model: nomic-embed-text-v1.5      # the name the server uses for the loaded model
  llm_model: meta-llama-3-8b-instruct
  llm_num_ctx: 8192                           # Beacon builds the prompt for this window
```

## What was tested

- **Ollama** through its native API: everything (unit tests, the measurements of this project, Docker).
- **The OpenAI-compatible adapter**: unit tests against simulated servers, and a real run against Ollama's own OpenAI-compatible endpoint (`http://localhost:11434/v1`): model listing, embeddings, chat with and without streaming, a missing model, and a complete question over an existing index.
- **LM Studio, llama.cpp and vLLM** have **not** been tried: they implement the same API, so they should work, but this has not been verified on those servers.
- **Commercial APIs** are not supported in this version: servers outside your machine or private network are refused. An experimental, untested implementation (consent, spending limit, API key) lives in the branch `experimental/paid-apis`.

## Things to keep in mind

- The model names are the ones each server uses for its loaded models; `beacon doctor` lists what the server offers.
- The server needs an embedding model loaded as well as a language model.
- Changing the embedding model rebuilds the index on the next `beacon sync` (vectors from different models are not comparable).
- The quality measurements of this project correspond to `llama3:8b` and `qwen3-embedding:0.6b` with Ollama; with other models, repeat `tools/eval_answers.py` and `tools/eval_retrieval.py`.
- The server must be on this machine or a private network (`localhost`, private addresses, `.local` names, or a Docker Compose service name). Anything else is refused.
