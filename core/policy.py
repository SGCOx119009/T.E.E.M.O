"""
core/policy.py — where the browser is allowed to go.

THE PROBLEM
    The browser operator drives a browser that may be signed into the user's
    bank, their mail and their accounts. "Open the page" is a harmless phrase
    until the page is a transfer screen, at which point the same phrase moves
    money. The list of domains below is the boundary that says: not there.

WHY A DENY LIST AND NOT AN ALLOW LIST
    An allow list is the stronger instrument, but it is useless until somebody
    writes it down, and until then it protects nothing. A deny list protects
    from the first second, with no configuration at all. So the deny list is
    the default and the allow list is an OPT-IN tightening: when the user
    sets one, only those domains are reachable — and the deny list still
    applies on top of it, because a user's own allow list is not a licence to
    reach a bank.

WHAT IS BLOCKED, AND WHY EACH ONE
    banking     — a transfer is irreversible and the user does not watch the
                  screen while it happens.
    payment     — a payment gateway takes money and gives nothing back.
    mail        — reading someone's mail is not browsing; sending on their
                  behalf is impersonation.
    accounts    — the page where credentials and sessions are managed.
    secrets     — password managers and 2FA. The assistant has no business
                  reading a vault, and solving an MFA challenge is a human
                  verification step that a machine should not cross.
    two_factor  — the same, wherever the challenge is hosted.
    captcha     — a proof of humanity. Solving it is defeating it.
    internal    — `.local`, `.lan`, `.internal`: the router, the printer, the
                  NAS, the intranet. Nothing there was meant for a page the
                  model can read.
    social_send — Twitter/X, LinkedIn, Facebook: READING is fine, SENDING is
                  not. Publishing under the user's name is not something to
                  do on a phrase.

THE CHECK HAPPENS TWICE
    Once on the address the user asked for, and again on every address the
    browser is actually sent to — because a redirect is a decision made by
    the site, not by the user, and a public page that redirects inward is
    still inward. `core/tasks/web.py` already resolves addresses and refuses
    the private and loopback ones; `check_address` calls it rather than
    reimplementing it.

TWO CHECKS, NOT ONE
    A name is not an address. `http://example.test/` is a name that says
    nothing about where it leads, and `http://127.0.0.1:8080/` is an
    address that no name policy would ever recognise. So a request must
    pass BOTH: `check_destination` (what is this page?) and
    `check_address` (where does it actually point?). `check_request` is
    the two of them together, and it is what every request goes through —
    the one the user asked for and every redirect after it.

READING AND ACTING ARE DIFFERENT
    `check_destination(url)` asks "may this page be opened?". 
    `check_destination(url, acting=True)` asks the stricter question: "may
    something be SENT here?". The difference is what the page does with the
    answer, and for a handful of domains the answer is the whole point.
"""
from __future__ import annotations

import ipaddress
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = [
    "Decision",
    "PolicyError",
    "allowed_domains",
    "check_address",
    "check_destination",
    "check_request",
    "classify_host",
    "host_of",
    "require_destination",
]


# ── The vocabulary ───────────────────────────────────────────────────────────

# Categories, in the order they are reported. The first match wins, so the
# most specific rule is checked first.
CATEGORY_BANKING = "banking"
CATEGORY_PAYMENT = "payment"
CATEGORY_MAIL = "mail"
CATEGORY_ACCOUNTS = "accounts"
CATEGORY_SECRETS = "secrets"
CATEGORY_TWO_FACTOR = "two_factor"
CATEGORY_CAPTCHA = "captcha"
CATEGORY_INTERNAL = "internal"
CATEGORY_SOCIAL_SEND = "social_send"

#: Hosts that are blocked outright. A host matches if it IS one of these or
#: is a subdomain of one of them. Payment gateways are the bulk of the list,
#: and they share one reason, so they are written as one tuple.

# Payment gateways. The reason is the same for all of them, so they share it.
_PAYMENT_REASON = "payment gateway"
_PAYMENT_HOSTS: tuple[str, ...] = (
    "paypal.com", "paypal.me", "venmo.com", "cashapp.com", "zellepay.com",
    "stripe.com", "mercadopago.com", "mercadopago.cl", "mercadopago.com.ar",
    "mercadopago.com.mx", "bizum.es", "paysafe.com", "skrill.com",
    "neteller.com", "moneygram.com", "westernunion.com", "revolut.com",
    "wise.com", "transferwise.com", "klarna.com", "affirm.com",
    "creditcard.com", "mybank.co.uk", "caixa.es",
)

# Mail. Reading someone's mail is not browsing.
_MAIL_REASON = "mail account"
_MAIL_HOSTS: tuple[str, ...] = (
    "mail.google.com", "gmail.com", "outlook.live.com", "outlook.com",
    "outlook.office.com", "outlook.office365.com", "mail.yahoo.com",
    "mail.proton.me", "proton.me", "mail.duckduckgo.com", "fastmail.com",
    "zohomail.com", "superhuman.com", "hey.com", "yandex.com",
)

# Account management: where sessions, keys and recovery codes live.
_ACCOUNTS_REASON = "account settings"
_ACCOUNT_HOSTS: tuple[str, ...] = (
    "accounts.google.com", "myaccount.google.com", "security.google.com",
    "github.com/settings", "gitlab.com/-/user_settings", "login.microsoftonline.com",
    "account.microsoft.com", "appleid.apple.com", "id.apple.com",
    "account.apple.com", "myaccount.uber.com", "account.booking.com",
    "accounts.binance.com", "passport.google.com",
)

# Password managers and vaults. The assistant has no business in one.
_SECRETS_REASON = "password manager"
_SECRET_HOSTS: tuple[str, ...] = (
    "1password.com", "lastpass.com", "bitwarden.com", "dashlane.com",
    "keepersecurity.com", "keepassxc.net", "enpass.io", "splashid.com",
    "roboform.com", "pwsafe.org", "nordpass.com", "protonpass.com",
)

# Two-factor services, and the human-verification challenge.
_TWO_FACTOR_REASON = "two-factor authentication"
_TWO_FACTOR_HOSTS: tuple[str, ...] = (
    "authy.com", "duosecurity.com", "duo.com", "okta.com", "onelogin.com",
    "yubico.com", "totp.app", "2fa.app", "auth0.com",
)

_CAPTCHA_REASON = "human verification challenge"
_CAPTCHA_HOSTS: tuple[str, ...] = (
    "recaptcha.net", "hcaptcha.com", "funcaptcha.com", "arkoselabs.com",
    "challenges.cloudflare.com", "px-captcha.com",
)

# Networks that were never meant to be a web page the model can read.
#
# ".test" and ".invalid" are deliberately NOT here: they are the reserved
# names of RFC 2606, used in documentation and in the tests, and they are
# not a private network. Blocking them would block every example address
# without protecting anything.
_INTERNAL_REASON = "internal network"
_INTERNAL_SUFFIXES: tuple[str, ...] = (
    ".local", ".lan", ".internal", ".intranet", ".corp", ".private",
    ".home.arpa", ".localdomain",
)
_INTERNAL_HOSTS: tuple[str, ...] = (
    "localhost", "localhost.localdomain", "router", "gateway",
)

# Domains where READING is allowed and SENDING is not.
_SOCIAL_SEND_REASON = "publishing under the user's name"
_SOCIAL_SEND_HOSTS: tuple[str, ...] = (
    "twitter.com", "x.com", "linkedin.com", "facebook.com", "fb.com",
    "messenger.com", "threads.net", "instagram.com", "reddit.com",
    "mastodon.social", "bsky.app",
)

# Path fragments that mean "prove you are human" or "prove who you are".
_TWO_FACTOR_PATHS = (
    "/two-factor", "/2fa", "/mfa", "/otp", "/one-time-code",
    "/challenge", "/verify-identity", "/security-check",
)
_CAPTCHA_PATHS = ("/captcha", "/challenge-form")

# Words that make a host a bank even when the TLD gives it away.
_BANK_WORDS = ("bank", "banco", "banque", "banco")

# Words that make a host a payment gateway even when the name is unusual.
_PAYMENT_WORDS = ("payment", "payments", "checkout", "pago", "pasarela")

# Path fragments that mean "account management" on an otherwise ordinary host.
_ACCOUNT_PATHS = ("/settings", "/account", "/preferences", "/security",
                  "/password", "/recovery", "/sessions")


@dataclass(frozen=True)
class Decision:
    """The verdict of the policy on one address.

    `allowed` is the answer. `reason` says why not, in words a person can
    act on, and `policy` names the rule that said so — so the model can
    report WHICH rule blocked the page and the user can change that rule.
    """
    allowed: bool
    reason: str = ""
    policy: str = ""
    category: str = ""
    host: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class PolicyError(ValueError):
    """The address is not allowed by the policy."""

    def __init__(self, decision: Decision):
        self.decision = decision
        super().__init__(decision.reason or "the address is not allowed")


# ── Addresses ───────────────────────────────────────────────────────────────

def host_of(url: str) -> str:
    """The host of an address, lowercased and without the port."""
    text = str(url or "").strip()
    if "://" not in text:
        text = "https://" + text
    try:
        host = (urlparse(text).hostname or "").lower()
    except ValueError:
        return ""
    return host.rstrip(".")


def _labels(host: str) -> list[str]:
    return [label for label in host.split(".") if label]


#: The schemes that name a network address. Anything else is not an address
#: the policy can resolve, so the address half of the policy does not apply
#: to it (the destination half still does).
_NETWORK_SCHEMES = ("http", "https")


def _scheme_of(url: str) -> str:
    """The scheme of an address, lowercased."""
    text = str(url or "").strip()
    if "://" not in text:
        text = "https://" + text
    try:
        return (urlparse(text).scheme or "").lower()
    except ValueError:
        return ""


def _literal_address(host: str):
    """The IP address of a host that IS one, or None when it is a name.

    A literal address needs no resolver: the number IS the address, so it
    can be judged without asking anybody's DNS server and without waiting
    for one.
    """
    try:
        return ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return None


def _is_local_address(address) -> bool:
    """Whether an IP address is one this policy must never reach.

    The same set `core/tasks/web.py` refuses: loopback, private (LAN),
    link-local (including the cloud metadata address), reserved,
    multicast and unspecified.
    """
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _host_matches(host: str, suffix: str) -> bool:
    """True when `host` is `suffix` or a subdomain of it.

    The suffix may be written with or without a leading dot: ".local" means
    the TLD, "example.com" means the domain and everything under it.
    """
    suffix = str(suffix or "").strip().lower().lstrip(".")
    if not suffix:
        return False
    return host == suffix or host.endswith("." + suffix)


def _any_matches(host: str, suffixes) -> str:
    for suffix in suffixes:
        if _host_matches(host, suffix):
            return suffix
    return ""


def classify_host(host: str) -> tuple[str, str]:
    """The category that blocks this host, and the reason. ("", "") if none."""
    host = str(host or "").lower().strip()
    if not host:
        return "", ""

    # Internal networks come first: a printer called "router.local" is not a
    # bank, and the reason must be the real one.
    if host in _INTERNAL_HOSTS or _any_matches(host, _INTERNAL_SUFFIXES):
        return CATEGORY_INTERNAL, _INTERNAL_REASON

    if _any_matches(host, _PAYMENT_HOSTS):
        return CATEGORY_PAYMENT, _PAYMENT_REASON
    if _any_matches(host, _MAIL_HOSTS):
        return CATEGORY_MAIL, _MAIL_REASON
    if _any_matches(host, _ACCOUNT_HOSTS):
        return CATEGORY_ACCOUNTS, _ACCOUNTS_REASON
    if _any_matches(host, _SECRET_HOSTS):
        return CATEGORY_SECRETS, _SECRETS_REASON
    if _any_matches(host, _TWO_FACTOR_HOSTS):
        return CATEGORY_TWO_FACTOR, _TWO_FACTOR_REASON
    if _any_matches(host, _CAPTCHA_HOSTS):
        return CATEGORY_CAPTCHA, _CAPTCHA_REASON

    # A host that says what it is. Checked label by label so that "bank" in
    # a path-like label counts but "banking.example.com" does not get missed
    # because the word is in the middle.
    for label in _labels(host):
        if any(word in label for word in _BANK_WORDS):
            return CATEGORY_BANKING, "banking"
        if any(word in label for word in _PAYMENT_WORDS):
            return CATEGORY_PAYMENT, _PAYMENT_REASON

    return "", ""


def _path_of(url: str) -> str:
    text = str(url or "").strip()
    if "://" not in text:
        text = "https://" + text
    try:
        return (urlparse(text).path or "").casefold()
    except ValueError:
        return ""


def _path_category(host: str, path: str) -> tuple[str, str]:
    """A category that only the PATH reveals."""
    if any(marker in path for marker in _TWO_FACTOR_PATHS):
        return CATEGORY_TWO_FACTOR, _TWO_FACTOR_REASON
    if any(marker in path for marker in _CAPTCHA_PATHS):
        return CATEGORY_CAPTCHA, _CAPTCHA_REASON
    return "", ""


# ── The user's own allow list ───────────────────────────────────────────────

def allowed_domains() -> list[str]:
    """The domains the user has authorised, if any.

    Empty means "the user has not restricted anything", which is NOT the same
    as "nothing is allowed" — the deny list is still in force either way.
    """
    try:
        from memory.config_manager import get_browser_allowlist
        return get_browser_allowlist()
    except Exception:
        return []


def _allowed_by_list(host: str, domains: list[str]) -> bool:
    for domain in domains:
        domain = str(domain or "").strip().lower().rstrip(".")
        if not domain:
            continue
        if host == domain or host.endswith("." + domain):
            return True
    return False


# ── The verdict ─────────────────────────────────────────────────────────────

def check_destination(url: str, *, acting: bool = False) -> Decision:
    """May this address be opened? And may something be SENT there?

    `acting=True` is the stricter question, and it is the one a form fill or
    a click that submits should ask. It is what separates reading a social
    page from posting on it.
    """
    host = host_of(url)
    if not host:
        return Decision(False, "the address has no host", "malformed", "malformed", host)

    category, reason = classify_host(host)
    if category:
        return Decision(False, reason, f"deny:{category}", category, host)

    path = _path_of(url)
    category, reason = _path_category(host, path)
    if category:
        return Decision(False, reason, f"deny:{category}", category, host)

    # Account management on an ordinary host: /settings, /account, /password.
    # Only on paths that are unambiguously account pages, so a normal article
    # under /account-news/ is not caught.
    if any(path == marker or path.startswith(marker + "/")
           or f"{marker}/" in path for marker in _ACCOUNT_PATHS):
        if path.startswith("/settings") or path.startswith("/account") \
                or path.startswith("/password") or path.startswith("/security") \
                or path.startswith("/preferences") or path.startswith("/recovery") \
                or path.startswith("/sessions"):
            return Decision(False, _ACCOUNTS_REASON, f"deny:{CATEGORY_ACCOUNTS}",
                            CATEGORY_ACCOUNTS, host)

    if acting and _any_matches(host, _SOCIAL_SEND_HOSTS):
        return Decision(False, _SOCIAL_SEND_REASON, f"deny:{CATEGORY_SOCIAL_SEND}",
                        CATEGORY_SOCIAL_SEND, host)

    domains = allowed_domains()
    if domains and not _allowed_by_list(host, domains):
        return Decision(
            False,
            "the browser is restricted to the domains the user authorised",
            "allowlist", "allowlist", host,
        )

    return Decision(True, "", "", "", host)


def require_destination(url: str, *, acting: bool = False) -> Decision:
    """The decision, or raise `PolicyError`."""
    decision = check_destination(url, acting=acting)
    if not decision.allowed:
        raise PolicyError(decision)
    return decision


def check_address(url: str, *, require_reachable: bool = True) -> Decision:
    """May this address be reached at all?

    This is the private-network half of the policy, and it is NOT done here:
    `core/tasks/web.py` already resolves the name and refuses loopback,
    private and link-local addresses, and reimplementing that would be two
    copies of a rule that must not drift. This calls it.

    A LITERAL address is judged here, without a resolver: the number IS
    the address, so `http://127.0.0.1:8080/`, `http://192.168.1.1/` and
    `http://169.254.169.254/` are refused without asking anybody's DNS
    server about anything. A NAME is resolved by `core/tasks/web.py`,
    which refuses every address it resolves to.

    `require_reachable=False` says what a name that does not resolve
    means: that the address cannot be reached AT ALL, which is not the
    same as reaching something forbidden. The request fails on its own,
    and a guard that refused it would be refusing addresses it has no
    business refusing.
    """
    host = host_of(url)
    if _scheme_of(url) not in _NETWORK_SCHEMES:
        return Decision(False, "only http and https addresses can be reached",
                        "deny:scheme", "scheme", host)

    literal = _literal_address(host)
    if literal is not None:
        if _is_local_address(literal):
            return Decision(False,
                            f"'{host}' points at a local or private network address",
                            "deny:private_address", "private_address", host)
        return Decision(True, "", "", "", host)

    try:
        from core.tasks import web as _web
    except Exception as exc:
        return Decision(False, f"the address policy could not be loaded ({type(exc).__name__})",
                        "deny:private_address", "private_address", host)
    try:
        _web.check_url(str(url or "").strip())
    except _web.Refused as exc:
        reason = str(exc)
        # The wording comes from core/tasks/web.py, which is the only place
        # the resolver is implemented; a name that does not resolve is a
        # different thing from a name that resolves inward.
        if "does not resolve" in reason:
            if not require_reachable:
                return Decision(True, "", "", "", host)
            return Decision(False, reason, "deny:unresolvable", "unresolvable", host)
        # It resolves, and it resolves inward: that is always a refusal.
        return Decision(False, reason, "deny:private_address",
                        "private_address", host)
    except Exception as exc:
        # The resolution itself failed — a timeout, a resolver that is down.
        # Nothing was shown to be safe, and nothing was shown to be
        # forbidden either: an address nobody can look up is an address
        # nobody can reach. A strict caller refuses it; a request guard
        # lets it through, because a browser that refuses every name
        # whenever the resolver blinks is a browser that does not work.
        if not require_reachable:
            return Decision(True, "", "", "", host)
        return Decision(False, f"the address could not be verified ({type(exc).__name__})",
                        "deny:private_address", "private_address", host)
    return Decision(True, "", "", "", host)


def check_request(url: str, *, acting: bool = False) -> Decision:
    """The whole verdict on ONE request: the destination AND the address.

    `check_destination` asks about the NAME — is this a bank, a mail
    account, a page of the user's own network? `check_address` asks about
    the ADDRESS — does that name point at a loopback, a LAN or a
    link-local address? A request that fails EITHER of them does not
    happen, so this is the check every request goes through, whether the
    user asked for the address or a site redirected the browser to it.

    The deny list wins and the allow list only ever HARDENS: the allow
    list is applied by `check_destination` AFTER the deny list, so an
    authorised domain cannot re-open a denied one, and a private address
    is refused even when the user's own list names it.
    """
    destination = check_destination(url, acting=acting)
    if not destination.allowed:
        return destination
    address = check_address(url, require_reachable=False)
    if not address.allowed:
        return address
    return destination


# ─────────────────────────────────────────────────────────────────────────────
#  § THE RISK CATALOGUE — WHAT AN ACTION IS ALLOWED TO DO
#
#  THE OTHER HALF OF THIS MODULE
#
#  Everything above asks "may the browser go there?". This section asks the
#  other question, the one that decides what the ASSISTANT is allowed to do
#  to the user's machine: "what kind of act is this?".
#
#  SEVEN CLASSES, AND WHY THERE ARE SEVEN
#
#    READ           only reads. Silent. The default.
#    WRITE_LOCAL    writes to disk. Silent, logged, reversible.
#    REVERSIBLE_SYS volume, brightness, theme. Silent, undoable.
#    EXTERNAL       the network: sending, publishing, mail. Confirmation.
#    IRREVERSIBLE   shutdown, restart, WiFi, deleting. Confirmation + warning.
#    CREDENTIAL     keys, tokens, passwords. Confirmation + masked + logged.
#    PRIVACY        camera, microphone, screen. Confirmation per event.
#
#  WHY THE CLASS IS DERIVED AND NEVER ASKED FOR
#
#  The class is re-derived from the RESOLVED TOOL, never from a label the
#  model wrote. A policy whose class can be supplied by the caller is not a
#  policy: a model that labels its own action "READ" has a policy that says
#  nothing. Nothing in this section reads a class from the arguments.
#
#  WHY READ-ONLY IS THE DEFAULT
#
#  "Start with tools that only read; disable the rest until they are needed."
#  A confirmation on everything trains the user to say yes without reading,
#  and a policy that has to be granted every time is a policy that is granted
#  every time. So the classes above READ are OFF until the user turns them
#  on — one tool at a time, with an optional expiry — and a refused action
#  says exactly what to grant instead of failing in silence.
# ─────────────────────────────────────────────────────────────────────────────

RISK_READ = "READ"
RISK_WRITE_LOCAL = "WRITE_LOCAL"
RISK_REVERSIBLE_SYS = "REVERSIBLE_SYS"
RISK_EXTERNAL = "EXTERNAL"
RISK_IRREVERSIBLE = "IRREVERSIBLE"
RISK_CREDENTIAL = "CREDENTIAL"
RISK_PRIVACY = "PRIVACY"

#: The seven classes, in the order of the plan.
RISK_CLASSES: tuple[str, ...] = (
    RISK_READ,
    RISK_WRITE_LOCAL,
    RISK_REVERSIBLE_SYS,
    RISK_EXTERNAL,
    RISK_IRREVERSIBLE,
    RISK_CREDENTIAL,
    RISK_PRIVACY,
)


@dataclass(frozen=True)
class RiskClass:
    """One class of risk, and what happens to an action in it.

    `treatment` is the sentence the plan uses, and `needs_confirmation` is
    the part of it that is a decision rather than a description.
    """

    name: str
    meaning: str
    treatment: str
    needs_confirmation: bool
    order: int


RISK_CATALOG: dict[str, RiskClass] = {
    RISK_READ: RiskClass(
        RISK_READ,
        "Only reads. It changes nothing.",
        "Silent. It is the default mode.",
        False, 0),
    RISK_WRITE_LOCAL: RiskClass(
        RISK_WRITE_LOCAL,
        "Writes to disk on this machine.",
        "Silent, logged, and reversible through core/undo.py.",
        False, 1),
    RISK_REVERSIBLE_SYS: RiskClass(
        RISK_REVERSIBLE_SYS,
        "Changes a setting of the system: volume, brightness, theme.",
        "Silent, and undoable.",
        False, 2),
    RISK_EXTERNAL: RiskClass(
        RISK_EXTERNAL,
        "Reaches the network: sending a message, publishing, mail.",
        "Confirmation.",
        True, 3),
    RISK_IRREVERSIBLE: RiskClass(
        RISK_IRREVERSIBLE,
        "Cannot be taken back: shutting down, restarting, WiFi, deleting.",
        "Confirmation, with an explicit warning.",
        True, 4),
    RISK_CREDENTIAL: RiskClass(
        RISK_CREDENTIAL,
        "Touches keys, tokens or passwords.",
        "Confirmation, with the secret masked, and a record of it.",
        True, 5),
    RISK_PRIVACY: RiskClass(
        RISK_PRIVACY,
        "Touches the camera, the microphone or the screen.",
        "Confirmation per event, with the destination visible.",
        True, 6),
}

# ── The fine table ──────────────────────────────────────────────────────────
#
# The coarse vocabulary of `core/tool_audit.py` ("read_or_navigate",
# "state_change", "high", "external") is good enough to decide WHETHER to
# ask, and too coarse to decide WHAT to ask about: "high" covers both
# "turn the volume up" and "delete the file". This table is the fine one,
# and it is what the class of an action is read from.
#
# The classes are the ones of the plan, and each one says what happens to
# an action in it:
#
#   READ           silent, and the default mode.
#   WRITE_LOCAL    silent, logged, and reversible through core/undo.py.
#   REVERSIBLE_SYS silent, and undoable (volume, brightness, theme).
#   EXTERNAL       confirmation: the action reaches the network.
#   IRREVERSIBLE   confirmation with an explicit warning: it cannot be
#                  taken back.
#   CREDENTIAL     confirmation with the secret masked, and a record of it.
#   PRIVACY        confirmation per event, with the destination visible.
#
# An action that is not listed here falls back to the coarse vocabulary, and
# a tool that is not listed at all and cannot be classified is NOT REVIEWED:
# it is refused rather than guessed at.
_ACTION_CLASS: dict[str, dict[str, str]] = {
    # ── Files ───────────────────────────────────────────────────────────────
    "file_controller": {
        "list": RISK_READ, "read": RISK_READ, "find": RISK_READ,
        "largest": RISK_READ, "disk_usage": RISK_READ, "info": RISK_READ,
        # A file on this disk: silent, logged, and undoable.
        "write": RISK_WRITE_LOCAL, "create_file": RISK_WRITE_LOCAL,
        "create_folder": RISK_WRITE_LOCAL, "copy": RISK_WRITE_LOCAL,
        "move": RISK_WRITE_LOCAL, "rename": RISK_WRITE_LOCAL,
        "organize_desktop": RISK_WRITE_LOCAL,
        # Deleting is the one thing about a file that cannot be taken back.
        "delete": RISK_IRREVERSIBLE,
    },
    "file_processor": {
        "info": RISK_READ, "list": RISK_READ, "read": RISK_READ,
        "extract_preview": RISK_READ, "search": RISK_READ,
        "word_count": RISK_READ, "validate": RISK_READ, "stats": RISK_READ,
        "explain": RISK_READ, "review": RISK_READ,
        # The document LEAVES the machine to be summarised: that is network.
        "summarize": RISK_EXTERNAL, "analyze": RISK_EXTERNAL,
        "describe": RISK_EXTERNAL, "translate_hint": RISK_EXTERNAL,
        "document": RISK_EXTERNAL, "to_bullet": RISK_EXTERNAL,
        # An image or a voice is the privacy of a person, not of a file.
        "ocr": RISK_PRIVACY, "transcribe": RISK_PRIVACY,
        "extract_audio": RISK_PRIVACY, "extract_frame": RISK_PRIVACY,
        # A new file next to the original: local, logged, undoable.
        "resize": RISK_WRITE_LOCAL, "convert": RISK_WRITE_LOCAL,
        "compress": RISK_WRITE_LOCAL, "extract": RISK_WRITE_LOCAL,
        "extract_text": RISK_WRITE_LOCAL, "to_word": RISK_WRITE_LOCAL,
        "filter": RISK_WRITE_LOCAL, "sort": RISK_WRITE_LOCAL,
        "format": RISK_WRITE_LOCAL, "to_csv": RISK_WRITE_LOCAL,
        "to_excel": RISK_WRITE_LOCAL, "to_json": RISK_WRITE_LOCAL,
        "fix": RISK_WRITE_LOCAL, "optimize": RISK_WRITE_LOCAL,
        "reformat": RISK_WRITE_LOCAL, "trim": RISK_WRITE_LOCAL,
        "text": RISK_WRITE_LOCAL,
        # Running what the user brought in is code execution.
        "run": RISK_EXTERNAL, "custom": RISK_EXTERNAL,
    },
    # ── The machine ─────────────────────────────────────────────────────────
    "computer_settings": {
        # The four that cannot be taken back.
        "shutdown": RISK_IRREVERSIBLE, "restart": RISK_IRREVERSIBLE,
        "toggle_wifi": RISK_IRREVERSIBLE, "close_window": RISK_IRREVERSIBLE,
        "close_tab": RISK_IRREVERSIBLE, "close_app": RISK_IRREVERSIBLE,
        # Volume, brightness, theme: the machine changes and comes back.
        "volume_up": RISK_REVERSIBLE_SYS, "volume_down": RISK_REVERSIBLE_SYS,
        "volume_set": RISK_REVERSIBLE_SYS, "set_volume": RISK_REVERSIBLE_SYS,
        "mute": RISK_REVERSIBLE_SYS, "unmute": RISK_REVERSIBLE_SYS,
        "toggle_mute": RISK_REVERSIBLE_SYS,
        "brightness_up": RISK_REVERSIBLE_SYS,
        "brightness_down": RISK_REVERSIBLE_SYS,
        "brightness_set": RISK_REVERSIBLE_SYS,
        "set_brightness": RISK_REVERSIBLE_SYS,
        "dark_mode": RISK_REVERSIBLE_SYS,
        "sleep_display": RISK_REVERSIBLE_SYS, "screen_off": RISK_REVERSIBLE_SYS,
        "full_screen": RISK_REVERSIBLE_SYS, "fullscreen": RISK_REVERSIBLE_SYS,
        "show_desktop": RISK_REVERSIBLE_SYS, "lock_screen": RISK_REVERSIBLE_SYS,
        "media_play_pause": RISK_REVERSIBLE_SYS, "media_next": RISK_REVERSIBLE_SYS,
        "media_previous": RISK_REVERSIBLE_SYS, "media_stop": RISK_REVERSIBLE_SYS,
        "play_pause": RISK_REVERSIBLE_SYS, "next_track": RISK_REVERSIBLE_SYS,
        "previous_track": RISK_REVERSIBLE_SYS,
        "media_pause": RISK_REVERSIBLE_SYS, "media_stop": RISK_REVERSIBLE_SYS,
        "seek_forward": RISK_REVERSIBLE_SYS, "seek_backward": RISK_REVERSIBLE_SYS,
        "fast_forward": RISK_REVERSIBLE_SYS, "rewind": RISK_REVERSIBLE_SYS,
        "escape": RISK_REVERSIBLE_SYS, "undo": RISK_REVERSIBLE_SYS,
        "redo": RISK_REVERSIBLE_SYS, "select_all": RISK_REVERSIBLE_SYS,
        "copy": RISK_REVERSIBLE_SYS, "scroll_up": RISK_REVERSIBLE_SYS,
        "scroll_down": RISK_REVERSIBLE_SYS, "scroll_top": RISK_REVERSIBLE_SYS,
        "scroll_bottom": RISK_REVERSIBLE_SYS, "page_up": RISK_REVERSIBLE_SYS,
        "page_down": RISK_REVERSIBLE_SYS, "zoom_in": RISK_REVERSIBLE_SYS,
        "zoom_out": RISK_REVERSIBLE_SYS, "zoom_reset": RISK_REVERSIBLE_SYS,
        "new_tab": RISK_REVERSIBLE_SYS, "minimize": RISK_REVERSIBLE_SYS,
        "maximize": RISK_REVERSIBLE_SYS, "switch_window": RISK_REVERSIBLE_SYS,
        "focus_search": RISK_REVERSIBLE_SYS, "open_settings": RISK_REVERSIBLE_SYS,
        "file_explorer": RISK_REVERSIBLE_SYS, "refresh_page": RISK_REVERSIBLE_SYS,
        "reload": RISK_REVERSIBLE_SYS, "go_back": RISK_REVERSIBLE_SYS,
        "go_forward": RISK_REVERSIBLE_SYS, "next_tab": RISK_REVERSIBLE_SYS,
        "prev_tab": RISK_REVERSIBLE_SYS, "energy_status": RISK_READ,
        "task_manager": RISK_READ, "find_on_page": RISK_READ,
        "battery": RISK_READ, "battery_status": RISK_READ,
        "power_status": RISK_READ, "energy": RISK_READ,
        # The screen is the privacy of the person in front of it.
        "screenshot": RISK_PRIVACY,
        # Writing into another program's window: it may well be sending.
        "write": RISK_EXTERNAL, "type": RISK_EXTERNAL,
        "type_text": RISK_EXTERNAL, "write_on_screen": RISK_EXTERNAL,
        "press_key": RISK_EXTERNAL, "enter": RISK_EXTERNAL,
        "paste": RISK_EXTERNAL, "cut": RISK_EXTERNAL, "save": RISK_EXTERNAL,
        "open_run": RISK_EXTERNAL,
    },
    "computer_control": {
        "screenshot": RISK_PRIVACY, "user_data": RISK_PRIVACY,
        "screen_find": RISK_READ, "inspect_windows": RISK_READ,
        "inspect_controls": RISK_READ, "read_control": RISK_READ,
        "list_processes": RISK_READ, "processes": RISK_READ,
        "task_manager": RISK_READ, "wait": RISK_READ,
        "close_app": RISK_IRREVERSIBLE, "close_application": RISK_IRREVERSIBLE,
        "quit_app": RISK_IRREVERSIBLE,
        "activate_window": RISK_REVERSIBLE_SYS,
        "focus_window": RISK_REVERSIBLE_SYS,
        "move": RISK_REVERSIBLE_SYS, "scroll": RISK_REVERSIBLE_SYS,
        "copy": RISK_REVERSIBLE_SYS,
        # The clicks and the typing are NOT here on purpose: what they are
        # aimed at decides their class, and that is the audit's job. See
        # `_raised_class` below: a click on "Buy now" is EXTERNAL and a
        # click on "Next page" is not.
    },
    "browser_control": {
        "list_browsers": RISK_READ, "get_text": RISK_READ,
        "get_url": RISK_READ, "read_page": RISK_READ,
        "scroll": RISK_READ, "go_to": RISK_READ, "search": RISK_READ,
        "back": RISK_READ, "forward": RISK_READ, "reload": RISK_READ,
        "new_tab": RISK_READ, "open": RISK_READ, "switch": RISK_READ,
        "screenshot": RISK_PRIVACY,
        # The clicks and the typing are aimed at something, and that
        # something is what decides: same rule as `computer_control`.
    },
    "screen_mark": {
        "observe": RISK_READ,
        # What it is aimed at decides, as above.
    },
    "desktop_control": {
        "wallpaper": RISK_WRITE_LOCAL, "wallpaper_url": RISK_WRITE_LOCAL,
        "organize": RISK_WRITE_LOCAL, "clean": RISK_WRITE_LOCAL,
        "current_wallpaper": RISK_READ, "list": RISK_READ, "stats": RISK_READ,
    },
    # ── The network ─────────────────────────────────────────────────────────
    "send_message": {"send": RISK_EXTERNAL},
    "gmail_control": {"send": RISK_EXTERNAL, "search": RISK_READ,
                      "read": RISK_READ},
    "calendar_control": {"create": RISK_EXTERNAL, "today": RISK_READ,
                         "days": RISK_READ, "delete": RISK_IRREVERSIBLE},
    "meet_control": {"create": RISK_EXTERNAL, "new": RISK_EXTERNAL},
    "game_updater": {"": RISK_EXTERNAL, "install": RISK_EXTERNAL,
                     "update": RISK_EXTERNAL, "schedule": RISK_IRREVERSIBLE,
                     "cancel_schedule": RISK_REVERSIBLE_SYS,
                     "list": RISK_READ, "download_status": RISK_READ,
                     "schedule_status": RISK_READ},
    "dev_agent": {"build_project": RISK_EXTERNAL},
    "code_helper": {
        "write": RISK_WRITE_LOCAL, "edit": RISK_WRITE_LOCAL,
        "optimize": RISK_WRITE_LOCAL, "auto": RISK_WRITE_LOCAL,
        "run": RISK_EXTERNAL, "build": RISK_EXTERNAL,
        "screen_debug": RISK_PRIVACY, "explain": RISK_READ,
    },
    "background_tasks": {
        "start": RISK_EXTERNAL, "steer": RISK_EXTERNAL,
        "stop": RISK_REVERSIBLE_SYS, "cancel": RISK_REVERSIBLE_SYS,
        "finish": RISK_REVERSIBLE_SYS, "list": RISK_READ, "status": RISK_READ,
        "result": RISK_READ, "agents": RISK_READ,
    },
    "routines": {
        "add": RISK_EXTERNAL, "update": RISK_EXTERNAL, "remove": RISK_EXTERNAL,
        "run_now": RISK_EXTERNAL, "quiet_hours": RISK_REVERSIBLE_SYS,
        "list": RISK_READ, "cooldown": RISK_REVERSIBLE_SYS,
    },
    "task_planner": {"preview": RISK_READ, "execute": RISK_EXTERNAL,
                     "cancel": RISK_REVERSIBLE_SYS, "status": RISK_READ,
                     "history": RISK_READ, "clear_history": RISK_IRREVERSIBLE},
    "manage_monitor": {"add": RISK_IRREVERSIBLE, "remove": RISK_IRREVERSIBLE,
                       "list": RISK_READ},
    # ── The local machine, quiet ────────────────────────────────────────────
    "save_memory": {"save": RISK_WRITE_LOCAL},
    "reminder": {"schedule": RISK_WRITE_LOCAL},
    "video_player": {"play": RISK_REVERSIBLE_SYS, "stop": RISK_REVERSIBLE_SYS,
                     "pause": RISK_REVERSIBLE_SYS, "resume": RISK_REVERSIBLE_SYS,
                     "mute": RISK_REVERSIBLE_SYS, "unmute": RISK_REVERSIBLE_SYS,
                     "forward": RISK_REVERSIBLE_SYS, "back": RISK_REVERSIBLE_SYS,
                     "seek_to": RISK_REVERSIBLE_SYS},
    "music_player": {"play": RISK_REVERSIBLE_SYS, "play_pause": RISK_REVERSIBLE_SYS,
                     "pause": RISK_REVERSIBLE_SYS, "resume": RISK_REVERSIBLE_SYS,
                     "next": RISK_REVERSIBLE_SYS,
                     "previous": RISK_REVERSIBLE_SYS,
                     "volume": RISK_REVERSIBLE_SYS},
    "spotify_control": {"search": RISK_READ, "play": RISK_REVERSIBLE_SYS,
                        "play_pause": RISK_REVERSIBLE_SYS,
                        "next": RISK_REVERSIBLE_SYS,
                        "previous": RISK_REVERSIBLE_SYS},
    "undo": {"": RISK_REVERSIBLE_SYS},
    "emotional_spectrum": {"": RISK_REVERSIBLE_SYS},
    # ── Reading ─────────────────────────────────────────────────────────────
    "web_search": {"": RISK_READ, "search": RISK_READ, "news": RISK_READ,
                   "research": RISK_READ, "price": RISK_READ,
                   "compare": RISK_READ},
    "youtube_video": {"": RISK_READ, "play": RISK_READ, "summarize": RISK_READ,
                      "get_info": RISK_READ, "trending": RISK_READ},
    "weather_report": {"": RISK_READ},
    "system_status": {"": RISK_READ},
    "assistant_state": {"": RISK_READ},
    "recall_memory": {"": RISK_READ},
    "open_app": {"": RISK_READ},
    # ── The privacy of a person ─────────────────────────────────────────────
    "screen_process": {"": RISK_PRIVACY},
    "close_camera": {"": RISK_PRIVACY},
    "camera": {"": RISK_PRIVACY},
    "shutdown_teemo": {"": RISK_IRREVERSIBLE},
    "flight_finder": {"": RISK_READ},
}

#: The class of a tool whose actions are not listed one by one.
_TOOL_DEFAULT: dict[str, str] = {
    "file_processor": RISK_WRITE_LOCAL,
    "weather_report": RISK_READ,
    "system_status": RISK_READ,
    "assistant_state": RISK_READ,
    "recall_memory": RISK_READ,
    "undo": RISK_REVERSIBLE_SYS,
    "emotional_spectrum": RISK_REVERSIBLE_SYS,
    "dev_agent": RISK_EXTERNAL,
    "shutdown_teemo": RISK_IRREVERSIBLE,
    "flight_finder": RISK_READ,
}

#: How severe each class is, from least to most. It is used in ONE place:
#: to make sure that the audit can RAISE a declared class (a click aimed at
#: "Buy now") but never lower it (a local write stays a local write).
_SEVERITY: tuple[str, ...] = (
    RISK_READ,
    RISK_WRITE_LOCAL,
    RISK_REVERSIBLE_SYS,
    RISK_EXTERNAL,
    RISK_IRREVERSIBLE,
    RISK_CREDENTIAL,
    RISK_PRIVACY,
)

#: What the coarse vocabulary of `core/tool_audit.py` means, in classes.
_COARSE_CLASS: dict[str, str] = {
    "read_or_navigate": RISK_READ,
    "standard": RISK_READ,
    "state_change": RISK_REVERSIBLE_SYS,
    "high": RISK_IRREVERSIBLE,
    "external": RISK_EXTERNAL,
}

#: A tool whose NAME says it touches credentials. The name is the only thing
#: read here: a model cannot rename itself into this class or out of it.
_CREDENTIAL_NAME_RE = re.compile(
    r"(?:credential|password|passwd|secret|token|api[_-]?key|apikey|keyring|"
    r"private[_-]?key)",
    re.IGNORECASE,
)

#: A tool whose NAME says it touches the camera, the microphone or the screen.
_PRIVACY_NAME_RE = re.compile(
    r"(?:camera|microphone|\bmic\b|screen_capture|recorder|webcam|"
    r"screen_process|screen_mark)",
    re.IGNORECASE,
)


def _coarse_risk(tool: str, arguments: dict | None = None) -> str:
    """The coarse vocabulary of the audit, for a tool this table does not know."""
    try:
        from core import tool_audit
        args = dict(arguments) if isinstance(arguments, dict) else {}
        return tool_audit.classify_risk(tool, args)
    except Exception:
        return "unclassified"


def _control_target_class(tool: str, arguments: dict | None = None) -> str:
    """What a routine control action is aimed at, according to the audit."""
    try:
        from core import tool_audit
        args = dict(arguments) if isinstance(arguments, dict) else {}
        return tool_audit.control_target_class(tool, args)
    except Exception:
        return ""


def _declared_class(tool: str, action: str) -> str | None:
    """The class this table declares for an action, if it declares one.

    The empty action is the class of the REST of the tool: a tool that
    declares `"": READ` and nothing else says that every action of it is
    a reading, and not that only the empty action is.
    """
    table = _ACTION_CLASS.get(tool)
    if table is None:
        return None
    if action in table:
        return table[action]
    if "" in table:
        return table[""]
    return _TOOL_DEFAULT.get(tool)


def _raised_class(tool: str, arguments: dict | None = None) -> str | None:
    """What the audit knows about the TARGET of this action.

    A click, a double click and a typed word are not classes by themselves:
    what they are aimed at is. A click on "Buy now" reaches the network
    (EXTERNAL); a click on a field called "Password" carries a secret
    (CREDENTIAL); a click on "Next page" is an ordinary local thing.

    The vocabulary lives in `core/tool_audit.py`, which is where it has
    always lived: this asks for it instead of keeping a second copy of the
    same word lists that would drift.
    """
    kind = _control_target_class(tool, arguments)
    if kind == "credential":
        return RISK_CREDENTIAL
    if kind == "sensitive":
        return RISK_EXTERNAL
    return None


def _max_class(first: str, second: str) -> str:
    """The more severe of two classes."""
    try:
        left = _SEVERITY.index(first)
    except ValueError:
        left = len(_SEVERITY) - 1
    try:
        right = _SEVERITY.index(second)
    except ValueError:
        right = len(_SEVERITY) - 1
    return first if left >= right else second


def risk_class_of(tool: str, action: str = "",
                  arguments: dict | None = None) -> str:
    """The class of an action, re-derived from the tool that was resolved.

    NEVER from a label: the arguments are read for the action and for
    nothing else, so a model that writes `{"risk": "READ"}` into its own
    arguments changes nothing. The order is:

      1. the fine table, when the tool is in it;
      2. the target of the action, when it is a routine control — the
         audit can only RAISE what the table declares, never lower it;
      3. the name of the tool, for credentials and for privacy;
      4. the coarse vocabulary of the audit.

    A tool that none of the four can place is UNREVIEWED, and `evaluate`
    refuses it: an action with no policy is not a low-risk action, it is an
    unknown one.
    """
    name = str(tool or "").strip().lower()
    act = str(action or "").strip().lower()
    args = dict(arguments) if isinstance(arguments, dict) else {}
    if act and "action" not in args:
        args["action"] = act

    declared = _declared_class(name, act)
    raised = _raised_class(name, args)
    if declared is not None:
        return _max_class(declared, raised) if raised else declared
    if raised is not None:
        return raised
    if _CREDENTIAL_NAME_RE.search(name):
        return RISK_CREDENTIAL
    if _PRIVACY_NAME_RE.search(name):
        return RISK_PRIVACY
    coarse = _coarse_risk(name, args)
    return _COARSE_CLASS.get(coarse, RISK_IRREVERSIBLE)


def class_is_known(tool: str, action: str = "") -> bool:
    """Whether the class of this action comes from somewhere real.

    "Somewhere real" means: the fine table declares it, the name of the
    tool says what it is (credentials, privacy), or the audit can place it.
    When none of the three can, the action has NO class, and the gate
    fails closed on it: an action nobody looked at is not a safe action,
    it is an unknown one.
    """
    name = str(tool or "").strip().lower()
    act = str(action or "").strip().lower()
    if _declared_class(name, act) is not None:
        return True
    if _CREDENTIAL_NAME_RE.search(name) or _PRIVACY_NAME_RE.search(name):
        return True
    probe = {"action": act, "mode": act} if act else {}
    return _coarse_risk(name, probe) != "unclassified"


def is_reviewed(tool: str) -> bool:
    """Whether this tool has a policy of its own.

    A tool that has none is refused by `evaluate`, because "nothing said
    about it" is not "it is harmless".
    """
    name = str(tool or "").strip().lower()
    if name in _ACTION_CLASS or name in _TOOL_DEFAULT:
        return True
    if _CREDENTIAL_NAME_RE.search(name) or _PRIVACY_NAME_RE.search(name):
        return True
    return _coarse_risk(name) != "unclassified"


# ── The grants ──────────────────────────────────────────────────────────────
#
# A grant is the user's permission for ONE tool (and optionally one action
# of it), with an optional expiry. It is stored in the configuration file
# beside the other settings, so it survives a restart, and it is kept in
# memory as well, so a change takes effect at once.
#
# The format in `config/api_keys.json` is:
#
#     "permissions": [
#         {"tool": "file_controller", "action": "", "expires_at": 1764000000.0}
#     ]
#
# `action` empty means "every action of that tool". `expires_at` is a UNIX
# timestamp; null means "no expiry".

_grants: dict[tuple[str, str], "Grant"] = {}
_grants_lock = threading.RLock()
_CONFIG_KEY = "permissions"
_READ_ONLY_KEY = "read_only"


@dataclass(frozen=True)
class Grant:
    """One permission the user has given."""

    tool: str
    action: str          # "" = every action of that tool
    expires_at: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.tool, self.action)

    def expired(self, now: float | None = None) -> bool:
        """Whether this grant has run out."""
        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) >= float(self.expires_at)


@dataclass(frozen=True)
class PolicyVerdict:
    """The verdict of the policy on one action."""

    allowed: bool
    risk_class: str
    reason: str
    needs_confirmation: bool
    read_only: bool
    granted: bool
    tool: str
    action: str


def _config() -> dict:
    """The configuration, read the one way there is to read it."""
    try:
        from memory.config_manager import load_api_keys
        data = load_api_keys()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_config(**fields) -> bool:
    try:
        from memory.config_manager import update_config
        update_config(**fields)
        return True
    except Exception:
        return False


def read_only_enabled() -> bool:
    """Whether the assistant is in read-only mode.

    ON by default, and it stays ON when the configuration cannot be read: a
    file that is corrupt must not turn into a permission.
    """
    value = _config().get(_READ_ONLY_KEY, True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off")
    return True


def set_read_only(enabled: bool) -> None:
    """Turn the read-only mode on or off, and remember it."""
    _write_config(**{_READ_ONLY_KEY: bool(enabled)})


def _stored_grants() -> list[Grant]:
    """The grants saved in the configuration, without the expired ones."""
    raw = _config().get(_CONFIG_KEY)
    if not isinstance(raw, list):
        return []
    out: list[Grant] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip().lower()
        if not tool:
            continue
        action = str(item.get("action") or "").strip().lower()
        expires = item.get("expires_at")
        try:
            expires_at = float(expires) if expires is not None else None
        except (TypeError, ValueError):
            expires_at = None
        entry = Grant(tool=tool, action=action, expires_at=expires_at)
        if not entry.expired():
            out.append(entry)
    return out


def _persist_grants() -> bool:
    """Write the live grants back to the configuration."""
    with _grants_lock:
        rows = [
            {"tool": g.tool, "action": g.action, "expires_at": g.expires_at}
            for g in _grants.values()
        ]
    return _write_config(**{_CONFIG_KEY: rows})


def grants() -> list[Grant]:
    """Every live grant: the ones in memory and the ones on disk."""
    now = time.time()
    with _grants_lock:
        for key, entry in list(_grants.items()):
            if entry.expired(now):
                _grants.pop(key, None)
        live = dict(_grants)
    for entry in _stored_grants():
        live.setdefault(entry.key, entry)
    return sorted(live.values(), key=lambda g: (g.tool, g.action))


def grant(tool: str, action: str = "",
          ttl_seconds: float | None = None) -> Grant:
    """Allow one tool (and optionally one action of it).

    The grant lives in memory as well, so it takes effect at once, and it is
    written to the configuration so it survives a restart. If the write
    fails, the grant stays in memory: losing the user's permission because
    a file could not be saved would be worse than not saving it.
    """
    name = str(tool or "").strip().lower()
    if not name:
        raise ValueError("a grant needs a tool")
    act = str(action or "").strip().lower()
    expires_at = None
    if ttl_seconds is not None:
        try:
            expires_at = time.time() + float(ttl_seconds)
        except (TypeError, ValueError):
            expires_at = None
    entry = Grant(tool=name, action=act, expires_at=expires_at)
    with _grants_lock:
        _grants[entry.key] = entry
    _persist_grants()
    return entry


def revoke(tool: str, action: str = "") -> bool:
    """Take a permission back. True when there was one to take."""
    name = str(tool or "").strip().lower()
    act = str(action or "").strip().lower()
    with _grants_lock:
        removed = _grants.pop((name, act), None) is not None
        if act:
            # A grant for "every action" covers this one too.
            removed = _grants.pop((name, ""), None) is not None or removed
    persisted = _persist_grants()
    return bool(removed or persisted)


def is_granted(tool: str, action: str = "") -> bool:
    """Whether the user has allowed this tool (and this action of it)."""
    name = str(tool or "").strip().lower()
    act = str(action or "").strip().lower()
    now = time.time()
    with _grants_lock:
        live = list(_grants.values())
    for entry in list(live) + _stored_grants():
        if entry.expired(now):
            continue
        if entry.tool != name:
            continue
        if entry.action in ("", act):
            return True
    return False


def granted_tools() -> list[str]:
    """The tools with a live grant."""
    return sorted({g.tool for g in grants()})


def tool_class(tool: str) -> str:
    """The worst class of the actions a tool declares.

    A tool is not one class: `file_controller` reads, writes and deletes.
    The badge the panel shows next to a tool is the worst of them, because
    that is the one the user has to decide about.
    """
    name = str(tool or "").strip().lower()
    table = _ACTION_CLASS.get(name)
    classes: list[str] = list(table.values()) if table else []
    default = _TOOL_DEFAULT.get(name)
    if default:
        classes.append(default)
    if not classes:
        if _CREDENTIAL_NAME_RE.search(name):
            return RISK_CREDENTIAL
        if _PRIVACY_NAME_RE.search(name):
            return RISK_PRIVACY
        coarse = _coarse_risk(name)
        return _COARSE_CLASS.get(coarse, RISK_IRREVERSIBLE)
    worst = classes[0]
    for klass in classes[1:]:
        worst = _max_class(worst, klass)
    return worst


def tools_above_read() -> list[str]:
    """Every tool whose class is not READ — what the panel has to offer.

    The TOOLS, not their actions: a permission is granted for a tool, so
    this is the list the permissions panel walks.
    """
    names: set[str] = set(_ACTION_CLASS.keys())
    names.update(_TOOL_DEFAULT.keys())
    out = []
    for name in sorted(names):
        if tool_class(name) != RISK_READ:
            out.append(name)
    return out


def describe(tool: str, action: str = "") -> str:
    """One line a person can read: the class, and whether it is allowed."""
    name = str(tool or "").strip().lower()
    klass = risk_class_of(name, action)
    if klass == RISK_READ:
        return f"{name}: reading only."
    if is_granted(name, action):
        return f"{name}: allowed ({klass})."
    if read_only_enabled():
        return f"{name}: not allowed — read-only mode is on ({klass})."
    return f"{name}: allowed with confirmation ({klass})."


def evaluate(tool: str, action: str = "",
             arguments: dict | None = None) -> PolicyVerdict:
    """The verdict of the policy on one action.

    This is the question the executor asks, and the only place it is asked:
    "may this happen, and does it need the user to see it first?".
    """
    name = str(tool or "").strip().lower()
    act = str(action or "").strip().lower()
    klass = risk_class_of(name, act, arguments)
    entry = RISK_CATALOG.get(klass, RISK_CATALOG[RISK_IRREVERSIBLE])
    read_only = read_only_enabled()
    allowed_grant = is_granted(name, act)

    if not is_reviewed(name):
        return PolicyVerdict(
            False, klass,
            f"'{name}' has no reviewed risk classification, so it is not "
            "allowed to act. Nothing was done.",
            entry.needs_confirmation, read_only, False, name, act)

    if klass == RISK_READ:
        return PolicyVerdict(True, klass, "", False, read_only,
                             allowed_grant, name, act)

    if allowed_grant:
        # A concession IS the permission: the user has already said yes to
        # this tool, and asking again is what the plan calls "training the
        # user to say yes without reading" (D-06). The action is still
        # recorded, with its class, in the audit log.
        return PolicyVerdict(True, klass, "the user has allowed this tool",
                             False, read_only, True, name, act)

    if read_only:
        what = "write" if klass in (RISK_WRITE_LOCAL, RISK_REVERSIBLE_SYS) else klass
        return PolicyVerdict(
            False, klass,
            f"'{name}' needs permission to {what}, and read-only mode is on. "
            f"Allow '{name}' in the permissions panel (Controls > Tools > "
            "Permissions), or turn read-only mode off there, and try again. "
            "Nothing was done.",
            entry.needs_confirmation, True, False, name, act)

    return PolicyVerdict(True, klass, "", entry.needs_confirmation,
                         read_only, False, name, act)


# ── Secrets in plain sight ──────────────────────────────────────────────────
#
# The confirmation banner shows the action that was RESOLVED: the tool, the
# target and the arguments. A secret in that picture is masked and not
# hidden: the user has to SEE that something secret is being sent, without
# the secret being readable on the screen or in the log.

_SENSITIVE_KEY_RE = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|apikey|authorization|"
    r"credential|private[_-]?key|otp|passphrase)",
    re.IGNORECASE,
)

#: The keys that NAME what a control is. One of them holding a sensitive name
#: makes the values that go INTO that control sensitive too: a password field
#: is called "Password", and the text typed into it is a secret even though
#: the key that carries it is called "text".
_SENSITIVE_TARGET_KEYS = {
    "automation_id", "control_name", "control_type", "label", "name",
    "placeholder", "selector", "target", "window_title",
}

#: The keys that carry the VALUE that goes into a control.
_SENSITIVE_VALUE_KEYS = {"content", "text", "value"}


def _has_sensitive_target(value) -> bool:
    """Whether this part of the arguments names something secret."""
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if str(key).casefold() not in _SENSITIVE_TARGET_KEYS:
            continue
        if isinstance(item, dict):
            if _has_sensitive_target(item):
                return True
        elif _SENSITIVE_KEY_RE.search(str(item)):
            return True
    return False


def mask_secret(value: str) -> str:
    """A secret, shortened to what identifies it and nothing more.

    "sk-abc123def456f4a" becomes "sk-…f4a": the prefix that says what kind
    of secret it is, and the last four characters that let the user tell
    two keys apart. Nothing in between, and never the whole thing.
    """
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "…"
    head = text[:3]
    for index, char in enumerate(text[:8]):
        if char in "-_":
            head = text[:index + 1]
            break
    return f"{head}…{text[-4:]}"


def mask_arguments(arguments: dict) -> dict:
    """The arguments of an action, with the secrets masked and PRESENT.

    The key stays, so the user sees that a credential is being sent, and the
    value becomes "sk-…f4a". Two things count as secret:

      * a value whose KEY says so — "api_key", "password", "token";
      * a value that goes INTO a control that says so — the text typed into
        a field called "Password" is a secret even though the key that
        carries it is called "text".

    The whole tree is walked: a secret inside a form is a secret.
    """
    if not isinstance(arguments, dict):
        return {}

    def _mask(value, sensitive: bool = False):
        if isinstance(value, dict):
            return {
                key: _mask(item, sensitive or bool(_SENSITIVE_KEY_RE.search(str(key))))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [_mask(item, sensitive) for item in value]
        if sensitive and isinstance(value, str):
            return mask_secret(value)
        return value

    masked: dict = {}
    for key, item in arguments.items():
        name = str(key)
        if _SENSITIVE_KEY_RE.search(name):
            masked[key] = _mask(item, True)
            continue
        if name.casefold() in _SENSITIVE_VALUE_KEYS and _has_sensitive_target(arguments):
            masked[key] = _mask(item, True)
            continue
        masked[key] = _mask(item, False)
    return masked
