"""
tests/test_panel_survival.py — the panel of providers, and the worker that
answers it.

WHAT IS BEING PROVED
    1. The panel hands the worker a callback that survives the panel. A
       signal of a destroyed widget is an access violation — it takes the
       interpreter down with it, not just the test.
    2. The callback is the safe one, and not the bare signal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from PyQt6 import sip  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    import os
    previous = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    if previous is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = previous


def test_the_worker_gets_the_safe_callback(monkeypatch, qt_app):
    """Not the bare signal: the panel may be gone when the answer lands."""
    from gui.providers_panel import ProvidersOverlay
    from providers import registry
    from providers import tts_piper

    given = {}

    def _prefetch(on_ready=None):
        given["callback"] = on_ready

    monkeypatch.setattr(tts_piper, "prefetch", _prefetch)
    monkeypatch.setattr(registry, "discover",
                        lambda kind: [{"_module": "tts_piper"}] if kind == "tts" else [])

    panel = ProvidersOverlay()
    try:
        callback = given.get("callback")
        assert callback is not None, "the prefetch was not asked to warm up"
        assert getattr(callback, "__func__", None) is ProvidersOverlay._safe_live_ready
    finally:
        sip.delete(panel)


def test_a_destroyed_panel_does_not_take_the_interpreter_down(monkeypatch, qt_app):
    """The crash that was measured: the worker answered a panel that had
    already been destroyed, and the answer was an access violation."""
    from gui.providers_panel import ProvidersOverlay
    from providers import registry

    monkeypatch.setattr(registry, "discover", lambda kind: [])

    panel = ProvidersOverlay()
    sip.delete(panel)
    assert sip.isdeleted(panel) is True

    # This is the line that used to take the whole interpreter with it.
    panel._safe_live_ready()
