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


def test_bare_command_shows_a_friendly_summary_and_version_flag():
    out = runner.invoke(cli_module.app, [])
    assert out.exit_code == 0 and "Happy hacking" in out.output and "beacon sync" in out.output
    out = runner.invoke(cli_module.app, ["--version"])
    assert out.exit_code == 0 and out.output.startswith("Beacon ")


def test_console_language_follows_the_os_locale_with_english_as_default(monkeypatch):
    from core import cli_lang
    monkeypatch.delenv("BEACON_LANG", raising=False)
    for value, expected in (("es_CL.UTF-8", "es"), ("es-MX", "es"), ("ES_ES", "es"), ("en_US.UTF-8", "en"), ("de_DE", "en"), ("", "en")):
        monkeypatch.setattr(cli_lang, "_os_language", lambda v=value: v)
        assert cli_lang.detect_language() == expected, value
    monkeypatch.setenv("BEACON_LANG", "es")  # el forzado manda sobre el sistema
    monkeypatch.setattr(cli_lang, "_os_language", lambda: "en_US")
    assert cli_lang.detect_language() == "es"
