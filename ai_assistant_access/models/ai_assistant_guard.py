import re

AI_KEY_SCOPE = "ai_assistant_ro"
AI_KEY_SCOPE_RW = "ai_assistant_rw"
AI_KEY_NAME = "AI assistant"

ALLOWED_OPERATIONS = (
    "models",
    "fields_get",
    "search_read",
    "search_count",
    "read",
    "name_search",
    "read_group",
)

ALLOWED_WRITE_OPERATIONS = ("create", "write")

DEFAULT_WRITABLE_MODELS = ("crm.lead", "project.task", "mail.activity", "res.partner")

MAX_WRITE_IDS = 50

PROTECTED_WRITE_FIELDS = frozenset(
    {
        "id",
        "create_uid",
        "create_date",
        "write_uid",
        "write_date",
        "active",
        "company_id",
        "company_ids",
    }
)

NOT_WRITABLE_FIELD_TYPES = frozenset({"binary", "one2many", "many2many"})

MODEL_PROTECTED_WRITE_FIELDS = {
    "res.partner": frozenset({"email", "parent_id", "commercial_partner_id"}),
}

INDIRECT_PROTECTED_FIELDS = ("active", "company_id")

ACTIVITY_MODEL = "mail.activity"

WRITABLE_VALUE_TYPES = (str, int, float, bool, type(None))

MAX_FIELDS = 50
MAX_LIMIT = 200
MAX_REQUEST_BYTES = 256 * 1024
API_ROUTE_PREFIX = "/ai_assistant/v1/"
NUMERIC_FIELD_TYPES = frozenset({"integer", "float", "monetary"})
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_AUDIT_OP_LENGTH = 64
MAX_AUDIT_MODEL_LENGTH = 128

DOMAIN_LOGIC_OPERATORS = frozenset({"&", "|", "!"})

DOMAIN_SUBQUERY_OPERATORS = frozenset({"any", "not any"})

DOMAIN_FORBIDDEN_OPERATORS = frozenset({"any!", "not any!"})

UNSAFE_AUDIT_OP_CHARS = re.compile(r"[^A-Za-z0-9_]")

DEFAULT_FIELD_BLACKLIST_REGEX = (
    "password|passwd|secret|token|api_key|apikey|private_key|totp|oauth|signature"
)

DEFAULT_ALLOWED_MODELS = (
    "res.partner",
    "res.company",
    "crm.lead",
    "crm.stage",
    "crm.team",
    "sale.order",
    "sale.order.line",
    "product.template",
    "product.product",
    "product.category",
    "project.project",
    "project.task",
    "project.task.type",
    "account.move",
    "account.move.line",
    "mail.activity",
    "mail.message",
)

HARD_BLACKLIST_MODELS = frozenset(
    {
        "res.users",
        "res.users.apikeys",
        "res.users.settings",
        "ir.config_parameter",
        "ir.mail_server",
        "fetchmail.server",
        "ir.attachment",
        "ir.logging",
        "ir.model.access",
        "ir.rule",
        "ir.module.module",
        "ir.cron",
        "ir.default",
        "res.groups",
        "base.automation",
        "mail.template",
    }
)

HARD_BLACKLIST_PREFIXES = (
    "res.users.",
    "payment.",
    "bus.",
    "ai.assistant.",
    "auth_oauth",
    "auth_totp",
    "ir.mail_server.",
    "fetchmail.server.",
    "ir.actions",
    "ir.ui.",
    "ir.model",
    "res.groups.",
    "base.automation.",
    "mail.template.",
)

SCOPED_MESSAGE_MODEL = "mail.message"

SCOPED_MODEL_FIELDS = {SCOPED_MESSAGE_MODEL: "model", ACTIVITY_MODEL: "res_model"}

FIELD_DESCRIPTION_ATTRIBUTES = (
    "string",
    "type",
    "relation",
    "required",
    "readonly",
    "selection",
    "help",
)


class AiAssistantError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def is_hard_blacklisted(model_name):
    if model_name in HARD_BLACKLIST_MODELS:
        return True
    return any(model_name.startswith(prefix) for prefix in HARD_BLACKLIST_PREFIXES)


def parse_model_list(raw):
    parts = [part.strip() for part in re.split(r"[,;\s]+", raw or "") if part.strip()]
    return tuple(dict.fromkeys(parts))


def check_model_allowed(model_name, allowed_models):
    if not isinstance(model_name, str) or not model_name:
        raise AiAssistantError("bad_request", "Parameter 'model' is required.")
    if is_hard_blacklisted(model_name) or model_name not in allowed_models:
        raise AiAssistantError(
            "model_not_allowed",
            f"Model {model_name} is not allowed for the AI assistant.",
            403,
        )


def is_field_blacklisted(field_name, pattern):
    return bool(re.search(pattern, field_name, re.IGNORECASE))


def is_field_readable(model, field_name, pattern):
    field = model._fields.get(field_name)
    if field is None or field.type == "binary":
        return False
    return not is_field_blacklisted(field_name, pattern)


def filter_field_names(model, field_names, pattern):
    return [name for name in field_names if is_field_readable(model, name, pattern)]


def check_fields_param(fields):
    if not isinstance(fields, list) or not fields:
        raise AiAssistantError("missing_fields", "Parameter 'fields' is required.")
    if len(fields) > MAX_FIELDS:
        raise AiAssistantError("too_many_fields", f"The maximum number of fields is {MAX_FIELDS}.")
    if any(not isinstance(name, str) for name in fields):
        raise AiAssistantError("bad_request", "Parameter 'fields' must be a list of strings.")


def clamp_limit(limit):
    if limit in (None, False, ""):
        return MAX_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise AiAssistantError("bad_request", "Parameter 'limit' must be a number.") from None
    if value <= 0:
        return MAX_LIMIT
    return min(value, MAX_LIMIT)


def check_queryable_field(model, field_name, pattern):
    if not isinstance(field_name, str) or not field_name:
        raise AiAssistantError("bad_request", "The field name must be a non-empty string.")
    if "." in field_name:
        raise AiAssistantError(
            "bad_request",
            f"Related fields with a dot ({field_name}) are not allowed, "
            "read the target model with a separate query.",
        )
    if not is_field_readable(model, field_name, pattern):
        raise AiAssistantError("field_not_allowed", f"Field {field_name} is not readable.", 403)


def _is_domain_like(value):
    return isinstance(value, (list, tuple)) and any(
        isinstance(item, (list, tuple)) for item in value
    )


def _check_domain_value(model, field_name, operator, value, pattern, allowed_models):
    field = model._fields[field_name]
    if operator in DOMAIN_FORBIDDEN_OPERATORS:
        raise AiAssistantError("bad_request", f"Operator '{operator}' is not allowed.")
    if operator in DOMAIN_SUBQUERY_OPERATORS:
        if not field.relational:
            raise AiAssistantError(
                "bad_request", f"Operator '{operator}' needs a relational field."
            )
        check_model_allowed(field.comodel_name, allowed_models)
        check_domain(model.env[field.comodel_name], value, pattern, allowed_models)
        return
    if _is_domain_like(value):
        raise AiAssistantError(
            "bad_request", "A nested domain is only allowed with the any and not any operators."
        )
    items = value if isinstance(value, (list, tuple)) else [value]
    if any(not isinstance(item, WRITABLE_VALUE_TYPES) for item in items):
        raise AiAssistantError(
            "bad_request",
            f"The value for field {field_name} must be a plain value or a list of plain values.",
        )
    if field.relational and (
        field.comodel_name not in allowed_models or is_hard_blacklisted(field.comodel_name)
    ):
        if any(isinstance(i, (str, float)) for i in items):
            raise AiAssistantError(
                "model_not_allowed",
                f"Field {field_name} can only be compared with record IDs.",
                403,
            )


def check_domain(model, domain, pattern, allowed_models=()):
    if domain in (None, False):
        return []
    if not isinstance(domain, list):
        raise AiAssistantError("bad_request", "Parameter 'domain' must be a list.")
    for item in domain:
        if isinstance(item, str):
            if item not in DOMAIN_LOGIC_OPERATORS:
                raise AiAssistantError(
                    "bad_request", f"Unknown logical operator '{item}' in the domain."
                )
            continue
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise AiAssistantError(
                "bad_request",
                "A domain condition must have the form [field, operator, value].",
            )
        check_queryable_field(model, item[0], pattern)
        if not isinstance(item[1], str):
            raise AiAssistantError("bad_request", "The operator in a condition must be a string.")
        _check_domain_value(
            model, item[0], item[1].strip().lower(), item[2], pattern, allowed_models
        )
    return domain


def check_order(model, order, pattern):
    if order in (None, False, ""):
        return None
    if not isinstance(order, str):
        raise AiAssistantError("bad_request", "Parameter 'order' must be a string.")
    for term in order.split(","):
        parts = term.split()
        if not parts or len(parts) > 2:
            raise AiAssistantError(
                "bad_request", "Parameter 'order' must have the form 'field [asc|desc]'."
            )
        if len(parts) == 2 and parts[1].lower() not in ("asc", "desc"):
            raise AiAssistantError("bad_request", f"Unknown sort direction '{parts[1]}'.")
        check_queryable_field(model, parts[0], pattern)
    return order


def sanitize_audit_op(op):
    if not isinstance(op, str):
        return False
    return UNSAFE_AUDIT_OP_CHARS.sub("_", op)[:MAX_AUDIT_OP_LENGTH]


def sanitize_audit_model(model_name):
    if not isinstance(model_name, str):
        return False
    return model_name[:MAX_AUDIT_MODEL_LENGTH]


def scoped_domain(model_name, domain, allowed_models):
    scope_field = SCOPED_MODEL_FIELDS.get(model_name)
    if not scope_field:
        return domain
    allowed = [name for name in allowed_models if not is_hard_blacklisted(name)]
    return [(scope_field, "in", allowed)] + list(domain)


def build_config(allowed_models, field_pattern, writable_models=DEFAULT_WRITABLE_MODELS):
    return {
        "allowed_models": tuple(allowed_models),
        "field_pattern": field_pattern,
        "writable_models": tuple(writable_models),
    }


def run_operation(env, op, payload, config):
    if op not in ALLOWED_OPERATIONS:
        raise AiAssistantError("unknown_op", f"Operation {op} is not supported.")
    return _HANDLERS[op](env, payload, config)


def _model_of(env, payload, config):
    model_name = payload.get("model")
    check_model_allowed(model_name, config["allowed_models"])
    model = env.get(model_name)
    if model is None:
        raise AiAssistantError(
            "model_not_allowed",
            f"Model {model_name} does not exist in this instance.",
            403,
        )
    return model_name, model


def _readable_fields(model, payload, config):
    fields = payload.get("fields")
    check_fields_param(fields)
    filtered = filter_field_names(model, fields, config["field_pattern"])
    return filtered or ["id"]


def _order_of(model, payload, config):
    return check_order(model, payload.get("order"), config["field_pattern"])


def _op_models(env, payload, config):
    entries = []
    for model_name in config["allowed_models"]:
        if is_hard_blacklisted(model_name):
            continue
        model = env.get(model_name)
        if model is None:
            continue
        if not model.has_access("read"):
            continue
        entries.append({"model": model_name, "name": model._description})
    return {"models": entries}


def _op_fields_get(env, payload, config):
    model_name, model = _model_of(env, payload, config)
    descriptions = model.fields_get(attributes=list(FIELD_DESCRIPTION_ATTRIBUTES))
    readable = {
        name: description
        for name, description in descriptions.items()
        if is_field_readable(model, name, config["field_pattern"])
    }
    return {"model": model_name, "fields": readable}


def _domain_of(model, model_name, payload, config):
    return scoped_domain(
        model_name,
        check_domain(
            model, payload.get("domain"), config["field_pattern"], config["allowed_models"]
        ),
        config["allowed_models"],
    )


def _op_search_read(env, payload, config):
    model_name, model = _model_of(env, payload, config)
    domain = _domain_of(model, model_name, payload, config)
    fields = _readable_fields(model, payload, config)
    limit = clamp_limit(payload.get("limit"))
    records = model.search_read(
        domain, fields, limit=limit, order=_order_of(model, payload, config)
    )
    return {
        "model": model_name,
        "records": records,
        "length": len(records),
        "limit": limit,
    }


def _op_read(env, payload, config):
    model_name, model = _model_of(env, payload, config)
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(i, int) for i in ids):
        raise AiAssistantError("bad_request", "Parameter 'ids' must be a list of numbers.")
    if len(ids) > MAX_LIMIT:
        raise AiAssistantError("bad_request", f"The maximum number of IDs is {MAX_LIMIT}.")
    fields = _readable_fields(model, payload, config)
    domain = scoped_domain(model_name, [("id", "in", ids)], config["allowed_models"])
    records = model.search_read(domain, fields, limit=MAX_LIMIT)
    return {"model": model_name, "records": records, "length": len(records)}


def _op_search_count(env, payload, config):
    model_name, model = _model_of(env, payload, config)
    domain = _domain_of(model, model_name, payload, config)
    return {"model": model_name, "count": model.search_count(domain)}


def _op_name_search(env, payload, config):
    model_name, model = _model_of(env, payload, config)
    name = payload.get("name") or ""
    if not isinstance(name, str):
        raise AiAssistantError("bad_request", "Parameter 'name' must be a string.")
    domain = _domain_of(model, model_name, payload, config)
    results = model.name_search(name=name, args=domain, limit=clamp_limit(payload.get("limit")))
    return {"model": model_name, "results": [list(item) for item in results]}


def _op_read_group(env, payload, config):
    model_name, model = _model_of(env, payload, config)
    groupby = payload.get("groupby")
    if (
        not isinstance(groupby, list)
        or not groupby
        or any(not isinstance(g, str) for g in groupby)
    ):
        raise AiAssistantError("bad_request", "Parameter 'groupby' is required.")
    for spec in groupby:
        check_queryable_field(model, spec.split(":")[0], config["field_pattern"])
    measures = payload.get("fields") or []
    if not isinstance(measures, list) or any(not isinstance(m, str) for m in measures):
        raise AiAssistantError("bad_request", "Parameter 'fields' must be a list of strings.")
    for name in measures:
        check_queryable_field(model, name, config["field_pattern"])
        if model._fields[name].type not in NUMERIC_FIELD_TYPES:
            raise AiAssistantError("bad_request", f"Field {name} is not numeric and cannot be summed.")
    aggregates = ["__count"] + [f"{name}:sum" for name in measures]
    domain = _domain_of(model, model_name, payload, config)
    rows = model._read_group(
        domain, groupby=groupby, aggregates=aggregates, limit=clamp_limit(payload.get("limit"))
    )
    keys = list(groupby) + aggregates
    groups = [{key: _jsonify(value) for key, value in zip(keys, row, strict=True)} for row in rows]
    return {"model": model_name, "groupby": list(groupby), "groups": groups}


def _jsonify(value):
    if hasattr(value, "_name") and hasattr(value, "ids"):
        return [[record.id, record.display_name] for record in value]
    if isinstance(value, tuple):
        return list(value)
    return value


_HANDLERS = {
    "models": _op_models,
    "fields_get": _op_fields_get,
    "search_read": _op_search_read,
    "read": _op_read,
    "search_count": _op_search_count,
    "name_search": _op_name_search,
    "read_group": _op_read_group,
}


def run_write_operation(env, op, payload, config):
    if op not in ALLOWED_WRITE_OPERATIONS:
        raise AiAssistantError("unknown_op", f"Operation {op} is not supported.")
    return _WRITE_HANDLERS[op](env, payload, config)


def check_model_writable(model_name, config):
    check_model_allowed(model_name, config["allowed_models"])
    if model_name not in config["writable_models"]:
        raise AiAssistantError(
            "model_not_writable",
            f"Model {model_name} is not editable by the AI assistant.",
            403,
        )


def check_write_values(model, values, pattern, creating=False):
    if not isinstance(values, dict) or not values:
        raise AiAssistantError("bad_request", "Parameter 'values' must be a non-empty object.")
    if len(values) > MAX_FIELDS:
        raise AiAssistantError("too_many_fields", f"The maximum number of fields is {MAX_FIELDS}.")
    for field_name, value in values.items():
        field = model._fields.get(field_name) if isinstance(field_name, str) else None
        if (
            field is None
            or field_name in PROTECTED_WRITE_FIELDS
            or (not creating and field_name in MODEL_PROTECTED_WRITE_FIELDS.get(model._name, ()))
            or field.type in NOT_WRITABLE_FIELD_TYPES
            or not field.store
            or field.related
            or field.inverse
            or field.readonly
            or is_field_blacklisted(field_name, pattern)
        ):
            raise AiAssistantError(
                "field_not_writable", f"Field {field_name} cannot be changed by the AI assistant."
            )
        if not isinstance(value, WRITABLE_VALUE_TYPES):
            raise AiAssistantError(
                "bad_request", f"The value of field {field_name} must be a plain value."
            )
        if (
            field.type == "many2one"
            and value not in (None, False)
            and (isinstance(value, bool) or not isinstance(value, int))
        ):
            raise AiAssistantError(
                "bad_request", f"The value of field {field_name} must be a record ID or false."
            )
    return values


def _writable_model_of(env, payload, config):
    check_model_writable(payload.get("model"), config)
    return _model_of(env, payload, config)


def _is_model_allowed(model_name, config):
    return model_name in config["allowed_models"] and not is_hard_blacklisted(model_name)


def _check_activity_document(env, model_name, res_id):
    valid_id = isinstance(res_id, int) and not isinstance(res_id, bool) and res_id > 0
    document = env[model_name].browse(res_id if valid_id else [])
    if not document.exists():
        raise AiAssistantError("bad_request", "The activity must belong to an existing record.")
    document.check_access("write")


def _check_activity_target(model, records, values, config):
    if model._name != ACTIVITY_MODEL:
        return
    if any(not _is_model_allowed(name, config) for name in records.mapped("res_model")):
        raise AiAssistantError(
            "model_not_allowed", "The activity belongs to a model that is not allowed.", 403
        )
    if records and "res_model_id" not in values and "res_id" not in values:
        return
    target_model = None
    if not records or "res_model_id" in values:
        # ir.model is hard blacklisted for the owner, the lookup reads metadata only
        target = model.env["ir.model"].sudo().browse(values.get("res_model_id") or []).exists()
        if not target or not _is_model_allowed(target.model, config):
            raise AiAssistantError(
                "model_not_allowed", "Activities can only be linked to allowed models.", 403
            )
        target_model = target.model
    for record in records or [model.browse()]:
        res_id = values["res_id"] if "res_id" in values else record.res_id
        _check_activity_document(model.env, target_model or record.res_model, res_id)


def _check_partner_protected(partners):
    if not partners:
        return
    partners = partners.with_context(active_test=False)
    company_partners = (
        partners.env["res.company"]
        .sudo()
        .with_context(active_test=False)
        .search_count([("partner_id", "in", partners.ids)], limit=1)
    )
    if partners.user_ids or company_partners:
        raise AiAssistantError(
            "record_protected",
            "Contacts of users and companies cannot be changed by the AI assistant.",
            403,
        )


def _check_write_target(model, records, values, config):
    _check_activity_target(model, records, values, config)
    if "active" in model._fields and not all(records.mapped("active")):
        raise AiAssistantError(
            "record_archived", "Archived records cannot be changed by the AI assistant.", 403
        )
    if model._name == "res.partner":
        _check_partner_protected(records)
        synced = {*model._address_fields(), *model._commercial_fields(), "type", "is_company"}
        related = records if records and synced & set(values) else model.browse()
        if not records and values.get("parent_id"):
            related = model.browse(values["parent_id"])
        roots = related.sudo().with_context(active_test=False).exists()
        while roots.parent_id:
            roots = roots.parent_id | roots.filtered(lambda p: not p.parent_id)
        if roots:
            _check_partner_protected(roots.search([("id", "child_of", roots.ids)]))


def _protected_snapshot(records):
    names = [name for name in INDIRECT_PROTECTED_FIELDS if name in records._fields]
    return {record.id: tuple(record[name] for name in names) for record in records}


def _op_create(env, payload, config):
    model_name, model = _writable_model_of(env, payload, config)
    values = check_write_values(
        model, payload.get("values"), config["field_pattern"], creating=True
    )
    _check_write_target(model, model.browse(), values, config)
    record = model.create(values)
    env.flush_all()
    if ("active" in record._fields and not record.active) or (
        "company_id" in record._fields
        and record.company_id
        and record.company_id not in env.companies
    ):
        raise AiAssistantError(
            "protected_field_changed",
            "The values would archive the record or move it to another company.",
            403,
        )
    return {"model": model_name, "id": record.id}


def _op_write(env, payload, config):
    model_name, model = _writable_model_of(env, payload, config)
    ids = payload.get("ids")
    if (
        not isinstance(ids, list)
        or not ids
        or any(isinstance(i, bool) or not isinstance(i, int) for i in ids)
    ):
        raise AiAssistantError("bad_request", "Parameter 'ids' must be a list of numbers.")
    if len(ids) > MAX_WRITE_IDS:
        raise AiAssistantError("bad_request", f"The maximum number of IDs is {MAX_WRITE_IDS}.")
    values = check_write_values(model, payload.get("values"), config["field_pattern"])
    records = model.browse(ids)
    _check_write_target(model, records, values, config)
    before = _protected_snapshot(records)
    records.write(values)
    env.flush_all()
    if _protected_snapshot(records) != before:
        raise AiAssistantError(
            "protected_field_changed",
            "The change would archive, restore or move records to another company.",
            403,
        )
    return {"model": model_name, "ids": ids, "length": len(ids)}


_WRITE_HANDLERS = {
    "create": _op_create,
    "write": _op_write,
}
