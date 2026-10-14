#!/usr/bin/env python3
"""autofix.py: Simplified entrypoint for AutoFixer with intent detection and mode selection."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

# Automatically load environment variables from .env
load_dotenv()

_ROOT_DIR = Path(__file__).resolve().parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from src.harness.intent import RunnerMode, classify_intent
from src.harness.runner import HarnessRunner
from src.harness.workspace import WorkspaceError, WorkspaceManager


def is_git_url(target: str) -> bool:
    return target.startswith(("http://", "https://", "git@", "ssh://"))


def get_user_inputs() -> tuple[str, str, RunnerMode, bool]:
    # Check if command line arguments were provided: autofix.py <target> <objective> [mode]
    if len(sys.argv) >= 3:
        target = sys.argv[1].strip()
        objective = sys.argv[2].strip()
        mode_str = sys.argv[3].strip() if len(sys.argv) >= 4 else "auto"
        interactive = "--interactive" in sys.argv or "-i" in sys.argv
        if mode_str == "auto":
            mode = classify_intent(objective)
        else:
            mode = RunnerMode(mode_str)
        return target, objective, mode, interactive

    print("\n" + "=" * 65)
    print(" [*] AutoFixer: Autonomous Polyglot Engineering & Verification")
    print("=" * 65)
    print("Provide either:")
    print("  - GitHub URL (e.g. https://github.com/user/repo.git)")
    print("  - Local Folder Path (e.g. ./my-project or ~/projects/api)")
    print("-" * 65)

    target = ""
    while not target:
        target = input("\n[1/3] Paste Repo URL or Local Path: ").strip().strip("'\"")
        if not target:
            print("Target cannot be empty.")
            continue
        if not is_git_url(target) and not Path(target).expanduser().resolve().exists():
            print(f"Error: Local path '{target}' does not exist on this machine. Try again.")
            target = ""

    print("\n[2/3] Enter Task Objective (Bug report, feature to write, or test suite to build):")
    print("     (Type or paste your prompt, then press Enter twice to submit)")
    prompt_lines: list[str] = []
    while True:
        try:
            line = input()
            if not line and prompt_lines:
                break
            if line:
                prompt_lines.append(line)
        except EOFError:
            break

    objective = "\n".join(prompt_lines).strip()

    # Intent detection with opportunity to override
    print("\n  [*] Analyzing intent...")
    detected_mode = classify_intent(objective)
    print(f"  Detected Mode: {detected_mode.value.upper()}")
    print("  Press Enter to accept, or type [fix / write-code / write-tests]:")
    override = input("  Mode override: ").strip().lower()
    if override in ("fix", "write-code", "write-tests"):
        mode = RunnerMode(override)
    else:
        mode = detected_mode

    interactive_choice = input("\n[3/3] Enable interactive test verification approval? [y/N]: ").strip().lower()
    interactive = interactive_choice in ("y", "yes")

    return target, objective, mode, interactive


def main() -> int:
    target, objective, mode, interactive = get_user_inputs()

    if not objective:
        print("\n[!] Error: Objective prompt cannot be empty.", file=sys.stderr)
        return 1

    target_is_remote = is_git_url(target)
    local_source_path = None if target_is_remote else Path(target).expanduser().resolve()

    workspace = WorkspaceManager(target=str(local_source_path or target))

    try:
        sandbox_path = workspace.prepare()
    except WorkspaceError as err:
        print(f"\n[FATAL] Workspace setup failed: {err}", file=sys.stderr)
        return 1

    try:
        runner = HarnessRunner(
            repo_path=sandbox_path,
            mode=mode,
            interactive=interactive,
            sandbox_python=workspace.sandbox_python,
            model_name="qwen2.5-coder:7b",
            max_attempts=3,
            max_iterations=12,
        )

        success, summary = runner.run(bug_objective=objective)

        print("\n" + "=" * 65)
        print("EXECUTION RESULT")
        print("=" * 65)
        print(summary)

        if not success:
            print("\n[X] Verification failed to satisfy acceptance criteria within budget.")
            return 1

        patch_file = Path("remediation.patch").resolve()
        # In FIX mode, ephemeral tests created in sandbox are not added to patch or copied to target repo
        keep_test_files = (mode != RunnerMode.FIX)
        workspace.export_patch(patch_file, keep_test_files=keep_test_files)
        print(f"\n[OK] Clean verified patch generated: {patch_file}")

        if not target_is_remote and local_source_path:
            print("-" * 65)
            choice = input(
                f"Apply verified changes directly to '{local_source_path}'? [y/N]: "
            ).strip().lower()
            if choice == "y":
                applied, msg = workspace.apply_to_source(
                    local_source_path,
                    patch_file,
                    keep_test_files=keep_test_files,
                )
                if applied:
                    print(f"[OK] {msg}")
                    # Offer to publish to a new GitHub repository
                    print("-" * 65)
                    pub_choice = input(
                        f"Publish '{local_source_path.name}' as a new GitHub repository? [y/N]: "
                    ).strip().lower()
                    if pub_choice in ("y", "yes"):
                        from src.harness.publisher import GitHubPublisher
                        publisher = GitHubPublisher()
                        pub_ok, pub_msg, repo_url = publisher.publish_local_folder(local_source_path)
                        if pub_ok:
                            print(f"\n[OK] {pub_msg}")
                            print(f"[+] GitHub URL: {repo_url}")
                        else:
                            print(f"\n[!] Publishing failed: {pub_msg}")
                else:
                    print(f"[!] Could not auto-apply changes: {msg}")
                    print(f"    Patch file preserved at: {patch_file}")

        elif target_is_remote:
            print("-" * 65)
            choice = input(
                f"Push verified remediation branch ('{workspace.branch_name}') to remote repository? [y/N]: "
            ).strip().lower()
            if choice == "y":
                pushed, msg, pr_url = workspace.push_to_remote(commit_message=f"AutoFixer: {objective[:80]}")
                if pushed:
                    print(f"[OK] {msg}")
                    if pr_url:
                        print(f"[+] Open Pull Request: {pr_url}")
                else:
                    print(f"[!] {msg}")
                    print(f"    Local verified patch preserved at: {patch_file}")

        return 0

    finally:
        workspace.cleanup()


if __name__ == "__main__":
    sys.exit(main())