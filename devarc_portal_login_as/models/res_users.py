import secrets

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.http.session import STORED_SESSION_BYTES, finalize, session_store

VIEW_AS_SESSION_KEY = "devarc_portal_view_as"


class ResUsers(models.Model):
    _inherit = "res.users"

    can_view_as_customer = fields.Boolean(
        compute="_compute_can_view_as_customer",
        help="The current user may view the portal as this user.",
    )

    @api.depends_context("uid")
    def _compute_can_view_as_customer(self):
        current_user = self.env.user
        for user in self:
            user.can_view_as_customer = bool(user.id) and current_user._portal_view_as_allowed(user)

    def _check_can_view_as(self, target):
        self.ensure_one()
        origin = self.sudo()
        target = target.sudo()
        if not (origin._is_internal() and origin.has_group("devarc_portal_login_as.group_view_my_customers")):
            raise AccessError(self.env._("You are not allowed to view the portal as a customer."))
        if (
            not target.active
            or not target.share
            or target._is_internal()
            or target == origin
            or target._is_system()
        ):
            raise AccessError(self.env._("Only active portal users can be viewed as a customer."))
        if target.company_id not in origin.company_ids:
            raise AccessError(self.env._("The portal user belongs to a company you have no access to."))
        if (
            not origin.has_group("devarc_portal_login_as.group_view_all_portal")
            and target.partner_id.commercial_partner_id.user_id != origin
        ):
            raise AccessError(self.env._("You can only view the portal of your own customers."))

    def _portal_view_as_allowed(self, target):
        try:
            self._check_can_view_as(target)
        except AccessError:
            return False
        return True

    def _portal_view_as_fingerprint(self, nonce):
        self.ensure_one()
        user = self.sudo()
        user.flush_recordset()
        return user._session_token_hash_compute(nonce, user._session_token_get_values())

    def _portal_view_as_switch_session(self, target, log):
        self.ensure_one()
        nonce = secrets.token_hex(16)
        request.session.pop("identity-check-last", None)
        request.session[VIEW_AS_SESSION_KEY] = {
            "log_id": log.id,
            "origin_uid": self.id,
            "nonce": nonce,
            "fingerprint": self._portal_view_as_fingerprint(nonce),
        }
        self._portal_view_as_finalize_session(target)
        session_store().rotate(request.session, request.env)
        log.session_identifier = request.session.sid[:STORED_SESSION_BYTES]

    @api.model
    def _portal_view_as_finalize_session(self, user):
        request.session["pre_login"] = user.login
        request.session["pre_uid"] = user.id
        finalize(request.session, request.env)
        request.update_env(user=user.id)

    @api.model
    def _portal_view_as_banner_values(self):
        state = request and request.session.get(VIEW_AS_SESSION_KEY)
        if not state:
            return {}
        log = self.env["devarc.portal.login.as.log"]._from_view_as_state(state)
        if not log:
            return {}
        return {
            "target_name": log.target_user_id.name,
            "expires_at": log.expires_at,
        }

    def action_view_as_customer(self):
        self.ensure_one()
        self.env.user._check_can_view_as(self)
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "devarc_portal_login_as.action_portal_login_as_wizard"
        )
        action["context"] = {"default_target_user_id": self.id}
        return action
