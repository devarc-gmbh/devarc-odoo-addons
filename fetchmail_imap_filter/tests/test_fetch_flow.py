from email.message import EmailMessage
from email.utils import formatdate
from unittest.mock import patch

from odoo.addons.mail.models.fetchmail import OdooIMAP4
from odoo.addons.mail.tests.common import MailCommon
from odoo.tests import tagged

from .test_imap_selection import HEADER_CRITERIA

MATCHING_SUBJECT = "Question about your offer"
OTHER_SUBJECT = "Private mail for the mailbox owner"


class FakeIMAP4(OdooIMAP4):
    def __init__(self, messages, matching_numbers):
        self._unread_messages = None
        self.messages = messages
        self.matching_numbers = matching_numbers
        self.selected_folders = []
        self.search_calls = []
        self.fetched_numbers = []
        self.stored_flags = []
        self.credentials = None
        self.logged_out = False

    def login(self, user, password):
        self.credentials = (user, password)
        return ("OK", [b"LOGIN completed"])

    def select(self, mailbox="INBOX", readonly=False):
        self.selected_folders.append(mailbox)
        return ("OK", [str(len(self.messages)).encode()])

    def search(self, charset, *criteria):
        self.search_calls.append((charset, *criteria))
        return ("OK", [b" ".join(self.matching_numbers)])

    def fetch(self, message_num, message_parts):
        self.fetched_numbers.append(message_num)
        return ("OK", [(b"%s (RFC822" % message_num, self.messages[message_num])])

    def store(self, message_num, command, flags):
        self.stored_flags.append((message_num, command, flags))
        return ("OK", [b"STORE completed"])

    def close(self):
        return ("OK", [b"CLOSE completed"])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b"LOGOUT completed"])


@tagged("post_install", "-at_install")
class TestFetchFlow(MailCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alias = cls.env["mail.alias"].create(
            {
                "alias_name": "kontakt",
                "alias_model_id": cls.env["ir.model"]._get_id("res.partner"),
                "alias_domain_id": cls.mail_alias_domain.id,
                "alias_contact": "everyone",
            }
        )
        cls.server = cls.env["fetchmail.server"].create(
            {
                "name": "Filtered mailbox",
                "server_type": "imap",
                "server": "imap.example.com",
                "port": 993,
                "is_ssl": True,
                "user": "odoo@example.com",
                "password": "secret",
                "imap_folder": "Odoo",
                "imap_search_criteria": HEADER_CRITERIA,
                "state": "done",
            }
        )

    def _build_mail(self, subject, recipient, message_id, delivered_to):
        mail = EmailMessage()
        mail["Message-Id"] = message_id
        mail["Subject"] = subject
        mail["From"] = "External Client <klient@example.com>"
        mail["To"] = recipient
        mail["Delivered-To"] = delivered_to
        mail["Date"] = formatdate(localtime=True)
        mail.set_content("Good morning, I have a question about your offer.")
        return mail.as_bytes()

    def test_matching_mail_creates_record_and_other_mail_is_left_on_server(self):
        self.assertEqual(self.alias.alias_full_name, "kontakt@test.mycompany.com")
        matching = self._build_mail(
            MATCHING_SUBJECT,
            "kontakt@test.mycompany.com",
            "<matching-1@example.com>",
            "odoo@example.com",
        )
        other = self._build_mail(
            OTHER_SUBJECT,
            "owner@example.com",
            "<other-1@example.com>",
            "owner@example.com",
        )
        connection = FakeIMAP4({b"1": matching, b"2": other}, [b"1"])

        with (
            self.enter_registry_test_mode(),
            self.registry.cursor() as cr,
            patch("odoo.addons.mail.models.fetchmail.OdooIMAP4_SSL", return_value=connection),
        ):
            server = self.server.with_env(self.server.env(cr=cr))
            self.assertIsNone(server._fetch_mail())

            partners = server.env["res.partner"].search(
                [("name", "in", [MATCHING_SUBJECT, OTHER_SUBJECT])]
            )
            self.assertEqual(partners.mapped("name"), [MATCHING_SUBJECT])
            self.assertEqual(partners.email_normalized, "klient@example.com")
            incoming = partners.message_ids.filtered(
                lambda message: message.message_type == "email"
            )
            self.assertEqual(len(incoming), 1)
            self.assertEqual(incoming.message_id, "<matching-1@example.com>")

        self.assertEqual(connection.credentials, ("odoo@example.com", "secret"))
        self.assertEqual(connection.selected_folders, ["Odoo"])
        self.assertEqual(connection.search_calls, [(None, HEADER_CRITERIA)])
        self.assertEqual(connection.fetched_numbers, [b"1"])
        self.assertIn((b"1", "+FLAGS", "\\Seen"), connection.stored_flags)
        self.assertNotIn(b"2", [stored[0] for stored in connection.stored_flags])
        self.assertTrue(connection.logged_out)
