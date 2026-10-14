"""tests/test_publisher.py: Unit tests for GitHubPublisher engine."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

from src.harness.publisher import GitHubPublisher


def test_resolve_token_from_env(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=ghp_test_token_12345\n", encoding="utf-8")

    publisher = GitHubPublisher(env_path=env_file)
    with patch.dict("os.environ", {}, clear=True):
        token = publisher.resolve_token(interactive=False)
        assert token == "ghp_test_token_12345"


def test_save_token_to_env(tmp_path: Path):
    env_file = tmp_path / ".env"
    publisher = GitHubPublisher(env_path=env_file)

    publisher._save_token_to_env("ghp_secret_999")
    content = env_file.read_text(encoding="utf-8")
    assert "GITHUB_TOKEN=ghp_secret_999" in content

    # Test update
    publisher._save_token_to_env("ghp_secret_new")
    content = env_file.read_text(encoding="utf-8")
    assert "GITHUB_TOKEN=ghp_secret_new" in content
    assert "ghp_secret_999" not in content


def test_validate_token_success(tmp_path: Path):
    publisher = GitHubPublisher(env_path=tmp_path / ".env")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"login": "octocat", "name": "The Octocat"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        valid, username, name = publisher.validate_token("valid_token")
        assert valid is True
        assert username == "octocat"
        assert name == "The Octocat"


def test_validate_token_unauthorized(tmp_path: Path):
    publisher = GitHubPublisher(env_path=tmp_path / ".env")

    err = urllib.error.HTTPError(
        url="https://api.github.com/user",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=err):
        valid, err_msg, _ = publisher.validate_token("invalid_token")
        assert valid is False
        assert "401 Unauthorized" in err_msg


def test_create_github_repo_success(tmp_path: Path):
    publisher = GitHubPublisher(env_path=tmp_path / ".env")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "html_url": "https://github.com/octocat/my-project",
        "clone_url": "https://github.com/octocat/my-project.git",
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        success, data, msg = publisher.create_github_repo(
            token="ghp_test",
            name="my-project",
            is_private=True,
            description="Test project",
        )
        assert success is True
        assert data.get("html_url") == "https://github.com/octocat/my-project"


def test_publish_local_folder_flow(tmp_path: Path):
    local_folder = tmp_path / "my_app"
    local_folder.mkdir()
    (local_folder / "app.py").write_text("print('hello')", encoding="utf-8")

    publisher = GitHubPublisher(env_path=tmp_path / ".env")

    with patch.object(publisher, "resolve_token", return_value="ghp_mock_token"), \
         patch.object(publisher, "validate_token", return_value=(True, "testuser", "Test User")), \
         patch.object(publisher, "create_github_repo", return_value=(
             True,
             {
                 "html_url": "https://github.com/testuser/my_app",
                 "clone_url": "https://github.com/testuser/my_app.git",
             },
             "Created",
         )), \
         patch("subprocess.run") as mock_subproc:

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""
        mock_subproc.return_value = mock_proc

        success, msg, url = publisher.publish_local_folder(
            local_path=local_folder,
            repo_name="my_app",
            is_private=True,
            interactive=False,
        )

        assert success is True
        assert "successfully published" in msg
        assert url == "https://github.com/testuser/my_app"
        assert (local_folder / ".gitignore").exists()

