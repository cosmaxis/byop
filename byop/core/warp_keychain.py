"""Native access to Warp's generic-password endpoint key map."""

import ctypes
import json
import sys
from functools import lru_cache

_SERVICE = b"dev.warp.Warp-Stable"
_ACCOUNT = b"AiCustomEndpointKeys"
_ITEM_NOT_FOUND = -25300


@lru_cache(maxsize=1)
def _load_frameworks():
    if sys.platform != "darwin":
        raise RuntimeError("Warp keychain requires macOS")
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
    except OSError:
        pass
    else:
        pointer = ctypes.c_void_p
        length = ctypes.c_uint32
        signatures = {
            "SecKeychainFindGenericPassword": [
                pointer, length, ctypes.c_char_p, length, ctypes.c_char_p,
                ctypes.POINTER(length), ctypes.POINTER(pointer), ctypes.POINTER(pointer),
            ],
            "SecKeychainAddGenericPassword": [
                pointer, length, ctypes.c_char_p, length, ctypes.c_char_p,
                length, pointer, ctypes.POINTER(pointer),
            ],
            "SecKeychainItemModifyAttributesAndData": [pointer, pointer, length, pointer],
            "SecKeychainItemDelete": [pointer],
            "SecKeychainItemFreeContent": [pointer, pointer],
        }
        for name, arguments in signatures.items():
            function = getattr(security, name)
            function.argtypes = arguments
            function.restype = ctypes.c_int32
        core_foundation.CFRelease.argtypes = [pointer]
        core_foundation.CFRelease.restype = None
        return security, core_foundation
    raise RuntimeError("Unable to load macOS keychain frameworks")


def _check_status(status: int) -> None:
    if status != 0:
        raise RuntimeError(f"Keychain operation failed ({status})")


def _find_item(security, *, password: bool = False):
    item = ctypes.c_void_p()
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None, len(_SERVICE), _SERVICE, len(_ACCOUNT), _ACCOUNT,
        ctypes.byref(length) if password else None,
        ctypes.byref(data) if password else None,
        ctypes.byref(item),
    )
    if status == _ITEM_NOT_FOUND:
        return None
    _check_status(status)
    return item, length, data


def _is_string_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    )


def _parse_keys(payload: bytes) -> dict[str, str] | None:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    return value if _is_string_map(value) else None


def get_endpoint_keys() -> dict[str, str] | None:
    security, core_foundation = _load_frameworks()
    found = _find_item(security, password=True)
    if found is None:
        return None
    item, length, data = found
    try:
        keys = _parse_keys(ctypes.string_at(data, length.value))
    finally:
        try:
            _check_status(security.SecKeychainItemFreeContent(None, data))
        finally:
            core_foundation.CFRelease(item)
    if keys is None:
        raise RuntimeError("Invalid Warp keychain data")
    return keys


def set_endpoint_keys(keys: dict[str, str]) -> None:
    if not _is_string_map(keys):
        raise ValueError("Endpoint keys must be a string-to-string map")
    payload = None
    try:
        payload = json.dumps(keys, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, ValueError, RecursionError):
        pass
    if payload is None:
        raise ValueError("Endpoint keys must be a UTF-8 string-to-string map")
    security, core_foundation = _load_frameworks()
    found = _find_item(security)
    if found is None:
        _check_status(security.SecKeychainAddGenericPassword(
            None, len(_SERVICE), _SERVICE, len(_ACCOUNT), _ACCOUNT,
            len(payload), payload, None,
        ))
        return
    item, _, _ = found
    try:
        # Updating data in place retains the existing item's access controls.
        _check_status(security.SecKeychainItemModifyAttributesAndData(
            item, None, len(payload), payload,
        ))
    finally:
        core_foundation.CFRelease(item)


def delete_endpoint_keys() -> None:
    security, core_foundation = _load_frameworks()
    found = _find_item(security)
    if found is None:
        return
    item, _, _ = found
    try:
        status = security.SecKeychainItemDelete(item)
        if status != _ITEM_NOT_FOUND:
            _check_status(status)
    finally:
        core_foundation.CFRelease(item)
