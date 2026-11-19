import psycopg2

from odoo import SUPERUSER_ID, api
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

PROBE_PARTNER_NAME = "AI assistant read-only probe"


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestReadOnlyTransaction(TransactionCase):
    @mute_logger("odoo.sql_db")
    def test_raw_insert_is_blocked_in_read_only_transaction(self):
        cursor = self.registry.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                cursor.execute(
                    "INSERT INTO ai_assistant_audit (ts, status, op) "
                    "VALUES (now(), 'ok', 'ro-probe')",
                    log_exceptions=False,
                )
        finally:
            cursor.rollback()
            cursor.close()
        self.assertFalse(self.env["ai.assistant.audit"].sudo().search([("op", "=", "ro-probe")]))

    @mute_logger("odoo.sql_db")
    def test_orm_sudo_create_is_blocked_in_read_only_transaction(self):
        cursor = self.registry.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            env = api.Environment(cursor, SUPERUSER_ID, {})
            with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                env["res.partner"].sudo().create({"name": PROBE_PARTNER_NAME})
        finally:
            cursor.rollback()
            cursor.close()
        self.assertFalse(
            self.env["res.partner"].sudo().search([("name", "=", PROBE_PARTNER_NAME)])
        )

    def test_next_cursor_writes_normally_after_rollback(self):
        cursor = self.registry.cursor()
        try:
            cursor.execute(
                "INSERT INTO ai_assistant_audit (ts, status, op) "
                "VALUES (now(), 'ok', 'rw-probe') RETURNING id"
            )
            self.assertTrue(cursor.fetchone()[0])
        finally:
            cursor.rollback()
            cursor.close()
