import functools
from unittest import SkipTest
from unittest.mock import Mock, call, patch

from odoo.tests import TransactionCase, tagged

from .test_imap_selection import GMAIL_RAW_CRITERIA


@tagged("post_install", "-at_install")
class TestGmailServer(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "google_gmail" not in cls.env["ir.module.module"]._installed():
            raise SkipTest("google_gmail is not installed")
        cls.server = cls.env["fetchmail.server"].create(
            {
                "name": "Google Workspace catch-all",
                "server_type": "gmail",
                "server": "imap.gmail.com",
                "port": 993,
                "is_ssl": True,
                "user": "odoo@example.com",
                "imap_folder": "Odoo",
                "imap_search_criteria": GMAIL_RAW_CRITERIA,
            }
        )

    def test_gmail_connection_searches_the_configured_folder(self):
        connection = Mock()
        connection.search.return_value = ("OK", [b"3 5"])
        with (
            patch("odoo.addons.mail.models.fetchmail.OdooIMAP4_SSL", return_value=connection),
            patch(
                "odoo.addons.google_gmail.models.google_gmail_mixin."
                "GoogleGmailMixin._generate_oauth2_string",
                return_value="user=odoo@example.com\1auth=Bearer token\1\1",
            ),
        ):
            self.server._connect__(allow_archived=True)

        connection.authenticate.assert_called_once()
        self.assertIsInstance(connection.check_unread_messages, functools.partial)
        self.assertEqual(connection.check_unread_messages(), 2)
        self.assertEqual(connection.select.call_args_list[-1], call("Odoo"))
        connection.search.assert_called_once_with(None, GMAIL_RAW_CRITERIA)
