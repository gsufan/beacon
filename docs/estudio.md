# Modo estudio

`beacon study` abre un asistente para participar en el estudio de Beacon: se elige un repositorio público,
se hacen preguntas libres y se valora cada respuesta. Todo ocurre en el equipo de quien participa; el único
resultado es un archivo que esa persona revisa y envía por su cuenta.

## Para participantes

Requisitos: Python 3.10, 3.11 o 3.12 (el 3.13 o superior todavía no funciona), Git, Node 20 o superior (para compilar la interfaz una vez) y
[Ollama](https://ollama.com) con los modelos que usa Beacon (`ollama pull llama3:8b` y
`ollama pull qwen3-embedding:0.6b`). Una GPU acelera mucho las respuestas, pero no es obligatoria.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cd frontend && npm install && npm run build && cd ..
beacon study
```

El asistente se abre en el navegador (`http://127.0.0.1:8000/study`) y guía cinco pasos:

1. **Participación.** Se acepta explícitamente; sin eso no se guarda nada.
2. **Equipo.** Se muestra tal cual lo que se guardaría (CPU, RAM, GPU y VRAM, sistema operativo, versión de
   Ollama y modelos). Es opcional y se puede omitir.
3. **Repositorio.** Una dirección https de un repositorio público (hasta 3000 archivos de código).
4. **Preguntas.** Mínimo 3, sobre cosas que de verdad se quieran saber del código. Cada respuesta se valora
   (correcta, en parte, incorrecta, no sé). El tiempo lo mide Beacon, no el navegador.
5. **Cierre.** Experiencia, identidad opcional y revisión del archivo. Al generarlo, `beacon-estudio-resultados-<id>.json` (y `beacon-estudio-identidad-<id>.json`, si se dio) quedan en la carpeta raíz del proyecto; también se pueden descargar o copiar.

### Qué contiene y qué no el archivo

Contiene las preguntas, las respuestas, los tiempos, las valoraciones, el repositorio elegido (dirección y
commit) y, si se autorizó, los datos del equipo. **No** contiene código fuente, rutas del equipo, nombre de
usuario, nombre del equipo ni IP. La identidad (perfil de GitHub o presentación) va en un archivo aparte y
es opcional. Se puede abandonar en cualquier momento con «Descartar todo y salir», que borra la sesión.

`beacon study --nueva` descarta una sesión anterior y empieza de cero; `--port` cambia el puerto.

## Para quien recibe los resultados

No se necesita un servidor propio. Recomendado: un **formulario sin inicio de sesión** (por ejemplo Google
Forms o Tally) con un campo de párrafo donde cada participante pega el contenido del botón «Copiar
resultados», y otro opcional para el contacto. El formulario se exporta a CSV y se analiza localmente:

```bash
python tools/study_analysis.py --csv respuestas.csv --columna "Resultados"
python tools/study_analysis.py carpeta_con_json/
```

El script valida cada archivo con las mismas reglas del modo estudio, descarta los inválidos y duplicados y
resume tiempos por VRAM, porcentaje de respuestas correctas y utilidad de las fuentes. Los archivos con
nombre «identidad» nunca se leen.
