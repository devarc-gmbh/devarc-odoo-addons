from odoo import api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    portal_login_as_user_id = fields.Many2one(
        "res.users",
        string="Portal user to view as",
        compute="_compute_portal_login_as_user_id",
        help="The only active portal user of this contact, if there is exactly one.",
    )

    can_view_as_customer = fields.Boolean(
        compute="_compute_can_view_as_customer",
        help="The current user may view the portal as the only portal user of this contact.",
    )

    def _compute_portal_login_as_user_id(self):
        for partner in self:
            portal_users = partner.user_ids.filtered(lambda user: user.active and user.share)
            partner.portal_login_as_user_id = portal_users if len(portal_users) == 1 else False

    @api.depends_context("uid")
    def _compute_can_view_as_customer(self):
        for partner in self:
            partner.can_view_as_customer = partner.portal_login_as_user_id.can_view_as_customer

    def action_view_as_customer(self):
        self.ensure_one()
        if not self.portal_login_as_user_id:
            raise UserError(self.env._("This contact has no single active portal user."))
        return self.portal_login_as_user_id.action_view_as_customer()
