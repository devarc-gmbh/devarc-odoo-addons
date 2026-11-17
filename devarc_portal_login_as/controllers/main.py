import hmac

from werkzeug.exceptions import Forbidden

from odoo import SUPERUSER_ID, http
from odoo.addons.web.controllers.session import Session
from odoo.http import request

from ..models.res_users import VIEW_AS_SESSION_KEY


class PortalLoginAsController(http.Controller):

    @http.route("/portal_login_as/return", type="http", auth="user", methods=["POST"], readonly=False)
    def portal_login_as_return(self, **kwargs):
        state = request.session.get(VIEW_AS_SESSION_KEY)
        if not state:
            raise Forbidden()
        log = request.env["devarc.portal.login.as.log"]._from_view_as_state(state)
        origin = request.env["res.users"].sudo().browse(state["origin_uid"]).exists()
        expected = origin.active and origin._portal_view_as_fingerprint(state["nonce"])
        if not expected or not hmac.compare_digest(expected, state["fingerprint"]):
            log._close("fingerprint_mismatch")
            request.session.logout(keep_db=True)
            return request.redirect("/web/login", 303)
        log._close("returned")
        request.session.pop(VIEW_AS_SESSION_KEY)
        request.env["res.users"]._portal_view_as_finalize_session(origin)
        return request.redirect(f"/odoo/action-devarc_portal_login_as.action_portal_login_as_log/{log.id}", 303)


class PortalLoginAsSession(Session):

    @http.route("/web/session/logout", type="http", auth="none", readonly=False)
    def logout(self, redirect="/odoo"):
        self._portal_view_as_close_on_logout()
        return super().logout(redirect=redirect)

    @http.route("/web/session/destroy", type="jsonrpc", auth="user", readonly=False)
    def destroy(self):
        self._portal_view_as_close_on_logout()
        return super().destroy()

    def _portal_view_as_close_on_logout(self):
        state = request.session.get(VIEW_AS_SESSION_KEY)
        if state:
            log_model = request.env["devarc.portal.login.as.log"].with_user(SUPERUSER_ID)
            log_model._from_view_as_state(state)._close("logout")
