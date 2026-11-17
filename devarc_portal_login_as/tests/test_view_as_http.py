import inspect
import json
import re
import time
from datetime import timedelta
from pathlib import PurePath
from unittest.mock import patch

from odoo import fields
from odoo.http.session import session_store
from odoo.tests import HttpCase, tagged
from odoo.tests.common import JsonRpcException

from odoo.addons.bus.tests.common import WebsocketCase
from odoo.addons.bus.websocket import CloseCode

from ..models.res_users import VIEW_AS_SESSION_KEY
from .common import PortalLoginAsCommon

BANNER_TEXT = "You are viewing the portal as"
LOG_MODEL = "devarc.portal.login.as.log"
WIZARD_MODEL = "devarc.portal.login.as.wizard"


class PortalLoginAsHttpCommon(PortalLoginAsCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        settings_model = cls.env["res.users.settings"]
        for user in cls.salesperson | cls.portal_acme:
            settings_model._find_or_create_for_user(user)

    def _login(self, user):
        self.authenticate(user.login, user.login)

    def _sid(self):
        return self.opener.cookies["session_id"]

    def _stored_session(self, sid):
        return session_store().get(sid)

    def _prime_identity_check(self):
        session = self._stored_session(self._sid())
        session["identity-check-last"] = time.time()
        session_store().save(session)

    def _create_wizard(self, target, reason="Customer cannot find the invoice"):
        return self.make_jsonrpc_request("/web/dataset/call_kw", {
            "model": WIZARD_MODEL,
            "method": "create",
            "args": [{"target_user_id": target.id, "reason": reason}],
            "kwargs": {},
        })

    def _call_start(self, wizard_id):
        return self.make_jsonrpc_request("/web/dataset/call_button", {
            "model": WIZARD_MODEL,
            "method": "action_start",
            "args": [[wizard_id]],
            "kwargs": {},
        })

    def _start(self, target):
        wizard_id = self._create_wizard(target)
        self._prime_identity_check()
        return self._call_start(wizard_id)

    def _session_uid(self):
        return self.make_jsonrpc_request("/web/session/get_session_info")["uid"]

    def _csrf_from(self, html):
        match = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(r'csrf_token: "([^"]+)"', html)
        self.assertTrue(match, "No CSRF token found in the page")
        return match.group(1)

    def _logs(self, target):
        self.env.invalidate_all()
        return self.env[LOG_MODEL].search([("target_user_id", "=", target.id)])

    def _post_return(self, csrf_token):
        return self.url_open(
            "/portal_login_as/return", data={"csrf_token": csrf_token}, allow_redirects=False,
        )

    def _assert_redirected_to_login(self, response):
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/web/login", response.headers["Location"])

    def _assert_session_expired(self, sid):
        self.assertFalse(self._stored_session(sid).uid)

    def _expire(self, log):
        log.sudo().write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.env.flush_all()


@tagged("post_install", "-at_install")
class TestViewAsHttp(PortalLoginAsHttpCommon):

    def test_start_requires_password_step_up(self):
        self._login(self.salesperson)
        result = self._call_start(self._create_wizard(self.portal_acme))
        self.assertEqual(result["res_model"], "res.users.identitycheck")
        self.assertFalse(self._logs(self.portal_acme))
        self.assertEqual(self._session_uid(), self.salesperson.id)

    def test_start_switches_session_and_returns(self):
        self._login(self.salesperson)
        origin_sid = self._sid()
        result = self._start(self.portal_acme)
        self.assertEqual(result["type"], "ir.actions.act_url")
        self.assertEqual(result["url"], "/my")

        view_as_sid = self._sid()
        self.assertNotEqual(view_as_sid, origin_sid)
        self._assert_session_expired(origin_sid)
        self.assertNotIn("identity-check-last", self._stored_session(view_as_sid))
        self.assertEqual(self._session_uid(), self.portal_acme.id)

        page = self.url_open("/my")
        self.assertEqual(page.status_code, 200)
        self.assertIn(BANNER_TEXT, page.text)
        self.assertIn(self.portal_acme.name, page.text)

        log = self._logs(self.portal_acme)
        self.assertEqual(len(log), 1)
        self.assertEqual(log.state, "active")
        self.assertEqual(log.origin_user_id, self.salesperson)
        self.assertTrue(log.ip_address)
        notification = self.env["mail.message"].search([("model", "=", LOG_MODEL), ("res_id", "=", log.id)])
        self.assertIn(self.portal_acme.partner_id, notification.partner_ids)
        self.assertNotIn("/odoo/", "".join(notification.mapped("body")))

        response = self._post_return(self._csrf_from(page.text))
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].endswith(
            f"/odoo/action-devarc_portal_login_as.action_portal_login_as_log/{log.id}"
        ))
        self.assertNotEqual(self._sid(), view_as_sid)
        self._assert_session_expired(view_as_sid)
        self.assertEqual(self._session_uid(), self.salesperson.id)
        self.assertEqual(self.url_open(response.headers["Location"]).status_code, 200)

        log = self._logs(self.portal_acme)
        self.assertEqual(log.state, "ended")
        self.assertEqual(log.end_reason, "returned")

    def test_return_without_view_as_is_forbidden(self):
        self._login(self.portal_acme)
        page = self.url_open("/my")
        response = self._post_return(self._csrf_from(page.text))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._logs(self.portal_acme))
        self.assertEqual(self._session_uid(), self.portal_acme.id)

    def test_return_requires_csrf_and_post(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        without_csrf = self.url_open("/portal_login_as/return", data={"x": "1"}, allow_redirects=False)
        self.assertEqual(without_csrf.status_code, 400)
        with_get = self.url_open("/portal_login_as/return", allow_redirects=False)
        self.assertEqual(with_get.status_code, 405)
        self.assertEqual(self._session_uid(), self.portal_acme.id)
        self.assertEqual(self._logs(self.portal_acme).state, "active")

    def test_expired_view_as_logs_out(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        view_as_sid = self._sid()
        log = self._logs(self.portal_acme)
        log.sudo().write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.env.flush_all()
        ir_http = type(self.env["ir.http"])
        with patch.object(
            ir_http, "_portal_view_as_close_log", wraps=ir_http._portal_view_as_close_log,
        ) as close_log:
            self._assert_redirected_to_login(self.url_open("/my", allow_redirects=False))
        close_log.assert_called_once_with(log.id, "expired")
        self._assert_session_expired(view_as_sid)
        self._assert_session_expired(self._sid())
        ir_http._portal_view_as_close_log(log.id, "expired")
        log = self._logs(self.portal_acme)
        self.assertEqual(log.state, "ended")
        self.assertEqual(log.end_reason, "expired")

    def test_expired_view_as_is_refused_on_fallback_routes(self):
        url = "/devarc_portal_login_as_test_fallback.txt"
        self.env["ir.attachment"].create({
            "name": "fallback.txt",
            "type": "binary",
            "url": url,
            "public": True,
            "raw": b"served through the ir.http fallback",
        })
        self._login(self.salesperson)
        self._start(self.portal_acme)
        log = self._logs(self.portal_acme)
        self.assertEqual(self.url_open(url).status_code, 200)
        self._expire(log)
        ir_http = type(self.env["ir.http"])
        # fallback routes run on a read-only test cursor, which cannot open the nested read-write one
        with patch.object(ir_http, "_portal_view_as_close_log") as close_log:
            response = self.url_open(url, allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b"served through the ir.http fallback", response.content)
        close_log.assert_called_once_with(log.id, "expired")
        self._assert_session_expired(self._sid())
        ir_http._portal_view_as_close_log(log.id, "expired")
        log.invalidate_recordset()
        self.assertEqual(log.state, "ended")
        self.assertEqual(log.end_reason, "expired")
        self.assertEqual(log.end_at, log.expires_at)

    def test_session_change_on_fallback_route_closes_log(self):
        url = "/devarc_portal_login_as_test_fallback_changed.txt"
        self.env["ir.attachment"].create({
            "name": "fallback_changed.txt",
            "type": "binary",
            "url": url,
            "public": True,
            "raw": b"served through the ir.http fallback",
        })
        self._login(self.salesperson)
        self._start(self.portal_acme)
        log = self._logs(self.portal_acme)
        self.make_jsonrpc_request("/web/session/authenticate", {
            "db": self.env.cr.dbname,
            "login": self.support.login,
            "password": self.support.login,
        })
        ir_http = type(self.env["ir.http"])
        # fallback routes run on a read-only test cursor, which cannot open the nested read-write one
        with patch.object(ir_http, "_portal_view_as_close_log") as close_log:
            response = self.url_open(url, allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        close_log.assert_called_once_with(log.id, "session_changed")
        self.assertEqual(self._session_uid(), self.support.id)
        self.assertNotIn(VIEW_AS_SESSION_KEY, self._stored_session(self._sid()))
        ir_http._portal_view_as_close_log(log.id, "session_changed")
        log.invalidate_recordset()
        self.assertEqual(log.state, "ended")
        self.assertEqual(log.end_reason, "session_changed")
        self.assertTrue(log.end_at)

    def test_login_as_other_user_clears_view_as_without_logout(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        self.make_jsonrpc_request("/web/session/authenticate", {
            "db": self.env.cr.dbname,
            "login": self.support.login,
            "password": self.support.login,
        })
        page = self.url_open("/my", allow_redirects=False)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(BANNER_TEXT, page.text)
        self.assertEqual(self._session_uid(), self.support.id)
        self.assertNotIn(VIEW_AS_SESSION_KEY, self._stored_session(self._sid()))
        log = self._logs(self.portal_acme)
        self.assertEqual(log.state, "ended")
        self.assertEqual(log.end_reason, "session_changed")

    def test_kill_switch_logs_out(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        view_as_sid = self._sid()
        self._logs(self.portal_acme).with_user(self.sysadmin).action_stop()
        self.env.flush_all()
        self._assert_redirected_to_login(self.url_open("/my", allow_redirects=False))
        self._assert_session_expired(view_as_sid)
        self._assert_session_expired(self._sid())
        self.assertEqual(self.url_open("/web/login", allow_redirects=False).status_code, 200)
        self.assertEqual(self._logs(self.portal_acme).end_reason, "stopped_by_admin")

    def test_run_check_blocked(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        check_id = self.env["res.users.identitycheck"].with_user(self.portal_acme).sudo().create({"request": "[]"}).id
        self.env.flush_all()
        with self.assertRaises(JsonRpcException) as error:
            self.make_jsonrpc_request("/web/dataset/call_button", {
                "model": "res.users.identitycheck",
                "method": "run_check",
                "args": [[check_id]],
                "kwargs": {"context": {"password": self.portal_acme.login}},
            })
        self.assertEqual(error.exception.args[0], "odoo.exceptions.AccessDenied")
        self.assertEqual(self._session_uid(), self.portal_acme.id)

    def test_logout_closes_log(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        self.url_open(
            "/web/session/logout", data={"csrf_token": self._csrf_from(self.url_open("/my").text)}, allow_redirects=False,
        )
        log = self._logs(self.portal_acme)
        self.assertEqual(log.state, "ended")
        self.assertEqual(log.end_reason, "logout")

    def test_session_destroy_closes_log(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        view_as_sid = self._sid()
        self.make_jsonrpc_request("/web/session/destroy")
        log = self._logs(self.portal_acme)
        self.assertEqual(log.state, "ended")
        self.assertEqual(log.end_reason, "logout")
        self._assert_session_expired(view_as_sid)

    def test_admin_stop_deletes_stored_session(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        view_as_sid = self._sid()
        log = self._logs(self.portal_acme)
        self.assertEqual(log.sudo().session_identifier, view_as_sid[:42])
        self.assertEqual(self._stored_session(view_as_sid).uid, self.portal_acme.id)
        log.with_user(self.sysadmin).action_stop()
        self.assertFalse(self._stored_session(view_as_sid))

    def test_cron_closes_abandoned_view_as(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        view_as_sid = self._sid()
        abandoned = self._logs(self.portal_acme)
        self._expire(abandoned)
        running = self._create_log(self.support, self.portal_beta)
        self.env[LOG_MODEL]._cron_close_expired()
        self.env.invalidate_all()
        self.assertEqual(abandoned.state, "ended")
        self.assertEqual(abandoned.end_reason, "expired")
        self.assertEqual(abandoned.end_at, abandoned.expires_at)
        self.assertFalse(self._stored_session(view_as_sid))
        self.assertEqual(running.state, "active")
        self.assertFalse(running.end_at)
        cron = self.env.ref("devarc_portal_login_as.ir_cron_close_expired_view_as")
        self.assertTrue(cron.active)
        self.assertEqual((cron.interval_number, cron.interval_type), (5, "minutes"))

    def test_double_start_blocked(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        with self.assertRaises(JsonRpcException):
            self._start(self.portal_acme)
        self.assertEqual(len(self._logs(self.portal_acme)), 1)
        self.assertEqual(self._session_uid(), self.portal_acme.id)

    def test_fingerprint_mismatch_forces_logout(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        page = self.url_open("/my")
        view_as_sid = self._sid()
        self.salesperson.sudo()._change_password("Other!2345")
        self.env.flush_all()
        self._assert_redirected_to_login(self._post_return(self._csrf_from(page.text)))
        self._assert_session_expired(self._sid())
        self._assert_session_expired(view_as_sid)
        self.assertEqual(self._logs(self.portal_acme).end_reason, "fingerprint_mismatch")

    def test_banner_absent_for_plain_portal_login(self):
        self._login(self.portal_acme)
        page = self.url_open("/my")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(BANNER_TEXT, page.text)

    def test_view_as_buttons_visibility(self):
        sales_users = self.env["res.users"].with_user(self.salesperson)
        self.assertTrue(sales_users.browse(self.portal_acme.id).can_view_as_customer)
        self.assertFalse(sales_users.browse(self.portal_beta.id).can_view_as_customer)
        self.assertFalse(sales_users.browse(self.plain_internal.id).can_view_as_customer)
        self.assertFalse(
            self.env["res.users"].with_user(self.plain_internal).browse(self.portal_acme.id).can_view_as_customer
        )
        sales_partners = self.env["res.partner"].with_user(self.salesperson)
        self.assertTrue(sales_partners.browse(self.acme_contact.id).can_view_as_customer)
        self.assertFalse(sales_partners.browse(self.acme.id).can_view_as_customer)


@tagged("post_install", "-at_install")
class TestViewAsWebsocket(PortalLoginAsHttpCommon, WebsocketCase):

    def assertCanOpenTestCursor(self):
        # bus whitelists acquire_cursor by a POSIX path pattern that never matches on Windows
        if any(
            frame.function == "acquire_cursor" and PurePath(frame.filename).as_posix().endswith("bus/tools/misc.py")
            or frame.function == "_portal_view_as_close_log"
            for frame in inspect.stack()
        ):
            return HttpCase.assertCanOpenTestCursor(self)
        return super().assertCanOpenTestCursor()

    def _view_as_websocket(self):
        self._login(self.salesperson)
        self._start(self.portal_acme)
        view_as_sid = self._sid()
        websocket = self.websocket_connect(cookie=f"session_id={view_as_sid};")
        return view_as_sid, websocket

    def test_websocket_works_during_view_as(self):
        view_as_sid, websocket = self._view_as_websocket()
        self.subscribe(websocket, ["pla_channel"], self.env["bus.bus"]._bus_last_id())
        self.env["bus.bus"]._sendone("pla_channel", "pla_notification", "hello")
        self.trigger_notification_dispatching()
        notifications = json.loads(websocket.recv())
        self.assertEqual(notifications[0]["message"]["payload"], "hello")
        self.assertEqual(self._stored_session(view_as_sid).uid, self.portal_acme.id)

    def test_websocket_closed_after_expiry(self):
        view_as_sid, websocket = self._view_as_websocket()
        log = self._logs(self.portal_acme)
        self._expire(log)
        ir_http = type(self.env["ir.http"])
        with patch.object(
            ir_http, "_portal_view_as_close_log", wraps=ir_http._portal_view_as_close_log,
        ) as close_log:
            self.subscribe(websocket, wait_for_dispatch=False)
            self.assert_close_with_code(websocket, CloseCode.SESSION_EXPIRED)
        close_log.assert_called_once_with(log.id, "expired")
        self.assertFalse(self._stored_session(view_as_sid))
        self.env[LOG_MODEL]._cron_close_expired()
        log = self._logs(self.portal_acme)
        self.assertEqual(log.end_reason, "expired")
        self.assertEqual(log.end_at, log.expires_at)

    def test_websocket_closed_after_admin_stop(self):
        view_as_sid, websocket = self._view_as_websocket()
        self.subscribe(websocket, ["pla_channel"], self.env["bus.bus"]._bus_last_id())
        self._logs(self.portal_acme).with_user(self.sysadmin).action_stop()
        self.env.flush_all()
        self.env["bus.bus"]._sendone("pla_channel", "pla_notification", "secret")
        self.trigger_notification_dispatching()
        self.assert_close_with_code(websocket, CloseCode.SESSION_EXPIRED)
        self.assertFalse(self._stored_session(view_as_sid))
