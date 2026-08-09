# Guía de migración — Fase A

Tienes `v1-flat-structure` como red de seguridad (`git checkout v1-flat-structure`
si algo sale mal). Estos pasos son seguros de aplicar sobre tu proyecto actual.

## 1. Copia la estructura nueva

Descomprime este zip **dentro** de la raíz de tu proyecto
(`tesis-rag-beacon/`), sobrescribiendo lo que corresponda. Vas a
terminar con: `config/`, `src/`, `tools/`, `tests/`, `pyproject.toml`,
`requirements.txt` (nuevo, reemplaza al tuyo).

## 2. Borra los archivos viejos ya migrados

Estos quedan reemplazados por `src/core/engine/*` — bórralos de la raíz:
```
chunker.py  git_watcher.py  indexer.py  doc_generator.py  rag_engine.py  api.py
diagnose_query.py  migrate_to_cosine.py  purge_generated.py  rank_check.py
```
(sus reemplazos ya están en `src/core/engine/` y `tools/`)

## 3. Mueve tus tests

```
mv test_setup.py test_chunker.py tests/
```

## 4. Instala el proyecto como paquete editable

```bash
pip install -r requirements.txt
pip install -e .
```
Esto habilita el comando `beacon` directamente (en vez de `python -m core.cli`).

## 5. Ajusta config/config.yaml

Ya viene con `microservices-demo` registrado apuntando a `../microservices-demo`
(mismo lugar donde ya lo tenías clonado) — confirma que la ruta relativa
coincide con dónde vive respecto a tu proyecto. Ajusta también el modelo si
usas otro distinto a `llama3:8b`.

## 6. Reindexa (una vez más, la última por un buen rato)

**Por qué hace falta de nuevo:** el índice ahora vive en
`data/<project_id>/chroma_db/` en vez de `data/chroma_db/` a secas — es
un cambio de ubicación, no hay forma de migrarlo sin reprocesar.

```bash
beacon sync microservices-demo
beacon docs microservices-demo
```

Nota que `sync` ya incluye la purga de código generado automáticamente —
ya no hace falta el paso manual de `purge_generated.py --confirm` de antes.

## 7. Probar

```bash
beacon projects
beacon ask microservices-demo "¿Cómo calcula el shippingservice el costo de envío?"
beacon serve
```

El último levanta la API + UI en `http://127.0.0.1:8000`.

## Qué cambió de fondo (por si algo no encaja)

- Los scripts de `tools/` ahora reciben `<project_id>` como primer argumento
  en vez de una ruta de repo — leen la ruta real desde `config.yaml`.
- `.env` / `.env.example` ya no se usan — todo vive en `config/config.yaml`.
  Puedes borrarlos cuando confirmes que todo funciona.
