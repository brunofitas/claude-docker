import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from claude_docker.cli import (
    CONTAINER_HOME,
    CREDENTIAL_DIRS,
    DEFAULT_PERMISSION_MODE,
    IMAGE_NAME,
    PERMISSION_MODES,
    _get_token_linux,
    _get_token_macos,
    _get_token_windows,
    build_image,
    get_claude_dir,
    get_claude_json_path,
    get_credential_mounts,
    get_oauth_token,
    parse_args,
    prepare_claude_json,
)

FAKE_CREDS = json.dumps(
    {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-fake-token",
            "refreshToken": "sk-ant-ort01-fake-refresh",
            "expiresAt": 9999999999999,
        }
    }
)


# --- parse_args ---


class TestParseArgs:
    def test_defaults(self):
        args, remaining = parse_args([])
        assert args.permission_mode == DEFAULT_PERMISSION_MODE
        assert args.build is False
        assert remaining == []

    def test_permission_mode(self):
        for mode in PERMISSION_MODES:
            args, _ = parse_args(["--permission-mode", mode])
            assert args.permission_mode == mode

    def test_invalid_permission_mode(self):
        with pytest.raises(SystemExit):
            parse_args(["--permission-mode", "invalid"])

    def test_build_flag(self):
        args, _ = parse_args(["--build"])
        assert args.build is True

    def test_remaining_args_passed_through(self):
        args, remaining = parse_args(["-p", "hello world"])
        assert remaining == ["-p", "hello world"]

    def test_mixed_args(self):
        args, remaining = parse_args(["--permission-mode", "plan", "--build", "-p", "test"])
        assert args.permission_mode == "plan"
        assert args.build is True
        assert remaining == ["-p", "test"]

    def test_network_host_default_true(self):
        args, _ = parse_args([])
        assert args.network_host is True

    def test_no_network_host_flag(self):
        args, _ = parse_args(["--no-network-host"])
        assert args.network_host is False

    def test_no_mount_creds_flag(self):
        args, _ = parse_args(["--no-mount-creds"])
        assert args.no_mount_creds is True

    def test_no_mount_creds_default_false(self):
        args, _ = parse_args([])
        assert args.no_mount_creds is False


# --- get_credential_mounts ---


class TestGetCredentialMounts:
    def test_returns_existing_dirs(self, tmp_path):
        # Create fake credential dirs
        gh_dir = tmp_path / ".config" / "gh"
        gh_dir.mkdir(parents=True)
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()

        with mock.patch("claude_docker.cli.Path.home", return_value=tmp_path):
            mounts = get_credential_mounts()

        host_paths = [m[0] for m in mounts]
        assert str(gh_dir) in host_paths
        assert str(aws_dir) in host_paths

    def test_skips_missing_dirs(self, tmp_path):
        # No credential dirs exist
        with mock.patch("claude_docker.cli.Path.home", return_value=tmp_path):
            mounts = get_credential_mounts()
        assert mounts == []

    def test_only_returns_dirs_not_files(self, tmp_path):
        # Create a file where a dir is expected
        aws_path = tmp_path / ".aws"
        aws_path.write_text("not a dir")

        with mock.patch("claude_docker.cli.Path.home", return_value=tmp_path):
            mounts = get_credential_mounts()
        assert mounts == []

    def test_container_paths_match_credential_dirs(self, tmp_path):
        # Create all credential dirs
        for rel_path, _ in CREDENTIAL_DIRS:
            (tmp_path / rel_path).mkdir(parents=True, exist_ok=True)

        with mock.patch("claude_docker.cli.Path.home", return_value=tmp_path):
            mounts = get_credential_mounts()

        container_paths = [m[1] for m in mounts]
        expected = [cp for _, cp in CREDENTIAL_DIRS]
        assert container_paths == expected


# --- prepare_claude_json ---


class TestPrepareClaudeJson:
    def test_patches_install_method(self, tmp_path):
        src = tmp_path / ".claude.json"
        src.write_text(json.dumps({"installMethod": "native", "foo": "bar"}))

        patched = prepare_claude_json(str(src))
        assert patched is not None

        with open(patched) as f:
            data = json.load(f)

        assert data["installMethod"] == "npm"
        assert data["foo"] == "bar"
        os.unlink(patched)

    def test_preserves_other_fields(self, tmp_path):
        src = tmp_path / ".claude.json"
        src.write_text(json.dumps({"userID": "123", "numStartups": 42}))

        patched = prepare_claude_json(str(src))
        with open(patched) as f:
            data = json.load(f)

        assert data["userID"] == "123"
        assert data["numStartups"] == 42
        os.unlink(patched)

    def test_returns_none_for_invalid_json(self, tmp_path):
        src = tmp_path / ".claude.json"
        src.write_text("not valid json")

        assert prepare_claude_json(str(src)) is None

    def test_returns_none_for_missing_file(self):
        assert prepare_claude_json("/nonexistent/path.json") is None


# --- get_oauth_token ---


class TestGetOauthToken:
    def test_env_var_takes_priority(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "env-token"}):
            assert get_oauth_token() == "env-token"

    def test_macos_dispatch(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("claude_docker.cli.platform.system", return_value="Darwin"),
            mock.patch("claude_docker.cli._get_token_macos", return_value="mac-tok"),
        ):
            assert get_oauth_token() == "mac-tok"

    def test_linux_dispatch(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("claude_docker.cli.platform.system", return_value="Linux"),
            mock.patch("claude_docker.cli._get_token_linux", return_value="lin-tok"),
        ):
            assert get_oauth_token() == "lin-tok"

    def test_windows_dispatch(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("claude_docker.cli.platform.system", return_value="Windows"),
            mock.patch("claude_docker.cli._get_token_windows", return_value="win-tok"),
        ):
            assert get_oauth_token() == "win-tok"

    def test_unknown_platform_returns_none(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("claude_docker.cli.platform.system", return_value="FreeBSD"),
        ):
            assert get_oauth_token() is None


# --- Platform token extractors ---


class TestGetTokenMacos:
    def test_extracts_token(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=FAKE_CREDS)
        with mock.patch("claude_docker.cli.subprocess.run", return_value=result):
            assert _get_token_macos() == "sk-ant-oat01-fake-token"

    def test_returns_none_on_failure(self):
        result = subprocess.CompletedProcess(args=[], returncode=44, stdout="")
        with mock.patch("claude_docker.cli.subprocess.run", return_value=result):
            assert _get_token_macos() is None

    def test_returns_none_on_missing_key(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps({"other": "data"}))
        with mock.patch("claude_docker.cli.subprocess.run", return_value=result):
            assert _get_token_macos() is None

    def test_returns_none_when_binary_not_found(self):
        with mock.patch("claude_docker.cli.subprocess.run", side_effect=FileNotFoundError):
            assert _get_token_macos() is None


class TestGetTokenLinux:
    def test_extracts_token(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=FAKE_CREDS)
        with mock.patch("claude_docker.cli.subprocess.run", return_value=result):
            assert _get_token_linux() == "sk-ant-oat01-fake-token"

    def test_returns_none_on_empty_output(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        with mock.patch("claude_docker.cli.subprocess.run", return_value=result):
            assert _get_token_linux() is None

    def test_returns_none_when_secret_tool_missing(self):
        with mock.patch("claude_docker.cli.subprocess.run", side_effect=FileNotFoundError):
            assert _get_token_linux() is None


class TestGetTokenWindows:
    def test_extracts_token(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=FAKE_CREDS)
        with mock.patch("claude_docker.cli.subprocess.run", return_value=result):
            assert _get_token_windows() == "sk-ant-oat01-fake-token"

    def test_returns_none_on_failure(self):
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        with mock.patch("claude_docker.cli.subprocess.run", return_value=result):
            assert _get_token_windows() is None


# --- Platform paths ---


class TestPlatformPaths:
    def test_claude_json_unix(self):
        with mock.patch("claude_docker.cli.platform.system", return_value="Darwin"):
            assert get_claude_json_path() == Path.home() / ".claude.json"

    def test_claude_json_windows_with_appdata(self):
        with (
            mock.patch("claude_docker.cli.platform.system", return_value="Windows"),
            mock.patch.dict(os.environ, {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}),
        ):
            assert get_claude_json_path() == Path("C:\\Users\\test\\AppData\\Roaming") / "claude.json"

    def test_claude_json_windows_no_appdata(self):
        with (
            mock.patch("claude_docker.cli.platform.system", return_value="Windows"),
            mock.patch.dict(os.environ, {"APPDATA": ""}, clear=False),
        ):
            assert get_claude_json_path() == Path.home() / ".claude.json"

    def test_claude_dir_unix(self):
        with mock.patch("claude_docker.cli.platform.system", return_value="Linux"):
            assert get_claude_dir() == Path.home() / ".claude"

    def test_claude_dir_windows_with_appdata(self):
        with (
            mock.patch("claude_docker.cli.platform.system", return_value="Windows"),
            mock.patch.dict(os.environ, {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}),
        ):
            assert get_claude_dir() == Path("C:\\Users\\test\\AppData\\Roaming") / "claude"


# --- build_image ---


class TestBuildImage:
    def test_includes_uid_gid_on_unix(self):
        with (
            mock.patch("claude_docker.cli.platform.system", return_value="Linux"),
            mock.patch("claude_docker.cli.subprocess.run") as mock_run,
        ):
            build_image()
            cmd = mock_run.call_args[0][0]
            assert "--build-arg" in cmd
            assert any(arg.startswith("USER_UID=") for arg in cmd)
            assert any(arg.startswith("USER_GID=") for arg in cmd)

    def test_skips_uid_gid_on_windows(self):
        with (
            mock.patch("claude_docker.cli.platform.system", return_value="Windows"),
            mock.patch("claude_docker.cli.subprocess.run") as mock_run,
        ):
            build_image()
            cmd = mock_run.call_args[0][0]
            assert not any(arg.startswith("USER_UID=") for arg in cmd)
            assert not any(arg.startswith("USER_GID=") for arg in cmd)

    def test_uses_correct_image_name(self):
        with (
            mock.patch("claude_docker.cli.platform.system", return_value="Darwin"),
            mock.patch("claude_docker.cli.subprocess.run") as mock_run,
        ):
            build_image()
            cmd = mock_run.call_args[0][0]
            assert IMAGE_NAME in cmd


# --- main (docker command assembly) ---


class TestMain:
    def _run_main(self, *, claude_dir_exists=True, claude_json_exists=True, token="fake-token", argv=None):
        """Helper that captures the docker command main() would run."""
        captured_cmd = []

        def fake_run_docker(cmd, patched_json=None):
            captured_cmd.extend(cmd)

        fake_claude_dir = mock.MagicMock(spec=Path)
        fake_claude_dir.exists.return_value = claude_dir_exists
        fake_claude_dir.__str__ = lambda self: "/fake/.claude"
        fake_claude_dir.__fspath__ = lambda self: "/fake/.claude"

        fake_claude_json = mock.MagicMock(spec=Path)
        fake_claude_json.exists.return_value = claude_json_exists
        fake_claude_json.__str__ = lambda self: "/fake/.claude.json"
        fake_claude_json.__fspath__ = lambda self: "/fake/.claude.json"

        with (
            mock.patch("claude_docker.cli.get_claude_dir", return_value=fake_claude_dir),
            mock.patch("claude_docker.cli.get_claude_json_path", return_value=fake_claude_json),
            mock.patch("claude_docker.cli.get_oauth_token", return_value=token),
            mock.patch("claude_docker.cli.run_docker", side_effect=fake_run_docker),
            mock.patch("claude_docker.cli.subprocess.run") as mock_inspect,
            mock.patch(
                "claude_docker.cli.prepare_claude_json",
                return_value="/tmp/patched.json" if claude_json_exists else None,
            ),
            mock.patch("sys.argv", ["claude-docker"] + (argv or [])),
        ):
            mock_inspect.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            from claude_docker.cli import main

            main()

        return captured_cmd

    def test_mounts_workspace_as_cwd(self):
        cmd = self._run_main()
        cwd = os.getcwd()
        idx = cmd.index(f"{cwd}:/workspace")
        assert cmd[idx - 1] == "-v"

    def test_mounts_claude_dir_when_exists(self):
        cmd = self._run_main(claude_dir_exists=True)
        assert f"/fake/.claude:{CONTAINER_HOME}/.claude" in cmd

    def test_skips_claude_dir_when_missing(self):
        cmd = self._run_main(claude_dir_exists=False)
        assert not any(CONTAINER_HOME + "/.claude" in arg for arg in cmd if "/.claude.json" not in arg)

    def test_passes_oauth_token(self):
        cmd = self._run_main(token="my-secret-token")
        idx = cmd.index("CLAUDE_CODE_OAUTH_TOKEN=my-secret-token")
        assert cmd[idx - 1] == "-e"

    def test_no_token_env_when_none(self):
        cmd = self._run_main(token=None)
        assert not any("CLAUDE_CODE_OAUTH_TOKEN" in arg for arg in cmd)

    def test_passes_extra_args(self):
        cmd = self._run_main(argv=["-p", "hello world"])
        assert "-p" in cmd
        assert "hello world" in cmd

    def test_image_name_in_command(self):
        cmd = self._run_main()
        assert IMAGE_NAME in cmd

    def test_default_permission_mode(self):
        cmd = self._run_main()
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == DEFAULT_PERMISSION_MODE

    def test_custom_permission_mode(self):
        cmd = self._run_main(argv=["--permission-mode", "plan"])
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "plan"

    def test_all_permission_modes_accepted(self):
        for mode in PERMISSION_MODES:
            cmd = self._run_main(argv=["--permission-mode", mode])
            idx = cmd.index("--permission-mode")
            assert cmd[idx + 1] == mode

    def test_network_host_enabled_by_default(self):
        cmd = self._run_main()
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "host"

    def test_no_network_host_disables_it(self):
        cmd = self._run_main(argv=["--no-network-host"])
        assert "--network" not in cmd

    def test_credential_mounts_added_by_default(self):
        fake_mounts = [("/home/user/.config/gh", f"{CONTAINER_HOME}/.config/gh")]
        with mock.patch("claude_docker.cli.get_credential_mounts", return_value=fake_mounts):
            cmd = self._run_main()
        assert f"/home/user/.config/gh:{CONTAINER_HOME}/.config/gh:ro" in cmd

    def test_no_mount_creds_skips_credential_mounts(self):
        fake_mounts = [("/home/user/.config/gh", f"{CONTAINER_HOME}/.config/gh")]
        with mock.patch("claude_docker.cli.get_credential_mounts", return_value=fake_mounts):
            cmd = self._run_main(argv=["--no-mount-creds"])
        assert not any(".config/gh" in arg for arg in cmd)

    def test_build_flag_triggers_build(self):
        with (
            mock.patch("claude_docker.cli.get_claude_dir") as mock_dir,
            mock.patch("claude_docker.cli.get_claude_json_path") as mock_json,
            mock.patch("claude_docker.cli.get_oauth_token", return_value="tok"),
            mock.patch("claude_docker.cli.run_docker"),
            mock.patch("claude_docker.cli.subprocess.run") as mock_run,
            mock.patch("claude_docker.cli.build_image") as mock_build,
            mock.patch("claude_docker.cli.prepare_claude_json", return_value=None),
            mock.patch("sys.argv", ["claude-docker", "--build"]),
        ):
            mock_dir.return_value = mock.MagicMock(exists=mock.Mock(return_value=False))
            mock_json.return_value = mock.MagicMock(exists=mock.Mock(return_value=False))
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            from claude_docker.cli import main

            main()
            mock_build.assert_called_once()
