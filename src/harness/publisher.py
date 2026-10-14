"""src/harness/publisher.py: Publish verified local projects to newly created GitHub repositories."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

_DEFAULT_GITIGNORE = """
# Python caches and virtual environments
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.venv/
venv/
.sandbox_venv/
*.orig
remediation.patch
"""


class GitHubPublisher:
    """Manages GitHub repository creation and initial code push from local folders."""

    def __init__(self, env_path: Path | None = None) -> None:
        self.env_path = env_path or Path(".env").resolve()

    def resolve_token(self, interactive: bool = True) -> str | None:
        """Resolve GitHub personal access token from environment, .env, or user prompt."""
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if token and token.strip():
            return token.strip()

        # Check .env file directly if not loaded
        if self.env_path.exists():
            for line in self.env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("GITHUB_TOKEN=") or line.startswith("GH_TOKEN="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        return val

        if not interactive:
            return None

        print("\n" + "=" * 65)
        print(" [!] GitHub Authentication Required")
        print("=" * 65)
        print("To create a new repository on your GitHub account, AutoFixer needs a")
        print("GitHub Personal Access Token (classic with 'repo' scope or fine-grained).")
        print("You can generate one at: https://github.com/settings/tokens")
        print("-" * 65)

        user_token = input("Paste your GitHub Token (or press Enter to skip): ").strip()
        if not user_token:
            return None

        save_choice = input("Save token to .env for future sessions? [Y/n]: ").strip().lower()
        if save_choice not in ("n", "no"):
            self._save_token_to_env(user_token)
            print("[OK] Token saved to .env")

        return user_token

    def _save_token_to_env(self, token: str) -> None:
        """Append or update GITHUB_TOKEN in local .env file."""
        try:
            content = self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else ""
            if "GITHUB_TOKEN=" in content:
                content = re.sub(r"GITHUB_TOKEN=.*", f"GITHUB_TOKEN={token}", content)
            else:
                prefix = "\n" if content and not content.endswith("\n") else ""
                content = f"{content}{prefix}GITHUB_TOKEN={token}\n"
            self.env_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            log.warning("Could not save token to .env: %s", exc)

    def validate_token(self, token: str) -> tuple[bool, str, str | None]:
        """Validate token with GitHub API and retrieve username.

        Returns:
            (is_valid, username_or_error, avatar_or_name)
        """
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "AutoFixer-Harness",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                username = data.get("login", "")
                return True, username, data.get("name")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return False, "Invalid GitHub token (401 Unauthorized).", None
            return False, f"GitHub API error (HTTP {exc.code}): {exc.reason}", None
        except Exception as exc:
            return False, f"Connection to GitHub failed: {exc}", None

    def create_github_repo(
        self,
        token: str,
        name: str,
        is_private: bool = True,
        description: str = "",
    ) -> tuple[bool, dict, str]:
        """Call GitHub REST API to create a new repository under the authenticated user."""
        url = "https://api.github.com/user/repos"
        payload = {
            "name": name,
            "private": is_private,
            "description": description or "Created by AutoFixer Autonomous Engineering Harness",
            "auto_init": False,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "AutoFixer-Harness",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return True, data, "Repository created successfully."
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("message", err_body)
                errors = err_json.get("errors", [])
                if errors and isinstance(errors, list):
                    msg += " — " + "; ".join(e.get("message", "") for e in errors if isinstance(e, dict))
            except Exception:
                msg = err_body
            return False, {}, f"GitHub repo creation failed (HTTP {exc.code}): {msg}"
        except Exception as exc:
            return False, {}, f"Network error during repo creation: {exc}"

    def publish_local_folder(
        self,
        local_path: Path,
        repo_name: str | None = None,
        is_private: bool | None = None,
        description: str = "",
        interactive: bool = True,
    ) -> tuple[bool, str, str | None]:
        """Interactive helper to create a GitHub repo and push local folder code."""
        local_path = local_path.resolve()
        if not local_path.exists() or not local_path.is_dir():
            return False, f"Local path '{local_path}' does not exist or is not a directory.", None

        # 1. Resolve authentication token
        token = self.resolve_token(interactive=interactive)
        if not token:
            return False, "GitHub publishing skipped: No token provided.", None

        # 2. Validate token and get username
        valid, username, _ = self.validate_token(token)
        if not valid:
            return False, f"GitHub authentication failed: {username}", None

        print(f"[*] Authenticated as GitHub user: @{username}")

        # 3. Resolve repo name and visibility
        if not repo_name:
            default_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "-", local_path.name)
            if interactive:
                chosen_name = input(f"GitHub repository name [{default_name}]: ").strip()
                repo_name = chosen_name if chosen_name else default_name
            else:
                repo_name = default_name

        if is_private is None:
            if interactive:
                vis_choice = input("Repository visibility: [P]rivate / [p]ublic? (default: Private): ").strip().lower()
                is_private = vis_choice not in ("public", "pub")
            else:
                is_private = True

        # 4. Create repo on GitHub
        print(f"[*] Creating GitHub repository '{username}/{repo_name}' (private={is_private})...")
        created, repo_data, err_msg = self.create_github_repo(
            token=token,
            name=repo_name,
            is_private=is_private,
            description=description,
        )
        if not created:
            return False, err_msg, None

        html_url = repo_data.get("html_url", f"https://github.com/{username}/{repo_name}")
        clone_url = repo_data.get("clone_url", f"https://github.com/{username}/{repo_name}.git")

        # 5. Initialize git in local folder if needed
        is_git_repo = (local_path / ".git").exists()
        if not is_git_repo:
            log.info("Initializing git repository in %s", local_path)
            subprocess.run(["git", "init"], cwd=str(local_path), check=True, capture_output=True)

        # 6. Ensure clean .gitignore
        gitignore_path = local_path / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(_DEFAULT_GITIGNORE.strip() + "\n", encoding="utf-8")

        # 7. Stage and commit
        subprocess.run(["git", "add", "."], cwd=str(local_path), check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c", "user.name=AutoFixer Bot",
                "-c", "user.email=autofixer@localhost",
                "commit",
                "-m", "Initial commit with verified code from AutoFixer",
            ],
            cwd=str(local_path),
            capture_output=True,
            check=False,
        )

        # 8. Set default branch to main
        subprocess.run(["git", "branch", "-M", "main"], cwd=str(local_path), capture_output=True, check=False)

        # 9. Configure remote with authenticated URL for push
        auth_url = f"https://x-access-token:{token}@github.com/{username}/{repo_name}.git"

        # Check if origin exists
        check_remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(local_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if check_remote.returncode == 0:
            subprocess.run(["git", "remote", "set-url", "origin", auth_url], cwd=str(local_path), check=True, capture_output=True)
        else:
            subprocess.run(["git", "remote", "add", "origin", auth_url], cwd=str(local_path), check=True, capture_output=True)

        # 10. Push to remote
        print(f"[*] Pushing local code to GitHub: {html_url} ...")
        push_res = subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=str(local_path),
            capture_output=True,
            text=True,
            check=False,
        )

        # 11. Security clean-up: Strip the plain-text token from origin URL in .git/config
        clean_url = f"https://github.com/{username}/{repo_name}.git"
        subprocess.run(["git", "remote", "set-url", "origin", clean_url], cwd=str(local_path), capture_output=True, check=False)

        if push_res.returncode == 0:
            return True, f"Project successfully published to GitHub!", html_url
        else:
            err = (push_res.stderr or push_res.stdout).strip()
            # Redact token if present in error message
            err = err.replace(token, "[REDACTED_TOKEN]")
            return False, f"Git push failed: {err}", html_url

