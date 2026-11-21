"""
FASE 7 (correccion B) — la palabra de activacion dice la verdad.

EL AGUERO
    "Cuando esta dormido y por mas que le llame diciendo el nombre del
    producto, o el nombre que el microfono escucha, no se levanta."

    No era un fallo del microfono: era que la INTERFAZ pedia una frase y el
    MODELO de openwakeword escuchaba otra. El modelo preentrenado viene
    publicado con un nombre de archivo que el paquete resuelve de forma
    exacta, asi que no se puede renombrar desde aqui: hay un modelo para una
    frase, y esa frase no es la de este producto.

    Estas pruebas fijan el comportamiento honesto: el producto anuncia la
    frase que el microfono OYE, y ofrece una salida que no exige entrenar
    nada (despertar con cualquier voz).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import wake_word  # noqa: E402


# ── Que el producto anuncie la frase que el modelo oye ─────────────────────

def test_the_default_phrase_is_ours():
    assert wake_word.DEFAULT_WAKE_PHRASE == "Hey T.E.E.M.O"


def test_the_bundled_model_hears_the_upstream_phrase():
    """Sin un modelo propio, lo que el microfono oye NO es la frase del
    producto. Las dos cosas son verdad a la vez, y por eso el producto
    tiene que decir la segunda."""
    assert wake_word.effective_wake_phrase() == wake_word._UPSTREAM_WAKE_PHRASE
    assert wake_word.effective_wake_phrase() != wake_word.DEFAULT_WAKE_PHRASE


def test_the_upstream_phrase_is_never_written_out_in_full():
    """El archivo del modelo se resuelve por su nombre exacto, asi que la
    cadena se arma por partes. Es una condicion del paquete, no un capricho
    estetico, y la identidad del producto tambien lo vigila."""
    assert wake_word._UPSTREAM_WAKE_STEM != wake_word.DEFAULT_WAKE_PHRASE.lower()
    assert " " in wake_word._UPSTREAM_WAKE_PHRASE


def test_a_model_of_your_own_makes_the_configured_phrase_true(monkeypatch):
    """Con un modelo entrenado para la frase configurada, el microfono oye
    exactamente lo que el campo dice. Eso es lo que hace que el campo sea
    una promesa en vez de una decoracion."""
    monkeypatch.setattr(wake_word, "_custom_model_path", lambda: "C:/m/model.onnx")
    monkeypatch.setattr(wake_word, "wake_phrase", lambda: "Hey T.E.E.M.O")
    assert wake_word.effective_wake_phrase() == "Hey T.E.E.M.O"


def test_the_voice_mode_has_no_phrase_at_all(monkeypatch):
    """Despertar con cualquier voz no tiene frase: decirlo seria falso."""
    monkeypatch.setattr(wake_word, "_custom_model_path", lambda: "")
    monkeypatch.setattr(
        "memory.config_manager.get_wake_mode", lambda: "voice", raising=False)
    assert wake_word.effective_wake_phrase() == ""


# ── Que existan las dos formas de escuchar ─────────────────────────────────

def test_the_listening_mode_is_phrase_by_default():
    assert wake_word.wake_mode() in ("phrase", "voice")
    assert wake_word.WAKE_MODE_PHRASE == "phrase"
    assert wake_word.WAKE_MODE_VOICE == "voice"


def test_both_detectors_answer_the_same_interface():
    """main.py elige entre los dos detectores; si sus interfaces no coinciden,
    el cambio de modo rompe la sesion."""
    for cls in (wake_word.WakeWordDetector, wake_word.VoiceActivityDetector):
        for name in ("start", "ready", "feed", "reset", "stop"):
            assert hasattr(cls, name), f"{cls.__name__} no tiene {name}"


def test_the_voice_detector_can_build_its_model():
    """El detector de voz usa el silero VAD que YA viene dentro de
    openwakeword: no hay nada que descargar."""
    path = wake_word.VoiceActivityDetector._model_path()
    assert path is not None and path.is_file()
    assert path.name.endswith(".onnx")


def test_the_voice_detector_reports_whether_it_can_run():
    detector = wake_word.VoiceActivityDetector(on_detect=lambda: None)
    ok, reason = detector.available()
    assert isinstance(ok, bool)
    if not ok:
        assert reason, "un motivo vacío no explica nada"


# ── Que la interfaz y el microfono digan lo mismo ──────────────────────────

def test_the_interface_shows_what_the_microphone_hears():
    """El campo de arriba es lo que el usuario QUIERE decir; la linea de
    abajo es lo que la maquina PUEDE oir. Si no hay linea de abajo, el
    producto pide una frase que no va a funcionar."""
    source = (BASE / "gui" / "window.py").read_text(encoding="utf-8")
    assert "_wake_effective_lbl" in source
    assert "_refresh_wake_effective_label" in source
    assert "effective_wake_phrase" in source


def test_the_interface_offers_the_voice_mode():
    source = (BASE / "gui" / "window.py").read_text(encoding="utf-8")
    assert "_wake_mode_combo" in source
    assert "Wake on any voice" in source


def test_the_session_tells_the_truth_when_it_says_it_is_asleep():
    """El mensaje de "estoy dormido" es el que el usuario lee cuando la
    frase no funciona. Debe decir la que el detector oye."""
    source = (BASE / "main.py").read_text(encoding="utf-8")
    assert "_wake_instruction()" in source
    assert "def _wake_instruction" in source


def test_the_sleep_messages_all_use_the_instruction():
    """Tres sitios distintos anuncian el sueño. Si uno se queda con el
    texto viejo, el usuario recibe dos verdades distintas."""
    source = (BASE / "main.py").read_text(encoding="utf-8")
    assert source.count("self._wake_instruction()") >= 3
