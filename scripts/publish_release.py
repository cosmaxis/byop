#!/usr/bin/env python3
"""Publish immutable BYOP artifacts and promote a verified release in R2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

BUCKET = "byop-releases"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PIN = b'BYOP_PINNED_VERSION=""'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_object(client, key: str) -> bytes:
    return client.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def put_immutable(client, key: str, data: bytes, content_type: str) -> None:
    try:
        client.put_object(
            Bucket=BUCKET, Key=key, Body=data, ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable", IfNoneMatch="*",
        )
    except Exception as exc:
        response = getattr(exc, "response", {})
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
        if read_object(client, key) != data:
            raise ValueError(f"Immutable release object already exists with different content: {key}") from exc
    if read_object(client, key) != data:
        raise ValueError(f"Uploaded release object did not verify: {key}")


def validate_manifest(data: bytes, version: str) -> dict[str, str]:
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid release manifest") from exc
    if (
        not isinstance(manifest, dict)
        or not VERSION.fullmatch(version)
        or manifest.get("version") != version
        or manifest.get("wheel") != f"byop-{version}-py3-none-any.whl"
        or not isinstance(manifest.get("sha256"), str)
        or not SHA256.fullmatch(manifest["sha256"])
    ):
        raise ValueError("Invalid release manifest")
    return manifest


def promote(client, version: str) -> None:
    if not VERSION.fullmatch(version):
        raise ValueError("Version must be a numeric X.Y.Z release")
    prefix = f"releases/{version}/"
    raw = read_object(client, prefix + "manifest.json")
    manifest = validate_manifest(raw, version)
    if digest(read_object(client, prefix + manifest["wheel"])) != manifest["sha256"]:
        raise ValueError("Release wheel checksum mismatch; latest was not updated")
    installer = read_object(client, prefix + "install.sh")
    if f'BYOP_PINNED_VERSION="{version}"'.encode() not in installer:
        raise ValueError("Release installer is not pinned to this version")
    client.put_object(
        Bucket=BUCKET, Key="latest.json", Body=raw,
        ContentType="application/json", CacheControl="no-store",
    )
    print(f"Promoted BYOP {version}")


def publish(client, version: str, wheel: Path, installer: Path) -> None:
    if not VERSION.fullmatch(version) or wheel.name != f"byop-{version}-py3-none-any.whl":
        raise ValueError("Release version and wheel filename do not match")
    wheel_bytes = wheel.read_bytes()
    script = installer.read_bytes()
    if script.count(PIN) != 1:
        raise ValueError("Installer must contain exactly one release version marker")
    script = script.replace(PIN, f'BYOP_PINNED_VERSION="{version}"'.encode())
    manifest = json.dumps({
        "version": version, "sha256": digest(wheel_bytes), "wheel": wheel.name,
    }, separators=(",", ":")).encode() + b"\n"
    prefix = f"releases/{version}/"
    put_immutable(client, prefix + wheel.name, wheel_bytes, "application/octet-stream")
    put_immutable(client, prefix + "install.sh", script, "text/plain; charset=utf-8")
    put_immutable(client, prefix + "manifest.json", manifest, "application/json")
    promote(client, version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    release = subparsers.add_parser("publish")
    release.add_argument("version")
    release.add_argument("wheel", type=Path)
    release.add_argument("installer", type=Path)
    rollback = subparsers.add_parser("promote")
    rollback.add_argument("version")
    args = parser.parse_args()

    import boto3

    client = boto3.client("s3", endpoint_url=os.environ["BYOP_R2_ENDPOINT"], region_name="auto")
    if args.command == "publish":
        publish(client, args.version, args.wheel, args.installer)
    else:
        promote(client, args.version)


if __name__ == "__main__":
    main()
