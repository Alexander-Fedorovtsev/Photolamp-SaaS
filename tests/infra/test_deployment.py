import copy
import importlib.util
import json
import os
import subprocess
import tempfile
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

        backup = subprocess.run(
            ["bash", str(ROOT / "infra/scripts/remote-backup.sh"), "main"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(backup.returncode, 0)
        self.assertIn("Expected one workflow Git SHA", backup.stderr)

    def test_retry_helper_recovers_and_is_bounded(self):
        remote = (ROOT / "infra/scripts/remote-deploy.sh").read_text()
        start = remote.index("retry_command() {")
        end = remote.index("\n}", start) + 2
        helper = remote[start:end]
        recover = subprocess.run(
            [
                "bash",
                "-c",
                helper + "; count=0; flaky() { ((count += 1)); ((count >= 3)); }; "
                'retry_command 4 0 "synthetic fetch" flaky; printf "%s" "$count"',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(recover.returncode, 0, recover.stderr)
        self.assertEqual(recover.stdout, "3")
        self.assertIn("attempt 2/4", recover.stderr)

        exhausted = subprocess.run(
            [
                "bash",
                "-c",
                helper + '; retry_command 2 0 "synthetic fetch" false',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(exhausted.returncode, 0)
        self.assertIn("failed after 2 attempts", exhausted.stderr)

    def test_ci_gate_retries_eventual_actions_visibility_and_is_bounded(self):
        verifier = ROOT / "infra/scripts/verify-ci-run.sh"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            fake_bin = fixture / "bin"
            fake_bin.mkdir()
            state = fixture / "calls"
            payload = fixture / "success.json"
            payload.write_text(json.dumps({"workflow_runs": [self.run]}))
            (fake_bin / "curl").write_text(
                "#!/usr/bin/env bash\n"
                "count=0\n"
                '[[ ! -f "$FAKE_STATE" ]] || count=$(cat "$FAKE_STATE")\n'
                "((count += 1))\n"
                'printf "%s" "$count" > "$FAKE_STATE"\n'
                'if ((count >= FAKE_SUCCESS_AFTER)); then cat "$FAKE_PAYLOAD"; '
                "else printf '{\"workflow_runs\":[]}'; fi\n"
            )
            (fake_bin / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n")
            (fake_bin / "curl").chmod(0o755)
            (fake_bin / "sleep").chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "GH_TOKEN": "synthetic-test-token",
                "FAKE_STATE": str(state),
                "FAKE_PAYLOAD": str(payload),
                "FAKE_SUCCESS_AFTER": "3",
            }
            recovered = subprocess.run(
                ["bash", str(verifier), SHA, REPOSITORY, str(fixture / "runs.json")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(state.read_text(), "3")
            self.assertIn("retrying (2/6)", recovered.stderr)

            state.unlink()
            environment["FAKE_SUCCESS_AFTER"] = "99"
            exhausted = subprocess.run(
                ["bash", str(verifier), SHA, REPOSITORY, str(fixture / "runs.json")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(exhausted.returncode, 0)
            self.assertEqual(state.read_text(), "6")
            self.assertIn("after 6 attempts", exhausted.stderr)

    def test_ssh_deploy_retries_only_transport_failures(self):
        runner = ROOT / "infra/scripts/run-remote-deploy.sh"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            fake_bin = fixture / "bin"
            fake_bin.mkdir()
            for name in ("key", "known-hosts", "remote.sh"):
                (fixture / name).write_text("synthetic\n")
            state = fixture / "calls"
            (fake_bin / "ssh").write_text(
                "#!/usr/bin/env bash\n"
                "cat >/dev/null\n"
                "count=0\n"
                '[[ ! -f "$FAKE_STATE" ]] || count=$(cat "$FAKE_STATE")\n'
                "((count += 1))\n"
                'printf "%s" "$count" > "$FAKE_STATE"\n'
                'if ((count >= FAKE_SUCCESS_AFTER)); then exit "$FAKE_FINAL_STATUS"; fi\n'
                "exit 255\n"
            )
            (fake_bin / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n")
            (fake_bin / "ssh").chmod(0o755)
            (fake_bin / "sleep").chmod(0o755)
            command = [
                "bash",
                str(runner),
                SHA,
                str(fixture / "key"),
                str(fixture / "known-hosts"),
                "staging.example.test",
                "photolamp-deploy",
                "38222",
                str(fixture / "remote.sh"),
            ]
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_STATE": str(state),
                "FAKE_SUCCESS_AFTER": "3",
                "FAKE_FINAL_STATUS": "0",
            }
            recovered = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(state.read_text(), "3")
            self.assertIn("retrying (2/4)", recovered.stderr)

            state.unlink()
            environment["FAKE_SUCCESS_AFTER"] = "99"
            exhausted = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(exhausted.returncode, 255)
            self.assertEqual(state.read_text(), "4")
            self.assertIn("failed after 4 attempts", exhausted.stderr)

            state.unlink()
            environment["FAKE_SUCCESS_AFTER"] = "1"
            environment["FAKE_FINAL_STATUS"] = "23"
            remote_failure = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(remote_failure.returncode, 23)
            self.assertEqual(state.read_text(), "1")

    def test_ssh_spools_remote_script_before_commands_can_consume_stdin(self):
        runner = ROOT / "infra/scripts/run-remote-deploy.sh"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            fake_bin = fixture / "bin"
            fake_bin.mkdir()
            for name in ("key", "known-hosts"):
                (fixture / name).write_text("synthetic\n")
            marker = fixture / "tail-ran"
            remote_script = fixture / "remote.sh"
            remote_script.write_text(
                "#!/usr/bin/env bash\n"
                "cat >/dev/null\n" + ":\n" * 20_000 + f"printf complete > {marker}\n"
            )
            (fake_bin / "ssh").write_text(
                "#!/usr/bin/env bash\n"
                "found_host=false\n"
                'for argument in "$@"; do\n'
                '  if [[ "$found_host" == true ]]; then exec bash -c "$argument"; fi\n'
                '  [[ "$argument" == *@* ]] && found_host=true\n'
                "done\n"
                "exit 2\n"
            )
            (fake_bin / "ssh").chmod(0o755)
            result = subprocess.run(
                [
                    "bash",
                    str(runner),
                    SHA,
                    str(fixture / "key"),
                    str(fixture / "known-hosts"),
                    "staging.example.test",
                    "photolamp-deploy",
                    "38222",
                    str(remote_script),
                ],
                cwd=ROOT,
                env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(), "complete")


class StagingTopology(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment = {
            **os.environ,
            "PHOTOLAMP_DEPLOY_SHA": SHA,
            "POSTGRES_PASSWORD": "a" * 64,
            "POSTGRES_RUNTIME_PASSWORD": "b" * 64,
            "BACKUP_ACTIVE_KEY_ID": "v1",
            "BACKUP_ENCRYPTION_KEYS": json.dumps({"v1": "c" * 64}),
            "CONTACT_ACTIVE_KEY_VERSION": "1",
            "CONTACT_ENCRYPTION_KEYS": json.dumps(
                {"1": "cGhvdG9sYW1wLWxvY2FsLXN5bnRoZXRpYy1rZXktdjE="}
            ),
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
                "--profile",
                "maintenance",
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
        for service in [
            "api",
            "web",
            "postgres",
            "redis",
            "db-bootstrap",
            "migrate",
            "backup-crypto",
            "contact-maintenance",
        ]:
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
        with self.assertRaisesRegex(ValueError, "staging security"):
            self.validator.validate(changed)
        changed = copy.deepcopy(self.model)
        changed["services"]["api"]["networks"]["internal"]["aliases"] = []
        with self.assertRaisesRegex(ValueError, "staging security"):
            self.validator.validate(changed)

        changed = copy.deepcopy(self.model)
        changed["services"]["web"]["environment"]["APP_ENV"] = "development"
        with self.assertRaisesRegex(ValueError, "staging security"):
            self.validator.validate(changed)

    def test_backup_keys_are_valid_and_isolated_from_app_services(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["backup-crypto"]["network_mode"] = "default"
        with self.assertRaisesRegex(ValueError, "must not have network access"):
            self.validator.validate(changed)

        changed = copy.deepcopy(self.model)
        changed["services"]["backup-crypto"]["environment"]["BACKUP_ACTIVE_KEY_ID"] = (
            "missing"
        )
        with self.assertRaisesRegex(ValueError, "versioned backup keyring"):
            self.validator.validate(changed)

        changed = copy.deepcopy(self.model)
        changed["services"]["api"]["environment"]["BACKUP_ENCRYPTION_KEYS"] = (
            "must-not-reach-api"
        )
        with self.assertRaisesRegex(ValueError, "isolated"):
            self.validator.validate(changed)

    def test_contact_keys_are_versioned_and_maintenance_isolated(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["api"]["environment"]["CONTACT_ACTIVE_KEY_VERSION"] = "2"
        with self.assertRaisesRegex(ValueError, "versioned contact keyring"):
            self.validator.validate(changed)

        changed = copy.deepcopy(self.model)
        changed["services"]["contact-maintenance"]["environment"][
            "CONTACT_ENCRYPTION_KEYS"
        ] = '{"1":"different-but-never-logged-value"}'
        with self.assertRaisesRegex(ValueError, "must match"):
            self.validator.validate(changed)

        changed = copy.deepcopy(self.model)
        changed["services"]["api"]["environment"]["DATABASE_OWNER_URL"] = (
            "must-not-reach-api"
        )
        with self.assertRaisesRegex(ValueError, "must not receive database owner"):
            self.validator.validate(changed)

    def test_rejects_default_credentials_or_unprotected_routes(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] = (
            "local-development-only"
        )
        with self.assertRaises(ValueError):
            self.validator.validate(changed)

    def test_runtime_role_is_separate_and_owner_url_is_not_exposed_to_api(self):
        changed = copy.deepcopy(self.model)
        changed["services"]["api"]["environment"]["DATABASE_URL"] = changed["services"][
            "migrate"
        ]["environment"]["DATABASE_OWNER_URL"]
        with self.assertRaisesRegex(ValueError, "separate 64-character runtime"):
            self.validator.validate(changed)
        changed = copy.deepcopy(self.model)
        changed["services"]["api"]["environment"]["DATABASE_OWNER_URL"] = (
            "must-not-reach-api"
        )
        with self.assertRaisesRegex(ValueError, "must not receive database owner"):
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
