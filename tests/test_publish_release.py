"""Release promotion must not publish partial or overwritten artifacts."""

import io
import json

import pytest

from scripts import publish_release


class PreconditionFailed(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 412}}


class Bucket:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, **kwargs):
        if IfNoneMatch == "*" and Key in self.objects:
            raise PreconditionFailed()
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


def test_publish_immutable_release_and_promote_previous_version(tmp_path):
    store = Bucket()
    installer = tmp_path / "install.sh"
    installer.write_text('#!/bin/sh\nBYOP_PINNED_VERSION=""\n')
    for version in ("2.1.11", "2.1.12"):
        wheel = tmp_path / f"byop-{version}-py3-none-any.whl"
        wheel.write_bytes(version.encode())
        publish_release.publish(store, version, wheel, installer)
        assert json.loads(store.objects["latest.json"])["version"] == version
        assert f'BYOP_PINNED_VERSION="{version}"'.encode() in store.objects[
            f"releases/{version}/install.sh"
        ]

    publish_release.promote(store, "2.1.11")
    assert json.loads(store.objects["latest.json"])["version"] == "2.1.11"

    latest = store.objects["latest.json"]
    wheel = tmp_path / "byop-2.1.12-py3-none-any.whl"
    wheel.write_bytes(b"different wheel")
    with pytest.raises(ValueError, match="already exists"):
        publish_release.publish(store, "2.1.12", wheel, installer)
    assert store.objects["latest.json"] == latest
    assert store.objects["releases/2.1.12/" + wheel.name] == b"2.1.12"

    store.objects["releases/2.1.12/" + wheel.name] = b"corrupted"
    with pytest.raises(ValueError, match="checksum mismatch"):
        publish_release.promote(store, "2.1.12")
    assert store.objects["latest.json"] == latest
