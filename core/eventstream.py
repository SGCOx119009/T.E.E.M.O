"""core/eventstream.py — lo que hizo el asistente, paso a paso, y cuánto tardó.

QUÉ ES ESTO
    Un **bus de eventos en memoria**: cada paso de herramienta deja un
    registro con su operador, su acción, sus argumentos (enmascarados), si
    salió bien, cuánto tardó y cuánto pesó. Es el sitio donde los cinco
    veredictos de `core/outcome.py` (done / unverified / failed / ask /
    started) **se ven**.

    Antes de esto, la latencia de una herramienta se medía y se guardaba en
    `core/tool_audit.py`, y no había ningún sitio donde se viera. La
    diferencia entre «el asistente respondió rápido» y «el asistente
    esperó cuatro segundos a una herramienta que no respondió» quedaba
    fuera de la vista de cualquiera.

EL BUFFER ES CIRCULAR Y ESTÁ ACOTADO
    Cinco mil eventos. Los más antiguos se caen cuando entran nuevos: un
    registro que crece sin límite es una memoria que crece sin límite, y
    esta aplicación vive en la memoria de la máquina del usuario.

LA ESCRITURA ES ATÓMICA
    El bus se escribe desde los hilos de las herramientas, que son muchos
    y que no se turnan. Un evento es un objeto entero o no es nada: no hay
    un evento a medio construir que alguien pueda leer.

LOS ARGUMENTOS SENSIBLES ESTÁN ENMASCARADOS
    Con la **misma regla de la FASE 9** (`core/policy.py::mask_arguments`):
    la clave se queda, para que se vea que se está mandando una
    credencial, y el valor se acorta. Un bus que guardara la contraseña
    del usuario en claro sería un bus que no se puede abrir delante de
    nadie.

NADA SALE DEL EQUIPO
    No hay red aquí, ni ficheros, ni procesos: el bus vive en la memoria
    del programa y muere con él. Es una herramienta de diagnóstico local,
    y por eso no tiene ni una línea que mande nada a ningún sitio.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

#: How many events the bus keeps. The oldest ones fall off the end.
MAX_EVENTS = 5000

#: The longest text kept for one argument. A stream that holds a whole
#: document is a stream that holds the user's whole day.
MAX_VALUE_CHARS = 400


@dataclass(frozen=True)
class Event:
    """One step of the assistant, as it happened."""

    ts: float
    turn_id: int
    operator: str
    action: str
    args_resolved: dict
    ok: bool
    duration_ms: int
    bytes: int
    error: str

    def as_dict(self) -> dict:
        """The event as plain data, for a table or for a file."""
        return {
            "ts": self.ts,
            "turn_id": self.turn_id,
            "operator": self.operator,
            "action": self.action,
            "args_resolved": dict(self.args_resolved or {}),
            "ok": bool(self.ok),
            "duration_ms": int(self.duration_ms),
            "bytes": int(self.bytes),
            "error": self.error,
        }

    def summary(self) -> str:
        """One line a person can read."""
        when = time.strftime("%H:%M:%S", time.localtime(self.ts))
        state = "ok" if self.ok else (self.error or "failed")
        return (f"{when}  {self.operator}"
                f"{'.' + self.action if self.action else ''}"
                f"  {self.duration_ms} ms  {state}")


_events: deque[Event] = deque(maxlen=MAX_EVENTS)
_lock = threading.RLock()
_subscribers: list[Callable[[Event], None]] = []
_turn = 0


# ── the turn ────────────────────────────────────────────────────────────────

def begin_turn() -> int:
    """A new turn of the conversation starts here, and it gets a number.

    The turn is the group of steps that belong to one request of the
    user. It is the thing the panel filters by, and it is what makes a
    list of thirty steps readable: the last turn is what just happened."""
    global _turn
    with _lock:
        _turn += 1
        return _turn


def current_turn() -> int:
    """The number of the turn that is running."""
    with _lock:
        return _turn


# ── writing ─────────────────────────────────────────────────────────────────

def emit(operator: str, action: str = "", args_resolved: dict | None = None,
         ok: bool = True, duration_ms: float = 0, bytes: int = 0,
         error: str = "", turn_id: int | None = None) -> Event | None:
    """Put one step in the bus, and tell whoever is watching.

    IT NEVER RAISES. A diagnostic that breaks the tool call it is
    describing is worse than no diagnostic at all."""
    try:
        from core.policy import mask_arguments

        masked = mask_arguments(args_resolved or {})
    except Exception:                                   # noqa: BLE001
        masked = {}
    try:
        with _lock:
            event = Event(
                ts=time.time(),
                turn_id=_turn if turn_id is None else int(turn_id),
                operator=str(operator or "")[:64],
                action=str(action or "")[:64],
                args_resolved=_bounded(masked),
                ok=bool(ok),
                duration_ms=max(0, min(int(duration_ms), 86_400_000)),
                bytes=max(0, int(bytes or 0)),
                error=str(error or "")[:200],
            )
            _events.append(event)
            watchers = list(_subscribers)
        for watcher in watchers:
            try:
                watcher(event)
            except Exception:                           # noqa: BLE001
                # A watcher that fails must not stop the others, and must
                # never stop the step that is being described.
                pass
        return event
    except Exception:                                   # noqa: BLE001
        return None


def _bounded(value):
    """The arguments, with the long texts cut.

    The keys stay — seeing THAT a credential was sent is the point of
    masking it — and the values are shortened so that a whole document
    cannot end up in a diagnostic."""
    if isinstance(value, dict):
        return {str(k): _bounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bounded(v) for v in value]
    if isinstance(value, str):
        return value[:MAX_VALUE_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_VALUE_CHARS]


# ── reading ─────────────────────────────────────────────────────────────────

def events(turn_id: int | None = None, operator: str | None = None,
           errors_only: bool = False, limit: int = 200) -> list[Event]:
    """The events, newest first, with the filters of la FASE 12.

    The three filters are the ones del plan: por turno, por operador y
    solo errores."""
    try:
        count = max(1, min(int(limit), MAX_EVENTS))
    except (TypeError, ValueError):
        count = 200
    turn = None
    if turn_id is not None:
        try:
            turn = int(turn_id)
        except (TypeError, ValueError):
            # A filter that does not make sense shows nothing, and never
            # takes the panel down with it.
            return []
    with _lock:
        everything = list(_events)
    out = []
    for event in reversed(everything):
        if turn is not None and event.turn_id != turn:
            continue
        if operator and event.operator != operator:
            continue
        if errors_only and event.ok:
            continue
        out.append(event)
        if len(out) >= count:
            break
    return out


def operators() -> list[str]:
    """The operators seen so far, for the filter."""
    with _lock:
        names = {event.operator for event in _events if event.operator}
    return sorted(names)


def turns() -> list[int]:
    """The turns seen so far, newest first."""
    with _lock:
        return sorted({event.turn_id for event in _events}, reverse=True)


def count() -> int:
    """How many events the bus holds."""
    with _lock:
        return len(_events)


def clear() -> None:
    """Empty the bus."""
    with _lock:
        _events.clear()


# ── watching ────────────────────────────────────────────────────────────────

def subscribe(callback: Callable[[Event], None]) -> None:
    """Call this every time an event is written.

    The callback runs on the thread that wrote the event, so it has to be
    quick — and it must not raise: a watcher that fails is ignored, and
    the step it was describing is still recorded."""
    with _lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe(callback: Callable[[Event], None]) -> None:
    """Stop calling this."""
    with _lock:
        if callback in _subscribers:
            _subscribers.remove(callback)
