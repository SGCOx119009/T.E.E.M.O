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

THE HYBRID, AND WHAT "HYBRID" MEANS HERE
    The browser is reached in whichever way is available, and both ways end
    in the same page:

    - OVER CDP, to the browser the user already has open. That browser has
      their session: signed in, with their tabs, with their settings. A
      second browser started by the assistant has none of that, and every
      page that needs a sign-in becomes a dead end.
    - LAUNCHED, when there is no running browser to attach to. It uses the
      user's own profile when that can be opened, and the assistant's own
      profile when it cannot — never the browser's live data directory.

    The debugging port is only ever on the loopback interface, and it is a
    constant rather than a setting: a debugging port on a public interface
    is the whole browser, for whoever finds it.

WHAT THE MODEL GETS: THE DOM, NOT A PICTURE
    A screenshot is a picture of pixels, and an action on a pixel is a guess
    that is wrong on every screen but this one. So the browser's default
    read is `read_page`, which returns the page's interactive elements as a
    numbered list — `[e7] button "Accept"` — which says what each thing IS
    and what it SAYS, at any resolution and on any screen.

    The screenshot is still there, for the questions a list cannot answer:
    what the page looks like, not what is on it.

THE POLICY IS PART OF THE OPERATOR, NOT A SETTING
    A browser that is signed into a bank can be told to open a bank. The
    deny list in `core/policy.py` says which pages are not the user's to
    command, it applies before the navigation and again on every redirect,
    and it is applied here rather than left to the caller — because a
    boundary that somebody has to remember to use is not a boundary.
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

    #: How the browser is reached. "cdp" attaches to the browser the user
    #: already has open, over the local debugging port; "launch" starts one.
    #: Both end in the same page, and the difference is only in the session.
    transport = ("cdp", "launch")

    #: What the model is given when it reads a page. "dom" means the
    #: structure — the elements and what they say. "screenshot" is still
    #: available for the questions the structure cannot answer, but it is
    #: not the default, because an action on a pixel is a guess.
    reads = "dom"

    #: The module that decides where the browser may go. It is named here
    #: so that the operator and the policy cannot drift apart: the operator
    #: is what the model sees, and the policy is what holds.
    policy_module = "core.policy"

    tools = (
        "browser_control",
        "web_search",
        "weather_report",
        "flight_finder",
        "meet_control",
    )
