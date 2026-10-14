import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_KEY = "cGhvdG9sYW1wLWxvY2FsLXN5bnRoZXRpYy1rZXktdjE="


def module(filename: str = "ensure-contact-secrets.py"):
    spec = importlib.util.spec_from_file_location(
        "contact_secrets", ROOT / "infra/scripts" / filename
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def keyring(content: str) -> dict[str, str]:
    serialized = content.split("CONTACT_ENCRYPTION_KEYS='", 1)[1].split("'", 1)[0]
    return json.loads(serialized)


class ContactSecrets(unittest.TestCase):
    def setUp(self):
        self.helper = module()
        self.temporary = tempfile.TemporaryDirectory()
        self.env = Path(self.temporary.name) / ".env"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, content: str) -> None:
        self.env.write_text(content)
        self.env.chmod(0o600)

    def test_preserves_implicit_historical_staging_key(self):
        self.write("POSTGRES_PASSWORD=owner-value\n")

        self.assertTrue(self.helper.ensure(self.env))

        content = self.env.read_text()
        self.assertEqual(keyring(content), {"1": LOCAL_KEY})
        self.assertIn("CONTACT_ACTIVE_KEY_VERSION=1", content)
        self.assertFalse(self.helper.ensure(self.env))
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_migrates_a_legacy_key_without_changing_it(self):
        self.write(f"CONTACT_ENCRYPTION_KEY={LOCAL_KEY}\nOTHER=value\n")

        self.assertTrue(self.helper.ensure(self.env))

        content = self.env.read_text()
        self.assertNotIn("CONTACT_ENCRYPTION_KEY=", content)
        self.assertEqual(keyring(content), {"1": LOCAL_KEY})
        self.assertIn("OTHER=value", content)

    def test_placeholders_create_a_non_demo_key(self):
        self.write(
            "CONTACT_ACTIVE_KEY_VERSION=GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY\n"
            "CONTACT_ENCRYPTION_KEYS=GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY\n"
        )

        self.assertTrue(self.helper.ensure(self.env))

        generated = keyring(self.env.read_text())["1"]
        self.assertNotEqual(generated, LOCAL_KEY)
        self.assertEqual(len(self.helper._decode(generated)), 32)

    def test_rejects_incomplete_repeated_or_unsafe_keyrings(self):
        for content in (
            "CONTACT_ACTIVE_KEY_VERSION=1\n",
            "CONTACT_ACTIVE_KEY_VERSION=1\nCONTACT_ENCRYPTION_KEYS='{}'\n",
            f'CONTACT_ACTIVE_KEY_VERSION=2\nCONTACT_ENCRYPTION_KEYS=\'{{"1":"{LOCAL_KEY}"}}\'\n',
            f'CONTACT_ACTIVE_KEY_VERSION=1\nCONTACT_ENCRYPTION_KEYS=\'{{"1":"{LOCAL_KEY}","2":"{LOCAL_KEY}"}}\'\n',
        ):
            with self.subTest(content=content[:40]):
                self.write(content)
                with self.assertRaises(ValueError):
                    self.helper.ensure(self.env)

        self.write("")
        self.env.chmod(0o644)
        with self.assertRaises(ValueError):
            self.helper.ensure(self.env)

    def test_rotation_adds_a_higher_active_version_and_retains_old_key(self):
        self.write("")
        self.helper.ensure(self.env)
        rotation = module("rotate-contact-key.py")

        rotation.rotate(self.env, 2)

        content = self.env.read_text()
        rotated = keyring(content)
        self.assertEqual(rotated["1"], LOCAL_KEY)
        self.assertNotEqual(rotated["2"], LOCAL_KEY)
        self.assertIn("CONTACT_ACTIVE_KEY_VERSION=2", content)
        with self.assertRaises(ValueError):
            rotation.rotate(self.env, 2)

    def test_retirement_removes_only_an_inactive_key(self):
        self.write("")
        self.helper.ensure(self.env)
        module("rotate-contact-key.py").rotate(self.env, 2)
        retirement = module("retire-contact-key.py")

        retirement.retire(self.env, 1)

        self.assertEqual(set(keyring(self.env.read_text())), {"2"})
        with self.assertRaises(ValueError):
            retirement.retire(self.env, 2)


if __name__ == "__main__":
    unittest.main()
