from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user

MY_CUSTOMERS = "devarc_portal_login_as.group_view_my_customers"
ALL_PORTAL = "devarc_portal_login_as.group_view_all_portal"


class PortalLoginAsCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.salesperson = new_test_user(cls.env, "pla_salesperson", groups=f"base.group_user,{MY_CUSTOMERS}")
        cls.support = new_test_user(cls.env, "pla_support", groups=f"base.group_user,{ALL_PORTAL}")
        cls.other_sales = new_test_user(cls.env, "pla_other_sales", groups=f"base.group_user,{MY_CUSTOMERS}")
        cls.plain_internal = new_test_user(cls.env, "pla_plain_internal", groups="base.group_user")
        cls.sysadmin = new_test_user(cls.env, "pla_sysadmin", groups="base.group_user,base.group_system")

        partner_model = cls.env["res.partner"]
        cls.acme = partner_model.create({"name": "Acme AG", "is_company": True, "user_id": cls.salesperson.id})
        cls.acme_contact = partner_model.create({"name": "Anna Acme", "parent_id": cls.acme.id})
        cls.portal_acme = new_test_user(
            cls.env, "pla_portal_acme", groups="base.group_portal", partner_id=cls.acme_contact.id,
        )
        cls.beta = partner_model.create({"name": "Beta GmbH", "is_company": True, "user_id": cls.other_sales.id})
        cls.portal_beta = new_test_user(
            cls.env, "pla_portal_beta", groups="base.group_portal", parent_id=cls.beta.id,
        )
        cls.inactive_portal = new_test_user(
            cls.env, "pla_inactive_portal", groups="base.group_portal", parent_id=cls.acme.id,
        )
        cls.inactive_portal.active = False

    @classmethod
    def _create_log(cls, origin, target, **values):
        start_at = fields.Datetime.now()
        return cls.env["devarc.portal.login.as.log"].sudo().create({
            "origin_user_id": origin.id,
            "target_user_id": target.id,
            "company_id": cls.company.id,
            "reason": "Customer cannot find the invoice",
            "start_at": start_at,
            "expires_at": start_at + timedelta(minutes=30),
            **values,
        })
