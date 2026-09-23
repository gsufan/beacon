"""Regresión: typer==0.15.1 crasheaba al pedir --help con click>=8.2
instalado (Parameter.make_metavar() missing 'ctx'). Se fijó a 0.15.4 en
requirements.txt — estos tests evitan que la incompatibilidad vuelva a
colarse sin que nadie lo note."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner  # noqa: E402

import core.cli as cli_module  # noqa: E402

runner = CliRunner()

COMMANDS = ["projects", "add", "edit", "remove", "sync", "docs", "ask", "export", "import", "status", "doctor", "serve"]


def test_top_level_help():
    result = runner.invoke(cli_module.app, ["--help"])
    assert result.exit_code == 0
    for cmd in COMMANDS:
        assert cmd in result.output


def test_each_command_help():
    for cmd in COMMANDS:
        result = runner.invoke(cli_module.app, [cmd, "--help"])
        assert result.exit_code == 0, f"'{cmd} --help' falló:\n{result.output}"


def test_configure_beacon_logging_formats_records_with_timestamp_and_level(capsys):
    import logging

    beacon_logger = logging.getLogger("beacon")
    beacon_logger.handlers.clear()  # no acumular handlers si el test corre más de una vez

    cli_module._configure_beacon_logging()
    logging.getLogger("beacon.api").warning("fallo de prueba en el watcher")

    err = capsys.readouterr().err
    assert "fallo de prueba en el watcher" in err
    assert "WARNING" in err
    assert "beacon.api" in err

    beacon_logger.handlers.clear()  # limpiar para no afectar otros tests
