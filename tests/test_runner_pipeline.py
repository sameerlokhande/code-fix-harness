"""tests/test_runner_pipeline.py: Unit tests for the streamlined Harness Engineering Pipeline."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.harness.intent import RunnerMode
from src.harness.runner import (
    HarnessRunner,
    _extract_code_files,
    _collect_workspace_source_files,
)
from src.harness.verifier import VerificationResult


def test_extract_code_files_file_marker():
    text = """Here is the fix:
FILE: sum.py
```python
def addition(a, b):
    return a + b
```
"""
    files = _extract_code_files(text)
    assert len(files) == 1
    assert files[0][0] == "sum.py"
    assert "return a + b" in files[0][1]


def test_extract_code_files_multiple_files():
    text = """
FILE: sum.py
```python
def add(a, b): return a + b
```

FILE: test_sum.py
```python
import sum
def test_add(): assert sum.add(1, 2) == 3
```
"""
    files = _extract_code_files(text)
    assert len(files) == 2
    assert files[0][0] == "sum.py"
    assert files[1][0] == "test_sum.py"


def test_extract_code_files_inline_fence():
    text = """
```python:calculator.py
class Calc: pass
```
"""
    files = _extract_code_files(text)
    assert len(files) == 1
    assert files[0][0] == "calculator.py"
    assert "class Calc" in files[0][1]


def test_extract_code_files_fallback_single_fence():
    text = """Here is the updated function:
```python
def addition(a, b):
    return a + b
```
"""
    files = _extract_code_files(text, default_filename="sum.py")
    assert len(files) == 1
    assert files[0][0] == "sum.py"
    assert "return a + b" in files[0][1]


def test_collect_workspace_source_files(tmp_path: Path):
    (tmp_path / "sum.py").write_text("def add(a, b): return a + b", encoding="utf-8")
    (tmp_path / "test_sum.py").write_text("assert True", encoding="utf-8")
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "sum.cpython-312.pyc").write_bytes(b"cache")

    files = _collect_workspace_source_files(tmp_path)
    assert "sum.py" in files
    assert "test_sum.py" in files
    assert not any("__pycache__" in k for k in files)


def test_fix_pipeline_reproduction_and_remediation(tmp_path: Path):
    """Test FIX mode generates reproduction test when none exists, applies fix, and verifies exit code 0."""
    (tmp_path / "sum.py").write_text("def addition(a, b): return 0", encoding="utf-8")

    runner = HarnessRunner(
        repo_path=tmp_path,
        mode=RunnerMode.FIX,
        max_attempts=1,
    )

    with patch.object(runner.verifier, "verify") as mock_verify, \
         patch.object(runner, "_call_model") as mock_call:

        # Step 1: Baseline check has no tests (exit code 5)
        # Step 2: Reproduction test check fails (exit code 1)
        # Step 3: Fix verification succeeds (exit code 0)
        mock_verify.side_effect = [
            VerificationResult(passed=False, exit_code=5, stdout="collected 0 items", stderr="", stack="Python", command=["pytest"]),
            VerificationResult(passed=False, exit_code=1, stdout="1 failed", stderr="", stack="Python", command=["pytest"]),
            VerificationResult(passed=True, exit_code=0, stdout="1 passed", stderr="", stack="Python", command=["pytest"]),
        ]

        # Call 1: Generate reproduction test
        # Call 2: Generate fix for sum.py
        mock_call.side_effect = [
            """FILE: test_sum_reproduction.py
```python
import sum
def test_addition(): assert sum.addition(2, 3) == 5
```""",
            """FILE: sum.py
```python
def addition(a, b):
    return a + b
```""",
        ]

        diff_mock = (
            "diff --git a/sum.py b/sum.py\n"
            "+def addition(a, b):\n"
            "+    return a + b\n"
            "diff --git a/test_sum_reproduction.py b/test_sum_reproduction.py\n"
            "+import sum\n"
            "+def test_addition(): assert sum.addition(2, 3) == 5"
        )

        with patch.object(runner.git_tools, "git_diff", return_value={"has_changes": True, "diff": diff_mock}):
            success, msg = runner.run("Make addition return a + b instead of 0")

            assert success is True
            assert "Remediation Succeeded" in msg
            assert (tmp_path / "test_sum_reproduction.py").exists()


def test_write_code_pipeline(tmp_path: Path):
    """Test WRITE_CODE mode generates both implementation and tests, verified by harness."""
    runner = HarnessRunner(
        repo_path=tmp_path,
        mode=RunnerMode.WRITE_CODE,
        max_attempts=1,
    )

    with patch.object(runner.verifier, "verify") as mock_verify, \
         patch.object(runner, "_call_model") as mock_call:

        mock_verify.side_effect = [
            VerificationResult(passed=False, exit_code=5, stdout="collected 0 items", stderr="", stack="Python", command=["pytest"]),
            VerificationResult(passed=True, exit_code=0, stdout="2 passed", stderr="", stack="Python", command=["pytest"]),
        ]

        mock_call.return_value = """
FILE: math_ops.py
```python
def multiply(a, b): return a * b
```

FILE: test_math_ops.py
```python
import math_ops
def test_multiply(): assert math_ops.multiply(3, 4) == 12
```
"""

        diff_mock = (
            "diff --git a/math_ops.py b/math_ops.py\n+def multiply(a, b): return a * b\n"
            "diff --git a/test_math_ops.py b/test_math_ops.py\n+import math_ops\n+def test_multiply(): assert math_ops.multiply(3, 4) == 12"
        )

        with patch.object(runner.git_tools, "git_diff", return_value={"has_changes": True, "diff": diff_mock}):
            success, msg = runner.run("Write a multiply function with tests")

            assert success is True
            assert "Code Implementation Succeeded" in msg
            assert (tmp_path / "math_ops.py").exists()
            assert (tmp_path / "test_math_ops.py").exists()


def test_write_tests_pipeline(tmp_path: Path):
    """Test WRITE_TESTS mode generates tests without modifying application source."""
    (tmp_path / "calc.py").write_text("def subtract(a, b): return a - b", encoding="utf-8")

    runner = HarnessRunner(
        repo_path=tmp_path,
        mode=RunnerMode.WRITE_TESTS,
        max_attempts=1,
    )

    with patch.object(runner.verifier, "verify") as mock_verify, \
         patch.object(runner, "_call_model") as mock_call:

        mock_verify.side_effect = [
            VerificationResult(passed=False, exit_code=5, stdout="collected 0 items", stderr="", stack="Python", command=["pytest"]),
            VerificationResult(passed=True, exit_code=0, stdout="1 passed", stderr="", stack="Python", command=["pytest"]),
        ]

        mock_call.return_value = """
FILE: test_calc.py
```python
import calc
def test_subtract(): assert calc.subtract(10, 4) == 6
```
"""

        diff_mock = (
            "diff --git a/test_calc.py b/test_calc.py\n"
            "+import calc\n"
            "+def test_subtract(): assert calc.subtract(10, 4) == 6"
        )

        with patch.object(runner.git_tools, "git_diff", return_value={"has_changes": True, "diff": diff_mock}):
            success, msg = runner.run("Write tests for calc.py")

            assert success is True
            assert "Test Suite Creation Succeeded" in msg
            assert (tmp_path / "test_calc.py").exists()

