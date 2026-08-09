"""Cliente ChromaDB compartido. Centraliza la configuración para no repetirla
en cada módulo que necesita persistencia."""

import chromadb
from chromadb.config import Settings


def get_chroma_client(path: str) -> chromadb.PersistentClient:
    # anonymized_telemetry=False: evita el error de compatibilidad
    # ChromaDB/posthog en consola (capture() takes 1 positional argument...),
    # y de paso no manda ningún dato de uso a servidores externos — acorde
    # al pilar de privacidad 100% local del proyecto.
    return chromadb.PersistentClient(
        path=path,
        settings=Settings(anonymized_telemetry=False),
    )
