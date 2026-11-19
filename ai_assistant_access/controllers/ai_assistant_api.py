import json
import logging
import re
import threading
import time
from collections import deque

import psycopg2

import odoo
from odoo import SUPERUSER_ID, api, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools import json_default, str2bool

from ..models import ai_assistant_guard
from ..models.ai_assistant_access import ACCESS_MODE_READ, ACCESS_MODE_READ_WRITE, STATE_PENDING
from ..models.ai_assistant_guard import (
    AI_KEY_SCOPE,
    AI_KEY_SCOPE_RW,
    ALLOWED_WRITE_OPERATIONS,
    API_ROUTE_PREFIX,
    DEFAULT_ALLOWED_MODELS,
    DEFAULT_FIELD_BLACKLIST_REGEX,
    DEFAULT_WRITABLE_MODELS,
    JSON_RESPONSE_HEADERS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    AiAssistantError,
)

_logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_PER_MIN = 60
PAIR_RATE_LIMIT_PER_MIN = 5
RATE_WINDOW_SECONDS = 60
MAX_RATE_BUCKETS = 10000
MAX_PAIR_RATE_BUCKETS = 1000
RATE_PURGE_INTERVAL_SECONDS = 1

_rate_lock = threading.Lock()
_rate_hits = {}
_pair_rate_hits = {}
_rate_purged_at = {}


class AiAssistantApi(http.Controller):
    @http.route(
        f"{API_ROUTE_PREFIX}<string:op>",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
        readonly=False,
        max_content_length=MAX_REQUEST_BYTES,
    )
    def dispatch(self, op, **kwargs):
        started = time.monotonic()
        entry = {
            "op": ai_assistant_guard.sanitize_audit_op(op),
            "ip": request.httprequest.remote_addr,
            "status": "error",
            "error_code": "internal_error",
            "authenticated": False,
        }
        status = 500
        payload = {"error": {"code": "internal_error", "message": "Unexpected error."}}
        try:
            body = self._read_payload()
            entry["model_name"] = ai_assistant_guard.sanitize_audit_model(body.get("model"))
            entry["domain_text"] = self._domain_text(body)
            result = self._run_op(op, body, entry)
            payload = result
            status = 200
            entry["status"] = "ok"
            entry["error_code"] = False
            entry["record_count"] = self._record_count(result)
        except AiAssistantError as error:
            status = error.status
            payload = {"error": {"code": error.code, "message": error.message}}
            entry["error_code"] = error.code
        except Exception:
            _logger.exception("AI assistant: request %s failed", entry["op"])
        entry["duration_ms"] = int((time.monotonic() - started) * 1000)
        serialized = self._serialize(payload)
        if serialized is None:
            status = 400
            entry["status"] = "error"
            entry["error_code"] = "response_too_large"
            serialized = json.dumps(
                {
                    "error": {
                        "code": "response_too_large",
                        "message": (
                            "The response is too large, narrow the query or lower the limit."
                        ),
                    }
                }
            )
        if entry.pop("authenticated"):
            self._write_audit(entry)
        else:
            _logger.warning(
                "AI assistant: unauthenticated request %s from %s rejected (%s)",
                entry["op"],
                entry["ip"],
                entry["error_code"],
            )
        return request.make_response(
            serialized,
            headers=JSON_RESPONSE_HEADERS,
            status=status,
        )

    def _run_op(self, op, body, entry):
        if op == "hello":
            return self._run_hello(body, entry)
        if op == "pair":
            return self._run_pair(body, entry)
        return self._run(op, body, entry, op in ALLOWED_WRITE_OPERATIONS)

    def _run(self, op, body, entry, write):
        key = self._precheck(body)
        if write:
            entry["field_names"] = self._field_names(body)
        cr = request.env.registry.cursor()
        try:
            if not write:
                # PostgreSQL blocks writes harder than the ORM: it also covers sudo() and raw SQL
                cr.execute("SET TRANSACTION READ ONLY")
            system_env = api.Environment(cr, SUPERUSER_ID, {})
            context = self._authorize(system_env, key, body, entry)
            if write and context["access_mode"] != ACCESS_MODE_READ_WRITE:
                raise AiAssistantError(
                    "write_disabled",
                    "Writing is disabled by the administrator or not granted to this access.",
                    403,
                )
            owner_env = api.Environment(
                cr,
                context["owner"].id,
                {"allowed_company_ids": context["company_ids"]},
            )
            if not write:
                return ai_assistant_guard.run_operation(owner_env, op, body, context["config"])
            result = ai_assistant_guard.run_write_operation(owner_env, op, body, context["config"])
            cr.commit()
            return result
        except psycopg2.errors.ReadOnlySqlTransaction:
            if write:
                raise
            raise AiAssistantError(
                "write_attempt",
                "This access is read-only, writing is not allowed.",
                403,
            ) from None
        except AccessError as error:
            raise AiAssistantError("access_denied", str(error), 403) from None
        except (UserError, ValidationError) as error:
            raise AiAssistantError("bad_request", str(error), 400) from None
        except psycopg2.errors.SerializationFailure:
            if not write:
                raise
            _logger.warning("AI assistant: %s hit a concurrent update", op)
            raise AiAssistantError(
                "write_conflict", "The record was changed at the same time, try again.", 409
            ) from None
        except (
            psycopg2.IntegrityError,
            psycopg2.DataError,
            psycopg2.ProgrammingError,
            ValueError,
        ) as error:
            if not write:
                _logger.warning(
                    "AI assistant: %s on %s rejected the query (%s)", op, entry.get("model_name"), type(error).__name__
                )
                raise AiAssistantError(
                    "bad_request", "The query is invalid, check the fields, groupby and domain.", 400
                ) from None
            if isinstance(error, psycopg2.ProgrammingError):
                raise
            _logger.warning("AI assistant: %s rejected the values (%s)", op, type(error).__name__)
            raise AiAssistantError(
                "bad_request", "The values are invalid or refer to a missing record.", 400
            ) from None
        finally:
            cr.rollback()
            cr.close()

    def _run_pair(self, body, entry):
        self._precheck(body, require_key=False)
        self._check_rate_limit(entry["ip"], PAIR_RATE_LIMIT_PER_MIN, pair=True)
        code = body.get("code")
        result = None
        if isinstance(code, str) and code:
            with request.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                access, key = env["ai.assistant.access"]._redeem_pairing_code(code)
                if key:
                    owner = access.user_id
                    entry["authenticated"] = True
                    entry["user_id"] = owner.id
                    result = {
                        "api_key": key,
                        "login": owner.login,
                        "access_mode": access.access_mode,
                        "expires_at": fields.Datetime.to_string(access.expires_at),
                        "odoo_url": env["ir.config_parameter"].sudo().get_param("web.base.url"),
                        "db": cr.dbname,
                        "instructions": owner._ai_assistant_claude_snippet(access.access_mode),
                    }
        if result is None:
            raise AiAssistantError("invalid_code", "Invalid or expired pairing code.", 401)
        return result

    def _run_hello(self, body, entry):
        key = self._precheck(body)
        try:
            with request.env.registry.cursor() as cr:
                system_env = api.Environment(cr, SUPERUSER_ID, {})
                context = self._authorize(system_env, key, body, entry)
                owner = context["owner"]
                partner = owner.partner_id.with_context(lang=owner.lang)
                if context["access_mode"] == ACCESS_MODE_READ_WRITE:
                    mode_label = partner.env._("read and edit")
                else:
                    mode_label = partner.env._("read-only")
                message = partner.env._(
                    "Your AI assistant just connected to Odoo with %(mode)s access "
                    "(from IP %(ip)s). If this was not you, revoke the key in "
                    "Preferences > AI Assistant.",
                    mode=mode_label,
                    ip=entry["ip"],
                )
                partner.message_notify(
                    subject=partner.env._("Your AI assistant connected"),
                    body=message,
                    author_id=system_env.ref("base.partner_root").id,
                    partner_ids=[owner.partner_id.id],
                )
        except AccessError as error:
            raise AiAssistantError("access_denied", str(error), 403) from None
        except (UserError, ValidationError) as error:
            raise AiAssistantError("bad_request", str(error), 400) from None
        return {"status": "ok"}

    def _precheck(self, body, require_key=True):
        # cheap checks first: they run on the dispatcher cursor until the key is verified
        params = api.Environment(request.env.cr, SUPERUSER_ID, {})["ir.config_parameter"].sudo()
        if request.httprequest.scheme != "https" and not str2bool(
            params.get_param("ai_assistant.allow_http"), False
        ):
            raise AiAssistantError("https_required", "An HTTPS connection is required.", 403)
        if not str2bool(params.get_param("ai_assistant.enabled"), False):
            raise AiAssistantError("disabled", "The AI assistant is disabled.", 503)
        database = body.get("db")
        if database and database != request.env.cr.dbname:
            raise AiAssistantError("db_not_allowed", "Unknown database.", 400)
        return self._bearer_key() if require_key else None

    def _authorize(self, env, key, body, entry):
        params = env["ir.config_parameter"].sudo()
        apikeys = env["res.users.apikeys"]
        uid = apikeys._check_credentials(scope=AI_KEY_SCOPE_RW, key=key)
        key_mode = ACCESS_MODE_READ_WRITE if uid else ACCESS_MODE_READ
        uid = uid or apikeys._check_credentials(scope=AI_KEY_SCOPE, key=key)
        if not uid or apikeys._check_credentials(scope="rpc", key=key):
            raise AiAssistantError("invalid_key", "Invalid access key.", 401)
        owner = env["res.users"].browse(uid)
        entry["authenticated"] = True
        entry["user_id"] = uid
        if not owner._is_internal():
            raise AiAssistantError("not_internal", "Only internal users have access.", 401)
        access = env["ai.assistant.access"].search(
            [("user_id", "=", uid)], order="id desc", limit=1
        )
        if not access or access.state == "revoked":
            raise AiAssistantError("revoked", "The access has been revoked.", 401)
        if access.state == STATE_PENDING:
            raise AiAssistantError("not_paired", "The access is still waiting for pairing.", 401)
        if access.state == "expired" or (
            access.expires_at and access.expires_at < fields.Datetime.now()
        ):
            raise AiAssistantError("expired", "The access has expired.", 401)
        if not access._matches_key(key):
            raise AiAssistantError("revoked", "The access has been revoked.", 401)
        self._check_rate_limit(access.id, self._rate_limit(params))
        entry["access_id"] = access.id
        writable_models = self._writable_models(params)
        editing = (
            key_mode == ACCESS_MODE_READ_WRITE
            and access.access_mode == ACCESS_MODE_READ_WRITE
            and bool(writable_models)
            and owner._ai_assistant_editing_allowed()
        )
        return {
            "owner": owner,
            "access_mode": ACCESS_MODE_READ_WRITE if editing else ACCESS_MODE_READ,
            "company_ids": self._company_ids(owner, body),
            "config": ai_assistant_guard.build_config(
                self._allowed_models(params),
                self._field_pattern(params),
                writable_models,
            ),
        }

    def _bearer_key(self):
        header = request.httprequest.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise AiAssistantError("invalid_key", "The Authorization header is missing.", 401)
        key = header[len("Bearer ") :].strip()
        if not key:
            raise AiAssistantError("invalid_key", "The access key is missing.", 401)
        return key

    def _company_ids(self, owner, body):
        allowed = owner.company_ids.ids
        requested = body.get("company_ids")
        if requested in (None, False, []):
            return allowed
        if not isinstance(requested, list) or any(not isinstance(i, int) for i in requested):
            raise AiAssistantError(
                "bad_request", "Parameter 'company_ids' must be a list of numbers."
            )
        if not set(requested).issubset(set(allowed)):
            raise AiAssistantError(
                "company_not_allowed", "The company is outside the user's allowed companies.", 400
            )
        return requested

    def _allowed_models(self, params):
        raw = params.get_param("ai_assistant.allowed_models")
        if raw is False or raw is None:
            return DEFAULT_ALLOWED_MODELS
        return ai_assistant_guard.parse_model_list(raw)

    def _writable_models(self, params):
        raw = params.get_param("ai_assistant.writable_models")
        if raw is False or raw is None:
            return DEFAULT_WRITABLE_MODELS
        return ai_assistant_guard.parse_model_list(raw)

    def _field_pattern(self, params):
        raw = (params.get_param("ai_assistant.field_blacklist_regex") or "").strip()
        if not raw:
            return DEFAULT_FIELD_BLACKLIST_REGEX
        combined = f"(?:{DEFAULT_FIELD_BLACKLIST_REGEX})|(?:{raw})"
        try:
            re.compile(raw)
            re.compile(combined)
        except re.error:
            _logger.warning(
                "Invalid ai_assistant.field_blacklist_regex, falling back to the default"
            )
            return DEFAULT_FIELD_BLACKLIST_REGEX
        return combined

    def _rate_limit(self, params):
        try:
            return int(params.get_param("ai_assistant.rate_limit_per_min"))
        except (TypeError, ValueError):
            return DEFAULT_RATE_LIMIT_PER_MIN

    def _drop_old_hits(self, hits, now):
        while hits and now - hits[0] > RATE_WINDOW_SECONDS:
            hits.popleft()

    def _purge_rate_hits(self, table, pair, now):
        if now - _rate_purged_at.get(pair, float("-inf")) < RATE_PURGE_INTERVAL_SECONDS:
            return
        _rate_purged_at[pair] = now
        for bucket in list(table):
            self._drop_old_hits(table[bucket], now)
            if not table[bucket]:
                del table[bucket]

    def _check_rate_limit(self, bucket, limit_per_min, pair=False):
        if limit_per_min <= 0:
            return
        table, max_buckets = (
            (_pair_rate_hits, MAX_PAIR_RATE_BUCKETS) if pair else (_rate_hits, MAX_RATE_BUCKETS)
        )
        now = time.monotonic()
        with _rate_lock:
            if bucket not in table and len(table) >= max_buckets:
                self._purge_rate_hits(table, pair, now)
                if len(table) >= max_buckets:
                    raise AiAssistantError(
                        "rate_limited", "Too many clients at once, try again in a moment.", 429
                    )
            hits = table.setdefault(bucket, deque())
            self._drop_old_hits(hits, now)
            if len(hits) >= limit_per_min:
                raise AiAssistantError(
                    "rate_limited", "Request rate limit for this key exceeded.", 429
                )
            hits.append(now)

    def _read_payload(self):
        raw = request.httprequest.get_data(as_text=True)
        if len(raw) > MAX_REQUEST_BYTES:
            raise AiAssistantError("bad_request", "The request body is too large.")
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            raise AiAssistantError("bad_request", "The request body is not valid JSON.") from None
        if not isinstance(payload, dict):
            raise AiAssistantError("bad_request", "The request body must be a JSON object.")
        return payload

    def _serialize(self, payload):
        body = json.dumps(payload, default=json_default, ensure_ascii=False)
        if len(body.encode("utf-8")) > MAX_RESPONSE_BYTES:
            return None
        return body

    def _domain_text(self, body):
        domain = body.get("domain")
        if domain in (None, False):
            return False
        return json.dumps(domain, default=json_default, ensure_ascii=False)[:512]

    def _field_names(self, body):
        values = body.get("values")
        if not isinstance(values, dict):
            return False
        names = [name for name in values if isinstance(name, str)]
        return ",".join(sorted(names))[:512] or False

    def _record_count(self, result):
        if not isinstance(result, dict):
            return 0
        if isinstance(result.get("id"), int):
            return 1
        for key in ("length", "count"):
            if isinstance(result.get(key), int):
                return result[key]
        for key in ("models", "groups", "results"):
            if isinstance(result.get(key), list):
                return len(result[key])
        return len(result.get("fields", ()))

    def _write_audit(self, entry):
        access_id = entry.pop("access_id", None)
        self._create_audit(entry)
        if access_id and entry.get("status") == "ok":
            self._touch_access(access_id)

    def _create_audit(self, entry):
        try:
            with request.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env["ai.assistant.audit"].create(
                    {
                        "user_id": entry.get("user_id"),
                        "ip": entry.get("ip"),
                        "op": entry.get("op"),
                        "model_name": entry.get("model_name"),
                        "domain_text": entry.get("domain_text"),
                        "field_names": entry.get("field_names"),
                        "record_count": entry.get("record_count") or 0,
                        "duration_ms": entry.get("duration_ms") or 0,
                        "status": entry.get("status"),
                        "error_code": entry.get("error_code"),
                    }
                )
        except Exception:
            _logger.exception("AI assistant: the audit record could not be written")

    def _touch_access(self, access_id):
        try:
            with request.env.registry.cursor() as cr:
                # read committed lets Postgres reapply the increment after a concurrent update, test cursors share an open transaction
                if not odoo.modules.module.current_test:
                    cr.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                env = api.Environment(cr, SUPERUSER_ID, {})
                env["ai.assistant.access"].browse(access_id)._record_usage()
        except Exception:
            _logger.exception("AI assistant: the access usage could not be updated")
