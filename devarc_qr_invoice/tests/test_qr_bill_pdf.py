import unittest
from pathlib import Path

from odoo.tests import HttpCase, tagged

from .common import SIX_IBAN, SIX_QR_IBAN, SIX_QR_REFERENCE, TEST_MESSAGE, SwissQrBillCommon

try:
    import pypdfium2 as pdfium
    import zxingcpp
except ImportError:
    pdfium = zxingcpp = None

EXPECTED_PAYLOAD_PATH = Path(__file__).parent / "fixtures" / "decoded_payload_qrr.txt"
RENDER_DPI = 300


@tagged("post_install", "-at_install")
@unittest.skipUnless(pdfium and zxingcpp, "pypdfium2 and zxing-cpp are required to decode the rendered QR-bill")
class TestQrBillPdf(SwissQrBillCommon, HttpCase):

    def _decode_invoice_qr(self, invoice):
        report = self.env["ir.actions.report"].with_context(force_report_rendering=True)
        pdf_content, content_type = report._render_qweb_pdf("account.account_invoices", invoice.ids)
        self.assertEqual(content_type, "pdf")
        document = pdfium.PdfDocument(pdf_content)
        page_image = document[len(document) - 1].render(scale=RENDER_DPI / 72).to_pil()
        barcodes = [code for code in zxingcpp.read_barcodes(page_image) if code.format == zxingcpp.BarcodeFormat.QRCode]
        self.assertEqual(len(barcodes), 1, "exactly one QR code is expected on the QR-bill page")
        self.assertEqual(barcodes[0].ec_level, "M")
        return barcodes[0].text

    def test_pdf_qr_payload_matches_six_reference_on_all_fields(self):
        invoice = self._create_qr_invoice(self.qr_iban_account, payment_reference=SIX_QR_REFERENCE)
        self.assertTrue(invoice.l10n_ch_is_qr_valid)
        expected_fields = EXPECTED_PAYLOAD_PATH.read_text(encoding="utf-8").split("\n")
        decoded_fields = self._decode_invoice_qr(invoice).split("\n")
        self.assertEqual(len(expected_fields), 31)
        self.assertEqual(decoded_fields, expected_fields)
        self.assertEqual(decoded_fields[3], SIX_QR_IBAN)

    def test_pdf_qrr_with_regular_iban_prints_non_reference(self):
        invoice = self._create_qr_invoice(self.iban_account, payment_reference=SIX_QR_REFERENCE)
        self.assertTrue(invoice.l10n_ch_is_qr_valid)
        decoded_fields = self._decode_invoice_qr(invoice).split("\n")
        self.assertEqual(len(decoded_fields), 31)
        self.assertEqual(decoded_fields[3], SIX_IBAN)
        self.assertEqual(decoded_fields[27:30], ["NON", "", TEST_MESSAGE])
