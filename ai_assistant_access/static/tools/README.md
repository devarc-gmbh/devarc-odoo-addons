# odoo_ai_mcp.py

A single-file, stdio MCP server that gives Claude Code (or any other MCP client) access to your
own Odoo data through the `/ai_assistant/v1/<op>` endpoint. The server runs locally on your own
machine, under your own AI client session - Odoo never holds or calls any AI provider
credentials.

With read access the server never writes to Odoo except a single `odoo_hello()` call (see
below): search, read, count, group, list models/fields, and a record link helper. With read and
edit access (only when the administrator enabled editing) it also offers `odoo_create` and
`odoo_write`. Deleting is impossible in every mode.

## Install dependencies

```bash
pip install -r requirements.txt
```

Pins: `mcp>=1.2,<2` (the `FastMCP` API changed in `mcp` 2.x), `httpx>=0.27`.

## Pairing (recommended)

Preferences > AI Assistant gives you a prompt with a one-time pairing code (valid 10 minutes).
Your assistant runs:

```bash
python odoo_ai_mcp.py pair --url https://your-instance --db your_database --code=<code>
```

The script only talks to `https://` URLs; plain `http://` (local tests) needs `--allow-http` or
`ODOO_AI_ALLOW_HTTP=1`.

The script exchanges the code for your personal key and stores the credentials file itself:

| OS | Default path |
|---|---|
| Windows | `%APPDATA%\odoo-ai\<host>_<db>.json` |
| macOS | `~/Library/Application Support/odoo-ai/<host>_<db>.json` |
| Linux | `$XDG_CONFIG_HOME/odoo-ai/<host>_<db>.json` or `~/.config/odoo-ai/<host>_<db>.json` |

`--path` stores the file elsewhere. On Linux and macOS the file is readable only by you (0600),
on Windows it lives in your per-user profile. The key is never printed: the script prints the
file path, an `.mcp.json` block and the assistant instructions for your access mode.

```json
{
  "mcpServers": {
    "odoo": {
      "command": "/path/to/python",
      "args": ["/path/to/odoo_ai_mcp.py"],
      "env": {
        "ODOO_AI_CREDENTIALS": "/path/to/odoo-ai/your-instance_your_database.json"
      }
    }
  }
}
```

Restart your MCP client after changing `.mcp.json`.

## What the assistant can never do

Deleting, archiving (`active`), changing companies, one2many/many2many commands and binary fields
are rejected by Odoo in every mode. `odoo_create` and `odoo_write` exist only for read and edit
access and only work while the administrator keeps **Allow editing** on.

## Revoking and invalidating

Revoke the key in Preferences > AI Assistant > "Revoke access"; the credentials file becomes
useless and you can delete it (`odoo_status` shows its path).

## Advanced: environment variables (manual setup)

| Variable | Description |
|---|---|
| `ODOO_AI_CREDENTIALS` | Path to the credentials file written by `pair`. |
| `ODOO_URL` | Base URL of your Odoo instance, overrides the file. |
| `ODOO_DB` | Database name, overrides the file. |
| `ODOO_AI_KEY` | Your personal AI access key, overrides the key from the file. |

Without a credentials file set all three of `ODOO_URL`, `ODOO_DB` and `ODOO_AI_KEY`, and keep
`"ODOO_AI_KEY": "${ODOO_AI_KEY}"` in `.mcp.json` so the real key never lands in the file. If your
client isn't Claude Code, check [docs/clients.md](../../../docs/clients.md) at the repo root for
the equivalent config file and whether `${VAR}` expansion works for it at all.

## Tools

| Tool | Description |
|---|---|
| `odoo_models()` | List of Odoo models the assistant is allowed to read. |
| `odoo_fields(model)` | List of readable fields on a model (binary and password/token-like fields excluded). |
| `odoo_search(model, domain=None, fields=None, limit=None, order=None)` | Search records by domain. If `fields` is omitted, a PII-free default field set is used for that model. |
| `odoo_read(model, ids, fields=None)` | Read specific records by ID. |
| `odoo_count(model, domain=None)` | Count records matching a domain. |
| `odoo_group(model, groupby, domain=None, fields=None, limit=None)` | Group and aggregate records (e.g. sum of revenue by salesperson). |
| `odoo_link(model, id)` | Verify access to a record and return a working link to its form view in Odoo. |
| `odoo_status()` | Login, access mode, expiry and credentials file path (never the key). |
| `odoo_hello()` | Announce the assistant to its owner in Odoo (one inbox notification). Call once at the start of a session. |
| `odoo_create(model, values)` | Read and edit access only, while the administrator keeps Allow editing on: create one record from plain values. |
| `odoo_write(model, ids, values)` | Read and edit access only, while the administrator keeps Allow editing on: change plain values of up to 50 records. |

`odoo_create` and `odoo_write` are registered only when the credentials file says
`"access_mode": "read_write"`; the Odoo endpoint stays the authority and answers
`write_disabled` when the administrator switches editing off. Values must be text, numbers,
booleans or record IDs; lists of related records, `active` and company fields are rejected.

Limits enforced client-side (the endpoint enforces them too regardless): `fields` max 50 items,
`limit` max 200 records. If you don't pass `fields`, the server sends a predefined PII-free set
(e.g. for `res.partner`: `name`, `company_name`, `city`, `country_id`, `user_id` - not
`email`/`phone`/`mobile`/`vat`; you only get those if you explicitly ask for them in `fields`).

## Error messages

The server translates endpoint error codes into readable text (e.g. `write_attempt` ->
"Writing is not allowed, this access is read-only.", `model_not_allowed` -> "This model is not
allowed for the AI assistant.", HTTP 429 -> "Rate limit exceeded..."). Raw tracebacks, the key
value and the content of the credentials file are never printed.

## Running and tests

The server is started by your MCP client according to `.mcp.json` - nothing to run manually.

Pytest suite (mocks all HTTP calls, nothing goes over the network):

```bash
pip install pytest
python -m pytest tests/
```
