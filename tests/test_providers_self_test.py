"""
El autotest de `providers/`, VISIBLE para la suite del proyecto.

POR QUE VIVE EN SU CARPETA Y NO EN `tests/`
    `providers/` es opcional: borrarlo deberia llevarselo todo, incluido
    el fichero que lo comprueba. Un directorio de pruebas en la raiz
    apuntaria a codigo que ya no esta. Ese razonamiento es correcto, y por
    eso el fichero se queda donde esta.

EL PROBLEMA QUE ESTO RESUELVE
    Precisamente por ahi, la suite del proyecto NUNCA lo recogia: `pytest
    tests` no mira `providers/`. Eight fallos de un autotest que el propio
    repositorio proclaimaba eran invisibles, y una prueba invisible no es
    una prueba: es una intencion.

    Este fichero lo ejecuta desde la suite, sin mover el original, y
    comprueba el resultado DE VERDAD: el autotest no lanza, pero tampoco
    aborta: cuenta sus fallos en una lista global. Un wrapper que solo
    ejecutara las funciones y no mirara ese contador PASSARIA con todo
    roto, que es peor que no mirar nada.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

providers_tests = pytest.importorskip("providers._tests",
                                      reason="the optional providers folder is not installed")

# The self-test's own functions, in the order its own runner uses them.
_CASES = [name for name in dir(providers_tests)
          if name.startswith("test_") and callable(getattr(providers_tests, name))]


def _failures() -> list[str]:
    return list(getattr(providers_tests, "_failures", []))


@pytest.mark.parametrize("name", _CASES)
def test_the_provider_self_test_passes(name):
    """Each check of the self-test, run for real and checked for real."""
    before = _failures()
    function = getattr(providers_tests, name)
    try:
        if inspect.iscoroutinefunction(function):
            asyncio.run(function())
        else:
            function()
    except Exception as exc:
        pytest.fail(f"{name} raised {type(exc).__name__}: {exc}")

    after = _failures()
    new = [f for f in after if f not in before]
    assert not new, f"{name} failed these checks: {new}"


def test_the_self_test_actually_has_cases():
    """A wrapper that runs nothing would pass forever."""
    assert len(_CASES) >= 15, f"only {len(_CASES)} cases found"


def test_the_self_test_is_runnable_on_its_own():
    """It is also a program: `python -m providers._tests` must work."""
    assert callable(getattr(providers_tests, "main", None))
