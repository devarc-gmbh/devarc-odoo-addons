import io
import unittest
from pathlib import Path

from PIL import Image, ImageFilter

from odoo.tests import HttpCase, tagged

from odoo.addons.devarc_qr_invoice.tools.qr_bill_decoder import decode
from odoo.addons.devarc_qr_invoice.tools.qr_bill_payload import QrBillPayloadError, parse

from .common import SIX_QR_IBAN, SIX_QR_REFERENCE, SwissQrBillCommon

try:
    import pypdfium2 as pdfium
    import zxingcpp
except ImportError:
    pdfium = zxingcpp = None

EXPECTED_PAYLOAD_PATH = Path(__file__).parent / "fixtures" / "decoded_payload_qrr.txt"
RENDER_SCALE = 300 / 72
PAPER_COLOR = (240, 238, 230)


def _image_bytes(image, image_format, **save_options):
    buffer = io.BytesIO()
    image.save(buffer, image_format, **save_options)
    return buffer.getvalue()


def _qr_code_image(text):
    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    matrix = memoryview(zxingcpp.write_barcode_to_image(barcode, scale=5))
    return Image.frombytes("L", (matrix.shape[1], matrix.shape[0]), matrix.tobytes())


@tagged("post_install", "-at_install")
@unittest.skipUnless(pdfium and zxingcpp, "pypdfium2 and zxing-cpp are required to decode QR-bills")
class TestQrBillDecoder(SwissQrBillCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.expected_payload = EXPECTED_PAYLOAD_PATH.read_text(encoding="utf-8")

    def _render_invoice_pdf(self):
        invoice = self._create_qr_invoice(self.qr_iban_account, payment_reference=SIX_QR_REFERENCE)
        report = self.env["ir.actions.report"].with_context(force_report_rendering=True)
        pdf_content, content_type = report._render_qweb_pdf("account.account_invoices", invoice.ids)
        self.assertEqual(content_type, "pdf")
        return pdf_content

    def test_invoice_pdf_is_decoded_to_six_payload(self):
        payload_text = decode(self._render_invoice_pdf())
        self.assertEqual(payload_text, self.expected_payload)
        payload = parse(payload_text)
        self.assertEqual(payload.iban, SIX_QR_IBAN)
        self.assertEqual(payload.reference, SIX_QR_REFERENCE)

    def test_degraded_photos_of_the_qr_bill_are_decoded(self):
        document = pdfium.PdfDocument(self._render_invoice_pdf())
        page = document[len(document) - 1].render(scale=RENDER_SCALE).to_pil().convert("RGB")
        photos = {
            "rotated 7 degrees": _image_bytes(page.rotate(7, expand=True, fillcolor=PAPER_COLOR), "PNG"),
            "blurred jpeg q40": _image_bytes(page.filter(ImageFilter.GaussianBlur(1.5)), "JPEG", quality=40),
        }
        for case, photo in photos.items():
            with self.subTest(case=case):
                self.assertEqual(decode(photo), self.expected_payload)

    def test_image_without_qr_bill_is_rejected(self):
        images = {
            "blank page": _image_bytes(Image.new("RGB", (800, 600), "white"), "PNG"),
            "foreign qr code": _image_bytes(_qr_code_image("https://devarc.ch"), "PNG"),
        }
        for case, image in images.items():
            with self.subTest(case=case), self.assertRaisesRegex(QrBillPayloadError, "no Swiss QR-bill"):
                decode(image)

    def test_two_different_qr_bills_are_rejected(self):
        other_payload = self.expected_payload.replace("1949.75", "100.00")
        first_code, second_code = _qr_code_image(self.expected_payload), _qr_code_image(other_payload)
        canvas = Image.new("L", (first_code.width + second_code.width + 75, max(first_code.height, second_code.height) + 50), 255)
        canvas.paste(first_code, (25, 25))
        canvas.paste(second_code, (first_code.width + 50, 25))
        with self.assertRaisesRegex(QrBillPayloadError, "2 different Swiss QR-bills"):
            decode(_image_bytes(canvas, "PNG"))

    def test_unreadable_documents_are_rejected(self):
        for data in (b"", b"not an image", b"%PDF-1.7 broken"):
            with self.subTest(data=data), self.assertRaises(QrBillPayloadError):
                decode(data)
