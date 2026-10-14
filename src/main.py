"""CLI Entrypoint for AutoFixer.

Accepts Git URLs or local directory paths alongside objectives.
Supports 3 modes (fix, write-code, write-tests) with automatic intent classification
and local sandbox venv execution.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from dotenv import load_dotenv

# Automatically load environment variables from .env
load_dotenv()


def _configure_logging(verbose: bool = False) -> None:
    """Set up structured logging to both stderr and a rotating log file."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
    ]
    try:
        from logging.handlers import RotatingFileHandler
        handlers.append(
            RotatingFileHandler(
                "autofixer.log",
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=3,
                encoding="utf-8",
            )
        )
    except Exception:
        pass  # If log file can't be created, fall back to stderr only

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)


_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from src.harness.intent import RunnerMode, classify_intent
from src.harness.runner import HarnessRunner
from src.harness.workspace import WorkspaceError, WorkspaceManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autofixer",
        description="Autonomous polyglot code remediation and engineering agent with deterministic verification harness.",
    )
    parser.add_argument(
        "--target",
        "-t",
        type=str,
        default=None,
        help="Git repository URL (https/ssh) or local folder path",
    )
    parser.add_argument(
        "--objective",
        "-o",
        type=str,
        default=None,
        help="Task objective (bug report, feature to write, or test suite to build)",
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=["auto", "fix", "write-code", "write-tests"],
        default="auto",
        help="Operating mode: 'auto' (detect intent with lightweight model), 'fix', 'write-code', or 'write-tests'",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Pause and ask for user confirmation with git diff preview before running tests",
    )
    parser.add_argument(
        "--classifier-model",
        type=str,
        default=None,
        help="Model for intent classification (default: auto-detected from installed models, e.g. qwen2.5:1.5b)",
    )
    parser.add_argument(
        "--patch-out",
        type=Path,
        default=Path("remediation.patch"),
        help="Output path for the verified git diff patch (default: ./remediation.patch)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="qwen2.5-coder:7b",
        help="Local Ollama coding model name (default: qwen2.5-coder:7b)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum macro recovery attempts (default: 3)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=12,
        help="Maximum tool steps per attempt (default: 12)",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Preserve sandboxed repository directory after completion",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG-level logging output",
    )
    return parser


def prompt_user_inputs(target: str | None, objective: str | None) -> tuple[str, str]:
    """Collect interactive inputs if flags were omitted."""
    print("=================================================================")
    print("AutoFixer — Autonomous Polyglot Code Engineering & Verification")
    print("=================================================================")

    resolved_target = target
    while not resolved_target or not resolved_target.strip():
        resolved_target = input("Enter Git Repo URL or Local Path: ").strip()

    resolved_objective = objective
    while not resolved_objective or not resolved_objective.strip():
        print("\nEnter Objective (Bug report, feature request, or test goal — press Enter twice):")
        lines = []
        while True:
            line = input()
            if not line and lines:
                break
            if line:
                lines.append(line)
        resolved_objective = "\n".join(lines).strip()

    return resolved_target, resolved_objective


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    _configure_logging(verbose=args.verbose)
    log = logging.getLogger(__name__)

    # Interactively prompt if target or objective was omitted
    target, objective = prompt_user_inputs(args.target, args.objective)

    # Resolve mode: auto-detect via intent classifier or use explicit selection
    if args.mode == "auto":
        print("[*] Analyzing user intent...")
        selected_mode = classify_intent(objective, model_name=args.classifier_model)
        print(f"[OK] Detected Intent Mode: {selected_mode.value.upper()}")
    else:
        selected_mode = RunnerMode(args.mode)
        print(f"[*] Selected Mode: {selected_mode.value.upper()}")

    workspace = WorkspaceManager(
        target=target,
        keep_workspace=args.keep_workspace,
    )

    try:
        sandbox_path = workspace.prepare()
    except WorkspaceError as err:
        log.error("Workspace initialization error: %s", err)
        print(f"\n[FATAL] Workspace initialization error: {err}", file=sys.stderr)
        return 1

    try:
        runner = HarnessRunner(
            repo_path=sandbox_path,
            mode=selected_mode,
            interactive=args.interactive,
            sandbox_python=workspace.sandbox_python,
            model_name=args.model,
            max_attempts=args.max_attempts,
            max_iterations=args.max_iterations,
        )

        success, result_message = runner.run(bug_objective=objective)

        print("\n" + "=" * 65)
        print("FINAL VERIFIED REPORT")
        print("=" * 65)
        print(result_message)

        if success:
            patch_dest = args.patch_out.resolve()
            keep_tests = (selected_mode != RunnerMode.FIX)
            workspace.export_patch(patch_dest, keep_test_files=keep_tests)
            print(f"\n[OK] Verified Git patch exported to: {patch_dest}")
            print(f"[i] To apply to your main branch: `git apply {patch_dest}`")
            log.info("Task succeeded. Patch exported to: %s", patch_dest)
            return 0
        else:
            print("\n[X] Verification failed to reach exit code 0 within budget.")
            log.warning("Task failed within budget.")
            return 1

    finally:
        workspace.cleanup()


if __name__ == "__main__":
    sys.exit(main())