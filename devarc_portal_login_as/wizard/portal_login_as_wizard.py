from markupsafe import Markup

from odoo import api, fields, models
from odoo.addons.base.models.res_users import check_identity
from odoo.exceptions import UserError
from odoo.http import request

from ..models.portal_login_as_log import VIEW_AS_DURATION
from ..models.res_users import VIEW_AS_SESSION_KEY


class DevarcPortalLoginAsWizard(models.TransientModel):
    _name = "devarc.portal.login.as.wizard"
    _description = "View Portal as Customer"

    target_user_id = fields.Many2one(
        "res.users",
        string="Portal user",
        required=True,
        ondelete="cascade",
        domain=[("share", "=", True), ("active", "=", True)],
        help="Portal user whose portal you want to see.",
    )
    reason = fields.Text(
        required=True,
        help="Why you need to see the portal as this customer. Stored in the audit log.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if request:
            request.session.pop("identity-check-last", None)
        return super().create(vals_list)

    @check_identity
    def action_start(self):
        self.ensure_one()
        if request.session.get(VIEW_AS_SESSION_KEY):
            raise UserError(self.env._("You are already viewing the portal as a customer. Return first."))
        origin = self.env.user
        target = self.target_user_id
        origin._check_can_view_as(target)
        log = self._create_view_as_log(origin, target)
        self._notify_customer(log)
        origin._portal_view_as_switch_session(target.sudo(), log)
        return {"type": "ir.actions.act_url", "url": "/my", "target": "self"}

    def _create_view_as_log(self, origin, target):
        httprequest = request.httprequest
        return self.env["devarc.portal.login.as.log"].sudo().create({
            "origin_user_id": origin.id,
            "target_user_id": target.id,
            "company_id": self.env.company.id,
            "reason": self.reason,
            "ip_address": httprequest.remote_addr,
            "user_agent": (httprequest.user_agent.string or "")[:255],
        })

    def _notify_customer(self, log):
        customer = log.target_user_id.partner_id
        log = log.with_context(lang=customer.lang)
        body = Markup("<p>%s</p>") % log.env._(
            "%(user)s from %(company)s is viewing your customer portal as you. "
            "The access ends automatically after %(minutes)s minutes.",
            user=log.origin_user_id.name,
            minutes=int(VIEW_AS_DURATION.total_seconds() // 60),
            company=log.company_id.name,
        )
        log.message_notify(
            partner_ids=customer.ids,
            subject=log.env._("%(company)s opened your customer portal", company=log.company_id.name),
            body=body,
            email_layout_xmlid="mail.mail_notification_light",
        )
