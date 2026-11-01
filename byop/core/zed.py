"""Zed installation and upgrade management.

Supports two install strategies on macOS:

* Homebrew cask (preferred, enables clean upgrades via ``brew upgrade``).
* Direct download from GitHub releases (fallback when brew is missing).

The cask is marked ``auto_updates`` by Homebrew, so ``brew upgrade --cask zed``
is the supported way to fetch the latest stable build.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

APPLICATIONS = Path("/Applications")
ZED_APP = APPLICATIONS / "Zed.app"
ZED_CLI = Path("/usr/local/bin/zed")


@dataclass
class ZedStatus:
    installed: bool
    version: str | None
    via_brew: bool


def _run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=False,
    )


def _bundle_version() -> str | None:
    if not ZED_APP.exists():
        return None
    res = _run(
        ["defaults", "read", str(ZED_APP / "Contents/Info.plist"),
         "CFBundleShortVersionString"]
    )
    if res.returncode == 0:
        return res.stdout.strip() or None
    return None


def has_brew() -> bool:
    return shutil.which("brew") is not None


def is_installed() -> bool:
    return ZED_APP.exists() or (Path.home() / "Applications" / "Zed.app").exists() or shutil.which("zed") is not None


def installed_version() -> str | None:
    return _bundle_version()


def brew_installed() -> bool:
    """Best-effort detection of whether Zed was installed via Homebrew."""

    if not has_brew():
        return False
    res = _run(["brew", "list", "--cask", "zed"], capture=True)
    return res.returncode == 0


def status() -> ZedStatus:
    return ZedStatus(
        installed=is_installed(),
        version=installed_version(),
        via_brew=brew_installed(),
    )


def install(
    log: Callable[[str], None] = print,
    force: bool = False,
) -> None:
    """Install or upgrade Zed, preferring Homebrew."""

    if is_installed() and not force:
        log("Zed is already installed; upgrading to the latest version.")
        upgrade(log=log)
        return

    if has_brew():
        log("Using Homebrew to install/upgrade Zed...")
        if brew_installed() and not force:
            upgrade(log=log)
            return
        res = _run(["brew", "install", "--cask", "zed"])
        if res.returncode != 0:
            raise RuntimeError(
                "Homebrew install failed:\n" + (res.stderr or res.stdout)
            )
        log("Zed installed via Homebrew.")
        return

    log("Homebrew not found; falling back to direct download.")
    _install_via_download(log=log)


def upgrade(log: Callable[[str], None] = print) -> None:
    """Upgrade Zed to the latest stable release."""

    if has_brew() and brew_installed():
        log("Upgrading Zed via Homebrew...")
        res = _run(["brew", "upgrade", "--cask", "zed"])
        if res.returncode != 0:
            # `brew upgrade` exits non-zero when already up to date.
            if "already up-to-date" in (res.stdout + res.stderr).lower():
                log("Zed is already up to date.")
                return
            raise RuntimeError(
                "Homebrew upgrade failed:\n" + (res.stderr or res.stdout)
            )
        log("Zed upgraded via Homebrew.")
        return

    if is_installed():
        log("Zed was not installed via Homebrew; re-installing latest.")
        _install_via_download(log=log)
        return

    raise RuntimeError("Zed is not installed; cannot upgrade.")


def _install_via_download(log: Callable[[str], None] = print) -> None:
    """Download the latest universal/arm64 dmg from GitHub and mount it."""

    import platform
    import urllib.request

    arch = "aarch64" if platform.machine() == "arm64" else "x86_64"
    tag = latest_release_tag()
    url = (
        f"https://github.com/zed-industries/zed/releases/download/"
        f"{tag}/Zed-{arch}.dmg"
    )
    log(f"Downloading {url}")
    with tempfile.TemporaryDirectory() as tmp:
        dmg = Path(tmp) / "Zed.dmg"
        urllib.request.urlretrieve(url, dmg)
        _run(["hdiutil", "attach", "-nobrowse", str(dmg)])
        mount = APPLICATIONS / "Zed.app"
        try:
            if mount.exists():
                _run(["rm", "-rf", str(mount)])
            _run(["cp", "-R", "/Volumes/Zed/Zed.app", str(mount)])
        finally:
            _run(["hdiutil", "detach", "/Volumes/Zed"])
    # Link the CLI if missing.
    if not ZED_CLI.exists():
        ZED_CLI.symlink_to(ZED_APP / "Contents" / "MacOS" / "cli")
    log("Zed installed via direct download.")


def latest_release_tag() -> str:
    """Return the latest Zed release tag (e.g. ``v1.11.3``)."""

    import urllib.request

    req = urllib.request.Request(
        "https://api.github.com/repos/zed-industries/zed/releases/latest",
        headers={"User-Agent": "byop"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        data = json_loads(resp.read().decode("utf-8"))
    return data["tag_name"]


def json_loads(text: str) -> dict:
    import json

    return json.loads(text)
