import hashlib
import re
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import Command, fields
from odoo.exceptions import AccessDenied, AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from ..models.ai_assistant_guard import AI_KEY_SCOPE, AI_KEY_SCOPE_RW
from .common import AiAssistantFixtures


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestAiAssistantKeyLifecycle(AiAssistantFixtures, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_ai_assistant()

    def _check(self, key, scope=AI_KEY_SCOPE):
        return self.env["res.users.apikeys"]._check_credentials(scope=scope, key=key)

    def _keys_of(self, user):
        return self.env["res.users.apikeys"].sudo().search([("user_id", "=", user.id)])

    def test_generate_creates_single_scoped_key(self):
        key = self._generate_access_as_owner(self.eva)
        apikeys = self._keys_of(self.eva)
        self.assertEqual(len(apikeys), 1)
        self.assertEqual(apikeys.scope, AI_KEY_SCOPE)
        self.assertEqual(self._check(key), self.eva.id)
        access = self.env["ai.assistant.access"].sudo().search([("user_id", "=", self.eva.id)])
        self.assertEqual(len(access), 1)
        self.assertEqual(access.state, "active")
        self.assertEqual(access.apikey_id, apikeys)
        self.assertTrue(access.expires_at)

    def test_second_generate_revokes_the_first(self):
        first = self._generate_access_as_owner(self.eva)
        second = self._generate_access_as_owner(self.eva)
        self.assertIsNone(self._check(first))
        self.assertEqual(self._check(second), self.eva.id)
        self.assertEqual(len(self._keys_of(self.eva)), 1)
        accesses = self.env["ai.assistant.access"].sudo().search([("user_id", "=", self.eva.id)])
        self.assertEqual(len(accesses), 2)
        self.assertEqual(len(accesses.filtered(lambda a: a.state == "active")), 1)
        self.assertEqual(len(accesses.filtered(lambda a: a.state == "revoked")), 1)

    @mute_logger("odoo.addons.base.models.res_users")
    def test_scoped_key_is_no_rpc_login(self):
        key = self._generate_access_as_owner(self.eva)
        self.assertIsNone(self._check(key, scope="rpc"))
        with self.assertRaises(AccessDenied):
            self.eva.with_user(self.eva)._check_credentials(
                {"login": self.eva.login, "password": key, "type": "password"},
                {"interactive": False},
            )

    def test_deactivated_user_loses_access(self):
        key = self._generate_access_as_owner(self.eva)
        self.eva.write({"active": False})
        self.assertIsNone(self._check(key))
        access = self.env["ai.assistant.access"].sudo().search([("user_id", "=", self.eva.id)])
        self.assertEqual(access.state, "revoked")

    def test_manual_revoke_removes_key(self):
        key = self._generate_access_as_owner(self.eva)
        self.eva.action_ai_assistant_revoke()
        self.assertIsNone(self._check(key))
        self.assertFalse(self._keys_of(self.eva))
        access = self.env["ai.assistant.access"].sudo().search([("user_id", "=", self.eva.id)])
        self.assertEqual(access.state, "revoked")

    def test_expiration_cron_expires_access(self):
        key = self._generate_access_as_owner(self.eva)
        access = self.env["ai.assistant.access"].sudo().search([("user_id", "=", self.eva.id)])
        access.write({"expires_at": "2020-01-01 00:00:00"})
        self.env["ai.assistant.access"]._cron_maintenance()
        self.assertEqual(access.state, "expired")
        self.assertIsNone(self._check(key))

    def test_system_user_cannot_generate_access_for_somebody_else(self):
        admin = self.env.ref("base.user_admin")
        with self.assertRaises(AccessError):
            self.eva.with_user(admin)._ai_assistant_start_pairing("read")
        with self.assertRaises(AccessError):
            self.eva.with_user(self.karol)._ai_assistant_start_pairing("read")
        self.assertFalse(self._keys_of(self.eva))

    def test_key_days_is_capped(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("ai_assistant.key_days", "3650")
        self.assertEqual(self.env["res.users"]._ai_assistant_key_days(), 365)
        self._generate_access_as_owner(self.eva)
        access = self.env["ai.assistant.access"].sudo().search([("user_id", "=", self.eva.id)])
        self.assertLessEqual(access.expires_at, fields.Datetime.now() + timedelta(days=365))
        params.set_param("ai_assistant.key_days", "0")
        self.assertEqual(self.env["res.users"]._ai_assistant_key_days(), 90)

    def test_owner_can_open_the_pairing_dialog(self):
        self.env["ai.assistant.pairing"].with_user(self.eva).check_access("read")

    def test_portal_user_cannot_generate_access(self):
        portal = self.env["res.users"].create(
            {
                "name": "Portal Tester",
                "login": "portal.ai.assistant.test",
                "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
            }
        )
        with self.assertRaises(UserError):
            portal.with_user(portal)._ai_assistant_start_pairing("read")

    def _accesses_of(self, user):
        return self.env["ai.assistant.access"].sudo().search([("user_id", "=", user.id)])

    def _pair_action(self, user, method_name):
        method = getattr(type(user), method_name).__wrapped__
        return method(user.with_user(user))

    def test_generated_access_defaults_to_read_mode(self):
        self._generate_access_as_owner(self.eva)
        access = self._accesses_of(self.eva)
        self.assertEqual(access.access_mode, "read")
        self.assertEqual(self._keys_of(self.eva).scope, AI_KEY_SCOPE)
        legacy = self.env["ai.assistant.access"].create({"user_id": self.karol.id})
        self.assertEqual(legacy.access_mode, "read")

    def test_read_write_access_uses_its_own_scope(self):
        self._set_editing_allowed(True)
        key = self._generate_access_as_owner(self.eva, "read_write")
        self.assertEqual(self._keys_of(self.eva).scope, AI_KEY_SCOPE_RW)
        self.assertEqual(self._check(key, scope=AI_KEY_SCOPE_RW), self.eva.id)
        self.assertIsNone(self._check(key))
        self.assertEqual(self._accesses_of(self.eva).access_mode, "read_write")

    def test_read_write_access_requires_editing_allowed(self):
        self._set_editing_allowed(False)
        with self.assertRaises(UserError):
            self._generate_access_as_owner(self.eva, "read_write")
        self.assertFalse(self._keys_of(self.eva))

    def test_start_pairing_creates_pending_access_without_key(self):
        old_key = self._generate_access_as_owner(self.eva)
        code = self._start_pairing_as_owner(self.eva)
        self.assertEqual(len(code), 12)
        self.assertIsNone(self._check(old_key))
        self.assertFalse(self._keys_of(self.eva))
        pending = self._accesses_of(self.eva).filtered(lambda a: a.state == "pending")
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending.apikey_id)
        self.assertEqual(pending.pairing_code_hash, hashlib.sha256(code.encode()).hexdigest())
        self.assertAlmostEqual(
            pending.pairing_expires_at,
            fields.Datetime.now() + timedelta(minutes=10),
            delta=timedelta(minutes=1),
        )
        second = self._start_pairing_as_owner(self.eva)
        self.assertNotEqual(code, second)
        self.assertEqual(pending.state, "revoked")
        self.assertFalse(pending.pairing_code_hash)

    def test_pending_access_expires_by_cron(self):
        with freeze_time(datetime.now() - timedelta(minutes=11)):
            self._start_pairing_as_owner(self.eva)
        pending = self._accesses_of(self.eva)
        self.assertEqual(pending.state, "pending")
        self.env["ai.assistant.access"]._cron_maintenance()
        self.assertEqual(pending.state, "expired")
        self.assertFalse(pending.pairing_code_hash)
        self.assertFalse(self._keys_of(self.eva))

    def test_fresh_pending_access_survives_cron(self):
        self._start_pairing_as_owner(self.eva)
        self.env["ai.assistant.access"]._cron_maintenance()
        self.assertEqual(self._accesses_of(self.eva).state, "pending")

    def test_redeem_pairing_code_issues_key_once(self):
        code = self._start_pairing_as_owner(self.eva)
        access, key = self.env["ai.assistant.access"]._redeem_pairing_code(code)
        self.assertEqual(self._check(key), self.eva.id)
        self.assertEqual((access.state, access.pairing_code_hash), ("active", False))
        self.assertEqual(access.apikey_id, self._keys_of(self.eva))
        self.assertTrue(access.expires_at)
        _again, second_key = self.env["ai.assistant.access"]._redeem_pairing_code(code)
        self.assertIsNone(second_key)
        self.assertEqual(len(self._keys_of(self.eva)), 1)

    def test_pair_read_action_returns_prompt_with_code_and_no_key(self):
        self.assertFalse(self._keys_of(self.eva))
        action = self._pair_action(self.eva, "action_ai_assistant_pair_read")
        self.assertEqual(action["res_model"], "ai.assistant.pairing")
        prompt = action["context"]["default_prompt"]
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        self.assertIn(base_url, prompt)
        self.assertIn(self.env.cr.dbname, prompt)
        self.assertIn(f"{base_url}/ai_assistant_access/static/tools/odoo_ai_mcp.py", prompt)
        self.assertIn(f"{base_url}/ai_assistant_access/static/tools/requirements.txt", prompt)
        self.assertNotIn("${", prompt)
        code = re.search(r"--code=([A-Za-z0-9_-]+)", prompt).group(1)
        pending = self._accesses_of(self.eva)
        self.assertEqual((pending.state, pending.access_mode), ("pending", "read"))
        self.assertEqual(pending.pairing_code_hash, hashlib.sha256(code.encode()).hexdigest())
        self.assertFalse(self._keys_of(self.eva))

    def test_pair_edit_action_requires_editing_allowed(self):
        self._set_editing_allowed(False)
        with self.assertRaises(UserError):
            self._pair_action(self.eva, "action_ai_assistant_pair_edit")
        self.assertFalse(self._accesses_of(self.eva))
        self._set_editing_allowed(True)
        action = self._pair_action(self.eva, "action_ai_assistant_pair_edit")
        self.assertIn("read and edit", action["context"]["default_prompt"])
        pending = self._accesses_of(self.eva)
        self.assertEqual((pending.state, pending.access_mode), ("pending", "read_write"))

    def test_pairing_actions_require_identity_check(self):
        for method_name in ("action_ai_assistant_pair_read", "action_ai_assistant_pair_edit"):
            with self.assertRaises(UserError):
                getattr(self.eva.with_user(self.eva), method_name)()
        self.assertFalse(self._accesses_of(self.eva))

    def test_only_owner_can_start_pairing(self):
        admin = self.env.ref("base.user_admin")
        with self.assertRaises(AccessError):
            self.eva.with_user(admin)._ai_assistant_start_pairing("read")
        self.assertFalse(self._accesses_of(self.eva))
