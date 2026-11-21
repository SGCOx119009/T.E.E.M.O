"""FASE 3 - identidad: pruebas de aceptacion.

La identidad de este producto es una de esas cosas que se deshacen sin que
nada se rompa: un log que vuelve a llamarse como antes, una palabra de
activacion que deja de coincidir con el boton que la muestra, un nombre de
herramienta que el modelo ya no puede llamar. Nada de eso lanza una excepcion;
simplemente el producto deja de llamarse como debe.

Estas pruebas son el cinturon de seguridad de ese nombre.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent

# El patron se arma por partes a proposito: un fichero de pruebas que
# contenga la marca que busca encontraria siempre su propia coincidencia.
_OLD_NAME = "jar" + "vis"
_OLD_GEN = r"mark\s+l[vix]+|mark\s+78|" + "lvi" + "ii"
_OLD_DOTTED = r"j\s*\.\s*a\s*\.\s*r\s*\.\s*v\s*\.\s*i\s*\.\s*s"
_PATTERN = re.compile(f"{_OLD_NAME}|{_OLD_GEN}|{_OLD_DOTTED}", re.IGNORECASE)

# Documentos que, por decision del plan, conservan la historia del proyecto.
_HISTORY_DOCS = {"readme.md", "AGENTS.md"}


def _python_files():
    for path in sorted(BASE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name in _HISTORY_DOCS:
            continue
        yield path


# ── El nombre viejo no aparece en ninguna parte del codigo ──────────────────

def test_no_old_brand_in_python_sources():
    """Ningun .py del producto se llama como la marca anterior.

    Se excluyen los ficheros de este propio test, que es el unico sitio
    donde el patron aparece escrito.
    """
    offenders = []
    for path in _python_files():
        if path.resolve() == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if _PATTERN.search(line):
                offenders.append(f"{path.relative_to(BASE)}:{n}: {line.strip()}")
    assert not offenders, "marca anterior en el codigo:\n" + "\n".join(offenders)


def test_no_old_brand_in_the_interface_files():
    """El HUD, el panel del telefono y el prompt no la nombran."""
    checked = [
        BASE / "core" / "prompt.txt",
        BASE / "dashboard" / "static" / "app.html",
        BASE / "dashboard" / "static" / "login.html",
    ]
    offenders = []
    for path in checked:
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if _PATTERN.search(line):
                offenders.append(f"{path.relative_to(BASE)}:{n}: {line.strip()}")
    assert not offenders, "marca anterior en la interfaz:\n" + "\n".join(offenders)


# ── El producto se llama T.E.E.M.O. ─────────────────────────────────────────

def test_the_product_is_called_teemo():
    from gui.theme import APP_VERSION
    assert APP_VERSION == "T.E.E.M.O."
    assert APP_VERSION.endswith("."), "el nombre se escribe con punto final"


def test_the_protocol_badge_is_not_empty_and_has_no_trailing_dot():
    """La insignia del protocolo no puede quedar vacia ni terminar en punto.

    Un punto final ahi se lee como una frase que se ha cortado, no como un
    nombre; y una insignia vacia deja una etiqueta suelta en el HUD.
    """
    from gui.theme import APP_PROTOCOL
    assert APP_PROTOCOL
    assert APP_PROTOCOL.strip() == APP_PROTOCOL
    assert not APP_PROTOCOL.endswith("."), f"protocolo con punto final: {APP_PROTOCOL!r}"
    assert APP_PROTOCOL == "T.E.E.M.O"


def test_the_window_the_hud_and_the_prompt_carry_the_name():
    """La ventana, el HUD y el prompt usan la marca.

    El prompt no la escribe: la recibe. El fichero de prompt es una plantilla
    con marcadores, y el nombre se inyecta desde el sistema vivo al montar la
    sesion — asi que lo que se comprueba aqui es el resultado RENDERIZADO, no
    una cadena suelta dentro de la plantilla. Comprobar la plantilla en lugar
    del prompt final seria comprobar la mitad equivocada de la verdad.
    """
    for rel in ("gui/window.py", "gui/hud.py", "gui/theme.py"):
        text = (BASE / rel).read_text(encoding="utf-8")
        assert "T.E.E.M.O." in text, f"{rel} no menciona la marca"

    template = (BASE / "core" / "prompt.txt").read_text(encoding="utf-8")
    assert "{assistant_name}" in template, "el prompt debe tomar el nombre de la config"

    main_mod = pytest.importorskip("main")
    rendered = main_mod._render_prompt(template, {
        "assistant_name": "T.E.E.M.O.",
        "platform": "Windows 11",
        "capabilities": "- something",
        "limits": "- nothing",
    })
    assert "T.E.E.M.O." in rendered, "el prompt renderizado no lleva la marca"
    assert "{assistant_name}" not in rendered, "quedo un marcador sin rellenar"


def test_the_dashboard_is_called_teemo():
    for name in ("app.html", "login.html"):
        text = (BASE / "dashboard" / "static" / name).read_text(encoding="utf-8")
        assert "T.E.E.M.O." in text, f"{name} no menciona la marca"


# ── La palabra de activacion ────────────────────────────────────────────────

def test_the_default_wake_phrase_is_the_product_name():
    from core.wake_word import DEFAULT_WAKE_PHRASE, wake_phrase
    assert DEFAULT_WAKE_PHRASE == "Hey T.E.E.M.O"
    assert wake_phrase() == DEFAULT_WAKE_PHRASE


def test_the_wake_phrase_is_configurable(tmp_path, monkeypatch):
    """La frase se guarda, se lee y un valor vacio vuelve al defecto."""
    from memory import config_manager as cm
    from core.wake_word import wake_phrase

    original = cm.load_api_keys().get("wake_phrase")
    try:
        cm.save_wake_phrase("Hey Gutenberg")
        assert cm.get_wake_phrase() == "Hey Gutenberg"
        assert wake_phrase() == "Hey Gutenberg"
        # Vacio restaura el defecto: una frase de espacios dejaría al
        # asistente dormido para siempre, sin nada que senalar.
        cm.save_wake_phrase("   ")
        assert cm.get_wake_phrase() == cm.DEFAULT_WAKE_PHRASE
    finally:
        if original is None:
            cm.save_wake_phrase("")
        else:
            cm.save_wake_phrase(original)


def test_the_wake_model_is_a_separate_thing_from_the_phrase():
    """La frase y el modelo no son lo mismo, y el codigo lo dice.

    El modelo preentrenado que trae el paquete de terceros esta entrenado con
    otra frase. Confundir ambas cosas haria prometer una deteccion que ese
    modelo no puede hacer.
    """
    from core import wake_word
    assert wake_word._UPSTREAM_WAKE_STEM, "el modelo por defecto debe existir"
    assert wake_word._UPSTREAM_WAKE_STEM != wake_word.DEFAULT_WAKE_PHRASE.lower()


# ── El nombre configurado gana sobre el de la aplicacion ───────────────────

def test_the_configured_name_wins_over_the_built_in_one(tmp_path, monkeypatch):
    """El nombre configurado manda; el de la aplicacion es solo el defecto."""
    from memory import config_manager as cm
    original = cm.load_api_keys().get("assistant_name")
    try:
        cm.save_assistant_config("NOVA", "Tester")
        assert cm.get_assistant_name() == "NOVA"
        cm.save_assistant_config("", "Tester")
        assert cm.get_assistant_name() == "T.E.E.M.O.", "sin nombre, el defecto"
    finally:
        if original is None:
            cm.save_assistant_config("", "")
        else:
            cm.save_assistant_config(original, "")


def test_a_rename_in_the_config_file_reaches_the_window():
    """Un cambio escrito en config/api_keys.json se refleja en vivo.

    Sin esto, editar el fichero a mano noeria nada hasta reiniciar, que es
    justo lo que el criterio de aceptacion promete que no pasa.
    """
    gui_window = pytest.importorskip("gui.window")
    from memory import config_manager as cm

    original = cm.load_api_keys().get("assistant_name")
    applied = []

    class _Stub:
        """Lo minimo que _watch_identity necesita de la ventana."""
        _identity_mtime = None
        _assistant_name = "T.E.E.M.O."

        def _apply_name_to_ui(self, name):
            self._assistant_name = name
            applied.append(name)
            return name.upper()

        def _refresh_identity_rows(self):
            pass

        class _Log:
            def append_log(self, _msg):
                pass
        _log = _Log()

    try:
        cm.save_assistant_config("NOVA", "")
        stub = _Stub()
        gui_window.MainWindow._watch_identity(stub)
        assert applied == ["NOVA"], "el cambio de nombre no llego a la ventana"
        assert stub._assistant_name == "NOVA"
    finally:
        if original is None:
            cm.save_assistant_config("", "")
        else:
            cm.save_assistant_config(original, "")


# ── El contrato de la herramienta de apagado ────────────────────────────────

def test_the_shutdown_tool_is_renamed_everywhere_it_is_declared():
    """El nombre de la herramienta de apagado es uno solo en todo el codigo.

    Declararla con un nombre y despacharla con otro no da error: el modelo
    simplemente nunca la llama.
    """
    main_src = (BASE / "main.py").read_text(encoding="utf-8")
    audit_src = (BASE / "core" / "tool_audit.py").read_text(encoding="utf-8")
    prompt = (BASE / "core" / "prompt.txt").read_text(encoding="utf-8")

    assert '"name": "shutdown_teemo"' in main_src
    assert 'elif name == "shutdown_teemo"' in main_src
    assert '"shutdown_teemo": "high"' in audit_src
    assert "shutdown_teemo" in prompt
    assert "shutdown_" + _OLD_NAME not in main_src
    assert "shutdown_" + _OLD_NAME not in prompt
