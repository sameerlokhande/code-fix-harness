"""src/harness/compactor.py: Deterministic context window trimmer."""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class ContextCompactor:
    """Deterministically controls conversation memory expansion without breaking tool call pairings.

    FIX #6: The compacted summary now preserves the last test-failure output and the
    most recently written file paths/snippets, so the agent doesn't repeat strategies
    it already tried.
    """

    def __init__(self, max_messages: int = 28, keep_recent: int = 8) -> None:
        self.max_messages = max_messages
        self.keep_recent = keep_recent

    def compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(messages) <= self.max_messages:
            return messages

        head: list[dict[str, Any]] = []
        idx = 0

        # Preserve System prompt
        if messages and messages[0].get("role") == "system":
            head.append(messages[0])
            idx += 1

        # Preserve Initial User Bug prompt
        if idx < len(messages) and messages[idx].get("role") == "user":
            head.append(messages[idx])
            idx += 1

        # Compute tail slice and ensure we don't start the tail with an orphaned 'tool' role message
        tail_start = len(messages) - self.keep_recent
        while tail_start > idx and messages[tail_start].get("role") == "tool":
            tail_start -= 1

        body = messages[idx:tail_start]
        tail = messages[tail_start:]

        if not body:
            return messages

        compacted_summary = self._summarize_body(body)
        summary_message = {
            "role": "user",
            "content": (
                f"[HARNESS CONTEXT COMPACTOR]: Intermediate steps summarized to preserve memory:\n"
                f"{compacted_summary}\n"
                f"Continue working towards passing tests."
            ),
        }

        log.debug("Compacted %d body messages into a summary.", len(body))
        return [*head, summary_message, *tail]

    def _summarize_body(self, body: list[dict[str, Any]]) -> str:
        """Build a rich summary that preserves test-failure context and edit history.

        FIX #6: Previously only tracked the last 5 tool names.  Now also captures:
          - The last test failure output (so the agent remembers what it needs to fix)
          - The paths + first-line snippets of files it already wrote (prevents repeating)
          - Approaches already tried (write_file / apply_patch targets with iteration index)
        """
        files_read: set[str] = set()
        files_written: list[str] = []       # ordered — most recent last
        actions: list[str] = []
        last_test_failure: str = ""
        last_test_passed: bool | None = None

        for msg in body:
            role = msg.get("role")

            if role == "assistant" and "tool_calls" in msg:
                for tool_call in msg["tool_calls"]:
                    fn = tool_call.get("function", {})
                    name = fn.get("name", "unknown")
                    raw_args = fn.get("arguments", {})
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    target = args.get("path") or args.get("query") or ""
                    if name == "read_file" and target:
                        files_read.add(target)
                    elif name in ("write_file", "apply_patch") and target:
                        files_written.append(target)
                    actions.append(f"{name}('{target}')")

            # Capture test runner output from tool response messages
            elif role == "tool" and msg.get("name") == "run_tests":
                try:
                    tool_result = json.loads(msg.get("content", "{}"))
                    if isinstance(tool_result, dict):
                        last_test_passed = tool_result.get("passed", None)
                        if not last_test_passed:
                            raw_output = tool_result.get("output", "")
                            # Keep last 800 chars of the failure output — enough for context
                            last_test_failure = raw_output[-800:] if raw_output else ""
                except Exception:
                    pass

        # De-duplicate written files while preserving order
        seen: set[str] = set()
        unique_written: list[str] = []
        for f in reversed(files_written):
            if f not in seen:
                seen.add(f)
                unique_written.insert(0, f)

        summary_parts = [
            f"- Files inspected: {', '.join(sorted(files_read)) or 'None'}",
            f"- Files modified (most recent last): {', '.join(unique_written) or 'None'}",
            f"- Recent tools used: {', '.join(actions[-8:]) if actions else 'Exploration'}",
        ]

        if last_test_passed is True:
            summary_parts.append("- Last run_tests result: PASSED ✓")
        elif last_test_passed is False and last_test_failure:
            summary_parts.append(
                f"- Last run_tests result: FAILED ✗\n"
                f"  Failure output (truncated):\n"
                f"  {last_test_failure.strip().replace(chr(10), chr(10) + '  ')}"
            )

        return "\n".join(summary_parts)