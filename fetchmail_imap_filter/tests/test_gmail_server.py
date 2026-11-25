import functools
from unittest import SkipTest
from unittest.mock import Mock, call, patch

from odoo.tests import TransactionCase, tagged

CRITERIA = '(UNSEEN X-GM-RAW "deliveredto:odoo@example.com")'


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
                "imap_search_criteria": CRITERIA,
            }
        )

    def test_gmail_connection_searches_the_configured_folder(self):
        connection = Mock()
        imap_select = connection.select
        imap_search = connection.search
        imap_search.return_value = ("OK", [b"3 5"])
        with (
            patch("odoo.addons.mail.models.fetchmail.IMAP4Connection", return_value=connection),
            patch(
                "odoo.addons.google_gmail.models.google_gmail_mixin."
                "GoogleGmailMixin._generate_oauth2_string",
                return_value="user=odoo@example.com\1auth=Bearer token\1\1",
            ),
        ):
            self.server.connect(allow_archived=True)

        connection.authenticate.assert_called_once()
        self.assertIsInstance(connection.search, functools.partial)
        connection.select()
        self.assertEqual(len(connection.search(None, "(UNSEEN)")[1][0].split()), 2)
        self.assertEqual(imap_select.call_args_list[-1], call("Odoo"))
        imap_search.assert_called_once_with(None, CRITERIA)
