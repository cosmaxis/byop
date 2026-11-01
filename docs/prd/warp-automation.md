# Warp endpoint automation

## Scope

This implementation supersedes the manual-only limitation in the August 2026 v1 PRD when the installed Warp settings schema matches the verified contract. It does not change older-version fallback, add production deployment, or submit inference during setup.

Evidence gathered October 26, 2026:

- Installed Warp Stable version `0.2026.10.21.08.25.02` exposes `CustomEndpointDefinition`, `CustomEndpointModel`, and `CustomEndpointSchema` in `/Applications/Warp.app/Contents/Resources/settings_schema.json`.
- The public client defines non-secret endpoint tables and a separate endpoint-key map. See [api_keys.rs](https://github.com/warpdotdev/warp/blob/1e6c3ae465d6a925a9b97ae03877aaadd159b8f1/crates/ai/src/api_keys.rs).
- The locally configured Keychain endpoint value was read back and compared to the provider credential without printing either. Warp's runtime completed streaming inference and a harmless terminal-tool round trip through Cosmaxis. The user subsequently confirmed desktop success after fully restarting Warp.

## Configuration contract

- File: `~/.warp/settings.toml`.
- Endpoint table: `agents.custom_endpoints.<endpoint ID>` with `name`, `base_url`, `schema`, and `models`.
- Each model has `name`, `alias`, and a stable UUID `config_key`.
- Protocol: `openai_chat_completions`.
- Default selection: `agents.execution_profiles.default.base_model`, only when the provider requests default-agent selection.
- Stable generic-password service: `dev.warp.Warp-Stable`.
- Account: `AiCustomEndpointKeys`.
- Value: UTF-8 JSON object mapping endpoint IDs to string credentials.

Do not overwrite the legacy `AiApiKeys` item, other endpoint credentials, unrelated settings, or permissions. Native Security.framework calls update existing items in place to retain access controls. Never place new credentials in subprocess arguments or settings files. Keychain failures abort configuration without writing endpoint settings.

Before changing the Keychain map, preserve the original settings in an owner-only `.bak` file. Render settings before touching live state, replace them atomically, detect intervening settings changes, and restore the previous Keychain map if the settings write fails. File and Keychain updates are not one cross-store transaction; concurrent Keychain writers or process termination can still require manual recovery. The implementation does not claim crash-atomicity across stores.

## User-visible behavior

A matching provider name updates only that endpoint and preserves existing model identifiers by model name. Duplicate provider names fail closed. Skip preserves the matching endpoint and key. Other providers remain untouched. Append remains unsupported by the CLI target's conflict contract.

Dry-run, status, and export must not access Warp's Keychain item. Export includes only endpoint metadata. Incompatible or missing installed schema, or disabled Keychain use, produces manual instructions without writes.

Save active work, fully quit Warp, and reopen it after setup. Creating a tab does not reload cached credentials. Select the custom model rather than Auto. Do not automate an application quit while terminal tasks may be active.

Warp routes prompts, context, URL, and key through its servers. A direct provider test alone does not validate Warp. The observed Anthropic path sent tools containing `required: null`, rejected by the Codex upstream; selecting Chat Completions avoided that serialization error. Automated tests must cover configuration and rollback, not claim to reproduce live inference.

## Verification

Use isolated temporary paths and fake Keychain calls for unit tests. Cover settings/comment preservation, stable IDs, unrelated keys/endpoints, selection, schema gating, dry-run, skip, validation, secret redaction, Keychain errors, and file-write rollback. Test the native adapter through fake Security.framework functions; never use the real user Keychain in the test suite.
