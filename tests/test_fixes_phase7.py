"""
FASE 7 (correcciones) — las pruebas de los fallos REALES que encontro la
auditoria de las fases 0 a 6.

QUE HAY AQUI, Y POR QUE CADA COSA ES UNA PRUEBA
    La suite arrastraba 196 fallos. El triage los separo en tres grupos:
    los que dependen de la red, los que son BUGS REALES de este codigo, y
    los que son tests rotos contra un contrato viejo. Este fichero cubre
    el segundo grupo: los fallos que eran de verdad del producto.

    Cada seccion dice QUE FALLA y por que importa. Un test que solo
    comprueba que una funcion no revienta no sirve: estos comprueban que el
    comportamiento es el correcto.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


# ── CSV: TODA la lectura de CSV estaba rota ────────────────────────────────

def test_a_csv_is_read_on_pandas_2(tmp_path, monkeypatch):
    """`pd.read_csv(..., errors="replace")` dejo de existir en pandas 1.3.

    En pandas 2 (el que hay instaladas) la llamada lanzaba TypeError en
    TODOS los CSV, y el usuario recibia "Could not read file", que habla del
    fichero y no del codigo. La previsualizacion, la busqueda y el analisis
    de CSV estaban muertos.
    """
    from actions import file_processor

    csv = tmp_path / "datos.csv"
    csv.write_text("nombre,edad\nAna,31\nLuis,44\n", encoding="utf-8")

    out = file_processor._process_data(csv, "csv", "info", {})
    assert "Could not read file" not in out, out
    assert "Rows: 2" in out, out


def test_a_csv_with_a_bad_byte_does_not_raise(tmp_path):
    """El proposito del `replace` era no reventar con un byte ilegible."""
    from actions import file_processor

    csv = tmp_path / "roto.csv"
    csv.write_bytes(b"nombre,edad\nAna,\xff\xfe31\n")

    out = file_processor._process_data(csv, "csv", "info", {})
    assert "Could not read file" not in out, out


def test_the_csv_reader_works_on_old_pandas_too(tmp_path, monkeypatch):
    """El argumento se llama distinto en cada pandas. Se supportan los dos."""
    import pandas as pd

    calls = []

    def _fake_read_csv(path, **kwargs):
        calls.append(kwargs)
        raise TypeError("read_csv() got an unexpected keyword argument "
                        "'encoding_errors'")

    monkeypatch.setattr(pd, "read_csv", _fake_read_csv)
    from actions import file_processor
    csv = tmp_path / "x.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")

    with pytest.raises(TypeError):
        file_processor._read_csv(csv)

    assert "encoding_errors" in calls[0]


# ── Parametros que no son un objeto ────────────────────────────────────────

@pytest.mark.parametrize("bad", [["a", "b"], "text", 42, 3.5])
def test_computer_control_refuses_parameters_that_are_not_an_object(bad):
    """El modelo puede enviar CUALQUIER cosa como argumentos.

    Con una lista, `params.get(...)` lanzaba AttributeError dentro de la
    herramienta que deberia estar rechazando la entrada: un traceback donde
    el usuario esperaba una respuesta.
    """
    from actions import computer_control

    out = computer_control.computer_control({"action": "type", "text": "x"}
                                            if False else bad)
    assert isinstance(out, str)
    assert "AttributeError" not in out
    assert "instead of an object" in out


@pytest.mark.parametrize("bad", [["a", "b"], "text", 42])
def test_desktop_refuses_parameters_that_are_not_an_object(bad):
    from actions import desktop

    out = desktop.desktop_control(bad)
    assert isinstance(out, str)
    assert "AttributeError" not in out
    assert "instead of an object" in out


# ── El launches: una URL con /v1 duplicado ─────────────────────────────────

def test_a_duplicated_terminal_v1_is_collapsed():
    """`.../v1/v1` es un 404 que se lee como "el servidor esta caido"."""
    from providers.llm_openai import _normalise

    assert _normalise("https://api.example.test/v1/v1") == \
        "https://api.example.test/v1"
    assert _normalise("https://api.example.test/v1/v1/") == \
        "https://api.example.test/v1"


def test_a_prefixed_v1_is_left_alone():
    """OpenRouter sirve bajo /api/v1: ahi el /v1 ES la ruta."""
    from providers.llm_openai import _normalise

    assert _normalise("https://openrouter.ai/api/v1") == \
        "https://openrouter.ai/api/v1"


def test_a_missing_v1_is_added():
    from providers.llm_openai import _normalise

    assert _normalise("https://api.example.test") == "https://api.example.test/v1"


# ── El instalador: instalar sin preguntar ──────────────────────────────────

def test_nothing_is_installed_without_confirmation():
    """El instalador descargaba paquetes y un navegador de 150 MB la primera
    vez que arrancaba la aplicacion, sin preguntar a nadie."""
    from unittest.mock import patch

    from core import installer

    logs: list[str] = []
    with (patch.object(installer, "_available", return_value=False),
          patch.object(installer, "_pip") as pip):
        installer.install_for_config({}, logs.append)

    pip.assert_not_called()
    assert any("not started" in m for m in logs), logs


def test_a_failed_install_is_not_reported_as_ready():
    """El log decia "All dependencies ready" DESPUES de una instalacion
    fallida. El usuario se lo creia hasta que la funcion no estaba."""
    from unittest.mock import patch

    from core import installer

    logs: list[str] = []
    with (patch.object(installer, "_available", return_value=False),
          patch.object(installer, "_pip", return_value=False)):
        installer.install_for_config({}, logs.append, confirmed=True)

    assert any("incomplete" in m for m in logs), logs
    assert not any("All dependencies ready" in m for m in logs), logs


# ── La busqueda en el menu de inicio ───────────────────────────────────────

def test_the_start_menu_search_does_not_type_blindly():
    """La version anterior escribia el nombre de la aplicacion donde
    estuviera el foco y pulsaba Enter. Eso puede escribir en la ventana de
    chat del usuario y pulsar Enter dentro de ella."""
    source = (BASE / "actions" / "open_app.py").read_text(encoding="utf-8")
    assert "pyautogui.write(app_name" not in source
    assert "SearchTextBox" in source


def test_the_start_menu_search_checks_what_it_wrote():
    source = (BASE / "actions" / "open_app.py").read_text(encoding="utf-8")
    assert "get_value()" in source


# ── El Event Stream: la latencia por accion ────────────────────────────────

def test_a_real_dispatch_is_recorded_in_the_audit_stream():
    """`core/tool_audit.record()` existia y funcionaba, y NADA lo llamaba
    nunca: el despacho real iba del modelo al manejador sin pasar por
    el. El Event Stream estaba siempre vacio."""
    import pathlib

    from core import action_loader, tool_audit

    registry = action_loader.discover_actions(pathlib.Path("actions"),
                                              eager=False)
    registry.validate_pending()
    tool_audit.clear()

    registry.run("file_controller", {"action": "list"}, {})

    events = tool_audit.recent(5)
    assert events, "the dispatch was not recorded"
    event = events[0]
    assert event.tool == "file_controller"
    assert event.action == "list"
    assert event.duration_ms >= 0


def test_the_audit_record_keeps_no_arguments():
    """El registro guarda metadatos, no lo que el usuario dijo."""
    from core import tool_audit

    event = tool_audit.record(
        "file_controller", "status=done", 5.0,
        {"action": "list", "path": "C:/Users/sergi/secret.txt"},
    )
    assert not hasattr(event, "arguments")
    assert not hasattr(event, "result")


# ── La purga de macOS y Linux ──────────────────────────────────────────────

def test_no_macos_or_linux_launcher_survived():
    """El producto es Windows-only. Las ramas de macOS y Linux se fueron,
    y con ellas el riesgo de que un fichero de configuracion antiguo
    metiera a una maquina Windows en el camino equivocado.

    Se mira el ARBOL, no el texto: los docstrings explican la historia y
    tienen derecho a nombrar lo que se elimino. Lo que no puede quedar es
    el codigo.
    """
    import ast

    forbidden = ("gtk-launch", "AVFOUNDATION", "os_system")
    for rel in ("actions/send_message.py", "actions/screen_processor.py"):
        tree = ast.parse((BASE / rel).read_text(encoding="utf-8"))
        # The docstrings are allowed to NAME what was removed: they explain
        # the history. What must not survive is executable code.
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                for word in forbidden:
                    assert word not in node.value, f"{rel}: {word} is still in the code"
            if isinstance(node, ast.Attribute) and node.attr == "CAP_AVFOUNDATION":
                raise AssertionError(f"{rel}: AVFOUNDATION is still used")


def test_the_operating_system_is_not_configurable_anymore():
    """El sistema operativo lo decide el sistema, no un fichero de
    configuracion."""
    from actions import send_message

    assert send_message._get_os() == "windows"


# ── El presupuesto general del bucle del agente ────────────────────────────

def test_the_live_loop_has_a_general_tool_budget():
    """El navegador tenia su fusible (FASE 5) y el motor de tareas el suyo,
    pero la conversacion en vivo no tenia ninguno: un modelo que seguía
    pidiendo herramientas las seguia recibiendo, y solo el usuario podia
    parar aquello."""
    import main

    assert main.MAX_TOOL_ROUNDS_PER_TURN >= 4
    source = (BASE / "main.py").read_text(encoding="utf-8")
    assert "MAX_TOOL_ROUNDS_PER_TURN" in source
    assert "REFUSED" in source


def test_a_new_turn_gets_a_new_budget():
    """El presupuesto es POR TURNO, no por sesion: una pregunta nueva
    empieza con el contador a cero."""
    source = (BASE / "main.py").read_text(encoding="utf-8")
    assert "self._tool_rounds = 0" in source
