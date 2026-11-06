from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import (
    SIX_CREDITOR_NAME,
    SIX_DEBTOR_NAME,
    SIX_IBAN,
    SIX_QR_IBAN,
    SIX_QR_REFERENCE,
    SIX_SCOR_REFERENCE,
    TEST_MESSAGE,
    SwissQrBillCommon,
)


@tagged("post_install", "-at_install")
class TestQrBillPayload(SwissQrBillCommon):

    def _assert_common_fields(self, fields_list, account, amount, currency):
        self.assertEqual(len(fields_list), 31)
        self.assertEqual(fields_list[:3], ["SPC", "0200", "1"])
        self.assertEqual(fields_list[3], account)
        self.assertEqual(fields_list[4:11], [
            "S", SIX_CREDITOR_NAME, "Musterstrasse", "123", "8000", "Seldwyla", "CH",
        ])
        self.assertEqual(fields_list[11:18], [""] * 7)
        self.assertEqual(fields_list[18:20], [amount, currency])
        self.assertEqual(fields_list[20:27], [
            "S", SIX_DEBTOR_NAME, "Musterstrasse", "1", "8000", "Seldwyla", "CH",
        ])
        self.assertEqual(fields_list[30], "EPD")

    def test_qr_iban_payload_uses_qrr_reference(self):
        invoice = self._create_qr_invoice(self.qr_iban_account, payment_reference=SIX_QR_REFERENCE)
        payload = invoice._devarc_get_qr_bill_payload()
        fields_list = payload.split("\n")
        self._assert_common_fields(fields_list, SIX_QR_IBAN, "1949.75", "CHF")
        self.assertEqual(fields_list[27:30], ["QRR", SIX_QR_REFERENCE, TEST_MESSAGE])
        self.assertLessEqual(len(payload), 997)
        self.assertFalse(invoice.devarc_qr_bill_error)
        self.assertTrue(invoice.l10n_ch_is_qr_valid)

    def test_iban_payload_uses_scor_reference_in_eur(self):
        invoice = self._create_qr_invoice(
            self.iban_account, amount=250.5, currency=self.eur, payment_reference=SIX_SCOR_REFERENCE,
        )
        fields_list = invoice._devarc_get_qr_bill_payload().split("\n")
        self._assert_common_fields(fields_list, SIX_IBAN, "250.50", "EUR")
        self.assertEqual(fields_list[27:30], ["SCOR", SIX_SCOR_REFERENCE, TEST_MESSAGE])

    def test_max_amount_is_accepted(self):
        invoice = self._create_qr_invoice(self.iban_account, amount=999999999.99)
        self.assertEqual(invoice._devarc_get_qr_bill_payload().split("\n")[18], "999999999.99")

    def test_qrr_with_regular_iban_falls_back_to_non(self):
        invoice = self._create_qr_invoice(self.iban_account, payment_reference=SIX_QR_REFERENCE)
        fields_list = invoice._devarc_get_qr_bill_payload().split("\n")
        self.assertEqual(len(fields_list), 31)
        self.assertEqual(fields_list[3], SIX_IBAN)
        self.assertEqual(fields_list[27:30], ["NON", "", TEST_MESSAGE])
        self.assertIs(invoice.l10n_ch_is_qr_valid, True)
        self.assertFalse(invoice.devarc_qr_bill_error)

    def test_amount_above_maximum_is_rejected(self):
        invoice = self._create_qr_invoice(self.iban_account, amount=1000000000.00)
        with self.assertRaisesRegex(UserError, "between 0.01"):
            invoice._devarc_get_qr_bill_payload()
        self.assertFalse(invoice.l10n_ch_is_qr_valid)

    def test_zero_amount_is_rejected(self):
        invoice = self._create_qr_invoice(self.iban_account, amount=0.0)
        with self.assertRaisesRegex(UserError, "between 0.01"):
            invoice._devarc_get_qr_bill_payload()
        self.assertFalse(invoice.l10n_ch_is_qr_valid)

    def test_foreign_iban_is_rejected(self):
        account = self._create_bank_account("DE89370400440532013000")
        invoice = self._create_qr_invoice(account, currency=self.eur)
        with self.assertRaisesRegex(UserError, "CH/LI"):
            invoice._devarc_get_qr_bill_payload()
        self.assertIn("CH/LI", invoice.devarc_qr_bill_error)
        self.assertFalse(invoice.l10n_ch_is_qr_valid)

    def _register_payment(self, invoice, amount=None):
        wizard_vals = {"amount": amount} if amount is not None else {}
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=invoice.ids,
        ).create(wizard_vals)._create_payments()

    def test_fully_paid_invoice_has_no_qr_bill_error(self):
        invoice = self._create_qr_invoice(self.iban_account, amount=250.0)
        self._register_payment(invoice)
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertFalse(invoice.devarc_qr_bill_error)

    def test_partially_paid_invoice_bills_the_residual(self):
        invoice = self._create_qr_invoice(self.iban_account, amount=250.0)
        self._register_payment(invoice, amount=100.0)
        self.assertEqual(invoice.payment_state, "partial")
        self.assertFalse(invoice.devarc_qr_bill_error)
        self.assertEqual(invoice._devarc_get_qr_bill_payload().split("\n")[18], "150.00")

    def test_vendor_bill_has_no_qr_bill_error(self):
        bill = self.init_invoice("in_invoice", amounts=[100.0])
        self.assertFalse(bill.devarc_qr_bill_error)
