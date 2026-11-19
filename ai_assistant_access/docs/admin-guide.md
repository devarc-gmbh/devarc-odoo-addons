# Administrator guide

## Settings

**Settings > General Settings > "AI Assistant" block** (`ir.config_parameter`, see
`models/res_config_settings.py` and `data/ir_config_parameter.xml`):

| Setting | Parameter | Default | Description |
|---|---|---|---|
| AI assistant enabled | `ai_assistant.enabled` | `True` | Global kill-switch; when off, the endpoint returns `503` on every request. Existing keys are unaffected and work again once re-enabled. |
| Allowed models | `ai_assistant.allowed_models` | `res.partner, res.company, crm.lead, crm.stage, crm.team, sale.order, sale.order.line, product.template, product.product, product.category, project.project, project.task, project.task.type, account.move, account.move.line, mail.activity, mail.message` | Comma-separated whitelist. The hard-coded blacklist in `models/ai_assistant_guard.py` cannot be overridden through this setting. Models not installed in the instance are silently skipped in the `models` operation. |
| Key validity | `ai_assistant.key_days` | `90` | Validity of a newly generated key; capped at 365 days in code regardless of a higher value here. |
| Request limit | `ai_assistant.rate_limit_per_min` | `60` | Per-key rate limit; `0` disables it. |
| Allow HTTP | `ai_assistant.allow_http` | `False` | Local development only - must stay off in production. HTTPS is otherwise required, `403 https_required` on plain HTTP. |
| Field blacklist | `ai_assistant.field_blacklist_regex` | `password\|passwd\|secret\|token\|api_key\|apikey\|private_key\|totp\|oauth\|signature` | Field names matching this pattern are never returned, in any operation, including inside `domain`/`groupby`/aggregates. A custom value extends the default pattern, it never replaces it. |

![AI Assistant block in General Settings](../ai_assistant_access/static/description/images/05-settings.png)

There is no in-app IP allowlist setting - see "Restrict source IPs" below for why, and what to use
instead.

### Hard blacklist (code, not editable from Settings)

`models/ai_assistant_guard.py` defines a hard blacklist checked *before* the admin whitelist:
`res.users`, `res.users.apikeys`, `res.users.settings`, `ir.config_parameter`, `ir.mail_server`,
`fetchmail.server`, `ir.attachment`, `ir.logging`, `ir.model.access`, `ir.rule`,
`ir.module.module`, plus every model whose name starts with `res.users.`, `payment.`, `bus.`,
`ai.assistant.`, `auth_oauth`, `auth_totp`, `ir.mail_server.`, `fetchmail.server.`, `ir.actions`,
`ir.ui.`, `ir.model`, plus `ir.cron`, `ir.default`, `res.groups*`, `base.automation*` and
`mail.template*`. These are the models that could leak credentials, integration secrets, run code
or change the security mechanism itself - adding them to "Allowed models" has no effect.

`mail.message` and `mail.activity` are on the default whitelist, but every request against them
gets an extra `('model', 'in', <allowed models>)` (`res_model` for activities) condition injected
server-side, so chatter messages and activities of models outside the whitelist never leak through
them. Activities can only be created or changed on documents of allowed models.

### Fields in domain, groupby and aggregates

Every field referenced in `domain`, `groupby`, or aggregated `fields` goes through the same check
as a directly-read field: dotted paths (`partner_id.vat`) are rejected with `400 bad_request`
(without this, a domain could bypass the model whitelist and turn `search_count` into a boolean
oracle for a secret field), and a bare field name still has to pass the field blacklist regex and
must not be binary, or it's rejected with `403 field_not_allowed`. The `any` and `not any`
operators are only accepted on relations to allowed models and their nested domain goes through the
same checks; `any!`/`not any!` and nested domains with any other operator are rejected.

## Editing (Allow editing)

Off by default. **Settings > AI Assistant > Allow editing** (`ai_assistant.allow_edit`) lets users
generate read and edit access:

- While **Allow editing** is on, read and edit keys can `create` (1 record) and `write` (up to 50
  ids) only in **Writable models** (`ai_assistant.writable_models`, default
  `crm.lead,project.task,mail.activity,res.partner`) that are also in the readable models and not
  in the hard blacklist; writes run as the user, with their ACL and record rules, never with sudo.
  An empty **Editable models** value turns editing off (`write_disabled`), a deleted parameter
  falls back to the default list.
- Even while **Allow editing** is on, `unlink` does not exist, and `active`, `company_id`,
  `company_ids`, one2many/many2many values, binary fields and fields matching the field blacklist
  are rejected (`field_not_writable`). The audit stores field names, never values.
- Only stored fields that are not related, readonly or inverse are writable; stored computed
  fields editable in the UI (`crm.lead` `stage_id`/`team_id`, `project.task` `stage_id`/`project_id`)
  are. On existing contacts `email` and `parent_id` cannot be changed, a new contact may set them,
  but not under a protected parent. Archived records and contacts of users or companies cannot be
  changed, an activity can only be moved to a record the user may edit, and a write or create
  that would archive, restore or move a record to another company is rolled back
  (`protected_field_changed`). Values the database rejects return 400 `bad_request`, a concurrent
  update returns 409 `write_conflict`.
- Turning **Allow editing** off takes effect on the next request: read and edit keys keep reading
  and every write returns 403 `write_disabled`, no key is revoked.

## Pairing endpoint

`POST /ai_assistant/v1/pair` is unauthenticated: it accepts a 12-character code that is stored
only as a sha256 hash, expires after 10 minutes and works once. It is rate limited to 5 requests
per minute per IP; the limiter is per worker, like the read rate limit. Each successful pairing
writes an audit line `pair` without the code.

## Kill-switch

- **Global:** Settings > AI Assistant > "AI assistant enabled". While off, every request gets
  `503 disabled`; keys stay valid and work again as soon as it's re-enabled.
- **Per user:** on the user's form (Settings > Users), "AI Assistant" tab, "Revoke access" button -
  available to the owner and to admins (`base.group_system`). Admins can only view and revoke a
  key on someone else's account, never generate one - key generation is restricted to
  `self == env.user` so the plaintext key is never shown to anyone but its owner.
- **Automatic on deactivation:** deactivating a user (`active = False`) immediately revokes their
  active access.

## Expiration

Default key validity is 90 days (`ai_assistant.key_days`), hard-capped at 365. Enforced twice: the
endpoint checks `expires_at` on every request, and a daily cron
(`ir_cron_ai_assistant_maintenance`) flips overdue access records to `expired` and removes the
underlying native API key. The same cron also garbage-collects old audit records (below).

## Audit

**Settings > Technical > "AI Assistant Audit"** (`base.group_system` only, read-only list) shows
every *authenticated* request, successful or not - including blocked write attempts and unknown
operations. Fields: user, timestamp, IP, operation, model, a domain truncated to 512 characters,
record count, duration, result, error code. Unauthenticated attempts (bad/missing key) are never
written here, only to the server log, so an attacker cannot use them to fill the database.

Prompt content and returned data are never stored - only request metadata. Records older than 90
days are removed automatically by the same daily cron.

## Restrict source IPs (reverse proxy / VPN, not an in-app setting)

There is no CIDR allowlist inside the module - checking `remote_addr` in the application layer is
unreliable behind a reverse proxy (it sees the proxy's address unless `proxy_mode` is set), so IP
restriction is handled where it actually works:

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

If Odoo runs behind a reverse proxy, set `proxy_mode = True` in `odoo.conf` so the real client IP
(used both in the audit log and in the `hello` connection-alert text) comes from
`X-Forwarded-For` instead of the proxy's own address. A VPN in front of the endpoint is a simpler
alternative to a per-IP allowlist for a small user base.

## Rate limit

Default 60 requests/minute per key (`0` disables it). The implementation is in-memory and counted
**per Odoo worker process**, not globally across a cluster - with multiple workers or servers, the
effective limit is a multiple of the configured value. This is a known, accepted trade-off, not a
bug. Exceeding it returns `429 rate_limited`.

## Response limits

Max 50 fields per request, max 200 records (`limit` is clamped even if the client asks for more).
Request body capped at 256 KB (`413 request_too_large`, rejected before the body is read), response
body capped at 1 MB (`400 response_too_large` instead of a silently truncated response).

## Offboarding checklist

1. **Deactivate the user in Odoo.** Their key becomes invalid immediately and their access record
   flips to `revoked`.
2. **Remove their seat in your AI provider's admin console**, if they were on a Team/Enterprise
   plan.
3. **Require deletion of the credentials file** (`odoo_status` shows its path) and `.mcp.json`
   from their machine.

Full rationale and the "data already read is out of reach" caveat: [docs/security.md](security.md#offboarding-checklist).

## Troubleshooting - error codes

Source: `controllers/ai_assistant_api.py`, `models/ai_assistant_guard.py`.

| HTTP | Code | When |
|---|---|---|
| 401 | `invalid_key` | Missing/invalid `Authorization: Bearer` header, or the key does not have the `ai_assistant_ro` scope (e.g. a regular RPC key with scope `rpc`). |
| 401 | `not_internal` | The key's owner is not an internal user (e.g. a portal user). |
| 401 | `revoked` | Access does not exist or is in state `revoked`. |
| 401 | `expired` | Access is in state `expired`, or `expires_at` is in the past. |
| 403 | `https_required` | Request came over plain HTTP and `ai_assistant.allow_http` is off. |
| 403 | `model_not_allowed` | Model is hard-blacklisted, not in `ai_assistant.allowed_models`, or does not exist in this instance. |
| 403 | `write_attempt` | The operation tried to write; PostgreSQL's `ReadOnlySqlTransaction` caught it. |
| 403 | `field_not_allowed` | A field in `domain`/`groupby`/an aggregate is blacklisted, binary, or does not exist on the model. |
| 403 | `access_denied` | Odoo `AccessError` - the owner's ACL/record rules rejected the request. |
| 400 | `unknown_op` | Operation outside the 7 (+`hello`) whitelist (e.g. `execute_kw`, `write`, `action_confirm`). |
| 400 | `bad_request` | Invalid/missing parameter, a dotted field in `domain`/`groupby`, a non-numeric `read_group` measure, a malformed domain condition, or invalid JSON. |
| 400 | `missing_fields` / `too_many_fields` | `fields` is missing, or has more than 50 entries. |
| 400 | `company_not_allowed` | `company_ids` in the request is not a subset of the owner's companies. |
| 400 | `db_not_allowed` | The `db` parameter in the body does not match the actual database. |
| 400 | `response_too_large` | Serialized response exceeded 1 MB. |
| 413 | `request_too_large` | Request body exceeded 256 KB. |
| 409 | `write_conflict` | A `create`/`write` collided with a concurrent update; retry. |
| 429 | `rate_limited` | `ai_assistant.rate_limit_per_min` exceeded for this key. |
| 503 | `disabled` | Global kill-switch `ai_assistant.enabled` is off. |
| 500 | `internal_error` | Unexpected error outside the above; detail stays in the Odoo log, no traceback leaves the response. |
