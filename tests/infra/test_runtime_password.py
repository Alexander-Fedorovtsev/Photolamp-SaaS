import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "ensure_runtime_password", ROOT / "infra/scripts/ensure-runtime-password.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


class RuntimePassword(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / ".env"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, content: str, mode: int = 0o600) -> None:
        self.path.write_text(content)
        self.path.chmod(mode)

    def test_provisions_once_without_disclosing_or_changing_other_values(self):
        self.write("POSTGRES_PASSWORD=owner-value\n")
        self.assertTrue(self.helper.ensure(self.path))
        content = self.path.read_text()
        self.assertIn("POSTGRES_PASSWORD=owner-value", content)
        runtime = content.split("POSTGRES_RUNTIME_PASSWORD=", 1)[1].strip()
        self.assertRegex(runtime, r"^[a-f0-9]{64}$")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertFalse(self.helper.ensure(self.path))
        self.assertEqual(self.path.read_text(), content)

    def test_replaces_documented_placeholder(self):
        self.write(
            "POSTGRES_RUNTIME_PASSWORD=GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY\n"
        )
        self.assertTrue(self.helper.ensure(self.path))
        self.assertNotIn("GENERATED_", self.path.read_text())

    def test_rejects_unsafe_mode_or_invalid_existing_secret(self):
        self.write("POSTGRES_RUNTIME_PASSWORD=short\n")
        with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
            self.helper.ensure(self.path)
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.helper.ensure(self.path)


if __name__ == "__main__":
    unittest.main()
