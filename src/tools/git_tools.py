"""src/tools/git_tools.py: Sandboxed file system, git operations, and test runner."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import subprocess
from typing import Any

from src.harness.interceptors import SecurityInterceptor
from src.harness.verifier import PolyglotVerifier

log = logging.getLogger(__name__)

# Max file size the agent is allowed to read or write (512 KB)
_MAX_FILE_BYTES = 512 * 1024


class SandboxedGitTools:
    """Tool execution core with strict workspace containment."""

    def __init__(
        self,
        workspace_root: Path,
        interceptor: SecurityInterceptor,
        verifier: PolyglotVerifier | None = None,
        custom_test_command: list[str] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.interceptor = interceptor
        self.verifier = verifier or PolyglotVerifier(self.workspace_root)
        # FIX #1: store custom_test_command so run_tests() forwards it correctly
        self.custom_test_command = custom_test_command

    def _normalize_path(self, path: str | None) -> str:
        """Coerce empty, whitespace, or null paths to the current directory."""
        if not path or not path.strip():
            return "."
        return path.strip()

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        try:
            target = self.interceptor.assert_path_contained(self._normalize_path(path))
            if not target.is_file():
                return {"error": f"File does not exist: {path}"}

            # FIX #9: guard against reading huge files
            file_size = target.stat().st_size
            if file_size > _MAX_FILE_BYTES:
                return {
                    "error": (
                        f"File '{path}' is too large to read ({file_size:,} bytes). "
                        f"Maximum allowed is {_MAX_FILE_BYTES:,} bytes. "
                        "Use start_line/end_line to read a specific range."
                    )
                }

            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            total_lines = len(lines)

            s_idx = max(1, start_line or 1) - 1
            e_idx = min(total_lines, end_line or total_lines)

            selected_lines = lines[s_idx:e_idx]
            numbered = [f"{i + s_idx + 1:4d} | {line}" for i, line in enumerate(selected_lines)]

            return {
                "path": str(target.relative_to(self.workspace_root)),
                "total_lines": total_lines,
                "content": "\n".join(numbered),
            }
        except Exception as exc:
            log.exception("read_file failed for path=%s", path)
            return {"error": f"read_file failed: {exc}"}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        try:
            target = self.interceptor.assert_path_contained(self._normalize_path(path))

            # FIX #9: guard against writing arbitrarily large content
            encoded = content.encode("utf-8")
            if len(encoded) > _MAX_FILE_BYTES:
                return {
                    "error": (
                        f"Content for '{path}' is too large to write ({len(encoded):,} bytes). "
                        f"Maximum allowed is {_MAX_FILE_BYTES:,} bytes. "
                        "Break the content into smaller files or chunks."
                    )
                }

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)
            log.info("write_file: wrote %d bytes to %s", len(encoded), path)
            return {
                "path": str(target.relative_to(self.workspace_root)),
                "bytes_written": len(encoded),
                "status": "success",
            }
        except Exception as exc:
            log.exception("write_file failed for path=%s", path)
            return {"error": f"write_file failed: {exc}"}

    def apply_patch(self, patch: str) -> dict[str, Any]:
        try:
            process = subprocess.run(
                ["git", "apply", "--whitespace=fix", "-"],
                input=patch,
                text=True,
                cwd=str(self.workspace_root),
                capture_output=True,
                check=False,
            )
            if process.returncode != 0:
                return {
                    "error": f"git apply failed (exit {process.returncode}): {process.stderr or process.stdout}",
                    "hint": "Check diff context, or use write_file to overwrite the file directly.",
                }
            log.info("apply_patch: patch applied successfully")
            return {"status": "success", "message": "Patch applied successfully"}
        except Exception as exc:
            log.exception("apply_patch failed")
            return {"error": f"apply_patch failed: {exc}"}

    def search_code(self, query: str, path: str = ".", max_matches: int = 25) -> dict[str, Any]:
        try:
            base_dir = self.interceptor.assert_path_contained(self._normalize_path(path))
            compiled_regex = re.compile(query, re.IGNORECASE)
            matches = []

            for file_path in base_dir.rglob("*"):
                if not file_path.is_file() or ".git" in file_path.parts:
                    continue

                # Skip files that are too large to search
                try:
                    if file_path.stat().st_size > _MAX_FILE_BYTES:
                        continue
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                found_in_file = False
                for line_num, line in enumerate(content.splitlines(), start=1):
                    if compiled_regex.search(line):
                        matches.append({
                            "file": str(file_path.relative_to(self.workspace_root)),
                            "line": line_num,
                            "text": line.strip()[:160],
                        })
                        found_in_file = True
                        # FIX #8: once we hit the overall cap, stop all iteration immediately
                        if len(matches) >= max_matches:
                            break
                if found_in_file and len(matches) >= max_matches:
                    break

            return {"query": query, "matches": matches, "count": len(matches)}
        except Exception as exc:
            log.exception("search_code failed for query=%s", query)
            return {"error": f"search_code failed: {exc}"}

    def list_directory(self, path: str = ".", max_depth: int = 2) -> dict[str, Any]:
        try:
            base_dir = self.interceptor.assert_path_contained(self._normalize_path(path))
            if not base_dir.is_dir():
                return {"error": f"Directory not found: {path}"}

            tree: list[str] = []

            def _walk(curr: Path, depth: int) -> None:
                if depth > max_depth:
                    return
                for item in sorted(curr.iterdir()):
                    if item.name.startswith(".git"):
                        continue
                    prefix = "  " * depth + ("📁 " if item.is_dir() else "📄 ")
                    rel = item.relative_to(self.workspace_root)
                    tree.append(f"{prefix}{item.name} ({rel})")
                    if item.is_dir():
                        _walk(item, depth + 1)

            _walk(base_dir, 0)
            return {"path": path, "entries": tree}
        except Exception as exc:
            log.exception("list_directory failed for path=%s", path)
            return {"error": f"list_directory failed: {exc}"}

    def run_tests(self) -> dict[str, Any]:
        """Execute the project test suite and return the exit code and outputs.

        FIX #1: Forwards custom_test_command so the CLI --test-command flag is honoured.
        """
        try:
            res = self.verifier.verify(custom_command=self.custom_test_command)
            output = (res.stdout + "\n" + res.stderr).strip()

            hint = "All tests passed with exit code 0." if res.passed else "Tests failed. Inspect the output trace."

            # Check for missing external dependency errors (ModuleNotFoundError / ImportError)
            missing_mod_match = re.search(r"(?:ModuleNotFoundError|ImportError):\s+No module named\s+['\"]([^'\"]+)['\"]", output)
            if missing_mod_match:
                missing_module = missing_mod_match.group(1)
                hint = (
                    f"[HARNESS CONSTRAINT - MISSING ENVIRONMENT DEPENDENCY: '{missing_module}']:\n"
                    f"You DO NOT have terminal/pip install access in this sandbox. Writing to 'requirements.txt' will NOT install it.\n"
                    f"ALTERNATIVE APPROACH REQUIRED:\n"
                    f"1. In your tests, use `unittest.mock` (or `pytest.MonkeyPatch` / `sys.modules`) to mock `{missing_module}`\n"
                    f"   Example: `import sys, unittest.mock; sys.modules['{missing_module}'] = unittest.mock.MagicMock()` BEFORE importing repo files.\n"
                    f"2. OR write unit tests and fixes for modules that do not depend on external uninstalled packages.\n"
                    f"3. Do NOT call `run_tests` again without implementing a mock or changing your test strategy."
                )

            log.info("run_tests: exit_code=%d passed=%s", res.exit_code, res.passed)
            return {
                "passed": res.passed,
                "exit_code": res.exit_code,
                "output": output[-3000:] if output else "No output produced.",
                "hint": hint,
            }
        except Exception as exc:
            log.exception("run_tests failed")
            return {"error": f"run_tests failed: {exc}"}

    def git_diff(self) -> dict[str, Any]:
        try:
            # Stage intent-to-add so untracked new files appear in git diff
            subprocess.run(
                ["git", "add", "-N", "."],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )
            process = subprocess.run(
                ["git", "diff"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )
            return {
                "diff": process.stdout,
                "has_changes": bool(process.stdout.strip()),
            }
        except Exception as exc:
            log.exception("git_diff failed")
            return {"error": f"git_diff failed: {exc}"}

    def reset_to_baseline(self) -> dict[str, Any]:
        """Discard all uncommitted and committed changes made during a failed attempt."""
        try:
            # Revert any uncommitted working tree changes
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )
            # Remove untracked files and directories created during the attempt
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )
            log.info("reset_to_baseline: workspace restored to HEAD")
            return {"success": True, "message": "Sandbox workspace reset cleanly to baseline state."}
        except Exception as exc:
            log.exception("reset_to_baseline failed")
            return {"error": f"Failed to reset workspace: {exc}"}