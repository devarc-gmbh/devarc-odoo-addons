from odoo import models
from odoo.http.session import STORED_SESSION_BYTES, SessionExpiredException, logout, session_store

from odoo.addons.bus.websocket import wsrequest

from .res_users import VIEW_AS_SESSION_KEY


class IrWebsocket(models.AbstractModel):
    _inherit = "ir.websocket"

    @classmethod
    def _authenticate(cls):
        super()._authenticate()
        session = wsrequest.session
        state = session.get(VIEW_AS_SESSION_KEY)
        if not state:
            return
        env = wsrequest.env
        log_model = env["devarc.portal.login.as.log"]
        if log_model._from_session(session):
            return
        log = log_model._from_view_as_state(state)
        end_reason = log._session_end_reason(session.uid)
        if log:
            env["ir.http"]._portal_view_as_close_log(log.id, end_reason)
        if end_reason == "session_changed":
            session.pop(VIEW_AS_SESSION_KEY)
            session_store().save(session)
            return
        session_store().delete_from_identifiers([session.sid[:STORED_SESSION_BYTES]])
        logout(session, keep_db=True)
        raise SessionExpiredException()
