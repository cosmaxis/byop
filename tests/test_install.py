"""Exercise the isolated macOS installer against local release fixtures."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
BASE_URL = "https://releases.example.test"
VERSION = "2.1.11"
WHEEL = f"byop-{VERSION}-py3-none-any.whl"
PAYLOAD = b"test wheel contents\n"


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps({
        "version": VERSION,
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "wheel": WHEEL,
    }))
    wheel = tmp_path / WHEEL
    pinned_manifest = tmp_path / "pinned-manifest.json"
    pinned_manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    wheel.write_bytes(PAYLOAD)
    executable(bin_dir / "uname", "echo Darwin\n")
    executable(bin_dir / "python3.13", 'exec "$TEST_PYTHON" "$@"\n')
    executable(bin_dir / "brew", 'echo "brew $*" >> "$CALLS"\n'
               'if [ "$1" = --prefix ]; then echo "$PY_PREFIX"; fi\n')
    executable(bin_dir / "pipx", 'echo "pipx $*" >> "$CALLS"\n'
               'if [ "$1" = install ]; then\n'
               '  for arg in "$@"; do wheel="$arg"; done\n'
               '  case "$wheel" in */byop-2.1.11-py3-none-any.whl) ;; *) exit 1;; esac\n'
               '  [ -f "$wheel" ] || exit 1\n'
               '  if [ "$READ_STDIN" = 1 ]; then /bin/cat >/dev/null; fi\n'
               '  if [ "$FAIL_PIPX" = 1 ]; then echo "dependency download failed" >&2; exit 1; fi\n'
               'fi\n'
               'if [ "$1" = environment ]; then echo "$BIN_LOCATION"; fi\n')
    executable(bin_dir / "git", 'echo "git $*" >> "$CALLS"; exit 1\n')
    executable(bin_dir / "gh", 'echo "gh $*" >> "$CALLS"; exit 1\n')
    executable(bin_dir / "curl", '''output=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) output="$2"; shift 2;;
        -*) shift;;
        *) url="$1"; shift;;
    esac
done
printf 'curl %s\\n' "$url" >> "$CALLS"
case "$url" in
    *Homebrew/install/HEAD/install.sh)
        /bin/cat "$BOOTSTRAP"; exit;;
    "$BYOP_BASE_URL/latest.json")
        [ "$FAIL_URL" = manifest ] && exit 22
        /bin/cp "$MANIFEST" "$output";;
    "$BYOP_BASE_URL/releases/2.1.11/manifest.json")
        [ "$FAIL_URL" = manifest ] && exit 22
        /bin/cp "$PINNED_MANIFEST" "$output";;
    "$BYOP_BASE_URL/releases/2.1.11/byop-2.1.11-py3-none-any.whl")
        [ "$FAIL_URL" = wheel ] && exit 22
        /bin/cp "$WHEEL_SOURCE" "$output";;
    *) exit 22;;
esac
if [ "$READ_STDIN" = 1 ]; then /bin/cat >/dev/null; fi
''')
    for name, target in (("mktemp", "/usr/bin/mktemp"), ("rm", "/bin/rm")):
        (bin_dir / name).symlink_to(target)
    env = {
        "PATH": str(bin_dir),
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "BYOP_BASE_URL": BASE_URL,
        "CALLS": str(calls),
        "TEST_PYTHON": sys.executable,
        "MANIFEST": str(manifest),
        "PINNED_MANIFEST": str(pinned_manifest),
        "WHEEL_SOURCE": str(wheel),
        "PY_PREFIX": str(tmp_path / "python"),
        "BOOTSTRAP": str(tmp_path / "bootstrap"),
        "BIN_LOCATION": str(tmp_path / "custom bin"),
    }
    return tmp_path, bin_dir, env


def run_installer(env, *, piped=False, pinned=None):
    script = INSTALLER.read_text(encoding="utf-8") if piped or pinned is not None else None
    if pinned is not None:
        script = script.replace('BYOP_PINNED_VERSION=""', f'BYOP_PINNED_VERSION="{pinned}"', 1)
    return subprocess.run(
        ["/bin/sh"] if script is not None else ["/bin/bash", str(INSTALLER)],
        input=script, env=env, text=True, capture_output=True, timeout=15,
    )


def assert_clean(sandbox):
    root, _, env = sandbox
    assert list(root.glob("byop.*")) == []
    calls = Path(env["CALLS"]).read_text()
    assert "git " not in calls and "gh " not in calls
    assert "api.github.com" not in calls and "github.com/Cosmaxis" not in calls
    return calls


def test_installer_verifies_and_installs_exact_wheel(sandbox):
    root, bin_dir, env = sandbox
    result = run_installer(env)
    assert result.returncode == 0, result.stderr
    calls = assert_clean(sandbox)
    assert f"curl {BASE_URL}/latest.json" in calls
    assert f"curl {BASE_URL}/releases/{VERSION}/{WHEEL}" in calls
    assert f"pipx install --force --python {bin_dir}/python3.13 " in calls
    assert calls.split("pipx install --force --python ")[1].splitlines()[0].endswith("/" + WHEEL)
    assert "brew install" not in calls
    assert "Installation complete" in result.stdout
    assert f'"{root}/custom bin/byop"' in result.stdout


def test_pinned_installer_ignores_newer_latest_release(sandbox):
    _, _, env = sandbox
    Path(env["MANIFEST"]).write_text(json.dumps({
        "version": "2.1.12",
        "sha256": hashlib.sha256(b"new release").hexdigest(),
        "wheel": "byop-2.1.12-py3-none-any.whl",
    }), encoding="utf-8")
    result = run_installer(env, pinned=VERSION)
    assert result.returncode == 0, result.stderr
    calls = assert_clean(sandbox)
    assert f"curl {BASE_URL}/releases/{VERSION}/manifest.json" in calls
    assert f"curl {BASE_URL}/latest.json" not in calls
    assert f"curl {BASE_URL}/releases/{VERSION}/{WHEEL}" in calls
    assert "pipx install --force" in calls


def test_pinned_installer_rejects_wrong_manifest_version(sandbox):
    _, _, env = sandbox
    Path(env["PINNED_MANIFEST"]).write_text(json.dumps({
        "version": "2.1.12",
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "wheel": "byop-2.1.12-py3-none-any.whl",
    }), encoding="utf-8")
    result = run_installer(env, pinned=VERSION)
    assert result.returncode != 0
    assert "Invalid release manifest" in result.stderr
    assert "pipx install" not in assert_clean(sandbox)


def test_pinned_installer_rejects_invalid_version_before_download(sandbox):
    _, _, env = sandbox
    result = run_installer(env, pinned="../2.1.11")
    assert result.returncode != 0
    assert "Invalid pinned release version" in result.stderr
    calls = assert_clean(sandbox)
    assert "curl " not in calls
    assert "pipx install" not in calls


def test_installer_provisions_python_and_passes_it_to_pipx(sandbox):
    root, bin_dir, env = sandbox
    executable(bin_dir / "python3.13", "exit 1\n")
    executable(bin_dir / "python3", "exit 1\n")
    prefix = root / "python"
    (prefix / "bin").mkdir(parents=True)
    executable(prefix / "bin" / "python3.12", 'exec "$TEST_PYTHON" "$@"\n')
    result = run_installer(env)
    assert result.returncode == 0, result.stderr
    calls = assert_clean(sandbox)
    assert "brew install python@3.12" in calls
    assert f"pipx install --force --python {prefix}/bin/python3.12 " in calls


def test_installer_bootstraps_homebrew_when_python_and_brew_are_missing(sandbox):
    root, bin_dir, env = sandbox
    (bin_dir / "brew").unlink()
    executable(bin_dir / "python3.13", "exit 1\n")
    prefix = root / "python"
    (prefix / "bin").mkdir(parents=True)
    executable(prefix / "bin" / "python3.12", 'exec "$TEST_PYTHON" "$@"\n')
    executable(root / "brew-template", 'echo "brew $*" >> "$CALLS"\n'
               'if [ "$1" = --prefix ]; then echo "$PY_PREFIX"; fi\n')
    Path(env["BOOTSTRAP"]).write_text(
        '/bin/cp "$BREW_TEMPLATE" "$BIN_DIR/brew"\n'
        'echo "homebrew bootstrapped" >> "$CALLS"\n', encoding="utf-8")
    env.update(BREW_TEMPLATE=str(root / "brew-template"), BIN_DIR=str(bin_dir))
    # The machine may have Homebrew outside the test PATH; hide it from this shell.
    bash_env = root / "bash_env"
    bash_env.write_text(
        'command() {\n'
        '  if [[ "$1" == "-v" ]]; then\n'
        '    if [[ -x "$BIN_DIR/$2" ]]; then PATH="$BIN_DIR:$PATH"; '
        'printf "%s\\n" "$BIN_DIR/$2"; return 0; fi\n'
        '    local found; found=$(builtin command -v "$2") || return 1\n'
        '    case "$found" in /opt/homebrew/bin/*|/usr/local/bin/*) return 1;; esac\n'
        '    printf "%s\\n" "$found"\n'
        '  else builtin command "$@"; fi\n'
        '}\n', encoding="utf-8")
    env["BASH_ENV"] = str(bash_env)
    result = run_installer(env)
    assert result.returncode == 0, result.stderr
    calls = assert_clean(sandbox)
    assert "homebrew bootstrapped" in calls
    assert "brew install python@3.12" in calls
    assert f"pipx install --force --python {prefix}/bin/python3.12 " in calls


def test_installer_installs_pipx_without_git(sandbox):
    root, bin_dir, env = sandbox
    (bin_dir / "pipx").unlink()
    prefix = root / "homebrew"
    (prefix / "bin").mkdir(parents=True)
    executable(root / "pipx-template", 'echo "pipx $*" >> "$CALLS"\n')
    executable(bin_dir / "brew", 'echo "brew $*" >> "$CALLS"\n'
               'case "$1" in\n'
               '  --prefix) echo "$BREW_PREFIX";;\n'
               '  install) /bin/cp "$TEMPLATES/pipx-template" "$BREW_PREFIX/bin/pipx";;\n'
               'esac\n')
    env.update(BREW_PREFIX=str(prefix), TEMPLATES=str(root))
    result = run_installer(env)
    assert result.returncode == 0, result.stderr
    calls = assert_clean(sandbox)
    assert "brew install pipx" in calls
    assert "brew install git" not in calls
    assert "pipx install --force" in calls


@pytest.mark.parametrize("manifest", [
    "", "not json", "{}", json.dumps({"version": "../other", "sha256": "0" * 64, "wheel": WHEEL}),
    json.dumps({"version": VERSION, "sha256": "F" * 64, "wheel": WHEEL}),
    json.dumps({"version": VERSION, "sha256": "0" * 64, "wheel": "other.whl"}),
    json.dumps({"version": [2, 1, 11], "sha256": "0" * 64, "wheel": WHEEL}),
])
def test_installer_refuses_invalid_manifest(sandbox, manifest):
    root, _, env = sandbox
    Path(env["MANIFEST"]).write_text(manifest, encoding="utf-8")
    result = run_installer(env)
    assert result.returncode != 0
    assert "Invalid release manifest" in result.stderr
    assert "pipx install" not in assert_clean(sandbox)


@pytest.mark.parametrize("failure", ["manifest", "wheel"])
def test_installer_refuses_network_failure(sandbox, failure):
    _, _, env = sandbox
    env["FAIL_URL"] = failure
    result = run_installer(env)
    assert result.returncode != 0
    assert "Could not download" in result.stderr
    assert "pipx install" not in assert_clean(sandbox)


def test_installer_refuses_checksum_mismatch(sandbox):
    _, _, env = sandbox
    Path(env["WHEEL_SOURCE"]).write_bytes(b"corrupt wheel")
    result = run_installer(env)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert "pipx install" not in assert_clean(sandbox)


def test_installer_reports_package_install_failure(sandbox):
    _, _, env = sandbox
    env["FAIL_PIPX"] = "1"
    result = run_installer(env)
    assert result.returncode != 0
    assert "dependency download failed" in result.stderr
    assert "pipx install failed" in result.stderr
    assert "Installation complete" not in result.stdout
    assert_clean(sandbox)


def test_piped_installer_continues_after_children_read_stdin(sandbox):
    root, bin_dir, env = sandbox
    executable(bin_dir / "python3.13", "exit 1\n")
    executable(bin_dir / "python3", "exit 1\n")
    prefix = root / "python"
    (prefix / "bin").mkdir(parents=True)
    executable(prefix / "bin" / "python3.12", 'exec "$TEST_PYTHON" "$@"\n')
    env["READ_STDIN"] = "1"
    result = run_installer(env, piped=True)
    assert result.returncode == 0, result.stderr
    calls = assert_clean(sandbox)
    assert "brew install python@3.12" in calls
    assert f"pipx install --force --python {prefix}/bin/python3.12 " in calls
    assert "[4/4] Installing byop" in result.stdout
    assert "Installation complete" in result.stdout
    assert f'"{root}/custom bin/byop"' in result.stdout
