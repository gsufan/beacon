[English](README.md) | **Español**

# Beacon

[![Tests](https://github.com/gsufan/beacon/actions/workflows/tests.yml/badge.svg)](https://github.com/gsufan/beacon/actions/workflows/tests.yml)

Servicio **autoalojado (on-premise)** de apoyo para comprender y documentar la deuda técnica de un repositorio de código, usando RAG (retrieval-augmented generation) sobre modelos de lenguaje que se ejecutan en la propia máquina o servidor de la organización: sin costo por consulta y sin enviar el código a servicios externos. Las respuestas citan el archivo y las líneas de donde salen, para que puedan verificarse.

Proyecto de título — INACAP, 2026.

## Qué hace

1. Indexa un repositorio de código con un **chunker políglota basado en AST** (tree-sitter), que entiende la estructura real de funciones, clases y métodos en 8+ lenguajes.
2. Genera embeddings locales (Ollama + `qwen3-embedding:0.6b`, multilingüe) y los guarda en **ChromaDB**.
3. Responde preguntas sobre el código vía un motor RAG con **expansión por grafo de llamadas** (si el código citado llama a otra función indexada, se agrega automáticamente al contexto) y reglas explícitas anti-alucinación.
4. Genera documentación `.md` por archivo, incremental — solo regenera lo que cambió desde el último commit indexado. Con esa documentación arma un resumen del repositorio (uno por carpeta y uno del proyecto) que responde preguntas abiertas como «¿qué hace este proyecto?».
5. Todo esto disponible por **CLI** y por una **interfaz web** (chat, navegador de documentación, configuración) servida por el mismo backend.

## Modos de uso

- **Una persona:** instalación local desde el repositorio y la consola (`beacon ask`, `beacon sync`). Ejecuta `python tools/install.py` para prepararlo todo en un paso y usa después el lanzador `beacon`; la [instalación manual](docs/es/instalacion-manual.md) incluye también el proceso largo paso a paso.
- **Un equipo:** un servidor con Docker Compose, con un único índice compartido, al que se accede desde el navegador, la consola o la API REST. Es la forma recomendada y se describe a continuación.

## Inicio rápido con Docker (recomendado)

Levanta Beacon **y** Ollama en contenedores, sin instalar Python ni Node en tu máquina.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
cp config/config.example.yaml config/config.yaml
```

Edita `config/config.yaml`: deja `host: http://ollama:11434` (el nombre `ollama` lo resuelve la red interna de Docker Compose — no uses `localhost` ahí) y agrega el/los repo(s) que quieras indexar.

```bash
docker compose up -d --build

# primera vez: descargar los modelos dentro del contenedor de Ollama
docker compose exec ollama ollama pull qwen3-embedding:0.6b
docker compose exec ollama ollama pull llama3:8b

# indexar un proyecto
docker compose exec beacon beacon sync <project_id>
docker compose exec beacon beacon docs <project_id>
```

La UI queda en `http://localhost:8000`. `./config` y `./data` quedan
montados desde tu máquina, así que la configuración y los índices
persisten entre reinicios del contenedor.

Más detalles (GPU en Windows, rutas locales, healthcheck) en [docs/es/docker.md](docs/es/docker.md).

## Participar en el estudio (modo estudio)

Si te invitaron a probar Beacon para el estudio de evaluación, necesitas Python 3.10, 3.11 o 3.12 (el 3.13 o superior todavía no funciona), Git, Node 20+ y [Ollama](https://ollama.com) con los modelos instalados.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
python tools/install.py    # entorno, dependencias, interfaz web y modelos (Linux/macOS: python3)
.\beacon                   # abre una consola con el entorno activo (Linux/macOS: ./beacon)
beacon study
```

El asistente se abre en el navegador y te guía: aceptas la participación, eliges (o no) compartir los datos de tu equipo, pegas la dirección de un repositorio público, haces al menos 3 preguntas valorando cada respuesta y revisas el resultado. Al terminar, `beacon-estudio-resultados-<id>.json` queda en la carpeta del proyecto (sin código, rutas ni tu nombre); envíalo con el botón «Enviar mis resultados» (si está disponible) o por el canal de la invitación. Detalle en [docs/es/estudio.md](docs/es/estudio.md).

## Documentación

| Documento | Contenido |
|---|---|
| [Instalación manual](docs/es/instalacion-manual.md) | Sin Docker, en Windows y Linux/macOS; cómo compartirlo con un equipo |
| [Uso](docs/es/uso.md) | CLI, interfaz web, sincronización automática, exportar e importar proyectos |
| [Docker](docs/es/docker.md) | Detalles y casos particulares del despliegue con contenedores |
| [Calidad medida](docs/es/calidad-medida.md) | Resultados de la búsqueda, de las respuestas y de las pruebas de carga |
| [Seguridad](docs/es/seguridad.md) | Limitaciones conocidas y clave de API |
| [Modo estudio](docs/es/estudio.md) | Participar en el estudio y analizar los resultados recibidos |
| [Servidores de modelos](docs/es/servidores-de-modelos.md) | Ollama (por defecto, recomendado) y alternativas opcionales: LM Studio, llama.cpp, vLLM |
| [Desarrollo](docs/es/desarrollo.md) | Estructura del código, frontend y pruebas |

## Resultados en breve

Sobre `psf/requests` (30 preguntas), el fragmento que responde aparece entre los cinco primeros resultados en el 93 % de los casos. Con 2.000 archivos, el indexado completo toma 6 minutos y la búsqueda tiene un p95 de 35 ms (sin contar el modelo de lenguaje). Los conjuntos de preguntas son chicos: sirven para comparar configuraciones, no como garantía general. Detalle en [Calidad medida](docs/es/calidad-medida.md).

## Licencia

MIT — ver [LICENSE](LICENSE).
