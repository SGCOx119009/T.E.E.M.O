"""
FASE 1 — el asistente no reporta un éxito que no ha comprobado.

QUE EXISTE ESTE FICHERE PARA
    `actions/computer_settings.py` decía "hecho, verificado" para acciones
    cuyos efectos nunca se comproban: la pantalla que no se apagaba, el tema
    que no cambiaba, un reinicio que Windows habia rechazado, una radio WiFi
    que no se movia. El usuario oia "listo" y no habia pasado nada — que es
    peor que un error, porque un error se puede corregir y una mentira
    consumes una confianza.

    Cada prueba de aqui es DETERMINISTA: las costuras (la llamada al sistema,
    la lectura de vuelta, el subproceso) se sustituyen, y la maquina real no
    aparece en ningun sitio. Nada de esto depende de tener bateria, de tener
    tarjeta grafica ni de que haya red.

LO QUE SE COMPRUEBA
    1. una accion que falla NO se reporta como hecha
    2. una accion cuyo efecto no se puede leer NO se reporta como verificada
    3. un reinicio o un apagado que Windows rechaza son un fallo
    4. un WiFi que no cambia no se reporta como cambiado
    5. la confirmacion no dice "hecho" antes de que la accion ocurra
    6. la accion de energia lee, o dice que no hay bateria — nunca inventa
"""
from __future__ import annotations

from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent

from actions import computer_settings as cs  # noqa: E402


# ── Ayudas ──────────────────────────────────────────────────────────────────

class _FakeCompleted:
    """Lo que devuelve `subprocess.run`, sin procesos de por medio."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocess:
    """Un `subprocess` de mentira: solo `run`, que es lo que usa este modulo."""

    def __init__(self, result: _FakeCompleted):
        self._result = result
        self.calls: list[tuple] = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._result


def _no_done(result: str) -> None:
    """El resultado no puede ser un "hecho y verificado"."""
    assert "status=done" not in result, result
    assert "verified=true" not in result, result


@pytest.fixture(autouse=True)
def _tool_is_usable(monkeypatch):
    """El modulo exige pyautogui antes de hacer nada; aqui no lo hay."""
    monkeypatch.setattr(cs, "_PYAUTOGUI", True)


def _run(action: str, **params) -> str:
    return cs.computer_settings({"action": action, **params})


def _confirmed(monkeypatch, action: str) -> str:
    """The outcome of an action the user confirmed on the screen.

    The irreversible actions do not happen until a human presses the button,
    so the only way to see what they report is to take the callback the
    confirmation was given and run it — which is what the HUD does.
    """
    captured: dict = {}

    def _fake_request(key, title, detail, run, voice_handler=None):
        captured["run"] = run
        return "[CONFIRMATION_PENDING] waiting"

    monkeypatch.setattr(cs.confirm, "request", _fake_request)
    monkeypatch.setattr(cs.confirm, "pending_title", lambda: "")

    pending = cs.computer_settings({"action": action})
    assert "[CONFIRMATION_PENDING]" in pending
    return captured["run"]()


# ── 1. La pantalla ──────────────────────────────────────────────────────────

def test_a_display_that_will_not_sleep_is_not_reported_as_done(monkeypatch):
    """Si la llamada falla, el resultado es un fallo — no un "hecho"."""
    def _boom():
        raise OSError("the message was refused")

    monkeypatch.setattr(cs, "_send_monitor_power_off", _boom)
    monkeypatch.setattr(cs, "_display_power_state", lambda: None)

    result = _run("sleep_display")

    assert "status=failed" in result
    _no_done(result)


def test_a_display_that_cannot_be_read_back_is_unverified(monkeypatch):
    """La peticion se envio, pero nadie lo confirmo: eso es "unverified"."""
    sent: list[bool] = []

    monkeypatch.setattr(cs, "_send_monitor_power_off", lambda: sent.append(True))
    monkeypatch.setattr(cs, "_display_power_state", lambda: None)

    result = _run("sleep_display")

    assert sent == [True], "la peticion no se envio"
    assert "status=unverified" in result
    _no_done(result)


def test_a_display_that_confirms_it_is_off_is_reported_as_done(monkeypatch):
    """Solo un estado LEIDO permite decir que se apagó."""
    monkeypatch.setattr(cs, "_send_monitor_power_off", lambda: None)
    monkeypatch.setattr(cs, "_display_power_state", lambda: True)

    result = _run("sleep_display")

    assert "status=done" in result
    assert "verified=true" in result


def test_the_display_that_still_reads_on_is_not_reported_as_done(monkeypatch):
    """Un estado que dice "encendida" no es una pantalla apagada."""
    monkeypatch.setattr(cs, "_send_monitor_power_off", lambda: None)
    monkeypatch.setattr(cs, "_display_power_state", lambda: False)

    result = _run("sleep_display")

    assert "status=unverified" in result
    _no_done(result)


# ── 2. El tema ──────────────────────────────────────────────────────────────

def test_a_theme_that_cannot_be_written_is_not_reported_as_done(monkeypatch):
    """El error del registro ya no se traga: se reporta."""
    import winreg

    def _refused(*_args, **_kwargs):
        raise PermissionError("the key is not writable")

    monkeypatch.setattr(winreg, "OpenKey", _refused)

    result = _run("dark_mode")

    assert "status=failed" in result
    _no_done(result)


def test_a_theme_that_cannot_be_read_back_is_unverified(monkeypatch):
    """Escrito puede no ser lo mismo que leido."""
    import winreg

    monkeypatch.setattr(winreg, "OpenKey", lambda *_a, **_k: object())
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda *_a, **_k: (1, winreg.REG_DWORD))
    monkeypatch.setattr(winreg, "SetValueEx", lambda *_a, **_k: None)
    monkeypatch.setattr(winreg, "CloseKey", lambda *_a, **_k: None)
    monkeypatch.setattr(cs, "_theme_is_dark", lambda: None)

    result = _run("dark_mode")

    assert "status=unverified" in result
    _no_done(result)


def test_a_theme_that_is_read_back_is_reported_as_done(monkeypatch):
    """El valor nuevo se lee antes de decir que el tema cambio."""
    import winreg

    written: list[str] = []

    monkeypatch.setattr(winreg, "OpenKey", lambda *_a, **_k: object())
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda *_a, **_k: (1, winreg.REG_DWORD))
    monkeypatch.setattr(winreg, "SetValueEx",
                        lambda name, *_a, **_k: written.append(name))
    monkeypatch.setattr(winreg, "CloseKey", lambda *_a, **_k: None)
    monkeypatch.setattr(cs, "_theme_is_dark", lambda: True)

    result = _run("dark_mode")

    assert "status=done" in result
    assert "verified=true" in result
    assert "theme=dark" in result
    assert written, "el registro no se toco"


# ── 3. Reinicio y apagado ───────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["restart", "shutdown"])
def test_a_refused_restart_is_a_failure(monkeypatch, action):
    """Windows devuelve un codigo; un codigo que no es cero es un fallo."""
    fake = _FakeSubprocess(_FakeCompleted(returncode=1115))
    monkeypatch.setattr(cs, "subprocess", fake)

    result = _confirmed(monkeypatch, action)

    assert "status=failed" in result
    _no_done(result)
    assert fake.calls, "el comando ni se lanzo"


@pytest.mark.parametrize("action", ["restart", "shutdown"])
def test_an_accepted_restart_is_reported_as_done(monkeypatch, action):
    """Un codigo de cero es la unica prueba de que Windows lo acepto."""
    fake = _FakeSubprocess(_FakeCompleted(returncode=0))
    monkeypatch.setattr(cs, "subprocess", fake)

    result = _confirmed(monkeypatch, action)

    assert "status=done" in result
    assert "verified=true" in result
    assert fake.calls


# ── 4. El WiFi ──────────────────────────────────────────────────────────────

def test_a_wifi_that_does_not_move_is_not_reported_as_done(monkeypatch):
    """`wifi_set` dijo que no; el resultado no puede ser "hecho"."""
    asked: list[bool] = []

    monkeypatch.setattr(cs, "wifi_state", lambda: "on")
    monkeypatch.setattr(cs, "wifi_set", lambda enabled: asked.append(enabled) or False)

    result = _confirmed(monkeypatch, "toggle_wifi")

    assert "status=failed" in result
    _no_done(result)
    assert asked == [False], "no se pidio el cambio de radio"


def test_a_wifi_that_cannot_be_read_back_is_unverified(monkeypatch):
    """Cambiado, pero sin lectura de vuelta: no se afirma nada."""
    monkeypatch.setattr(cs, "wifi_state", lambda: "on")
    monkeypatch.setattr(cs, "wifi_set", lambda enabled: True)

    result = _confirmed(monkeypatch, "toggle_wifi")

    assert "status=unverified" in result
    _no_done(result)


def test_a_wifi_that_is_read_back_is_reported_as_done(monkeypatch):
    """Cambiado Y leido: eso sí se puede decir."""
    states = ["on", "off"]
    monkeypatch.setattr(cs, "wifi_state", lambda: states.pop(0))
    monkeypatch.setattr(cs, "wifi_set", lambda enabled: True)

    result = _confirmed(monkeypatch, "toggle_wifi")

    assert "status=done" in result
    assert "verified=true" in result


# ── 5. La confirmacion ──────────────────────────────────────────────────────

def test_the_confirmation_does_not_report_success_before_the_action_runs(monkeypatch):
    """El boton de la pantalla es lo unico que autoriza; nada mas."""
    performed: list[str] = []
    captured: dict = {}

    def _fake_request(key, title, detail, run, voice_handler=None):
        captured["run"] = run
        return "[CONFIRMATION_PENDING] waiting"

    monkeypatch.setattr(cs.confirm, "request", _fake_request)
    monkeypatch.setattr(cs.confirm, "pending_title", lambda: "")
    monkeypatch.setattr(cs, "wifi_set",
                        lambda enabled: performed.append("wifi") or False)

    result = _run("toggle_wifi")

    assert "[CONFIRMATION_PENDING]" in result
    assert performed == [], "la accion ocurrio antes de la confirmacion"
    assert "done" not in result.lower().replace("not confirmed", "")


def test_the_confirmed_action_reports_what_it_actually_did(monkeypatch):
    """El callback ya no escribe "<accion> hecho": pregunta a la accion."""
    captured: dict = {}

    def _fake_request(key, title, detail, run, voice_handler=None):
        captured["run"] = run
        return "[CONFIRMATION_PENDING] waiting"

    monkeypatch.setattr(cs.confirm, "request", _fake_request)
    monkeypatch.setattr(cs.confirm, "pending_title", lambda: "")
    monkeypatch.setattr(cs, "wifi_state", lambda: "on")
    monkeypatch.setattr(cs, "wifi_set", lambda enabled: False)

    _run("toggle_wifi")
    outcome = captured["run"]()

    assert "status=failed" in outcome
    assert "toggle_wifi done." not in outcome


# ── 6. La energia ───────────────────────────────────────────────────────────

def test_the_energy_action_reports_the_reading_it_actually_got(monkeypatch):
    """Una lectura real: porcentaje, carga y plan de energia."""
    monkeypatch.setattr(cs, "_battery_reading", lambda: {
        "present": True, "percent": 42, "charging": True, "on_ac": False})
    monkeypatch.setattr(cs, "_power_scheme", lambda: ("381b4222", "Balanced"))

    result = _run("energy_status")

    assert "status=done" in result
    assert "battery_percent=42" in result
    assert "charging=yes" in result
    assert "power_scheme=Balanced" in result
    assert "energy_saver=off" in result


def test_a_computer_without_a_battery_says_so_and_invents_no_percentage(monkeypatch):
    """Un escritorio sin bateria: se dice, y no sale ningun porcentaje."""
    monkeypatch.setattr(cs, "_battery_reading", lambda: {
        "present": False, "percent": None, "charging": None, "on_ac": True})
    monkeypatch.setattr(cs, "_power_scheme", lambda: ("381b4222", "Balanced"))

    result = _run("energy_status")

    assert "status=done" in result
    assert "battery=" in result
    assert "no battery" in result
    assert "battery_percent" not in result
    assert "%" not in result


def test_an_unreadable_battery_is_unverified_and_never_a_number(monkeypatch):
    """Si Windows no contesta, no hay porcentaje: hay una respuesta honesta."""
    monkeypatch.setattr(cs, "_battery_reading", lambda: None)
    monkeypatch.setattr(cs, "_power_scheme", lambda: ("381b4222", "Balanced"))

    result = _run("energy_status")

    assert "status=unverified" in result
    assert "battery_percent" not in result
    _no_done(result)


def test_an_unknown_battery_level_is_unverified(monkeypatch):
    """Hay bateria, pero el nivel es desconocido: no se inventa."""
    monkeypatch.setattr(cs, "_battery_reading", lambda: {
        "present": True, "percent": None, "charging": None, "on_ac": None})
    monkeypatch.setattr(cs, "_power_scheme", lambda: ("381b4222", "Balanced"))

    result = _run("energy_status")

    assert "status=unverified" in result
    assert "battery_percent" not in result


def test_a_machine_that_says_nothing_about_energy_is_a_failure(monkeypatch):
    """Ni bateria ni plan: no hay nada que decir, y se dice."""
    monkeypatch.setattr(cs, "_battery_reading", lambda: None)
    monkeypatch.setattr(cs, "_power_scheme", lambda: None)

    result = _run("energy_status")

    assert "status=failed" in result
    assert "battery_percent" not in result


def test_the_energy_action_is_registered_and_reachable():
    """El nombre vive en el vocabulario cerrado y en la declaracion."""
    from core.operators.base import ALLOWED_ACTIONS, parse_action

    assert "energy_status" in ALLOWED_ACTIONS
    assert parse_action({"action": "energy_status"}).action == "energy_status"
    assert "energy_status" in (
        cs.TOOL["parameters"]["properties"]["action"]["description"])


def test_the_energy_action_is_reachable_through_the_operator_registry():
    """El operador del sistema ofrece la herramienta que lo lee."""
    from core.action_loader import discover_actions
    from core.operators import OperatorRegistry

    actions = discover_actions(BASE / "actions", logger=lambda m: None,
                                eager=False)
    registry = OperatorRegistry.from_registries(action_registry=actions,
                                                 logger=lambda m: None)

    operator = registry.operator_for("computer_settings")
    assert operator is not None
    assert operator.name == "system"
    declaration = next(d for d in registry.declarations()
                       if d["name"] == "computer_settings")
    assert "energy_status" in declaration["description"]


# ── 7. El resto del modulo ─────────────────────────────────────────────────

def test_a_keystroke_is_not_reported_as_a_verified_effect(monkeypatch):
    """Una tecla enviada a lo que tuviera el foco no se puede comprobar."""
    monkeypatch.setattr(cs, "pyautogui", type("_P", (), {
        "press": staticmethod(lambda *_a, **_k: None),
        "hotkey": staticmethod(lambda *_a, **_k: None),
        "scroll": staticmethod(lambda *_a, **_k: None),
    })())

    result = _run("copy")

    assert "status=unverified" in result
    _no_done(result)


def test_no_action_of_this_module_claims_a_verified_effect_it_never_read(monkeypatch):
    """Una barrida: ninguna de estas acciones puede decir "hecho y verificado"."""
    import winreg

    monkeypatch.setattr(cs, "_send_monitor_power_off", lambda: None)
    monkeypatch.setattr(cs, "_display_power_state", lambda: None)
    monkeypatch.setattr(winreg, "OpenKey", lambda *_a, **_k: object())
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda *_a, **_k: (1, winreg.REG_DWORD))
    monkeypatch.setattr(winreg, "SetValueEx", lambda *_a, **_k: None)
    monkeypatch.setattr(winreg, "CloseKey", lambda *_a, **_k: None)
    monkeypatch.setattr(cs, "_theme_is_dark", lambda: None)
    monkeypatch.setattr(cs, "pyautogui", type("_P", (), {
        "press": staticmethod(lambda *_a, **_k: None),
        "hotkey": staticmethod(lambda *_a, **_k: None),
        "scroll": staticmethod(lambda *_a, **_k: None),
    })())

    for action in ("sleep_display", "screen_off", "dark_mode", "copy",
                   "scroll_up", "paste", "lock_screen", "open_run", "zoom_in"):
        result = _run(action)
        assert "status=done" not in result, f"{action}: {result}"
        assert "verified=true" not in result, f"{action}: {result}"
