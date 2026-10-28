[English](../en/study-mode.md) | **Español**

# Modo estudio

`beacon study` abre un asistente para participar en el estudio de Beacon: se elige un repositorio público,
se hacen preguntas libres y se valora cada respuesta. Todo ocurre en el equipo de quien participa; el único
resultado es un archivo que esa persona revisa y, si quiere, envía con un botón o por su cuenta. Nada sale del equipo sin que lo pida.

## Para participantes

Requisitos: Python 3.10, 3.11 o 3.12 (el 3.13 o superior todavía no funciona), Git, Node 20 o superior (para compilar la interfaz una vez) y
[Ollama](https://ollama.com) con los modelos que usa Beacon (`ollama pull llama3:8b` y
`ollama pull qwen3-embedding:0.6b`). Una GPU acelera mucho las respuestas, pero no es obligatoria.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
python tools/install.py    # entorno, dependencias, interfaz web y modelos de Ollama (Linux/macOS: python3)
.\beacon                   # abre una consola con el entorno activo (Linux/macOS: ./beacon)
beacon study
```

Si Python 3.13 o superior es el predeterminado, ejecuta el instalador con una versión compatible (por ejemplo `py -3.11 tools/install.py`).

El asistente se abre en el navegador (`http://127.0.0.1:8000/study`) y guía cinco pasos:

1. **Participación.** Antes de aceptar, el asistente revisa que todo lo necesario esté en orden: Ollama responde, los dos modelos están descargados (si falta alguno, indica el `ollama pull` exacto), hay GPU y espacio en disco, y hay conexión para clonar. Si algo esencial falta, no deja comenzar hasta que lo resuelvas y pulses «Reintentar». La participación se acepta explícitamente; sin eso no se guarda nada.
2. **Equipo.** Se muestra tal cual lo que se guardaría (CPU, RAM, GPU y VRAM, sistema operativo, versión de
   Ollama y modelos). Es opcional y se puede omitir.
3. **Repositorio.** Una dirección https de un repositorio público (hasta 3000 archivos de código). Beacon lo clona, lo indexa y carga el modelo de respuestas en memoria, para que el tiempo de la primera pregunta no incluya esa carga.
4. **Preguntas.** Mínimo 3, sobre cosas que de verdad se quieran saber del código. Cada respuesta se valora
   (correcta, en parte, incorrecta, no sé). El tiempo lo mide Beacon, no el navegador. Una respuesta tiene un tope de longitud y, si el modelo tarda más de 3 minutos, se detiene sola con un aviso; también puedes pulsar «Cancelar» mientras espera.
5. **Cierre.** Experiencia, identidad opcional y revisión del archivo. Al generarlo, `beacon-estudio-resultados-<id>.json` (y `beacon-estudio-identidad-<id>.json`, si se dio) quedan en la carpeta raíz del proyecto; también se pueden descargar o copiar. Si el estudio tiene un destino de envío configurado, aparece el botón **Enviar mis resultados**: manda exactamente el contenido que se ve en pantalla (y la identidad solo si se marca la casilla) y muestra un código de recibo. Si no hay destino, o el envío falla, se descarga el archivo y se envía por el canal de la invitación.

### Dónde queda el repositorio clonado

El repositorio que eliges se clona en `data/estudio/` dentro de la carpeta de Beacon (el asistente muestra la ruta exacta). Se borra solo al descartar la sesión («Descartar todo y salir», que además detiene el servidor) o al responder que sí cuando cierras `beacon study` con Ctrl+C. Si prefieres borrarlo a mano para recuperar espacio, esa carpeta es la única que ocupa lugar. Para ver y limpiar el espacio de otros proyectos: `beacon space` y `beacon clean`.

### Qué contiene y qué no el archivo

Contiene las preguntas, las respuestas, los tiempos, las valoraciones, el repositorio elegido (dirección y
commit) y, si se autorizó, los datos del equipo. **No** contiene código fuente, rutas del equipo, nombre de
usuario, nombre del equipo ni IP. La identidad (perfil de GitHub o presentación) va en un archivo aparte y
es opcional. Se puede abandonar en cualquier momento con «Descartar todo y salir», que borra la sesión.

`beacon study --new` descarta una sesión anterior y empieza de cero; `--port` cambia el puerto y `--no-browser` evita que se abra el navegador.

## Para quien recibe los resultados

Hay tres vías, de la más cómoda a la más simple. El participante elige: si la primera no está configurada o falla, usa las otras.

1. **Botón «Enviar mis resultados» (recomendado).** El asistente envía el archivo a un colector propio y guarda cada envío como un archivo JSON en una carpeta privada de Google Drive. No requiere cuenta ni formulario para quien participa. Configuración, una sola vez:
   1. Entra a [script.google.com](https://script.google.com), crea un proyecto nuevo y pega el contenido de `tools/study_collector.gs`.
   2. Crea una carpeta en Drive para los resultados y copia su identificador (la parte final de su dirección). En el proyecto, *Configuración del proyecto → Propiedades de la secuencia de comandos*, agrega `FOLDER_ID` con ese valor.
   3. *Implementar → Nueva implementación → Aplicación web*: ejecutar como **tú** y acceso para **cualquier persona**. Autoriza el acceso a Drive y copia la dirección que termina en `/exec` (al abrirla en el navegador debe responder `{"ok":true,"servicio":"beacon-estudio"}`).
   4. Esa dirección va en `DEFAULT_SUBMIT_URL` de `src/core/study/submit.py` (así viaja en el repositorio y los participantes no configuran nada). Para probar otra, sin editar el código: variable de entorno `BEACON_STUDY_SUBMIT_URL`.
   El colector solo acepta archivos del estudio (esquema 1, id corto, consentimiento y al menos 3 preguntas, hasta 2 MB), no sobrescribe un envío anterior y no guarda nada más. Cualquiera que conozca la dirección podría enviarle datos, pero no leer los ya recibidos; los archivos se validan al analizarlos.
2. **Formulario sin inicio de sesión** (por ejemplo Google Forms o Tally) con un campo de párrafo donde el participante pega el contenido del botón «Copiar resultados», y otro opcional para el contacto. Se exporta a CSV.
3. **Por el canal de la invitación**: el participante descarga el archivo y lo envía a mano.

Los archivos se analizan localmente, sea desde la carpeta de Drive descargada o desde el CSV:

```bash
python tools/study_analysis.py carpeta_con_json/
python tools/study_analysis.py --csv respuestas.csv --columna "Resultados"
```

El script valida cada archivo con las mismas reglas del modo estudio, descarta los inválidos y duplicados y
resume tiempos por VRAM, porcentaje de respuestas correctas y utilidad de las fuentes. Los archivos con
nombre «identidad» nunca se leen.
