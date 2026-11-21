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
