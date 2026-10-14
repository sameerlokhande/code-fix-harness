"""Security, Secret, and Path Sandboxing Interceptor.

Prevents prompt injection of raw tokens, strips secrets from feedback loops,
and guarantees directory containment across all tool executions.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
from typing import Any

log = logging.getLogger(__name__)


class PathSandboxViolation(PermissionError):
    """Raised when an agent attempts an escape path traversal out of the workspace."""


class SecurityInterceptor:
    """Manages secret isolation, credential scrubbing, and file system boundaries."""

    # FIX #11: expanded to cover Anthropic, Google AI, JWT, and env-style secrets
    SECRET_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"ghp_[A-Za-z0-9_]{36,255}"),            # GitHub Personal Access Token
        re.compile(r"github_pat_[A-Za-z0-9_]{82}"),         # GitHub Fine-Grained Token
        re.compile(r"sk-[A-Za-z0-9]{32,64}"),               # OpenAI / Generic API Key
        re.compile(r"sk-ant-[A-Za-z0-9\-_]{40,}"),          # Anthropic API Key
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),              # Google AI / Firebase API Key
        re.compile(r"AKIA[0-9A-Z]{16}"),                    # AWS Access Key ID
        re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.=:_\+\/]{20,}"),  # Bearer Header
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT token
        re.compile(r'(?i)(?:password|passwd|secret|api_key|token)\s*=\s*["\']?[^\s"\']{8,}["\']?'),  # Key=value patterns
    ]

    def __init__(self, workspace_root: Path, managed_secrets: dict[str, str] | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self._managed_secrets = managed_secrets or {}

    def assert_path_contained(self, target_path: str | Path) -> Path:
        """Validate that target path resolves strictly inside workspace_root."""
        raw_path = Path(target_path)
        resolved = (
            raw_path.resolve()
            if raw_path.is_absolute()
            else (self.workspace_root / raw_path).resolve()
        )

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as err:
            log.warning("Path sandbox violation attempt: %s -> %s", target_path, resolved)
            raise PathSandboxViolation(
                f"Security Sandbox Violation: Path '{target_path}' resolves to '{resolved}', "
                f"outside workspace root '{self.workspace_root}'."
            ) from err

        return resolved

    def sanitize_text(self, text: Any) -> str:
        """Redact known credentials and high-entropy secret patterns from outputs."""
        if not isinstance(text, str):
            sanitized = json.dumps(text) if isinstance(text, (dict, list)) else str(text)
        else:
            sanitized = text
        for secret_val in self._managed_secrets.values():
            if secret_val and len(secret_val) > 4:
                sanitized = sanitized.replace(secret_val, "[REDACTED_SECRET]")

        for pattern in self.SECRET_PATTERNS:
            sanitized = pattern.sub("[REDACTED_CREDENTIAL]", sanitized)

        return sanitized

    def inject_environment(self, base_env: dict[str, str] | None = None) -> dict[str, str]:
        """Inject runtime secrets into subprocess environments without exposing them to the LLM."""
        env = (base_env or os.environ).copy()
        env.update(self._managed_secrets)
        return env