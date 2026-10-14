"""src/harness/verifier.py: Resilient polyglot build and test verification."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple

log = logging.getLogger(__name__)


class VerificationResult(NamedTuple):
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    stack: str
    command: list[str]

    def format_diagnostic(self) -> str:
        if self.passed:
            return "VERIFICATION SUCCESS: All tests passed with exit code 0."

        combined_logs = []
        if self.stdout.strip():
            combined_logs.append(f"--- STDOUT ---\n{self.stdout.strip()[-2000:]}")
        if self.stderr.strip():
            combined_logs.append(f"--- STDERR ---\n{self.stderr.strip()[-2000:]}")

        logs_str = "\n".join(combined_logs) if combined_logs else "No diagnostic output captured."

        notes = ""
        if self.exit_code == 5 and "pytest" in " ".join(self.command):
            notes = (
                "\n\n[NOTICE - NO TESTS COLLECTED (Exit code 5)]:\n"
                "There are no test files in this repository. You must:\n"
                "1. Inspect the codebase using `list_directory` and `read_file`.\n"
                "2. Create a test file (e.g. `test_reproduction.py`) that imports actual repository modules and asserts their behavior.\n"
                "3. Make required code improvements or bug fixes in the repository's source files.\n"
                "(WARNING: Trivial tests like `assert True` without source changes will be rejected by the harness.)"
            )

        return (
            f"VERIFICATION FAILED: Command `{' '.join(self.command)}` exited with code {self.exit_code}.{notes}\n"
            f"Detected Stack: {self.stack}\n"
            f"Output:\n{logs_str}"
        )


class PolyglotVerifier:
    def __init__(
        self,
        repo_path: Path,
        timeout_seconds: int = 60,
        sandbox_python: str | None = None,
    ) -> None:
        self.repo_path = repo_path.resolve()
        self.timeout_seconds = timeout_seconds
        # Prefer the isolated sandbox venv's Python, falling back to host Python
        self.python_exe = sandbox_python or sys.executable

    def detect_stack(self) -> tuple[str, list[str]]:
        """Detect the project build/test stack from marker files.

        Uses confidence scoring so repositories with multiple marker files
        (e.g., Python project with a subfolder or config) are accurately matched.
        """
        markers: list[tuple[str, list[str], list[str], int]] = [
            ("Rust",            ["Cargo.toml"],                                  ["cargo", "test"],                                 3),
            ("Go",              ["go.mod"],                                       ["go", "test", "./..."],                           3),
            ("Java (Gradle)",   ["build.gradle", "build.gradle.kts"],            ["gradle", "test"],                                3),
            ("Java (Maven)",    ["pom.xml"],                                      ["mvn", "test"],                                   3),
            ("TypeScript/Node", ["package.json"],                                ["npm", "test"],                                   2),
            ("Python (pytest)", ["pytest.ini", "pyproject.toml", "setup.cfg"],  [self.python_exe, "-m", "pytest"],                 2),
            ("Python (standard)", ["requirements.txt", "Pipfile", "setup.py"],  [self.python_exe, "-m", "pytest"],                 1),
        ]

        scores: dict[str, tuple[int, list[str]]] = {}  # stack_name -> (score, command)
        for stack_name, marker_files, command, weight in markers:
            score = sum(weight for m in marker_files if (self.repo_path / m).exists())
            if score:
                existing_score = scores.get(stack_name, (0, command))[0]
                scores[stack_name] = (existing_score + score, command)

        if scores:
            best_stack = max(scores, key=lambda k: scores[k][0])
            log.info("Detected stack: %s (confidence score: %d)", best_stack, scores[best_stack][0])
            return best_stack, scores[best_stack][1]

        # Check for any Python source files
        if any(self.repo_path.glob("*.py")) or any(self.repo_path.glob("**/*.py")):
            return "Python (detected .py files)", [self.python_exe, "-m", "pytest"]

        return "Unknown", [self.python_exe, "-m", "pytest"]

    def verify(self, custom_command: list[str] | None = None, env: dict[str, str] | None = None) -> VerificationResult:
        stack_name, detected_command = self.detect_stack()
        command = custom_command or detected_command

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        log.info("Running local verification: %s", " ".join(command))
        try:
            process = subprocess.run(
                command,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=run_env,
                check=False,
            )
            log.info("Verification exit_code=%d", process.returncode)
            return VerificationResult(
                passed=(process.returncode == 0),
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                stack=stack_name,
                command=command,
            )
        except subprocess.TimeoutExpired as exc:
            log.warning("Verification timed out after %ds", self.timeout_seconds)
            return VerificationResult(
                passed=False,
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=f"Verification timed out after {self.timeout_seconds} seconds.",
                stack=stack_name,
                command=command,
            )
        except FileNotFoundError as exc:
            log.error("Verification command not found: %s", exc)
            return VerificationResult(
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=f"Command executable not found: {exc}",
                stack=stack_name,
                command=command,
            )