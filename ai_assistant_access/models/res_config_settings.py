from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ai_assistant_enabled = fields.Boolean(
        string="AI assistant enabled",
        config_parameter="ai_assistant.enabled",
        help="Global kill switch; once off the endpoint returns 503 and the keys stay in place.",
    )
    ai_assistant_allowed_models = fields.Char(
        string="Allowed models",
        config_parameter="ai_assistant.allowed_models",
        help="Comma separated list of models; the hard blacklist in the code cannot be bypassed.",
    )
    ai_assistant_key_days = fields.Integer(
        string="Key validity (days)",
        config_parameter="ai_assistant.key_days",
        help="Number of days a newly generated key stays valid.",
    )
    ai_assistant_rate_limit_per_min = fields.Integer(
        string="Requests per minute",
        config_parameter="ai_assistant.rate_limit_per_min",
        help="Maximum number of requests per key and minute; 0 disables the limit.",
    )
    ai_assistant_allow_http = fields.Boolean(
        string="Allow HTTP",
        config_parameter="ai_assistant.allow_http",
        help="Local development only; must stay off in production.",
    )
    ai_assistant_allow_edit = fields.Boolean(
        string="Allow editing",
        config_parameter="ai_assistant.allow_edit",
        help="Lets users generate a read and edit access; once off, existing read and edit "
        "keys fall back to read only.",
    )
    ai_assistant_writable_models = fields.Char(
        string="Editable models",
        config_parameter="ai_assistant.writable_models",
        help="Comma separated models the assistant may create and edit; they must also be "
        "allowed models and deleting is never possible.",
    )
    ai_assistant_field_blacklist_regex = fields.Char(
        string="Field blacklist (regex)",
        config_parameter="ai_assistant.field_blacklist_regex",
        help="Regular expression on field names that are never returned.",
    )

    @api.model
    def _get_classified_fields(self, fnames=None):
        classified = super()._get_classified_fields(fnames)
        keep = self.env.context.get("ai_assistant_keep_empty_models")
        if keep:
            classified["config"] = [
                (name, key) for name, key in classified["config"] if name not in keep
            ]
        return classified

    def set_values(self):
        empty = {
            name: key
            for name, key in (
                ("ai_assistant_allowed_models", "ai_assistant.allowed_models"),
                ("ai_assistant_writable_models", "ai_assistant.writable_models"),
            )
            if not (self[name] or "").strip()
        }
        if not empty:
            return super().set_values()
        result = super(
            ResConfigSettings, self.with_context(ai_assistant_keep_empty_models=list(empty))
        ).set_values()
        params = self.env["ir.config_parameter"].sudo()
        for key in empty.values():
            params.set_str(key, " ")
        return result
