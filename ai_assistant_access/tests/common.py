from odoo import Command

TEST_FIELD_BLACKLIST = "vat|password|passwd|secret|token|api_key|apikey|totp|oauth|signature"

TEST_PARAMS = {
    "ai_assistant.enabled": "True",
    "ai_assistant.allow_http": "True",
    "ai_assistant.key_days": "90",
    "ai_assistant.rate_limit_per_min": "60",
    "ai_assistant.field_blacklist_regex": TEST_FIELD_BLACKLIST,
}


class AiAssistantFixtures:
    @classmethod
    def _setup_ai_assistant(cls):
        params = cls.env["ir.config_parameter"].sudo()
        for key, value in TEST_PARAMS.items():
            params.set_param(key, value)
        cls.group_user = cls.env.ref("base.group_user")
        cls.eva = cls._create_internal_user("Eva Tester", "eva.ai.assistant.test")
        cls.karol = cls._create_internal_user("Karol Tester", "karol.ai.assistant.test")

    @classmethod
    def _create_internal_user(cls, name, login):
        return cls.env["res.users"].create(
            {
                "name": name,
                "login": login,
                "password": "AiAssistantTest2026!",
                "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
            }
        )

    @classmethod
    def _generate_access_as_owner(cls, user, mode="read"):
        code = cls._start_pairing_as_owner(user, mode)
        return cls.env["ai.assistant.access"]._redeem_pairing_code(code)[1]

    @classmethod
    def _start_pairing_as_owner(cls, user, mode="read"):
        return user.with_user(user)._ai_assistant_start_pairing(mode)

    @classmethod
    def _set_editing_allowed(cls, allowed):
        cls.env["ir.config_parameter"].sudo().set_param("ai_assistant.allow_edit", str(allowed))

    @classmethod
    def _restrict_to_own_partners(cls, user):
        group = cls.env["res.groups"].create({"name": f"AI test - own contacts {user.id}"})
        cls.env["ir.rule"].create(
            {
                "name": f"AI test - only own contacts {user.id}",
                "model_id": cls.env["ir.model"]._get_id("res.partner"),
                "domain_force": "[('user_id', '=', user.id)]",
                "groups": [Command.set([group.id])],
                "perm_read": True,
                "perm_write": True,
                "perm_create": True,
                "perm_unlink": True,
            }
        )
        user.write({"group_ids": [Command.link(group.id)]})
        return group
