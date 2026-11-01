import json
from unittest import mock

import pytest
import tomlkit

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.warp import WarpTarget, endpoint_schema_supported, validate_warp_endpoint


def provider(url="https://api.example.com/v1"):
    return ProviderConfig("Demo", url, "secret-key-123", [ModelConfig("model")])


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    monkeypatch.setattr("byop.core.targets.warp.validate_warp_endpoint", lambda _: None)
    monkeypatch.setattr("byop.core.targets.warp.endpoint_schema_supported", lambda: True)
    monkeypatch.setattr("byop.core.targets.warp.get_endpoint_keys", lambda: None)
    monkeypatch.setattr("byop.core.targets.warp.set_endpoint_keys", mock.Mock())
    monkeypatch.setattr("byop.core.targets.warp.delete_endpoint_keys", mock.Mock())


def load(path):
    return tomlkit.parse(path.read_text()).unwrap()


def test_detection_from_temp_settings(tmp_path):
    with mock.patch("byop.core.targets.warp.WARP_APP", tmp_path / "Warp.app"), mock.patch("byop.core.targets.warp.shutil.which", return_value=None):
        assert WarpTarget(tmp_path / ".warp" / "settings.toml").is_installed() is False
        (tmp_path / ".warp").mkdir()
        assert WarpTarget(tmp_path / ".warp" / "settings.toml").is_installed() is True


def test_dry_run_redacts_key_and_does_not_write(tmp_path):
    path = tmp_path / ".warp" / "settings.toml"
    logs = []
    with mock.patch("byop.core.targets.warp.get_endpoint_keys") as get_keys, mock.patch("byop.core.targets.warp.set_endpoint_keys") as set_keys:
        WarpTarget(path).configure(provider(), dry_run=True, log=logs.append)
    assert not path.exists()
    get_keys.assert_not_called()
    set_keys.assert_not_called()
    output = "\n".join(logs)
    assert "openai_chat_completions" in output
    assert "secret-key-123" not in output


def test_configure_preserves_comments_and_settings_and_stores_key(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('# Keep this comment\n[appearance]\nspacing = "compact"\n')
    logs = []
    with mock.patch("byop.core.targets.warp.set_endpoint_keys") as set_keys:
        WarpTarget(path).configure(provider(), log=logs.append)
    data = load(path)
    endpoints = data["agents"]["custom_endpoints"]
    assert len(endpoints) == 1
    endpoint_id, endpoint = next(iter(endpoints.items()))
    assert endpoint["name"] == "Demo"
    assert endpoint["base_url"] == "https://api.example.com/v1"
    assert endpoint["schema"] == "openai_chat_completions"
    assert endpoint["models"][0]["name"] == "model"
    assert data["agents"]["execution_profiles"]["default"]["base_model"] == endpoint["models"][0]["config_key"]
    set_keys.assert_called_once_with({endpoint_id: "secret-key-123"})
    assert path.read_text().startswith("# Keep this comment\n")
    assert data["appearance"]["spacing"] == "compact"
    assert "secret-key-123" not in path.read_text()
    assert "secret-key-123" not in "\n".join(logs)
    assert any("quit" in line.lower() and "reopen" in line.lower() for line in logs)
    assert any("new tab" in line.lower() for line in logs)
    assert path.with_name("settings.toml.bak").read_text().startswith("# Keep this comment")
    assert path.with_name("settings.toml.bak").stat().st_mode & 0o777 == 0o600


def test_reconfigure_reuses_legacy_ids_and_preserves_other_endpoints(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(tomlkit.dumps({"agents": {
        "custom_endpoints": {
            "legacy-endpoint": {"name": "Demo", "base_url": "https://old.example.com/v1", "schema": "anthropic_messages", "models": [{"name": "model", "alias": "existing", "config_key": "stable-model-id"}]},
            "other": {"name": "Other", "base_url": "https://other.example.com/v1", "schema": "openai_responses", "models": [{"name": "other", "config_key": "other-id"}]},
        },
        "execution_profiles": {"default": {"execute_commands": "always_ask", "base_model": "other-id"}},
    }}))
    with mock.patch("byop.core.targets.warp.get_endpoint_keys", return_value={"legacy-endpoint": "old-key", "other": "other-key"}), mock.patch("byop.core.targets.warp.set_endpoint_keys") as set_keys:
        target = WarpTarget(path)
        target.configure(provider(), log=lambda _: None)
        first = path.read_text()
        target.configure(provider(), log=lambda _: None)
    assert path.read_text() == first
    endpoints = load(path)["agents"]["custom_endpoints"]
    assert set(endpoints) == {"legacy-endpoint", "other"}
    assert endpoints["legacy-endpoint"]["models"][0] == {"name": "model", "alias": "existing", "config_key": "stable-model-id"}
    assert endpoints["other"]["schema"] == "openai_responses"
    assert load(path)["agents"]["execution_profiles"]["default"]["execute_commands"] == "always_ask"
    set_keys.assert_called_with({"legacy-endpoint": "secret-key-123", "other": "other-key"})


def test_skip_does_not_touch_settings_or_keychain(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[agents.custom_endpoints.existing]\nname = "Demo"\nbase_url = "https://api.example.com/v1"\n')
    original = path.read_bytes()
    with mock.patch("byop.core.targets.warp.get_endpoint_keys") as get_keys, mock.patch("byop.core.targets.warp.set_endpoint_keys") as set_keys:
        WarpTarget(path).configure(provider(), conflict_action="skip", log=lambda _: None)
    assert path.read_bytes() == original
    get_keys.assert_not_called()
    set_keys.assert_not_called()


def test_append_rejected(tmp_path):
    with pytest.raises(ValueError, match="append"):
        WarpTarget(tmp_path / "settings.toml").configure(provider(), conflict_action="append", log=lambda _: None)


def test_unsupported_schema_falls_back_without_any_writes(tmp_path, monkeypatch):
    monkeypatch.setattr("byop.core.targets.warp.endpoint_schema_supported", lambda: False)
    path = tmp_path / "settings.toml"
    path.write_text('[unrelated]\nvalue = "keep"\n')
    original = path.read_bytes()
    logs = []
    with mock.patch("byop.core.targets.warp.get_endpoint_keys") as get_keys:
        WarpTarget(path).configure(provider(), log=logs.append)
    assert path.read_bytes() == original
    get_keys.assert_not_called()
    assert any("manual" in item.lower() for item in logs)
    assert "secret-key-123" not in "\n".join(logs)


def test_no_keychain_mode_falls_back_without_plaintext(tmp_path):
    path = tmp_path / "settings.toml"
    with mock.patch("byop.core.targets.warp.set_endpoint_keys") as set_keys:
        WarpTarget(path).configure(provider(), use_keychain=False, use_env=True, log=lambda _: None)
    assert not path.exists()
    set_keys.assert_not_called()


def test_keychain_failure_leaves_settings_untouched(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[appearance]\nspacing = "compact"\n')
    original = path.read_bytes()
    with mock.patch("byop.core.targets.warp.set_endpoint_keys", side_effect=RuntimeError("denied")), pytest.raises(RuntimeError, match="denied"):
        WarpTarget(path).configure(provider(), log=lambda _: None)
    assert path.read_bytes() == original


def test_file_failure_restores_previous_key_map(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[appearance]\nspacing = "compact"\n')
    path.with_name("settings.toml.bak").write_bytes(path.read_bytes())
    with mock.patch("byop.core.targets.warp.get_endpoint_keys", return_value={"other": "preserved-key"}), mock.patch("byop.core.targets.warp.set_endpoint_keys") as set_keys, mock.patch("byop.core.targets.warp.atomic_write", side_effect=OSError("disk full")), pytest.raises(OSError, match="disk full"):
        WarpTarget(path).configure(provider(), log=lambda _: None)
    assert set_keys.call_args_list[-1] == mock.call({"other": "preserved-key"})
    assert load(path) == {"appearance": {"spacing": "compact"}}


def test_file_failure_removes_new_keychain_item(tmp_path):
    with mock.patch("byop.core.targets.warp.delete_endpoint_keys") as delete_keys, mock.patch("byop.core.targets.warp.atomic_write", side_effect=OSError("disk full")), pytest.raises(OSError, match="disk full"):
        WarpTarget(tmp_path / "settings.toml").configure(provider(), log=lambda _: None)
    delete_keys.assert_called_once_with()


def test_export_and_status_include_only_nonsecret_endpoint_metadata(tmp_path):
    path = tmp_path / "settings.toml"
    target = WarpTarget(path)
    target.configure(provider(), log=lambda _: None)
    assert target.current_provider_names() == ["Demo"]
    exported = target.export_config()["warp"]
    assert exported["config_path"] == str(path)
    assert exported["providers"]["Demo"]["schema"] == "openai_chat_completions"
    assert "secret-key-123" not in json.dumps(exported)


def test_toml_round_trip_preserves_unrelated_tables(tmp_path):
    path = tmp_path / "settings.toml"
    target = WarpTarget(path)
    data = {"general": {"theme": "dark"}, "telemetry": {"enabled": False}}
    target.write_settings(data)
    assert target.load_settings() == data


def test_multiline_inline_tables_from_warp_are_preserved(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[privacy]\ncustom_secret_regex_list = [\n  {\n    name = "IPv6",\n    pattern = "test",\n  },\n]\n')
    WarpTarget(path).configure(provider(), log=lambda _: None)
    assert load(path)["privacy"]["custom_secret_regex_list"] == [{"name": "IPv6", "pattern": "test"}]


@pytest.mark.parametrize("duplicate_source", ["incoming", "existing"])
def test_duplicate_models_fail_before_persistence(tmp_path, duplicate_source):
    path = tmp_path / "settings.toml"
    models = [{"name": "model", "config_key": "first-id"}]
    config = provider()
    if duplicate_source == "incoming":
        config.models.append(ModelConfig("model"))
    else:
        models.append({"name": "model", "config_key": "second-id"})
    path.write_text(tomlkit.dumps({"agents": {
        "custom_endpoints": {"existing": {"name": "Demo", "models": models}},
        "execution_profiles": {"secondary": {"base_model": "first-id"}},
    }}))
    original = path.read_bytes()
    with mock.patch("byop.core.targets.warp.get_endpoint_keys") as get_keys, pytest.raises(ValueError, match="unique"):
        WarpTarget(path).configure(config, log=lambda _: None)
    get_keys.assert_not_called()
    assert path.read_bytes() == original


def test_symlink_settings_fail_before_keychain_access(tmp_path):
    destination = tmp_path / "dotfile.toml"
    destination.write_text('[appearance]\nspacing = "compact"\n')
    path = tmp_path / "settings.toml"
    path.symlink_to(destination)
    with mock.patch("byop.core.targets.warp.get_endpoint_keys") as get_keys, pytest.raises(ValueError, match="symlink"):
        WarpTarget(path).configure(provider(), log=lambda _: None)
    get_keys.assert_not_called()
    assert path.is_symlink()
    assert destination.read_text() == '[appearance]\nspacing = "compact"\n'


@pytest.mark.parametrize("default_enabled,profile_name,field", [
    (False, "default", "base_model"),
    (True, "default", "coding_model"),
    (True, "secondary", "base_model"),
])
def test_replacing_referenced_model_fails_without_changes(tmp_path, default_enabled, profile_name, field):
    path = tmp_path / "settings.toml"
    path.write_text(tomlkit.dumps({"agents": {
        "custom_endpoints": {"existing": {"name": "Demo", "models": [{"name": "old", "config_key": "old-id"}]}},
        "execution_profiles": {profile_name: {field: "old-id"}},
    }}))
    original = path.read_bytes()
    config = provider()
    config.set_default_agent = default_enabled
    with mock.patch("byop.core.targets.warp.get_endpoint_keys") as get_keys, pytest.raises(ValueError, match="execution profile"):
        WarpTarget(path).configure(config, log=lambda _: None)
    get_keys.assert_not_called()
    assert path.read_bytes() == original


@pytest.mark.parametrize("mutation", ["none", "base_url", "models", "new_required", "missing"])
def test_schema_guard_rejects_incompatible_structure(tmp_path, monkeypatch, mutation):
    from byop.core.targets import warp

    schema = {"$defs": {
        "CustomEndpointDefinition": {"type": "object", "required": ["name", "base_url", "models"], "properties": {
            "name": {"type": "string"}, "base_url": {"type": "string"},
            "schema": {"$ref": "#/$defs/CustomEndpointSchema"},
            "models": {"type": "array", "items": {"$ref": "#/$defs/CustomEndpointModel"}},
        }},
        "CustomEndpointModel": {"type": "object", "properties": {
            "name": {"type": "string"}, "alias": {"type": ["string", "null"]}, "config_key": {"type": "string"},
        }},
        "CustomEndpointSchema": {"oneOf": [{"const": "openai_chat_completions", "type": "string"}]},
    }}
    if mutation in {"base_url", "models"}:
        schema["$defs"]["CustomEndpointDefinition"]["properties"][mutation]["type"] = "integer"
    elif mutation == "new_required":
        schema["$defs"]["CustomEndpointDefinition"]["required"].append("unsupported_field")
    resources = tmp_path / "Contents" / "Resources"
    resources.mkdir(parents=True)
    if mutation != "missing":
        (resources / "settings_schema.json").write_text(json.dumps(schema))
    monkeypatch.setattr(warp, "WARP_APP", tmp_path)
    assert endpoint_schema_supported() is (mutation == "none")


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://localhost/v1", "https://127.0.0.1/v1", "https://user:secret@example.com/v1"])
def test_endpoint_must_be_public_https(url):
    with pytest.raises(ValueError):
        validate_warp_endpoint(url)
