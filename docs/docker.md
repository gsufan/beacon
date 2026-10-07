# Docker: detalles y casos particulares

Complementa el inicio rápido del [README](../README.md).

**Nota sobre `repo_path` local**: si registras un proyecto con `source_type: local` apuntando a una ruta de tu máquina, esa ruta tiene que estar además montada como volumen en `docker-compose.yml` para que el contenedor la vea — el modo **"Clonar desde URL"** (ver Configuración en la UI) no tiene este problema, porque el clonado ocurre dentro del contenedor.

Sin GPU, Ollama corre sobre CPU (más lento pero funciona). El compose incluye, comentada, la configuración para usar GPU NVIDIA si el host la tiene.

**Docker Desktop en un notebook con Windows y GPU de 8 GB** (probado el
1-oct-2026 con una RTX 5070 Laptop): todo funciona —contenedores, GPU dentro
del contenedor, clonado, indexado y consultas—, pero dentro de Docker Ollama
ve solo unos 6,4 GB de VRAM libres (Windows usa el resto). llama3:8b y
qwen3-embedding no caben juntos, Ollama los alterna y cada recarga tarda
cerca de un minuto y medio: una consulta mientras se generaba la
documentación tardó 571 s. En ese caso conviene usar el Ollama instalado en
Windows desde el contenedor, que ve la GPU completa: con esa configuración
la misma consulta tardó 13,6 s (la primera, incluida la carga del modelo) y
la siguiente 6,2 s. Basta con cambiar en `config/config.yaml`:

```yaml
ollama_host: http://host.docker.internal:11434
```

y levantar solo el servicio de Beacon, sin el contenedor de Ollama
(`docker compose up -d --build --no-deps beacon`), o usar la instalación sin
Docker. En un
servidor dedicado con Linux, donde la GPU no la usa también el escritorio,
el compose completo funciona sin este ajuste.

El contenedor `beacon` espera a que `ollama` pase su healthcheck (`depends_on: condition: service_healthy`) antes de arrancar, así que no falla la primera consulta por arrancar antes de que Ollama esté listo.
