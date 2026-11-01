# ECC for Codex CLI

This supplements the root `AGENTS.md` with a repo-local ECC baseline.

## Repo Skill

- Repo-generated Codex skill: `.agents/skills/byop/SKILL.md`
- Claude-facing companion skill: `.claude/skills/byop/SKILL.md`
- Keep user-specific credentials and private MCPs in `~/.codex/config.toml`, not in this repo.

## MCP Baseline

The ECC server examples in `.codex/config.toml` are disabled by default and use
distinct names so they do not override personal MCP servers. Review a server's
package and credentials before enabling it for a session, for example:
`codex -c mcp_servers.ecc_context7.enabled=true`. User approval, sandbox, and
credential settings remain in the user's own configuration.

## Multi-Agent Support

- Explorer: read-only evidence gathering
- Reviewer: correctness, security, and regression review
- Docs researcher: API and release-note verification

## Workflow Files

- No dedicated workflow command files were generated for this repo.

Use these workflow files as reusable task scaffolds when the detected repository workflows recur.