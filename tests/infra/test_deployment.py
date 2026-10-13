import copy
import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
REPOSITORY = "Alexander-Fedorovtsev/Photolamp-SaaS"
AUTH = "photolamp:$2y$05$" + "." * 53  # Synthetic fixture, not a working credential.


def module(filename: str):
    spec = importlib.util.spec_from_file_location(
        "deploy_helper", ROOT / "infra/scripts" / filename
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


class DeploymentGate(unittest.TestCase):
    def setUp(self):
        self.gate = module("check-ci-run.py")
        self.run = {
            "id": 1,
            "run_number": 1,
            "run_attempt": 1,
            "head_sha": SHA,
            "head_branch": "main",
            "event": "push",
            "conclusion": "success",
            "head_repository": {"full_name": REPOSITORY},
        }

    def test_exact_successful_main_push(self):
        self.assertTrue(
            self.gate.approved({"workflow_runs": [self.run]}, SHA, REPOSITORY)
        )

    def test_untrusted_or_failed_runs_cannot_deploy(self):
        for patch in [
            {"head_sha": "b" * 40},
            {"event": "pull_request"},
            {"head_branch": "feature/demo"},
            {"head_repository": {"full_name": "other/repository"}},
            {"conclusion": "failure"},
            {"conclusion": None},
        ]:
            with self.subTest(patch=patch):
                self.assertFalse(
                    self.gate.approved(
                        {"workflow_runs": [{**self.run, **patch}]}, SHA, REPOSITORY
                    )
                )
        self.assertFalse(self.gate.approved({"workflow_runs": []}, SHA, REPOSITORY))
        self.assertFalse(
            self.gate.approved({"workflow_runs": [self.run]}, "main", REPOSITORY)
        )

    def test_later_failure_overrides_earlier_success(self):
        failed = {**self.run, "id": 2, "run_number": 2, "conclusion": "failure"}
        self.assertFalse(
            self.gate.approved({"workflow_runs": [self.run, failed]}, SHA, REPOSITORY)
        )

    def test_failed_rerun_overrides_prior_attempt(self):
        failed = {**self.run, "run_attempt": 2, "conclusion": "failure"}
        self.assertFalse(
            self.gate.approved({"workflow_runs": [self.run, failed]}, SHA, REPOSITORY)
        )

    def test_remote_rejects_invalid_sha_before_host_operations(self):
        result = subprocess.run(
            ["bash", str(ROOT / "infra/scripts/remote-deploy.sh"), "main"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected one exact Git SHA", result.stderr)


class StagingTopology(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment = {
            **os.environ,
            "PHOTOLAMP_DEPLOY_SHA": SHA,
            "POSTGRES_PASSWORD": "a" * 64,
            "TRAEFIK_BASIC_AUTH_USERS": AUTH,
        }
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.yaml",
                "-f",
                "compose.staging.yaml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        cls.model = json.loads(result.stdout)
        cls.validator = module("check-staging-config.py")

    def test_valid_compose_and_intact_basic_auth(self):
        self.validator.validate(self.model)
        serialized = self.model["services"]["web"]["labels"][
            "traefik.http.middlewares.photolamp-test-auth.basicauth.users"
        ]
        self.assertEqual(serialized.replace("$$", "$"), AUTH)

    def test_rejects_host_ports_and_shared_database_network(self):
        for service in ["api", "web", "postgres", "redis"]:
            changed = copy.deepcopy(self.model)
            changed["services"][service]["ports"] = [{"published": "8000"}]
            with self.subTest(service=service), self.assertRaises(ValueError):
                self.validator.validate(changed)
        changed = copy.deepcopy(self.model)
        changed["services"]["postgres"]["networks"]["traefik"] = None
        with self.assertRaises(ValueError):
            self.validator.validate(changed)

    def test_rejects_api_names_that_can_collide_on_shared_edge(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["web"]["environment"]["API_INTERNAL_URL"] = (
            "http://api:8000"
        )
        with self.assertRaisesRegex(ValueError, "Photolamp-specific API alias"):
            self.validator.validate(changed)
        changed = copy.deepcopy(self.model)
        changed["services"]["api"]["networks"]["internal"]["aliases"] = []
        with self.assertRaisesRegex(ValueError, "Photolamp-specific API alias"):
            self.validator.validate(changed)

    def test_rejects_default_credentials_or_unprotected_routes(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] = (
            "local-development-only"
        )
        with self.assertRaises(ValueError):
            self.validator.validate(changed)
        changed = copy.deepcopy(self.model)
        changed["services"]["web"]["labels"][
            "traefik.http.routers.photolamp-test.middlewares"
        ] = ""
        with self.assertRaises(ValueError):
            self.validator.validate(changed)

    def test_validator_cli_does_not_disclose_bad_secret(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] = (
            "do-not-disclose-this-value"
        )
        result = subprocess.run(
            ["python3", str(ROOT / "infra/scripts/check-staging-config.py")],
            input=json.dumps(changed),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("do-not-disclose-this-value", result.stdout + result.stderr)
        self.assertIn("64-character random hex database password", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_validator_explains_corrupted_auth_without_disclosing_it(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["web"]["labels"][
            "traefik.http.middlewares.photolamp-test-auth.basicauth.users"
        ] = "private-corrupted-auth-value"
        result = subprocess.run(
            ["python3", str(ROOT / "infra/scripts/check-staging-config.py")],
            input=json.dumps(changed),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Basic Auth must contain one intact bcrypt hash", result.stderr)
        self.assertNotIn("private-corrupted-auth-value", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
