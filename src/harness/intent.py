"""src/harness/intent.py: Lightweight intent classifier using a small local model."""

from __future__ import annotations

from enum import Enum
import logging
import ollama

log = logging.getLogger(__name__)

# Preferred classifier models from smallest to largest
_PREFERRED_CLASSIFIER_MODELS = [
    "qwen2.5:0.5b",
    "qwen2.5:1.5b",
    "qwen2.5:3b",
    "llama3.2:3b",
    "qwen2.5-coder:7b",
    "llama3.1:8b",
]


class RunnerMode(str, Enum):
    FIX = "fix"
    WRITE_CODE = "write-code"
    WRITE_TESTS = "write-tests"


_CLASSIFICATION_PROMPT = """You are an intent classification assistant for an autonomous software engineering harness.
Given a user's objective or prompt, classify it into exactly one of these 3 modes:

- fix: Fixing bugs, troubleshooting broken code, resolving test failures, making existing features work correctly.
- write-code: Implementing a new module, creating new functions, building features from scratch.
- write-tests: Writing unit/integration tests for existing code, increasing test coverage, creating test suites.

Objective:
{objective}

Reply with ONLY ONE word: 'fix', 'write-code', or 'write-tests'.
"""


def _resolve_available_model(preferred_model: str | None = None) -> str | None:
    """Find the best available local model for intent classification."""
    try:
        res = ollama.list()
        installed_models = []
        if hasattr(res, "models"):
            installed_models = [m.model for m in res.models]
        elif isinstance(res, dict) and "models" in res:
            installed_models = [m.get("name", "") for m in res["models"]]

        # Clean model names (strip :latest tag if needed for matching)
        installed_set = set(installed_models)
        installed_base = {m.split(":")[0]: m for m in installed_models}

        if preferred_model and (preferred_model in installed_set or preferred_model in installed_base):
            return preferred_model if preferred_model in installed_set else installed_base[preferred_model]

        # Search in preferred order
        for candidate in _PREFERRED_CLASSIFIER_MODELS:
            candidate_base = candidate.split(":")[0]
            if candidate in installed_set:
                return candidate
            if candidate_base in installed_base and not candidate_base.startswith("nomic"):
                return installed_base[candidate_base]

        # Fallback to any non-embedding model
        for m in installed_models:
            if not m.startswith("nomic") and not "embed" in m:
                return m
    except Exception as exc:
        log.warning("Could not list local Ollama models: %s", exc)

    return preferred_model or "qwen2.5:1.5b"


def classify_intent(objective: str, model_name: str | None = None, use_llm: bool = False) -> RunnerMode:
    """Classify user objective instantly using fast keyword heuristics, with optional LLM fallback."""
    if not objective or not objective.strip():
        return RunnerMode.FIX

    cleaned = objective.strip().lower()

    # Fast test-related keywords
    test_keywords = [
        "test", "tests", "testing", "pytest", "unit test", "integration test",
        "coverage", "assert", "mock", "reproduction"
    ]
    # Fast code creation keywords
    code_keywords = [
        "create", "implement", "build", "write code", "generate", "feature",
        "new function", "new class", "new module", "add endpoint", "develop"
    ]
    # Fast fix keywords
    fix_keywords = [
        "fix", "bug", "error", "exception", "crash", "traceback", "broken",
        "repair", "failing", "remediation", "resolve", "patch", "issue"
    ]

    # If prompt specifically asks to write tests
    if any(k in cleaned for k in ["write test", "write tests", "add test", "add tests", "create test", "test coverage"]):
        return RunnerMode.WRITE_TESTS

    # Check matches
    has_test = any(k in cleaned for k in test_keywords)
    has_code = any(k in cleaned for k in code_keywords)
    has_fix = any(k in cleaned for k in fix_keywords)

    if has_fix:
        return RunnerMode.FIX
    if has_test and not has_code:
        return RunnerMode.WRITE_TESTS
    if has_code and not has_fix:
        return RunnerMode.WRITE_CODE

    # If LLM classification wasn't explicitly requested, return FIX instantly without swapping models
    if not use_llm:
        return RunnerMode.FIX

    resolved_model = _resolve_available_model(model_name)
    if not resolved_model:
        return RunnerMode.FIX

    try:
        response = ollama.chat(
            model=resolved_model,
            messages=[{"role": "user", "content": _CLASSIFICATION_PROMPT.format(objective=objective)}],
            options={"temperature": 0.0},
        )
        content = response.get("message", {}).get("content", "").strip().lower()
        if "write-test" in content or "test" in content:
            return RunnerMode.WRITE_TESTS
        elif "write-code" in content or "code" in content:
            return RunnerMode.WRITE_CODE
        return RunnerMode.FIX
    except Exception:
        return RunnerMode.FIX
