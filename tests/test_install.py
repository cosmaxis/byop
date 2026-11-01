"""Exercise installer prerequisite handling without installing system packages."""

import subprocess
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_installer_provisions_python_and_passes_it_to_pipx(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prefix = tmp_path / "python"
    (prefix / "bin").mkdir(parents=True)
    log = tmp_path / "calls"
    executable(bin_dir / "uname", "echo Darwin\n")
    executable(bin_dir / "git", "exit 0\n")
    executable(bin_dir / "python3", "exit 1\n")  # Python older than 3.11
    executable(
        prefix / "bin" / "python3.12",
        'if [ "$1" = --version ]; then echo "Python 3.12.0"; fi\nexit 0\n',
    )
    executable(
        bin_dir / "brew",
        'echo "brew $*" >> "$CALLS"\n'
        'if [ "$1" = --prefix ]; then echo "$PY_PREFIX"; fi\n',
    )
    executable(bin_dir / "pipx", 'echo "pipx $*" >> "$CALLS"\n')
    executable(bin_dir / "curl", 'echo \'{"tag_name":"v2.0.0"}\'\n')
    for name in ("grep", "head", "sed"):
        (bin_dir / name).symlink_to(Path("/usr/bin") / name)

    result = subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        env={
            "PATH": str(bin_dir),
            "HOME": str(tmp_path),
            "CALLS": str(log),
            "PY_PREFIX": str(prefix),
        },
        text=True,
        capture_output=True,
        check=True,
    )

    calls = log.read_text()
    assert "brew install python@3.12" in calls
    assert f"pipx install --force --python {prefix}/bin/python3.12 " in calls
    assert "Installation complete" in result.stdout


def test_installer_reuses_compatible_python(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    executable(bin_dir / "uname", "echo Darwin\n")
    executable(bin_dir / "git", "exit 0\n")
    executable(bin_dir / "python3.13", 'if [ "$1" = --version ]; then echo "Python 3.13.0"; fi\n')
    executable(bin_dir / "brew", 'echo "brew $*" >> "$CALLS"\n')
    executable(bin_dir / "pipx", 'echo "pipx $*" >> "$CALLS"\n')
    executable(bin_dir / "curl", 'echo \'{"tag_name":"v2.0.0"}\'\n')
    for name in ("grep", "head", "sed"):
        (bin_dir / name).symlink_to(Path("/usr/bin") / name)

    subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        env={"PATH": str(bin_dir), "HOME": str(tmp_path), "CALLS": str(log)},
        text=True,
        capture_output=True,
        check=True,
    )

    calls = log.read_text()
    assert "brew install" not in calls
    assert f"pipx install --force --python {bin_dir}/python3.13 " in calls


def test_installer_bootstraps_homebrew_when_python_and_brew_are_missing(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prefix = tmp_path / "python"
    (prefix / "bin").mkdir(parents=True)
    log = tmp_path / "calls"
    executable(bin_dir / "uname", "echo Darwin\n")
    executable(bin_dir / "git", "exit 0\n")
    executable(prefix / "bin" / "python3.12", "exit 0\n")
    executable(
        tmp_path / "brew-template",
        'echo "brew $*" >> "$CALLS"\n'
        'if [ "$1" = --prefix ]; then echo "$PY_PREFIX"; fi\n',
    )
    bootstrap = tmp_path / "bootstrap"
    bootstrap.write_text(
        '/bin/cp "$BREW_TEMPLATE" "$BIN_DIR/brew"\n'
        'echo "homebrew bootstrapped" >> "$CALLS"\n',
        encoding="utf-8",
    )
    executable(
        bin_dir / "curl",
        'case "$*" in *Homebrew*) /bin/cat "$BOOTSTRAP";; '
        '*) echo \'{"tag_name":"v2.0.0"}\';; esac\n',
    )
    executable(bin_dir / "pipx", 'echo "pipx $*" >> "$CALLS"\n')
    for name in ("grep", "head", "sed"):
        (bin_dir / name).symlink_to(Path("/usr/bin") / name)
    # Keep the test isolated even when Homebrew exists outside the test PATH.
    bash_env = tmp_path / "bash_env"
    bash_env.write_text(
        "command() {\n"
        '  if [[ "$1" == "-v" ]]; then\n'
        '    if [[ -x "$BIN_DIR/$2" ]]; then PATH="$BIN_DIR:$PATH"; '
        'printf "%s\\n" "$BIN_DIR/$2"; return 0; fi\n'
        '    local found; found=$(builtin command -v "$2") || return 1\n'
        '    case "$found" in /opt/homebrew/bin/*|/usr/local/bin/*) return 1;; esac\n'
        '    printf "%s\\n" "$found"\n'
        '  else builtin command "$@"; fi\n'
        "}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        env={
            "PATH": str(bin_dir),
            "HOME": str(tmp_path),
            "CALLS": str(log),
            "PY_PREFIX": str(prefix),
            "BIN_DIR": str(bin_dir),
            "BREW_TEMPLATE": str(tmp_path / "brew-template"),
            "BOOTSTRAP": str(bootstrap),
            "BASH_ENV": str(bash_env),
        },
        text=True,
        capture_output=True,
        check=True,
    )
    calls = log.read_text()
    assert "homebrew bootstrapped" in calls
    assert "brew install python@3.12" in calls
    assert f"pipx install --force --python {prefix}/bin/python3.12 " in calls
    assert "Installation complete" in result.stdout


def test_installer_installs_missing_git_and_pipx(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prefix = tmp_path / "homebrew"
    (prefix / "bin").mkdir(parents=True)
    log = tmp_path / "calls"
    executable(bin_dir / "uname", "echo Darwin\n")
    executable(bin_dir / "python3.13", 'if [ "$1" = --version ]; then echo Python; fi\n')
    # Simulate Apple's git placeholder when Command Line Tools are missing.
    executable(bin_dir / "git", "exit 1\n")
    executable(tmp_path / "git-template", "exit 0\n")
    executable(tmp_path / "pipx-template", 'echo "pipx $*" >> "$CALLS"\n')
    executable(
        bin_dir / "brew",
        'echo "brew $*" >> "$CALLS"\n'
        'case "$1" in\n'
        '  --prefix) echo "$BREW_PREFIX";;\n'
        '  install) /bin/cp "$TEMPLATES/$2-template" "$BREW_PREFIX/bin/$2";;\n'
        'esac\n',
    )
    executable(bin_dir / "curl", 'echo \'{"tag_name":"v2.0.0"}\'\n')
    for name in ("grep", "head", "sed"):
        (bin_dir / name).symlink_to(Path("/usr/bin") / name)

    result = subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        env={
            "PATH": str(bin_dir),
            "HOME": str(tmp_path),
            "CALLS": str(log),
            "BREW_PREFIX": str(prefix),
            "TEMPLATES": str(tmp_path),
        },
        text=True,
        capture_output=True,
        check=True,
    )
    calls = log.read_text()
    assert "brew install git" in calls
    assert "brew install pipx" in calls
    assert f"pipx install --force --python {bin_dir}/python3.13 " in calls
    assert "Installation complete" in result.stdout


def test_installer_reports_package_install_failure(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable(bin_dir / "uname", "echo Darwin\n")
    executable(bin_dir / "python3.13", "exit 0\n")
    executable(bin_dir / "brew", "exit 0\n")
    executable(bin_dir / "git", "exit 0\n")
    executable(
        bin_dir / "pipx",
        'if [ "$1" = install ]; then echo "dependency download failed" >&2; exit 1; fi\n',
    )
    executable(bin_dir / "curl", 'echo \'{"tag_name":"v2.0.0"}\'\n')
    for name in ("grep", "head", "sed"):
        (bin_dir / name).symlink_to(Path("/usr/bin") / name)
    result = subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "dependency download failed" in result.stderr
    assert "Installation complete" not in result.stdout
