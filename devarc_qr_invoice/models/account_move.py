from odoo import api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    devarc_qr_bill_error = fields.Text(
        string="QR-bill Problem",
        compute="_compute_devarc_qr_bill_error",
        help="Reason why the Swiss QR-bill cannot be generated for this invoice, empty when it can.",
    )

    @api.depends("partner_id", "currency_id", "partner_bank_id", "amount_residual", "payment_reference")
    def _compute_l10n_ch_qr_is_valid(self):
        super()._compute_l10n_ch_qr_is_valid()
        for move in self.filtered("l10n_ch_is_qr_valid"):
            move.l10n_ch_is_qr_valid = not move.partner_bank_id._devarc_check_qr_bill_rules(move.amount_residual)

    @api.depends(
        "move_type",
        "partner_id",
        "partner_bank_id",
        "currency_id",
        "amount_residual",
        "payment_reference",
        "ref",
        "name",
        "state",
    )
    def _compute_devarc_qr_bill_error(self):
        self.devarc_qr_bill_error = False
        for move in self.filtered(lambda m: m.move_type == "out_invoice" and m.partner_bank_id):
            if move.state == "posted" and move.currency_id.is_zero(move.amount_residual):
                continue
            try:
                move._devarc_get_qr_bill_payload()
            except UserError as error:
                move.devarc_qr_bill_error = error.args[0]

    def _devarc_qr_bill_free_communication(self):
        self.ensure_one()
        bank = self.partner_bank_id
        if (
            bank.l10n_ch_qr_iban
            or bank._is_iso11649_reference(self.payment_reference)
            or bank._is_qr_reference(self.payment_reference)
        ):
            return self.ref or self.name
        return self.payment_reference or self.ref or self.name

    def _devarc_qr_bill_args(self):
        self.ensure_one()
        return {
            "amount": self.amount_residual,
            "free_communication": self._devarc_qr_bill_free_communication(),
            "structured_communication": self.payment_reference,
            "currency": self.currency_id,
            "debtor_partner": self.partner_id,
        }

    def _devarc_get_qr_bill_payload(self):
        self.ensure_one()
        qr_args = self._devarc_qr_bill_args()
        self.partner_bank_id._build_qr_code_vals(qr_method="ch_qr", silent_errors=False, **qr_args)
        return "\n".join(self.partner_bank_id._get_qr_vals(qr_method="ch_qr", **qr_args))
