"""Aísla los tests de la configuración real del desarrollador.

config/config.yaml no está versionado (cada instalación tiene sus propios
proyectos), así que en un clon limpio o en la CI no existe. Antes de que
cualquier test importe core.config, se apunta CONFIG_PATH y CREDENTIALS_PATH
a copias temporales creadas desde config.example.yaml: los tests nunca leen
ni escriben la configuración real de quien los ejecuta.
"""

import os
import shutil
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TMP = Path(tempfile.mkdtemp(prefix="beacon-tests-"))
shutil.copyfile(_ROOT / "config" / "config.example.yaml", _TMP / "config.yaml")

os.environ["DEUDA_TECNICA_CONFIG"] = str(_TMP / "config.yaml")
os.environ["DEUDA_TECNICA_CREDENTIALS"] = str(_TMP / "credentials.yaml")
