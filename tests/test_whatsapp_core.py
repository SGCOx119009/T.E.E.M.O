from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugins import _whatsapp_core as whatsapp_core


class _FakeControl:
    def __init__(
        self, kind: str, name: str, value: str = "", parent=None,
    ) -> None:
        self.value = value
        self.clicked = False
        self.focused = False
        self._parent = parent
        self.element_info = SimpleNamespace(
            control_type=kind,
            name=name,
            automation_id="",
            element=SimpleNamespace(CurrentHasKeyboardFocus=False),
        )

    def set_edit_text(self, value: str) -> None:
        self.value = value

    def get_value(self) -> str:
        return self.value

    def click_input(self) -> None:
        self.clicked = True

    def set_focus(self) -> None:
        self.focused = True
        self.element_info.element.CurrentHasKeyboardFocus = True

    def parent(self):
        return self._parent


class _FakeWindow:
    def __init__(self) -> None:
        self.active = True
        self.handle = 123

    def set_focus(self) -> None:
        self.active = True

    def is_active(self) -> bool:
        return self.active


class WhatsAppDesktopTests(unittest.TestCase):
    @staticmethod
    def _type_into_fake_control(control, value: str) -> bool:
        control.value = value
        control.set_focus()
        return True

    def _controls(self, message: str = ""):
        search = _FakeControl("Edit", "Buscar un chat o iniciar uno nuevo")
        recipient = _FakeControl("ListItem", "Casa de Edith")
        header = _FakeControl("Text", "Casa de Edith")
        composer = _FakeControl("Edit", "Escribe un mensaje")
        controls = [search, recipient, header, composer]
        if message:
            controls.append(_FakeControl("Text", message))
        return controls, search, recipient, composer

    def test_exact_recipient_message_and_post_send_readback_are_required(self) -> None:
        message = "testeo controlado"
        controls, search, recipient, composer = self._controls()
        window = _FakeWindow()
        transport = whatsapp_core.WhatsAppDesktop(window, controls)

        def fake_press(key: str) -> None:
            self.assertEqual(key, "enter")
            controls.append(_FakeControl("Text", message))

        with (
            patch.object(whatsapp_core, "_window_controls", return_value=controls),
            patch.object(
                whatsapp_core, "_type_unicode_into_control",
                side_effect=self._type_into_fake_control,
            ),
            patch("pyautogui.press", side_effect=fake_press) as press,
        ):
            sent, reason = transport.send_message_to(
                "Casa de Edith", message
            )

        self.assertTrue(sent, reason)
        self.assertEqual(search.value, "Casa de Edith")
        self.assertTrue(recipient.clicked)
        self.assertEqual(composer.value, message)
        self.assertTrue(composer.focused)
        press.assert_called_once_with("enter")

    def test_nested_data_items_resolve_only_the_top_level_exact_chat_row(self) -> None:
        grid = _FakeControl("DataGrid", "Chats")
        row = _FakeControl(
            "DataItem", "Mama Luisa: último mensaje", parent=grid,
        )
        nested_label = _FakeControl(
            "DataItem", "Mama Luisa", parent=row,
        )

        matches = whatsapp_core._recipient_items(
            [row, nested_label], "mamá luisa",
        )

        self.assertEqual(matches, [row])

    def test_raw_data_item_name_must_start_at_recipient_boundary(self) -> None:
        self.assertTrue(whatsapp_core._name_starts_at_boundary(
            "Yampier Ayer: last message", "Yampier",
        ))
        self.assertTrue(whatsapp_core._name_starts_at_boundary(
            "Yampier: last message", "Yampier",
        ))
        self.assertFalse(whatsapp_core._name_starts_at_boundary(
            "Yampier2", "Yampier",
        ))
        self.assertFalse(whatsapp_core._name_starts_at_boundary(
            "Other Yampier", "Yampier",
        ))

    def test_data_item_chat_can_be_prepared_without_sending(self) -> None:
        search = _FakeControl("Edit", "Buscar un chat o iniciar uno nuevo")
        grid = _FakeControl("DataGrid", "Chats")
        row = _FakeControl(
            "DataItem", "Mama Luisa: último mensaje", parent=grid,
        )
        nested_label = _FakeControl(
            "DataItem", "Mama Luisa", parent=row,
        )
        header = _FakeControl("Text", "Mama Luisa")
        composer = _FakeControl("Edit", "Escribe un mensaje")
        controls = [search, grid, row, nested_label, header, composer]
        transport = whatsapp_core.WhatsAppDesktop(_FakeWindow(), controls)

        with patch.object(
            whatsapp_core, "_window_controls", return_value=controls,
        ), patch.object(
            whatsapp_core, "_type_unicode_into_control",
            side_effect=self._type_into_fake_control,
        ):
            displayed_name, reason = transport.prepare_recipient("mamá luisa")

        self.assertEqual(reason, "")
        self.assertEqual(displayed_name, "Mama Luisa")
        self.assertTrue(row.clicked)
        self.assertFalse(nested_label.clicked)
        self.assertEqual(composer.value, "")

    def test_ambiguous_chat_match_stops_before_enter(self) -> None:
        controls = self._controls()[0]
        controls.insert(2, _FakeControl("ListItem", "Casa de Edith"))
        transport = whatsapp_core.WhatsAppDesktop(_FakeWindow(), controls)

        with (
            patch.object(whatsapp_core, "_window_controls", return_value=controls),
            patch.object(
                whatsapp_core, "_type_unicode_into_control",
                side_effect=self._type_into_fake_control,
            ),
            patch("pyautogui.press") as press,
        ):
            sent, reason = transport.send_message_to("Casa de Edith", "test")

        self.assertFalse(sent)
        self.assertIn("multiple WhatsApp chats", reason)
        press.assert_not_called()

    def test_message_is_not_sent_without_unique_chat_search_control(self) -> None:
        controls = [_FakeControl("Edit", "Buscar contactos")]
        transport = whatsapp_core.WhatsAppDesktop(_FakeWindow(), controls)

        with (
            patch.object(whatsapp_core, "_window_controls", return_value=controls),
            patch("pyautogui.press") as press,
        ):
            sent, reason = transport.send_message_to("Mama Luisa", "test")

        self.assertFalse(sent)
        self.assertIn("chat search field", reason)
        press.assert_not_called()

    def test_message_is_not_sent_if_composer_readback_does_not_match(self) -> None:
        setup = self._controls()
        controls, composer = setup[0], setup[3]
        composer.set_edit_text = None
        transport = whatsapp_core.WhatsAppDesktop(_FakeWindow(), controls)

        with (
            patch.object(whatsapp_core, "_window_controls", return_value=controls),
            patch.object(
                whatsapp_core, "_type_unicode_into_control",
                side_effect=lambda control, value: (
                    False if control is composer
                    else self._type_into_fake_control(control, value)
                ),
            ),
            patch("pyautogui.press") as press,
        ):
            sent, reason = transport.send_message_to("Casa de Edith", "test")

        self.assertFalse(sent)
        self.assertIn("verify the exact message", reason)
        press.assert_not_called()

    def test_sent_but_unreadable_result_is_reported_as_unknown_not_failed(self) -> None:
        controls = self._controls()[0]
        transport = whatsapp_core.WhatsAppDesktop(_FakeWindow(), controls)

        with (
            patch.object(whatsapp_core, "_window_controls", return_value=controls),
            patch.object(
                whatsapp_core, "_type_unicode_into_control",
                side_effect=self._type_into_fake_control,
            ),
            patch("pyautogui.press") as press,
            patch.object(whatsapp_core, "_SEND_TIMEOUT", 0),
        ):
            sent, reason = transport.send_message_to(
                "Casa de Edith", "test"
            )

        self.assertIsNone(sent)
        self.assertIn("outcome is unknown", reason)
        press.assert_called_once_with("enter")

    def test_closed_app_uses_existing_open_app_launcher_then_waits_for_window(self) -> None:
        window = _FakeWindow()
        controls = [
            _FakeControl("Text", "WhatsApp"),
            _FakeControl("Edit", "Buscar un chat o iniciar uno nuevo"),
        ]
        with (
            patch.object(whatsapp_core.platform, "system", return_value="Windows"),
            patch.object(
                whatsapp_core, "_visible_windows",
                side_effect=[[], [window]],
            ),
            patch.object(
                whatsapp_core, "_window_controls", return_value=controls,
            ),
            patch.object(whatsapp_core.time, "sleep"),
        ):
            with patch(
                "actions.open_app.open_app",
                return_value="status=started action=open_app",
            ) as launch:
                connected, reason = whatsapp_core._connect_window()

        self.assertEqual(reason, "")
        self.assertIsNotNone(connected)
        launch.assert_called_once_with({"app_name": "WhatsApp"})

    def test_foreground_api_is_used_when_set_focus_does_not_activate_window(self) -> None:
        window = _FakeWindow()
        window.active = False

        def set_foreground(handle: int) -> int:
            self.assertEqual(handle, window.handle)
            window.active = True
            return 1

        with (
            patch.object(whatsapp_core.platform, "system", return_value="Windows"),
            patch.object(
                whatsapp_core.ctypes.windll.user32,
                "SetForegroundWindow",
                side_effect=set_foreground,
            ),
        ):
            self.assertTrue(whatsapp_core._bring_to_front(window))

    def test_whatsapp_window_title_accepts_accessible_instance_suffix(self) -> None:
        self.assertTrue(whatsapp_core._is_whatsapp_title("(2) WhatsApp"))
        self.assertTrue(whatsapp_core._is_whatsapp_title("WhatsApp"))
        self.assertFalse(whatsapp_core._is_whatsapp_title("WhatsApp Settings"))

if __name__ == "__main__":
    unittest.main()
