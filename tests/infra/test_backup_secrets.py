import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module(filename: str = "ensure-backup-secrets.py"):
    spec = importlib.util.spec_from_file_location(
        "backup_secrets", ROOT / "infra/scripts" / filename
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


class BackupSecrets(unittest.TestCase):
    def setUp(self):
        self.helper = module()
        self.temporary = tempfile.TemporaryDirectory()
        self.env = Path(self.temporary.name) / ".env"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, content: str) -> None:
        self.env.write_text(content)
        self.env.chmod(0o600)

    def test_provisions_once_and_preserves_other_values(self):
        self.write("POSTGRES_PASSWORD=owner-value\n")

        self.assertTrue(self.helper.ensure(self.env))
        content = self.env.read_text()
        self.assertIn("POSTGRES_PASSWORD=owner-value", content)
        self.assertIn("BACKUP_ACTIVE_KEY_ID=v1", content)
        serialized = content.split("BACKUP_ENCRYPTION_KEYS='", 1)[1].split("'", 1)[0]
        keyring = json.loads(serialized)
        self.assertRegex(keyring["v1"], r"^[a-f0-9]{64}$")
        self.assertFalse(self.helper.ensure(self.env))
        self.assertEqual(self.env.read_text(), content)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_accepts_rotated_keyring(self):
        keyring = json.dumps({"v1": "a" * 64, "v2": "b" * 64}, separators=(",", ":"))
        self.write(f"BACKUP_ACTIVE_KEY_ID=v2\nBACKUP_ENCRYPTION_KEYS='{keyring}'\n")
        self.assertFalse(self.helper.ensure(self.env))

    def test_replaces_documented_placeholders(self):
        self.write(
            "BACKUP_ACTIVE_KEY_ID=GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY\n"
            "BACKUP_ENCRYPTION_KEYS=GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY\n"
            "BACKUP_RETENTION_DAYS=30\n"
        )
        self.assertTrue(self.helper.ensure(self.env))
        content = self.env.read_text()
        self.assertNotIn("GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY", content)
        self.assertIn("BACKUP_RETENTION_DAYS=30", content)

    def test_rejects_incomplete_invalid_or_unsafe_configuration(self):
        for content in (
            "BACKUP_ACTIVE_KEY_ID=v1\n",
            "BACKUP_ACTIVE_KEY_ID=V1\nBACKUP_ENCRYPTION_KEYS='{}'\n",
            "BACKUP_ACTIVE_KEY_ID=v1\nBACKUP_ENCRYPTION_KEYS='not-json'\n",
            'BACKUP_ACTIVE_KEY_ID=v2\nBACKUP_ENCRYPTION_KEYS=\'{"v1":"'
            + "a" * 64
            + "\"}'\n",
        ):
            with self.subTest(content=content[:30]):
                self.write(content)
                with self.assertRaises(ValueError):
                    self.helper.ensure(self.env)

        self.write("")
        self.env.chmod(0o644)
        with self.assertRaises(ValueError):
            self.helper.ensure(self.env)

        unsafe = Path(self.temporary.name) / "unsafe"
        unsafe.symlink_to(self.env)
        with self.assertRaises(ValueError):
            self.helper.ensure(unsafe)

    def test_atomic_replacement_preserves_owner(self):
        self.write("")
        before = self.env.stat()
        self.helper.ensure(self.env)
        after = self.env.stat()
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o600)

    def test_rotation_adds_active_key_and_retains_old_key(self):
        self.write("")
        self.helper.ensure(self.env)
        before = json.loads(
            self.env.read_text()
            .split("BACKUP_ENCRYPTION_KEYS='", 1)[1]
            .split("'", 1)[0]
        )
        rotation = module("rotate-backup-key.py")

        rotation.rotate(self.env, "v2")

        content = self.env.read_text()
        self.assertIn("BACKUP_ACTIVE_KEY_ID=v2", content)
        after = json.loads(
            content.split("BACKUP_ENCRYPTION_KEYS='", 1)[1].split("'", 1)[0]
        )
        self.assertEqual(after["v1"], before["v1"])
        self.assertRegex(after["v2"], r"^[a-f0-9]{64}$")
        with self.assertRaises(ValueError):
            rotation.rotate(self.env, "v2")


if __name__ == "__main__":
    unittest.main()
