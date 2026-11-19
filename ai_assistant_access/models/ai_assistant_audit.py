from datetime import timedelta

from odoo import api, fields, models

AUDIT_RETENTION_DAYS = 90


class AiAssistantAudit(models.Model):
    _name = "ai.assistant.audit"
    _description = "AI Assistant - Request Audit"
    _order = "id desc"

    user_id = fields.Many2one(
        "res.users",
        string="User",
        ondelete="set null",
        index=True,
        help="Owner of the key the request ran under.",
    )
    ts = fields.Datetime(
        string="Time",
        required=True,
        default=fields.Datetime.now,
        help="Time the request reached the endpoint.",
    )
    ip = fields.Char(string="IP address", help="Source IP address of the request.")
    op = fields.Char(string="Operation", help="Name of the read-only operation that was called.")
    model_name = fields.Char(string="Model", help="Odoo model the request was about.")
    domain_text = fields.Char(string="Domain", help="Request domain truncated to 512 characters.")
    field_names = fields.Char(
        string="Fields",
        size=512,
        help="Names of the fields a create or write request changed, without their values.",
    )
    record_count = fields.Integer(string="Records", help="Number of records returned.")
    duration_ms = fields.Integer(string="Duration (ms)", help="Time spent processing the request.")
    status = fields.Selection(
        [("ok", "OK"), ("error", "Error")],
        string="Result",
        required=True,
        default="ok",
        help="Whether the request succeeded or failed.",
    )
    error_code = fields.Char(string="Error code", help="Error code returned to the client.")

    @api.model
    def _gc_audit(self):
        limit = fields.Datetime.now() - timedelta(days=AUDIT_RETENTION_DAYS)
        self.sudo().search([("ts", "<", limit)]).unlink()
