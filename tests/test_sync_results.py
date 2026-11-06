"""The CLI and the API show the sync results with the same keys as before they were typed."""

from core.engine.doc_generator import DocSyncResult
from core.engine.indexer import IndexSyncResult


def test_index_result_dict():
    assert IndexSyncResult(changed=False, summary="0 cambios").to_dict() == {"status": "sin_cambios", "detalle": "0 cambios"}
    rebuilt = IndexSyncResult(changed=True, summary="s", files_indexed=2, chunks_inserted=5, chunks_purged=1,
                              model_change="a → b").to_dict()
    assert rebuilt == {"status": "ok", "detalle": "s", "archivos_indexados": 2, "chunks_insertados": 5,
                       "chunks_purgados": 1, "modelo_embeddings": "a → b (índice reconstruido)"}


def test_doc_result_dict():
    assert DocSyncResult(changed=False, summary="s").to_dict() == {"status": "sin_cambios", "detalle": "s"}
    assert DocSyncResult(changed=False, summary="s", overview={"carpetas": 1}).to_dict()["resumen_proyecto"] == {"carpetas": 1}
    done = DocSyncResult(changed=True, summary="s", generated=3, deleted=1, skipped=["x.py"], overview={}).to_dict()
    assert done == {"status": "ok", "detalle": "s", "docs_generados": 3, "docs_eliminados": 1,
                    "sin_chunks_indexados": ["x.py"], "resumen_proyecto": {}}
