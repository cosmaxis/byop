import pytest

from byop import cli
from byop.core import connection
from byop.core import profiles as prof
from byop.core.config import ModelConfig, ProviderConfig


@pytest.mark.parametrize("decline", [False, True])
def test_explicit_key_test_defaults_yes_but_decline_never_reads_or_sends_key(
    monkeypatch, tmp_path, decline,
):
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="default"))
    actions = iter(["Test API key", "Quit"])
    monkeypatch.setattr(cli.prompt, "choose", lambda *args, **kwargs: next(actions))
    monkeypatch.setattr(cli.prompt, "confirm", lambda message, default: False if decline else default)
    events = []

    def load_key(profile):
        events.append("read")
        return provider.api_key

    def check_key(endpoint, key):
        events.append((endpoint, key))
        return "ok", "Request accepted"

    monkeypatch.setattr(cli, "_load_profile_api_key", load_key)
    monkeypatch.setattr(connection, "check_api_key", check_key)
    assert cli.main([]) == 0
    assert events == ([] if decline else ["read", (provider.api_url, provider.api_key)])
