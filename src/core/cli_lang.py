"""Idioma de la consola: español si el sistema operativo está en español (cualquier variante), inglés en cualquier
otro caso. `BEACON_LANG=es|en` fuerza uno. Solo afecta a los mensajes y la ayuda de la consola."""

import locale
import os
import sys


def _os_language() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                return buffer.value
        except Exception:  # noqa: BLE001
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return locale.getlocale()[0] or ""
    except Exception:  # noqa: BLE001
        return ""


def detect_language() -> str:
    forced = os.environ.get("BEACON_LANG", "").strip().lower()
    if forced in ("es", "en"):
        return forced
    return "es" if _os_language().lower().startswith("es") else "en"


LANG = detect_language()


def tr(es: str, en: str) -> str:
    """El texto en el idioma de la consola."""
    return es if LANG == "es" else en
