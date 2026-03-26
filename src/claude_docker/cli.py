import argparse
import contextlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

IMAGE_NAME = "claude-docker:latest"
CONTAINER_USER = "claude"
CONTAINER_HOME = f"/home/{CONTAINER_USER}"

PERMISSION_MODES = [
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
]

DEFAULT_PERMISSION_MODE = "bypassPermissions"

# Host credential directories to auto-mount (host_path_relative_to_home, container_path)
CREDENTIAL_DIRS = [
    (".config/gh", f"{CONTAINER_HOME}/.config/gh"),  # GitHub CLI
    (".aws", f"{CONTAINER_HOME}/.aws"),  # AWS CLI
    (".azure", f"{CONTAINER_HOME}/.azure"),  # Azure CLI
    (".config/gcloud", f"{CONTAINER_HOME}/.config/gcloud"),  # Google Cloud SDK
]


def build_image():
    dockerfile = Path(__file__).parent / "Dockerfile"
    cmd = [
        "docker",
        "build",
        "-t",
        IMAGE_NAME,
        "-f",
        str(dockerfile),
    ]

    # Pass UID/GID on Unix so container user matches host file ownership
    if platform.system() != "Windows":
        cmd += [
            "--build-arg",
            f"USER_UID={os.getuid()}",
            "--build-arg",
            f"USER_GID={os.getgid()}",
        ]

    cmd.append(str(dockerfile.parent))
    subprocess.run(cmd, check=True)


def _get_token_macos():
    """Extract OAuth token from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            creds = json.loads(result.stdout.strip())
            return creds.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        pass
    return None


def _get_token_linux():
    """Extract OAuth token from libsecret (GNOME Keyring / KDE Wallet)."""
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", "Claude Code-credentials"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            creds = json.loads(result.stdout.strip())
            return creds.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        pass
    return None


def _get_token_windows():
    """Extract OAuth token from Windows Credential Manager."""
    try:
        ps_cmd = (
            'powershell -Command "'
            "[System.Net.NetworkCredential]::new('', "
            "(Get-StoredCredential -Target 'Claude Code-credentials').Password"
            ').Password"'
        )
        result = subprocess.run(ps_cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0 and result.stdout.strip():
            creds = json.loads(result.stdout.strip())
            return creds.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        pass
    return None


def get_oauth_token():
    """Extract Claude OAuth token from the platform's credential store."""
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return env_token

    system = platform.system()
    if system == "Darwin":
        return _get_token_macos()
    elif system == "Linux":
        return _get_token_linux()
    elif system == "Windows":
        return _get_token_windows()
    return None


def get_credential_mounts():
    """Detect existing host credential directories and return mount pairs."""
    mounts = []
    home = Path.home()
    for rel_path, container_path in CREDENTIAL_DIRS:
        host_path = home / rel_path
        if host_path.is_dir():
            mounts.append((str(host_path), container_path))
    return mounts


def get_claude_json_path():
    """Get the path to .claude.json based on platform."""
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "claude.json"
        return Path.home() / ".claude.json"
    return Path.home() / ".claude.json"


def get_claude_dir():
    """Get the path to .claude directory based on platform."""
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "claude"
        return Path.home() / ".claude"
    return Path.home() / ".claude"


def prepare_claude_json(source_path):
    """Create a patched copy of .claude.json with installMethod set to npm."""
    try:
        with open(source_path) as f:
            config = json.load(f)
        config["installMethod"] = "npm"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(config, tmp)
        return tmp.name
    except (json.JSONDecodeError, OSError):
        return None


def run_docker(cmd, patched_json=None):
    """Run docker and clean up temp files. Works on all platforms."""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(cmd)
            sys.exit(result.returncode)
        else:
            if patched_json:
                pid = os.fork()
                if pid == 0:
                    os.execvp("docker", cmd)
                else:
                    _, status = os.waitpid(pid, 0)
                    sys.exit(os.waitstatus_to_exitcode(status))
            else:
                os.execvp("docker", cmd)
    finally:
        if patched_json:
            with contextlib.suppress(OSError):
                os.unlink(patched_json)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="claude-docker",
        description="Run Claude Code inside a Docker container.",
    )
    parser.add_argument(
        "--permission-mode",
        choices=PERMISSION_MODES,
        default=DEFAULT_PERMISSION_MODE,
        help=f"Claude permission mode (default: {DEFAULT_PERMISSION_MODE})",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Force rebuild the Docker image",
    )
    parser.add_argument(
        "--network-host",
        action="store_true",
        help="Run container with --network host (enables OAuth browser callbacks)",
    )
    parser.add_argument(
        "--no-mount-creds",
        action="store_true",
        help="Disable auto-mounting of host credential directories",
    )

    args, remaining = parser.parse_known_args(argv)
    return args, remaining


def main():
    args, remaining = parse_args()

    cwd = os.getcwd()
    claude_dir = get_claude_dir()
    claude_json = get_claude_json_path()

    result = subprocess.run(
        ["docker", "image", "inspect", IMAGE_NAME],
        capture_output=True,
    )
    if result.returncode != 0 or args.build:
        print("Building claude-docker image...")
        build_image()

    cmd = [
        "docker",
        "run",
        "--rm",
        "-it",
    ]

    if args.network_host:
        cmd += ["--network", "host"]

    cmd += ["-v", f"{cwd}:/workspace"]

    if claude_dir.exists():
        cmd += ["-v", f"{claude_dir}:{CONTAINER_HOME}/.claude"]

    if not args.no_mount_creds:
        for host_path, container_path in get_credential_mounts():
            cmd += ["-v", f"{host_path}:{container_path}:ro"]

    patched_json = None
    if claude_json.exists():
        patched_json = prepare_claude_json(claude_json)
        if patched_json:
            cmd += ["-v", f"{patched_json}:{CONTAINER_HOME}/.claude.json"]

    token = get_oauth_token()
    if token:
        cmd += ["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"]
    else:
        print(
            "Warning: Could not find Claude OAuth token.\n"
            "  You can either:\n"
            "  - Run /login inside the container\n"
            "  - Set CLAUDE_CODE_OAUTH_TOKEN environment variable\n"
        )

    cmd += [IMAGE_NAME]
    cmd += ["--permission-mode", args.permission_mode]
    cmd += remaining

    run_docker(cmd, patched_json)
