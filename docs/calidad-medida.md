# Calidad medida

Mediciones de la búsqueda, de las respuestas y de la carga. Las herramientas están en `tools/` y los conjuntos de preguntas en `eval/`.

## Calidad de la búsqueda (medida)

`tools/eval_retrieval.py` mide la recuperación contra conjuntos de
preguntas de referencia, escritas en español y cada una con la función que
la responde:

- `eval/requests.yaml`: 30 preguntas sobre psf/requests (Python).
- `eval/microservices-demo.yaml`: 22 preguntas sobre
  GoogleCloudPlatform/microservices-demo, 11 servicios en Go, C#,
  JavaScript, Python y Java.
- `eval/requests-identificadores.yaml` y
  `eval/microservices-demo-identificadores.yaml`: 15 y 10 preguntas que
  nombran un identificador ("¿para qué sirve `super_len`?", "¿dónde se usa
  `max_redirects`?"), como las hace quien ya vio el nombre en un error o un
  log.

```bash
python tools/eval_retrieval.py <project_id> eval/requests.yaml
```

Si una función esperada no existe en el índice, la herramienta se detiene
antes de medir, para que un error en el conjunto no se cuente como fallo de
la búsqueda.

| Configuración | Hit@1 | Hit@5 | Hit@10 | MRR@10 |
|---|---|---|---|---|
| nomic-embed-text (versión anterior) | 7% | 33% | 40% | 0,19 |
| qwen3-embedding:0.6b + tests penalizados | 70% | 93% | 100% | 0,80 |
| + ruta del archivo en el texto embebido, sin firmas `@overload` (actual) | 73% | 93% | 100% | 0,83 |

Con el contexto adaptativo, el fragmento que responde la pregunta llega al
modelo en el 97% de los casos (8 fragmentos en promedio).

Resultados por repositorio con la configuración actual:

| Repositorio | Preguntas | Hit@1 | Hit@5 | Hit@10 | MRR@10 | En contexto |
|---|---|---|---|---|---|---|
| psf/requests (Python) | 30 | 73% | 93% | 100% | 0,83 | 97% |
| microservices-demo (5 lenguajes) | 22 | 41% | 95% | 95% | 0,63 | 95% |
| psf/requests, con identificadores | 15 | 67% | 93% | 100% | 0,79 | 100% |
| microservices-demo, con identificadores | 10 | 100% | 100% | 100% | 1,00 | 100% |

En el repositorio políglota, el mayor avance vino de embeber cada fragmento
junto con la ruta de su archivo (Hit@5 de 77% a 95%): el código solo no dice
a qué servicio pertenece, y el generador de carga (`locustfile.py`), que
repite los nombres de las operaciones de la tienda, le ganaba al servicio
que las implementa. Lo que se entrega al modelo sigue siendo solo el código.

Cuando la pregunta nombra un identificador, la búsqueda es híbrida: además
de la similitud semántica, suben los fragmentos que se llaman así o que lo
usan (si la pregunta es "¿dónde se usa…?", pesa más quien lo usa que la
definición). En las preguntas con identificadores de psf/requests, Hit@5
pasó de 80% a 93% y el fragmento correcto llega al modelo en el 100% de los
casos (antes 87%); los demás conjuntos no cambiaron.

Los conjuntos son chicos, así que los números sirven para comparar
configuraciones, no como garantía general.

Si cambias `embedding_model` en `config.yaml`, el próximo `beacon sync`
reconstruye el índice solo (los vectores de modelos distintos no son
comparables), y mientras tanto las consultas responden con un aviso claro.

## Calidad de las respuestas (medida)

`tools/eval_answers.py` llama al modelo real y revisa cada respuesta con
criterios verificables, sin juicio humano: si menciona los datos clave de la
pregunta, si cita el archivo donde está la respuesta, si ante una pregunta
sobre algo que no existe en el repositorio dice que no hay información (sin
agregar código de relleno), si escribe identificadores que no aparecen en
el contexto entregado (posible invención) y si responde en español.

```bash
python tools/eval_answers.py <project_id> eval/answers-requests.yaml
```

Sobre psf/requests (12 preguntas respondibles y 4 que no lo son), con
llama3:8b:

| Configuración | Contenido | Cita el archivo | Ambos | Falso rechazo | Rechazo correcto |
|---|---|---|---|---|---|
| Prompt anterior | 75% | 33% | 33% | 8% | 100% |
| Reglas de cita y de rechazo ajustadas | 75% | 92% | 67% | 0% | 100% |
| + fragmentos rotulados por ruta y regla de premisas falsas | 100% | 83-92% | 83-92% | 0% | 100% |
| + búsqueda híbrida (configuración actual) | 92% | 83% | 75% | 0% | 100% |

Qué mostró cada medición:

- El modelo citaba "el fragmento 3" en vez del archivo (el usuario no ve esa
  numeración) y a veces cerraba una respuesta correcta con la frase de "no
  encontré información". Pedirle otra cosa en el prompt ayudó, pero lo
  resolvió quitar el número: cada fragmento llega rotulado con su ruta y
  sus líneas, que es lo que el modelo copia al citar.
- Ante una pregunta con una premisa falsa ("¿qué modelo de aprendizaje
  automático usa requests para predecir la codificación?") el modelo la
  aceptaba y completaba con lo que sabía de una biblioteca externa. Una
  regla explícita para ese caso lo corrigió.
- Con la temperatura por defecto, la misma pregunta daba resultados
  distintos entre corridas (el contenido correcto variaba entre 67% y 92%),
  lo que impedía comparar cambios. Las respuestas se generan con
  temperatura 0,2 y semilla fija; aun así, entre dos corridas iguales una
  respuesta puede cambiar, de ahí el rango en la tabla. Con 12 preguntas,
  cada una vale 8 puntos: diferencias de una pregunta entre configuraciones
  (como la última fila) están dentro de esa variación.

Como control, el conjunto `eval/answers-microservices-demo.yaml` se
escribió después de estos ajustes y no se usó para ninguno. Ahí los
resultados son más bajos: contenido 80%, cita 60%, ambos 60%, sin rechazos
indebidos y con 100% de rechazos correctos (con la configuración actual). Las respuestas aciertan en lo
principal pero son breves y a menudo no citan el archivo; es un límite del
modelo de 8B parámetros que conviene conocer (la interfaz muestra igual las
fuentes de cada respuesta). Una respuesta tarda unos 5 segundos con GPU de
8 GB.

## Pruebas de estrés (medidas)

`tools/stress_test.py` genera un repositorio sintético de miles de archivos
y mide indexado, búsqueda, consultas simultáneas contra el servidor real y
consultas mientras otra consola reconstruye el índice:

```bash
python tools/stress_test.py generar ../stress-repo --archivos 2000
python tools/stress_test.py medir ../stress-repo --concurrencia 1 4 8
```

Con 2.000 archivos en Python, Go y JavaScript, GPU de 8 GB:

| Medición | Resultado |
|---|---|
| Indexado completo (18.012 fragmentos) | 6 min (50 fragmentos/s), 260 MB de memoria, 122 MB en disco |
| Sincronizar tras cambiar 20 archivos | 5,6 s |
| Búsqueda sin LLM (p95) | 35 ms |
| Consultas completas simultáneas: 1 / 4 / 8 | mediana 3,2 / 6,4 / 12,1 s; máximo 24,9 s; sin errores |
| Consultas durante una reconstrucción completa | 128 de 128 respondidas |

Con varias consultas a la vez la espera crece casi en proporción: Ollama
genera las respuestas de a una en la GPU. La primera versión de esta prueba
encontró dos fallas, ya corregidas: consultas con error 500 mientras otra
consola corría `sync --full`, y proyectos a medio borrar en Windows cuando
el servidor tenía el índice abierto. El
informe completo de la última corrida queda en `eval/stress_report.json`.
