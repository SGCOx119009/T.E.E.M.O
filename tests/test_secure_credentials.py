"""The rules the credential store has to keep.

The store is `core/secure_credentials.py`: Windows DPAPI (user scope) first,
the Windows Credential Locker second, and **no** plaintext and **no** encoded
fallback. If neither is usable the credential is refused, not written down.

Two backends are exercised here:

* the **vault** path — the `keyring` stand-in patched into `sys.modules`
  below stands for the Windows Credential Locker;
* the **dpapi** path — forced with `secure_credentials.set_backend_override`,
  the test hook that lets one machine exercise both.

Nothing here touches the real configuration file, the real vault or the real
DPAPI store: every test works in a temporary directory.
"""

from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import secure_credentials
from memory import config_manager


@contextlib.contextmanager
def _forced_backend(backend: str | None):
    """Force the store's backend for the duration of a test."""
    secure_credentials.set_backend_override(backend)
    try:
        yield
    finally:
        secure_credentials.set_backend_override(None)


class SecureCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        secure_credentials.set_backend_override(None)
        self.addCleanup(secure_credentials.set_backend_override, None)

    # ── The rules the plan lists ────────────────────────────────────────────

    def test_secure_key_is_preferred_over_config_file(self) -> None:
        with (
            patch.dict("sys.modules", {"keyring": _FakeKeyring("secure-key")}),
            patch.object(config_manager, "load_api_keys", return_value={"gemini_api_key": "legacy-key"}),
            TemporaryDirectory() as temp_dir,
            patch.object(config_manager, "CONFIG_FILE", Path(temp_dir) / "api_keys.json"),
            patch.object(config_manager, "CONFIG_DIR", Path(temp_dir)),
        ):
            self.assertEqual(config_manager.get_gemini_key(), "secure-key")

    def test_empty_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            config_manager.save_api_keys(" ")

    def test_non_text_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            config_manager.save_api_keys(None)

    def test_backend_without_valid_priority_is_rejected(self) -> None:
        fake = _FakeKeyring(None)
        fake.backend.priority = None
        with (
            patch.dict("sys.modules", {"keyring": fake}),
        ):
            with self.assertRaisesRegex(RuntimeError, "backend is unavailable"):
                config_manager.save_api_keys("some-secret")

    def test_delete_uses_secure_store_without_returning_secret(self) -> None:
        fake = _FakeKeyring("secure-key")
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            config_file.write_text(
                '{"gemini_api_key": "legacy-key"}', encoding="utf-8"
            )
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
            ):
                self.assertIs(config_manager.delete_gemini_key(), True)
                self.assertIsNone(fake.value)
                self.assertNotIn(
                    "gemini_api_key", config_file.read_text(encoding="utf-8")
                )

    def test_save_uses_secure_store_and_preserves_nonsecret_config(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            config_file.write_text('{"language": "es"}', encoding="utf-8")
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
            ):
                config_manager.save_api_keys("  new-secret  ")

            self.assertEqual(fake.value, "new-secret")
            saved = config_file.read_text(encoding="utf-8")
            self.assertNotIn("new-secret", saved)
            self.assertNotIn("gemini_api_key", saved)
            self.assertIn('"language": "es"', saved)

    def test_saving_key_invalidates_the_live_client_cache(self) -> None:
        fake = _FakeKeyring(None)
        invalidate = Mock()
        gemini_module = type(
            "GeminiModule", (), {"invalidate_api_key_cache": staticmethod(invalidate)}
        )()
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            with (
                patch.dict(
                    "sys.modules",
                    {"keyring": fake, "core.gemini": gemini_module},
                ),
                patch.object(
                    config_manager, "CONFIG_FILE", config_dir / "api_keys.json"
                ),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
            ):
                config_manager.save_api_keys("new-secret")

        invalidate.assert_called_once_with()

    def test_save_failure_restores_previous_secure_key(self) -> None:
        fake = _FakeKeyring("previous-secret")
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            config_file.write_text('{"language": "es"}', encoding="utf-8")
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
                patch.object(
                    config_manager,
                    "_write_api_config",
                    side_effect=OSError("simulated write failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "restored"):
                    config_manager.save_api_keys("new-secret")

            self.assertEqual(fake.value, "previous-secret")

    def test_legacy_key_is_migrated_to_secure_store(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "api_keys.json"
            config_file.write_text(
                '{"gemini_api_key": "legacy-secret", "language": "es"}',
                encoding="utf-8",
            )
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", Path(temp_dir)),
            ):
                self.assertEqual(config_manager.get_gemini_key(), "legacy-secret")

            saved_config = config_file.read_text(encoding="utf-8")
            self.assertEqual(fake.value, "legacy-secret")
            self.assertNotIn("legacy-secret", saved_config)
            self.assertIn('"language": "es"', saved_config)

    def test_legacy_cleanup_failure_rolls_back_new_secure_key(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            config_file.write_text(
                '{"gemini_api_key": "legacy-secret"}', encoding="utf-8"
            )
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
                patch.object(
                    config_manager,
                    "_write_api_config",
                    side_effect=OSError("simulated write failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "rolled back"):
                    config_manager.get_gemini_key()

            self.assertIsNone(fake.value)
            self.assertIn("legacy-secret", config_file.read_text(encoding="utf-8"))

    def test_existing_secure_key_removes_stale_legacy_copy(self) -> None:
        fake = _FakeKeyring("secure-secret")
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            config_file.write_text(
                '{"gemini_api_key": "legacy-secret", "language": "es"}',
                encoding="utf-8",
            )
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
            ):
                self.assertEqual(config_manager.get_gemini_key(), "secure-secret")

            saved = config_file.read_text(encoding="utf-8")
            self.assertNotIn("legacy-secret", saved)
            self.assertIn('"language": "es"', saved)

    def test_existing_secure_key_reports_stale_copy_cleanup_failure(self) -> None:
        fake = _FakeKeyring("secure-secret")
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            config_file.write_text(
                '{"gemini_api_key": "legacy-secret"}', encoding="utf-8"
            )
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
                patch.object(
                    config_manager,
                    "_write_api_config",
                    side_effect=OSError("simulated write failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "could not be removed"):
                    config_manager.get_gemini_key()

            self.assertEqual(fake.value, "secure-secret")
            self.assertIn("legacy-secret", config_file.read_text(encoding="utf-8"))

    def test_non_object_json_does_not_break_config_load_or_key_lookup(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            config_file.write_text("null", encoding="utf-8")
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
            ):
                self.assertEqual(config_manager.load_api_keys(), {})
                with self.assertRaisesRegex(ValueError, "JSON object"):
                    config_manager.get_gemini_key()

    def test_plaintext_keyring_is_rejected_without_config_fallback(self) -> None:
        fake = _FakeKeyring(None, backend_name="PlaintextKeyring")
        with TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "config" / "api_keys.json"
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_file.parent),
            ):
                with self.assertRaisesRegex(RuntimeError, "Plaintext"):
                    config_manager.save_api_keys("must-not-be-saved")
            self.assertFalse(config_file.exists())


class SecureStoreTests(unittest.TestCase):
    """The store itself: DPAPI, the binary file, and the refusal to fall back."""

    def setUp(self) -> None:
        secure_credentials.set_backend_override(None)
        self.addCleanup(secure_credentials.set_backend_override, None)

    # ── DPAPI ───────────────────────────────────────────────────────────────

    def test_dpapi_round_trip_returns_the_same_text(self) -> None:
        blob = secure_credentials.protect("secreto-de-prueba")
        self.assertIsInstance(blob, bytes)
        self.assertNotIn(b"secreto-de-prueba", blob)
        self.assertEqual(
            secure_credentials.unprotect(blob), "secreto-de-prueba"
        )

    def test_dpapi_is_available_on_this_machine(self) -> None:
        self.assertTrue(secure_credentials.dpapi_available())

    def test_backend_is_dpapi_when_both_stores_are_usable(self) -> None:
        self.assertEqual(secure_credentials.backend_name(), "dpapi")

    def test_store_file_never_contains_the_plain_secret(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "secrets.bin"
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                _forced_backend("dpapi"),
            ):
                secure_credentials.store_secret(
                    "gemini_api_key", "secreto-de-prueba", store_path=store
                )
                raw = store.read_bytes()
                self.assertNotIn(b"secreto-de-prueba", raw)
                self.assertTrue(raw)
                self.assertEqual(
                    secure_credentials.read_secret(
                        "gemini_api_key", store_path=store
                    ),
                    "secreto-de-prueba",
                )

    def test_module_source_has_no_base64_fallback(self) -> None:
        source = (BASE / "core" / "secure_credentials.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("base64", "b64encode", "b64decode"):
            self.assertNotIn(forbidden, source.casefold())

    # ── The refusal ─────────────────────────────────────────────────────────

    def test_no_backend_at_all_is_refused_with_a_clear_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "secrets.bin"
            with (
                patch.object(
                    secure_credentials, "dpapi_available", return_value=False
                ),
                patch.dict("sys.modules", {"keyring": None}),
                _forced_backend(None),
            ):
                with self.assertRaisesRegex(
                    secure_credentials.SecureStoreError,
                    "no DPAPI and no Windows credential vault",
                ):
                    secure_credentials.store_secret(
                        "gemini_api_key", "secreto-de-prueba", store_path=store
                    )
            self.assertFalse(store.exists())

    # ── The vault → DPAPI migration ─────────────────────────────────────────

    def test_vault_secret_is_migrated_to_dpapi_and_the_vault_is_cleaned(self) -> None:
        fake = _FakeKeyring("secreto-del-vault")
        with TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "secrets.bin"
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                _forced_backend("dpapi"),
            ):
                value = secure_credentials.read_secret(
                    "gemini_api_key", store_path=store
                )
            self.assertEqual(value, "secreto-del-vault")
            self.assertIsNone(fake.value)
            self.assertTrue(store.exists())
            self.assertNotIn(b"secreto-del-vault", store.read_bytes())

    def test_failed_migration_keeps_the_vault_copy(self) -> None:
        fake = _FakeKeyring("secreto-del-vault")
        with TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "secrets.bin"
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                _forced_backend("dpapi"),
                patch.object(
                    secure_credentials.os,
                    "replace",
                    side_effect=OSError("simulated write failure"),
                ),
            ):
                with self.assertRaisesRegex(
                    secure_credentials.SecureStoreError, "vault copy"
                ):
                    secure_credentials.read_secret(
                        "gemini_api_key", store_path=store
                    )
            self.assertEqual(fake.value, "secreto-del-vault")
            self.assertFalse(store.exists())

    # ── Writes and deletes ──────────────────────────────────────────────────

    def test_failed_write_is_rolled_back_and_leaves_no_half_store(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "secrets.bin"
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                _forced_backend("dpapi"),
            ):
                secure_credentials.store_secret(
                    "gemini_api_key", "primer-secreto", store_path=store
                )
                before = store.read_bytes()
                with patch.object(
                    secure_credentials.os,
                    "replace",
                    side_effect=OSError("simulated write failure"),
                ):
                    with self.assertRaises(
                        secure_credentials.SecureStoreError
                    ):
                        secure_credentials.store_secret(
                            "gemini_api_key", "segundo-secreto", store_path=store
                        )
                self.assertEqual(store.read_bytes(), before)
                self.assertEqual(
                    secure_credentials.read_secret(
                        "gemini_api_key", store_path=store
                    ),
                    "primer-secreto",
                )

    def test_delete_secret_never_returns_the_secret(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "secrets.bin"
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                _forced_backend("dpapi"),
            ):
                secure_credentials.store_secret(
                    "gemini_api_key", "secreto-de-prueba", store_path=store
                )
                result = secure_credentials.delete_secret(
                    "gemini_api_key", store_path=store
                )
                self.assertIsNone(result)
                self.assertIsNone(
                    secure_credentials.read_secret(
                        "gemini_api_key", store_path=store
                    )
                )


class ConfigManagerDpapiTests(unittest.TestCase):
    """The application's own credential layer, on the DPAPI store."""

    def setUp(self) -> None:
        secure_credentials.set_backend_override(None)
        self.addCleanup(secure_credentials.set_backend_override, None)

    def test_saved_key_goes_to_the_dpapi_store_and_not_the_config_file(self) -> None:
        fake = _FakeKeyring(None)
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "api_keys.json"
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(config_manager, "CONFIG_FILE", config_file),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
                _forced_backend("dpapi"),
            ):
                config_manager.save_api_keys("clave-de-prueba")
                store = config_dir / "secrets.bin"
                self.assertTrue(store.exists())
                self.assertNotIn(b"clave-de-prueba", store.read_bytes())
                self.assertEqual(
                    config_manager.get_gemini_key(), "clave-de-prueba"
                )
            self.assertNotIn(
                "clave-de-prueba", config_file.read_text(encoding="utf-8")
            )

    def test_vault_key_is_migrated_to_dpapi_when_it_is_read(self) -> None:
        fake = _FakeKeyring("clave-del-vault")
        with TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            with (
                patch.dict("sys.modules", {"keyring": fake}),
                patch.object(
                    config_manager, "CONFIG_FILE", config_dir / "api_keys.json"
                ),
                patch.object(config_manager, "CONFIG_DIR", config_dir),
                _forced_backend("dpapi"),
            ):
                self.assertEqual(
                    config_manager.get_gemini_key(), "clave-del-vault"
                )
                self.assertIsNone(fake.value)
                store = config_dir / "secrets.bin"
                self.assertTrue(store.exists())
                self.assertNotIn(b"clave-del-vault", store.read_bytes())


class _FakeKeyring:
    def __init__(self, value: str | None, backend_name: str = "WinVaultKeyring"):
        self.value = value
        self.values: dict[str, str] = {}
        self.backend = type(backend_name, (), {"priority": 5})()

    def get_keyring(self):
        return self.backend

    def get_password(self, service: str, account: str) -> str | None:
        if account == "gemini_api_key":
            return self.value
        return self.values.get(account)

    def set_password(self, service: str, account: str, value: str) -> None:
        if account == "gemini_api_key":
            self.value = value
        else:
            self.values[account] = value

    def delete_password(self, service: str, account: str) -> None:
        if account == "gemini_api_key":
            self.value = None
        else:
            self.values.pop(account, None)
