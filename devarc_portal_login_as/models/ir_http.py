from odoo import SUPERUSER_ID, api, http, models
from odoo.http import request

from .res_users import VIEW_AS_SESSION_KEY


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _authenticate(cls, endpoint):
        super()._authenticate(endpoint)
        cls._portal_view_as_check()

    @classmethod
    def _serve_fallback(cls):
        cls._portal_view_as_check()
        return super()._serve_fallback()

    @classmethod
    def _portal_view_as_check(cls):
        state = request.session.get(VIEW_AS_SESSION_KEY)
        if not state:
            return
        log_model = request.env["devarc.portal.login.as.log"]
        if log_model._from_session(request.session):
            return
        log = log_model._from_view_as_state(state)
        end_reason = log._session_end_reason(request.session.uid)
        if log:
            cls._portal_view_as_close_log(log.id, end_reason)
        if end_reason == "session_changed":
            request.session.pop(VIEW_AS_SESSION_KEY)
            return
        raise http.SessionExpiredException("The view-as session has ended.")

    @classmethod
    def _portal_view_as_close_log(cls, log_id, end_reason):
        # own read-write cursor: fallback routes may run on a read-only one and the request rolls back on expiry
        with cls.pool.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env["devarc.portal.login.as.log"].browse(log_id)._close(end_reason)
