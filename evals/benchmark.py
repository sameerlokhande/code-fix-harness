"""Comparative Benchmark: Harnessed vs. Ungoverned Agent.

Evaluates repair reliability between an unconstrained raw prompt approach
versus the governed AutoFixer verification harness.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
import sys

_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

import ollama
import pytest

from src.harness.runner import HarnessRunner
from src.harness.verifier import PolyglotVerifier


def reset_mock_workspace(source: Path, destination: Path) -> None:
    """Recreate pristine mock workspace from source template."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def run_ungoverned_agent(repo_path: Path, model_name: str) -> bool:
    """Simulates an ungoverned agent: relies on model self-reporting without harness verification."""
    test_file = repo_path / "math_service.py"
    code = test_file.read_text()

    prompt = (
        f"Here is a file with a failing test where dividing by zero crashes:\n\n{code}\n\n"
        f"Fix the bug so that `divide_numbers` raises ValueError('Cannot divide by zero') when denominator is 0. "
        f"Respond with the updated file content only."
    )

    response = ollama.chat(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_output = response["message"]["content"]

    # Ungoverned: naively overwrites code based on model claim, with no outer test harness
    # Strip markdown code blocks if present
    cleaned = raw_output
    if "```python" in cleaned:
        cleaned = cleaned.split("```python")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    test_file.write_text(cleaned)

    # Post-hoc check to see if it accidentally passed
    verifier = PolyglotVerifier(repo_path)
    result = verifier.verify()
    return result.passed


def run_harnessed_agent(repo_path: Path, model_name: str) -> bool:
    """Runs the controlled AutoFixer harness with programmatic verification & repair loops."""
    runner = HarnessRunner(
        repo_path=repo_path,
        model_name=model_name,
        max_attempts=3,
        max_iterations=8,
    )
    success, _ = runner.run(
        bug_objective="Fix divide_numbers so test_divide_numbers_zero passes without breaking test_divide_numbers_valid."
    )
    return success


def run_benchmark(model_name: str = "qwen2.5:7b") -> None:
    """Run side-by-side benchmark comparison."""
    base_repo = Path(__file__).parent / "mock_bug_repo"

    print("=================================================================")
    print("AUTODETECTION & GOVERNANCE BENCHMARK")
    print(f"Model Under Test: {model_name}")
    print("=================================================================")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Run Ungoverned Trial
        ungoverned_dir = tmp_path / "ungoverned"
        reset_mock_workspace(base_repo, ungoverned_dir)
        print("\n[*] Starting Trial 1: Ungoverned Agent (Prompt-and-Pray)...")
        ungoverned_success = run_ungoverned_agent(ungoverned_dir, model_name)
        print(f"[-] Ungoverned Result: {'PASSED' if ungoverned_success else 'FAILED'}")

        # 2. Run Harnessed Trial
        harnessed_dir = tmp_path / "harnessed"
        reset_mock_workspace(base_repo, harnessed_dir)
        print("\n[*] Starting Trial 2: AutoFixer Harnessed Agent...")
        harnessed_success = run_harnessed_agent(harnessed_dir, model_name)
        print(f"[-] Harnessed Result: {'PASSED' if harnessed_success else 'FAILED'}")

        print("\n=================================================================")
        print("BENCHMARK SUMMARY")
        print("=================================================================")
        print(f"Ungoverned Success Rate: {'100%' if ungoverned_success else '0%'}")
        print(f"Harnessed Success Rate:   {'100%' if harnessed_success else '0%'}")
        print("Core Difference: The harness guarantees zero false-positive completions via exit code 0.")


if __name__ == "__main__":
    run_benchmark()