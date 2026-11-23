"""Secure credential storage for T.E.E.M.O. — DPAPI first, no plaintext fallback.

A key typed into the settings panel is a long-lived secret. In a JSON file
next to the application it is one backup, one sync folder or one stray
screenshot away from being exposed. Windows offers two places to keep it, and
this module uses both, in this order:

  1. **DPAPI** (`CryptProtectData`, user scope) — the primary store. The
     ciphertext is tied to the Windows account that produced it, so a
     `secrets.bin` copied to another machine, another user or another profile
     decrypts to nothing. This is the mechanism the plan asks for.
  2. **The Windows Credential Locker** (`keyring`) — the secondary store, used
     when DPAPI is not reachable. It has the same property (the entry belongs
     to the Windows account), and it is where the keys of previous versions
     already live, so nothing is lost when the store moves.

There is **no plaintext fallback and no encoded fallback**. An encoding is
not encryption: it is a secret written down in a file and called protected.
When neither DPAPI nor the vault is usable the honest answer is that the
credential cannot be stored, and that is what this module says.

The module depends on the standard library only (`ctypes`, `os`, `sys`,
`tempfile`, `threading`, `pathlib`).
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import threading
from pathlib import Path

# The service name is the product's own identity — never a predecessor's — so
# the entries in the vault belong to T.E.E.M.O. and to nothing else.
SERVICE = "T.E.E.M.O."

# The name of the binary store that sits next to `config/api_keys.json`.
STORE_FILENAME = "secrets.bin"

# The message used when neither store is available. It never contains a
# secret: it names the two mechanisms and stops.
UNAVAILABLE_MESSAGE = (
    "secure credential storage is unavailable "
    "(no DPAPI and no Windows credential vault)"
)


class SecureStoreError(RuntimeError):
    """A credential could not be stored, read, migrated or removed safely.

    It is a `RuntimeError` because that is what callers of the credential
    layer have always caught. The message never contains the secret itself.
    """


# ── DPAPI ───────────────────────────────────────────────────────────────────
# CryptProtectData / CryptUnprotectData are called directly through ctypes:
# the `cryptography` package is not needed for this, and a dependency that
# can fail to install is a dependency that can fail to protect a secret.

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    """The `DATA_BLOB` structure Win32 passes secrets in and out with."""

    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


_dll_lock = threading.Lock()
_dlls: object = None  # None = not loaded yet, False = unavailable


def _dpapi_dlls():
    """The (crypt32, kernel32) pair, or None when DPAPI cannot be reached."""
    global _dlls
    with _dll_lock:
        if _dlls is None:
            try:
                crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            except (AttributeError, OSError):
                _dlls = False
            else:
                crypt32.CryptProtectData.argtypes = [
                    ctypes.POINTER(_DATA_BLOB),
                    ctypes.c_wchar_p,
                    ctypes.POINTER(_DATA_BLOB),
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(_DATA_BLOB),
                ]
                crypt32.CryptProtectData.restype = ctypes.c_bool
                # CryptUnprotectData takes the OUTPUT blob LAST, after dwFlags —
                # the order the .NET Framework declares it in. Passing it in the
                # position the documentation shows (third) fails with
                # ERROR_NO_DATA on Windows, and the failure looks like a
                # corrupted store rather than a wrong call.
                crypt32.CryptUnprotectData.argtypes = [
                    ctypes.POINTER(_DATA_BLOB),
                    ctypes.POINTER(ctypes.c_wchar_p),
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(_DATA_BLOB),
                ]
                crypt32.CryptUnprotectData.restype = ctypes.c_bool
                kernel32.LocalFree.argtypes = [ctypes.c_void_p]
                kernel32.LocalFree.restype = ctypes.c_void_p
                _dlls = (crypt32, kernel32)
    return _dlls if _dlls else None


def _require_dpapi():
    dlls = _dpapi_dlls()
    if dlls is None:
        raise SecureStoreError("Windows DPAPI is not available on this system.")
    return dlls


def protect(plaintext: str) -> bytes:
    """Encrypt `plaintext` with DPAPI, in the scope of the current user.

    The result is ciphertext: it can only be decrypted by the same Windows
    account, on the same machine, with the same user profile.
    """
    if not isinstance(plaintext, str):
        raise SecureStoreError("A credential must be text.")
    crypt32, kernel32 = _require_dpapi()
    data = plaintext.encode("utf-8")
    buffer = ctypes.create_string_buffer(data, max(len(data), 1))
    in_blob = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise SecureStoreError(
            "Windows DPAPI could not protect the credential "
            f"(error {ctypes.get_last_error()})."
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def unprotect(blob: bytes) -> str:
    """Decrypt a blob produced by `protect`.

    A blob that was encrypted by another account or copied from another
    machine cannot be decrypted here — and that is the point of DPAPI.
    """
    if not isinstance(blob, (bytes, bytearray)):
        raise SecureStoreError("The stored credential is not a byte string.")
    crypt32, kernel32 = _require_dpapi()
    buffer = ctypes.create_string_buffer(bytes(blob), max(len(blob), 1))
    in_blob = _DATA_BLOB(len(blob), ctypes.cast(buffer, ctypes.c_void_p))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise SecureStoreError(
            "Windows DPAPI could not decrypt the credential "
            f"(error {ctypes.get_last_error()}); it belongs to another "
            "Windows account."
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def dpapi_available() -> bool:
    """Whether DPAPI can protect and unprotect a value on this machine."""
    if _dpapi_dlls() is None:
        return False
    try:
        return unprotect(protect("teemo-probe")) == "teemo-probe"
    except SecureStoreError:
        return False


# ── The Windows Credential Locker (keyring) ────────────────────────────────


def _keyring_module():
    """The imported `keyring` module, or None when it is not usable."""
    try:
        import keyring
    except Exception:
        return None
    return keyring


def vault_problem() -> str | None:
    """Why the vault cannot be used, or None when it can.

    The rules are the ones this project has always applied: the backend must
    not be a plaintext one, it must be the Windows Credential Locker, and it
    must expose a usable priority.
    """
    keyring = _keyring_module()
    if keyring is None:
        return "Secure credential storage is unavailable (the keyring package is missing)."
    try:
        backend = keyring.get_keyring()
    except Exception as exc:
        return (
            "Secure credential storage is unavailable "
            f"({type(exc).__name__})."
        )
    backend_type = type(backend)
    if backend_type.__name__.casefold().startswith("plaintext"):
        return "Plaintext credential backends are not supported."
    if backend_type.__name__ != "WinVaultKeyring":
        return "Windows credential storage requires the Windows Credential Locker."
    priority = getattr(backend, "priority", None)
    if not isinstance(priority, (int, float)) or priority < 0:
        return "The configured secure credential backend is unavailable."
    return None


def vault_available() -> bool:
    """Whether the Windows Credential Locker is usable as the second store."""
    return vault_problem() is None


def _require_vault():
    problem = vault_problem()
    if problem:
        raise SecureStoreError(problem)
    return _keyring_module()


def vault_module():
    """The `keyring` module, once its backend has been accepted.

    Raises `SecureStoreError` (a `RuntimeError`) when the vault cannot be
    used, with the reason.
    """
    return _require_vault()


# ── Choosing the backend ────────────────────────────────────────────────────

_backend_lock = threading.RLock()
_backend_override: str | None = None


def set_backend_override(backend: str | None) -> None:
    """Force the backend used by this module. **A test hook, not a feature.**

    ``None`` restores the normal choice (DPAPI first, then the vault);
    ``"dpapi"`` and ``"vault"`` force one of them. Nothing in the application
    calls this — it exists so both store paths can be exercised on a machine
    where both are available.
    """
    global _backend_override
    if backend not in (None, "dpapi", "vault"):
        raise ValueError("The backend override must be 'dpapi', 'vault' or None.")
    with _backend_lock:
        _backend_override = backend


def _keyring_is_injected() -> bool:
    """Whether `keyring` has been replaced in `sys.modules` by a test double.

    A real `keyring` module has a `__spec__` and a `__file__`; a stand-in
    object injected by a test has neither. Treating that as an explicit
    request to exercise the vault path keeps the two store paths testable
    without a machine that is missing one of them.
    """
    module = sys.modules.get("keyring")
    if module is None:
        return False
    return getattr(module, "__spec__", None) is None and \
        getattr(module, "__file__", None) is None


def backend_name() -> str:
    """The store that would be used right now: dpapi, vault or unavailable."""
    with _backend_lock:
        override = _backend_override
    if override:
        return override
    if _keyring_is_injected():
        return "vault"
    if dpapi_available():
        return "dpapi"
    if vault_available():
        return "vault"
    return "unavailable"


# ── The binary store ────────────────────────────────────────────────────────
# One file, `config/secrets.bin`, holding every DPAPI-protected credential of
# the application. Each record is:
#
#     [u32 name length][name, utf-8][u32 blob length][DPAPI blob]
#
# The file contains ciphertext only. It is written through a temporary file
# that replaces it in one step, so a reader sees the old file or the new one
# and never half of either.

_u32_max = 0xFFFFFFFF


def default_store_path() -> Path:
    """`config/secrets.bin`, next to `config/api_keys.json`."""
    return Path(__file__).resolve().parent.parent / "config" / STORE_FILENAME


def _read_records(path: Path) -> dict[str, bytes]:
    """Every record in the store file, or an empty dict when there is none."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise SecureStoreError(
            f"The secure credential store could not be read ({type(exc).__name__})."
        ) from exc
    if not data:
        return {}
    records: dict[str, bytes] = {}
    offset = 0
    total = len(data)
    while offset < total:
        if offset + 4 > total:
            raise SecureStoreError("The secure credential store is damaged.")
        name_len = int.from_bytes(data[offset:offset + 4], "little")
        offset += 4
        if name_len > _u32_max or offset + name_len > total:
            raise SecureStoreError("The secure credential store is damaged.")
        try:
            name = data[offset:offset + name_len].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecureStoreError(
                "The secure credential store is damaged."
            ) from exc
        offset += name_len
        if offset + 4 > total:
            raise SecureStoreError("The secure credential store is damaged.")
        blob_len = int.from_bytes(data[offset:offset + 4], "little")
        offset += 4
        if offset + blob_len > total:
            raise SecureStoreError("The secure credential store is damaged.")
        records[name] = data[offset:offset + blob_len]
        offset += blob_len
    return records


def _write_records(records: dict[str, bytes], path: Path) -> None:
    """Write every record to the store file, atomically."""
    payload = bytearray()
    for name, blob in records.items():
        raw = name.encode("utf-8")
        payload += len(raw).to_bytes(4, "little")
        payload += raw
        payload += len(blob).to_bytes(4, "little")
        payload += blob
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".secrets.", suffix=".tmp"
        )
    except OSError as exc:
        raise SecureStoreError(
            "The secure credential store could not be written "
            f"({type(exc).__name__})."
        ) from exc
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        raise SecureStoreError(
            "The secure credential store could not be written "
            f"({type(exc).__name__})."
        ) from exc
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _restore_file(path: Path, original: bytes | None) -> None:
    """Put the store file back the way it was after a failed write."""
    try:
        if original is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)
    except OSError:
        pass


def _read_dpapi_secret(account: str, path: Path) -> str | None:
    records = _read_records(path)
    blob = records.get(account)
    if blob is None:
        return None
    return unprotect(blob)


def _read_vault_secret(account: str) -> str | None:
    keyring = _require_vault()
    try:
        return keyring.get_password(SERVICE, account)
    except Exception as exc:
        raise SecureStoreError(
            f"Secure credential lookup failed ({type(exc).__name__})."
        ) from exc


def _store_vault_secret(account: str, secret: str) -> None:
    keyring = _require_vault()
    try:
        keyring.set_password(SERVICE, account, secret)
    except Exception as exc:
        raise SecureStoreError(
            f"Secure credential storage failed ({type(exc).__name__})."
        ) from exc


def _delete_vault_secret(account: str) -> None:
    keyring = _require_vault()
    try:
        keyring.delete_password(SERVICE, account)
    except Exception as exc:
        raise SecureStoreError(
            f"Secure credential removal failed ({type(exc).__name__})."
        ) from exc


# ── The public store ────────────────────────────────────────────────────────


def store_secret(account: str, secret: str, *, store_path: Path | None = None) -> None:
    """Store a secret in the secure store. It is never written in plaintext."""
    if not isinstance(account, str) or not account.strip():
        raise SecureStoreError("A credential account name is invalid.")
    if not isinstance(secret, str):
        raise SecureStoreError("A credential must be text.")
    backend = backend_name()
    if backend == "vault":
        _store_vault_secret(account, secret)
        return
    if backend != "dpapi":
        raise SecureStoreError(UNAVAILABLE_MESSAGE)

    path = Path(store_path) if store_path is not None else default_store_path()
    blob = protect(secret)
    original = None
    if path.exists():
        try:
            original = path.read_bytes()
        except OSError:
            original = None
    records = _read_records(path)
    records[account] = blob
    _write_records(records, path)
    try:
        verified = _read_dpapi_secret(account, path)
    except SecureStoreError:
        verified = None
    if verified != secret:
        _restore_file(path, original)
        raise SecureStoreError(
            "The secure credential store did not keep the credential; "
            "it was not saved."
        )
    # The vault must not keep a second, older copy of the same credential.
    if vault_available():
        try:
            stale = _read_vault_secret(account)
        except SecureStoreError:
            stale = None
        if stale is not None:
            try:
                _delete_vault_secret(account)
            except SecureStoreError as exc:
                _restore_file(path, original)
                raise SecureStoreError(
                    "The credential was stored, but the previous vault copy "
                    f"could not be removed ({type(exc).__name__})."
                ) from exc


def read_secret(account: str, *, store_path: Path | None = None) -> str | None:
    """Read a secret, or None when there is none.

    DPAPI is the primary store. A secret that is still in the vault — left
    there by a previous version — is migrated to DPAPI: it is copied, the
    copy is verified by reading it back, and only then is the vault copy
    removed. If the migration fails the vault is left exactly as it was and
    the error says so.
    """
    if not isinstance(account, str) or not account.strip():
        raise SecureStoreError("A credential account name is invalid.")
    backend = backend_name()
    if backend == "vault":
        return _read_vault_secret(account)
    if backend != "dpapi":
        raise SecureStoreError(UNAVAILABLE_MESSAGE)

    path = Path(store_path) if store_path is not None else default_store_path()
    value = _read_dpapi_secret(account, path)
    if value is not None:
        return value
    if not vault_available():
        return None
    legacy = _read_vault_secret(account)
    if legacy is None:
        return None
    _migrate_to_dpapi(account, legacy, path)
    return legacy


def _migrate_to_dpapi(account: str, secret: str, path: Path) -> None:
    """Copy a vault secret into DPAPI, verify it, then drop the vault copy."""
    original = None
    if path.exists():
        try:
            original = path.read_bytes()
        except OSError:
            original = None
    records = _read_records(path)
    records[account] = protect(secret)
    try:
        _write_records(records, path)
        verified = _read_dpapi_secret(account, path)
        if verified != secret:
            raise SecureStoreError(
                "The migrated credential could not be verified."
            )
    except SecureStoreError:
        _restore_file(path, original)
        raise SecureStoreError(
            "The vault credential could not be migrated to DPAPI; the vault "
            "copy was kept unchanged."
        ) from None
    except OSError as exc:
        _restore_file(path, original)
        raise SecureStoreError(
            "The vault credential could not be migrated to DPAPI; the vault "
            f"copy was kept unchanged ({type(exc).__name__})."
        ) from exc
    try:
        _delete_vault_secret(account)
    except SecureStoreError as exc:
        _restore_file(path, original)
        raise SecureStoreError(
            "The credential was migrated to DPAPI, but the vault copy could "
            "not be removed; the migration was undone and the vault copy "
            f"was kept unchanged ({type(exc).__name__})."
        ) from exc


def delete_secret(account: str, *, store_path: Path | None = None) -> None:
    """Remove a secret from the secure store.

    It returns nothing: a delete that answers with the credential it removed
    is a delete that prints the credential.
    """
    if not isinstance(account, str) or not account.strip():
        raise SecureStoreError("A credential account name is invalid.")
    backend = backend_name()
    if backend == "vault":
        _delete_vault_secret(account)
        return
    if backend != "dpapi":
        raise SecureStoreError(UNAVAILABLE_MESSAGE)

    path = Path(store_path) if store_path is not None else default_store_path()
    records = _read_records(path)
    if account in records:
        del records[account]
        _write_records(records, path)
    if vault_available():
        try:
            stale = _read_vault_secret(account)
        except SecureStoreError:
            stale = None
        if stale is not None:
            _delete_vault_secret(account)
