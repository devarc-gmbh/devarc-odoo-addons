import functools
from imaplib import IMAP4
from unittest.mock import Mock, patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

HEADER_CRITERIA = '(UNSEEN HEADER Delivered-To "odoo@example.com")'
GMAIL_RAW_CRITERIA = '(UNSEEN X-GM-RAW "deliveredto:odoo@example.com")'
GMAIL_ALL_MAIL_FOLDER = "[Gmail]/All Mail"


@tagged("post_install", "-at_install")
class TestImapSelection(TransactionCase):
    def _create_server(self, **values):
        return self.env["fetchmail.server"].create(
            {
                "name": "Filtered mailbox",
                "server_type": "imap",
                "server": "imap.example.com",
                "port": 993,
                "is_ssl": True,
                "user": "odoo@example.com",
                "password": "secret",
                **values,
            }
        )

    def _connect_mocked_imap(self, server, search_result=("OK", [b""])):
        connection = Mock()
        connection._unread_messages = None
        connection.search.return_value = search_result
        with patch(
            "odoo.addons.mail.models.fetchmail.OdooIMAP4_SSL", return_value=connection
        ) as imap_class:
            server._connect__(allow_archived=True)
        imap_class.assert_called_once()
        connection.login.assert_called_once_with("odoo@example.com", "secret")
        return connection

    def test_default_folder_and_criteria_keep_core_behaviour(self):
        server = self._create_server()
        self.assertEqual(server.imap_folder, "INBOX")
        self.assertEqual(server.imap_search_criteria, "(UNSEEN)")

        connection = self._connect_mocked_imap(server)
        self.assertEqual(connection.check_unread_messages(), 0)
        connection.select.assert_called_once_with("INBOX")
        connection.search.assert_called_once_with(None, "(UNSEEN)")

    def test_custom_folder_and_criteria_are_sent_to_imap(self):
        server = self._create_server(imap_folder="Odoo", imap_search_criteria=HEADER_CRITERIA)

        connection = self._connect_mocked_imap(server)
        connection.check_unread_messages()
        connection.select.assert_called_once_with("Odoo")
        connection.search.assert_called_once_with(None, HEADER_CRITERIA)

    def test_criteria_with_quotes_are_passed_verbatim(self):
        server = self._create_server(imap_search_criteria=GMAIL_RAW_CRITERIA)
        self.assertEqual(server.imap_search_criteria, GMAIL_RAW_CRITERIA)

        connection = self._connect_mocked_imap(server)
        connection.check_unread_messages()
        self.assertEqual(connection.search.call_args, ((None, GMAIL_RAW_CRITERIA),))

    def test_unread_message_numbers_are_read_from_search_result(self):
        server = self._create_server()
        connection = self._connect_mocked_imap(server, ("OK", [b"4 7 9"]))

        self.assertEqual(connection.check_unread_messages(), 3)
        self.assertEqual(connection._unread_messages, [b"9", b"7", b"4"])

    def test_pop_connection_is_untouched(self):
        server = self._create_server(server_type="pop", port=995)
        connection = Mock()
        with patch("odoo.addons.mail.models.fetchmail.OdooPOP3_SSL", return_value=connection):
            server._connect__(allow_archived=True)

        self.assertNotIsInstance(connection.check_unread_messages, functools.partial)

    def test_search_criteria_without_unseen_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._create_server(imap_search_criteria='(HEADER Delivered-To "odoo@example.com")')

        server = self._create_server()
        with self.assertRaises(ValidationError):
            server.imap_search_criteria = "(ALL)"

        server.imap_search_criteria = HEADER_CRITERIA
        self.assertEqual(server.imap_search_criteria, HEADER_CRITERIA)

    def test_folder_with_space_is_quoted_for_select(self):
        server = self._create_server(imap_folder=GMAIL_ALL_MAIL_FOLDER)

        connection = self._connect_mocked_imap(server)
        connection.check_unread_messages()
        connection.select.assert_called_once_with('"[Gmail]/All Mail"')

    def test_non_ascii_and_ampersand_folders_are_encoded_to_modified_utf7(self):
        expected = {
            "Faktúry": "Fakt&APo-ry",
            "Entwürfe": "Entw&APw-rfe",
            "A&B": "A&-B",
            "INBOX": "INBOX",
        }
        server = self._create_server()
        for folder, mailbox in expected.items():
            with self.subTest(folder=folder):
                server.imap_folder = folder
                self.assertEqual(server._imap_mailbox_argument(), mailbox)

    def test_folder_with_quotes_and_backslashes_is_escaped(self):
        server = self._create_server(imap_folder='Odoo "Work"\\Mail')

        connection = self._connect_mocked_imap(server)
        connection.check_unread_messages()
        connection.select.assert_called_once_with('"Odoo \\"Work\\"\\\\Mail"')

    def test_search_error_response_raises_and_leaves_no_message_numbers(self):
        server = self._create_server()
        connection = self._connect_mocked_imap(server, ("NO", [b"SEARCH not allowed"]))

        with self.assertRaises(IMAP4.error) as error:
            connection.check_unread_messages()

        self.assertIn("(UNSEEN)", str(error.exception))
        self.assertIn("SEARCH not allowed", str(error.exception))
        self.assertIsNone(connection._unread_messages)

    def test_search_without_untagged_response_counts_zero(self):
        server = self._create_server()
        connection = self._connect_mocked_imap(server, ("OK", [None]))

        self.assertEqual(connection.check_unread_messages(), 0)
        self.assertEqual(connection._unread_messages, [])

    def test_criteria_with_negated_or_alternative_unseen_are_rejected(self):
        for criteria in ("(NOT UNSEEN)", "(OR UNSEEN SEEN)", "(OR SEEN UNSEEN)"):
            with self.subTest(criteria=criteria):
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self._create_server(imap_search_criteria=criteria)
                self.env.invalidate_all()

    def test_criteria_starting_with_unseen_are_accepted(self):
        accepted = (
            "(UNSEEN)",
            HEADER_CRITERIA,
            GMAIL_RAW_CRITERIA,
            "(UNSEEN OR FROM a@example.com FROM b@example.com)",
        )
        for criteria in accepted:
            with self.subTest(criteria=criteria):
                server = self._create_server(imap_search_criteria=criteria)
                self.env.flush_all()
                self.assertEqual(server.imap_search_criteria, criteria)

    def test_line_breaks_in_folder_or_criteria_are_rejected(self):
        injections = (
            {"imap_folder": "INBOX\r\nA01 LOGOUT"},
            {"imap_folder": "INBOX\nA01 LOGOUT"},
            {"imap_search_criteria": "(UNSEEN)\r\nA01 LOGOUT"},
        )
        for values in injections:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self._create_server(**values)
                self.env.invalidate_all()
