[Español](../es/modelos-externos.md) | **English**

# External model servers (optional)

Beacon runs its models locally by default, and **local mode with Ollama is the recommended one**: the code never leaves your machine or your network and there is no cost per query. For a given project you can opt into a model server that is not local, for example a commercial API, to get a more capable model. The most visible gain is on open questions about the whole repository, where `llama3:8b` is limited.

## What leaves your network

With an external server, Beacon sends that provider:

- **all of the project's code**, when indexing, to compute embeddings;
- the **code chunks** retrieved for every question and the question itself;
- **whole files** when generating documentation.

That may break your organization's confidentiality or data protection policies. Decide it project by project: a sensitive repository can stay local while another one uses an external server.

## Requirements (there are no defaults)

Beacon refuses to talk to a server outside your machine or private network unless the project has all of this, set by you:

| Requirement | Why |
|---|---|
| Explicit consent, recorded with its date | The code leaves the organization |
| A spending limit in dollars | Beacon stops before a call that would pass it |
| The provider's prices per million tokens (input, output and embeddings) | They are needed to compute the spending; Beacon has no built-in prices because they change |
| An API key | Stored in `config/credentials.yaml` (or in the `BEACON_API_KEY_<PROJECT>` environment variable), never in `config.yaml` and never returned by the API |
| An `https://` address | The key and the code travel over the network |

Servers on `localhost`, on private addresses, `.local` names and single-label names (such as a Docker Compose service) count as local and need none of this, which also covers local OpenAI-compatible servers such as llama.cpp, vLLM or LM Studio.

The limit is Beacon's own estimate: the provider's account is the source of truth for spending. Load a small prepaid credit with the provider and keep it as the real ceiling. A consumer subscription (ChatGPT, Claude, Gemini) does **not** include API access: the API is billed separately, per token.

## Setting it up

```bash
beacon cloud enable my-project \
  --host https://api.openai.com/v1 \
  --embedding-model text-embedding-3-small --llm-model gpt-4o-mini \
  --budget-usd 5 --price-input 0.15 --price-output 0.60 --price-embedding 0.02 \
  --context-window 128000
```

The command shows the notice, asks for confirmation and for the API key (hidden), and saves the project's configuration. The prices above are only an example: use the current ones from your provider. Because the embedding model changes, run `beacon sync my-project --docs` afterwards to rebuild the index.

```bash
beacon cloud status              # projects with their own server, and spent / limit
beacon cloud disable my-project  # back to the global (local) configuration; deletes the key
```

Each project keeps its usage in `data/<id>/usage.json`. When a call would pass the limit, Beacon raises an error (HTTP 402 in the API) instead of sending it.

## Supported servers

The `openai` provider speaks the OpenAI-compatible API (`/chat/completions`, `/embeddings`, `/models`), which many services and local servers implement: OpenAI, OpenRouter, Groq, Mistral, vLLM, llama.cpp, LM Studio and others. Models that reject `max_tokens`, `temperature` or `seed` are handled automatically. Rate limits (429) are retried with backoff.

## Quality

The measurements in [Measured quality](measured-quality.md) correspond to `llama3:8b` with Ollama. With another model, repeat `tools/eval_answers.py` and `tools/eval_retrieval.py` before relying on it.
