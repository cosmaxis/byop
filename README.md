# byop

Configure one LLM provider across your macOS coding tools. Byop saves reusable profiles; it does not proxy requests or verify gateway compatibility.

## Install

```sh
curl -sfS https://byop.cosmaxis.com/install | sh
```

The [installer](install.sh) downloads a version-pinned wheel from Cloudflare, verifies its SHA-256, and installs it with pipx. It installs Homebrew, Python 3.11+, and pipx if needed; Homebrew may request administrator access. Python dependencies may be downloaded from PyPI. If `byop` is not on your PATH, use the path printed by the installer.

Releases are published to R2 by CI after tests and builds pass. To roll back, run the CI workflow on `master` with `rollback_version` set to a previously published R2 version; CI verifies its artifacts before updating the latest pointer. The checksum detects corrupted downloads, not compromise of the release origin.

## Get started

```sh
byop
```

1. Choose **Add provider** and enter your provider name, API URL, key, and model IDs.
2. Choose **Set up tools** and select the tools to configure. Byop asks before installing missing tools.
3. To change a field later, choose **Edit provider**. Select **Models** to add or edit models, choose the default, or remove models; choose **Save** when finished.

Use ↑/↓ and Enter to navigate; Space selects tools. Escape cancels cursor menus, and Ctrl-C cancels setup. Set `BYOP_PLAIN=1` for line-based menus.

## Tools and model limits

| Tool | `--target` | Context / output limits |
| --- | --- | --- |
| [Zed](https://zed.dev) | `zed` | Per model |
| [Pi](https://pi.dev) | `py` | Per model |
| [Oh My Pi](https://omp.sh) | `omp` | Per model |
| [OpenCode](https://opencode.ai) | `opencode` | Per model |
| [Claude Code](https://code.claude.com/docs/) | `claude` | First model; session-wide |
| [Codex](https://developers.openai.com/codex/) | `codex` | Context only |
| [Warp](https://docs.warp.dev/agents/inference/custom-inference-endpoint/) | `warp` | Not supported by its endpoint schema |

Limits are supplied by you, not discovered from the gateway. Claude can ignore context overrides for some recognized model IDs; check `/context`. Pi and Oh My Pi apply the `max_tokens` compatibility option per model. Oh My Pi uses the active `OMP_PROFILE` or `PI_PROFILE`; pass `--profile NAME` or `--omp-models-path PATH` to select another location.

## Providers and models

Use `byop profile edit` to change the active provider without re-entering other fields. Model details include the context window, maximum output, reasoning effort, and capabilities. After editing, run `byop apply` to update tools. The first model is the default where a tool accepts only one.

You can enter comma-separated model IDs or paste JSON at the first provider prompt. Omit `api_key` to enter it privately:

```json
{
  "provider_name": "Example",
  "api_url": "https://api.example.com/v1",
  "models": [
    {"name": "fast", "max_tokens": 128000, "max_output_tokens": 16000},
    {"name": "pro", "max_tokens": 500000, "max_output_tokens": 32000}
  ]
}
```

In byop's JSON format, `max_tokens` is the context window. `models` also accepts ID strings or an object keyed by model ID, including Pi `contextWindow`/`maxTokens` and OpenCode `limit.context`/`limit.output` fields. With `--config-file`, include `api_key` and protect the file.

For scripts, repeat `--model` and `--target`:

```sh
byop apply --provider Example --api-url https://api.example.com/v1 \
  --api-key "$EXAMPLE_API_KEY" \
  --model 'fast:max_tokens=128000:max_output_tokens=16000' \
  --model 'pro:max_tokens=500000:max_output_tokens=32000' \
  --target zed --target py --dry-run
```

Remove `--dry-run` to apply. Command-line arguments can expose keys to other processes; prefer the interactive key prompt on shared systems. Run `byop apply --help` for all options.

## Useful commands

| Command | Purpose |
| --- | --- |
| `byop status` | Show the active provider and model limits |
| `byop profile list` / `byop profile use NAME` | List or switch providers |
| `byop profile export` | Print the active profile without its API key |
| `byop apply --dry-run` | Preview changes without writing settings or keys |
| `byop doctor` / `byop export-config` | Inspect tool configuration |

`status` and `doctor` inspect local settings; they do not test inference. `--skip-install` configures without installing tools. Without `--target`, scripted application selects installed tools only.

## Compatibility and keys

- **Claude Code** requires an Anthropic Messages-compatible `/v1/messages` gateway, not only OpenAI compatibility. BYOP prevents inherited `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` values from overriding its configured credential; environment-backed profiles using either variable retain that variable as their credential. Project or managed settings can override the global settings. See [Claude's context restrictions](https://code.claude.com/docs/en/model-config#correct-the-window-for-a-gateway-or-custom-model-id).
- **Codex** requires an OpenAI Responses-compatible gateway. It supports one active provider and rejects `--conflict append`.
- **Warp** requires public HTTPS and a compatible installed schema plus Keychain access; otherwise setup is manual. Save your work, fully quit Warp, reopen it, and choose the custom model. Prompts, context, endpoint URL, and key pass through Warp's servers. See [Warp's endpoint requirements](https://docs.warp.dev/agents/inference/custom-inference-endpoint/).
- **Keys** default to macOS Keychain. `--env-key` writes a plaintext shell export; `--no-keychain` can write plaintext to tool settings. Environment-backed profiles reuse their variable. Zed adds a shell reference under its required name and refuses to overwrite an existing export. OpenCode's Keychain-backed export needs a new terminal. Older profiles may have shell exports that byop did not record. Providers sharing a Keychain URL must use the same key.

Byop preserves unrelated settings, including Oh My Pi’s string-valued model IDs and headers. Use `--conflict replace` or `skip` for an existing provider; `append` applies only to Pi, Oh My Pi, and OpenCode. Review a dry run before changing customized tools.

[MIT license](LICENSE) · [Report an issue](https://github.com/Cosmaxis/byop/issues)
