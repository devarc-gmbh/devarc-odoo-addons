import logging
import secrets
from datetime import timedelta
from string import Template

from odoo import api, fields, models
from odoo.addons.base.models.res_users import check_identity
from odoo.exceptions import AccessError, UserError
from odoo.tools import file_open, str2bool

from .ai_assistant_access import (
    ACCESS_MODE_READ,
    ACCESS_MODE_READ_WRITE,
    PAIRING_TTL_MINUTES,
    STATE_ACTIVE,
    STATE_PENDING,
)
from .ai_assistant_guard import AI_KEY_NAME, AI_KEY_SCOPE, AI_KEY_SCOPE_RW

_logger = logging.getLogger(__name__)

TOOLS_FILE_PREFIX = "ai_assistant_access/static/tools"
ASSISTANT_INSTRUCTIONS_PATHS = {
    ACCESS_MODE_READ: f"{TOOLS_FILE_PREFIX}/assistant_instructions_read.md",
    ACCESS_MODE_READ_WRITE: f"{TOOLS_FILE_PREFIX}/assistant_instructions_read_write.md",
}
PAIRING_PROMPT_PATH = f"{TOOLS_FILE_PREFIX}/pairing_prompt.md"
TOOLS_URL_PATH = "/ai_assistant_access/static/tools"
DEFAULT_KEY_DAYS = 90
MAX_KEY_DAYS = 365
KEY_SCOPES = {ACCESS_MODE_READ: AI_KEY_SCOPE, ACCESS_MODE_READ_WRITE: AI_KEY_SCOPE_RW}
PROMPT_MODE_LABELS = {ACCESS_MODE_READ: "read-only", ACCESS_MODE_READ_WRITE: "read and edit"}


class ResUsers(models.Model):
    _inherit = "res.users"

    ai_assistant_access_ids = fields.One2many(
        "ai.assistant.access",
        "user_id",
        string="AI assistant accesses",
        help="History of generated accesses; at most one of them can be active.",
    )
    ai_assistant_state = fields.Selection(
        [
            ("none", "Not generated"),
            ("pending", "Waiting for your assistant"),
            ("active", "Active"),
            ("inactive", "Invalid"),
        ],
        string="AI access status",
        compute="_compute_ai_assistant_state",
        help="Status of the last access generated for the AI assistant.",
    )
    ai_assistant_expires_at = fields.Datetime(
        string="Active until",
        compute="_compute_ai_assistant_state",
        help="Expiry date of the active access.",
    )
    ai_assistant_edit_allowed = fields.Boolean(
        string="AI editing allowed",
        compute="_compute_ai_assistant_edit_allowed",
        help="The administrator allows read and edit access for AI assistants.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "ai_assistant_access_ids",
            "ai_assistant_state",
            "ai_assistant_expires_at",
            "ai_assistant_edit_allowed",
        ]

    @api.depends("ai_assistant_access_ids.state", "ai_assistant_access_ids.expires_at")
    def _compute_ai_assistant_state(self):
        for user in self:
            accesses = user.ai_assistant_access_ids
            active = accesses.filtered(lambda access: access.state == STATE_ACTIVE)[:1]
            user.ai_assistant_expires_at = active.expires_at
            if active:
                user.ai_assistant_state = "active"
            elif accesses.filtered(lambda access: access.state == STATE_PENDING):
                user.ai_assistant_state = "pending"
            elif accesses:
                user.ai_assistant_state = "inactive"
            else:
                user.ai_assistant_state = "none"

    def _compute_ai_assistant_edit_allowed(self):
        allowed = self._ai_assistant_editing_allowed()
        for user in self:
            user.ai_assistant_edit_allowed = allowed

    @api.model
    def _ai_assistant_claude_snippet(self, mode=ACCESS_MODE_READ):
        path = ASSISTANT_INSTRUCTIONS_PATHS.get(
            mode, ASSISTANT_INSTRUCTIONS_PATHS[ACCESS_MODE_READ]
        )
        try:
            with file_open(path, "r") as snippet:
                return snippet.read()
        except OSError:
            _logger.warning("File %s could not be read", path)
            return ""

    @api.model
    def _ai_assistant_editing_allowed(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("ai_assistant.allow_edit")
        return str2bool(raw or "False", False)

    @api.model
    def _ai_assistant_key_days(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("ai_assistant.key_days")
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_KEY_DAYS
        if days <= 0:
            return DEFAULT_KEY_DAYS
        return min(days, MAX_KEY_DAYS)

    def _ai_assistant_issue_key(self, mode):
        self.ensure_one()
        expires_at = fields.Datetime.now() + timedelta(days=self._ai_assistant_key_days())
        apikeys = self.env["res.users.apikeys"].with_user(self).sudo()
        key = apikeys._generate(KEY_SCOPES[mode], AI_KEY_NAME, expires_at)
        apikey = (
            self.env["res.users.apikeys"]
            .sudo()
            .search(
                [("user_id", "=", self.id), ("scope", "=", KEY_SCOPES[mode])],
                order="id desc",
                limit=1,
            )
        )
        return key, apikey, expires_at

    def _ai_assistant_check_can_issue(self, mode):
        self.ensure_one()
        # not even an administrator may see another user's key, hence no _is_system exception
        if self != self.env.user:
            raise AccessError(
                self.env._("Only the owner can generate their own AI assistant access.")
            )
        if not self._is_internal():
            raise UserError(self.env._("Only an internal user can have AI assistant access."))
        if mode not in KEY_SCOPES:
            raise UserError(self.env._("Unknown AI assistant access mode."))
        if mode == ACCESS_MODE_READ_WRITE and not self._ai_assistant_editing_allowed():
            raise UserError(
                self.env._("Editing through the AI assistant is disabled by the administrator.")
            )

    def _ai_assistant_start_pairing(self, mode):
        self._ai_assistant_check_can_issue(mode)
        self._ai_assistant_revoke_active()
        code = secrets.token_urlsafe(9)
        accesses = self.env["ai.assistant.access"].sudo()
        accesses.create(
            {
                "user_id": self.id,
                "state": STATE_PENDING,
                "access_mode": mode,
                "pairing_code_hash": accesses._hash_pairing_code(code),
                "pairing_expires_at": fields.Datetime.now()
                + timedelta(minutes=PAIRING_TTL_MINUTES),
            }
        )
        return code

    def _ai_assistant_pairing_prompt(self, code, mode):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        with file_open(PAIRING_PROMPT_PATH, "r") as template_file:
            template = Template(template_file.read())
        return template.safe_substitute(
            odoo_url=base_url,
            db=self.env.cr.dbname,
            code=code,
            access_mode=PROMPT_MODE_LABELS[mode],
            ttl_minutes=PAIRING_TTL_MINUTES,
            tool_url=f"{base_url}{TOOLS_URL_PATH}/odoo_ai_mcp.py",
            requirements_url=f"{base_url}{TOOLS_URL_PATH}/requirements.txt",
        )

    def _ai_assistant_pairing_action(self, mode):
        self.ensure_one()
        code = self._ai_assistant_start_pairing(mode)
        return {
            "type": "ir.actions.act_window",
            "res_model": "ai.assistant.pairing",
            "name": self.env._("Connect your AI assistant"),
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_prompt": self._ai_assistant_pairing_prompt(code, mode)},
        }

    def _ai_assistant_revoke_active(self):
        accesses = self.sudo().ai_assistant_access_ids.filtered(
            lambda access: access.state in (STATE_ACTIVE, STATE_PENDING)
        )
        accesses._revoke()

    @check_identity
    def action_ai_assistant_pair_read(self):
        return self._ai_assistant_pairing_action(ACCESS_MODE_READ)

    @check_identity
    def action_ai_assistant_pair_edit(self):
        return self._ai_assistant_pairing_action(ACCESS_MODE_READ_WRITE)

    def action_ai_assistant_revoke(self):
        self.ensure_one()
        if self != self.env.user and not self.env.user._is_system():
            raise AccessError(
                self.env._("Only the owner or an administrator can revoke AI assistant access.")
            )
        self._ai_assistant_revoke_active()
        return True

    def write(self, vals):
        result = super().write(vals)
        if "active" in vals and not vals["active"]:
            for user in self:
                user._ai_assistant_revoke_active()
        return result
