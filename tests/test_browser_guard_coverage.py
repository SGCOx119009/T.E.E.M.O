"""FASE 5 - la guarda de rutas debe estar en TODOS los motores y comprobar
las DOS mitades de la politica.

Dos defectos concretos quedan cubiertos aqui:

  1. la guarda solo se instalaba en las ramas de Chromium, de modo que una
     redireccion en Firefox o en WebKit nunca se volvia a comprobar;
  2. la guarda solo preguntaba por el DESTINO (el nombre), de modo que una
     redireccion a una direccion literal de loopback, de red local o
     link-local pasaba sin que nadie la mirara.

Nada aqui abre un navegador y nada aqui toca la red: las ramas de motor se
ejercitan con un Playwright falso, y las direcciones usadas son literales,
que no necesitan un resolvedor. Los nombres que aparecen (example.test,
bank.example) se rechazan o se permiten por su NOMBRE, sin resolverse nunca.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from actions import browser_control
from core import policy as domain_policy


# ── Lo que hace falta para ejercitar una guarda sin abrir un navegador ─────

class _Route:
    """Una peticion que el motor de enrutamiento entrega a la guarda."""

    def __init__(self, url: str):
        self.request = type("R", (), {"url": url})()
        self.aborted = False
        self.continued = False

    async def abort(self):
        self.aborted = True

    async def continue_(self):
        self.continued = True


class _Context:
    """Un contexto de navegador falso: solo sabe recibir rutas."""

    def __init__(self, *, fail_route: bool = False):
        self.fail_route = fail_route
        self.patterns: list[str] = []
        self.handlers: list = []
        self.pages = [type("P", (), {"url": "about:blank"})()]

    async def route(self, pattern, handler):
        if self.fail_route:
            raise RuntimeError("this engine cannot intercept requests")
        self.patterns.append(pattern)
        self.handlers.append(handler)


class _Engine:
    """Un motor de Playwright falso que devuelve un contexto falso."""

    def __init__(self, context):
        self._context = context
        self.launched: list = []

    async def launch_persistent_context(self, profile, **kwargs):
        self.launched.append((profile, kwargs))
        return self._context


class _Playwright:
    """Un Playwright falso con los motores que cada rama necesita."""

    def __init__(self, **engines):
        for name, context in engines.items():
            setattr(self, name, _Engine(context))


def _session():
    """Una sesion sin navegador detras, como la que usan las otras pruebas."""
    session = browser_control._BrowserSession.__new__(browser_control._BrowserSession)
    session._context = None
    session._page = None
    session._blocked_by_policy = None
    session._guard_installed = False
    session._guard_failed = False
    session.profile_kind = ""
    return session


def _install_guard(session):
    """Instala la guarda en un contexto falso y devuelve su manejador."""
    context = _Context()
    session._context = context
    asyncio.run(session._install_route_guard())
    if not context.handlers:
        raise AssertionError("the guard was not installed")
    return context.handlers[0]


def _launch_session(engine: str, context):
    """Una sesion lista para lanzar un motor concreto."""
    session = _session()
    session.browser_name = engine
    session._spec = {"engine": engine, "exe": None, "channel": None}
    session._pw = _Playwright(**{engine: context})
    return session


# ── 1. La guarda esta en cada motor ─────────────────────────────────────────

class GuardOnEveryEngineTests(unittest.TestCase):
    """La politica es del CONTEXTO, no del motor: todos la llevan."""

    def test_the_firefox_branch_installs_the_guard(self) -> None:
        """Firefox no es la excepcion: una redireccion ahi tambien se comprueba.

        La rama se ejercita con un motor falso, asi que la prueba no abre
        un navegador real.
        """
        context = _Context()
        session = _launch_session("firefox", context)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(browser_control, "_firefox_profile_dir",
                          return_value=str(Path(tmp) / "firefox")), \
             patch("pathlib.Path.home", return_value=Path(tmp)):
            asyncio.run(session._launch())

        self.assertIn("**/*", context.patterns,
                      "the guard must be installed on the Firefox branch")
        self.assertEqual(len(context.handlers), 1)
        self.assertTrue(session._guard_installed)

        # Y lo instalado es la guarda de la politica, no cualquier ruta.
        route = _Route("https://www.bankofexample.com/login")
        asyncio.run(context.handlers[0](route))
        self.assertTrue(route.aborted,
                        "the installed guard must refuse a denied category")

    def test_the_webkit_branch_installs_the_guard(self) -> None:
        """Igual que Firefox: Safari/WebKit tambien comprueba cada redireccion."""
        context = _Context()
        session = _launch_session("webkit", context)
        with tempfile.TemporaryDirectory() as tmp, \
             patch("pathlib.Path.home", return_value=Path(tmp)):
            asyncio.run(session._launch())

        self.assertIn("**/*", context.patterns,
                      "the guard must be installed on the WebKit branch")
        self.assertEqual(len(context.handlers), 1)
        self.assertTrue(session._guard_installed)

        route = _Route("https://www.bankofexample.com/login")
        asyncio.run(context.handlers[0](route))
        self.assertTrue(route.aborted,
                        "the installed guard must refuse a denied category")

    def test_every_engine_branch_of_the_launch_installs_the_guard(self) -> None:
        """Ninguna rama de motor puede quedarse sin guarda.

        Se lee el codigo de `_launch` y se comprueba que CADA rama de motor
        instala la guarda: una rama nueva que se anada sin ella volveria a
        abrir un hole, y el hole es el defecto que se esta corrigiendo.
        """
        source = inspect.getsource(browser_control._BrowserSession._launch)

        def _branch(marker: str, until: str) -> str:
            self.assertIn(marker, source, f"the branch {marker} is gone")
            self.assertIn(until, source, f"the branch {marker} lost its end")
            start = source.index(marker)
            end = source.index(until, start)
            return source[start:end]

        firefox = _branch('if engine_name == "firefox"',
                          'if engine_name == "webkit"')
        webkit = _branch('if engine_name == "webkit"',
                         "profile = _real_profile_dir(")

        self.assertIn("_install_route_guard()", firefox,
                      "the Firefox branch installs no guard")
        self.assertIn("_install_route_guard()", webkit,
                      "the WebKit branch installs no guard")

    def test_an_engine_that_cannot_be_guarded_is_refused(self) -> None:
        """Un motor que no puede interceptar peticiones no se usa.

        Un navegador sin guarda no tiene politica: es peor que no tener
        navegador, porque parece que la politica sigue ahi.
        """
        context = _Context(fail_route=True)
        session = _launch_session("firefox", context)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(browser_control, "_firefox_profile_dir",
                          return_value=str(Path(tmp) / "firefox")), \
             patch("pathlib.Path.home", return_value=Path(tmp)):
            with self.assertRaises(browser_control._GuardUnavailable):
                asyncio.run(session._launch())

        self.assertTrue(session._guard_failed)
        self.assertFalse(session._guard_installed)

    def test_a_navigation_is_refused_when_the_browser_cannot_be_guarded(self) -> None:
        """La respuesta dice QUAL fue la politica, no solo que algo fallo."""
        session = _session()

        async def _get_page(**_kwargs):
            raise browser_control._GuardUnavailable(
                "the browser could not be guarded by the domain policy"
            )

        session._get_page = _get_page
        result = asyncio.run(session.go_to("http://8.8.8.8/"))

        self.assertIn("status=failed", result)
        self.assertIn("policy=deny:guard_unavailable", result)


# ── 2. La redireccion se comprueba por nombre Y por direccion ──────────────

class RedirectAddressTests(unittest.TestCase):
    """Una redireccion a una direccion prohibida se para."""

    def test_a_redirect_to_a_literal_loopback_address_is_refused(self) -> None:
        for url in ("http://127.0.0.1:8080/", "http://[::1]:8080/"):
            with self.subTest(url=url):
                session = _session()
                handler = _install_guard(session)
                route = _Route(url)

                asyncio.run(handler(route))

                self.assertTrue(route.aborted, f"{url} must be refused")
                self.assertFalse(route.continued)
                self.assertIsNotNone(session._blocked_by_policy)
                self.assertEqual(session._blocked_by_policy.policy,
                                 "deny:private_address")

    def test_a_redirect_to_a_literal_lan_address_is_refused(self) -> None:
        session = _session()
        handler = _install_guard(session)
        route = _Route("http://192.168.1.1/")

        asyncio.run(handler(route))

        self.assertTrue(route.aborted, "a LAN address must be refused")
        self.assertFalse(route.continued)
        self.assertEqual(session._blocked_by_policy.policy,
                         "deny:private_address")

    def test_a_redirect_to_a_literal_link_local_address_is_refused(self) -> None:
        """La direccion de metadatos de la nube es link-local: se para igual."""
        session = _session()
        handler = _install_guard(session)
        route = _Route("http://169.254.169.254/")

        asyncio.run(handler(route))

        self.assertTrue(route.aborted, "a link-local address must be refused")
        self.assertFalse(route.continued)
        self.assertEqual(session._blocked_by_policy.policy,
                         "deny:private_address")

    def test_a_redirect_to_a_denied_category_is_still_refused(self) -> None:
        """Lo de antes sigue valiendo: un banco es un banco por donde se llegue."""
        session = _session()
        handler = _install_guard(session)
        route = _Route("https://www.bankofexample.com/login")

        asyncio.run(handler(route))

        self.assertTrue(route.aborted)
        self.assertFalse(route.continued)
        self.assertEqual(session._blocked_by_policy.policy, "deny:banking")

    def test_an_allowed_destination_still_passes(self) -> None:
        """La guarda no cierra la puerta: una pagina corriente sigue pasando."""
        session = _session()
        handler = _install_guard(session)
        route = _Route("http://8.8.8.8/")

        asyncio.run(handler(route))

        self.assertFalse(route.aborted, "an allowed destination must pass")
        self.assertTrue(route.continued)

    def test_the_guard_runs_the_address_check_on_every_request(self) -> None:
        """La guarda no pregunta solo por el destino: pregunta por la direccion.

        Se mira la llamada, no el resultado: una guarda que solo mirase el
        nombre dejaria pasar una redireccion a un loopback.
        """
        session = _session()
        handler = _install_guard(session)
        seen: list[str] = []
        real = domain_policy.check_address

        def _spy(url, **kwargs):
            seen.append(url)
            return real(url, **kwargs)

        with patch.object(domain_policy, "check_address", _spy):
            asyncio.run(handler(_Route("http://8.8.8.8/")))

        self.assertEqual(seen, ["http://8.8.8.8/"],
                         "the address check must run on every request")

    def test_the_initial_request_and_the_redirect_use_the_same_checks(self) -> None:
        """La direccion que pidio el usuario y la que chose el sitio se
        comprueban igual.

        Una redireccion a un loopback se para, y la misma direccion pedida
        de entrada se para con la misma politica.
        """
        for url in ("http://127.0.0.1:8080/", "http://192.168.1.1/",
                    "http://169.254.169.254/", "https://www.bankofexample.com/x"):
            with self.subTest(url=url):
                decision = domain_policy.check_request(url)
                self.assertFalse(decision.allowed)

                session = _session()
                handler = _install_guard(session)
                route = _Route(url)
                asyncio.run(handler(route))
                self.assertTrue(route.aborted)
                self.assertEqual(session._blocked_by_policy.policy,
                                 decision.policy)


# ── 3. La lista blanca endurece, nunca abre ────────────────────────────────

class AllowListStillOnlyHardensTests(unittest.TestCase):
    """La lista blanca del usuario es un filtro, no una llave."""

    def test_the_allow_list_does_not_re_open_a_denied_destination(self) -> None:
        with patch.object(domain_policy, "allowed_domains",
                          return_value=["bank.example"]):
            decision = domain_policy.check_request("https://bank.example/x")
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.policy, "deny:banking")

    def test_the_allow_list_does_not_re_open_a_private_address(self) -> None:
        """Ni siquiera una direccion de la red local se abre por estar en la
        lista: la lista es del usuario, la red privada no."""
        with patch.object(domain_policy, "allowed_domains",
                          return_value=["192.168.1.1"]):
            decision = domain_policy.check_request("http://192.168.1.1/")
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.policy, "deny:private_address")

    def test_the_allow_list_still_restricts_to_the_authorised_domains(self) -> None:
        with patch.object(domain_policy, "allowed_domains",
                          return_value=["example.test"]):
            self.assertTrue(
                domain_policy.check_request("https://example.test/x").allowed)
            other = domain_policy.check_request("https://somewhere.test/x")
            self.assertFalse(other.allowed)
            self.assertEqual(other.policy, "allowlist")

    def test_a_redirect_through_a_denied_destination_stays_refused(self) -> None:
        """Con una lista blanca puesta, la guarda sigue parando lo prohibido."""
        with patch.object(domain_policy, "allowed_domains",
                          return_value=["example.test"]):
            session = _session()
            handler = _install_guard(session)

            allowed = _Route("https://example.test/x")
            asyncio.run(handler(allowed))
            self.assertTrue(allowed.continued)

            denied = _Route("https://www.bankofexample.com/login")
            asyncio.run(handler(denied))
            self.assertTrue(denied.aborted)
            self.assertEqual(session._blocked_by_policy.policy, "deny:banking")


if __name__ == "__main__":
    unittest.main()
