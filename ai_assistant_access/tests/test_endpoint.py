import json
import logging
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import psycopg2
import werkzeug.wrappers
from freezegun import freeze_time

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import HttpCase
from odoo.tools import mute_logger

from ..controllers import ai_assistant_api
from ..models import ai_assistant_guard
from .common import AiAssistantFixtures

WRITE_PROBE_NAME = "AI assistant write probe"


def _write_probe(env, op, payload, config):
    env["res.partner"].sudo().create({"name": WRITE_PROBE_NAME})
    return {"records": []}


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestAiAssistantEndpoint(AiAssistantFixtures, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_ai_assistant()
        cls._restrict_to_own_partners(cls.karol)
        cls.eva_key = cls._generate_access_as_owner(cls.eva)
        cls.karol_key = cls._generate_access_as_owner(cls.karol)
        cls.partner_eva = cls.env["res.partner"].create(
            {
                "name": "Kontakt Evy AI",
                "user_id": cls.eva.id,
            }
        )
        cls.partner_karol = cls.env["res.partner"].create(
            {
                "name": "Kontakt Karola AI",
                "user_id": cls.karol.id,
            }
        )
        cls.company_b = cls.env["res.company"].create({"name": "AI Test Firma B"})
        cls.company_c = cls.env["res.company"].create({"name": "AI Test Firma C"})
        cls.eva.write(
            {
                "company_ids": [Command.link(cls.company_b.id)],
            }
        )
        cls.partner_b = cls.env["res.partner"].create(
            {
                "name": "Kontakt firmy B AI",
                "company_id": cls.company_b.id,
            }
        )
        cls.partner_c = cls.env["res.partner"].create(
            {
                "name": "Kontakt firmy C AI",
                "company_id": cls.company_c.id,
            }
        )

    def setUp(self):
        super().setUp()
        ai_assistant_api._rate_hits.clear()
        ai_assistant_api._pair_rate_hits.clear()
        ai_assistant_api._rate_purged_at.clear()

    def _post(self, op, payload=None, key=None, with_key=True):
        headers = {}
        if with_key:
            headers["Authorization"] = f"Bearer {key or self.eva_key}"
        body = dict(payload or {})
        body.setdefault("db", self.env.cr.dbname)
        return self.url_open(
            f"/ai_assistant/v1/{op}",
            data=json.dumps(body),
            headers={**(headers or {}), "Content-Type": "application/json"},
        )

    def _partner_names(self, response):
        return {record["name"] for record in response.json()["records"]}

    def test_search_read_returns_records(self):
        response = self._post(
            "search_read",
            {
                "model": "res.partner",
                "domain": [["id", "=", self.partner_eva.id]],
                "fields": ["name", "user_id"],
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["model"], "res.partner")
        self.assertEqual(payload["length"], 1)
        self.assertEqual(payload["records"][0]["name"], self.partner_eva.name)

    def test_missing_key_is_rejected(self):
        response = self._post(
            "search_read", {"model": "res.partner", "fields": ["name"]}, with_key=False
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_key")

    def test_rpc_scope_key_is_rejected(self):
        rpc_key = (
            self.env["res.users.apikeys"]
            .with_user(self.karol)
            .sudo()
            ._generate(None, "AI test rpc key", datetime.now() + timedelta(days=30))
        )
        response = self._post(
            "search_read", {"model": "res.partner", "fields": ["name"]}, key=rpc_key
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_key")

    def test_unpaired_key_of_the_owner_is_rejected(self):
        orphan_key = (
            self.env["res.users.apikeys"]
            .with_user(self.eva)
            .sudo()
            ._generate(
                ai_assistant_guard.AI_KEY_SCOPE, "AI test orphan key", datetime.now() + timedelta(days=30)
            )
        )
        response = self._post(
            "search_read", {"model": "res.partner", "fields": ["name"]}, key=orphan_key
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "revoked")

    def test_oversized_body_is_rejected_before_it_is_read(self):
        body = {"model": "res.partner", "fields": ["name"], "pad": "x" * ai_assistant_guard.MAX_REQUEST_BYTES}
        with patch.object(
            werkzeug.wrappers.Request, "get_data", autospec=True,
            side_effect=werkzeug.wrappers.Request.get_data,
        ) as get_data:
            response = self._post("search_read", body)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(response.json()["error"]["code"], "request_too_large")
        get_data.assert_not_called()

    def test_request_count_is_incremented_in_sql(self):
        access = self.env["ai.assistant.access"].sudo().search([("user_id", "=", self.eva.id)], limit=1)
        count = access.request_count
        self.env.cr.execute(
            "UPDATE ai_assistant_access SET request_count = request_count + 10 WHERE id = %s", [access.id]
        )
        access._record_usage()
        self.assertEqual(access.request_count, count + 11)
        self.assertTrue(access.last_used_at)

    def test_invalid_read_query_is_logged_without_data(self):
        with self.assertLogs("odoo.addons.ai_assistant_access.controllers.ai_assistant_api", "WARNING") as logs:
            response = self._post(
                "read_group",
                {"model": "res.partner", "groupby": ["display_name"], "fields": ["color"]},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "bad_request")
        self.assertTrue(
            any("read_group on res.partner rejected the query (ValueError)" in line for line in logs.output),
            logs.output,
        )

    def test_invalid_read_query_is_a_bad_request(self):
        with mute_logger("odoo.addons.ai_assistant_access.controllers.ai_assistant_api"):
            response = self._post(
                "read_group",
                {"model": "res.partner", "groupby": ["user_id"], "fields": ["name"]},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "bad_request")

    def test_audit_survives_a_failed_access_update(self):
        access_model = self.env["ai.assistant.access"].sudo()
        access = access_model.search([("user_id", "=", self.eva.id)], limit=1)
        count = access.request_count
        audit_model = self.env["ai.assistant.audit"].sudo()
        before = audit_model.search_count([])

        def record_usage_failing(records):
            raise psycopg2.errors.SerializationFailure()

        with patch.object(type(access_model), "_record_usage", record_usage_failing), self.assertLogs(
            "odoo.addons.ai_assistant_access.controllers.ai_assistant_api", "ERROR"
        ) as logs:
            response = self._post("search_count", {"model": "res.partner", "domain": []})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("access usage could not be updated" in line for line in logs.output), logs.output)
        self.assertEqual(audit_model.search_count([]), before + 1)
        access.invalidate_recordset()
        self.assertEqual(access.request_count, count)

    def test_unknown_operations_are_rejected(self):
        for op in ("execute_kw", "unlink", "action_confirm"):
            response = self._post(op, {"model": "res.partner", "fields": ["name"]})
            self.assertEqual(response.status_code, 400, op)
            self.assertEqual(response.json()["error"]["code"], "unknown_op", op)

    def test_model_outside_whitelist_is_rejected(self):
        response = self._post("search_read", {"model": "ir.ui.view", "fields": ["name"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "model_not_allowed")

    def test_blacklisted_model_stays_blocked_when_whitelisted(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "ai_assistant.allowed_models", "res.partner,ir.config_parameter,ir.mail_server"
        )
        for model_name in ("ir.config_parameter", "ir.mail_server"):
            response = self._post("search_read", {"model": model_name, "fields": ["id"]})
            self.assertEqual(response.status_code, 403, model_name)
            self.assertEqual(response.json()["error"]["code"], "model_not_allowed", model_name)

    @mute_logger("odoo.sql_db")
    def test_write_inside_request_is_blocked_and_not_persisted(self):
        with patch.object(ai_assistant_guard, "run_operation", _write_probe):
            response = self._post("search_read", {"model": "res.partner", "fields": ["name"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "write_attempt")
        self.assertFalse(self.env["res.partner"].sudo().search([("name", "=", WRITE_PROBE_NAME)]))

    def test_record_rules_are_evaluated_for_the_owner(self):
        domain = [["id", "in", [self.partner_eva.id, self.partner_karol.id]]]
        karol_names = self._partner_names(
            self._post(
                "search_read",
                {"model": "res.partner", "domain": domain, "fields": ["name"]},
                key=self.karol_key,
            )
        )
        self.assertEqual(karol_names, {self.partner_karol.name})
        eva_names = self._partner_names(
            self._post(
                "search_read", {"model": "res.partner", "domain": domain, "fields": ["name"]}
            )
        )
        self.assertEqual(eva_names, {self.partner_eva.name, self.partner_karol.name})

    def test_binary_and_secret_fields_are_never_returned(self):
        fields_response = self._post("fields_get", {"model": "res.partner"})
        descriptions = fields_response.json()["fields"]
        self.assertIn("name", descriptions)
        self.assertNotIn("image_1920", descriptions)
        self.assertNotIn("vat", descriptions)
        read_response = self._post(
            "read",
            {
                "model": "res.partner",
                "ids": [self.partner_eva.id],
                "fields": ["name", "image_1920", "vat"],
            },
        )
        record = read_response.json()["records"][0]
        self.assertEqual(set(record), {"id", "name"})

    def test_limit_is_clamped_and_fields_are_required(self):
        clamped = self._post(
            "search_read",
            {
                "model": "res.partner",
                "fields": ["name"],
                "limit": 1000,
            },
        )
        self.assertEqual(clamped.json()["limit"], 200)
        missing = self._post("search_read", {"model": "res.partner"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"]["code"], "missing_fields")

    def test_message_search_is_limited_to_whitelisted_models(self):
        allowed = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": self.partner_eva.id,
                "body": "AI test allowed",
            }
        )
        hidden = self.env["mail.message"].create(
            {
                "model": "ir.ui.view",
                "res_id": 1,
                "body": "AI test hidden",
            }
        )
        response = self._post(
            "search_read",
            {
                "model": "mail.message",
                "domain": [["id", "in", [allowed.id, hidden.id]]],
                "fields": ["body", "model"],
            },
        )
        returned = {record["id"] for record in response.json()["records"]}
        self.assertEqual(returned, {allowed.id})

    def test_multi_company_scope(self):
        domain = [["id", "in", [self.partner_b.id, self.partner_c.id]]]
        names = self._partner_names(
            self._post(
                "search_read", {"model": "res.partner", "domain": domain, "fields": ["name"]}
            )
        )
        self.assertEqual(names, {self.partner_b.name})
        foreign = self._post(
            "search_read",
            {
                "model": "res.partner",
                "fields": ["name"],
                "company_ids": [self.company_c.id],
            },
        )
        self.assertEqual(foreign.status_code, 400)
        self.assertEqual(foreign.json()["error"]["code"], "company_not_allowed")

    def test_kill_switch_returns_503(self):
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.enabled", "False")
        response = self._post("search_read", {"model": "res.partner", "fields": ["name"]})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "disabled")

    def test_https_is_required_by_default(self):
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.allow_http", "False")
        response = self._post("search_read", {"model": "res.partner", "fields": ["name"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "https_required")

    def test_expired_access_is_rejected(self):
        access = (
            self.env["ai.assistant.access"]
            .sudo()
            .search(
                [
                    ("user_id", "=", self.eva.id),
                    ("state", "=", "active"),
                ]
            )
        )
        access.write({"expires_at": "2020-01-01 00:00:00"})
        response = self._post("search_read", {"model": "res.partner", "fields": ["name"]})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "expired")

    def test_revoked_access_is_rejected(self):
        access = (
            self.env["ai.assistant.access"]
            .sudo()
            .search(
                [
                    ("user_id", "=", self.eva.id),
                    ("state", "=", "active"),
                ]
            )
        )
        access.write({"state": "revoked"})
        response = self._post("search_read", {"model": "res.partner", "fields": ["name"]})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "revoked")

    def test_rate_limit_returns_429(self):
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.rate_limit_per_min", "1")
        first = self._post("search_count", {"model": "res.partner", "domain": []})
        self.assertEqual(first.status_code, 200)
        second = self._post("search_count", {"model": "res.partner", "domain": []})
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "rate_limited")

    def test_read_group_and_name_search(self):
        grouped = self._post(
            "read_group",
            {
                "model": "res.partner",
                "domain": [["id", "in", [self.partner_eva.id, self.partner_karol.id]]],
                "groupby": ["user_id"],
                "fields": [],
            },
        )
        self.assertEqual(grouped.status_code, 200)
        self.assertEqual(len(grouped.json()["groups"]), 2)
        searched = self._post(
            "name_search",
            {
                "model": "res.partner",
                "name": self.partner_eva.name,
            },
        )
        self.assertEqual(searched.status_code, 200)
        self.assertIn(self.partner_eva.id, [item[0] for item in searched.json()["results"]])

    def test_audit_is_written_for_success_and_failure(self):
        audit_model = self.env["ai.assistant.audit"].sudo()
        before = audit_model.search_count([])
        self._post("search_count", {"model": "res.partner", "domain": []})
        self._post("execute_kw", {"model": "res.partner"})
        self.assertEqual(audit_model.search_count([]), before + 2)
        latest = audit_model.search([], order="id desc", limit=2)
        self.assertEqual(
            [(entry.op, entry.status, entry.error_code) for entry in latest],
            [("execute_kw", "error", "unknown_op"), ("search_count", "ok", False)],
        )
        self.assertEqual(latest.mapped("user_id"), self.eva)

    def test_dotted_domain_fields_are_rejected(self):
        for op, payload in (
            (
                "search_read",
                {
                    "model": "res.partner",
                    "domain": [["user_ids.oauth_access_token", "like", "a%"]],
                    "fields": ["name"],
                },
            ),
            (
                "search_count",
                {
                    "model": "res.company",
                    "domain": [["partner_id.vat", "like", "SK%"]],
                },
            ),
            (
                "name_search",
                {
                    "model": "res.partner",
                    "name": "a",
                    "domain": [["user_ids.password", "!=", False]],
                },
            ),
        ):
            response = self._post(op, payload)
            self.assertEqual(response.status_code, 400, op)
            self.assertEqual(response.json()["error"]["code"], "bad_request", op)

    def test_secret_and_unknown_domain_fields_are_rejected(self):
        for model, field in (
            ("res.partner", "vat"),
            ("res.company", "vat"),
            ("res.partner", "no_such_field"),
        ):
            response = self._post(
                "search_count",
                {
                    "model": model,
                    "domain": [[field, "like", "a%"]],
                },
            )
            self.assertEqual(response.status_code, 403, field)
            self.assertEqual(response.json()["error"]["code"], "field_not_allowed", field)

    def test_search_count_oracle_on_secret_field_is_blocked(self):
        for value in ("a%", "b%", "c%"):
            response = self._post(
                "search_count",
                {
                    "model": "res.company",
                    "domain": [["vat", "like", value]],
                },
            )
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("count", response.json())

    def test_malformed_domain_is_bad_request(self):
        for domain in ([["name", "="]], [[1, "=", 1]], [["name", "like", "a"], "?"], "name"):
            response = self._post("search_count", {"model": "res.partner", "domain": domain})
            self.assertEqual(response.status_code, 400, domain)
            self.assertEqual(response.json()["error"]["code"], "bad_request", domain)

    def test_order_by_secret_field_is_rejected(self):
        rejected = self._post(
            "search_read",
            {
                "model": "res.company",
                "fields": ["name"],
                "order": "vat desc",
            },
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["error"]["code"], "field_not_allowed")
        allowed = self._post(
            "search_read",
            {
                "model": "res.partner",
                "fields": ["name"],
                "order": "name asc",
            },
        )
        self.assertEqual(allowed.status_code, 200)

    def test_logic_operators_stay_allowed_in_domain(self):
        response = self._post(
            "search_count",
            {
                "model": "res.partner",
                "domain": [
                    "|",
                    ["id", "=", self.partner_eva.id],
                    ["id", "=", self.partner_karol.id],
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)

    def test_read_group_rejects_dotted_and_secret_fields(self):
        dotted = self._post(
            "read_group",
            {
                "model": "res.partner",
                "groupby": ["user_id.login"],
                "fields": [],
            },
        )
        self.assertEqual(dotted.status_code, 400)
        self.assertEqual(dotted.json()["error"]["code"], "bad_request")
        dotted_measure = self._post(
            "read_group",
            {
                "model": "res.partner",
                "groupby": ["user_id"],
                "fields": ["user_id.id"],
            },
        )
        self.assertEqual(dotted_measure.status_code, 400)
        secret_measure = self._post(
            "read_group",
            {
                "model": "res.partner",
                "groupby": ["user_id"],
                "fields": ["vat"],
            },
        )
        self.assertEqual(secret_measure.status_code, 403)
        self.assertEqual(secret_measure.json()["error"]["code"], "field_not_allowed")

    @mute_logger("odoo.addons.ai_assistant_access.controllers.ai_assistant_api")
    def test_unauthenticated_requests_write_no_audit(self):
        audit_model = self.env["ai.assistant.audit"].sudo()
        before = audit_model.search_count([])
        missing = self._post("search_count", {"model": "res.partner"}, with_key=False)
        self.assertEqual(missing.status_code, 401)
        wrong = self._post("search_count", {"model": "res.partner"}, key="nie-je-platny-kluc")
        self.assertEqual(wrong.status_code, 401)
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.enabled", "False")
        disabled = self._post("search_count", {"model": "res.partner"})
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(audit_model.search_count([]), before)

    def test_audit_values_are_truncated(self):
        audit_model = self.env["ai.assistant.audit"].sudo()
        long_op = "a" * 200
        self.assertEqual(self._post(long_op, {"model": "res.partner"}).status_code, 400)
        entry = audit_model.search([], order="id desc", limit=1)
        self.assertEqual(entry.op, "a" * 64)
        self.assertEqual(self._post("search_count", {"model": "x" * 300}).status_code, 403)
        entry = audit_model.search([], order="id desc", limit=1)
        self.assertEqual(entry.model_name, "x" * 128)

    @mute_logger("odoo.sql_db")
    def test_audit_is_written_for_write_attempt(self):
        audit_model = self.env["ai.assistant.audit"].sudo()
        before = audit_model.search_count([])
        with patch.object(ai_assistant_guard, "run_operation", _write_probe):
            self._post("search_read", {"model": "res.partner", "fields": ["name"]})
        self.assertEqual(audit_model.search_count([]), before + 1)
        self.assertEqual(
            audit_model.search([], order="id desc", limit=1).error_code, "write_attempt"
        )

    def _owner_messages(self):
        return (
            self.env["mail.message"]
            .sudo()
            .search(
                [
                    ("model", "=", "res.partner"),
                    ("res_id", "=", self.eva.partner_id.id),
                ]
            )
        )

    def test_hello_notifies_the_owner_once(self):
        self.eva.notification_type = "inbox"
        audit_model = self.env["ai.assistant.audit"].sudo()
        before = self._owner_messages()
        response = self._post("hello")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        message = self._owner_messages() - before
        self.assertEqual(len(message), 1)
        self.assertEqual(message.author_id, self.env.ref("base.partner_root"))
        self.assertEqual(message.message_type, "user_notification")
        notification = (
            self.env["mail.notification"]
            .sudo()
            .search(
                [
                    ("mail_message_id", "=", message.id),
                    ("res_partner_id", "=", self.eva.partner_id.id),
                ]
            )
        )
        self.assertEqual(len(notification), 1)
        self.assertEqual(notification.notification_type, "inbox")
        entry = audit_model.search([], order="id desc", limit=1)
        self.assertEqual((entry.op, entry.status, entry.error_code), ("hello", "ok", False))
        self.assertEqual(entry.user_id, self.eva)
        self.assertTrue(entry.ip)
        self.assertIn(entry.ip, message.body)
        self.assertFalse(
            self._owner_messages().filtered(lambda record: record.message_type == "comment")
        )

    def test_hello_ignores_request_payload(self):
        self.eva.notification_type = "inbox"
        before = self._owner_messages()
        response = self._post("hello", {"body": "<script>x</script>", "subject": "y"})
        self.assertEqual(response.status_code, 200)
        message = self._owner_messages() - before
        self.assertEqual(len(message), 1)
        self.assertNotIn("script", message.body)
        self.assertNotEqual(message.subject, "y")

    def test_hello_rejects_revoked_access(self):
        access = (
            self.env["ai.assistant.access"]
            .sudo()
            .search(
                [
                    ("user_id", "=", self.eva.id),
                    ("state", "=", "active"),
                ]
            )
        )
        access.write({"state": "revoked"})
        before = self._owner_messages()
        response = self._post("hello")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "revoked")
        self.assertFalse(self._owner_messages() - before)

    def test_hello_rejects_expired_access(self):
        access = (
            self.env["ai.assistant.access"]
            .sudo()
            .search(
                [
                    ("user_id", "=", self.eva.id),
                    ("state", "=", "active"),
                ]
            )
        )
        access.write({"expires_at": "2020-01-01 00:00:00"})
        response = self._post("hello")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "expired")

    def test_hello_respects_the_kill_switch(self):
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.enabled", "False")
        before = self._owner_messages()
        response = self._post("hello")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "disabled")
        self.assertFalse(self._owner_messages() - before)

    def test_hello_shares_the_rate_limit_with_reads(self):
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.rate_limit_per_min", "1")
        first = self._post("search_count", {"model": "res.partner", "domain": []})
        self.assertEqual(first.status_code, 200)
        second = self._post("hello")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "rate_limited")

    def _apikeys_of(self, user):
        return self.env["res.users.apikeys"].sudo().search([("user_id", "=", user.id)])

    def _pair(self, code):
        return self.url_open(
            "/ai_assistant/v1/pair",
            data=json.dumps({"db": self.env.cr.dbname, "code": code}),
            headers={"Content-Type": "application/json"},
        )

    def _eva_partner_read(self, key):
        return self._post(
            "search_read",
            {
                "model": "res.partner",
                "domain": [["id", "=", self.partner_eva.id]],
                "fields": ["name"],
            },
            key=key,
        )

    def _grant_partner_editing(self, user):
        user.write({"groups_id": [Command.link(self.env.ref("base.group_partner_manager").id)]})

    def _rw_key_of(self, user):
        self._set_editing_allowed(True)
        return self._generate_access_as_owner(user, "read_write")

    def _partners_named(self, name):
        return self.env["res.partner"].sudo().search([("name", "=", name)])

    def test_read_write_key_degrades_to_read_when_editing_is_off(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        self._set_editing_allowed(False)
        create = {"model": "res.partner", "values": {"name": "AI partner switch"}}
        self.assertEqual(self._eva_partner_read(rw_key).status_code, 200)
        denied = self._post("create", create, key=rw_key)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "write_disabled")
        self._set_editing_allowed(True)
        allowed = self._post("create", create, key=rw_key)
        self.assertEqual(allowed.status_code, 200)
        created = self._partners_named("AI partner switch")
        self.assertEqual(allowed.json(), {"model": "res.partner", "id": created.id})
        self.assertEqual(created.create_uid, self.eva)
        self._set_editing_allowed(False)
        again = self._post("create", create, key=rw_key)
        self.assertEqual(again.status_code, 403)
        self.assertEqual(again.json()["error"]["code"], "write_disabled")
        self.assertEqual(len(self._partners_named("AI partner switch")), 1)
        self.assertEqual(len(self._apikeys_of(self.eva)), 1)

    def test_read_only_key_cannot_use_write_operations(self):
        self._set_editing_allowed(True)
        self._grant_partner_editing(self.eva)
        for op, payload in (
            ("create", {"model": "res.partner", "values": {"name": "AI partner ro"}}),
            (
                "write",
                {"model": "res.partner", "ids": [self.partner_eva.id], "values": {"name": "x"}},
            ),
        ):
            response = self._post(op, payload)
            self.assertEqual(response.status_code, 403, op)
            self.assertEqual(response.json()["error"]["code"], "write_disabled", op)
        self.assertFalse(self._partners_named("AI partner ro"))
        self.assertEqual(self.partner_eva.name, "Kontakt Evy AI")
        response = self._post("unlink", {"model": "res.partner", "ids": [self.partner_eva.id]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "unknown_op")

    def test_read_write_key_creates_lead_as_owner_and_audits_field_names(self):
        if "crm.lead" not in self.env:
            self.skipTest("crm is not installed")
        self.eva.write(
            {"groups_id": [Command.link(self.env.ref("sales_team.group_sale_salesman").id)]}
        )
        create = {"model": "crm.lead", "values": {"name": "AI lead"}}
        denied = self._post("create", create)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "write_disabled")
        rw_key = self._rw_key_of(self.eva)
        response = self._post("create", create, key=rw_key)
        self.assertEqual(response.status_code, 200)
        lead = self.env["crm.lead"].sudo().browse(response.json()["id"])
        self.assertEqual(lead.name, "AI lead")
        self.assertEqual(lead.create_uid, self.eva)
        self.assertIn(lead.company_id, self.eva.company_ids)
        written = self._post(
            "write",
            {"model": "crm.lead", "ids": [lead.id], "values": {"name": "AI lead 2"}},
            key=rw_key,
        )
        self.assertEqual(written.status_code, 200)
        self.assertEqual(written.json(), {"model": "crm.lead", "ids": [lead.id], "length": 1})
        self.assertEqual(lead.name, "AI lead 2")
        audits = (
            self.env["ai.assistant.audit"]
            .sudo()
            .search(
                [
                    ("user_id", "=", self.eva.id),
                    ("op", "in", ("create", "write")),
                    ("status", "=", "ok"),
                ]
            )
        )
        self.assertEqual(len(audits), 2)
        for audit in audits:
            self.assertEqual(audit.field_names, "name")
            self.assertEqual(audit.record_count, 1)
            self.assertFalse(audit.domain_text)

    def test_write_respects_record_rules_of_the_owner(self):
        self._grant_partner_editing(self.karol)
        rw_key = self._rw_key_of(self.karol)
        own = self._post(
            "write",
            {"model": "res.partner", "ids": [self.partner_karol.id], "values": {"city": "Bern"}},
            key=rw_key,
        )
        self.assertEqual(own.status_code, 200)
        self.assertEqual(self.partner_karol.city, "Bern")
        foreign = self._post(
            "write",
            {"model": "res.partner", "ids": [self.partner_eva.id], "values": {"name": "Hacked"}},
            key=rw_key,
        )
        self.assertEqual(foreign.status_code, 403)
        self.assertEqual(foreign.json()["error"]["code"], "access_denied")
        self.assertEqual(self.partner_eva.sudo().read(["name"])[0]["name"], "Kontakt Evy AI")

    def test_write_rejects_relational_commands_and_protected_fields(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        child = self.env["res.partner"].create(
            {"name": "AI child", "parent_id": self.partner_eva.id}
        )
        category = self.env["res.partner.category"].create({"name": "AI category"})
        self.partner_eva.category_id = category
        for values in (
            {"child_ids": [[2, child.id]]},
            {"child_ids": [[5]]},
            {"category_id": [[6, 0, []]]},
            {"active": False},
            {"vat": "CHE-123"},
            {"image_1920": False},
        ):
            response = self._post(
                "write",
                {"model": "res.partner", "ids": [self.partner_eva.id], "values": values},
                key=rw_key,
            )
            self.assertEqual(response.status_code, 400, values)
            self.assertEqual(response.json()["error"]["code"], "field_not_writable", values)
        self.assertTrue(child.exists())
        self.assertEqual(child.parent_id, self.partner_eva)
        self.assertTrue(category.exists())
        self.assertEqual(self.partner_eva.category_id, category)
        self.assertTrue(self.partner_eva.active)

    def test_write_outside_writable_models_and_unlink_are_rejected(self):
        rw_key = self._rw_key_of(self.eva)
        response = self._post(
            "write", {"model": "account.move", "ids": [1], "values": {"ref": "x"}}, key=rw_key
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "model_not_writable")
        response = self._post(
            "unlink", {"model": "res.partner", "ids": [self.partner_eva.id]}, key=rw_key
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "unknown_op")
        self.assertTrue(self.partner_eva.exists())

    def test_pending_access_is_rejected_as_not_paired(self):
        self.env["ai.assistant.access"].sudo().create(
            {"user_id": self.eva.id, "state": "pending", "access_mode": "read"}
        )
        response = self._eva_partner_read(self.eva_key)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_paired")

    def test_hello_text_depends_on_access_mode(self):
        self.eva.notification_type = "inbox"
        before = self._owner_messages()
        self.assertEqual(self._post("hello").status_code, 200)
        read_message = self._owner_messages() - before
        self.assertIn("read-only", read_message.body)
        self._set_editing_allowed(True)
        rw_key = self._generate_access_as_owner(self.eva, "read_write")
        before = self._owner_messages()
        self.assertEqual(self._post("hello", key=rw_key).status_code, 200)
        rw_message = self._owner_messages() - before
        self.assertIn("read and edit", rw_message.body)
        self.assertNotIn("read-only", rw_message.body)

    def test_pair_exchanges_code_for_working_key_exactly_once(self):
        code = self._start_pairing_as_owner(self.eva)
        self.assertFalse(self._apikeys_of(self.eva))
        response = self._pair(code)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            set(data),
            {"api_key", "login", "access_mode", "expires_at", "odoo_url", "db", "instructions"},
        )
        self.assertEqual(data["login"], self.eva.login)
        self.assertEqual(data["access_mode"], "read")
        self.assertEqual(data["db"], self.env.cr.dbname)
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        self.assertEqual(data["odoo_url"], base_url)
        self.assertIn("read-only", data["instructions"])
        self.assertNotIn("odoo_create", data["instructions"])
        self.assertEqual(self._eva_partner_read(data["api_key"]).status_code, 200)
        second = self._pair(code)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(second.json()["error"]["code"], "invalid_code")
        self.assertEqual(len(self._apikeys_of(self.eva)), 1)
        access = (
            self.env["ai.assistant.access"]
            .sudo()
            .search([("user_id", "=", self.eva.id)], order="id desc", limit=1)
        )
        self.assertEqual((access.state, access.pairing_code_hash), ("active", False))
        entry = (
            self.env["ai.assistant.audit"]
            .sudo()
            .search([("op", "=", "pair")], order="id desc", limit=1)
        )
        self.assertEqual((entry.user_id, entry.status), (self.eva, "ok"))
        self.assertNotIn(code, entry.domain_text or "")

    def test_pair_in_edit_mode_returns_edit_instructions(self):
        self._set_editing_allowed(True)
        code = self._start_pairing_as_owner(self.eva, "read_write")
        response = self._pair(code)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["access_mode"], "read_write")
        self.assertIn("odoo_create", data["instructions"])
        self.assertNotIn(data["api_key"], data["instructions"])

    def test_pair_with_expired_code_issues_no_key(self):
        with freeze_time(datetime.now() - timedelta(minutes=11)):
            code = self._start_pairing_as_owner(self.eva)
        response = self._pair(code)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_code")
        access = (
            self.env["ai.assistant.access"]
            .sudo()
            .search([("user_id", "=", self.eva.id)], order="id desc", limit=1)
        )
        self.assertEqual(access.state, "expired")
        self.assertFalse(self._apikeys_of(self.eva))

    def test_pair_rejects_unknown_or_missing_code(self):
        for code in ("not-a-real-code", "", None):
            response = self._pair(code)
            self.assertEqual(response.status_code, 401, code)
            self.assertEqual(response.json()["error"]["code"], "invalid_code", code)

    def test_pair_is_rate_limited_per_ip(self):
        for _attempt in range(5):
            self.assertEqual(self._pair("wrong-code-123").status_code, 401)
        response = self._pair("wrong-code-123")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")

    def test_pair_respects_the_kill_switch(self):
        code = self._start_pairing_as_owner(self.eva)
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.enabled", "False")
        response = self._pair(code)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self._apikeys_of(self.eva))

    @contextmanager
    def _captured_logs(self):
        with self.assertLogs("odoo", level="DEBUG") as captured:
            logging.getLogger("odoo.addons.ai_assistant_access").debug("log capture started")
            noisy = [logging.getLogger(name) for name in ("odoo.sql_db", "odoo.models")]
            levels = [logger.level for logger in noisy]
            for logger in noisy:
                logger.setLevel(logging.INFO)
            try:
                yield captured
            finally:
                for logger, level in zip(noisy, levels, strict=True):
                    logger.setLevel(level)
        self.captured_log_text = "\n".join(
            logging.Formatter("%(name)s %(message)s").format(record) for record in captured.records
        )

    def _assert_not_logged(self, *secrets):
        for secret in secrets:
            self.assertTrue(secret)
            self.assertNotIn(secret, self.captured_log_text)

    def _all_secret_carriers(self, user):
        messages = (
            self.env["mail.message"].sudo().search([("partner_ids", "in", user.partner_id.ids)])
        )
        audits = self.env["ai.assistant.audit"].sudo().search([("user_id", "=", user.id)])
        return "\n".join(
            [message.body or "" for message in messages]
            + [message.subject or "" for message in messages]
            + [audit.domain_text or "" for audit in audits]
            + [audit.field_names or "" for audit in audits]
        )

    def test_keys_and_codes_never_reach_the_logs(self):
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.rate_limit_per_min", "2")
        rpc_key = (
            self.env["res.users.apikeys"]
            .with_user(self.karol)
            .sudo()
            ._generate(None, "AI test rpc key", datetime.now() + timedelta(days=30))
        )
        read = {"model": "res.partner", "fields": ["name"]}
        with self._captured_logs():
            self.assertEqual(self._post("search_read", read).status_code, 200)
            self.assertEqual(self._post("search_read", read, key=rpc_key).status_code, 401)
            self.assertEqual(self._post("search_read", read).status_code, 200)
            self.assertEqual(self._post("search_read", read).status_code, 429)
        self._assert_not_logged(self.eva_key, rpc_key)
        ai_assistant_api._rate_hits.clear()
        ai_assistant_api._pair_rate_hits.clear()
        ai_assistant_api._rate_purged_at.clear()
        with (
            patch.object(ai_assistant_guard, "run_operation", side_effect=RuntimeError("boom")),
            self._captured_logs(),
        ):
            response = self._post("search_read", read, key=self.karol_key)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        self.assertIn("RuntimeError: boom", self.captured_log_text)
        self._assert_not_logged(self.karol_key)

    def test_pairing_code_and_issued_key_never_reach_logs_messages_or_audit(self):
        attachments_before = self.env["ir.attachment"].sudo().search_count([])
        code = self._start_pairing_as_owner(self.eva)
        with self._captured_logs():
            wrong = self._pair(f"{code}x")
            paired = self._pair(code)
            replayed = self._pair(code)
        self.assertEqual((wrong.status_code, paired.status_code), (401, 200))
        self.assertEqual(replayed.status_code, 401)
        key = paired.json()["api_key"]
        self._assert_not_logged(code, f"{code}x", key)
        with self._captured_logs():
            self.assertEqual(self._post("hello", key=key).status_code, 200)
            self.assertEqual(self._eva_partner_read(key).status_code, 200)
        self._assert_not_logged(code, key)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count([]), attachments_before)
        carriers = self._all_secret_carriers(self.eva)
        self.assertIn("read-only access", carriers)
        self.assertNotIn(code, carriers)
        self.assertNotIn(key, carriers)

    def test_written_values_never_reach_the_audit(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        secret_value = "AI audit value must stay private"
        response = self._post(
            "create",
            {"model": "res.partner", "values": {"name": secret_value, "comment": secret_value}},
            key=rw_key,
        )
        self.assertEqual(response.status_code, 200)
        entry = (
            self.env["ai.assistant.audit"]
            .sudo()
            .search([("op", "=", "create")], order="id desc", limit=1)
        )
        self.assertEqual(entry.field_names, "comment,name")
        self.assertNotIn(secret_value, self._all_secret_carriers(self.eva))
        self.assertNotIn(rw_key, self._all_secret_carriers(self.eva))

    def test_rpc_scope_key_is_rejected_for_writes(self):
        self._set_editing_allowed(True)
        self._grant_partner_editing(self.karol)
        rpc_key = (
            self.env["res.users.apikeys"]
            .with_user(self.karol)
            .sudo()
            ._generate(None, "AI test rpc key", datetime.now() + timedelta(days=30))
        )
        for op, payload in (
            ("create", {"model": "res.partner", "values": {"name": "AI partner rpc"}}),
            (
                "write",
                {"model": "res.partner", "ids": [self.partner_karol.id], "values": {"name": "x"}},
            ),
        ):
            response = self._post(op, payload, key=rpc_key)
            self.assertEqual(response.status_code, 401, op)
            self.assertEqual(response.json()["error"]["code"], "invalid_key", op)
        self.assertFalse(self._partners_named("AI partner rpc"))

    def test_kill_switch_blocks_writes(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.enabled", "False")
        response = self._post(
            "create", {"model": "res.partner", "values": {"name": "AI partner off"}}, key=rw_key
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "disabled")
        self.assertFalse(self._partners_named("AI partner off"))

    def test_pair_requires_https_by_default(self):
        code = self._start_pairing_as_owner(self.eva)
        self.env["ir.config_parameter"].sudo().set_param("ai_assistant.allow_http", "False")
        response = self._pair(code)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "https_required")
        self.assertFalse(self._apikeys_of(self.eva))

    def test_domain_subqueries_cannot_reach_blacklisted_models_or_fields(self):
        for domain, code in (
            ([["user_ids", "any", [["login", "=", "admin"]]]], "model_not_allowed"),
            ([["user_ids", "not any", [["api_key_ids", "!=", False]]]], "model_not_allowed"),
            ([["child_ids", "any", [["vat", "=", "CHE-1"]]]], "field_not_allowed"),
            ([["child_ids", "any!", [["name", "=", "x"]]]], "bad_request"),
            ([["user_ids", "in", [["login", "=", "admin"]]]], "bad_request"),
            ([["user_ids", "ilike", "admin"]], "model_not_allowed"),
        ):
            response = self._post("search_count", {"model": "res.partner", "domain": domain})
            self.assertIn(response.status_code, (400, 403), domain)
            self.assertEqual(response.json()["error"]["code"], code, domain)
        allowed = self._post(
            "search_count",
            {"model": "res.partner", "domain": [["child_ids", "any", [["name", "=", "x"]]]]},
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_empty_writable_models_disable_writing_and_missing_means_defaults(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        create = {"model": "res.partner", "values": {"name": "AI partner writable"}}
        settings = self.env["res.config.settings"].create({"ai_assistant_writable_models": False})
        settings.set_values()
        settings.set_values()
        params = self.env["ir.config_parameter"].sudo()
        self.assertEqual(params.get_param("ai_assistant.writable_models"), " ")
        param = self.env.ref("ai_assistant_access.param_ai_assistant_writable_models")
        self.assertEqual((param.key, param.value), ("ai_assistant.writable_models", " "))
        denied = self._post("create", create, key=rw_key)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "write_disabled")
        self.assertFalse(self._partners_named("AI partner writable"))
        params.search([("key", "=", "ai_assistant.writable_models")]).unlink()
        allowed = self._post("create", create, key=rw_key)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(len(self._partners_named("AI partner writable")), 1)

    def test_pair_of_edit_code_after_editing_was_disabled_issues_read_key(self):
        self._set_editing_allowed(True)
        code = self._start_pairing_as_owner(self.eva, "read_write")
        self._set_editing_allowed(False)
        response = self._pair(code)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["access_mode"], "read")
        access = (
            self.env["ai.assistant.access"]
            .sudo()
            .search([("user_id", "=", self.eva.id)], order="id desc", limit=1)
        )
        self.assertEqual(access.access_mode, "read")
        self.assertEqual(access.apikey_id.scope, ai_assistant_guard.AI_KEY_SCOPE)
        self._set_editing_allowed(True)
        denied = self._post(
            "create",
            {"model": "res.partner", "values": {"name": "AI x"}},
            key=response.json()["api_key"],
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "write_disabled")

    def test_partner_identity_and_user_contacts_cannot_be_changed(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        child_values = {"name": "AI child B", "parent_id": self.partner_b.id, "email": "b@ex.com"}
        created = self._post(
            "create", {"model": "res.partner", "values": child_values}, key=rw_key
        )
        self.assertEqual(created.status_code, 200, created.text)
        child = self._partners_named("AI child B")
        self.assertEqual((child.parent_id, child.email), (self.partner_b, "b@ex.com"))
        for ids, values in (
            ([self.partner_eva.id], {"parent_id": self.partner_b.id}),
            ([self.partner_eva.id], {"email": "x@example.com"}),
            ([self.karol.partner_id.id], {"email": "x@example.com"}),
        ):
            response = self._post(
                "write", {"model": "res.partner", "ids": ids, "values": values}, key=rw_key
            )
            self.assertEqual(response.status_code, 400, values)
            self.assertEqual(response.json()["error"]["code"], "field_not_writable", values)
        company_partner = self.company_b.partner_id
        company_contact = self.env["res.partner"].create(
            {"name": "AI company contact", "parent_id": company_partner.id, "type": "contact"}
        )
        portal_firm = self.env["res.partner"].create({"name": "AI portal firm", "is_company": True})
        portal = self.env["res.users"].create(
            {
                "name": "AI portal contact",
                "login": "ai.portal.contact",
                "groups_id": [Command.set([self.env.ref("base.group_portal").id])],
            }
        )
        portal.partner_id.parent_id = portal_firm
        industry = self.env["res.partner.industry"].create({"name": "AI industry"})
        for op, payload in (
            ("write", {"ids": [self.karol.partner_id.id], "values": {"city": "x"}}),
            ("write", {"ids": [company_partner.id], "values": {"city": "x"}}),
            ("write", {"ids": [company_contact.id], "values": {"city": "x"}}),
            ("write", {"ids": [portal_firm.id], "values": {"company_registry": "CHE-1"}}),
            ("write", {"ids": [portal_firm.id], "values": {"industry_id": industry.id}}),
            ("create", {"values": {"name": "AI child U", "parent_id": self.karol.partner_id.id}}),
            ("create", {"values": {"name": "AI child U", "parent_id": company_partner.id}}),
        ):
            response = self._post(op, {"model": "res.partner", **payload}, key=rw_key)
            self.assertEqual(response.status_code, 403, payload)
            self.assertEqual(response.json()["error"]["code"], "record_protected", payload)
        self.assertFalse(self._partners_named("AI child U"))
        self.assertFalse(self.partner_eva.parent_id)
        self.assertNotEqual(self.karol.partner_id.city, "x")
        self.assertNotEqual(company_partner.city, "x")
        self.assertFalse(portal.partner_id.company_registry or portal.partner_id.industry_id)

    def test_indirect_archive_through_a_write_is_rolled_back(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        partner_class = type(self.env["res.partner"])
        original_write = partner_class.write

        def archiving_write(records, vals):
            result = original_write(records, vals)
            if "comment" in vals:
                original_write(records, {"active": False})
            return result

        with patch.object(partner_class, "write", archiving_write):
            response = self._post(
                "write",
                {
                    "model": "res.partner",
                    "ids": [self.partner_eva.id],
                    "values": {"comment": "AI"},
                },
                key=rw_key,
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "protected_field_changed")
        self.partner_eva.invalidate_recordset()
        self.assertTrue(self.partner_eva.active)
        self.assertFalse(self.partner_eva.comment)

    def _rw_key_with_crm(self):
        if "crm.lead" not in self.env:
            self.skipTest("crm is not installed")
        self.eva.write(
            {
                "groups_id": [
                    Command.link(self.env.ref("sales_team.group_sale_salesman_all_leads").id),
                ]
            }
        )
        return self._rw_key_of(self.eva)

    def test_lead_writes_cannot_reactivate_move_or_touch_activities(self):
        rw_key = self._rw_key_with_crm()
        won = self.env["crm.stage"].create({"name": "AI Won", "is_won": True})
        team_c = self.env["crm.team"].create(
            {"name": "AI team C", "company_id": self.company_c.id}
        )
        lost = self.env["crm.lead"].create(
            {"name": "AI lost lead", "user_id": self.eva.id, "type": "opportunity"}
        )
        lost.action_set_lost()
        lead = self.env["crm.lead"].create({"name": "AI open lead", "user_id": self.eva.id})
        activity = lead.activity_schedule(
            "mail.mail_activity_data_todo",
            summary="Admin plan",
            user_id=self.env.ref("base.user_admin").id,
        )
        company = lead.company_id
        moved_codes = ("protected_field_changed", "bad_request", "access_denied")
        for ids, values, codes in (
            ([lost.id], {"stage_id": won.id}, ("record_archived",)),
            ([lost.id], {"name": "AI revived"}, ("record_archived",)),
            ([lead.id], {"team_id": team_c.id}, moved_codes),
            ([lead.id], {"activity_summary": "AI changed"}, ("field_not_writable",)),
        ):
            response = self._post(
                "write", {"model": "crm.lead", "ids": ids, "values": values}, key=rw_key
            )
            self.assertIn(response.status_code, (400, 403), values)
            self.assertIn(response.json()["error"]["code"], codes, values)
        created = self._post(
            "create",
            {"model": "crm.lead", "values": {"name": "AI lead C", "team_id": team_c.id}},
            key=rw_key,
        )
        self.assertIn(created.json()["error"]["code"], moved_codes)
        stage = self.env["crm.stage"].create({"name": "AI next stage"})
        staged = self._post(
            "write",
            {"model": "crm.lead", "ids": [lead.id], "values": {"stage_id": stage.id}},
            key=rw_key,
        )
        self.assertEqual(staged.status_code, 200, staged.text)
        lead.invalidate_recordset()
        self.assertEqual(lead.stage_id, stage)
        (lost | lead).invalidate_recordset()
        activity.invalidate_recordset()
        self.assertFalse(lost.active)
        self.assertEqual(lost.name, "AI lost lead")
        self.assertEqual(lead.company_id, company)
        self.assertEqual(activity.summary, "Admin plan")
        self.assertFalse(self.env["crm.lead"].sudo().search([("name", "=", "AI lead C")]))

    def test_activity_cannot_be_moved_to_a_record_outside_the_owner_rights(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        activity = self.partner_eva.activity_schedule(
            "mail.mail_activity_data_todo", summary="AI move", user_id=self.eva.id
        )
        for res_id, status, code in (
            (self.partner_c.id, 403, "access_denied"),
            (2147483000, 400, "bad_request"),
        ):
            response = self._post(
                "write",
                {"model": "mail.activity", "ids": [activity.id], "values": {"res_id": res_id}},
                key=rw_key,
            )
            self.assertEqual(response.status_code, status, response.text)
            self.assertEqual(response.json()["error"]["code"], code)
        activity.invalidate_recordset()
        self.assertEqual(activity.res_id, self.partner_eva.id)
        moved = self._post(
            "write",
            {
                "model": "mail.activity",
                "ids": [activity.id],
                "values": {"res_id": self.partner_b.id},
            },
            key=rw_key,
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        activity.invalidate_recordset()
        self.assertEqual(activity.res_id, self.partner_b.id)

    def test_write_database_errors_are_bad_requests_without_traceback(self):
        self._grant_partner_editing(self.eva)
        rw_key = self._rw_key_of(self.eva)
        with mute_logger("odoo.sql_db"):
            response = self._post(
                "write",
                {
                    "model": "res.partner",
                    "ids": [self.partner_eva.id],
                    "values": {"user_id": 2147483000},
                },
                key=rw_key,
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["error"],
            {
                "code": "bad_request",
                "message": "The values are invalid or refer to a missing record.",
            },
        )
        self.partner_eva.invalidate_recordset()
        self.assertEqual(self.partner_eva.user_id, self.eva)

    def test_full_pair_buckets_never_block_authenticated_requests(self):
        now = time.monotonic()
        with patch.object(ai_assistant_api, "MAX_PAIR_RATE_BUCKETS", 2):
            for ip in ("10.0.0.1", "10.0.0.2"):
                ai_assistant_api._pair_rate_hits[ip] = deque([now])
            paired = self._pair("wrong-code-123")
            self.assertEqual(paired.status_code, 429)
            self.assertEqual(paired.json()["error"]["code"], "rate_limited")
            response = self._eva_partner_read(self.eva_key)
            self.assertEqual(response.status_code, 200, response.text)

    def test_admin_regex_with_global_flags_does_not_break_requests(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "ai_assistant.field_blacklist_regex", "(?i)salary"
        )
        response = self._eva_partner_read(self.eva_key)
        self.assertEqual(response.status_code, 200, response.text)
