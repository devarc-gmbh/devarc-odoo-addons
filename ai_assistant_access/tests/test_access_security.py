from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .common import AiAssistantFixtures


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestAiAssistantAccessSecurity(AiAssistantFixtures, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_ai_assistant()
        cls.admin = cls.env.ref("base.user_admin")
        cls.eva_access = cls.env["ai.assistant.access"].create({"user_id": cls.eva.id})
        cls.karol_access = cls.env["ai.assistant.access"].create({"user_id": cls.karol.id})
        cls.audit = cls.env["ai.assistant.audit"].create(
            {
                "user_id": cls.eva.id,
                "op": "search_read",
                "model_name": "res.partner",
                "status": "ok",
            }
        )

    def test_user_sees_only_own_access(self):
        visible = self.env["ai.assistant.access"].with_user(self.eva).search([])
        self.assertIn(self.eva_access, visible)
        self.assertNotIn(self.karol_access, visible)

    def test_user_cannot_read_foreign_access(self):
        with self.assertRaises(AccessError):
            self.karol_access.with_user(self.eva).read(["state"])

    def test_user_cannot_read_audit(self):
        with self.assertRaises(AccessError):
            self.env["ai.assistant.audit"].with_user(self.eva).search([])

    def test_user_cannot_write_audit(self):
        with self.assertRaises(AccessError):
            self.env["ai.assistant.audit"].with_user(self.eva).create(
                {
                    "op": "search_read",
                    "status": "ok",
                }
            )

    def test_user_cannot_write_own_access_directly(self):
        with self.assertRaises(AccessError):
            self.eva_access.with_user(self.eva).write({"state": "revoked"})
        self.assertEqual(self.eva_access.state, "active")

    def test_user_cannot_create_access_directly(self):
        with self.assertRaises(AccessError):
            self.env["ai.assistant.access"].with_user(self.eva).create(
                {
                    "user_id": self.eva.id,
                }
            )

    def test_admin_sees_all_accesses_and_audit(self):
        accesses = self.env["ai.assistant.access"].with_user(self.admin).search([])
        self.assertIn(self.eva_access, accesses)
        self.assertIn(self.karol_access, accesses)
        audits = (
            self.env["ai.assistant.audit"]
            .with_user(self.admin)
            .search([("id", "=", self.audit.id)])
        )
        self.assertEqual(audits, self.audit)

    def test_only_one_active_access_per_user(self):
        with self.assertRaises(ValidationError):
            self.env["ai.assistant.access"].create({"user_id": self.eva.id})

    def test_user_cannot_see_foreign_pending_access(self):
        self._start_pairing_as_owner(self.karol)
        pending = self.env["ai.assistant.access"].search(
            [("user_id", "=", self.karol.id), ("state", "=", "pending")]
        )
        self.assertEqual(len(pending), 1)
        self.assertNotIn(pending, self.env["ai.assistant.access"].with_user(self.eva).search([]))
        with self.assertRaises(AccessError):
            pending.with_user(self.eva).read(["state"])
        seen = pending.with_user(self.admin).read(["state", "access_mode"])[0]
        self.assertEqual((seen["state"], seen["access_mode"]), ("pending", "read"))

    def test_admin_access_list_shows_mode_but_no_pairing_hash(self):
        arch = self.env.ref("ai_assistant_access.view_users_form_ai_assistant").arch
        self.assertIn('name="access_mode"', arch)
        self.assertNotIn("pairing_code_hash", arch)
