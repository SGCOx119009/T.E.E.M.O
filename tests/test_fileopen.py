"""
FASE 7 (correccion A1/A2) — abrir un ARCHIVO con su propia aplicacion.

EL AGUERO QUE ESTA PRUEBA CIERRA
    "Abre el Excel que dice WA" se respondio "no puedo abrirlo directamente".
    No era un permiso: era que la unica forma de arrancar algo era `open_app`,
    y `open_app` tomaba el NOMBRE DE UNA APLICACION. Un documento quedaba
    fuera de alcance, y el asistente lo decia como si fuera una limitacion
    de la maquina.

    Estas pruebas comprueban las tres cosas que faltaban: que un nombre de
    fichero se reconoce como fichero, que se encuentra en el disco, y que se
    abre CON LA APLICACION QUE WINDOWS ASOCIA — y que cuando algo falla, se
    dice la verdad en vez de decir "hecho".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from actions import open_app as oa          # noqa: E402
from core import fileopen                   # noqa: E402


# ── Que un nombre se tome por un fichero, y no por una aplicacion ──────────

@pytest.mark.parametrize("text", [
    "wa.xlsx",
    "informe.pdf",
    "CUENTAS.XLSX",
    r"C:\Users\sergi\Desktop\wa.xlsx",
    r"Desktop\informe.pdf",
    "presupuesto 2024.docx",
])
def test_a_file_name_is_recognised_as_a_file(text):
    assert fileopen.looks_like_file(text) is True


@pytest.mark.parametrize("text", [
    "Excel",
    "WhatsApp",
    "Visual Studio Code",
    "notepad.exe",
    "setup.cmd",
    "",
])
def test_an_application_name_is_not_a_file(text):
    assert fileopen.looks_like_file(text) is False


def test_a_name_alone_is_not_guessed_to_be_a_file():
    """"WA" sin extension es un nombre: puede ser un fichero o una app.

    Decidirlo por la extension es lo que separa las dos rutas. Sin
    extension, la herramienta no lo adivina y el caso vuelve al questionario.
    """
    assert fileopen.looks_like_file("WA") is False


# ── Que el fichero se encuentre de verdad ───────────────────────────────────

def test_a_full_path_resolves_to_itself(tmp_path):
    target = tmp_path / "wa.xlsx"
    target.write_bytes(b"x")
    assert fileopen.resolve(str(target)) == str(target)


def test_a_bare_name_is_found_in_the_users_folders(tmp_path, monkeypatch):
    (tmp_path / "wa.xlsx").write_bytes(b"x")
    monkeypatch.setattr(fileopen, "search_roots", lambda: [str(tmp_path)])
    assert fileopen.resolve("wa.xlsx") == str(tmp_path / "wa.xlsx")


def test_a_bare_name_matches_on_the_stem(tmp_path, monkeypatch):
    """El usuario dice "WA" y el fichero es "WA.xlsx": el nombre sin
    extension se empareja con el nombre del fichero, con o sin extension."""
    (tmp_path / "WA.xlsx").write_bytes(b"x")
    monkeypatch.setattr(fileopen, "search_roots", lambda: [str(tmp_path)])
    assert fileopen.resolve("WA") == str(tmp_path / "WA.xlsx")


def test_a_file_that_does_not_exist_is_not_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(fileopen, "search_roots", lambda: [str(tmp_path)])
    assert fileopen.resolve("nada.xlsx") is None


def test_the_search_does_not_walk_the_whole_machine(monkeypatch):
    """La busqueda tiene un presupuesto. Un "open WA" no puede convertirse en
    un escaneo del disco."""
    assert fileopen._SEARCH_BUDGET_S <= 5.0
    assert fileopen._SEARCH_DEPTH <= 3


# ── Que la ventana que prueba la apertura sea la del documento ──────────────

def test_a_title_matches_the_document_name_on_whole_words():
    assert fileopen._mentions("WA - Excel", "WA") is True
    assert fileopen._mentions("cuentas - Excel", "cuentas") is True
    assert fileopen._mentions("cuentas.xlsx - Excel", "cuentas") is True


def test_a_title_does_not_match_a_document_inside_a_word():
    """"WA" dentro de "WhatsApp" NO es el documento "WA.xlsx"."""
    assert fileopen._mentions("WhatsApp", "WA") is False
    assert fileopen._mentions("walter.xlsx - Excel", "WA") is False


def test_a_title_that_names_the_document_does_match():
    assert fileopen._mentions("WA.xlsx - Excel", "WA") is True


class _FakeThread:
    """Un hilo que no arranca nada: la prueba comprueba el contrato del
    resultado, no la verificacion en segundo plano."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.started = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        return None


# ── Que se abra CON SU APLICACION ───────────────────────────────────────────

def test_the_association_is_read_from_the_registry(monkeypatch):
    """.xlsx -> el ejecutable que Windows tiene asociado, no uno supuesto."""
    monkeypatch.setattr(
        fileopen, "_read_value",
        lambda path, root: {
            ".xlsx": "Excel.Sheet.12",
            "Excel.Sheet.12\\shell\\open\\command":
                '"C:\\Program Files\\Microsoft Office\\root\\Office16\\EXCEL.EXE" "%1"',
        }.get(path),
    )
    assoc = fileopen.association("C:\\tmp\\wa.xlsx")
    assert assoc.name == "EXCEL"
    assert assoc.exe.lower().endswith("excel.exe")
    assert assoc.usable is True


def test_the_executable_is_taken_out_of_a_quoted_command():
    assert fileopen._exe_from_command(
        '"C:\\Program Files\\Office\\EXCEL.EXE" "%1"') == \
        "C:\\Program Files\\Office\\EXCEL.EXE"
    assert fileopen._exe_from_command("C:\\Tools\\thing.exe %1") == \
        "C:\\Tools\\thing.exe"
    assert fileopen._exe_from_command("") == ""


def test_a_file_with_no_application_associated_is_not_claimed_to_open(
        tmp_path, monkeypatch):
    """Si Windows no tiene con que abrirlo, se dice. No se dice "hecho"."""
    target = tmp_path / "wa.xyz"
    target.write_bytes(b"x")

    def _boom(path):
        raise OSError(215, "no application is associated")

    monkeypatch.setattr(fileopen, "open_with_default_app", _boom)
    result = oa.open_app({"app_name": str(target)})
    assert "status=failed" in result
    assert "wa.xyz" in result


def test_opening_a_document_reports_the_file_and_the_application(
        tmp_path, monkeypatch):
    """El resultado dice QUE se abrio y CON QUE, no solo 'done'."""
    target = tmp_path / "wa.xlsx"
    target.write_bytes(b"x")
    opened: list[str] = []

    monkeypatch.setattr(fileopen, "open_with_default_app",
                        lambda p: opened.append(p))
    monkeypatch.setattr(
        fileopen, "association",
        lambda p: fileopen.Association(prog_id="Excel.Sheet.12",
                                       exe="C:\\x\\EXCEL.EXE", name="EXCEL"))
    # El hilo de verificacion no se lanza en la prueba: se comprueba el
    # contrato del resultado, que es lo que ve el modelo.
    started: list[bool] = []

    class _T(_FakeThread):
        def start(self):
            started.append(True)

    monkeypatch.setattr(oa.threading, "Thread", _T)

    result = oa.open_app({"app_name": str(target)})
    assert "status=started" in result
    assert "wa.xlsx" in result
    assert "EXCEL" in result
    assert opened == [str(target)]
    assert started, "the verification thread must be started"


def test_a_file_that_is_not_there_is_reported_as_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fileopen, "search_roots", lambda: [str(tmp_path)])
    result = oa.open_app({"app_name": "no_existe.xlsx"})
    assert "status=failed" in result
    assert "no_existe.xlsx" in result


def test_an_application_still_goes_down_the_application_route(monkeypatch):
    """La ruta del fichero NO se come la ruta de la aplicacion."""
    called: list[str] = []
    monkeypatch.setattr(oa, "_open_document",
                        lambda spoken, player: called.append(spoken) or "DOC")
    monkeypatch.setattr(oa.appidentity, "running", lambda *a, **k: [])
    monkeypatch.setattr(oa.appidentity, "launcher_name", lambda s: s)
    monkeypatch.setattr(oa.appidentity, "snapshot", lambda: set())
    monkeypatch.setattr(oa.threading, "Thread", _FakeThread)

    result = oa.open_app({"app_name": "Excel"})
    assert called == []
    assert "status=started" in result


# ── Que el MODELO sepa que puede ───────────────────────────────────────────

def test_the_tool_description_says_it_opens_files():
    """A2: el modelo decia "no tengo la capacidad de ejecutar o mostrar
    aplicaciones de escritorio" CON LA HERRAMIENTA CARGADA. La causa no era
    solo la herramienta: su descripcion solo hablaba de aplicaciones, asi
    que un documento le quedaba fuera del alcance por descripcion."""
    desc = oa.TOOL["description"].lower()
    assert "file" in desc
    assert "document" in desc
    assert "extension" in desc
    assert "you can" in desc, "la descripcion debe decir que se puede"


def test_the_tool_still_tells_the_model_not_to_open_browsers():
    """La regla que evita lanzar el navegador DOS veces no se pierde."""
    desc = oa.TOOL["description"]
    assert "browser_control" in desc
    assert "TWICE" in desc
