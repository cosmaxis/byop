"""Warp keychain tests use only in-memory native API doubles."""

import ctypes
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import warp_keychain


class NativeKeychain:
    def __init__(self):
        self.payload = b'{"endpoint":"test-key"}'
        self.find_status = 0
        self.write_status = 0
        self.delete_status = 0
        self.free_status = 0
        self.item = 1234
        self.buffer = None
        self.security = Mock()
        self.core_foundation = Mock()
        self.security.SecKeychainFindGenericPassword.side_effect = self.find
        self.security.SecKeychainItemModifyAttributesAndData.side_effect = self.modify
        self.security.SecKeychainAddGenericPassword.side_effect = self.add
        self.security.SecKeychainItemDelete.side_effect = lambda item: self.delete_status
        self.security.SecKeychainItemFreeContent.side_effect = lambda *args: self.free_status
        self.written = None

    def find(self, keychain, service_length, service, account_length, account, length, data, item):
        assert keychain is None
        assert service == b"dev.warp.Warp-Stable"
        assert service_length == len(service)
        assert account == b"AiCustomEndpointKeys"
        assert account_length == len(account)
        if self.find_status == 0:
            ctypes.cast(item, ctypes.POINTER(ctypes.c_void_p))[0] = self.item
            if data is not None:
                self.buffer = ctypes.create_string_buffer(self.payload)
                ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = len(self.payload)
                ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(
                    self.buffer
                )
            else:
                assert length is None
        return self.find_status

    def modify(self, item, attributes, length, data):
        assert item.value == self.item
        assert attributes is None
        self.written = ctypes.string_at(data, length)
        return self.write_status

    def add(self, keychain, service_length, service, account_length, account, length, data, item):
        assert keychain is None
        assert service == b"dev.warp.Warp-Stable"
        assert service_length == len(service)
        assert account == b"AiCustomEndpointKeys"
        assert account_length == len(account)
        assert item is None
        self.written = ctypes.string_at(data, length)
        return self.write_status


@pytest.fixture(autouse=True)
def native(monkeypatch):
    fake = NativeKeychain()

    def load(path):
        if path == "/System/Library/Frameworks/Security.framework/Security":
            return fake.security
        if path == "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation":
            return fake.core_foundation
        raise AssertionError("Unexpected native library")

    fake.loader = Mock(side_effect=load)
    monkeypatch.setattr(ctypes, "CDLL", fake.loader)
    monkeypatch.setattr(sys, "platform", "darwin")
    warp_keychain._load_frameworks.cache_clear()
    yield fake
    warp_keychain._load_frameworks.cache_clear()


def assert_released(native, *, password):
    native.core_foundation.CFRelease.assert_called_once()
    assert native.core_foundation.CFRelease.call_args.args[0].value == native.item
    if password:
        native.security.SecKeychainItemFreeContent.assert_called_once()
        attributes, data = native.security.SecKeychainItemFreeContent.call_args.args
        assert attributes is None
        assert data.value == ctypes.addressof(native.buffer)
    else:
        native.security.SecKeychainItemFreeContent.assert_not_called()


def test_import_does_not_load_frameworks(native):
    importlib.reload(warp_keychain)
    native.loader.assert_not_called()


@pytest.mark.parametrize("operation", ["get_endpoint_keys", "delete_endpoint_keys", "set_endpoint_keys"])
def test_non_macos_rejected_without_loading(native, monkeypatch, operation):
    monkeypatch.setattr(sys, "platform", "linux")
    arguments = ({"endpoint": "test-key"},) if operation == "set_endpoint_keys" else ()
    with pytest.raises(RuntimeError, match="macOS"):
        getattr(warp_keychain, operation)(*arguments)
    native.loader.assert_not_called()


def test_framework_load_errors_are_generic(native):
    native.loader.side_effect = OSError("sensitive-library-detail")
    with pytest.raises(RuntimeError) as error:
        warp_keychain.get_endpoint_keys()
    assert "sensitive-library-detail" not in str(error.value)


def test_native_signatures_are_declared(native):
    warp_keychain.get_endpoint_keys()
    assert native.security.SecKeychainFindGenericPassword.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    assert native.security.SecKeychainFindGenericPassword.restype is ctypes.c_int32
    for name, arguments in {
        "SecKeychainItemModifyAttributesAndData": [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p
        ],
        "SecKeychainAddGenericPassword": [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
            ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ],
        "SecKeychainItemDelete": [ctypes.c_void_p],
        "SecKeychainItemFreeContent": [ctypes.c_void_p, ctypes.c_void_p],
    }.items():
        function = getattr(native.security, name)
        assert function.argtypes == arguments
        assert function.restype is ctypes.c_int32
    assert native.core_foundation.CFRelease.argtypes == [ctypes.c_void_p]
    assert native.core_foundation.CFRelease.restype is None


@pytest.mark.parametrize("keys", [{}, {"endpoint": "test-key"}, {"端点": "clé\u0000value"}])
def test_get_returns_utf8_string_map_and_releases_resources(native, keys):
    native.payload = json.dumps(keys, ensure_ascii=False).encode("utf-8")
    assert warp_keychain.get_endpoint_keys() == keys
    assert_released(native, password=True)


@pytest.mark.parametrize(
    "payload",
    [b"", b"not-json-test-key", b"\xff", b"[]", b"null", b'"test-key"',
     b'{"endpoint":42}', b'{"endpoint":null}', b'{"endpoint":{}}',
     b'{"endpoint":["test-key"]}', b'{"endpoint":true}', b"[" * 2000],
)
def test_invalid_payload_fails_closed_and_releases_resources(native, payload, caplog, capsys):
    native.payload = payload
    with pytest.raises(RuntimeError) as error:
        warp_keychain.get_endpoint_keys()
    assert str(error.value) == "Invalid Warp keychain data"
    assert error.value.__context__ is None
    assert_released(native, password=True)
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


def test_copy_failure_still_releases_native_resources(native, monkeypatch):
    def fail_copy(*args):
        raise MemoryError

    monkeypatch.setattr(ctypes, "string_at", fail_copy)
    with pytest.raises(MemoryError):
        warp_keychain.get_endpoint_keys()
    assert_released(native, password=True)


def test_get_missing_returns_none(native):
    native.find_status = -25300
    assert warp_keychain.get_endpoint_keys() is None
    native.core_foundation.CFRelease.assert_not_called()
    native.security.SecKeychainItemFreeContent.assert_not_called()


@pytest.mark.parametrize("operation", ["get_endpoint_keys", "set_endpoint_keys", "delete_endpoint_keys"])
@pytest.mark.parametrize("status", [-25293, -25299, -50])
def test_lookup_errors_never_become_missing(native, operation, status):
    native.find_status = status
    arguments = ({"endpoint": "test-key"},) if operation == "set_endpoint_keys" else ()
    with pytest.raises(RuntimeError) as error:
        getattr(warp_keychain, operation)(*arguments)
    assert str(error.value) == f"Keychain operation failed ({status})"
    native.security.SecKeychainAddGenericPassword.assert_not_called()
    native.security.SecKeychainItemModifyAttributesAndData.assert_not_called()
    native.security.SecKeychainItemDelete.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("keys", [{}, {"端点": "clé\u0000value", "another": "test-key"}])
def test_set_updates_in_place_or_creates_exact_item(native, missing, keys, caplog, capsys):
    native.find_status = -25300 if missing else 0
    warp_keychain.set_endpoint_keys(keys)
    assert json.loads(native.written.decode("utf-8")) == keys
    assert "clé".encode() in native.written if keys else native.written == b"{}"
    if missing:
        native.security.SecKeychainAddGenericPassword.assert_called_once()
        native.security.SecKeychainItemModifyAttributesAndData.assert_not_called()
        native.core_foundation.CFRelease.assert_not_called()
    else:
        native.security.SecKeychainItemModifyAttributesAndData.assert_called_once()
        native.security.SecKeychainAddGenericPassword.assert_not_called()
        assert_released(native, password=False)
    native.security.SecKeychainItemDelete.assert_not_called()
    native.security.SecKeychainItemFreeContent.assert_not_called()
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("keys", [None, [], {1: "test-key"}, {"endpoint": 42}])
def test_set_rejects_non_string_maps_before_loading(native, keys):
    with pytest.raises(ValueError, match="string-to-string map"):
        warp_keychain.set_endpoint_keys(keys)
    native.loader.assert_not_called()


def test_set_rejects_invalid_utf8_without_exposing_value(native):
    with pytest.raises(ValueError) as error:
        warp_keychain.set_endpoint_keys({"endpoint": "sensitive-test-key\ud800"})
    assert str(error.value) == "Endpoint keys must be a UTF-8 string-to-string map"
    assert error.value.__context__ is None
    native.loader.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_set_errors_are_generic_and_do_not_replace_existing_item(native, missing):
    native.find_status = -25300 if missing else 0
    native.write_status = -25293
    with pytest.raises(RuntimeError) as error:
        warp_keychain.set_endpoint_keys({"endpoint": "test-key"})
    assert str(error.value) == "Keychain operation failed (-25293)"
    native.security.SecKeychainItemDelete.assert_not_called()
    if not missing:
        assert_released(native, password=False)
        native.security.SecKeychainAddGenericPassword.assert_not_called()


def test_delete_uses_exact_item_and_releases_reference(native):
    warp_keychain.delete_endpoint_keys()
    native.security.SecKeychainItemDelete.assert_called_once()
    assert native.security.SecKeychainItemDelete.call_args.args[0].value == native.item
    assert_released(native, password=False)


def test_delete_missing_is_noop(native):
    native.find_status = -25300
    warp_keychain.delete_endpoint_keys()
    native.security.SecKeychainItemDelete.assert_not_called()
    native.core_foundation.CFRelease.assert_not_called()


def test_delete_item_already_removed_is_noop(native):
    native.delete_status = -25300
    warp_keychain.delete_endpoint_keys()
    assert_released(native, password=False)


@pytest.mark.parametrize("status", [-25293, -50])
def test_delete_errors_release_reference(native, status):
    native.delete_status = status
    with pytest.raises(RuntimeError) as error:
        warp_keychain.delete_endpoint_keys()
    assert str(error.value) == f"Keychain operation failed ({status})"
    assert_released(native, password=False)


def test_free_error_still_releases_item(native):
    native.free_status = -50
    with pytest.raises(RuntimeError, match=r"Keychain operation failed \(-50\)"):
        warp_keychain.get_endpoint_keys()
    assert_released(native, password=True)


def test_frameworks_load_only_once(native):
    warp_keychain.get_endpoint_keys()
    warp_keychain.get_endpoint_keys()
    assert native.loader.call_count == 2
