"""tests/test_workspace_remote.py: Unit tests for remote URL parsing and push_to_remote functionality."""

from pathlib import Path
from unittest.mock import patch, MagicMock

from src.harness.workspace import _build_pr_url, WorkspaceManager


def test_build_pr_url_github():
    url1 = "https://github.com/octocat/Hello-World.git"
    assert _build_pr_url(url1, "fix-123") == "https://github.com/octocat/Hello-World/pull/new/fix-123"

    url2 = "git@github.com:octocat/Hello-World.git"
    assert _build_pr_url(url2, "fix-123") == "https://github.com/octocat/Hello-World/pull/new/fix-123"


def test_build_pr_url_gitlab():
    url = "https://gitlab.com/group/project.git"
    pr_url = _build_pr_url(url, "fix-123")
    assert "gitlab.com/group/project/-/merge_requests/new" in pr_url
    assert "fix-123" in pr_url


def test_push_to_remote_mock(tmp_path: Path):
    wm = WorkspaceManager("https://github.com/test-user/my-repo.git")
    wm.workspace_path = tmp_path
    wm.branch_name = "autofixer/remediation-test"

    with patch("subprocess.run") as mock_run:
        # Mock git add, git commit, and git push (returncode=0)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Everything up-to-date"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        success, msg, pr_url = wm.push_to_remote("Test commit")
        assert success is True
        assert "autofixer/remediation-test" in msg
        assert pr_url == "https://github.com/test-user/my-repo/pull/new/autofixer/remediation-test"

