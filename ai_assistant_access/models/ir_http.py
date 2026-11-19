import json

from werkzeug.exceptions import RequestEntityTooLarge

from odoo import models
from odoo.http import request

from .ai_assistant_guard import API_ROUTE_PREFIX, JSON_RESPONSE_HEADERS


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _handle_error(cls, exception):
        # the body limit is enforced by werkzeug before the route runs, so the route cannot answer it itself
        if isinstance(exception, RequestEntityTooLarge) and request.httprequest.path.startswith(API_ROUTE_PREFIX):
            return request.make_response(
                json.dumps({"error": {"code": "request_too_large", "message": "The request body is too large."}}),
                headers=JSON_RESPONSE_HEADERS,
                status=413,
            )
        return super()._handle_error(exception)
