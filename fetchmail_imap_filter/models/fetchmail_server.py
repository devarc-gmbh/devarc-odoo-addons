import base64
import functools
import itertools
import re
from imaplib import IMAP4

from odoo import api, fields, models
from odoo.exceptions import ValidationError

UNSEEN_PREFIX = re.compile(r"[\s(]*UNSEEN\b", re.IGNORECASE)
IMAP_LITERAL = re.compile(r"\{\d+\+?\}\s*$")
IMAP_ATOM_FOLDER = re.compile(r"[A-Za-z0-9._/\-\[\]&]+")


class FetchmailServer(models.Model):
    _inherit = "fetchmail.server"

    imap_folder = fields.Char(
        string="IMAP Folder",
        default="INBOX",
        required=True,
        help="IMAP mailbox the incoming mails are fetched from, e.g. 'INBOX' or a Gmail "
        "label such as 'Odoo' fed by a filter. Non-ASCII characters are encoded to IMAP "
        "modified UTF-7 and names containing a space or a special character are quoted "
        "automatically. Ignored for POP servers.",
    )
    imap_search_criteria = fields.Char(
        string="IMAP Search Criteria",
        default="(UNSEEN)",
        required=True,
        help="IMAP search criteria selecting which mails are fetched, e.g. "
        "'(UNSEEN X-GM-RAW \"deliveredto:odoo@example.com\")' fetches only the mails a "
        "catch-all rule delivered to this mailbox. Ignored for POP servers.",
    )

    @api.constrains("imap_search_criteria")
    def _check_imap_search_criteria(self):
        for server in self:
            criteria = server.imap_search_criteria or ""
            if not UNSEEN_PREFIX.match(criteria):
                raise ValidationError(
                    self.env._(
                        "IMAP search criteria must start with UNSEEN, so that every search "
                        "key is combined with UNSEEN. Criteria starting with NOT or OR can "
                        "match already read mails, which are then fetched and processed on "
                        "every run."
                    )
                )
            if not criteria.isascii():
                raise ValidationError(
                    self.env._(
                        "The IMAP Search Criteria field must contain ASCII characters only, "
                        "the IMAP connection cannot send other characters in a search."
                    )
                )
            if IMAP_LITERAL.search(criteria):
                raise ValidationError(
                    self.env._(
                        "IMAP search criteria must not end with an IMAP literal such as {5}, "
                        "the IMAP server would wait for data that is never sent."
                    )
                )

    @api.constrains("imap_folder", "imap_search_criteria")
    def _check_imap_line_breaks(self):
        for server in self:
            values = (server.imap_folder or "", server.imap_search_criteria or "")
            if any("\r" in value or "\n" in value for value in values):
                raise ValidationError(
                    self.env._(
                        "IMAP folder and search criteria must not contain line breaks, the "
                        "IMAP server would read the rest of the line as a separate command."
                    )
                )

    def _connect__(self, allow_archived=False):  # noqa: PLW3201
        connection = super()._connect__(allow_archived=allow_archived)
        if self._get_connection_type() == "imap":
            connection.check_unread_messages = functools.partial(
                self._imap_check_unread_messages, connection
            )
        return connection

    def _imap_utf7_encode(self, name):
        parts = []
        for printable, chars in itertools.groupby(name, key=lambda char: " " <= char <= "~"):
            run = "".join(chars)
            if printable:
                parts.append(run.replace("&", "&-"))
            else:
                encoded = base64.b64encode(run.encode("utf-16-be")).decode("ascii")
                parts.append(f"&{encoded.rstrip('=').replace('/', ',')}-")
        return "".join(parts)

    def _imap_mailbox_argument(self):
        folder = self._imap_utf7_encode(self.imap_folder or "INBOX")
        if IMAP_ATOM_FOLDER.fullmatch(folder):
            return folder
        escaped = folder.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _imap_check_unread_messages(self, connection):
        self.ensure_one()
        criteria = self.imap_search_criteria or "(UNSEEN)"
        result, data = connection.select(self._imap_mailbox_argument())
        if result != "OK":
            raise IMAP4.error(
                f"IMAP SELECT of folder {self.imap_folder!r} returned {result}: {data}"
            )
        result, data = connection.search(None, criteria)
        if result != "OK":
            raise IMAP4.error(
                f"IMAP SEARCH {criteria} in folder {self.imap_folder!r} returned {result}: {data}"
            )
        connection._unread_messages = data[0].split() if data and data[0] else []
        connection._unread_messages.reverse()
        return len(connection._unread_messages)
