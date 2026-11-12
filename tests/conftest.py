"""Isolates the tests from the developer's real configuration.

config/config.yaml is not versioned, so it does not exist in a clean clone or in CI. Before any test
imports core.config, CONFIG_PATH and CREDENTIALS_PATH point to temporary copies created from
config.example.yaml: the tests never read or write the real configuration.
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
os.environ["BEACON_MONITOR_SECONDS"] = "0"  # no periodic calls to a real model server from the tests


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_collector(monkeypatch):
    """The real study collector address ships in the code: no test may send to it."""
    from core.study import submit
    monkeypatch.setattr(submit, "DEFAULT_SUBMIT_URL", "")
