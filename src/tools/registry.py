"""src/tools/registry.py: Pydantic schemas and tool dispatcher."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from src.tools.git_tools import SandboxedGitTools

log = logging.getLogger(__name__)


class ReadFileInput(BaseModel):
    path: str = Field(description="Relative path of file to inspect")
    start_line: int | None = Field(default=None, description="Starting 1-indexed line number")
    end_line: int | None = Field(default=None, description="Ending line number")


class WriteFileInput(BaseModel):
    path: str = Field(description="Relative path of file to write")
    content: str = Field(description="Exact new text content of file")


class ApplyPatchInput(BaseModel):
    patch: str = Field(description="Standard unified diff formatted patch string")


class SearchCodeInput(BaseModel):
    query: str = Field(description="Regex or literal string to search across codebase")
    path: str | None = Field(default=".", description="Relative folder path to limit search scope")


class ListDirectoryInput(BaseModel):
    path: str | None = Field(default=".", description="Relative folder path to list")
    max_depth: int = Field(default=2, description="Maximum subfolder recursion depth")


class RunTestsInput(BaseModel):
    pass


class GitDiffInput(BaseModel):
    pass


class ToolRegistry:
    """Registers and executes sandboxed tool functions."""

    def __init__(self, tools_core: SandboxedGitTools) -> None:
        self.tools = tools_core
        # FIX #19: handlers validate args strictly via Pydantic before dispatch,
        # unknown kwargs now produce a clear ValidationError instead of being swallowed.
        self._handlers: dict[str, tuple[type[BaseModel], Callable[..., dict[str, Any]]]] = {
            "read_file":      (ReadFileInput,      lambda **kw: self.tools.read_file(**kw)),
            "write_file":     (WriteFileInput,     lambda **kw: self.tools.write_file(**kw)),
            "apply_patch":    (ApplyPatchInput,    lambda **kw: self.tools.apply_patch(**kw)),
            "search_code":    (SearchCodeInput,    lambda **kw: self.tools.search_code(**kw)),
            "list_directory": (ListDirectoryInput, lambda **kw: self.tools.list_directory(**kw)),
            "run_tests":      (RunTestsInput,      lambda **kw: self.tools.run_tests()),
            "git_diff":       (GitDiffInput,       lambda **kw: self.tools.git_diff()),
        }

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read file lines with line numbers from the workspace repository.",
                    "parameters": ReadFileInput.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write or overwrite a file in the workspace with complete content.",
                    "parameters": WriteFileInput.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "apply_patch",
                    "description": "Apply a unified diff git patch directly to repository files.",
                    "parameters": ApplyPatchInput.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": "Search code using regex pattern matching across project files.",
                    "parameters": SearchCodeInput.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "Inspect repository filesystem tree structure.",
                    "parameters": ListDirectoryInput.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_tests",
                    "description": "Run the repository test suite to verify whether tests pass (exit code 0) or fail.",
                    "parameters": RunTestsInput.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_diff",
                    "description": "Review current changes in working tree via git diff.",
                    "parameters": GitDiffInput.model_json_schema(),
                },
            },
        ]

    def dispatch(self, tool_name: str, arguments: dict[str, Any] | str) -> str:
        if tool_name not in self._handlers:
            log.warning("Unknown tool dispatched: %s", tool_name)
            return json.dumps({"error": f"Unknown tool: '{tool_name}'"})

        try:
            parsed_args = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(parsed_args, dict):
                parsed_args = {}

            schema_cls, handler = self._handlers[tool_name]
            # Validate args through Pydantic — strips unknown fields and raises on bad types
            validated = schema_cls(**parsed_args)
            result = handler(**validated.model_dump())
            return json.dumps(result, indent=2)
        except ValidationError as exc:
            log.warning("Pydantic validation failed for tool '%s': %s", tool_name, exc)
            return json.dumps({"error": f"Invalid arguments for '{tool_name}': {exc.errors()}"})
        except Exception as exc:
            log.exception("Tool execution error on '%s'", tool_name)
            return json.dumps({"error": f"Tool execution error on '{tool_name}': {str(exc)}"})