"""
tests/test_router.py — FASE 7: the Router Local-First.

WHAT IS BEING PROVED
    1. The classifier is local, deterministic and instant. A rule set, not a
       model: deciding whether to pay for a model must not cost a model.
    2. It speaks the languages the product speaks. A user who writes in
       Spanish gets a Spanish decision, not an English one that happens to
       match.
    3. A request that touches credentials is never sent to the cloud.
    4. A TRIVIAL request with a local model stays on this machine, and the
       fact is written in the log — because "it does not leave the machine"
       is only true if you can see it.
    5. A model that does not fit in the free VRAM and does not fit in RAM is
       not loaded.
    6. Nothing in the router leaves the machine. There is no telemetry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import router  # noqa: E402


# ── The classifier ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Translate this to Spanish", router.TRIVIAL),
    ("clasifica estos datos", router.TRIVIAL),
    ("format this list", router.TRIVIAL),
    ("¿qué hora es?", router.TRIVIAL),
    ("traduza isto", router.TRIVIAL),
    ("traduis ceci", router.TRIVIAL),
    ("翻译这段文字", router.TRIVIAL),
])
def test_trivial_requests_are_recognised_in_every_language(text, expected):
    assert router.classify(text) == expected


@pytest.mark.parametrize("text", [
    "What is the latest news?",
    "dame las noticias de hoy",
    "qual é o preço de hoje",
    "最新消息是什么",
])
def test_fresh_facts_are_grounded(text):
    assert router.classify(text) == router.GROUNDED


@pytest.mark.parametrize("text", [
    "Analyse why this crashes and compare the two approaches",
    "analiza por qué falla y compara las dos opciones",
    "compare as duas abordagens",
    "compare les deux approches",
])
def test_heavy_reasoning_is_recognised(text):
    assert router.classify(text) == router.REASONING


@pytest.mark.parametrize("text", [
    "Click the button on the screen labelled Save",
    "haz clic en el botón de la pantalla",
    "点击屏幕上的按钮",
])
def test_screen_work_is_grounding(text):
    assert router.classify(text) == router.GROUNDING


@pytest.mark.parametrize("text", [
    "What is my password?",
    "lee mi contraseña del archivo",
    "what is the api key for the service",
    "读取密码",
])
def test_credentials_are_secret(text):
    assert router.classify(text) == router.SECRET


def test_a_secret_question_about_the_weather_is_still_a_secret():
    """The order of the rules is not alphabetical on purpose: a question
    that contains a password AND asks for the weather is a secret
    question."""
    assert router.classify("what is the weather today, and my password is 1234") \
        == router.SECRET


def test_an_ordinary_conversation_is_chat():
    assert router.classify("hola, ¿qué tal estás?") == router.CHAT
    assert router.classify("") == router.CHAT


def test_the_classifier_is_deterministic():
    text = "Translate this and tell me the weather"
    assert len({router.classify(text) for _ in range(20)}) == 1


# ── The headroom ────────────────────────────────────────────────────────────

def test_a_model_that_fits_in_vram_is_loaded():
    fits, why = router.model_fits("qwen2.5:1.5b", vram_mb=4096, ram_mb=8192)
    assert fits is True
    assert "VRAM" in why


def test_a_model_that_only_fits_in_ram_is_loaded():
    fits, why = router.model_fits("qwen2.5:1.5b", vram_mb=0, ram_mb=16384)
    assert fits is True
    assert "RAM" in why


def test_a_model_that_fits_nowhere_is_not_loaded():
    fits, why = router.model_fits("llama3.1:8b", vram_mb=1024, ram_mb=2048)
    assert fits is False
    assert why


def test_an_unknown_model_is_treated_as_the_big_one():
    """A wrong guess here costs a failed load, not a wrong answer."""
    assert router.model_vram_mb("some-model-nobody-has-heard-of") >= 4000


def test_the_machine_reports_its_headroom_without_raising():
    assert router.free_vram_mb() >= 0
    assert router.free_ram_mb() >= 0


# ── The decision ────────────────────────────────────────────────────────────

def test_a_trivial_request_with_no_local_model_goes_to_the_cloud(monkeypatch):
    monkeypatch.setattr(router, "local_provider", lambda: ("", {}))
    decision = router.decide("Translate this to Spanish")
    assert decision.klass == router.TRIVIAL
    assert decision.destination == router.CLOUD
    assert decision.stays_local is False


def test_a_trivial_request_with_a_local_model_stays_here(monkeypatch):
    monkeypatch.setattr(router, "local_provider",
                        lambda: ("ollama", {"base_url": "http://127.0.0.1:11434/v1",
                                            "model": "qwen2.5:1.5b"}))
    monkeypatch.setattr(router, "model_fits",
                        lambda model, vram_mb=None, ram_mb=None: (True, "fits"))
    decision = router.decide("Translate this to Spanish")
    assert decision.destination == router.LOCAL
    assert decision.stays_local is True
    assert "trivial" in decision.as_line()


def test_a_trivial_request_whose_model_does_not_fit_goes_to_the_cloud(monkeypatch):
    monkeypatch.setattr(router, "local_provider",
                        lambda: ("ollama", {"base_url": "http://127.0.0.1:11434/v1",
                                            "model": "llama3.1:8b"}))
    monkeypatch.setattr(router, "model_fits",
                        lambda model, vram_mb=None, ram_mb=None: (False, "no room"))
    decision = router.decide("Translate this to Spanish")
    assert decision.destination == router.CLOUD
    assert "does not fit" in decision.reason


def test_a_secret_never_goes_to_the_cloud(monkeypatch):
    monkeypatch.setattr(router, "local_provider", lambda: ("", {}))
    decision = router.decide("what is my password")
    assert decision.destination == router.NONE
    assert decision.stays_local is True


def test_a_secret_with_a_local_model_is_read_here(monkeypatch):
    monkeypatch.setattr(router, "local_provider",
                        lambda: ("ollama", {"base_url": "http://127.0.0.1:11434/v1",
                                            "model": "qwen2.5:3b"}))
    monkeypatch.setattr(router, "model_fits",
                        lambda model, vram_mb=None, ram_mb=None: (True, "fits"))
    decision = router.decide("what is my password")
    assert decision.destination == router.LOCAL


def test_a_cloud_provider_is_never_called_local(monkeypatch):
    """'Local' is a property of a base URL, not a wish."""
    monkeypatch.setattr(router, "provider_config",
                        lambda pid: {"base_url": "https://api.openai.com/v1",
                                     "model": "gpt-4o-mini"})
    decision = router.decide("Translate this to Spanish", provider="openai")
    assert decision.destination == router.CLOUD
    assert decision.stays_local is False


# ── The log ─────────────────────────────────────────────────────────────────

def test_every_decision_is_written_down():
    router.reset()
    router.record(router.decide("Translate this"))
    router.record(router.decide("Analyse why this fails"))
    recent = router.recent()
    assert len(recent) == 2
    assert recent[0].klass == router.TRIVIAL
    assert "class=" in recent[0].as_line()
    assert "class=" in recent[1].as_line()


def test_the_log_line_says_where_the_request_went():
    decision = router.Decision(router.TRIVIAL, router.LOCAL, "because", True, "qwen")
    line = decision.as_line()
    assert "trivial" in line
    assert "local" in line
    assert "qwen" in line


# ── The text of the request ────────────────────────────────────────────────

def test_the_request_text_is_the_users_own_words():
    assert router.request_text("hello") == "hello"
    parts = [{"role": "user", "parts": [{"text": "Translate this"}]}]
    assert "Translate this" in router.request_text(parts)


def test_a_long_conversation_is_classified_by_the_last_question():
    parts = [
        {"role": "user", "parts": [{"text": "hola"}]},
        {"role": "model", "parts": [{"text": "buenas"}]},
        {"role": "user", "parts": [{"text": "dame las noticias de hoy"}]},
    ]
    assert router.classify(router.request_text(parts)) == router.GROUNDED


# ── Nothing leaves the machine ──────────────────────────────────────────────

def test_the_router_has_no_telemetry_and_no_network():
    """The router's decisions stay on this machine. The only thing that
    leaves is the log the user can read, and it stays here too.

    The check is on the IMPORTS, not on the words: a module can mention
    telemetry in a comment and still import a client for it, and a module
    can avoid the word and still import `requests`.
    """
    import ast

    source = (BASE / "core" / "router.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    for forbidden in ("requests", "httpx", "urllib", "socket", "http",
                      "posthog", "post_hog", "telemetry", "aiohttp"):
        assert forbidden not in imported, (
            f"the router must not import {forbidden}")


def test_the_router_does_not_import_a_model():
    source = (BASE / "core" / "router.py").read_text(encoding="utf-8")
    assert "import gemini" not in source
    assert "from core import gemini" not in source
