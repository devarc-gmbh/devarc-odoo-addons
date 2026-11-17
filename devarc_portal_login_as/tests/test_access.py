from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from .common import MY_CUSTOMERS, PortalLoginAsCommon


@tagged("post_install", "-at_install")
class TestPortalLoginAsAccess(PortalLoginAsCommon):

    def test_my_customer_allowed(self):
        self.salesperson._check_can_view_as(self.portal_acme)
        self.assertIs(self.portal_acme.with_user(self.salesperson).env.user._portal_view_as_allowed(self.portal_acme), True)
        self.assertTrue(self.portal_acme.with_user(self.salesperson).can_view_as_customer)

    def test_foreign_customer_denied(self):
        with self.assertRaises(AccessError):
            self.salesperson._check_can_view_as(self.portal_beta)
        self.assertFalse(self.portal_beta.with_user(self.salesperson).can_view_as_customer)

    def test_all_portal_group_allows_any(self):
        self.support._check_can_view_as(self.portal_beta)
        self.support._check_can_view_as(self.portal_acme)

    def test_internal_target_denied(self):
        with self.assertRaises(AccessError):
            self.support._check_can_view_as(self.plain_internal)
        with self.assertRaises(AccessError):
            self.sysadmin._check_can_view_as(self.salesperson)
        self.assertFalse(self.plain_internal.with_user(self.support).can_view_as_customer)

    def test_inactive_target_denied(self):
        with self.assertRaises(AccessError):
            self.support._check_can_view_as(self.inactive_portal)

    def test_without_group_denied(self):
        with self.assertRaises(AccessError):
            self.plain_internal._check_can_view_as(self.portal_acme)

    def test_other_company_target_denied(self):
        other_company = self.env["res.company"].create({"name": "Other Company AG"})
        portal_other = new_test_user(
            self.env,
            "pla_portal_other_company",
            groups="base.group_portal",
            company_id=other_company.id,
            company_ids=[other_company.id],
        )
        with self.assertRaises(AccessError):
            self.support._check_can_view_as(portal_other)

    def test_partner_resolves_single_portal_user(self):
        self.assertEqual(self.acme_contact.portal_login_as_user_id, self.portal_acme)
        new_test_user(self.env, "pla_portal_acme_second", groups="base.group_portal", partner_id=self.acme_contact.id)
        self.acme_contact.invalidate_recordset(["user_ids", "portal_login_as_user_id"])
        self.assertFalse(self.acme_contact.portal_login_as_user_id)
        lonely_partner = self.env["res.partner"].create({"name": "No Portal Contact"})
        self.assertFalse(lonely_partner.portal_login_as_user_id)

    def test_log_record_rules(self):
        own_log = self._create_log(self.salesperson, self.portal_acme)
        foreign_log = self._create_log(self.other_sales, self.portal_beta)
        log_model = self.env["devarc.portal.login.as.log"]
        self.assertEqual(log_model.with_user(self.salesperson).search([]), own_log)
        self.assertEqual(log_model.with_user(self.support).search([]), own_log | foreign_log)
        self.assertEqual(log_model.with_user(self.sysadmin).search([]), own_log | foreign_log)
        sales_admin = new_test_user(
            self.env, "pla_sales_admin", groups=f"base.group_user,base.group_system,{MY_CUSTOMERS}",
        )
        sales_admin_log = self._create_log(sales_admin, self.portal_acme)
        self.assertEqual(log_model.with_user(sales_admin).search([]), sales_admin_log)
        with self.assertRaises(AccessError):
            own_log.with_user(self.salesperson).write({"reason": "x"})
        own_log.with_user(self.sysadmin).action_stop()
        self.assertEqual(own_log.state, "ended")
        self.assertEqual(own_log.end_reason, "stopped_by_admin")
        with self.assertRaises(UserError):
            own_log.with_user(self.sysadmin).action_stop()

    def test_fingerprint_changes_with_password(self):
        fp1 = self.salesperson._portal_view_as_fingerprint("n")
        self.assertEqual(self.salesperson._portal_view_as_fingerprint("n"), fp1)
        self.salesperson._change_password("NewPass!234")
        fp2 = self.salesperson._portal_view_as_fingerprint("n")
        self.assertTrue(fp1)
        self.assertNotEqual(fp2, fp1)
        self.assertEqual(self.salesperson._portal_view_as_fingerprint("n"), fp2)

    def test_wizard_create_requires_reason(self):
        wizard_model = self.env["devarc.portal.login.as.wizard"].with_user(self.salesperson)
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            wizard_model.create({"target_user_id": self.portal_acme.id})
            wizard_model.env.flush_all()

    def test_close_idempotent(self):
        log = self._create_log(self.salesperson, self.portal_acme)
        log._close("returned")
        end_at = log.end_at
        self.assertEqual(log.state, "ended")
        self.assertEqual(log._close("logout"), log.browse())
        self.assertEqual(log.end_reason, "returned")
        self.assertEqual(log.end_at, end_at)

    def test_view_as_action_requires_permission(self):
        action = self.portal_acme.with_user(self.salesperson).action_view_as_customer()
        self.assertEqual(action["res_model"], "devarc.portal.login.as.wizard")
        self.assertEqual(action["context"], {"default_target_user_id": self.portal_acme.id})
        with self.assertRaises(AccessError):
            self.portal_beta.with_user(self.salesperson).action_view_as_customer()

    def test_deleting_portal_user_keeps_readable_log(self):
        log = self._create_log(self.salesperson, self.portal_acme)
        target_name = self.portal_acme.name
        self.portal_acme.unlink()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(log.exists())
        self.assertFalse(log.target_user_id)
        self.assertEqual(log.target_name, target_name)
        self.assertEqual(log.origin_name, self.salesperson.name)
        self.assertEqual(log.partner_id, self.acme)
        self.assertEqual(log.display_name, f"{self.salesperson.name} / {target_name}")
