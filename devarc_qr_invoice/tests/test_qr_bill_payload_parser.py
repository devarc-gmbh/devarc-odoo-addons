from datetime import date
from decimal import Decimal
from pathlib import Path

from odoo.tests import TransactionCase, tagged

from odoo.addons.devarc_qr_invoice.tools.qr_bill_payload import QrBillPayloadError, parse

from .common import SIX_IBAN, SIX_QR_IBAN, SIX_QR_REFERENCE, SIX_SCOR_REFERENCE

FIXTURES_PATH = Path(__file__).parent / "fixtures"


def _read_fixture(name):
    return (FIXTURES_PATH / name).read_text(encoding="utf-8")


def _replace_line(lines, index, value):
    return [*lines[:index], value, *lines[index + 1:]]


@tagged("post_install", "-at_install")
class TestQrBillPayloadParser(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.qrr_lines = _read_fixture("decoded_payload_qrr.txt").split("\n")
        cls.scor_lines = _read_fixture("qr_payload_scor_eur.txt").split("\n")

    def test_six_qrr_payload(self):
        payload = parse(_read_fixture("decoded_payload_qrr.txt"))
        self.assertEqual(payload.iban, SIX_QR_IBAN)
        self.assertTrue(payload.is_qr_iban)
        self.assertEqual(payload.amount, Decimal("1949.75"))
        self.assertEqual(payload.currency, "CHF")
        self.assertEqual(payload.reference_type, "QRR")
        self.assertEqual(payload.reference, SIX_QR_REFERENCE)
        self.assertEqual(payload.unstructured_message, "TEST - NEPLATIT Order from 15.10.2020")
        self.assertEqual(payload.creditor.address_type, "S")
        self.assertEqual(payload.creditor.name, "Max Muster & Söhne (sample company)")
        self.assertEqual(payload.creditor.street_or_line_1, "Musterstrasse")
        self.assertEqual(payload.creditor.building_or_line_2, "123")
        self.assertEqual((payload.creditor.zip, payload.creditor.city, payload.creditor.country_code), ("8000", "Seldwyla", "CH"))
        self.assertIsNone(payload.ultimate_creditor)
        self.assertEqual(payload.debtor.name, "Simon Muster")
        self.assertEqual(payload.billing_info, "")
        self.assertEqual(payload.alt_procedures, ())

    def test_crlf_and_trailing_newline_are_accepted(self):
        payload = parse("\r\n".join(self.qrr_lines) + "\r\n")
        self.assertEqual(payload.reference, SIX_QR_REFERENCE)

    def test_scor_payload_in_eur(self):
        payload = parse(_read_fixture("qr_payload_scor_eur.txt"))
        self.assertEqual(payload.iban, SIX_IBAN)
        self.assertFalse(payload.is_qr_iban)
        self.assertEqual(payload.amount, Decimal("250.50"))
        self.assertEqual(payload.currency, "EUR")
        self.assertEqual(payload.reference_type, "SCOR")
        self.assertEqual(payload.reference, SIX_SCOR_REFERENCE)

    def test_non_payload_without_amount_and_debtor(self):
        payload = parse(_read_fixture("qr_payload_non_no_amount.txt"))
        self.assertIsNone(payload.amount)
        self.assertIsNone(payload.debtor)
        self.assertEqual(payload.reference_type, "NON")
        self.assertEqual(payload.reference, "")
        self.assertEqual(payload.unstructured_message, "TEST - NEPLATIT Donation")

    def test_combined_address_with_billing_info_and_alternative_procedures(self):
        payload = parse(_read_fixture("qr_payload_k_address_strd.txt"))
        self.assertEqual(payload.creditor.address_type, "K")
        self.assertEqual(payload.creditor.street_or_line_1, "Rue du Lac 1268")
        self.assertEqual(payload.creditor.building_or_line_2, "2501 Biel")
        self.assertEqual((payload.creditor.zip, payload.creditor.city), ("", ""))
        self.assertEqual(payload.debtor.name, "Pia-Maria Rutschmann-Schnyder")
        self.assertEqual(payload.amount, Decimal("3949.75"))
        self.assertTrue(payload.billing_info.startswith("//S1/"))
        self.assertEqual(payload.alt_procedures, ("Name AV1: UV;UltraPay005;12345", "Name AV2: XY;XYService;54321"))
        self.assertEqual(payload.invoice_number, "10201409")
        self.assertEqual(payload.invoice_date, date(2020, 7, 1))

    def test_swico_billing_info_invoice_number_and_date(self):
        billing_infos = {
            "//S1/10/1092/11/260531/20/LE-65/30/477236108/31/260531/32/8.1/40/0:30": ("1092", date(2026, 5, 31)),
            "//S1/10/1093/11/260531/30/477236108/32/8.1/40/0:30": ("1093", date(2026, 5, 31)),
            "//S1/10/11630/11/260608": ("11630", date(2026, 6, 8)),
            "//S1/10/11630/11/260601260630": ("11630", date(2026, 6, 1)),
            "//S1/10/RE\\/2026\\\\7/30/477236108": ("RE/2026\\7", None),
            "//S1/30/477236108/32/8.1": ("", None),
            "//S1/10/1092/11/261340": ("1092", None),
            "//S1/10/1092/11/2605": ("1092", None),
        }
        for billing_info, (invoice_number, invoice_date) in billing_infos.items():
            with self.subTest(billing_info=billing_info):
                payload = parse("\n".join(_replace_line(self.qrr_lines, 31, billing_info)))
                self.assertEqual((payload.invoice_number, payload.invoice_date), (invoice_number, invoice_date))

    def test_invalid_or_missing_swico_billing_info_is_ignored(self):
        billing_infos = ("", "Rechnung 1092", "//S2/10/1092", "//S1/10", "//S1/10/1092/11", "//S1/x1/1092", "/S1/10/1092")
        for billing_info in billing_infos:
            with self.subTest(billing_info=billing_info):
                payload = parse("\n".join(_replace_line(self.qrr_lines, 31, billing_info)))
                self.assertEqual((payload.invoice_number, payload.invoice_date), ("", None))
                self.assertEqual(payload.billing_info, billing_info)

    def test_invalid_payloads_are_rejected(self):
        invalid_payloads = {
            "wrong header": _replace_line(self.qrr_lines, 0, "SPX"),
            "version 0100": _replace_line(self.qrr_lines, 1, "0100"),
            "coding type 2": _replace_line(self.qrr_lines, 2, "2"),
            "german iban": _replace_line(self.scor_lines, 3, "DE89370400440532013000"),
            "iban checksum": _replace_line(self.scor_lines, 3, "CH5900791123000889012"),
            "qrr check digit": _replace_line(self.qrr_lines, 28, SIX_QR_REFERENCE[:-1] + "8"),
            "scor mod97": _replace_line(self.scor_lines, 28, "RF18539007547035"),
            "currency usd": _replace_line(self.qrr_lines, 19, "USD"),
            "zero amount": _replace_line(self.qrr_lines, 18, "0.00"),
            "amount above maximum": _replace_line(self.qrr_lines, 18, "1000000000.00"),
            "amount with three decimals": _replace_line(self.qrr_lines, 18, "1949.750"),
            "trailer": _replace_line(self.qrr_lines, 30, "EPX"),
            "30 lines": self.qrr_lines[:30],
            "35 lines": [*self.qrr_lines, "", "", "", "x"],
            "qrr with regular iban": _replace_line(self.qrr_lines, 3, SIX_IBAN),
            "scor with qr-iban": _replace_line(self.scor_lines, 3, SIX_QR_IBAN),
            "non with reference": _replace_line(_replace_line(self.scor_lines, 27, "NON"), 28, SIX_SCOR_REFERENCE),
            "unknown reference type": _replace_line(self.scor_lines, 27, "ESR"),
            "unknown address type": _replace_line(self.qrr_lines, 4, "X"),
            "missing creditor": [*self.qrr_lines[:4], *[""] * 7, *self.qrr_lines[11:]],
        }
        for case, lines in invalid_payloads.items():
            with self.subTest(case=case), self.assertRaises(QrBillPayloadError):
                parse("\n".join(lines))

    def test_empty_payload_is_rejected(self):
        for text in ("", "   ", None):
            with self.subTest(text=text), self.assertRaises(QrBillPayloadError):
                parse(text)
