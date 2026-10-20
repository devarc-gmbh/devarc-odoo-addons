from odoo import api, models


class ReportL10nChQrReportMain(models.AbstractModel):
    _inherit = "report.l10n_ch.qr_report_main"

    @api.model
    def _get_report_values(self, docids, data=None):
        values = super()._get_report_values(docids, data=data)
        values["qr_code_urls"] = {
            invoice.id: invoice.partner_bank_id.build_qr_code_base64(
                qr_method="ch_qr",
                silent_errors=False,
                **invoice._devarc_qr_bill_args(),
            )
            for invoice in values["docs"]
        }
        return values
