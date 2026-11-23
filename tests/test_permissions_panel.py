"""
tests/test_permissions_panel.py — the permissions panel.

WHAT IS BEING PROVED
    1. The panel builds, even when there is no policy at all — and then it
       says so, instead of pretending the assistant may do nothing.
    2. With a policy, it lists the tools whose class is above READ, with
       the class written next to each one.
    3. Granting calls grant() with the tool and the expiry that was picked;
       revoking calls revoke().
    4. The master switch calls set_read_only().
    5. A policy that raises does not take the panel down with it.
    6. Every new key exists in the five languages, none of them is the
       English string, and they all carry the same placeholders.

WHY THE POLICY IS FAKE IN MOST OF THESE TESTS
    The policy is written by somebody else and may not even be there: it may
    be missing, it may be a module without the API in it, it may raise. The
    panel has to survive all three, so the tests put each of them in front
    of it on purpose.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import localization  # noqa: E402


# ── The keys this panel added ───────────────────────────────────────────────

NEW_KEYS = (
    "controls.permissions",
    "drawer.tool.permissions",
    "permissions.title",
    "permissions.explain",
    "permissions.read_only",
    "permissions.read_only_explain",
    "permissions.read_only_off_notice",
    "permissions.granted_title",
    "permissions.never",
    "permissions.expires_in",
    "permissions.close",
    "permissions.unavailable",
    "permissions.class.read",
    "permissions.class.write_local",
    "permissions.class.reversible_sys",
    "permissions.class.external",
    "permissions.class.irreversible",
    "permissions.class.credential",
    "permissions.class.privacy",
    "permissions.grant",
    "permissions.revoke",
    "permissions.expiry.forever",
    "permissions.expiry.hour",
)

#: The keys that are genuinely the same word in every language.
WHITELIST = {
    "app.name", "action.ok",
    "music.service_spotify", "music.service_youtube",
    "gesture.backend.mediapipe",
}


# ── The fake policy ─────────────────────────────────────────────────────────

class FakeGrant:
    """What `grants()` hands back: a tool, an action and a deadline."""

    def __init__(self, tool: str, action: str = "", expires_at=None):
        self.tool = tool
        self.action = action
        self.expires_at = expires_at


class FakePolicy:
    """A policy that behaves, and remembers what it was asked to do."""

    def __init__(self, tools=(), read_only=True):
        self.tools = list(tools)
        self.read_only = bool(read_only)
        self.granted: dict[str, float | None] = {}
        self.calls: list[tuple] = []

    # what the panel reads
    def tools_above_read(self):
        return list(self.tools)

    def read_only_enabled(self):
        return self.read_only

    def is_granted(self, tool, action=""):
        return tool in self.granted

    def grants(self):
        return [FakeGrant(t, "", e) for t, e in self.granted.items()]

    def risk_class_of(self, tool, action="", arguments=None):
        return {"file_controller": "WRITE_LOCAL",
                "mail": "EXTERNAL"}.get(tool, "READ")

    def describe(self, tool, action=""):
        return f"{tool}: {self.risk_class_of(tool)}"

    # what the panel changes
    def set_read_only(self, enabled):
        self.read_only = bool(enabled)
        self.calls.append(("set_read_only", bool(enabled)))

    def grant(self, tool, action="", ttl_seconds=None):
        expires = None if ttl_seconds is None else time.time() + float(ttl_seconds)
        self.granted[tool] = expires
        self.calls.append(("grant", tool, action, ttl_seconds))
        return FakeGrant(tool, action, expires)

    def revoke(self, tool, action=""):
        self.granted.pop(tool, None)
        self.calls.append(("revoke", tool, action))
        return True


class ExplodingPolicy:
    """A policy where every single call fails."""

    def _boom(self, *args, **kwargs):
        raise RuntimeError("the policy is on fire")

    tools_above_read = _boom
    read_only_enabled = _boom
    set_read_only = _boom
    is_granted = _boom
    grant = _boom
    revoke = _boom
    grants = _boom
    risk_class_of = _boom
    describe = _boom


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    import os

    previous = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    if previous is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = previous


@pytest.fixture()
def no_policy(monkeypatch):
    """No policy module at all: the panel must say so and keep working."""
    monkeypatch.setitem(sys.modules, "core.policy", None)
    monkeypatch.setitem(sys.modules, "core.action_policy", None)


@pytest.fixture()
def fake_policy(monkeypatch):
    """A policy that behaves, in place of the real one."""
    policy = FakePolicy(tools=["file_controller", "mail"])
    monkeypatch.setitem(sys.modules, "core.policy", policy)
    monkeypatch.setitem(sys.modules, "core.action_policy", None)
    return policy


@pytest.fixture()
def overlay(qt_app):
    """A panel with no parent, so the window is out of the picture."""
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    yield panel
    try:
        panel.hide()
        panel.deleteLater()
    except RuntimeError:
        pass


def _labels(overlay):
    from PyQt6.QtWidgets import QLabel

    return [lbl.text() for lbl in overlay.findChildren(QLabel)]


def _buttons(overlay):
    from PyQt6.QtWidgets import QPushButton

    return overlay.findChildren(QPushButton)


# ── The panel builds ────────────────────────────────────────────────────────

def test_the_panel_builds_with_no_policy_at_all(qt_app, no_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        assert panel is not None
        texts = _labels(panel)
        assert localization.t("permissions.title") in texts
        # It says the policy is not available, instead of pretending that the
        # assistant may do nothing.
        assert localization.t("permissions.unavailable") in texts
    finally:
        panel.hide()
        panel.deleteLater()


def test_the_panel_builds_with_a_policy(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        assert panel is not None
        texts = _labels(panel)
        assert localization.t("permissions.title") in texts
        assert localization.t("permissions.unavailable") not in texts
    finally:
        panel.hide()
        panel.deleteLater()


def test_a_policy_that_raises_does_not_take_the_panel_down(qt_app, monkeypatch):
    from gui.overlays import PermissionsOverlay

    monkeypatch.setitem(sys.modules, "core.policy", ExplodingPolicy())
    monkeypatch.setitem(sys.modules, "core.action_policy", None)

    panel = PermissionsOverlay()
    try:
        # It built, and asking it to draw itself again did not raise.
        panel.refresh()
        assert panel is not None
        texts = _labels(panel)
        assert localization.t("permissions.title") in texts
    finally:
        panel.hide()
        panel.deleteLater()


def test_the_panel_has_a_close_button(overlay):
    assert localization.t("permissions.close") in [b.text() for b in _buttons(overlay)]


# ── The list of tools ───────────────────────────────────────────────────────

def test_it_lists_the_tools_that_are_above_read(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        assert set(panel._rows) == {"file_controller", "mail"}
        texts = _labels(panel)
        # The name is a human one, not the raw key…
        assert "File controller" in texts
        # …and the raw key is still there for anybody who needs it.
        assert "file_controller" in texts
    finally:
        panel.hide()
        panel.deleteLater()


def test_the_class_of_risk_is_written_next_to_each_tool(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        texts = _labels(panel)
        assert localization.t("permissions.class.write_local") in texts
        assert localization.t("permissions.class.external") in texts
    finally:
        panel.hide()
        panel.deleteLater()


def test_the_tools_that_are_only_read_are_not_listed(qt_app, monkeypatch):
    from gui.overlays import PermissionsOverlay

    policy = FakePolicy(tools=["file_controller"])
    monkeypatch.setitem(sys.modules, "core.policy", policy)
    monkeypatch.setitem(sys.modules, "core.action_policy", None)

    panel = PermissionsOverlay()
    try:
        assert "file_controller" in panel._rows
        assert "calculator" not in panel._rows
    finally:
        panel.hide()
        panel.deleteLater()


# ── Granting and revoking ──────────────────────────────────────────────────

def test_granting_calls_the_policy_with_the_tool_and_the_expiry(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        row = panel._rows["file_controller"]
        row["combo"].setCurrentIndex(1)          # one hour
        row["button"].click()

        assert ("grant", "file_controller", "", 3600) in fake_policy.calls
    finally:
        panel.hide()
        panel.deleteLater()


def test_granting_without_an_expiry_asks_for_no_deadline(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        row = panel._rows["file_controller"]
        row["combo"].setCurrentIndex(0)          # no expiry
        row["button"].click()

        assert ("grant", "file_controller", "", None) in fake_policy.calls
    finally:
        panel.hide()
        panel.deleteLater()


def test_revoking_calls_the_policy(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        row = panel._rows["file_controller"]
        row["combo"].setCurrentIndex(0)
        row["button"].click()                    # grants it
        qt_app.processEvents()                   # the panel redraws

        # The row was rebuilt, so the button is a new one.
        panel._rows["file_controller"]["button"].click()

        assert ("revoke", "file_controller", "") in fake_policy.calls
    finally:
        panel.hide()
        panel.deleteLater()


def test_the_master_switch_calls_set_read_only(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        assert panel._read_only_button is not None
        panel._read_only_button.click()

        assert ("set_read_only", False) in fake_policy.calls
    finally:
        panel.hide()
        panel.deleteLater()


def test_read_only_off_is_said_out_loud(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    panel = PermissionsOverlay()
    try:
        panel._read_only_button.click()
        qt_app.processEvents()

        texts = _labels(panel)
        assert localization.t("permissions.read_only_off_notice") in texts
    finally:
        panel.hide()
        panel.deleteLater()


def test_what_is_granted_is_listed(qt_app, fake_policy):
    from gui.overlays import PermissionsOverlay

    fake_policy.granted["file_controller"] = None      # no deadline
    panel = PermissionsOverlay()
    try:
        texts = _labels(panel)
        assert localization.t("permissions.granted_title") in texts
        assert localization.t("permissions.never") in texts
    finally:
        panel.hide()
        panel.deleteLater()


# ── The catalogue ───────────────────────────────────────────────────────────

def test_every_new_key_has_all_five_languages():
    codes = set(localization.language_codes())
    for key in NEW_KEYS:
        assert key in localization.CATALOG, f"{key} is not in the catalogue"
        assert set(localization.CATALOG[key]) == codes, (
            f"{key} is missing a language")


def test_none_of_the_new_keys_is_the_english_string():
    for key in NEW_KEYS:
        if key in WHITELIST:
            continue
        english = localization.CATALOG[key]["en"]
        for code in ("es", "pt", "fr", "zh"):
            assert localization.CATALOG[key][code] != english, (
                f"{key} was not translated into {code}")


def test_the_new_keys_carry_the_same_placeholders():
    import re

    for key in NEW_KEYS:
        english = localization.CATALOG[key]["en"]
        expected = set(re.findall(r"\{(\w+)\}", english))
        for code in ("es", "pt", "fr", "zh"):
            found = set(re.findall(r"\{(\w+)\}", localization.CATALOG[key][code]))
            assert found == expected, f"{key} has different placeholders in {code}"
