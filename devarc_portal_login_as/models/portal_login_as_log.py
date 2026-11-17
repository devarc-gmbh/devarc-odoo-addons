from datetime import timedelta

from odoo import api, fields, http, models
from odoo.exceptions import AccessError, UserError

from .res_users import VIEW_AS_SESSION_KEY

VIEW_AS_DURATION = timedelta(minutes=30)


class DevarcPortalLoginAsLog(models.Model):
    _name = "devarc.portal.login.as.log"
    _description = "Portal View-As Log"
    _inherit = ["mail.thread"]
    _order = "start_at desc, id desc"

    origin_user_id = fields.Many2one(
        "res.users",
        string="Viewed by",
        readonly=True,
        index=True,
        ondelete="set null",
        help="Internal user who viewed the portal as the customer. Empty once the user is deleted.",
    )
    origin_name = fields.Char(
        string="Viewed by (name)",
        readonly=True,
        help="Name of the internal user when the view-as session started, kept after the user is deleted.",
    )
    target_user_id = fields.Many2one(
        "res.users",
        string="Portal user",
        readonly=True,
        index=True,
        ondelete="set null",
        help="Portal user whose portal was viewed. Empty once the user is deleted.",
    )
    target_name = fields.Char(
        string="Portal user (name)",
        readonly=True,
        help="Name of the portal user when the view-as session started, kept after the user is deleted.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        readonly=True,
        index=True,
        help="Commercial partner of the portal user when the view-as session started.",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        index=True,
        default=lambda self: self.env.company,
        help="Company of the user who started the view-as session.",
    )
    reason = fields.Text(
        required=True,
        readonly=True,
        help="Why the portal was viewed as the customer.",
    )
    start_at = fields.Datetime(
        string="Started at",
        required=True,
        readonly=True,
        default=fields.Datetime.now,
        help="When the view-as session started.",
    )
    expires_at = fields.Datetime(
        string="Expires at",
        compute="_compute_expires_at",
        store=True,
        precompute=True,
        required=True,
        readonly=True,
        help="When the view-as session ends automatically.",
    )
    end_at = fields.Datetime(
        string="Ended at",
        readonly=True,
        copy=False,
        help="When the view-as session ended.",
    )
    state = fields.Selection(
        [("active", "Active"), ("ended", "Ended")],
        required=True,
        default="active",
        readonly=True,
        tracking=True,
        copy=False,
        help="Active while the view-as session may still be used.",
    )
    end_reason = fields.Selection(
        [
            ("returned", "Returned"),
            ("logout", "Logout"),
            ("expired", "Expired"),
            ("stopped_by_admin", "Stopped by admin"),
            ("fingerprint_mismatch", "Origin user changed"),
            ("session_changed", "Session changed"),
        ],
        readonly=True,
        tracking=True,
        copy=False,
        help="How the view-as session ended.",
    )
    ip_address = fields.Char(
        string="IP address",
        readonly=True,
        help="IP address the view-as session was started from.",
    )
    user_agent = fields.Char(
        readonly=True,
        help="Browser that started the view-as session.",
    )
    session_identifier = fields.Char(
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="Identifier of the browser session used for the view-as session, deleted when the session ends.",
    )
    is_expired = fields.Boolean(
        compute="_compute_is_expired",
        help="The session is still active but past its expiry time.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        users = self.env["res.users"].sudo()
        for vals in vals_list:
            origin = users.browse(vals.get("origin_user_id"))
            target = users.browse(vals.get("target_user_id"))
            vals.setdefault("origin_name", origin.name)
            vals.setdefault("target_name", target.name)
            vals.setdefault("partner_id", target.partner_id.commercial_partner_id.id)
        return super().create(vals_list)

    @api.depends("origin_name", "target_name")
    def _compute_display_name(self):
        for log in self:
            log.display_name = f"{log.origin_name} / {log.target_name}"

    @api.depends("start_at")
    def _compute_expires_at(self):
        for log in self:
            log.expires_at = log.start_at + VIEW_AS_DURATION

    @api.depends("state", "expires_at")
    def _compute_is_expired(self):
        now = fields.Datetime.now()
        for log in self:
            log.is_expired = log.state == "active" and bool(log.expires_at) and log.expires_at < now

    @api.model
    def _from_view_as_state(self, state):
        return self.sudo().browse(state["log_id"]).exists()

    @api.model
    def _from_session(self, session):
        state = session.get(VIEW_AS_SESSION_KEY)
        log = self._from_view_as_state(state) if state else self.browse()
        if log._is_running() and log.target_user_id.id == session.uid:
            return log
        return self.browse()

    def _is_running(self):
        return self.state == "active" and fields.Datetime.now() <= self.expires_at

    def _session_end_reason(self, uid):
        if self._is_running() and self.target_user_id.id != uid:
            return "session_changed"
        return "expired"

    def _close(self, end_reason):
        to_close = self.filtered(lambda log: log.state == "active")
        now = fields.Datetime.now()
        for log in to_close:
            log.write({
                "state": "ended",
                "end_reason": end_reason,
                "end_at": min(now, log.expires_at),
            })
        to_close._delete_view_as_sessions()
        return to_close

    def _delete_view_as_sessions(self):
        identifiers = [identifier for identifier in self.sudo().mapped("session_identifier") if identifier]
        if identifiers:
            http.root.session_store.delete_from_identifiers(identifiers)

    @api.model
    def _cron_close_expired(self):
        expired = self.sudo().search([
            ("state", "=", "active"),
            ("expires_at", "<", fields.Datetime.now()),
        ])
        expired._close("expired")

    def action_stop(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only administrators can stop a view-as session."))
        if self.state != "active":
            raise UserError(self.env._("This view-as session has already ended."))
        self._close("stopped_by_admin")
        return True
