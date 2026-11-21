"""
core/operators/browser.py — the operator for the web.

THE WEB IS A PLACE, NOT A TAB
    Everything the assistant does on the web — navigating, reading a page,
    searching, filling a form, joining a meeting — is one domain, and it is
    the domain that the grounded operators of a later phase will speak. The
    DOM-aware operator replaces the coordinate-driven one from here; the
    grouping already exists so that replacing it changes one module.

WHAT IT OWNS
    The browser itself, the searches that go with it, and the two tools that
    are a web read dressed as something else: the weather and the flights.

WHAT IT DOES NOT OWN
    Video is media, not browser: playing a YouTube video in the HUD is a
    different thing from driving a browser, and the two tools used to claim
    each other's job — which is the bug the video work fixed. They are
    separated here for the same reason.
"""
from __future__ import annotations

from core.operators.base import Operator


class BrowserOperator(Operator):
    name = "browser"
    description = (
        "The web: browsing pages, searching, reading what a page says, "
        "and the web-backed lookups."
    )
    context = "browser"
    risk_class = "high"
    tools = (
        "browser_control",
        "web_search",
        "weather_report",
        "flight_finder",
        "meet_control",
    )
