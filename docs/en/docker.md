[Español](../es/docker.md) | **English**

# Docker: details and special cases

Complements the quick start in the [README](../../README.md).

**Note on a local `repo_path`**: if you register a project with `source_type: local` pointing to a path on your machine, that path must also be mounted as a volume in `docker-compose.yml` so that the container can see it — the **"Clone from URL"** mode (see Settings in the UI) does not have this problem, because the cloning happens inside the container.

Without a GPU, Ollama runs on CPU (slower, but it works). The compose file includes, commented out, the configuration to use an NVIDIA GPU if the host has one.

**Docker Desktop on a Windows laptop with an 8 GB GPU** (tested on
1 Oct 2026 with an RTX 5070 Laptop): everything works —containers, GPU inside
the container, cloning, indexing and queries—, but inside Docker Ollama sees
only about 6.4 GB of free VRAM (Windows uses the rest). llama3:8b and
qwen3-embedding do not fit together, Ollama swaps them and each reload takes
about a minute and a half: a query made while the documentation was being
generated took 571 s. In that case it is better to use the Ollama installed on
Windows from the container, which sees the whole GPU: with that setup the same
query took 13.6 s (the first one, including the model load) and the next one
6.2 s. It is enough to change in `config/config.yaml`:

```yaml
ai_provider:
  host: http://host.docker.internal:11434
```

and start only the Beacon service, without the Ollama container
(`docker compose up -d --build --no-deps beacon`), or use the installation
without Docker. On a dedicated Linux server, where the GPU is not also used by
the desktop, the full compose file works without this adjustment.

The `beacon` container waits for `ollama` to pass its healthcheck (`depends_on: condition: service_healthy`) before starting, so the first query does not fail because it started before Ollama was ready.
