#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_FIELDS: dict[str, list[str]] = {
    "res.partner": ["name", "company_name", "city", "country_id", "user_id"],
    "res.company": ["name", "currency_id"],
    "crm.lead": [
        "name",
        "partner_id",
        "stage_id",
        "user_id",
        "team_id",
        "expected_revenue",
        "probability",
    ],
    "crm.stage": ["name", "sequence"],
    "crm.team": ["name"],
    "sale.order": ["name", "partner_id", "state", "amount_total", "date_order"],
    "sale.order.line": ["order_id", "product_id", "name", "product_uom_qty", "price_subtotal"],
    "product.template": ["name", "list_price", "default_code", "categ_id"],
    "product.product": ["name", "list_price", "default_code", "categ_id"],
    "product.category": ["name"],
    "project.project": ["name", "user_id", "partner_id"],
    "project.task": ["name", "project_id", "user_ids", "stage_id"],
    "project.task.type": ["name"],
    "account.move": ["name", "partner_id", "state", "amount_total", "invoice_date", "move_type"],
    "account.move.line": ["move_id", "account_id", "name", "debit", "credit"],
    "mail.activity": ["summary", "activity_type_id", "date_deadline", "user_id"],
    "mail.message": ["subject", "body", "model", "res_id", "author_id", "date"],
}
FALLBACK_FIELDS = ["id", "display_name"]

MAX_FIELDS = 50
MAX_LIMIT = 200
DEFAULT_LIMIT = 80
TIMEOUT_SECONDS = 30.0

ERROR_MESSAGES = {
    "write_attempt": "Writing is not allowed, this access is read-only.",
    "model_not_allowed": "This model is not allowed for the AI assistant.",
    "unknown_op": "This operation is not supported.",
    "not_internal": "This account does not have internal access to Odoo.",
    "invalid_key": "The access key is invalid.",
    "expired": "The access key has expired, generate a new one in Preferences.",
    "revoked": "The access has been revoked.",
    "disabled": "The AI assistant is currently disabled in Odoo.",
    "company_not_allowed": "The company is outside your allowed companies.",
    "missing_fields": "The required parameter 'fields' is missing.",
    "too_many_fields": f"Too many fields, the maximum is {MAX_FIELDS}.",
    "rate_limited": "Rate limit exceeded for this key, try again in a moment.",
    "write_disabled": "Editing is disabled for this access, ask the Odoo administrator.",
    "model_not_writable": "Records of this model cannot be edited by the AI assistant.",
}

ACCESS_MODE_READ = "read"
ACCESS_MODE_READ_WRITE = "read_write"
CREDENTIALS_FORMAT = 1
CREDENTIALS_DIR_NAME = "odoo-ai"
CREDENTIALS_ENV = "ODOO_AI_CREDENTIALS"
ALLOW_HTTP_ENV = "ODOO_AI_ALLOW_HTTP"
UNSAFE_FILE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class OdooAIError(RuntimeError):
    pass


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _load_credentials_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise OdooAIError(f"The credentials file {path} could not be read.") from None
    if (
        not isinstance(data, dict)
        or data.get("format") != CREDENTIALS_FORMAT
        or not all(isinstance(data.get(name), str) for name in ("odoo_url", "db", "api_key"))
    ):
        raise OdooAIError(f"The credentials file {path} is not a valid odoo-ai credentials file.")
    return data


def _credentials() -> dict[str, Any]:
    raw_path = _env(CREDENTIALS_ENV)
    stored = _load_credentials_file(Path(raw_path)) if raw_path else {}
    return {
        "url": (_env("ODOO_URL") or stored.get("odoo_url", "")).strip().rstrip("/"),
        "db": _env("ODOO_DB") or stored.get("db", "").strip(),
        "key": _env("ODOO_AI_KEY") or stored.get("api_key", "").strip(),
        "login": stored.get("login"),
        "access_mode": stored.get("access_mode") or ACCESS_MODE_READ,
        "expires_at": stored.get("expires_at"),
        "path": raw_path or None,
    }


def _require_env() -> tuple[str, str, str]:
    credentials = _credentials()
    url, db, key = credentials["url"], credentials["db"], credentials["key"]
    missing = [
        name
        for name, value in (("ODOO_URL", url), ("ODOO_DB", db), ("ODOO_AI_KEY", key))
        if not value
    ]
    if missing:
        raise OdooAIError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Set {CREDENTIALS_ENV} to the credentials file or set the variables directly."
        )
    return url, db, key


def configured_access_mode() -> str:
    try:
        return _credentials()["access_mode"]
    except OdooAIError:
        return ACCESS_MODE_READ


def default_credentials_dir() -> Path:
    if sys.platform == "win32":
        base = _env("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = _env("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / CREDENTIALS_DIR_NAME


def default_credentials_path(url: str, db: str) -> Path:
    host = urlsplit(url).hostname or "odoo"
    name = UNSAFE_FILE_NAME_CHARS.sub("_", f"{host}_{db}")
    return default_credentials_dir() / f"{name}.json"


def write_credentials(path: Path, credentials: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(credentials, handle, indent=2)
    if os.name == "posix":
        os.chmod(path, 0o600)


def mcp_config_block(path: Path) -> dict[str, Any]:
    return {
        "mcpServers": {
            "odoo": {
                "command": sys.executable,
                "args": [str(Path(__file__).resolve())],
                "env": {CREDENTIALS_ENV: str(path)},
            }
        }
    }


def check_url_scheme(url: str, allow_http: bool = False) -> None:
    scheme = urlsplit(url).scheme
    if scheme == "https":
        return
    if scheme == "http" and (allow_http or _env(ALLOW_HTTP_ENV) == "1"):
        return
    raise OdooAIError(
        "The Odoo URL must start with https://. Plain http:// is only allowed for local tests "
        f"with --allow-http or {ALLOW_HTTP_ENV}=1."
    )


def _pair_error(response: httpx.Response) -> str:
    if response.status_code == 401:
        return (
            "Pairing failed: invalid or expired code. "
            "Generate a new prompt in Odoo under Preferences > AI Assistant."
        )
    try:
        error = response.json().get("error") or {}
        code = error.get("code") if isinstance(error, dict) else None
    except (ValueError, AttributeError):
        code = None
    if code in ERROR_MESSAGES:
        return f"Pairing failed: {ERROR_MESSAGES[code]}"
    return f"Pairing failed: Odoo returned an error (status {response.status_code})."


def pair(url: str, db: str, code: str, path: str | None = None, allow_http: bool = False) -> int:
    url = url.strip().rstrip("/")
    try:
        check_url_scheme(url, allow_http)
    except OdooAIError as error:
        print(f"Pairing failed: {error}", file=sys.stderr)
        return 1
    try:
        response = httpx.post(
            f"{url}/ai_assistant/v1/pair", json={"db": db, "code": code}, timeout=TIMEOUT_SECONDS
        )
    except httpx.HTTPError:
        print("Pairing failed: Could not connect to the Odoo server.", file=sys.stderr)
        return 1
    if not response.is_success:
        print(_pair_error(response), file=sys.stderr)
        return 1
    try:
        data = response.json()
        credentials = {
            "format": CREDENTIALS_FORMAT,
            "odoo_url": url,
            "db": db,
            "login": data["login"],
            "api_key": data["api_key"],
            "access_mode": data["access_mode"],
            "expires_at": data["expires_at"],
        }
    except (ValueError, KeyError, TypeError):
        print("Pairing failed: Odoo returned an unexpected response.", file=sys.stderr)
        return 1
    target = Path(path).expanduser() if path else default_credentials_path(url, db)
    write_credentials(target, credentials)
    print(f"Credentials saved to: {target}")
    print(f"Access mode: {credentials['access_mode']}, valid until {credentials['expires_at']}.")
    print("\nAdd this block to .mcp.json:\n")
    print(json.dumps(mcp_config_block(target), indent=2))
    instructions = data.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        print("\nAdd these assistant instructions to CLAUDE.md:\n")
        print(instructions)
    return 0


def _resolve_fields(model: str, fields: list[str] | None) -> list[str]:
    resolved = list(fields) if fields else DEFAULT_FIELDS.get(model, FALLBACK_FIELDS)
    return resolved[:MAX_FIELDS]


def _resolve_limit(limit: int | None) -> int:
    if not limit or limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def _translate_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 429:
        raise OdooAIError(ERROR_MESSAGES["rate_limited"])
    try:
        data = response.json()
    except ValueError:
        raise OdooAIError(
            f"Odoo returned an unexpected response (status {response.status_code})."
        ) from None
    if response.is_success:
        return data if isinstance(data, dict) else {"result": data}
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code and code in ERROR_MESSAGES:
        raise OdooAIError(ERROR_MESSAGES[code])
    message = error.get("message") if isinstance(error, dict) else None
    raise OdooAIError(message or f"Odoo returned an error (status {response.status_code}).")


def _call(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    url_base, db, key = _require_env()
    check_url_scheme(url_base)
    url = f"{url_base}/ai_assistant/v1/{op}"
    body: dict[str, Any] = {"db": db, **payload}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        raise OdooAIError("Could not connect to the Odoo server.") from None
    return _translate_response(response)


def odoo_hello() -> dict[str, Any]:
    """Announce yourself to the owner in Odoo (one inbox notification).

    Call once at the start of a session.
    """
    return _call("hello", {})


def odoo_models() -> dict[str, Any]:
    """List the Odoo models the AI assistant is allowed to read."""
    return _call("models", {})


def odoo_fields(model: str) -> dict[str, Any]:
    """List the readable fields of a model (binary and password or token fields excluded)."""
    return _call("fields_get", {"model": model})


def odoo_search(
    model: str,
    domain: list | None = None,
    fields: list[str] | None = None,
    limit: int | None = None,
    order: str | None = None,
) -> dict[str, Any]:
    """Search records of a model by domain and return the given fields.

    A PII-free set of fields is used when fields is omitted.
    """
    payload: dict[str, Any] = {
        "model": model,
        "domain": domain or [],
        "fields": _resolve_fields(model, fields),
        "limit": _resolve_limit(limit),
    }
    if order:
        payload["order"] = order
    return _call("search_read", payload)


def odoo_read(model: str, ids: list[int], fields: list[str] | None = None) -> dict[str, Any]:
    """Read specific records by ID."""
    if not ids:
        raise OdooAIError("Provide at least one ID.")
    payload = {"model": model, "ids": list(ids), "fields": _resolve_fields(model, fields)}
    return _call("read", payload)


def odoo_count(model: str, domain: list | None = None) -> dict[str, Any]:
    """Count the records matching a domain."""
    return _call("search_count", {"model": model, "domain": domain or []})


def odoo_group(
    model: str,
    groupby: list[str],
    domain: list | None = None,
    fields: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Group and aggregate records (e.g. sum of revenue by salesperson)."""
    if not groupby:
        raise OdooAIError("Provide at least one field to group by (groupby).")
    payload = {
        "model": model,
        "domain": domain or [],
        "groupby": list(groupby),
        "fields": list(fields) if fields else [],
        "limit": _resolve_limit(limit),
    }
    return _call("read_group", payload)


def odoo_link(model: str, id: int) -> dict[str, Any]:
    """Verify access to a record and return a working link to its form view in Odoo."""
    _call("read", {"model": model, "ids": [id], "fields": ["id"]})
    url_base, _db, _key = _require_env()
    return {"model": model, "id": id, "url": f"{url_base}/odoo/{model}/{id}"}


def odoo_status() -> dict[str, Any]:
    """Show which Odoo login, access mode and credentials file this assistant uses (no key)."""
    credentials = _credentials()
    return {
        "odoo_url": credentials["url"],
        "db": credentials["db"],
        "login": credentials["login"],
        "access_mode": credentials["access_mode"],
        "expires_at": credentials["expires_at"],
        "credentials_path": credentials["path"],
    }


def odoo_create(model: str, values: dict[str, Any]) -> dict[str, Any]:
    """Create one record from plain values; related lists, archiving and company stay locked."""
    if not values:
        raise OdooAIError("Provide at least one field value.")
    return _call("create", {"model": model, "values": dict(values)})


def odoo_write(model: str, ids: list[int], values: dict[str, Any]) -> dict[str, Any]:
    """Change plain values of up to 50 records by ID; related lists, archiving, company locked."""
    if not ids:
        raise OdooAIError("Provide at least one ID.")
    if not values:
        raise OdooAIError("Provide at least one field value.")
    return _call("write", {"model": model, "ids": list(ids), "values": dict(values)})


READ_TOOLS = (
    odoo_hello,
    odoo_models,
    odoo_fields,
    odoo_search,
    odoo_read,
    odoo_count,
    odoo_group,
    odoo_link,
    odoo_status,
)
WRITE_TOOLS = (odoo_create, odoo_write)


def build_server(access_mode: str) -> FastMCP:
    app = FastMCP("odoo-ai-assistant")
    tools = READ_TOOLS + (WRITE_TOOLS if access_mode == ACCESS_MODE_READ_WRITE else ())
    for tool in tools:
        app.add_tool(tool)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="odoo_ai_mcp.py")
    commands = parser.add_subparsers(dest="command")
    pair_parser = commands.add_parser("pair", help="Pair this machine with Odoo using a code.")
    pair_parser.add_argument(
        "--url", required=True, help="Odoo URL, e.g. https://odoo.example.com"
    )
    pair_parser.add_argument("--db", required=True, help="Odoo database name.")
    pair_parser.add_argument("--code", required=True, help="One-time pairing code from Odoo.")
    pair_parser.add_argument("--path", help="Custom path of the credentials file.")
    pair_parser.add_argument(
        "--allow-http", action="store_true", help="Allow plain http:// for local tests only."
    )
    args = parser.parse_args(argv)
    if args.command == "pair":
        return pair(args.url, args.db, args.code, args.path, args.allow_http)
    build_server(configured_access_mode()).run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
