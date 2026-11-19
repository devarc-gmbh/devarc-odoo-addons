import time
from collections import deque
from unittest.mock import patch

from lxml import etree

from odoo import Command, api
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools.misc import file_open

from ..controllers import ai_assistant_api
from ..models import ai_assistant_guard
from ..models.ai_assistant_guard import AiAssistantError
from .common import TEST_FIELD_BLACKLIST, AiAssistantFixtures


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestAiAssistantGuard(AiAssistantFixtures, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_ai_assistant()
        cls.config = ai_assistant_guard.build_config(
            ai_assistant_guard.DEFAULT_ALLOWED_MODELS, TEST_FIELD_BLACKLIST
        )

    def test_hard_blacklist_covers_secret_models(self):
        for model_name in (
            "res.users",
            "res.users.apikeys",
            "res.users.settings",
            "ir.config_parameter",
            "ir.mail_server",
            "fetchmail.server",
            "payment.provider",
            "payment.token",
            "auth_oauth.provider",
            "auth_totp.device",
            "ir.attachment",
            "ir.logging",
            "bus.bus",
            "ir.model.access",
            "ir.rule",
            "ir.module.module",
            "ai.assistant.access",
            "ai.assistant.audit",
            "ir.actions.server",
            "ir.actions.act_window",
            "ir.actions.actions",
            "ir.cron",
            "ir.ui.view",
            "ir.ui.menu",
            "ir.model",
            "ir.model.fields",
            "ir.model.data",
            "ir.default",
            "res.groups",
            "res.groups.privilege",
            "base.automation",
            "mail.template",
        ):
            self.assertTrue(ai_assistant_guard.is_hard_blacklisted(model_name), model_name)

    def test_business_models_are_not_blacklisted(self):
        for model_name in ("res.partner", "res.company", "crm.lead", "sale.order"):
            self.assertFalse(ai_assistant_guard.is_hard_blacklisted(model_name), model_name)

    def test_admin_whitelist_cannot_override_blacklist(self):
        config = ai_assistant_guard.build_config(
            ("res.partner", "ir.config_parameter", "res.users"), TEST_FIELD_BLACKLIST
        )
        for model_name in ("ir.config_parameter", "res.users"):
            with self.assertRaises(AiAssistantError) as caught:
                ai_assistant_guard.check_model_allowed(model_name, config["allowed_models"])
            self.assertEqual(caught.exception.code, "model_not_allowed")
            self.assertEqual(caught.exception.status, 403)

    def test_parse_model_list(self):
        self.assertEqual(
            ai_assistant_guard.parse_model_list("res.partner, crm.lead\nsale.order,,"),
            ("res.partner", "crm.lead", "sale.order"),
        )

    def test_limit_is_clamped(self):
        self.assertEqual(ai_assistant_guard.clamp_limit(1000), 200)
        self.assertEqual(ai_assistant_guard.clamp_limit(None), 200)
        self.assertEqual(ai_assistant_guard.clamp_limit(0), 200)
        self.assertEqual(ai_assistant_guard.clamp_limit(5), 5)

    def test_fields_param_is_required_and_bounded(self):
        with self.assertRaises(AiAssistantError) as missing:
            ai_assistant_guard.check_fields_param(None)
        self.assertEqual(missing.exception.code, "missing_fields")
        with self.assertRaises(AiAssistantError) as too_many:
            ai_assistant_guard.check_fields_param(["name"] * 51)
        self.assertEqual(too_many.exception.code, "too_many_fields")

    def test_binary_and_secret_fields_are_filtered(self):
        partner = self.env["res.partner"]
        self.assertEqual(
            ai_assistant_guard.filter_field_names(
                partner, ["name", "image_1920", "vat", "unknown_field"], TEST_FIELD_BLACKLIST
            ),
            ["name"],
        )

    def test_domain_rejects_dotted_field_paths(self):
        partner = self.env["res.partner"]
        for domain in (
            [("user_ids.oauth_access_token", "like", "a%")],
            ["|", ("name", "=", "x"), ("user_ids.password", "!=", False)],
            [("child_ids.vat", "=", "SK1")],
        ):
            with self.assertRaises(AiAssistantError) as caught:
                ai_assistant_guard.check_domain(partner, domain, TEST_FIELD_BLACKLIST)
            self.assertEqual(caught.exception.code, "bad_request")
            self.assertEqual(caught.exception.status, 400)

    def test_domain_rejects_blacklisted_and_unknown_fields(self):
        partner = self.env["res.partner"]
        for field_name in ("vat", "image_1920", "no_such_field"):
            with self.assertRaises(AiAssistantError) as caught:
                ai_assistant_guard.check_domain(
                    partner, [(field_name, "=", "x")], TEST_FIELD_BLACKLIST
                )
            self.assertEqual(caught.exception.code, "field_not_allowed", field_name)
            self.assertEqual(caught.exception.status, 403, field_name)

    def test_domain_rejects_malformed_leaves(self):
        partner = self.env["res.partner"]
        for domain in ("name", [("name", "=")], [(1, "=", 1)], [("name", 1, "x")], ["+"]):
            with self.assertRaises(AiAssistantError) as caught:
                ai_assistant_guard.check_domain(partner, domain, TEST_FIELD_BLACKLIST)
            self.assertEqual(caught.exception.code, "bad_request")

    def test_domain_accepts_plain_leaves_and_logic_operators(self):
        partner = self.env["res.partner"]
        domain = ["|", ("name", "ilike", "a"), "!", ("id", "in", [1, 2])]
        self.assertEqual(
            ai_assistant_guard.check_domain(partner, domain, TEST_FIELD_BLACKLIST), domain
        )
        self.assertEqual(ai_assistant_guard.check_domain(partner, None, TEST_FIELD_BLACKLIST), [])

    def test_order_is_validated_like_a_domain_field(self):
        partner = self.env["res.partner"]
        self.assertEqual(
            ai_assistant_guard.check_order(partner, "name asc, id desc", TEST_FIELD_BLACKLIST),
            "name asc, id desc",
        )
        self.assertIsNone(ai_assistant_guard.check_order(partner, "", TEST_FIELD_BLACKLIST))
        for order, code in (
            ("user_ids.password", "bad_request"),
            ("name sideways", "bad_request"),
            ("name asc desc", "bad_request"),
            (["name"], "bad_request"),
            ("vat", "field_not_allowed"),
        ):
            with self.assertRaises(AiAssistantError) as caught:
                ai_assistant_guard.check_order(partner, order, TEST_FIELD_BLACKLIST)
            self.assertEqual(caught.exception.code, code, order)

    def test_audit_values_are_sanitized(self):
        self.assertEqual(
            ai_assistant_guard.sanitize_audit_op("search_read\ninjected ok"),
            "search_read_injected_ok",
        )
        self.assertEqual(len(ai_assistant_guard.sanitize_audit_op("x" * 500)), 64)
        self.assertFalse(ai_assistant_guard.sanitize_audit_op(None))
        self.assertEqual(len(ai_assistant_guard.sanitize_audit_model("m" * 500)), 128)
        self.assertFalse(ai_assistant_guard.sanitize_audit_model({"model": "res.partner"}))

    def test_message_domain_is_scoped_to_whitelist(self):
        domain = ai_assistant_guard.scoped_domain(
            "mail.message", [("id", ">", 0)], ("res.partner", "crm.lead")
        )
        self.assertEqual(domain[0], ("model", "in", ["res.partner", "crm.lead"]))
        domain = ai_assistant_guard.scoped_domain("mail.activity", [], ("res.partner",))
        self.assertEqual(domain, [("res_model", "in", ["res.partner"])])
        self.assertEqual(ai_assistant_guard.scoped_domain("res.partner", [], ("res.partner",)), [])

    def test_message_scope_never_includes_hard_blacklisted_models(self):
        domain = ai_assistant_guard.scoped_domain(
            "mail.message", [], ("res.partner", "res.users", "ir.attachment")
        )
        self.assertEqual(domain, [("model", "in", ["res.partner"])])

    def test_only_seven_read_operations_exist(self):
        self.assertEqual(
            set(ai_assistant_guard.ALLOWED_OPERATIONS),
            {
                "models",
                "fields_get",
                "search_read",
                "search_count",
                "read",
                "name_search",
                "read_group",
            },
        )
        self.assertEqual(len(ai_assistant_guard.ALLOWED_OPERATIONS), 7)

    def test_read_group_sums_only_numeric_fields(self):
        payload = {"model": "res.partner", "groupby": ["user_id"], "fields": ["name"]}
        partner_model = type(self.env["res.partner"])
        with patch.object(partner_model, "_read_group") as read_group, self.assertRaises(AiAssistantError) as caught:
            ai_assistant_guard.run_operation(self.env, "read_group", payload, self.config)
        self.assertEqual(caught.exception.code, "bad_request")
        self.assertEqual(caught.exception.status, 400)
        read_group.assert_not_called()
        result = ai_assistant_guard.run_operation(
            self.env, "read_group", dict(payload, fields=["color"]), self.config
        )
        self.assertIn("color:sum", result["groups"][0])

    def test_hello_is_not_a_guard_operation(self):
        with self.assertRaises(AiAssistantError) as caught:
            ai_assistant_guard.run_operation(self.env, "hello", {}, self.config)
        self.assertEqual(caught.exception.code, "unknown_op")
        self.assertEqual(caught.exception.status, 400)

    def test_write_operations_are_unknown(self):
        for op in ("execute_kw", "write", "create", "unlink", "action_confirm", "message_post"):
            with self.assertRaises(AiAssistantError) as caught:
                ai_assistant_guard.run_operation(self.env, op, {}, self.config)
            self.assertEqual(caught.exception.code, "unknown_op")
            self.assertEqual(caught.exception.status, 400)

    def test_models_operation_skips_uninstalled_models(self):
        config = ai_assistant_guard.build_config(
            ("res.partner", "model.that.does.not.exist"), TEST_FIELD_BLACKLIST
        )
        result = ai_assistant_guard.run_operation(self.env, "models", {}, config)
        self.assertEqual([entry["model"] for entry in result["models"]], ["res.partner"])

    def test_fields_get_hides_binary_and_secret_fields(self):
        result = ai_assistant_guard.run_operation(
            self.env, "fields_get", {"model": "res.partner"}, self.config
        )
        self.assertIn("name", result["fields"])
        self.assertNotIn("image_1920", result["fields"])
        self.assertNotIn("vat", result["fields"])

    def _assert_write_error(self, op, payload, code, status=400, config=None):
        with self.assertRaises(AiAssistantError) as caught:
            ai_assistant_guard.run_write_operation(self.env, op, payload, config or self.config)
        self.assertEqual(caught.exception.code, code, payload)
        self.assertEqual(caught.exception.status, status, payload)

    def test_only_create_and_write_are_write_operations(self):
        self.assertEqual(ai_assistant_guard.ALLOWED_WRITE_OPERATIONS, ("create", "write"))
        self.assertFalse(
            set(ai_assistant_guard.ALLOWED_WRITE_OPERATIONS)
            & set(ai_assistant_guard.ALLOWED_OPERATIONS)
        )
        for op in ("unlink", "search_read", "execute_kw", "copy"):
            self._assert_write_error(op, {"model": "res.partner"}, "unknown_op")

    def test_write_config_defaults_to_writable_models(self):
        self.assertEqual(
            self.config["writable_models"], ai_assistant_guard.DEFAULT_WRITABLE_MODELS
        )

    def test_create_and_write_scalar_values(self):
        created = ai_assistant_guard.run_write_operation(
            self.env,
            "create",
            {"model": "res.partner", "values": {"name": "AI guard"}},
            self.config,
        )
        partner = self.env["res.partner"].browse(created["id"])
        self.assertEqual(created["model"], "res.partner")
        self.assertEqual(partner.name, "AI guard")
        written = ai_assistant_guard.run_write_operation(
            self.env,
            "write",
            {
                "model": "res.partner",
                "ids": [partner.id],
                "values": {"comment": "note", "city": "Bern"},
            },
            self.config,
        )
        self.assertEqual(written, {"model": "res.partner", "ids": [partner.id], "length": 1})
        self.assertEqual(partner.city, "Bern")

    def test_model_must_be_writable(self):
        self._assert_write_error(
            "write",
            {"model": "account.move", "ids": [1], "values": {"ref": "x"}},
            "model_not_writable",
            403,
        )
        self._assert_write_error(
            "create", {"model": "ir.ui.view", "values": {"name": "x"}}, "model_not_allowed", 403
        )
        config = ai_assistant_guard.build_config(
            ("res.partner", "res.users"),
            TEST_FIELD_BLACKLIST,
            ("res.partner", "res.users"),
        )
        self._assert_write_error(
            "create",
            {"model": "res.users", "values": {"name": "x"}},
            "model_not_allowed",
            403,
            config,
        )

    def test_protected_and_relational_list_fields_are_not_writable(self):
        partner = self.env["res.partner"].create({"name": "AI guard parent"})
        child = self.env["res.partner"].create({"name": "AI guard child", "parent_id": partner.id})
        for values in (
            {"child_ids": [[2, child.id]]},
            {"child_ids": [[5]]},
            {"child_ids": []},
            {"category_id": [[6, 0, []]]},
            {"active": False},
            {"company_id": 1},
            {"create_uid": 1},
            {"id": 5},
            {"vat": "CHE-1"},
            {"image_1920": False},
            {"no_such_field": 1},
        ):
            self._assert_write_error(
                "write",
                {"model": "res.partner", "ids": [partner.id], "values": values},
                "field_not_writable",
            )
        self.assertTrue(child.exists())
        self.assertEqual(child.parent_id, partner)

    def test_values_must_be_flat_scalars(self):
        for values in (
            {"name": ["a"]},
            {"name": {"a": 1}},
            {"country_id": "3"},
            {"country_id": True},
            {"country_id": [1, "x"]},
            {},
            ["name"],
            None,
        ):
            self._assert_write_error(
                "create", {"model": "res.partner", "values": values}, "bad_request"
            )

    def test_write_ids_are_validated_and_bounded(self):
        for ids in (None, [], ["1"], [True], list(range(1, 52))):
            self._assert_write_error(
                "write",
                {"model": "res.partner", "ids": ids, "values": {"name": "x"}},
                "bad_request",
            )

    def test_domain_subqueries_are_checked_like_the_top_level(self):
        partner = self.env["res.partner"]
        allowed = self.config["allowed_models"]
        for domain, code in (
            ([("user_ids", "any", [("login", "=", "admin")])], "model_not_allowed"),
            ([("user_ids", "not any", [("api_key_ids", "!=", False)])], "model_not_allowed"),
            ([("child_ids", "any", [("vat", "=", "CHE-1")])], "field_not_allowed"),
            ([("child_ids", "any", [("user_ids.login", "=", "admin")])], "bad_request"),
            ([("child_ids", "any!", [("name", "=", "x")])], "bad_request"),
            ([("child_ids", "ANY", "name")], "bad_request"),
            ([("name", "any", [("id", "=", 1)])], "bad_request"),
            ([("user_ids", "in", [["login", "=", "admin"]])], "bad_request"),
            ([("child_ids", "in", ["|", ("name", "=", "x")])], "bad_request"),
            ([("user_ids", "ilike", "admin")], "model_not_allowed"),
            ([("name", "in", [{"a": 1}])], "bad_request"),
            ([("name", "=", {"a": 1})], "bad_request"),
        ):
            with self.assertRaises(AiAssistantError) as caught:
                ai_assistant_guard.check_domain(partner, domain, TEST_FIELD_BLACKLIST, allowed)
            self.assertEqual(caught.exception.code, code, domain)
        with self.assertRaises(AiAssistantError) as caught:
            ai_assistant_guard.check_domain(
                partner,
                [("user_ids", "ilike", "admin")],
                TEST_FIELD_BLACKLIST,
                allowed + ("res.users",),
            )
        self.assertEqual(caught.exception.code, "model_not_allowed")
        domain = [("name", "in", ["|", "x"])]
        self.assertEqual(
            ai_assistant_guard.check_domain(partner, domain, TEST_FIELD_BLACKLIST, allowed), domain
        )
        domain = [("child_ids", "any", [("name", "=", "x")]), ("user_ids", "in", [1, 2])]
        self.assertEqual(
            ai_assistant_guard.check_domain(partner, domain, TEST_FIELD_BLACKLIST, allowed), domain
        )
        with self.assertRaises(AiAssistantError) as caught:
            ai_assistant_guard.check_domain(partner, domain[:1], TEST_FIELD_BLACKLIST)
        self.assertEqual(caught.exception.code, "model_not_allowed")

    def test_activity_reads_are_scoped_to_allowed_models(self):
        partner = self.env["res.partner"].create({"name": "AI activity target"})
        activity = partner.activity_schedule("mail.mail_activity_data_todo", summary="AI plan")
        payload = {
            "model": "mail.activity",
            "domain": [("id", "=", activity.id)],
            "fields": ["summary"],
        }
        scoped = ai_assistant_guard.build_config(("mail.activity",), TEST_FIELD_BLACKLIST)
        for op in ("search_read", "search_count"):
            result = ai_assistant_guard.run_operation(self.env, op, payload, scoped)
            self.assertEqual(result.get("length", result.get("count")), 0, op)
        result = ai_assistant_guard.run_operation(self.env, "search_read", payload, self.config)
        self.assertEqual(result["records"], [{"id": activity.id, "summary": "AI plan"}])

    def test_activity_writes_need_an_allowed_document_model(self):
        partner = self.env["res.partner"].create({"name": "AI activity owner"})
        activity = partner.activity_schedule("mail.mail_activity_data_todo", summary="AI plan")
        scoped = ai_assistant_guard.build_config(
            ("mail.activity",), TEST_FIELD_BLACKLIST, ("mail.activity",)
        )
        partner_model_id = self.env["ir.model"]._get_id("res.partner")
        users_model_id = self.env["ir.model"]._get_id("res.users")
        for op, payload, config in (
            ("write", {"ids": [activity.id], "values": {"summary": "x"}}, scoped),
            ("create", {"values": {"summary": "x", "res_id": partner.id}}, self.config),
            (
                "create",
                {"values": {"summary": "x", "res_model_id": users_model_id, "res_id": 1}},
                self.config,
            ),
            (
                "write",
                {"ids": [activity.id], "values": {"res_model_id": users_model_id}},
                self.config,
            ),
        ):
            self._assert_write_error(
                op, {"model": "mail.activity", **payload}, "model_not_allowed", 403, config
            )
        created = ai_assistant_guard.run_write_operation(
            self.env,
            "create",
            {
                "model": "mail.activity",
                "values": {
                    "summary": "AI follow up",
                    "res_model_id": partner_model_id,
                    "res_id": partner.id,
                    "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                },
            },
            self.config,
        )
        self.assertEqual(self.env["mail.activity"].browse(created["id"]).res_model, "res.partner")
        self.assertEqual(activity.summary, "AI plan")
        for res_id in (2147483000, 0, True, "1"):
            self._assert_write_error(
                "write",
                {"model": "mail.activity", "ids": [activity.id], "values": {"res_id": res_id}},
                "bad_request",
            )
        self.assertEqual(activity.res_id, partner.id)

    def test_only_plain_stored_fields_are_writable(self):
        partner = self.env["res.partner"].create({"name": "AI plain fields"})
        for values in (
            {"display_name": "x"},
            {"country_code": "CH"},
            {"commercial_partner_id": partner.id},
            {"commercial_company_name": "x"},
        ):
            self._assert_write_error(
                "write",
                {"model": "res.partner", "ids": [partner.id], "values": values},
                "field_not_writable",
            )
        if "crm.lead" not in self.env:
            return
        lead = self.env["crm.lead"].create({"name": "AI guard lead"})
        for values in (
            {"activity_summary": "x"},
            {"activity_type_id": self.env.ref("mail.mail_activity_data_todo").id},
            {"company_id": False},
            {"email_from": "x@example.com"},
        ):
            self._assert_write_error(
                "write",
                {"model": "crm.lead", "ids": [lead.id], "values": values},
                "field_not_writable",
            )
        stage = self.env["crm.stage"].create({"name": "AI guard stage"})
        ai_assistant_guard.run_write_operation(
            self.env,
            "write",
            {"model": "crm.lead", "ids": [lead.id], "values": {"stage_id": stage.id}},
            self.config,
        )
        self.assertEqual(lead.stage_id, stage)

    def test_stored_computed_relations_are_writable_but_company_is_kept(self):
        if "project.task" not in self.env:
            self.skipTest("project is not installed")
        company_c = self.env["res.company"].create({"name": "AI guard project company C"})
        project_a = self.env["project.project"].create({"name": "AI project A"})
        project_b = self.env["project.project"].create({"name": "AI project B"})
        project_c = self.env["project.project"].create(
            {"name": "AI project C", "company_id": company_c.id}
        )
        stage = self.env["project.task.type"].create(
            {"name": "AI stage", "project_ids": [Command.set((project_a | project_b).ids)]}
        )
        task = self.env["project.task"].create({"name": "AI task", "project_id": project_a.id})
        company = task.company_id
        main_only = self.env(context={"allowed_company_ids": [self.env.company.id]})
        for values in ({"stage_id": stage.id}, {"project_id": project_b.id}):
            ai_assistant_guard.run_write_operation(
                main_only,
                "write",
                {"model": "project.task", "ids": [task.id], "values": values},
                self.config,
            )
        self.assertEqual((task.stage_id, task.project_id), (stage, project_b))
        self.assertEqual(task.company_id, company)
        with self.assertRaises(AiAssistantError) as caught:
            ai_assistant_guard.run_write_operation(
                main_only,
                "write",
                {
                    "model": "project.task",
                    "ids": [task.id],
                    "values": {"project_id": project_c.id},
                },
                self.config,
            )
        self.assertEqual(caught.exception.code, "protected_field_changed")

    def test_writable_fields_never_reach_company_active_or_x2many_related(self):
        offenders = []
        for model_name in ai_assistant_guard.DEFAULT_WRITABLE_MODELS:
            if model_name not in self.env:
                continue
            model = self.env[model_name]
            for name, field in model._fields.items():
                try:
                    ai_assistant_guard.check_write_values(
                        model, {name: False}, TEST_FIELD_BLACKLIST
                    )
                except AiAssistantError:
                    continue
                if field.related or (field.relational and field.comodel_name == "res.company"):
                    offenders.append(f"{model_name}.{name}")
        self.assertFalse(offenders, " | ".join(offenders))

    def test_partner_identity_fields_and_user_contacts_are_protected(self):
        partner = self.env["res.partner"].create({"name": "AI identity"})
        parent = self.env["res.partner"].create({"name": "AI identity parent", "is_company": True})
        for values in ({"email": "x@example.com"}, {"parent_id": parent.id}, {"parent_id": False}):
            self._assert_write_error(
                "write",
                {"model": "res.partner", "ids": [partner.id], "values": values},
                "field_not_writable",
            )
            created = ai_assistant_guard.run_write_operation(
                self.env,
                "create",
                {"model": "res.partner", "values": {"name": "AI child", **values}},
                self.config,
            )
            self.assertTrue(self.env["res.partner"].browse(created["id"]).exists())
        company_partner = self.env.company.partner_id
        for op, payload in (
            ("write", {"ids": [self.eva.partner_id.id], "values": {"city": "x"}}),
            ("write", {"ids": [company_partner.id], "values": {"city": "x"}}),
            ("create", {"values": {"name": "AI child", "parent_id": self.eva.partner_id.id}}),
            ("create", {"values": {"name": "AI child", "parent_id": company_partner.id}}),
        ):
            self._assert_write_error(
                op, {"model": "res.partner", **payload}, "record_protected", 403
            )
        self.assertNotEqual(self.eva.partner_id.city, "x")
        self.assertNotEqual(company_partner.city, "x")

    def test_company_flag_change_above_user_contact_is_rejected(self):
        group = self.env["res.partner"].create({"name": "AI group", "is_company": True})
        firm = self.env["res.partner"].create(
            {"name": "AI subsidiary", "is_company": True, "parent_id": group.id}
        )
        contact = self.env["res.partner"].create(
            {"name": "AI portal contact", "parent_id": firm.id}
        )
        new_test_user(
            self.env,
            login="ai_portal_firm_contact",
            groups="base.group_portal",
            partner_id=contact.id,
        )
        self._assert_write_error(
            "write",
            {"model": "res.partner", "ids": [firm.id], "values": {"is_company": False}},
            "record_protected",
            403,
        )
        self.assertEqual(contact.commercial_partner_id, firm)

    def test_archived_records_are_not_writable(self):
        partner = self.env["res.partner"].create({"name": "AI archived", "active": False})
        self._assert_write_error(
            "write",
            {"model": "res.partner", "ids": [partner.id], "values": {"city": "Bern"}},
            "record_archived",
            403,
        )
        self.assertFalse(partner.city)

    def test_indirect_change_of_active_or_company_is_rejected(self):
        partner = self.env["res.partner"].create({"name": "AI indirect"})
        company_b = self.env["res.company"].create({"name": "AI guard company B"})
        partner_class = type(self.env["res.partner"])
        original_write = partner_class.write
        for side_effect in ({"active": False}, {"company_id": company_b.id}):

            def write_with_side_effect(records, vals, side_effect=side_effect):
                result = original_write(records, vals)
                if "comment" in vals:
                    original_write(records, side_effect)
                return result

            with patch.object(partner_class, "write", write_with_side_effect):
                self._assert_write_error(
                    "write",
                    {"model": "res.partner", "ids": [partner.id], "values": {"comment": "x"}},
                    "protected_field_changed",
                    403,
                )
            original_write(partner, {"active": True, "company_id": False})
        if "crm.lead" not in self.env:
            return
        company_c = self.env["res.company"].create({"name": "AI guard company C"})
        team_c = self.env["crm.team"].create({"name": "AI team C", "company_id": company_c.id})
        lead_class = type(self.env["crm.lead"])
        original_create = lead_class.create

        @api.model_create_multi
        def create_in_company_c(model, vals_list):
            return original_create(model, [dict(vals, team_id=team_c.id) for vals in vals_list])

        main_only = self.env(context={"allowed_company_ids": [self.env.company.id]})
        with (
            patch.object(lead_class, "create", create_in_company_c),
            self.assertRaises(AiAssistantError) as caught,
        ):
            ai_assistant_guard.run_write_operation(
                main_only,
                "create",
                {"model": "crm.lead", "values": {"name": "AI lead C"}},
                self.config,
            )
        self.assertEqual(caught.exception.code, "protected_field_changed")


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestAiAssistantInstalledDefaults(TransactionCase):
    def test_installed_field_blacklist_blocks_every_default_alternative(self):
        params = self.env["ir.config_parameter"].sudo()
        controller = ai_assistant_api.AiAssistantApi()
        installed = controller._field_pattern(params)
        for name in ("password", "x_signature", "signature", "api_key", "totp_secret"):
            self.assertTrue(ai_assistant_guard.is_field_blacklisted(name, installed), name)
        self.assertFalse(ai_assistant_guard.is_field_blacklisted("name", installed))
        params.set_param("ai_assistant.field_blacklist_regex", "  vat\n")
        custom = controller._field_pattern(params)
        for name in ("vat", "password", "signature"):
            self.assertTrue(ai_assistant_guard.is_field_blacklisted(name, custom), name)
        params.set_param("ai_assistant.field_blacklist_regex", "(?i)salary")
        flagged = controller._field_pattern(params)
        self.assertEqual(flagged, ai_assistant_guard.DEFAULT_FIELD_BLACKLIST_REGEX)
        self.assertTrue(ai_assistant_guard.is_field_blacklisted("password", flagged))
        with file_open("ai_assistant_access/data/ir_config_parameter.xml", "rb") as handle:
            tree = etree.parse(handle)
        value = tree.find(
            ".//record[@id='param_ai_assistant_field_blacklist']/field[@name='value']"
        )
        self.assertEqual(value.text, ai_assistant_guard.DEFAULT_FIELD_BLACKLIST_REGEX)

    def test_rate_limit_buckets_are_purged_and_capped(self):
        controller = ai_assistant_api.AiAssistantApi()
        hits = ai_assistant_api._pair_rate_hits
        hits.clear()
        ai_assistant_api._rate_hits.clear()
        ai_assistant_api._rate_purged_at.clear()
        with (
            patch.object(ai_assistant_api, "MAX_PAIR_RATE_BUCKETS", 3),
            patch.object(ai_assistant_api, "RATE_PURGE_INTERVAL_SECONDS", 3600),
        ):
            for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
                controller._check_rate_limit(ip, 5, pair=True)
            with self.assertRaises(AiAssistantError) as caught:
                controller._check_rate_limit("10.0.0.4", 5, pair=True)
            self.assertEqual(
                (caught.exception.code, caught.exception.status), ("rate_limited", 429)
            )
            controller._check_rate_limit(1, 5)
            controller._check_rate_limit("10.0.0.1", 5, pair=True)
            for bucket in hits:
                hits[bucket] = deque([time.monotonic() - 120])
            with self.assertRaises(AiAssistantError):
                controller._check_rate_limit("10.0.0.4", 5, pair=True)
            ai_assistant_api._rate_purged_at.clear()
            controller._check_rate_limit("10.0.0.4", 5, pair=True)
        self.assertEqual(set(hits), {"10.0.0.4"})
        self.assertEqual(set(ai_assistant_api._rate_hits), {1})
        hits.clear()
        ai_assistant_api._rate_hits.clear()
