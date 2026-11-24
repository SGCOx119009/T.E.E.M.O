"""tests/test_eventstream.py — el Event Stream de la FASE 12.

QUÉ SE COMPRUEBA
    1. Cada paso de herramienta deja un evento con su operador, su acción,
       su tiempo **medido** y cómo salió.
    2. Los argumentos sensibles llegan **enmascarados** (la regla de la
       FASE 9), y los largos, cortados.
    3. El buffer es **circular**: los eventos viejos se caen.
    4. Los turnos se numeran y los tres filtros del plan funcionan.
    5. El bus **nunca** rompe la llamada que está describiendo, ni aunque
       un vigilante falle.
    6. La auditoría (el único sitio donde se mide) deja el paso en el bus.
    7. El panel de la interfaz muestra los pasos y filtra.
    8. La ventana tiene el grupo, y `main.py` abre el turno en el sitio
       real de frontera de turno.
    9. **Nada sale del equipo**: el módulo no tiene ni una línea de red.
"""
from __future__ import annotations

import os
import sys
from collections import deque
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import eventstream  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_bus():
    """Every test starts with an empty bus and leaves it empty."""
    saved_events = eventstream._events
    saved_subs = list(eventstream._subscribers)
    saved_turn = eventstream._turn
    eventstream._events = deque(maxlen=eventstream.MAX_EVENTS)
    eventstream._subscribers = []
    eventstream._turn = 0
    yield
    eventstream._events = saved_events
    eventstream._subscribers = saved_subs
    eventstream._turn = saved_turn


@pytest.fixture()
def qt_app():
    """A Qt application, offscreen."""
    previous = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    if previous is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = previous


# ── the bus ─────────────────────────────────────────────────────────────────

def test_a_step_leaves_its_latency_and_its_outcome():
    """The event carries what happened and how long it took."""
    event = eventstream.emit(
        "file_controller",
        "read",
        {"path": "notas.txt"},
        ok=True,
        duration_ms=123,
        bytes=42,
    )
    assert event is not None
    assert event.operator == "file_controller"
    assert event.action == "read"
    assert event.ok is True
    assert event.duration_ms == 123
    assert event.bytes == 42
    assert event.error == ""
    assert event.ts > 0


def test_the_secrets_are_masked_and_the_key_stays():
    """A bus that kept the password in clear could not be shown to anybody."""
    secret = "sk-0123456789abcdef"
    event = eventstream.emit(
        "gmail",
        "send",
        {"api_key": secret, "to": "amigo@example.com"},
        ok=True,
    )
    assert event is not None
    assert secret not in str(event.args_resolved)
    assert "api_key" in event.args_resolved
    # The address is not a secret, so it stays whole.
    assert event.args_resolved["to"] == "amigo@example.com"


def test_the_long_texts_are_cut():
    """A step does not carry a whole document into a diagnostic."""
    event = eventstream.emit(
        "notes",
        "write",
        {"text": "x" * 5000},
        ok=True,
    )
    assert event is not None
    assert len(event.args_resolved["text"]) == eventstream.MAX_VALUE_CHARS


def test_the_buffer_is_circular():
    """The oldest events fall off the end: this is a memory, not a diary."""
    eventstream._events = deque(maxlen=5)
    for number in range(12):
        eventstream.emit("tool", "step", {"n": number}, ok=True)
    assert eventstream.count() == 5
    numbers = [event.args_resolved["n"] for event in eventstream.events(limit=50)]
    assert numbers == [11, 10, 9, 8, 7]


def test_the_turn_numbers_the_steps():
    """A new instruction opens a new turn, and the steps belong to it."""
    first = eventstream.begin_turn()
    eventstream.emit("tool", "step", {}, ok=True)
    second = eventstream.begin_turn()
    eventstream.emit("tool", "step", {}, ok=True)
    assert second == first + 1
    assert eventstream.current_turn() == second
    assert len(eventstream.events(turn_id=first)) == 1
    assert len(eventstream.events(turn_id=second)) == 1
    assert len(eventstream.events()) == 2


def test_the_three_filters_of_the_plan():
    """By turn, by operator, and only the errors."""
    eventstream.begin_turn()
    eventstream.emit("alpha", "go", {}, ok=True)
    eventstream.emit("beta", "go", {}, ok=False, error="boom")
    eventstream.emit("alpha", "stop", {}, ok=False, error="nope")
    turn = eventstream.current_turn()

    assert len(eventstream.events(turn_id=turn)) == 3
    assert len(eventstream.events(operator="alpha")) == 2
    errors = eventstream.events(errors_only=True)
    assert len(errors) == 2
    assert all(event.ok is False for event in errors)
    assert len(eventstream.events(operator="alpha", errors_only=True)) == 1


def test_the_bus_never_raises():
    """A diagnostic that breaks the call it describes is worse than none."""
    assert eventstream.emit(None, None, None) is not None
    assert eventstream.emit("t", "a", {"k": object()}, ok=True) is not None
    assert eventstream.emit("t", "a", "not-a-dict", ok=True) is not None
    assert eventstream.emit("t", "a", {}, ok=True, duration_ms=-5).duration_ms == 0
    assert eventstream.emit("t", "a", {}, ok=True, duration_ms=10**12).duration_ms \
        == 86_400_000
    # And the reading side survives a broken argument too.
    assert eventstream.events(turn_id="not-a-number") == []


def test_a_watcher_that_fails_does_not_break_the_bus():
    """The watchers are told; the ones that fail are ignored."""
    seen = []

    def broken(event):
        raise RuntimeError("this watcher is broken")

    def good(event):
        seen.append(event)

    eventstream.subscribe(broken)
    eventstream.subscribe(good)
    event = eventstream.emit("tool", "step", {}, ok=True)
    assert event is not None
    assert len(seen) == 1
    assert eventstream.count() == 1

    eventstream.unsubscribe(good)
    eventstream.emit("tool", "step", {}, ok=True)
    assert len(seen) == 1
    assert eventstream.count() == 2


def test_clear_empties_the_bus():
    """The user can wipe what the assistant did."""
    eventstream.emit("tool", "step", {}, ok=True)
    assert eventstream.count() == 1
    eventstream.clear()
    assert eventstream.count() == 0
    assert eventstream.events() == []


# ── the audit writes into the bus ───────────────────────────────────────────

def test_the_audit_leaves_the_step_with_the_measured_time():
    """The audit is the ONLY place where a tool is measured.

    Two measurements would be two numbers for the same thing, and that is
    how a diagnostic starts lying. So the bus takes the number from here
    and never measures again."""
    from core import tool_audit

    eventstream.clear()
    tool_audit.record(
        "file_controller",
        "status=done action=read",
        87.4,
        {"path": "notas.txt"},
    )
    events = eventstream.events()
    assert len(events) == 1
    assert events[0].operator == "file_controller"
    assert events[0].duration_ms == 87
    assert events[0].ok is True


def test_the_audit_marks_the_failures():
    """A tool that failed is a step that failed, and the panel says so."""
    from core import tool_audit

    eventstream.clear()
    tool_audit.record("file_controller",
                      "status=failed action=read reason=no_existe", 12, {})
    events = eventstream.events()
    assert len(events) == 1
    assert events[0].ok is False
    assert events[0].error


# ── the panel ───────────────────────────────────────────────────────────────

def test_the_panel_shows_the_steps(qt_app):
    """What the bus holds is what the table shows."""
    from gui.event_panel import EventStreamPanel

    eventstream.emit("file_controller", "read", {}, ok=True, duration_ms=42)
    eventstream.emit("gmail", "send", {}, ok=False, error="boom")

    panel = EventStreamPanel()
    panel.refresh()
    table = panel._table
    assert table.rowCount() == 2
    assert table.item(0, 1).text() == "gmail"
    assert table.item(1, 1).text() == "file_controller"
    assert table.item(1, 3).text() == "42 ms"


def test_the_panel_filters_the_errors(qt_app):
    """The 'only errors' box empties the table of what went well."""
    from gui.event_panel import EventStreamPanel

    eventstream.emit("file_controller", "read", {}, ok=True)
    eventstream.emit("gmail", "send", {}, ok=False, error="boom")

    panel = EventStreamPanel()
    panel.refresh()
    assert panel._table.rowCount() == 2
    panel._errors.setChecked(True)
    panel.refresh()
    assert panel._table.rowCount() == 1
    assert panel._table.item(0, 1).text() == "gmail"


def test_the_panel_offers_the_operators(qt_app):
    """The filter of the operator is filled with the operators seen."""
    from gui.event_panel import EventStreamPanel

    eventstream.emit("alpha", "go", {}, ok=True)
    eventstream.emit("beta", "go", {}, ok=True)

    panel = EventStreamPanel()
    panel.refresh()
    names = [panel._operator.itemText(i)
             for i in range(panel._operator.count())]
    assert "alpha" in names
    assert "beta" in names
    # The first entry is "all", so the table is not empty by accident.
    assert panel._operator.itemData(0) == ""


def test_the_panel_survives_a_panel_that_cannot_draw(qt_app):
    """A panel that fails to draw is an empty panel, not a crash."""
    from gui.event_panel import EventStreamPanel

    panel = EventStreamPanel()
    panel._table = None
    panel.refresh()


# ── the wiring ──────────────────────────────────────────────────────────────

def test_the_window_has_the_group_and_the_signal():
    """The Event Stream is a group of the drawer, not a floating window."""
    import gui.window as window

    assert "events" in window.MainWindow._GROUPS
    assert hasattr(window.MainWindow, "_events_sig")


def test_the_turn_opens_where_the_turn_really_ends():
    """The turn of the bus is opened in the real boundary of the turn.

    It is opened in `_send_and_arm()`, which is the one place where a new
    instruction of the user becomes one — not somewhere else, where the
    number would be one step ahead of what happened."""
    source = (BASE / "main.py").read_text(encoding="utf-8")
    assert "eventstream.begin_turn()" in source
    anchor = source.find("eventstream.begin_turn()")
    assert anchor > 0
    around = source[max(0, anchor - 4000):anchor]
    assert "_send_and_arm" in around


def test_nothing_leaves_the_machine():
    """The bus is a local diagnostic: it has no line of network in it."""
    source = (BASE / "core" / "eventstream.py").read_text(encoding="utf-8")
    for forbidden in ("import socket", "import urllib", "import requests",
                      "import http", "socket.socket", "urlopen"):
        assert forbidden not in source, forbidden
