# Guía de defensa — preguntas probables

Complementa a [ARQUITECTURA.md](ARQUITECTURA.md). Pensado para repasar
antes de la defensa de diciembre 2026, no como transcripción a leer.

## "¿Por qué local y no una API como OpenAI?"

Costo cero de operación, y el código del cliente nunca sale de su red —
relevante para empresas que no pueden mandar su código propietario a un
tercero. La contrapartida es calidad: modelos locales chicos (`llama3:8b`)
son menos confiables siguiendo instrucciones que GPT-4/Claude — por eso
existen las reglas anti-alucinación explícitas y el post-procesamiento de
`text_sanitize.py`.

## "¿Cómo evitás que el modelo alucine?"

Tres capas: (1) el prompt de sistema prohíbe explícitamente responder algo
que no esté en el contexto recuperado, y obliga a decir "no puedo
confirmar" si una función llamada no está incluida; (2) el contexto se
arma con evidencia trazable — cada fuente citada tiene archivo y líneas
exactas; (3) la expansión por grafo de llamadas reduce el caso más común de
alucinación (el modelo "adivinando" qué hace una función que no vio).

## "¿Por qué chunking por AST y no por líneas/tokens?"

Dividir por líneas fijas corta funciones a la mitad y mezcla contexto no
relacionado en el mismo chunk, lo que degrada tanto el embedding como la
cita de fuentes. Tree-sitter da la estructura real del lenguaje —
funciones, clases, métodos como unidades completas — así el chunk
recuperado siempre es una unidad de código con sentido propio.

## "¿Qué pasa si el repo es gigante?"

El indexado es incremental (compara contra el último commit indexado vía
git diff), así que solo se reprocesa lo que cambió. El watcher automático
opcional hace esto transparente: sync periódico sin intervención manual.

## "¿Cómo se aísla un proyecto de otro?" (multi-tenant local)

Cada proyecto registrado tiene su propia carpeta `data/<project_id>/` con
su propio índice ChromaDB y su propia documentación generada — no
comparten colecciones. Esto permite que una empresa registre varios repos
sin que se mezclen los resultados de búsqueda.

## "¿Por qué el explorador de carpetas en la UI y no un input de archivo?"

Porque el indexador siempre lee del disco donde corre el **proceso
backend**, nunca del navegador. Si Beacon está desplegado en un servidor
central, un `<input type="file">` del navegador mostraría el filesystem
del cliente, que es irrelevante — el repo tiene que ser accesible por el
proceso que lo va a leer. Por eso hay dos rutas de registro: explorar el
filesystem del servidor (mismo caso si corrés Beacon local), o clonar por
URL (para cuando el repo vive en otro lugar).

## "¿Cómo garantizás que el token de un repo privado no se filtra?"

Se guarda en `config/credentials.yaml`, un archivo separado y
**gitignoreado** — nunca en `config.yaml`, que sí está versionado en git.
Es una decisión de diseño explícita: mezclar secretos con configuración
versionada es un vector de leak real.

## "¿Qué decisiones técnicas te costó más tiempo detectar?"

Dos bugs reales documentados: (1) `nomic-embed-text` necesita los
prefijos `search_document:`/`search_query:` en cada texto — sin eso la
recuperación semántica funciona pero es notablemente peor, y no es obvio
desde la documentación de Ollama; (2) modelos chicos como `llama3:8b` a
veces "confirman" instrucciones del sistema (como el idioma pedido) como
texto visible en la respuesta en vez de solo seguirlas — se resolvió con
una heurística de post-procesamiento (`strip_preamble`) más que confiando
en que el prompt alcance.

## "¿Qué le sacarías o le agregarías si tuvieras más tiempo?"

Sacado deliberadamente: panel visual de grafo de llamadas (se probó, no
aportaba valor claro frente a su complejidad para el usuario). Pendiente
y de baja prioridad: empaquetado multiplataforma/Docker (Fase D) — el
proyecto corre hoy vía `pip install -e .` + `beacon serve`, suficiente
para la defensa pero no para distribución a usuarios no técnicos.

## Comandos para hacer una demo en vivo

```bash
beacon doctor                              # confirma que todo está sano
beacon ask microservices-demo "¿qué hace CartService?"
beacon serve                               # UI en http://127.0.0.1:8000
```
