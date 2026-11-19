# Security model

Eight layers, hardest (hardest to bypass) to softest.

1. **PostgreSQL `READ ONLY` transaction.** Every request runs on a fresh cursor whose first
   statement is `SET TRANSACTION READ ONLY`. Any write - even through `sudo()`, even through raw
   SQL - fails with `ReadOnlySqlTransaction`; the endpoint catches it and returns `403
   write_attempt`. This is enforced at the database level, not in application code.
2. **Whitelist of 7 read operations.** Only `models`, `fields_get`, `search_read`, `search_count`,
   `read`, `name_search`, `read_group` are reachable. No business method (`action_confirm`,
   `message_post`, `unlink`, ...) is dispatchable.
3. **Identity = the key's owner.** Access rights (`ir.model.access`) and record rules (`ir.rule`)
   are evaluated for the original user, nothing is cloned or elevated. The assistant sees exactly
   what its owner sees in the UI, including every company in their multi-company scope.
4. **Model whitelist (admin-configurable) plus a hard blacklist (code, not overridable).** The
   admin-editable whitelist lives in `ir.config_parameter` (`ai_assistant.allowed_models`). The
   hard blacklist (`res.users`, `res.users.apikeys*`, `ir.config_parameter`, `ir.mail_server`,
   `payment.*`, `auth_oauth*`, `auth_totp*`, `ir.attachment`, `ir.model.access`, `ir.rule`,
   `ai.assistant.*`, `ir.actions*`, `ir.ui.*`, `ir.model*`, `ir.cron`, `res.groups`,
   `base.automation`, `mail.template`, and more) is checked *before* the whitelist and cannot be
   re-enabled by an admin through settings.
5. **Field filter.** Binary fields are never returned. A regex blacklist on field names (`password`,
   `secret`, `token`, `api_key`, `totp`, `oauth`, `signature`, ...) applies to every field the
   client can reference, including inside `domain`, `groupby` and aggregates. Dotted paths
   (`partner_id.vat`) are rejected outright - without that check, `search_count` could be used as a
   boolean oracle to guess a secret field's value character by character. For the same reason the
   nested domain of `any`/`not any` is checked recursively against the model whitelist and the
   field filter. An admin regex extends the built-in pattern, it never replaces it. Requests are capped at
   50 fields and 200 records; responses are capped at 1 MB.
6. **The key itself.** Scoped (`ai_assistant_ro` - does not work for `/xmlrpc`, `/jsonrpc` or web
   login), hashed in the database, shown to its owner exactly once, expires by default after 90
   days (admin-configurable, 365-day hard cap), at most one active key per user, HTTPS required
   (except explicit local dev), rate-limited per key.
7. **Audit.** Every authenticated request - success or failure - is logged (`ai.assistant.audit`:
   who, when, IP, operation, model, a truncated domain, record count, duration, result). Prompt
   content and returned data are never stored in Odoo, only request metadata. Unauthenticated
   attempts (bad/missing key) are never written to the audit table, only to the server log - so an
   attacker cannot use failed auth attempts to fill the database.
8. **Kill-switch.** A global toggle (`ai_assistant.enabled`) returns `503` on every request when
   off, plus per-user revocation from the user's own Preferences or from the admin's user form.

## `hello`: the one write, and a connection alert

The module performs exactly one write operation, `hello`. When an AI client connects and calls it
(typically once at the start of a session), the owner receives a single Odoo inbox notification -
*"Your AI assistant just connected to Odoo with read-only access (from IP `<ip>`). If this was not
you, revoke the key in Preferences > AI Assistant."* - authored by OdooBot, in the owner's own
language. The request body is ignored entirely (no HTML/script injection surface); the text is
generated server-side.

This means `hello` doubles as a **connection alert**: if a key is ever stolen and used from another
machine, the owner sees an unfamiliar IP in their inbox and can revoke the key immediately. There is
no cooldown on `hello` - the shared per-key rate limit is the only throttle - so any documentation
or scheduled-agent setup that claims "no writes, ever" without mentioning `hello` is wrong; say "the
only write is `hello`" instead.

## Secrets

- **The key lives only on the client machine.** `pair` writes it to a credentials file in the
  user profile (`%APPDATA%` on Windows, mode `0600` on macOS/Linux) - never in `.mcp.json`, never
  committed to a repository, never shown in the browser or the chat, never passed on a command
  line. Advanced setups may use the `ODOO_AI_KEY` environment variable instead.
- **Provider API keys (Anthropic/OpenAI/Google/xAI/...) never touch Odoo.** The module has no
  field, no import, and no call site for any AI provider - this is machine-checked by a dedicated
  test, not just documented.
- **Per-client gotchas, advanced `ODOO_AI_KEY` setup only** (the credentials file written by
  `pair` is the default; see [docs/clients.md](clients.md) for sources): environment-variable
  expansion of `${ODOO_AI_KEY}` does not work reliably in Cline (values are taken literally) or
  OpenCode (a confirmed bug where `env` values never reach the spawned child process), and is not
  confirmed to work in Codex's TOML config or Windsurf. For these clients, paste the key value
  directly into the client's own local, non-versioned config file - never into anything you might
  commit.
- **Cloud sandboxes:** where a dedicated secret/vault mechanism exists (Cursor Runtime Secrets,
  GitHub Copilot Agents secrets), prefer it over a plain environment variable. Exception: Codex
  cloud secrets are removed before the agent phase starts, so if the key is needed at call time it
  has to be a plain (more visible) environment variable there, not a "secret".

## Network restriction: reverse proxy / VPN, not an in-app CIDR list

Source-IP restriction is handled outside the module, at your reverse proxy or VPN, not by an
in-app CIDR allowlist - an in-app check on `remote_addr` behind a reverse proxy without
`proxy_mode` would see the proxy's IP, not the real client, giving a false sense of security.

nginx:
```nginx
location /ai_assistant/ {
    allow 203.0.113.0/24;
    deny all;
}
```

Traefik:
```yaml
http:
  middlewares:
    odoo-ai-ipallow:
      ipAllowList:
        sourceRange:
          - "203.0.113.0/24"
```

If you run behind a reverse proxy, also set Odoo's `proxy_mode = True` so `remote_addr` (used in
both the audit log and the `hello` connection-alert text) reflects `X-Forwarded-For`, not the
proxy's own address.

## GDPR Art. 28 / Swiss revDSG

The module itself sends nothing anywhere - it is a passive read endpoint. The AI provider your
client talks to (Anthropic, OpenAI, Google, ...) is the one that receives the data your assistant
reads, and is the data processor under GDPR Art. 28 (or the analogous Swiss revDSG obligations) if
that data includes personal data of third parties. If you use the assistant to read customer or
contact data, check whether your AI provider offers a data processing agreement - typically
available on Team/Enterprise plans, typically **not** on individual/consumer plans. Using a
consumer plan to process third-party personal data is at your own risk; using it only on your own
data carries no such concern.

## Offboarding checklist

When someone leaves, do exactly these three steps:

1. **Deactivate the user in Odoo.** Their key becomes invalid immediately (native API key
   credential check includes `active`), and the module's own access record flips to `revoked`.
2. **Remove their seat in your AI provider's admin console**, if they were on a Team/Enterprise
   plan (an individual Pro/Max/Plus subscription is the departing person's own to cancel).
3. **Require deletion of the credentials file** (`odoo_status` shows its path) and `.mcp.json`
   from their machine as part of the offboarding process.

Data the assistant already read before step 1 is out of the company's reach - it lived only in
that person's own AI client session. This is why the model whitelist stays narrow (no HR/payroll
data by default) and why a Team/Enterprise plan with a DPA is recommended: it is a preventive
control, not something that can be fixed retroactively after the fact.
