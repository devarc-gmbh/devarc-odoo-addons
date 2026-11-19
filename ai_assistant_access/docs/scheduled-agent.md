# Running a scheduled agent (advanced)

You can wire your AI client so it checks your Odoo data on a schedule instead of only when you ask
interactively. Ranked from safest/simplest to most complex, based on the research pass in
`research-providers-2026-09-06.md` (access date 2026-09-06, re-verify before relying on any single
provider's exact behavior - this is a fast-moving area).

> **Advanced setup.** Interactive use pairs through a one-time code (see the user guide). The
> unattended setups below pass the key as the `ODOO_AI_KEY` environment variable or point
> `ODOO_AI_CREDENTIALS` at the credentials file written by `pair`.

## 1. Your own OS cron / systemd timer / Task Scheduler + a headless CLI (recommended)

Run a headless AI CLI on a machine you control (`claude -p`, `codex exec`, `copilot -p`,
`gemini -p`, `droid exec`, ...), triggered by your own scheduler, with `.mcp.json`/`config.toml`
pointing at `odoo_ai_mcp.py`.

- **Why this is the reference setup:** it's the only option confirmed from all three angles at
  once: officially documented as an automation-clean use of a subscription, full network access (a
  normal OS process, no sandbox/firewall/allowlist), and the key stays in an OS-level secret you
  control - [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions),
  [Codex exec / non-interactive](https://developers.openai.com/codex/noninteractive).
- **Cost:** included in the Pro/Max/Plus/Copilot Pro subscription you likely already have for
  coding; the only limit is your plan's normal rate limit.

### cron (Linux/macOS)

```cron
# every day at 07:00, ask Claude Code to check Odoo and summarize
0 7 * * * cd /path/to/project && ODOO_AI_KEY="$(cat ~/.secrets/odoo_ai_key)" claude -p "Read assistant_instructions_read.md, then summarize my open leads." --output-format json >> /var/log/odoo-agent.log 2>&1
```

### systemd timer (Linux)

```ini
# /etc/systemd/system/odoo-agent.service
[Unit]
Description=Daily Odoo AI assistant check

[Service]
Type=oneshot
WorkingDirectory=/path/to/project
EnvironmentFile=/etc/odoo-agent.env
ExecStart=/usr/bin/claude -p "Read assistant_instructions_read.md, then summarize my open leads." --output-format json
```

```ini
# /etc/systemd/system/odoo-agent.timer
[Unit]
Description=Run odoo-agent daily

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

### Windows Task Scheduler

```powershell
$action = New-ScheduledTaskAction -Execute "claude.exe" -Argument '-p "Read assistant_instructions_read.md, then summarize my open leads." --output-format json' -WorkingDirectory "C:\path\to\project"
$trigger = New-ScheduledTaskTrigger -Daily -At 7am
Register-ScheduledTask -TaskName "OdooAIAgent" -Action $action -Trigger $trigger -RunLevel Limited
```
(Set `ODOO_AI_KEY` as a user-level environment variable beforehand, e.g. `setx ODOO_AI_KEY "..."`
- never inline it in the scheduled command line, see warnings below.)

## 2. Cursor Cloud Agents / Automations, when it allows your own MCP server in its sandbox

Cursor's cloud agents explicitly support both stdio and HTTP MCP transports, and the sandbox's
network level can be set to "allow all network access within the sandbox"
([Cloud Agents capabilities](https://cursor.com/docs/cloud-agent/capabilities),
[sandbox network controls](https://forum.cursor.com/t/cursor-2-5-sandbox-network-access-controls/152126)).
Requires Pro+/Ultra (Automations need Pro+ or higher). Store `ODOO_AI_KEY` as a **Runtime Secret**
(redacted from transcripts, but still visible to a human in a shared terminal) -
[Secrets & Network docs](https://cursor.com/docs/cloud-agent/security-network). Whether Automations
can run a script not tied to a git repository is not confirmed with a reliable source.

## 3. GitHub Agentic Workflows (gh-aw) on a GitHub Actions cron

YAML frontmatter with a `schedule:` cron trigger, engine of your choice (Copilot/Claude/Codex/
Gemini), MCP server config pointed at `odoo_ai_mcp.py`
([gh-aw repo](https://github.com/github/gh-aw)). Store `ODOO_AI_KEY` as a GitHub Actions
secret. **Important:** GitHub Copilot's coding-agent firewall does not apply to MCP servers at all
([firewall docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-firewall))
- your `hello` call reaches Odoo regardless of the firewall allowlist. A generic (non-Copilot)
  Actions runner has normal, unrestricted network access.

## 4. Anthropic-managed Routines / Codex cloud Automations / Devin Scheduled Sessions

**Network access is OFF by default** in these cloud sandboxes
([Claude Managed Agents egress](https://blog.cloudflare.com/claude-managed-agents/),
[Codex cloud internet access](https://developers.openai.com/codex/cloud/internet-access)) - you
must explicitly enable internet and allowlist your Odoo domain before a `hello` call (or any MCP
call) can reach it. It is **not confirmed** whether these products can even attach a user-supplied
stdio binary like `odoo_ai_mcp.py`, as opposed to only pre-defined/whitelisted servers tied to a
repo or environment - treat this as the least proven option of the five.

**Codex-specific secrets gotcha:** Codex cloud secrets are removed **before** the agent phase
begins ([Codex cloud environments](https://developers.openai.com/codex/cloud/environments)). If
your MCP server needs `ODOO_AI_KEY` *while running* (it does), it cannot be a Codex "secret" - it
has to be a plain, more visible environment variable instead.

## 5. API billing (fallback)

If none of the above fits, or your subscription's terms explicitly forbid the automation you want,
fall back to a provider API key billed per token. This is unambiguously ToS-clean everywhere - API
keys exist specifically for programmatic use. Keep the provider key on the same machine that runs
the cron job, in an OS-level secret store, and never in Odoo - see
[API Key Best Practices](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure)
for the general pattern (applies analogously to OpenAI/Google/xAI keys). No pricing here by
design - check each provider's official pricing page.

## Warnings (read before you automate anything)

- **Never use `--bare` with Claude Code for this use case.** `--bare` skips `.mcp.json` entirely
  and does not use your subscription login (it requires `ANTHROPIC_API_KEY` instead) - your MCP
  server and your subscription both silently stop working.
- **Never put the key on the command line.** Use an environment variable or a file with `600`
  permissions read by the scheduler - a plaintext key in a cron line or a scheduled-task argument
  is visible to anyone who can list processes or read the crontab/task definition.
- **Env-var expansion is not universal.** It is confirmed broken or unreliable in Cline (values
  taken literally), OpenCode (env not passed to the spawned child, [GH #26332](https://github.com/anomalyco/opencode/issues/26332)),
  and not confirmed at all for Codex's TOML config - see [docs/clients.md](clients.md) before
  picking a client for an unattended job.
- **Cloud sandboxes default to no network** (Codex cloud, Claude Managed Agents/Routines) - your
  `hello` call and every other MCP call will silently fail to reach Odoo until you allowlist the
  domain.
- **Copilot's coding-agent firewall does not cover MCP servers** - a security nuance worth knowing
  even though it works in your favor here (see section 3).
- **`hello` self-spam:** if your instructions tell the assistant to call `hello` at the start of
  every session, a scheduled agent that starts many short sessions per day will generate one inbox
  notification each time. Either drop that line from the scheduled agent's instructions file, or
  deliberately keep it and treat the notifications as a heartbeat ("my scheduled agent is alive").
  There is no cooldown on `hello` by design (see [docs/security.md](security.md)); the shared rate
  limit is the only backstop.
- **ToS notes, per provider:**
  - Anthropic: the OAuth-token restriction that bans third-party tools from using a Claude
    Pro/Max/Team login does **not** apply here - you are running the official Claude Code client
    itself as an MCP client against your own server, which is the documented, supported use case
    ([Consumer Terms](https://www.anthropic.com/legal/consumer-terms), section on automated access).
  - OpenAI: usage policy forbids commercial resale/white-labeling and multi-user use of one
    subscription; one person automating access to their own Odoo data appears to be in scope, but
    this is not spelled out as an explicit example
    ([Codex pricing/UI](https://uibakery.io/blog/openai-codex-pricing)).
  - GitHub Copilot: there is an open community discussion about running Copilot CLI/Actions "on
    behalf of multiple users" in automated workflows - relevant mainly if you'd distribute one
    shared gh-aw setup to many users, not for a single person's own automation
    ([GitHub community discussion](https://github.com/orgs/community/discussions/169533)).
