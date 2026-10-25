[English](../en/external-models.md) | **Español**

# Servidores de modelos externos (opcional)

Beacon ejecuta sus modelos de forma local por defecto, y **el modo local con Ollama es el recomendado**: el código nunca sale de tu equipo ni de tu red y no hay costo por consulta. En un proyecto puntual puedes optar por un servidor de modelos que no sea local, por ejemplo una API comercial, para usar un modelo más capaz. La ganancia más visible está en las preguntas abiertas sobre todo el repositorio, donde `llama3:8b` es limitado.

## Qué sale de tu red

Con un servidor externo, Beacon envía a ese proveedor:

- **todo el código del proyecto**, al indexar, para calcular los embeddings;
- los **fragmentos de código** recuperados con cada pregunta y la pregunta misma;
- **archivos completos** al generar la documentación.

Eso puede incumplir las políticas de confidencialidad o de protección de datos de tu organización. Se decide proyecto por proyecto: un repositorio sensible puede seguir siendo solo local mientras otro usa un servidor externo.

## Requisitos (no hay valores por defecto)

Beacon se niega a hablar con un servidor fuera de tu equipo o de tu red privada si el proyecto no tiene todo esto, fijado por ti:

| Requisito | Por qué |
|---|---|
| Consentimiento explícito, registrado con su fecha | El código sale de la organización |
| Un límite de gasto en dólares | Beacon se detiene antes de una llamada que lo superaría |
| Los precios del proveedor por millón de tokens (entrada, salida y embeddings) | Hacen falta para calcular el gasto; Beacon no trae precios incorporados porque cambian |
| Una clave de API | Se guarda en `config/credentials.yaml` (o en la variable de entorno `BEACON_API_KEY_<PROYECTO>`), nunca en `config.yaml` y la API nunca la devuelve |
| Una dirección `https://` | La clave y el código viajan por la red |

Los servidores en `localhost`, en direcciones privadas, nombres `.local` y nombres de una sola palabra (como un servicio de Docker Compose) cuentan como locales y no necesitan nada de esto, lo que cubre también los servidores locales compatibles con OpenAI como llama.cpp, vLLM o LM Studio.

El límite es una estimación de Beacon: la cuenta del proveedor es la fuente de verdad del gasto. Carga un crédito prepagado pequeño con el proveedor y déjalo como tope real. Una suscripción de consumo (ChatGPT, Claude, Gemini) **no** incluye acceso a la API: la API se factura aparte, por token.

## Configuración

```bash
beacon cloud enable mi-proyecto \
  --host https://api.openai.com/v1 \
  --embedding-model text-embedding-3-small --llm-model gpt-4o-mini \
  --budget-usd 5 --price-input 0.15 --price-output 0.60 --price-embedding 0.02 \
  --context-window 128000
```

El comando muestra el aviso, pide confirmación y la clave de API (oculta), y guarda la configuración del proyecto. Los precios de arriba son solo un ejemplo: usa los vigentes de tu proveedor. Como cambia el modelo de embeddings, corre después `beacon sync mi-proyecto --docs` para reconstruir el índice.

```bash
beacon cloud status              # proyectos con servidor propio, y gastado / límite
beacon cloud disable mi-proyecto # vuelve a la configuración global (local); borra la clave
```

Cada proyecto guarda su consumo en `data/<id>/usage.json`. Cuando una llamada superaría el límite, Beacon lanza un error (HTTP 402 en la API) en vez de enviarla.

## Servidores compatibles

El proveedor `openai` usa la API compatible con OpenAI (`/chat/completions`, `/embeddings`, `/models`), que implementan muchos servicios y servidores locales: OpenAI, OpenRouter, Groq, Mistral, vLLM, llama.cpp, LM Studio y otros. Los modelos que rechazan `max_tokens`, `temperature` o `seed` se manejan solos. Los límites de tasa (429) se reintentan con espera progresiva.

## Calidad

Las mediciones de [Calidad medida](calidad-medida.md) corresponden a `llama3:8b` con Ollama. Con otro modelo, repite `tools/eval_answers.py` y `tools/eval_retrieval.py` antes de confiar en él.
