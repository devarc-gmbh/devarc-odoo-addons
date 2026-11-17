from odoo import models
from odoo.exceptions import AccessDenied
from odoo.http import request

from .res_users import VIEW_AS_SESSION_KEY


class ResUsersIdentitycheck(models.TransientModel):
    _inherit = "res.users.identitycheck"

    def run_check(self):
        if request and request.session.get(VIEW_AS_SESSION_KEY):
            raise AccessDenied(self.env._("Security settings cannot be changed while viewing the portal as a customer."))
        return super().run_check()
