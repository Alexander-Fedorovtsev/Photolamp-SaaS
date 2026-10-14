import importlib.util
import json
import sys
import unittest
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "infra" / "scripts" / "send-telegram-alert.py"


def load_script():
    spec = importlib.util.spec_from_file_location("send_telegram_alert", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TelegramAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alert = load_script()

    def test_loads_private_group_and_optional_topic(self):
        destination = self.alert.load_destination(
            {
                "TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35,
                "TELEGRAM_ALERT_CHAT_ID": "-1001234567890",
                "TELEGRAM_ALERT_THREAD_ID": "42",
            }
        )
        self.assertEqual(destination.chat_id, "-1001234567890")
        self.assertEqual(destination.thread_id, 42)

    def test_rejects_missing_or_malformed_destination(self):
        invalid = (
            {},
            {
                "TELEGRAM_BOT_TOKEN": "secret",
                "TELEGRAM_ALERT_CHAT_ID": "-1001234567890",
            },
            {
                "TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35,
                "TELEGRAM_ALERT_CHAT_ID": "some chat",
            },
            {
                "TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35,
                "TELEGRAM_ALERT_CHAT_ID": "-1001234567890",
                "TELEGRAM_ALERT_THREAD_ID": "0",
            },
        )
        for environment in invalid:
            with (
                self.subTest(environment=environment),
                self.assertRaises(self.alert.AlertConfigurationError),
            ):
                self.alert.load_destination(environment)

    def test_builds_bounded_plain_text_without_secrets(self):
        message = self.alert.build_message(
            status="failure",
            component="staging monitor",
            environment="staging",
            repository="Alexander-Fedorovtsev/Photolamp-SaaS",
            run_url=(
                "https://github.com/Alexander-Fedorovtsev/Photolamp-SaaS/"
                "actions/runs/123"
            ),
            sha="a" * 40,
            observed_at=datetime(2026, 10, 14, 12, 30, tzinfo=UTC),
        )
        self.assertIn("🔴 Photolamp: требуется внимание", message)
        self.assertIn("Ревизия: aaaaaaaaaaaa", message)
        self.assertIn("2026-10-14 12:30:00", message)
        self.assertNotIn("TELEGRAM", message)

    def test_posts_json_without_printing_api_response(self):
        destination = self.alert.TelegramDestination(
            "123456789:" + "A" * 35, "@photolamp_alerts", None
        )
        response = mock.MagicMock()
        response.read.return_value = b'{"ok":true,"result":{"message_id":1}}'
        response.__enter__.return_value = response

        with mock.patch.object(
            self.alert.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            self.alert.send_message(destination, "test")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["chat_id"], "@photolamp_alerts")
        self.assertEqual(payload["text"], "test")
        self.assertTrue(payload["disable_web_page_preview"])

    def test_hides_telegram_error_body(self):
        destination = self.alert.TelegramDestination(
            "123456789:" + "A" * 35, "@photolamp_alerts", None
        )
        error = urllib.error.HTTPError(
            "https://api.telegram.org/redacted",
            400,
            "Bad Request",
            {},
            None,
        )
        with (
            mock.patch.object(self.alert.urllib.request, "urlopen", side_effect=error),
            self.assertRaisesRegex(
                self.alert.AlertDeliveryError, "Telegram API request failed"
            ) as raised,
        ):
            self.alert.send_message(destination, "test")
        self.assertNotIn("Bad Request", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
