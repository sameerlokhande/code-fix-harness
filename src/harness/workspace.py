"""src/harness/workspace.py: Workspace isolation for Git URLs and local folders with local sandbox venv."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

log = logging.getLogger(__name__)

# Git identity used when committing imported non-git directories in CI environments
_GIT_AUTHOR_NAME = "AutoFixer Bot"
_GIT_AUTHOR_EMAIL = "autofixer@localhost"


class WorkspaceError(RuntimeError):
    """Raised when repository cloning, checkout, or isolation fails."""


def _venv_python(venv_path: Path) -> str:
    """Get the Python executable path inside the venv (cross-platform)."""
    win_py = venv_path / "Scripts" / "python.exe"
    if win_py.exists():
        return str(win_py)
    unix_py = venv_path / "bin" / "python"
    if unix_py.exists():
        return str(unix_py)
    return sys.executable


def _venv_pip(venv_path: Path) -> str:
    """Get the pip executable path inside the venv (cross-platform)."""
    win_pip = venv_path / "Scripts" / "pip.exe"
    if win_pip.exists():
        return str(win_pip)
    unix_pip = venv_path / "bin" / "pip"
    if unix_pip.exists():
        return str(unix_pip)
    return "pip"


def _clean_patch_diff(
    diff_text: str,
    keep_test_files: bool = True,
    baseline_files: set[str] | None = None,
) -> str:
    """Filter out binary files, cache directories, and ephemeral tests when keep_test_files=False."""
    if not diff_text:
        return ""
    blocks = re.split(r"(?=diff --git )", diff_text)
    clean_blocks = []
    baseline_set = baseline_files or set()

    for block in blocks:
        if not block.strip():
            continue
        if any(ign in block for ign in ("__pycache__", ".pytest_cache", ".pyc", ".sandbox_venv", ".venv")):
            continue
        if "Binary files " in block:
            continue

        # In FIX mode, skip newly generated test files that were not in baseline
        if not keep_test_files:
            file_match = re.search(r"diff --git a/(.*?) b/", block)
            if file_match:
                rel_path = file_match.group(1).replace("\\", "/")
                is_test = (
                    rel_path.startswith("test_")
                    or rel_path.endswith("_test.py")
                    or "/test" in rel_path
                    or "tests/" in rel_path
                )
                if is_test and rel_path not in baseline_set:
                    continue

        clean_blocks.append(block)
    return "".join(clean_blocks)


def _build_pr_url(remote_url: str, branch_name: str) -> str | None:
    """Derive GitHub or GitLab new PR / MR URL from a repository clone URL."""
    # GitHub: https://github.com/owner/repo or git@github.com:owner/repo.git
    gh_match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?", remote_url)
    if gh_match:
        owner, repo = gh_match.group(1), gh_match.group(2)
        return f"https://github.com/{owner}/{repo}/pull/new/{branch_name}"

    # GitLab: https://gitlab.com/owner/repo or git@gitlab.com:owner/repo.git
    gl_match = re.search(r"gitlab\.com[:/]([^/]+)/([^/.]+)(?:\.git)?", remote_url)
    if gl_match:
        owner, repo = gl_match.group(1), gl_match.group(2)
        return f"https://gitlab.com/{owner}/{repo}/-/merge_requests/new?merge_request%5Bsource_branch%5D={branch_name}"

    return None


class WorkspaceManager:
    """Provisions and sandboxes Git repositories for remediation runs with an isolated venv."""

    GIT_URL_PATTERNS = [
        re.compile(r"^https?://.*\.git$|^https?://github\.com/|^https?://gitlab\.com/"),
        re.compile(r"^git@.*:.*\.git$"),
        re.compile(r"^ssh://.*"),
    ]
    VENV_DIR = ".sandbox_venv"

    def __init__(self, target: str, keep_workspace: bool = False, create_isolated_venv: bool = False) -> None:
        self.raw_target = target.strip()
        self.keep_workspace = keep_workspace
        self.create_isolated_venv = create_isolated_venv
        self.is_remote = self._is_remote_url(self.raw_target)
        self._temp_dir_obj = tempfile.TemporaryDirectory(prefix="autofixer_")
        self.workspace_path = Path(self._temp_dir_obj.name).resolve()
        self.branch_name = f"autofixer/remediation-{int(time.time())}"
        self.sandbox_python: str = sys.executable
        self.baseline_files: set[str] = set()

    @classmethod
    def _is_remote_url(cls, target: str) -> bool:
        return any(pattern.search(target) for pattern in cls.GIT_URL_PATTERNS)

    def prepare(self) -> Path:
        """Clone or copy target into sandbox, set up environment, and prepare remediation branch."""
        try:
            if self.is_remote:
                self._clone_remote()
            else:
                self._isolate_local()
            self._create_branch()

            # Record initial repository files so ephemeral test files can be excluded
            self.baseline_files = {
                str(p.relative_to(self.workspace_path)).replace("\\", "/")
                for p in self.workspace_path.rglob("*")
                if p.is_file() and ".git" not in p.parts
            }

            if self.create_isolated_venv:
                self._create_sandbox_venv()
            else:
                self.sandbox_python = sys.executable
                log.info("Using host environment python: %s", self.sandbox_python)
        except Exception:
            # FIX #16: always clean up the temp dir if preparation fails mid-way
            try:
                self._temp_dir_obj.cleanup()
            except Exception:
                pass
            raise
        return self.workspace_path

    def _clone_remote(self) -> None:
        log.info("Cloning remote repository: %s", self.raw_target)
        print(f"[*] Cloning remote repository: {self.raw_target} ...")
        cmd = ["git", "clone", "--depth", "50", self.raw_target, str(self.workspace_path)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise WorkspaceError(f"Remote git clone failed:\n{res.stderr or res.stdout}")

    def _isolate_local(self) -> None:
        source_path = Path(self.raw_target).expanduser().resolve()
        if not source_path.exists() or not source_path.is_dir():
            raise WorkspaceError(f"Local source directory does not exist: {source_path}")

        log.info("Sandboxing local directory: %s", source_path)
        print(f"[*] Sandboxing local directory: {source_path} ...")
        if (source_path / ".git").exists():
            cmd = ["git", "clone", str(source_path), str(self.workspace_path)]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise WorkspaceError(f"Local repository clone failed:\n{res.stderr or res.stdout}")
        else:
            shutil.copytree(
                source_path,
                self.workspace_path,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".venv", "venv", ".sandbox_venv", "node_modules", "__pycache__", "target"),
            )
            subprocess.run(["git", "init"], cwd=str(self.workspace_path), check=True, capture_output=True)
            # Ensure runtime cache directories and venvs are excluded from git diffs
            exclude_file = self.workspace_path / ".git" / "info" / "exclude"
            try:
                exclude_file.parent.mkdir(parents=True, exist_ok=True)
                exclude_file.write_text(
                    "\n.venv/\nvenv/\n.sandbox_venv/\n__pycache__/\n*.pyc\n*.pyo\n*.pyd\n.pytest_cache/\nnode_modules/\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                log.warning("Could not write .git/info/exclude: %s", exc)

            subprocess.run(["git", "add", "."], cwd=str(self.workspace_path), check=True, capture_output=True)
            # FIX #10: supply git identity so commit succeeds on CI machines with no global config
            subprocess.run(
                [
                    "git",
                    "-c", f"user.name={_GIT_AUTHOR_NAME}",
                    "-c", f"user.email={_GIT_AUTHOR_EMAIL}",
                    "commit",
                    "-m", "Initial import for AutoFixer",
                ],
                cwd=str(self.workspace_path),
                check=True,
                capture_output=True,
            )

    def _create_branch(self) -> None:
        res = subprocess.run(
            ["git", "checkout", "-b", self.branch_name],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise WorkspaceError(f"Failed to create fix branch:\n{res.stderr}")
        log.info("Created remediation branch: %s", self.branch_name)

    def _create_sandbox_venv(self) -> None:
        """Create an isolated Python venv inside workspace and install dependencies."""
        venv_path = self.workspace_path / self.VENV_DIR

        # Ensure .sandbox_venv is ignored by git so it doesn't leak into diffs/patches
        exclude_file = self.workspace_path / ".git" / "info" / "exclude"
        try:
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            with exclude_file.open("a", encoding="utf-8") as f:
                f.write(f"\n{self.VENV_DIR}/\n")
        except Exception as exc:
            log.warning("Could not append to .git/info/exclude: %s", exc)

        print(f"  [*] Provisioning local sandbox venv...")
        log.info("Creating sandbox venv at: %s", venv_path)

        try:
            # Create the venv using system python
            res = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                log.warning("Venv creation failed (%s), falling back to host python: %s", res.returncode, res.stderr)
                self.sandbox_python = sys.executable
                return

            self.sandbox_python = _venv_python(venv_path)
            pip_exe = _venv_pip(venv_path)

            # Auto-install dependencies if manifest exists
            manifest_candidates = [
                ("requirements.txt", ["-r"]),
                ("requirement.txt", ["-r"]),
                ("pyproject.toml", ["-e", "."]),
                ("setup.py", ["-e", "."]),
            ]

            for manifest_name, install_args in manifest_candidates:
                manifest_path = self.workspace_path / manifest_name
                if manifest_path.exists():
                    print(f"  [*] Installing dependencies into sandbox venv from '{manifest_name}'...")
                    log.info("Installing dependencies from %s", manifest_name)
                    cmd = [pip_exe, "install", "-q", "--no-warn-script-location"]
                    if "-r" in install_args:
                        cmd.extend(["-r", str(manifest_path)])
                    else:
                        cmd.extend(["-e", str(self.workspace_path)])

                    dep_res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    if dep_res.returncode != 0:
                        log.warning("pip install from %s exited with %d: %s", manifest_name, dep_res.returncode, dep_res.stderr[:200])
                        print(f"  [!] Note: Some dependencies from '{manifest_name}' could not be installed automatically.")
                    else:
                        print(f"  [OK] Dependencies installed from '{manifest_name}'.")
                    break

            # Always ensure pytest is installed in the sandbox venv
            subprocess.run(
                [pip_exe, "install", "-q", "--no-warn-script-location", "pytest"],
                capture_output=True,
                text=True,
                check=False,
            )
            log.info("Sandbox venv ready with Python: %s", self.sandbox_python)
            print(f"  [OK] Sandbox venv ready.")

        except Exception as exc:
            log.warning("Venv initialization exception (%s), falling back to host python", exc)
            self.sandbox_python = sys.executable

    def export_patch(self, destination: Path, keep_test_files: bool = True) -> Path:
        """Export working changes as a standalone unified git diff patch file."""
        # Collect unstaged / intent-to-add changes
        subprocess.run(
            ["git", "add", "-N", "."],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            check=False,
        )

        # Unstaged working-tree diff
        unstaged = subprocess.run(
            ["git", "diff"],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            check=False,
        ).stdout

        # Staged / committed diff since the branch diverged from its parent commit
        committed = subprocess.run(
            ["git", "diff", "HEAD~1", "HEAD"],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            check=False,
        ).stdout

        patch_content = unstaged or committed

        # Final fallback: bare git diff (catches edge cases on shallow repos)
        if not patch_content.strip():
            patch_content = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(self.workspace_path),
                capture_output=True,
                text=True,
                check=False,
            ).stdout

        clean_content = _clean_patch_diff(
            patch_content,
            keep_test_files=keep_test_files,
            baseline_files=self.baseline_files,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(clean_content, encoding="utf-8")
        log.info("Patch exported to %s (%d bytes)", destination, len(clean_content))
        return destination

    def apply_to_source(self, target_path: Path, patch_file: Path, keep_test_files: bool = True) -> tuple[bool, str]:
        """Apply verified changes directly to the source directory.

        Attempts `git apply` first if the target is a git repository.
        Falls back to directly copying verified modified and new files.
        If keep_test_files is False, ephemeral sandbox tests are not applied.
        """
        target_path = target_path.resolve()
        if (target_path / ".git").exists():
            res = subprocess.run(
                ["git", "apply", "--whitespace=fix", str(patch_file)],
                cwd=str(target_path),
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                return True, f"Changes cleanly applied via git apply to '{target_path}'"

        # Direct copy fallback (for non-git directories or if git apply fails):
        diff_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
        )
        copied: list[str] = []
        for line in diff_res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                rel_file = parts[-1].strip().replace("\\", "/")
                if any(p in rel_file for p in ("__pycache__", ".pyc", ".pytest_cache", ".venv", ".sandbox_venv")):
                    continue

                # Ephemeral test check: if keep_test_files is False, skip new tests not in baseline
                if not keep_test_files:
                    is_test = (
                        rel_file.startswith("test_")
                        or rel_file.endswith("_test.py")
                        or "/test" in rel_file
                        or "tests/" in rel_file
                    )
                    if is_test and rel_file not in self.baseline_files:
                        continue

                src = self.workspace_path / rel_file
                dst = target_path / rel_file
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    copied.append(rel_file)

        if copied:
            return True, f"Verified changes applied directly: {', '.join(copied)}"

        return False, "No modified files detected to apply."

    def push_to_remote(self, commit_message: str = "AutoFixer: Verified remediation") -> tuple[bool, str, str | None]:
        """Commit verified changes in sandbox and push branch to remote repository.

        Returns:
            (success, message, pull_request_url)
        """
        # 1. Stage and commit changes in the sandbox repository
        subprocess.run(["git", "add", "."], cwd=str(self.workspace_path), check=False)
        subprocess.run(
            [
                "git",
                "-c", f"user.name={_GIT_AUTHOR_NAME}",
                "-c", f"user.email={_GIT_AUTHOR_EMAIL}",
                "commit",
                "-m", commit_message,
            ],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            check=False,
        )

        # 2. Push branch to origin
        push_res = subprocess.run(
            ["git", "push", "-u", "origin", self.branch_name],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            check=False,
        )

        if push_res.returncode == 0:
            pr_url = _build_pr_url(self.raw_target, self.branch_name)
            return True, f"Branch '{self.branch_name}' successfully pushed to remote repository.", pr_url
        else:
            err = (push_res.stderr or push_res.stdout).strip()
            log.warning("git push failed: %s", err)
            return False, f"Git push failed: {err}", None

    def _has_uncommitted(self) -> bool:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            check=False,
        )
        return bool(res.stdout.strip())

    def cleanup(self) -> None:
        if self.keep_workspace:
            log.info("Preserving workspace directory at: %s", self.workspace_path)
            print(f"[i] Preserving workspace directory at: {self.workspace_path}")
            return
        try:
            self._temp_dir_obj.cleanup()
            log.info("Workspace cleaned up.")
        except Exception as exc:
            log.warning("Failed to clean up workspace: %s", exc)