"""src/harness/runner.py: Streamlined, deterministic Harness Engineering Pipeline.

Implements governed test-driven remediation (Fail-to-Pass & Pass-to-Pass),
code implementation, and test suite expansion with zero tool-calling overhead.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import subprocess
import time
from typing import Any

import ollama

from src.harness.intent import RunnerMode
from src.harness.interceptors import SecurityInterceptor
from src.harness.verifier import PolyglotVerifier, VerificationResult
from src.tools.git_tools import SandboxedGitTools

log = logging.getLogger(__name__)

# Files/directories to ignore during automatic workspace scanning
_IGNORE_PATTERNS = {
    ".git", ".venv", "venv", ".sandbox_venv", "__pycache__", ".pytest_cache",
    ".idea", ".vscode", "node_modules", "target", "build", "dist"
}

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".cpp"}


def _collect_workspace_source_files(
    repo_path: Path,
    max_files: int = 8,
    max_bytes_per_file: int = 12000,
) -> dict[str, str]:
    """Inspect workspace and collect primary source and test files."""
    files: dict[str, str] = {}
    repo_path = repo_path.resolve()

    # Prioritize root files, then 1 level deep
    candidates: list[Path] = []
    for item in sorted(repo_path.rglob("*")):
        if item.is_file() and not any(part in _IGNORE_PATTERNS for part in item.parts):
            if item.suffix.lower() in _CODE_EXTENSIONS:
                candidates.append(item)

    # Sort so root-level source files come first
    candidates.sort(key=lambda p: (len(p.relative_to(repo_path).parts), p.name))

    for p in candidates[:max_files]:
        rel = str(p.relative_to(repo_path)).replace("\\", "/")
        try:
            if p.stat().st_size <= max_bytes_per_file:
                content = p.read_text(encoding="utf-8", errors="ignore")
                files[rel] = content
        except Exception as exc:
            log.warning("Could not read workspace file %s: %s", rel, exc)

    return files


def _extract_code_files(text: str, default_filename: str | None = None) -> list[tuple[str, str]]:
    """Extract file paths and content from model output using multiple resilient patterns."""
    if not text or not text.strip():
        return []

    results: list[tuple[str, str]] = []

    # Pattern 1: FILE: path/to/file.py followed by ```python ... ```
    p1 = re.findall(
        r"(?:(?:FILE|File|Path|filename):\s*([^\r\n`#]+)|###\s*([^\r\n`]+))\s*\r?\n\s*```[a-zA-Z0-9_\-\+]*\r?\n([\s\S]*?)```",
        text,
    )
    for m in p1:
        path = (m[0] or m[1]).strip().strip("'\"`")
        code = m[2]
        if path and code:
            results.append((path, code))

    if results:
        return results

    # Pattern 2: Code fence with path inline: ```python:path/to/file.py or ```python filename=path
    p2 = re.findall(
        r"```[a-zA-Z0-9_\-\+]+(?::|\s+filename=|\s+path=)\s*([^\r\n`]+)\r?\n([\s\S]*?)```",
        text,
    )
    for m in p2:
        path = m[0].strip().strip("'\"`")
        code = m[1]
        if path and code:
            results.append((path, code))

    if results:
        return results

    # Pattern 3: Structured JSON [{"path": "...", "content": "..."}] or {"path": ..., "content": ...}
    json_candidates = re.findall(r"```(?:json)?\s*([\[\{][\s\S]*?[\]\}])\s*```", text)
    if not json_candidates and text.strip().startswith(("{", "[")):
        json_candidates = [text.strip()]

    for jc in json_candidates:
        try:
            parsed = json.loads(jc, strict=False)
            if isinstance(parsed, dict) and "path" in parsed and "content" in parsed:
                results.append((str(parsed["path"]).strip(), str(parsed["content"])))
            elif isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "path" in item and "content" in item:
                        results.append((str(item["path"]).strip(), str(item["content"])))
        except Exception:
            pass

    if results:
        return results

    # Pattern 4: Fallback for single code block when default_filename is known
    fences = re.findall(r"```(?:[a-zA-Z0-9_\-\+]+)?\r?\n([\s\S]*?)```", text)
    if fences and default_filename:
        # Use the largest code block
        largest = max(fences, key=len)
        if largest.strip():
            return [(default_filename, largest)]

    # Pattern 5: Pure code without fences if it starts with valid statements and default_filename provided
    if default_filename and ("def " in text or "import " in text or "class " in text):
        clean = text.strip()
        if not clean.startswith("```"):
            return [(default_filename, clean)]

    return results


class HarnessRunner:
    """Streamlined autonomous harness governor supporting FIX, WRITE_CODE, and WRITE_TESTS."""

    _MODEL_RETRY_ATTEMPTS = 3
    _MODEL_RETRY_BASE_DELAY = 2.0

    def __init__(
        self,
        repo_path: Path,
        mode: RunnerMode = RunnerMode.FIX,
        interactive: bool = False,
        sandbox_python: str | None = None,
        model_name: str = "qwen2.5-coder:7b",
        max_attempts: int = 3,
        max_iterations: int = 12,
        max_messages: int = 28,
        test_command: list[str] | None = None,
    ) -> None:
        self.repo_path = repo_path.resolve()
        self.mode = mode
        self.interactive = interactive
        self.model_name = model_name
        self.max_attempts = max_attempts
        self.custom_test_command = test_command

        self.interceptor = SecurityInterceptor(self.repo_path)
        self.verifier = PolyglotVerifier(self.repo_path, sandbox_python=sandbox_python)
        self.git_tools = SandboxedGitTools(
            self.repo_path,
            self.interceptor,
            self.verifier,
            custom_test_command=self.custom_test_command,
        )

    def _call_model(self, prompt: str) -> str:
        """Call the local model with retry on transient failures."""
        last_exc: Exception | None = None
        for attempt in range(1, self._MODEL_RETRY_ATTEMPTS + 1):
            try:
                res = ollama.chat(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.2, "num_ctx": 4096},
                )
                msg = res.get("message", {})
                return msg.get("content", "").strip()
            except Exception as exc:
                last_exc = exc
                if attempt < self._MODEL_RETRY_ATTEMPTS:
                    delay = self._MODEL_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log.warning("Model invocation failed (attempt %d/%d): %s — retrying in %.1fs", attempt, self._MODEL_RETRY_ATTEMPTS, exc, delay)
                    time.sleep(delay)
        raise RuntimeError(f"Model invocation permanently failed after {self._MODEL_RETRY_ATTEMPTS} attempts: {last_exc}") from last_exc

    def _format_source_files(self, files: dict[str, str]) -> str:
        """Format captured files for model context."""
        if not files:
            return "(No existing code files detected in repository)"
        parts = []
        for path, content in files.items():
            parts.append(f"--- File: {path} ---\n{content}\n")
        return "\n".join(parts)

    def _is_valid_remediation_diff(self, diff_text: str) -> tuple[bool, str]:
        """Validate that changes are substantive and match the active mode contract."""
        if not diff_text or not diff_text.strip():
            return False, "No code changes found in diff."

        modified_files = re.findall(r"diff --git a/(.*?) b/", diff_text)
        if not modified_files:
            return False, "No modified files detected in diff."

        has_trivial_assert = "assert True" in diff_text or "assert 1 == 1" in diff_text
        has_imports = bool(re.search(r"^\+\s*(?:from|import)\s+([a-zA-Z0-9_]+)", diff_text, re.MULTILINE))

        if self.mode == RunnerMode.WRITE_TESTS:
            has_tests = any("test" in f for f in modified_files)
            if not has_tests:
                return False, "No test files were created or modified."
            if has_trivial_assert:
                return False, "Test suite only added trivial assertions ('assert True')."
            if not has_imports:
                return False, "New test files do not import any application modules."
            return True, "Substantive test suite created."

        if self.mode == RunnerMode.WRITE_CODE:
            has_tests = any("test" in f for f in modified_files)
            has_source = any("test" not in f for f in modified_files)
            if not has_source:
                return False, "No application source files were created."
            if not has_tests:
                return False, "No accompanying test files were created for the new code."
            if has_trivial_assert:
                return False, "Test suite contains trivial assertions ('assert True')."
            return True, "Substantive implementation and test suite created."

        # FIX mode
        only_tests = all(
            Path(f).name.startswith("test_") or Path(f).name.endswith("_test.py") or "test" in Path(f).parts
            for f in modified_files
        )
        if only_tests:
            if has_trivial_assert:
                return False, "Diff only created a trivial test without source fixes."
            if not has_imports:
                return False, "Test file created does not import or test repository modules."

        return True, "Valid substantive changes detected."

    def _interactive_gate(self) -> bool:
        """Present current workspace diff for user confirmation before test execution."""
        if not self.interactive:
            return True

        diff_res = self.git_tools.git_diff()
        current_diff = diff_res.get("diff", "").strip()
        print("\n  =======================================================")
        print("  [INTERACTIVE VERIFICATION GATE]")
        if current_diff:
            print("  Current Workspace Changes:")
            preview = current_diff.splitlines()[:20]
            print("\n".join("    " + l for l in preview))
            if len(current_diff.splitlines()) > 20:
                print("    ... (truncated)")
        else:
            print("  No changes currently detected in workspace.")
        print("  =======================================================")
        confirm = input("  [?] Run tests now? [Y/n]: ").strip().lower()
        if confirm in ("n", "no"):
            print("  [!] Verification skipped by user decision.")
            return False
        return True

    def run(self, bug_objective: str) -> tuple[bool, str]:
        """Dispatch objective to the appropriate streamlined pipeline."""
        print(f"[*] Initializing AutoFixer Harness on: {self.repo_path}")
        print(f"[*] Active Mode: {self.mode.value.upper()} (Interactive Approval: {self.interactive})")

        initial_check = self.verifier.verify(custom_command=self.custom_test_command)
        print(f"[*] Detected Stack: {initial_check.stack} (Baseline Passed: {initial_check.passed})")

        if self.mode == RunnerMode.WRITE_CODE:
            return self._run_write_code_pipeline(bug_objective)
        elif self.mode == RunnerMode.WRITE_TESTS:
            return self._run_write_tests_pipeline(bug_objective)
        else:
            return self._run_fix_pipeline(bug_objective, initial_check)

    # -------------------------------------------------------------------------
    # PIPELINE 1: BUG FIX & REMEDIATION (FAIL_TO_PASS & PASS_TO_PASS)
    # -------------------------------------------------------------------------
    def _run_fix_pipeline(self, objective: str, initial_check: VerificationResult) -> tuple[bool, str]:
        sources = _collect_workspace_source_files(self.repo_path)
        primary_file = list(sources.keys())[0] if sources else None

        # Check if pre-existing tests reproduce the failure
        has_existing_failing_test = (
            not initial_check.passed
            and initial_check.exit_code not in (-1, 5)
            and "collected 0 items" not in initial_check.stdout
        )

        failing_diagnostic = initial_check.format_diagnostic()

        # Rule A (FAIL_TO_PASS): If no failing test exists, generate one first!
        if not has_existing_failing_test:
            print("\n[*] Rule A (FAIL_TO_PASS): No failing baseline test detected.")
            print("[*] Generating reproduction test suite...")
            repro_name = f"test_{Path(primary_file).stem}_reproduction.py" if primary_file else "test_reproduction.py"

            repro_prompt = (
                f"You are an Autonomous Test Engineer governed by a verification harness.\n"
                f"Objective: {objective}\n\n"
                f"Workspace Source Code:\n{self._format_source_files(sources)}\n\n"
                f"Task: Write a pytest test file `{repro_name}` that exposes the bug or exercises the missing feature.\n"
                f"The test MUST import the repository module and test its behavior.\n\n"
                f"Output format:\n"
                f"FILE: {repro_name}\n"
                f"```python\n"
                f"<test code>\n"
                f"```\n"
            )

            repro_resp = self._call_model(repro_prompt)
            repro_files = _extract_code_files(repro_resp, default_filename=repro_name)

            for p, c in repro_files:
                self.git_tools.write_file(p, c)
                print(f"  [+] Created reproduction test: {p}")

            # Commit reproduction test so it becomes the baseline test contract and survives backtracking
            subprocess.run(["git", "add", "."], cwd=str(self.repo_path), capture_output=True, check=False)
            subprocess.run(
                [
                    "git",
                    "-c", "user.name=AutoFixer Bot",
                    "-c", "user.email=autofixer@localhost",
                    "commit",
                    "-m", "Baseline: Add reproduction test suite (FAIL_TO_PASS)",
                ],
                cwd=str(self.repo_path),
                capture_output=True,
                check=False,
            )

            # Run test to establish failing baseline
            check = self.verifier.verify(custom_command=self.custom_test_command)
            failing_diagnostic = check.format_diagnostic()
            print(f"  [*] Baseline established: tests executed (exit_code={check.exit_code}).")

        # Macro Remediation Loop (up to max_attempts)
        for attempt in range(1, self.max_attempts + 1):
            print(f"\n=======================================================")
            print(f"[>] Macro Attempt {attempt}/{self.max_attempts} (Mode: FIX)")
            print(f"=======================================================")

            # Refresh sources including the reproduction test
            current_sources = _collect_workspace_source_files(self.repo_path)

            diagnostic_header = (
                f"PREVIOUS ATTEMPT ({attempt - 1}) FAILED TEST DIAGNOSTICS:\n{failing_diagnostic}\n\n"
                f"CRITICAL FIX INSTRUCTION: Look carefully at the failing test function names and failure trace above. "
                f"You MUST define all required functions and handle the edge cases tested by the test suite.\n"
                if attempt > 1
                else f"Test Diagnostics / Failure Trace:\n{failing_diagnostic}\n"
            )

            fix_prompt = (
                f"You are an Autonomous Software Remediation Engineer governed by a verification harness.\n"
                f"Objective: {objective}\n\n"
                f"Current Workspace Files:\n{self._format_source_files(current_sources)}\n\n"
                f"{diagnostic_header}\n"
                f"Task: Provide the fixed, complete code for the file(s) so that all tests pass with exit code 0.\n"
                f"Output each modified file with a FILE: marker:\n"
                f"FILE: <relative_path>\n"
                f"```python\n"
                f"<complete fixed file content>\n"
                f"```\n"
            )

            print("  [*] Requesting surgical fix from model...")
            model_resp = self._call_model(fix_prompt)
            extracted = _extract_code_files(model_resp, default_filename=primary_file)

            if not extracted:
                print("  [!] Could not parse file changes from model response. Retrying...")
                continue

            for p, content in extracted:
                self.git_tools.write_file(p, content)
                print(f"  [+] Applied changes to: {p}")

            if not self._interactive_gate():
                continue

            print("  [*] Polyglot Verification Harness: Running tests...")
            verify_res = self.verifier.verify(custom_command=self.custom_test_command)

            if verify_res.passed:
                diff_res = self.git_tools.git_diff()
                diff_text = diff_res.get("diff", "")
                is_valid, reason = self._is_valid_remediation_diff(diff_text)
                if is_valid:
                    print("\n[OK] GOAL ACHIEVED: All tests pass cleanly with exit code 0!")
                    return True, f"Remediation Succeeded on Attempt {attempt}.\n\nGenerated Patch:\n{diff_text}"
                else:
                    print(f"  [!] Tests passed exit code 0, but harness rejected diff: {reason}")
                    failing_diagnostic = f"DIFF REJECTED: {reason}"
            else:
                print(f"  [X] Attempt {attempt} failed verification (exit_code={verify_res.exit_code}).")
                failing_diagnostic = verify_res.format_diagnostic()
                print(f"  [!] Error Diagnostic:\n{(verify_res.stdout + verify_res.stderr).strip()[-1500:]}\n")

            if attempt < self.max_attempts:
                print("  [i] Backtracking to clean baseline before next attempt...")
                self.git_tools.reset_to_baseline()

        return False, f"Remediation bounded: Failed after {self.max_attempts} attempts. Exit code 0 not satisfied."

    # -------------------------------------------------------------------------
    # PIPELINE 2: WRITE CODE & ACCOMPANYING TESTS
    # -------------------------------------------------------------------------
    def _run_write_code_pipeline(self, objective: str) -> tuple[bool, str]:
        sources = _collect_workspace_source_files(self.repo_path)
        failing_diagnostic = "Initial implementation required."

        for attempt in range(1, self.max_attempts + 1):
            print(f"\n=======================================================")
            print(f"[>] Macro Attempt {attempt}/{self.max_attempts} (Mode: WRITE-CODE)")
            print(f"=======================================================")

            prompt = (
                f"You are an Autonomous Software Engineering Agent governed by a verification harness.\n"
                f"Objective: {objective}\n\n"
                f"Existing Workspace Files:\n{self._format_source_files(sources)}\n\n"
                f"Diagnostics from previous run:\n{failing_diagnostic}\n\n"
                f"Task: Implement the requested feature AND write accompanying pytest tests that exercise it.\n"
                f"Provide BOTH the implementation file and test file.\n"
                f"Output format:\n"
                f"FILE: <implementation_file.py>\n"
                f"```python\n"
                f"<implementation code>\n"
                f"```\n\n"
                f"FILE: test_<feature>.py\n"
                f"```python\n"
                f"<test code>\n"
                f"```\n"
            )

            print("  [*] Requesting implementation and tests from model...")
            resp = self._call_model(prompt)
            extracted = _extract_code_files(resp)

            if not extracted:
                print("  [!] Could not parse files from model output. Retrying...")
                continue

            for p, content in extracted:
                self.git_tools.write_file(p, content)
                print(f"  [+] Wrote: {p}")

            if not self._interactive_gate():
                continue

            print("  [*] Polyglot Verification Harness: Running tests...")
            verify_res = self.verifier.verify(custom_command=self.custom_test_command)

            if verify_res.passed:
                diff_res = self.git_tools.git_diff()
                diff_text = diff_res.get("diff", "")
                is_valid, reason = self._is_valid_remediation_diff(diff_text)
                if is_valid:
                    print("\n[OK] GOAL ACHIEVED: Code and tests pass cleanly with exit code 0!")
                    return True, f"Code Implementation Succeeded on Attempt {attempt}.\n\nGenerated Patch:\n{diff_text}"
                else:
                    print(f"  [!] Tests passed, but diff rejected: {reason}")
                    failing_diagnostic = f"DIFF REJECTED: {reason}"
            else:
                print(f"  [X] Attempt {attempt} failed verification (exit_code={verify_res.exit_code}).")
                failing_diagnostic = verify_res.format_diagnostic()
                print(f"  [!] Diagnostic:\n{(verify_res.stdout + verify_res.stderr).strip()[-1500:]}\n")

            if attempt < self.max_attempts:
                print("  [i] Backtracking to clean baseline before next attempt...")
                self.git_tools.reset_to_baseline()

        return False, f"Code implementation bounded: Failed after {self.max_attempts} attempts."

    # -------------------------------------------------------------------------
    # PIPELINE 3: WRITE TESTS FOR EXISTING CODE
    # -------------------------------------------------------------------------
    def _run_write_tests_pipeline(self, objective: str) -> tuple[bool, str]:
        sources = _collect_workspace_source_files(self.repo_path)
        primary_file = list(sources.keys())[0] if sources else "app.py"
        test_filename = f"test_{Path(primary_file).stem}.py"
        failing_diagnostic = "Initial test generation."

        for attempt in range(1, self.max_attempts + 1):
            print(f"\n=======================================================")
            print(f"[>] Macro Attempt {attempt}/{self.max_attempts} (Mode: WRITE-TESTS)")
            print(f"=======================================================")

            prompt = (
                f"You are an Autonomous Test Engineering Agent governed by a verification harness.\n"
                f"Objective: {objective}\n\n"
                f"Existing Application Code:\n{self._format_source_files(sources)}\n\n"
                f"Diagnostics from previous run:\n{failing_diagnostic}\n\n"
                f"Task: Write a comprehensive pytest test suite `{test_filename}` for the existing code.\n"
                f"DO NOT modify the existing source code. Exercise edge cases, expected outcomes, and error handling.\n"
                f"Output format:\n"
                f"FILE: {test_filename}\n"
                f"```python\n"
                f"<test code>\n"
                f"```\n"
            )

            print("  [*] Requesting test suite from model...")
            resp = self._call_model(prompt)
            extracted = _extract_code_files(resp, default_filename=test_filename)

            if not extracted:
                print("  [!] Could not parse test file from model output. Retrying...")
                continue

            for p, content in extracted:
                self.git_tools.write_file(p, content)
                print(f"  [+] Wrote test file: {p}")

            if not self._interactive_gate():
                continue

            print("  [*] Polyglot Verification Harness: Running tests...")
            verify_res = self.verifier.verify(custom_command=self.custom_test_command)

            if verify_res.passed:
                diff_res = self.git_tools.git_diff()
                diff_text = diff_res.get("diff", "")
                is_valid, reason = self._is_valid_remediation_diff(diff_text)
                if is_valid:
                    print("\n[OK] GOAL ACHIEVED: Test suite passes cleanly with exit code 0!")
                    return True, f"Test Suite Creation Succeeded on Attempt {attempt}.\n\nGenerated Patch:\n{diff_text}"
                else:
                    print(f"  [!] Tests passed, but diff rejected: {reason}")
                    failing_diagnostic = f"DIFF REJECTED: {reason}"
            else:
                print(f"  [X] Attempt {attempt} failed verification (exit_code={verify_res.exit_code}).")
                failing_diagnostic = verify_res.format_diagnostic()
                print(f"  [!] Diagnostic:\n{(verify_res.stdout + verify_res.stderr).strip()[-1500:]}\n")

            if attempt < self.max_attempts:
                print("  [i] Backtracking to clean baseline before next attempt...")
                self.git_tools.reset_to_baseline()

        return False, f"Test suite creation bounded: Failed after {self.max_attempts} attempts."