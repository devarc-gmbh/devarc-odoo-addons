from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase

from .common import AiAssistantFixtures


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestAiAssistantSetupSnippets(AiAssistantFixtures, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_ai_assistant()

    def test_claude_snippet_states_read_only_rule(self):
        snippet = self.env["res.users"]._ai_assistant_claude_snippet("read")
        self.assertTrue(snippet)
        self.assertIn("read-only", snippet)
        self.assertIn("odoo_link", snippet)
        self.assertEqual(snippet, self.env["res.users"]._ai_assistant_claude_snippet())

    def test_claude_snippet_for_edit_mode_describes_editing_without_deleting(self):
        snippet = self.env["res.users"]._ai_assistant_claude_snippet("read_write")
        self.assertIn("odoo_create", snippet)
        self.assertIn("odoo_write", snippet)
        self.assertIn("never delete", snippet)
        self.assertNotIn("read-only", snippet)
        self.assertNotEqual(snippet, self.env["res.users"]._ai_assistant_claude_snippet("read"))

    def test_no_attachment_is_created_for_the_setup_package(self):
        before = self.env["ir.attachment"].sudo().search_count([])
        self._generate_access_as_owner(self.eva)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count([]), before)

    def test_access_state_is_exposed_to_the_owner(self):
        self._generate_access_as_owner(self.eva)
        own = self.eva.with_user(self.eva)
        own.invalidate_recordset()
        self.assertEqual(own.ai_assistant_state, "active")
        self.assertTrue(own.ai_assistant_expires_at)

    def test_pending_state_and_edit_flag_are_exposed_to_the_owner(self):
        self._set_editing_allowed(False)
        own = self.eva.with_user(self.eva)
        self.assertFalse(own.ai_assistant_edit_allowed)
        self._start_pairing_as_owner(self.eva)
        self._set_editing_allowed(True)
        own.invalidate_recordset()
        self.assertEqual(own.ai_assistant_state, "pending")
        self.assertTrue(own.ai_assistant_edit_allowed)


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestAiAssistantToolDownload(HttpCase):
    def test_mcp_server_is_served_without_login(self):
        response = self.url_open("/ai_assistant_access/static/tools/odoo_ai_mcp.py")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ODOO_AI_KEY", response.text)
