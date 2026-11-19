# Supported AI clients

`odoo_ai_mcp.py` is a single-file **stdio** MCP server. Any client below that supports stdio MCP
servers can use it. The JSON key that wraps the server entry differs per client
(`mcpServers` vs `servers` vs `context_servers` vs TOML `[[mcp_servers]]`) - this is the single
biggest source of "I pasted the snippet and nothing happened" support requests, so check the
column below before copying the root snippet from Preferences > AI Assistant (which uses
`mcpServers`, the most common shape).

All facts below are sourced from the research pass listed at the bottom of this file
(access date 2026-09-06). Anything not explicitly confirmed by a cited source is marked
**(unverified)**.

Onboarding is the same for every client: `python odoo_ai_mcp.py pair --url ... --db ... --code ...`
stores the credentials file, and the client config only needs `ODOO_AI_CREDENTIALS=<path>` in the
MCP server `env`. The `${ODOO_AI_KEY}` column below matters only for the advanced environment
variable setup.

## Main list

| Client | MCP stdio | Config file (root key) | `${ODOO_AI_KEY}` env expansion (advanced setup only) | Instructions file | Headless CLI | Source |
|---|---|---|---|---|---|---|
| Claude Code (CLI / Desktop / Cowork) | Yes | `.mcp.json` or `~/.claude.json` (`mcpServers`) | Yes (fails to start if var is missing) | `CLAUDE.md` | `claude -p "prompt"` | [Claude Code MCP docs](https://code.claude.com/docs/en/mcp), [headless docs](https://code.claude.com/docs/en/headless) |
| OpenAI Codex (CLI / IDE extension) | Yes | `~/.codex/config.toml` (`[mcp_servers.<name>]`) | Not confirmed - example values are literal strings; workaround `codex mcp add <name> --env VAR=VALUE -- <cmd>` | `AGENTS.md` (unverified) | `codex exec` | [Codex CLI MCP setup](https://inventivehq.com/knowledge-base/openai/how-to-set-up-mcp-servers), [Codex exec](https://developers.openai.com/codex/noninteractive) |
| GitHub Copilot CLI | Yes | `~/.copilot/mcp-config.json` (`mcpServers`), or workspace `.mcp.json`/`.github/mcp.json` | Requested but not confirmed as implemented (open issue) | `.github/copilot-instructions.md` (unverified) | `copilot -p "prompt"` | [Add MCP servers for Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers), [headless mode](https://www.devleader.ca/2026/07/27/running-github-copilot-cli-in-scripts-and-cicd-pipelines-headless-mode) |
| VS Code Copilot (agent mode) | Yes | `.vscode/mcp.json` (root key **`servers`**, not `mcpServers`) | Yes, `${env:VAR}` (VS Code variable substitution) | `.github/copilot-instructions.md` (unverified) | N/A (IDE) | [VS Code MCP servers docs](https://code.visualstudio.com/docs/agent-customization/mcp-servers), [MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration) |
| Google Gemini CLI | Yes | `~/.gemini/settings.json` or project `.gemini/settings.json` (`mcpServers`) | Yes - `$VAR`, `${VAR}`, `%VAR%` (Windows); missing var resolves to empty string, no hard failure | `GEMINI.md` | `gemini -p` / `--non-interactive` | [Gemini CLI MCP docs](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html), [headless docs](https://geminicli.com/docs/cli/headless/) |
| Google Antigravity (IDE + `agy` CLI) | Yes, one shared config for IDE/CLI/SDK | `~/.gemini/config/mcp_config.json` | Not verified | Not verified | `agy` CLI (unverified) | [Configuring MCP for Antigravity](https://medium.com/google-cloud/configuring-mcp-servers-and-skills-for-antigravity-cli-and-ide-a938c7eebb78), [Antigravity MCP docs](https://antigravity.google/docs/mcp/) |
| xAI Grok Build (CLI) | Yes, native, "works with zero reconfiguration" from an existing Claude Code MCP config | Compatible with existing config, exact file not documented | Not verified | Not verified | Not explicit; "timer-based command scripts" exist | [Grok Build review](https://www.buildfastwithai.com/blogs/grok-build-xai-cli-ai-agents-2026), [xAI release notes](https://releasebot.io/updates/xai) |
| Cursor (editor agent) | Yes | `~/.cursor/mcp.json` (`mcpServers`) | Yes, `${env:NAME}` | `.cursorrules` (unverified) | N/A (IDE); cloud/background agents have their own web/CLI | [Cursor MCP docs](https://cursor.com/docs/mcp), [env vars discussion](https://forum.cursor.com/t/how-to-use-environment-variables-in-mcp-json/79296/9) |
| Windsurf (Cascade) | Yes | `~/.codeium/windsurf/mcp_config.json` | Not confirmed in docs | Not verified | Not found with a reliable source (assume none) | [Cascade MCP docs](https://docs.windsurf.com/windsurf/cascade/mcp), [setup guide](https://fast.io/resources/windsurf-mcp-setup-guide/) |
| Mistral Vibe (CLI) | Not confirmed - documented examples are remote/URL-oriented | `~/.vibe/config.toml` (`[[mcp_servers]]`) | Not confirmed | Not verified | Not explicit | [Mistral Vibe config](https://gist.github.com/chris-hatton/6e1a62be8412473633f7ef02d067547d), [Mistral docs](https://docs.mistral.ai/vibe/code/cli/configuration) |
| Amazon Kiro (IDE) | Yes, explicit `"transport": "stdio"` | `mcp.json` (`mcpServers`) | Yes, `${EXPANDED_VARIABLE}` | Not verified | N/A (IDE) | [Kiro MCP configuration](https://kiro.dev/docs/mcp/configuration/) |
| Devin | Has its own MCP server (sessions/scheduling); acting as an MCP *client* for a custom stdio server is not documented with a confirmed config format | Per Devin MCP docs | Not verified | Not verified | Sessions are API-driven | [Devin MCP docs](https://docs.devin.ai/work-with-devin/devin-mcp), [Scheduled Sessions](https://docs.devin.ai/product-guides/scheduled-sessions) |
| Factory Droid | Yes | Droid config, exact path unverified | Not verified | Not verified | `droid exec` | [Droid CLI overview](https://docs.factory.ai/droid-cli/overview), [Droid Exec](https://docs.factory.ai/droid-exec/overview) |
| Warp (terminal) | Yes, auto-discovers `~/.claude.json`, `.mcp.json`, `.codex/config.toml` | Settings > Agents > MCP, or whatever config it discovers | Not verified | Reuses whichever config file it discovered | "headless TUI" mentioned, exact flag unverified | [Warp MCP integration guide](https://lushbinary.com/blog/warp-mcp-integration-guide/), [Warp docs](https://docs.warp.dev/guides/external-tools/using-mcp-servers-with-warp/) |
| OpenCode | Yes, **but** a confirmed bug: `env` values in the config are not passed to the spawned child process ([GH #26332](https://github.com/anomalyco/opencode/issues/26332)) | `opencode.json` or `~/.config/opencode/opencode.json` | Syntax `{env:VAR}` exists but does not reach local MCP server processes - in the advanced setup set `ODOO_AI_KEY` in your OS environment instead | Not verified | `opencode run` / `opencode serve` + `run --attach` | [OpenCode config docs](https://opencode.ai/docs/config/), [CLI docs](https://opencode.ai/docs/cli/) |
| Cline (VS Code) | Yes | `cline_mcp_settings.json` (`mcpServers`) | **No** - values are taken literally, not expanded from the host environment (multiple confirmed issues) - paste the key value directly and never commit the file | Not verified | Cline CLI 2.0 has a headless mode (`-y`/`--yolo`) | [env var issue](https://github.com/cline/cline/issues/3129), [related issue](https://github.com/cline/cline/issues/3533), [Cline CLI 2.0](https://cline.ghost.io/introducing-cline-cli-2-0/) |
| Zed | Yes | `~/.config/zed/settings.json` (root key **`context_servers`**, requires `"source": "custom"`) | Yes, `env` block is passed to the spawned process | Not verified | None (editor) | [Zed MCP setup guide](https://mcpfind.org/blog/how-to-use-mcp-with-zed), [Zed MCP clients](https://www.claudemarket.ai/mcp/clients/zed) |
| JetBrains Junie | Yes | `~/.junie/mcp/mcp.json` (user) or `.junie/mcp/mcp.json` (project) (`mcpServers`) | Not verified | Not verified | Not confirmed with a reliable source | [Junie CLI MCP config](https://junie.jetbrains.com/docs/junie-cli-mcp-configuration.html), [Junie plugin MCP settings](https://junie.jetbrains.com/docs/junie-plugin-mcp-settings.html) |
| Perplexity | macOS Mac App Store app only, via the `PerplexityXPC` helper; Windows/Linux apps have no local MCP | Configured through the app's Settings > Connectors UI, not a plain file | N/A | N/A | No | [Local/Remote MCPs for Perplexity](https://www.perplexity.ai/help-center/en/articles/11502712-local-and-remote-mcps-for-perplexity) |
| Aider | Conflicting sources on whether native MCP support exists at all; if present, via `--mcp-server` flag / `.aider.conf.yml` `mcp-servers:` list - **verify with `aider --help \| grep mcp` on your installed version before relying on this** | `~/.aider.conf.yml` | Not verified | Not verified | Aider is CLI-first by design | [MCP in Aider](https://www.wearewarp.com/agents/mcp/aider), [Aider config docs](https://aider.chat/docs/config.html) |

## Not supported in 1.0 (remote/HTTP MCP needed)

These clients only accept a **remote** MCP server (a public HTTPS URL), not a local stdio process.
Wrapping `odoo_ai_mcp.py` in an HTTP/SSE transport is a deliberate non-goal for this release (it
would reopen exactly the "always-reachable remote agent" surface that was decided against for this
module) - so these clients are not supported yet:

- **Claude.ai chat** (web/mobile) - [remote MCP connectors only](https://claude.com/docs/connectors/custom/remote-mcp).
- **ChatGPT app** (Developer Mode) - [remote MCP connector only](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).
- **Perplexity web/Computer** (beyond the macOS local app above) - [connector-based only](https://www.perplexity.ai/help-center/en/articles/11502712-local-and-remote-mcps-for-perplexity).
- **Google Jules** - MCP is limited to a security-vetted provider whitelist (Linear, Neon,
  Supabase, Stitch, Tinybird, Context7); a custom server like this one is not confirmed to be
  addable - [Jules async coding agent guide](https://www.digitalapplied.com/blog/google-jules-gemini-async-coding-agent-guide).

**Explicitly excluded:** Amazon Q Developer CLI - MCP works only in interactive chat mode, not in
scripted/automated runs, and the product itself is being phased out (EOL 2027, replaced by Kiro
above) - [Amazon Q Developer MCP limitation](https://aws.amazon.com/blogs/devops/extend-the-amazon-q-developer-cli-with-mcp/).

## A note on network access in cloud/agentic surfaces

Two nuances worth knowing when picking a client for unattended use (see also
[docs/scheduled-agent.md](scheduled-agent.md)):

- Cloud sandboxes for Codex and Claude Managed Agents/Routines have **network access OFF by
  default** - an MCP call to your Odoo instance will not go through until you explicitly allow the
  domain.
- GitHub Copilot's coding-agent firewall **does not apply to MCP servers**, only to the Bash tool -
  so an MCP-initiated call reaches its destination even when the firewall is otherwise locked down
  ([firewall docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-firewall)).

## Sources

Full research pass with all citations: `research-providers-2026-09-06.md` (internal, access date
2026-09-06). Re-verify before relying on any "(unverified)" cell above - this is a fast-moving
part of the ecosystem.
