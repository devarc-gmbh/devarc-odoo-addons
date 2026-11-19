import hashlib

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL

from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT

STATE_PENDING = "pending"
STATE_ACTIVE = "active"
STATE_REVOKED = "revoked"
STATE_EXPIRED = "expired"

ACCESS_MODE_READ = "read"
ACCESS_MODE_READ_WRITE = "read_write"

PAIRING_TTL_MINUTES = 10


class AiAssistantAccess(models.Model):
    _name = "ai.assistant.access"
    _description = "AI Assistant - User Access"
    _order = "id desc"

    user_id = fields.Many2one(
        "res.users",
        string="User",
        required=True,
        ondelete="cascade",
        index=True,
        help="Owner of the access; requests run under their identity and access rights.",
    )
    apikey_id = fields.Many2one(
        "res.users.apikeys",
        string="API Key",
        ondelete="set null",
        help="Native Odoo API key with the ai_assistant_ro or ai_assistant_rw scope; "
        "only its hash is stored.",
    )
    state = fields.Selection(
        [
            (STATE_PENDING, "Waiting for pairing"),
            (STATE_ACTIVE, "Active"),
            (STATE_REVOKED, "Revoked"),
            (STATE_EXPIRED, "Expired"),
        ],
        string="Status",
        required=True,
        default=STATE_ACTIVE,
        help="Only an active and non-expired access passes through the endpoint.",
    )
    access_mode = fields.Selection(
        [
            (ACCESS_MODE_READ, "Read only"),
            (ACCESS_MODE_READ_WRITE, "Read and edit"),
        ],
        string="Mode",
        required=True,
        default=ACCESS_MODE_READ,
        help="Read and edit works only while the administrator allows editing; "
        "otherwise the access falls back to read only.",
    )
    pairing_code_hash = fields.Char(
        string="Pairing code hash",
        copy=False,
        index="btree_not_null",
        help="SHA-256 of the one-time pairing code; cleared once the code is used or expires.",
    )
    pairing_expires_at = fields.Datetime(
        string="Pairing code valid until",
        copy=False,
        help="After this moment the pairing code can no longer be exchanged for a key.",
    )
    expires_at = fields.Datetime(
        string="Valid until",
        help="After this date the endpoint rejects the access even before the cron runs.",
    )
    last_used_at = fields.Datetime(string="Last used")
    request_count = fields.Integer(string="Requests", default=0)

    @api.constrains("user_id", "state")
    def _check_single_active_access(self):
        for access in self.filtered(lambda record: record.state == STATE_ACTIVE):
            duplicate = self.search_count(
                [
                    ("id", "!=", access.id),
                    ("user_id", "=", access.user_id.id),
                    ("state", "=", STATE_ACTIVE),
                ]
            )
            if duplicate:
                raise ValidationError(
                    self.env._(
                        "User %s already has an active AI assistant access.",
                        access.user_id.display_name,
                    )
                )

    @api.model
    def _hash_pairing_code(self, code):
        return hashlib.sha256(code.encode()).hexdigest()

    def _matches_key(self, key):
        self.ensure_one()
        if not self.apikey_id:
            return False
        self.env.cr.execute(
            SQL("SELECT key FROM res_users_apikeys WHERE id = %s", self.apikey_id.id)
        )
        row = self.env.cr.fetchone()
        return bool(row) and KEY_CRYPT_CONTEXT.verify(key, row[0])

    def _record_usage(self):
        self.env.cr.execute(
            SQL(
                "UPDATE %s SET request_count = COALESCE(request_count, 0) + 1, last_used_at = %s WHERE id IN %s",
                SQL.identifier(self._table),
                fields.Datetime.now(),
                tuple(self.ids),
            )
        )
        self.invalidate_recordset(["request_count", "last_used_at"])

    def _revoke(self):
        for access in self:
            apikey = access.apikey_id
            access.write({"state": STATE_REVOKED, "pairing_code_hash": False})
            if apikey:
                apikey.sudo()._remove()

    def action_revoke(self):
        self.check_access("write")
        self._revoke()
        return True

    def _try_lock_for_update(self):
        if not self:
            return self
        self.env.cr.execute(SQL(
            "SELECT id FROM %s WHERE id IN %s FOR UPDATE SKIP LOCKED",
            SQL.identifier(self._table),
            tuple(self.ids),
        ))
        return self.browse(row[0] for row in self.env.cr.fetchall())

    @api.model
    def _redeem_pairing_code(self, code):
        access = (
            self.sudo()
            .search(
                [
                    ("state", "=", STATE_PENDING),
                    ("pairing_code_hash", "=", self._hash_pairing_code(code)),
                ],
                limit=1,
            )
            ._try_lock_for_update()
        )
        if not access:
            return access, None
        access.invalidate_recordset()
        if access.state != STATE_PENDING:
            return access, None
        owner = access.user_id
        if (
            not access.pairing_expires_at
            or access.pairing_expires_at < fields.Datetime.now()
            or not owner.active
            or not owner._is_internal()
        ):
            access.write({"state": STATE_EXPIRED, "pairing_code_hash": False})
            return access, None
        mode = access.access_mode
        if mode == ACCESS_MODE_READ_WRITE and not owner._ai_assistant_editing_allowed():
            mode = ACCESS_MODE_READ
        key, apikey, expires_at = owner._ai_assistant_issue_key(mode)
        access.write(
            {
                "state": STATE_ACTIVE,
                "access_mode": mode,
                "apikey_id": apikey.id,
                "expires_at": expires_at,
                "pairing_code_hash": False,
                "pairing_expires_at": False,
            }
        )
        return access, key

    @api.model
    def _expire_overdue(self):
        now = fields.Datetime.now()
        overdue = self.sudo().search(
            [
                ("state", "=", STATE_ACTIVE),
                ("expires_at", "!=", False),
                ("expires_at", "<", now),
            ]
        )
        for access in overdue:
            apikey = access.apikey_id
            access.state = STATE_EXPIRED
            if apikey:
                apikey.sudo()._remove()
        self.sudo().search(
            [
                ("state", "=", STATE_PENDING),
                ("pairing_expires_at", "!=", False),
                ("pairing_expires_at", "<", now),
            ]
        ).write({"state": STATE_EXPIRED, "pairing_code_hash": False})

    @api.model
    def _cron_maintenance(self):
        self._expire_overdue()
        self.env["ai.assistant.audit"]._gc_audit()
