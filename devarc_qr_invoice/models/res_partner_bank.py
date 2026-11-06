from odoo import models

QR_BILL_MIN_AMOUNT = 0.01
QR_BILL_MAX_AMOUNT = 999999999.99
QR_BILL_IBAN_COUNTRIES = ("CH", "LI")


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    def _check_for_qr_code_errors(self, qr_method, amount, currency, debtor_partner, free_communication, structured_communication):
        if qr_method == "ch_qr":
            error = self._devarc_check_qr_bill_rules(amount)
            if error:
                return error
        return super()._check_for_qr_code_errors(qr_method, amount, currency, debtor_partner, free_communication, structured_communication)

    def _devarc_check_qr_bill_rules(self, amount):
        if (self.sanitized_acc_number or "")[:2] not in QR_BILL_IBAN_COUNTRIES:
            return self.env._("A Swiss QR-bill requires a Swiss or Liechtenstein IBAN (CH/LI).")
        if not QR_BILL_MIN_AMOUNT <= round(amount, 2) <= QR_BILL_MAX_AMOUNT:
            return self.env._("The QR-bill amount must be between 0.01 and 999,999,999.99.")
        return None
