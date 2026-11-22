"""
El AGUERO QUE ESTAS PRUEBAS CIERRAN
    "system_status" no decia la temperatura, no decia cuales eran los tres
    programas que mas RAM y CPU estaban usando, y no decia a que red Wi-Fi
    estaba conectado el equipo. El motivo no era que el dato no existiera:
    era que nadie lo leia, y una asercion que nunca se comprueba no informa
    de nada — ni del dato ni de su ausencia.

    Asi que estas pruebas comprueban tres cosas: que el dato se LEE, que
    cuando no se puede leer se dice con un motivo (nunca un cero, nunca un
    numero inventado), y que la red Wi-Fi se distingue de "no conectada" y
    de "desconocida".

    Los datos de netsh se leen del formato REAL de Windows —en ingles y en
    castellano— porque el label es la parte que cambia con el idioma del
    equipo y el BSSID se parece tanto al SSID que un parser ingenuo devuelve
    la direccion MAC del punto de acceso como si fuera el nombre de la red.
"""
from __future__ import annotations

import os
import sys
import types
from types import SimpleNamespace

import psutil
import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from actions import system_monitor as sm  # noqa: E402


# ── Fakes ───────────────────────────────────────────────────────────────────

class _FakeProc:
    """Un proceso que contesta, o que muere justo al preguntarle."""

    def __init__(self, pid, name, rss_mb=100.0, cpu=0.0, die_on=None):
        self.pid = pid
        self._name = name
        self._rss = int(rss_mb * 1024 * 1024)
        self._cpu = float(cpu)
        self._die_on = die_on

    def name(self):
        if self._die_on == "name":
            raise psutil.NoSuchProcess(self.pid)
        return self._name

    def memory_info(self):
        if self._die_on == "memory":
            raise psutil.NoSuchProcess(self.pid)
        return SimpleNamespace(rss=self._rss)

    def cpu_percent(self, interval=None):
        if self._die_on == "cpu":
            raise psutil.AccessDenied(self.pid)
        return self._cpu


def _install_processes(monkeypatch, procs):
    """La lista de procesos que vera el monitor, y sin esperas de verdad."""
    monkeypatch.setattr(sm.psutil, "process_iter", lambda *a, **k: list(procs))
    monkeypatch.setattr(sm.time, "sleep", lambda seconds: None)


def _refuse_wmi(monkeypatch):
    """El modulo `wmi` no debe tocar el equipo real desde una prueba."""
    fake = types.ModuleType("wmi")

    class _W:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("the WMI query was refused")

    fake.WMI = _W
    monkeypatch.setitem(sys.modules, "wmi", fake)


# Salidas REALES de `netsh wlan show interfaces`.

_ENGLISH_NETSH = """
Interface name: Wi-Fi
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wireless-AC 9560
    GUID                   : 0a1b2c3d-0000-0000-0000-000000000000
    Physical address       : a4:b1:b2:c3:d4:e5
    State                  : connected
    SSID                   : MyHomeNet
    BSSID                  : 90:f6:52:d7:6b:ce
    Network type           : Infrastructure
    Radio type             : 802.11n
    Authentication         : WPA2-Personal
    Signal                 : 99%
    Profile                : MyHomeNet
"""

_SPANISH_NETSH = """
Hay 1 interfaz en el sistema:

    Nombre                 : Wi-Fi
    Descripción            : Realtek 8821CE Wireless LAN 802.11ac PCI-E NIC
    Dirección       : 20:4e:f6:93:0b:ad
    Estado                 : conectado
    SSID                   : SG:CO
    AP BSSID               : 90:f6:52:d7:6b:ce
    Banda                  : 2,4 GHz
    Perfil                 : SG:CO
"""

_SPANISH_WITHOUT_SSID = """
Hay 1 interfaz en el sistema:

    Nombre                 : Wi-Fi
    Descripción            : Realtek 8821CE Wireless LAN 802.11ac PCI-E NIC
    Estado                 : conectado
    Red                    : MiRed
    Perfil                 : MiRed
"""


# ── La temperatura ──────────────────────────────────────────────────────────

def test_no_temperature_is_invented_when_the_sensor_call_raises(monkeypatch):
    """Si el sensor falla, no hay temperatura. Y no hay un cero."""
    def _boom(*args, **kwargs):
        raise RuntimeError("the driver says no")

    monkeypatch.setattr(sm.psutil, "sensors_temperatures", _boom, raising=False)
    monkeypatch.setattr(sm, "_temp_from_wmi", lambda: (None, "the WMI query was refused"))

    value, reason = sm._read_cpu_temp()
    assert value is None
    assert reason, "un motivo es obligatorio: lo que no se dice, se inventa"
    assert sm._get_cpu_temp() is None


def test_a_raw_wmi_value_is_converted_to_celsius():
    """WMI da decimas de grado Kelvin: 3231 -> 323.1 K -> unos 50 °C."""
    celsius = sm._kelvin_tenths_to_celsius(3231)
    assert celsius == pytest.approx(49.95, abs=0.01)
    assert 49.5 <= celsius <= 50.5, "unos 50 grados, que es lo que dice el dato"


def test_a_raw_wmi_value_of_zero_is_not_zero_degrees(monkeypatch):
    """Un cero aqui es la consulta fallando, no cero grados."""
    assert sm._kelvin_tenths_to_celsius(0) is None
    assert sm._kelvin_tenths_to_celsius(-1) is None
    assert sm._kelvin_tenths_to_celsius("not a number") is None

    _refuse_wmi(monkeypatch)
    monkeypatch.setattr(sm, "_temp_from_sensors", lambda: (None, "psutil has no sensors"))
    monkeypatch.setattr(sm, "_run_hidden", lambda command, timeout=12.0: "CurrentTemperature\n0\n")

    value, reason = sm._read_cpu_temp()
    assert value is None, "un cero no es una temperatura"
    assert reason


# ── Los procesos que mas RAM y mas CPU usan ────────────────────────────────

def test_the_top_ram_list_has_at_most_three_entries_with_the_expected_keys(monkeypatch):
    _install_processes(monkeypatch, [
        _FakeProc(4001, "alpha.exe", 900.0, 10.0),
        _FakeProc(4002, "beta.exe", 500.0, 20.0),
        _FakeProc(4003, "gamma.exe", 100.0, 30.0),
        _FakeProc(4004, "delta.exe", 50.0, 40.0),
    ])

    top = sm._top_ram_processes()
    assert len(top) == 3
    for entry in top:
        assert set(entry) == {"name", "memory_mb", "cpu_percent"}
        assert isinstance(entry["name"], str)
        assert isinstance(entry["memory_mb"], float)
        assert isinstance(entry["cpu_percent"], float)


def test_the_top_ram_list_is_sorted_by_memory(monkeypatch):
    _install_processes(monkeypatch, [
        _FakeProc(4001, "small.exe", 10.0, 90.0),
        _FakeProc(4002, "big.exe", 900.0, 1.0),
        _FakeProc(4003, "medium.exe", 300.0, 50.0),
    ])

    top = sm._top_ram_processes()
    memory = [entry["memory_mb"] for entry in top]
    assert memory == sorted(memory, reverse=True)
    assert top[0]["name"] == "big.exe"


def test_the_top_cpu_list_is_sorted_by_cpu(monkeypatch):
    _install_processes(monkeypatch, [
        _FakeProc(4001, "idle.exe", 900.0, 1.0),
        _FakeProc(4002, "busy.exe", 10.0, 88.0),
        _FakeProc(4003, "middle.exe", 100.0, 40.0),
    ])

    top = sm._top_cpu_processes()
    cpu = [entry["cpu_percent"] for entry in top]
    assert cpu == sorted(cpu, reverse=True)
    assert top[0]["name"] == "busy.exe"


def test_a_process_that_dies_while_being_read_is_skipped(monkeypatch):
    """Un proceso puede morir entre la lista y la pregunta. Se salta."""
    _install_processes(monkeypatch, [
        _FakeProc(4001, "alive.exe", 300.0, 5.0),
        _FakeProc(4002, "dead.exe", 900.0, 50.0, die_on="memory"),
        _FakeProc(4003, "blocked.exe", 800.0, 40.0, die_on="cpu"),
    ])

    names = [entry["name"] for entry in sm._top_ram_processes()]
    assert "dead.exe" not in names
    assert "blocked.exe" not in names
    assert "alive.exe" in names


def test_the_assistant_itself_is_not_in_the_list(monkeypatch):
    """Al usuario no le interesa la memoria que gasta el propio asistente."""
    _install_processes(monkeypatch, [
        _FakeProc(os.getpid(), os.path.basename(sys.executable), 900.0, 99.0),
        _FakeProc(4001, "alpha.exe", 100.0, 5.0),
    ])

    names = [entry["name"] for entry in sm._top_ram_processes()]
    assert names == ["alpha.exe"]


# ── La red Wi-Fi ───────────────────────────────────────────────────────────

def test_the_english_netsh_output_gives_the_ssid_and_never_the_bssid():
    """El BSSID es la direccion MAC del punto de acceso, no el nombre."""
    ssid, reason = sm._parse_wifi_ssid(_ENGLISH_NETSH)
    assert ssid == "MyHomeNet"
    assert reason == ""
    assert "90:f6" not in (ssid or "")


def test_the_spanish_netsh_output_gives_the_ssid_and_not_the_bssid():
    """En castellano el label del BSSID es "AP BSSID": tambien se excluye."""
    ssid, reason = sm._parse_wifi_ssid(_SPANISH_NETSH)
    assert ssid == "SG:CO"
    assert reason == ""
    assert "90:f6" not in (ssid or "")


def test_the_spanish_output_without_an_ssid_line_is_not_guessed():
    """Un "Red : MiRed" no es el SSID. Se dice que no se pudo leer."""
    ssid, reason = sm._parse_wifi_ssid(_SPANISH_WITHOUT_SSID)
    assert ssid is None
    assert reason, "sin motivo no se puede decir lo que no se sabe"
    assert "MiRed" not in (reason or "")


def test_netsh_output_that_is_garbage_is_unknown_and_does_not_raise():
    ssid, reason = sm._parse_wifi_ssid("### nada de esto lo escribio netsh ###")
    assert ssid is None
    assert reason


def test_a_machine_with_no_wireless_adapter_says_so():
    """Sin adaptador NO es lo mismo que sin nombre."""
    ssid, reason = sm._parse_wifi_ssid(
        "There is no wireless interface on the system.")
    assert ssid is None
    assert "adapter" in reason.lower()

    ssid, reason = sm._parse_wifi_ssid(
        "No hay interfaces wireless en el sistema.")
    assert ssid is None
    assert "adapter" in reason.lower()


def test_a_disconnected_machine_is_not_the_same_as_an_unknown_one():
    """Tres hechos distintos: conectada, desconectada y desconocida."""
    ssid, reason = sm._parse_wifi_ssid("    State                  : disconnected\n")
    assert ssid is None
    assert "not connected" in reason.lower()

    ssid, reason = sm._parse_wifi_ssid("    Estado                 : desconectado\n")
    assert ssid is None
    assert "not connected" in reason.lower()

    ssid, reason = sm._parse_wifi_ssid("nada util aqui")
    assert ssid is None
    assert "not connected" not in reason.lower()


# ── El resultado completo ──────────────────────────────────────────────────

def test_get_system_status_always_reports_the_four_facts(monkeypatch):
    """Ninguna clave desaparece: o hay valor, o hay un motivo."""
    _install_processes(monkeypatch, [
        _FakeProc(4001, "alpha.exe", 300.0, 5.0),
        _FakeProc(4002, "beta.exe", 200.0, 15.0),
    ])
    monkeypatch.setattr(sm, "_read_cpu_temp",
                        lambda: (None, "the thermal zone is not readable here"))
    monkeypatch.setattr(sm, "_read_wifi_ssid",
                        lambda: (None, "netsh did not report a wireless network"))

    status = sm.get_system_status()

    for key in ("temperature_c", "top_cpu_processes", "top_ram_processes",
                "wifi_ssid"):
        assert key in status, f"falta la clave {key}"
    assert status["temperature_c"] is None
    assert status["temperature_note"]
    assert status["wifi_ssid"] is None
    assert status["wifi_note"]
    assert status["top_ram_processes"], "los procesos si se pudieron leer"
    assert status["top_cpu_processes"]
    assert "temperature" in status["unavailable"]
    assert "wifi_ssid" in status["unavailable"]
    assert "top_ram_processes" not in status["unavailable"]
    assert status["status"] == "done"
    assert status["verified"] is True


def test_a_read_temperature_is_reported_as_a_number(monkeypatch):
    _install_processes(monkeypatch, [_FakeProc(4001, "alpha.exe", 300.0, 5.0)])
    monkeypatch.setattr(sm, "_read_cpu_temp", lambda: (52.34, ""))
    monkeypatch.setattr(sm, "_read_wifi_ssid", lambda: ("MyHomeNet", ""))

    status = sm.get_system_status()
    assert status["temperature_c"] == pytest.approx(52.3, abs=0.05)
    assert status["wifi_ssid"] == "MyHomeNet"
    assert status["unavailable"] == []


def test_no_process_at_all_is_reported_as_unavailable(monkeypatch):
    """Una lista vacia sin motivo parece un equipo sin procesos."""
    _install_processes(monkeypatch, [])
    monkeypatch.setattr(sm, "_read_cpu_temp", lambda: (None, "no sensor"))
    monkeypatch.setattr(sm, "_read_wifi_ssid", lambda: (None, "no wifi"))

    status = sm.get_system_status()
    assert status["top_ram_processes"] == []
    assert status["top_cpu_processes"] == []
    assert status["top_ram_note"]
    assert "top_ram_processes" in status["unavailable"]


def test_the_description_the_model_reads_names_the_new_facts():
    """Sin esto, el modelo no pregunta por lo que la herramienta da."""
    desc = sm.TOOL_DESCRIPTION.lower()
    assert "temperature" in desc
    assert "ram" in desc
    assert "cpu" in desc
    assert "wi-fi" in desc
