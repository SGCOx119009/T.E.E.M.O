"""
core/router.py — where each request goes, decided on this machine.

THE IDEA
    "Intelligence per watt": a request that needs no cloud must not take one,
    and a request that is heavy must not block the voice.

    The decision of WHICH one a request is has to be made here, in
    milliseconds, by something deterministic. A model cannot be paid for to
    decide whether to be paid for, and a heuristic that needs the cloud is
    not a heuristic.

THE CLASSES
    TRIVIAL   classify, extract, translate, format, "what time is it".
              -> LOCAL. It does not leave this machine.
    CHAT      ordinary conversation.
              -> the CONFIGURED provider, because the voice lives there.
    REASONING multi-step reasoning, long analysis, code.
              -> CLOUD, degrading to local if the cloud is not answering.
    GROUNDED  needs facts that are fresh today.
              -> CLOUD, with the search tool.
    GROUNDING needs to find an element on the screen.
              -> the LOCAL grounding layer (FASE 6).
    SECRET    touches credentials, keys, sensitive files.
              -> LOCAL, and never a small model that could leak them.

THE LOG IS THE POINT
    "A TRIVIAL request does not leave the machine" is only true if you can
    SEE it. Every decision is written down — the class, the destination, and
    whether the request stayed here — so the claim can be checked against the
    log instead of taken on faith.

WHAT IS NOT HERE
    No telemetry. Nothing is sent anywhere to report that this file exists.
    The one place the router's decisions leave the machine is the log the
    user can read, and it stays on this machine.

THE CLASSIFIER IS A RULE SET, NOT A MODEL
    It has to be local, deterministic and instant. A rule set is all three;
    a small model is only the first two, and costs a second one to run.
"""
from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass

# ── The classes ─────────────────────────────────────────────────────────────
TRIVIAL = "trivial"
CHAT = "chat"
REASONING = "reasoning"
GROUNDED = "grounded"
GROUNDING = "grounding"
SECRET = "secret"

CLASSES = (TRIVIAL, CHAT, REASONING, GROUNDED, GROUNDING, SECRET)

# ── The destinations ────────────────────────────────────────────────────────
LOCAL = "local"
CLOUD = "cloud"
SCREEN = "screen"
# "none" means: no model may see this. A secret with no local model is not
# sent to the cloud — it is refused, because a credential in a cloud
# provider's log is a credential that leaked.
NONE = "none"

# ── The rule sets ───────────────────────────────────────────────────────────
# Each list is a set of patterns in every language the product speaks. The
# classifier looks at the user's own words, so the words have to be in the
# language the user writes in.

_SECRET = (
    r"passwo?rd|passphrase|api[\s_-]?key|secret[\s_]?key|access[\s_]?token|"
    r"bearer\s+token|private[\s_]?key|\.env\b|credential|cookie|session[\s_]?id|"
    r"cvv|card[\s_]?number|seed[\s_]?phrase|mnemonic|"
    r"contraseñ?a|clave|secreto|credencial|"
    r"senha|chave|segredo|"
    r"mot[\s_]?de[\s_]?passe|secret|"
    r"密码|密钥|口令|令牌"
)

_GROUNDING = (
    r"on\s+(?:the\s+)?screen|click\s+(?:on\s+)?the|press\s+the\s+(?:button|icon)|"
    r"the\s+button\s+(?:labelled|labeled)|highlight(?:ed)?\s+(?:button|element)|"
    r"open\s+the\s+menu|this\s+window|"
    r"en\s+la\s+pantalla|haz\s+clic|clic(?:a|ke)\s+en|el\s+bot[oó]n|el\s+men[uú]|"
    r"na\s+tela|clique\s+em|o\s+bot[aã]o|o\s+menu|"
    r"[àa]\s+l'[ée]cran|cliquez\s+sur|le\s+bouton|le\s+menu|"
    r"屏幕|点击|按钮|菜单"
)

_TRIVIAL = (
    r"\btranslate\b|\bclassify\b|\bextract\b|\bformat\b|\bcapitali[sz]e\b|"
    r"\bupper\s?case\b|\blower\s?case\b|\bword\s+count\b|what(?:'s|\s+is)\s+the\s+time|"
    r"\bone\s+line\b|\bspell\b|\buppercase\b|"
    r"\btraduce\b|\bclasifica\b|\bextrae\b|\bformatea\b|\bcuenta\s+las\s+palabras|"
    r"qu[eé]\s+hora|m[aá]s\s+may[uú]sculas|resumen\s+en\s+una\s+l[ií]nea|"
    r"\btraduz[a-z]*\b|\bclassifique\b|\bextraia\b|\bformate\b|contar\s+palavras|que\s+horas|"
    r"\btraduis\b|\bclasse\b|\bextrais\b|\bformate\b|compter\s+les\s+mots|quelle\s+heure|"
    r"翻译|分类|提取|格式化|大写|几点|字数"
)

_GROUNDED = (
    r"\bnews\b|\bheadlines?\b|\blatest\b|\btoday'?s\b|\brecent(?:ly)?\b|"
    r"\bweather\b|\bforecast\b|\bcurrent\s+(?:price|weather|score|version)\b|"
    r"\bwhat\s+happened\b|\bwho\s+won\b|\brelease\s+date\b|\bright\s+now\b|"
    r"\bnoticias\b|\b[úu]ltimas\b|\bel\s+clima\b|\bprecio\b|qu[eé]\s+pas[oó]|"
    r"\bnot[ií]cias\b|\b[úu]ltimas\b|\bpre[cç]o\b|o\s+que\s+aconteceu|"
    r"\bactualit[eé]s?\b|\bderni[eè]res?\b|\bm[ée]t[ée]o\b|\bprix\b|"
    r"新闻|最新|天气|价格|今天"
)

_REASONING = (
    r"\banaly[sz]e\b|\bcompare\b|\bexplain\s+why\b|\bwrite\s+(?:a\s+)?(?:code|script|program)\b|"
    r"\bdebug\b|\bdesign\b|\bplan\b|\bstep[\s-]by[\s-]step\b|\brefactor\b|\barchitect\w*\b|"
    r"\bprove\b|\boptimi[sz]e\b|\bdebuggear\b|"
    r"\banaliza\b|\bcompara\b|explica\s+por\s+qu[eé]|escribe\s+c[oó]digo|\bdepur\w+\b|"
    r"\bdise[ñn]a\b|\bplanifica\b|paso\s+a\s+paso|"
    r"\banalisa\b|\bcompare\b|explique\s+por\s+que|escreva\s+c[oó]digo|\bdepure\b|"
    r"\banalyser\b|\bcomparez\b|expliquez\s+pourquoi|[ée]crivez\s+du\s+code|"
    r"分析|比较|为什么|写代码|调试|设计|步骤"
)

# The order matters, and it is not alphabetical: the most dangerous class is
# checked first, because a question that contains a secret AND asks for the
# weather is a secret question.
_RULES = (
    (SECRET, _SECRET),
    (GROUNDING, _GROUNDING),
    (TRIVIAL, _TRIVIAL),
    (GROUNDED, _GROUNDED),
    (REASONING, _REASONING),
)

_COMPILED = tuple((klass, re.compile(pattern, re.IGNORECASE))
                  for klass, pattern in _RULES)


# ── How much a local model needs ────────────────────────────────────────────
# The figures come from the plan's own table. A model whose size is not
# known is assumed to be the big one: loading a model that does not fit is
# the failure this table exists to prevent, and a wrong guess here costs a
# failed load, not a wrong answer.
_MODEL_VRAM_MB = {
    "qwen2.5:1.5b": 1100,
    "qwen2.5:3b": 2000,
    "phi3.5-mini": 2200,
    "qwen3:1.7b": 1400,
    "llama3.2:3b": 2000,
    "gemma2:2b": 1600,
    "qwen2.5:7b": 4500,
    "llama3.1:8b": 5200,
}
_UNKNOWN_MODEL_MB = 4500
# A model that runs on the CPU needs the RAM to be roughly twice what it
# would need on the GPU, because the weights are read into system memory
# and the activations are not quantised the same way.
_RAM_MULTIPLIER = 2


@dataclass(frozen=True)
class Decision:
    """Where one request is going, and why."""
    klass: str
    destination: str
    reason: str
    stays_local: bool
    model: str = ""

    def as_line(self) -> str:
        where = "local" if self.stays_local else self.destination
        model = f" model={self.model}" if self.model else ""
        return (f"[Router] class={self.klass} -> {where}{model} "
                f"({self.reason})")


# ── The classifier ──────────────────────────────────────────────────────────

def classify(text: str) -> str:
    """Which class a request is. Local, deterministic, instant.

    Returns CHAT when nothing matches: an ordinary conversation is the
    default, and the default has to be safe rather than clever.
    """
    raw = str(text or "").strip()
    if not raw:
        return CHAT
    for klass, pattern in _COMPILED:
        if pattern.search(raw):
            return klass
    return CHAT


def request_text(contents) -> str:
    """The user's own words out of whatever shape the caller passed.

    `contents` is a string, or a list of parts, or a list of {"role": ...,
    "parts": ...} turns. The router only needs the TEXT to classify, and
    only the last user turn: a long conversation with a question at the end
    must be classified by the question.
    """
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return request_text(contents.get("parts") or contents.get("text") or "")
    if not isinstance(contents, (list, tuple)):
        return ""
    texts: list[str] = []
    for item in contents:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            if item.get("text"):
                texts.append(str(item["text"]))
            elif item.get("parts"):
                texts.append(request_text(item["parts"]))
        elif isinstance(item, (list, tuple)):
            texts.append(request_text(item))
    return " ".join(texts[-4:])[-4000:]


# ── The machine's headroom ─────────────────────────────────────────────────

def free_vram_mb() -> int:
    """Free video memory in MB, or 0 when there is no usable GPU.

    Never raises and never guesses: a machine with no NVIDIA card has no
    VRAM to speak of, and the answer is 0, not a number invented from the
    integrated adapter's shared memory.

    THE REGULATOR FIRST
        The plan says the hardware regulator is "the source of truth of the
        free VRAM", and it is: it reads the card once a second, with NVML,
        and it publishes the number in its `ResourceState`. So the router
        asks it before shelling out to `nvidia-smi`, and it only does the
        slow thing when the regulator has no reading to give — a machine
        with no NVIDIA card, or an application that never started the
        regulator at all.
    """
    try:
        from core.hardware_equilibrium import free_vram_mb as _from_regulator
        published = float(_from_regulator())
        if published >= 0:
            return int(published)
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4.0,
        )
        if out.returncode == 0 and out.stdout.strip():
            first = out.stdout.strip().splitlines()[0]
            total, _, used = first.partition(",")
            return max(0, int(float(total)) - int(float(used)))
    except Exception:
        pass
    return 0


def free_ram_mb() -> int:
    """Available system memory in MB."""
    try:
        import psutil
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        return 0


def model_vram_mb(model: str) -> int:
    """How much VRAM a model wants, in MB."""
    name = str(model or "").strip().lower()
    for key, size in _MODEL_VRAM_MB.items():
        if key in name:
            return size
    return _UNKNOWN_MODEL_MB


def model_fits(model: str, vram_mb: int | None = None,
               ram_mb: int | None = None) -> tuple[bool, str]:
    """Can this model be loaded here? (fits, reason).

    The rule from the plan: a model that does not fit in the free VRAM AND
    does not fit in RAM either is NOT loaded — the class is marked as not
    served locally and the request descends to the next destination.
    """
    if not str(model or "").strip():
        return False, "no model was named"
    need = model_vram_mb(model)
    vram = free_vram_mb() if vram_mb is None else int(vram_mb)
    ram = free_ram_mb() if ram_mb is None else int(ram_mb)
    if vram >= need:
        return True, f"{need} MB of VRAM, {vram} MB free"
    if ram >= need * _RAM_MULTIPLIER:
        return True, f"{need * _RAM_MULTIPLIER} MB of RAM, {ram} MB free"
    return False, (f"{need} MB of VRAM needed and {vram} MB free; "
                   f"{need * _RAM_MULTIPLIER} MB of RAM needed and "
                   f"{ram} MB free")


# ── The local provider ──────────────────────────────────────────────────────

def local_provider():
    """The provider that runs on this machine, and its config.

    Returns ("", {}) when there is none. The difference between "a model on
    my machine" and "a model I pay for" is a base URL, and that difference
    is the whole point of this router — so it is read from the base URL and
    not from a flag somebody could set wrongly.
    """
    try:
        from memory.config_manager import (get_provider_config, get_providers,
                                           get_sidecar_provider)
    except Exception:
        return "", {}
    try:
        sidecar = get_sidecar_provider()
        if sidecar:
            cfg = get_provider_config(sidecar) or {}
            if _is_loopback(str(cfg.get("base_url") or "")):
                return sidecar, cfg
        creds = (get_providers() or {}).get("creds")
        if isinstance(creds, dict):
            for provider_id, cfg in creds.items():
                if not isinstance(cfg, dict):
                    continue
                if _is_loopback(str(cfg.get("base_url") or "")):
                    return provider_id, cfg
    except Exception:
        pass
    return "", {}


def _is_loopback(url: str) -> bool:
    host = str(url or "").partition("://")[2].partition("/")[0].partition(":")[0].lower()
    if host in ("localhost", "::1", "127.0.0.1"):
        return True
    if host.startswith("127."):
        return True
    if host.endswith(".local") or host.endswith(".lan"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        return a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)
    return False


def provider_config(provider_id: str) -> dict:
    """The stored configuration of one provider, or {}."""
    if not str(provider_id or "").strip():
        return {}
    try:
        from memory.config_manager import get_provider_config
        cfg = get_provider_config(provider_id) or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def provider_is_local(provider_id: str) -> bool:
    """Is this provider running on a machine the user owns?"""
    if not str(provider_id or "").strip():
        return False
    return _is_loopback(str(provider_config(provider_id).get("base_url") or ""))


def local_model() -> str:
    """The name of the local model, or "" when there is none."""
    _, cfg = local_provider()
    return str(cfg.get("model") or "").strip()


# ── The decision ────────────────────────────────────────────────────────────

def decide(text: str, *, provider: str = "") -> Decision:
    """Where this request goes.

    `provider` is the provider the caller is about to use, when it already
    knows — the router then judges THAT provider instead of looking for
    one. A decision of LOCAL is only ever made for a provider that really
    is on this machine: "local" is a property of a base URL, not a wish.
    """
    klass = classify(text)
    if provider:
        cfg = provider_config(provider)
        model = str(cfg.get("model") or "").strip()
        is_local = _is_loopback(str(cfg.get("base_url") or ""))
    else:
        provider_id, cfg = local_provider()
        provider = provider_id
        model = str((cfg or {}).get("model") or "").strip()
        is_local = bool(provider_id)

    if klass == GROUNDING:
        return Decision(GROUNDING, SCREEN,
                        "the element is on this screen and the grounding "
                        "layer reads it here", True, model)

    if klass == SECRET:
        if not model or not is_local:
            return Decision(SECRET, NONE,
                            "it touches credentials and there is no local model "
                            "to read them with", True, "")
        fits, why = model_fits(model)
        if not fits:
            return Decision(SECRET, NONE,
                            f"it touches credentials and the local model does "
                            f"not fit here ({why})", True, model)
        return Decision(SECRET, LOCAL,
                        "it touches credentials, so it stays on this machine",
                        True, model)

    if klass == TRIVIAL:
        if not model or not is_local:
            return Decision(TRIVIAL, CLOUD,
                            "it is trivial, but no local model is configured",
                            False, "")
        fits, why = model_fits(model)
        if not fits:
            return Decision(TRIVIAL, CLOUD,
                            f"the local model does not fit here ({why})",
                            False, model)
        return Decision(TRIVIAL, LOCAL,
                        f"it is trivial and the local model answers it ({why})",
                        True, model)

    if klass == GROUNDED:
        return Decision(GROUNDED, CLOUD,
                        "it needs facts from today, which live on the web",
                        False, model)

    if klass == REASONING:
        if model and is_local:
            fits, why = model_fits(model)
            if fits:
                return Decision(REASONING, LOCAL,
                                f"it is heavy but the local model can take it "
                                f"({why})", True, model)
        return Decision(REASONING, CLOUD,
                        "it is heavy reasoning and the cloud is where the big "
                        "models are", False, model)

    return Decision(CHAT, CLOUD,
                    "it is conversation, and the voice lives in the "
                    "configured provider", False, model)


# ── The log ─────────────────────────────────────────────────────────────────
_log_lock = threading.Lock()
_last: list[Decision] = []


def record(decision: Decision, logger=print) -> Decision:
    """Write the decision down and keep the last few."""
    with _log_lock:
        _last.append(decision)
        del _last[:-50]
    try:
        logger(decision.as_line())
    except Exception:
        pass
    return decision


def recent(limit: int = 10) -> list[Decision]:
    """The last few decisions, for the interface and for the tests."""
    with _log_lock:
        return list(_last[-limit:])


def reset() -> None:
    """Forget the recorded decisions. For the tests."""
    with _log_lock:
        _last.clear()
