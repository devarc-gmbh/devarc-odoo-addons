import base64
import io
import unittest
from datetime import date
from pathlib import Path

from lxml import etree

from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tests import HttpCase, tagged

from .common import SIX_CREDITOR_NAME, SIX_IBAN, SIX_QR_IBAN, SIX_QR_REFERENCE, TEST_MESSAGE, SwissQrBillCommon

try:
    import pypdfium2 as pdfium
    import zxingcpp
except ImportError:
    pdfium = zxingcpp = None
else:
    from .test_qr_bill_decoder import _qr_code_image

FIXTURES_PATH = Path(__file__).parent / "fixtures"
GERMAN_IBAN = "DE89370400440532013000"
K_ADDRESS_VENDOR = "Robert Schneider AG"
OTHER_QR_REFERENCE = "210000000003139471430009025"
REAL_SWICO_BILLING_INFO = "//S1/10/1092/11/260531/20/LE-65/30/477236108/31/260531/32/8.1/40/0:30"


def _read_fixture(name):
    return (FIXTURES_PATH / name).read_text(encoding="utf-8")


def _swiss_iban(bban):
    check_digits = 98 - int("".join(str(int(char, 36)) for char in f"{bban}CH00")) % 97
    return f"CH{check_digits:02d}{bban}"


def _with_lines(payload_text, replacements):
    lines = payload_text.split("\n")
    for index, value in replacements.items():
        lines[index] = value
    return "\n".join(lines)


def _with_iban(payload_text, iban):
    lines = payload_text.split("\n")
    lines[3] = iban
    return "\n".join(lines)


class QrBillImportCommon(SwissQrBillCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor_qr_iban = _swiss_iban("31999000123456789")
        cls.other_vendor_qr_iban = _swiss_iban("31999000987654321")
        cls.qrr_payload = _read_fixture("decoded_payload_qrr.txt")
        cls.k_address_payload = _with_iban(_read_fixture("qr_payload_k_address_strd.txt"), cls.vendor_qr_iban)
        cls.six_vendor = cls.env["res.partner"].create({"name": SIX_CREDITOR_NAME, "is_company": True})
        (cls.qr_iban_account | cls.iban_account).sudo().write({"partner_id": cls.six_vendor.id})

    def _import(self, company=None, env=None, **wizard_vals):
        env = env or self.env
        wizard = env["devarc.qr.bill.import"].create({
            "company_id": (company or self.env.company).id,
            **wizard_vals,
        })
        action = wizard.action_import()
        bill_action = action["params"]["next"] if action["type"] == "ir.actions.client" else action
        return env["account.move"].browse(bill_action["res_id"]), action

    def _assert_six_qrr_bill(self, bill):
        self.assertRecordValues(bill, [{
            "move_type": "in_invoice",
            "state": "draft",
            "payment_reference": SIX_QR_REFERENCE,
            "currency_id": self.chf.id,
            "amount_total": 1949.75,
            "ref": False,
            "invoice_date": False,
        }])
        self.assertEqual(bill.partner_bank_id.sanitized_acc_number, SIX_QR_IBAN)
        self.assertEqual(len(bill.invoice_line_ids), 1)
        self.assertFalse(bill.invoice_line_ids.tax_ids)


@tagged("post_install", "-at_install")
class TestQrBillImport(QrBillImportCommon):

    def test_text_import_creates_draft_vendor_bill(self):
        bill, action = self._import(payload_text=self.qrr_payload)
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["id"], self.env.ref("account.action_move_in_invoice_type").id)
        self.assertEqual(action["view_mode"], "form")
        self.assertEqual(action["views"], [(False, "form")])
        self._assert_six_qrr_bill(bill)
        self.assertEqual(bill.partner_id, self.qr_iban_account.partner_id)
        self.assertEqual(bill.partner_bank_id, self.qr_iban_account)
        self.assertIn(TEST_MESSAGE, bill.narration)
        self.assertFalse(bill.attachment_ids)

    def test_existing_vendor_is_found_by_iban(self):
        vendor = self.env["res.partner"].create({"name": "Known Vendor GmbH", "zip": "9999"})
        bank_account = self.env["res.partner.bank"].create({"acc_number": self.vendor_qr_iban, "partner_id": vendor.id})
        partner_count = self.env["res.partner"].search_count([])
        bill, _action = self._import(payload_text=self.k_address_payload)
        self.assertEqual(bill.partner_id, vendor)
        self.assertEqual(bill.partner_bank_id, bank_account)
        self.assertEqual(self.env["res.partner"].search_count([]), partner_count)

    def test_existing_vendor_is_found_by_name_and_zip(self):
        vendor = self.env["res.partner"].create({"name": K_ADDRESS_VENDOR, "zip": "2501", "is_company": True})
        partner_count = self.env["res.partner"].search_count([])
        bill, _action = self._import(payload_text=self.k_address_payload)
        self.assertEqual(bill.partner_id, vendor)
        self.assertEqual(bill.partner_bank_id.partner_id, vendor)
        self.assertEqual(bill.partner_bank_id.sanitized_acc_number, self.vendor_qr_iban)
        self.assertFalse(bill.partner_bank_id.allow_out_payment)
        self.assertEqual(self.env["res.partner"].search_count([]), partner_count)

    def test_new_vendor_is_created_from_combined_address(self):
        partner_count = self.env["res.partner"].search_count([])
        bill, _action = self._import(payload_text=self.k_address_payload)
        self.assertEqual(self.env["res.partner"].search_count([]), partner_count + 1)
        self.assertRecordValues(bill.partner_id, [{
            "name": K_ADDRESS_VENDOR,
            "street": "Rue du Lac 1268",
            "zip": "2501",
            "city": "Biel",
            "country_id": self.env.ref("base.ch").id,
            "supplier_rank": 1,
        }])
        self.assertRecordValues(bill.partner_bank_id, [{
            "partner_id": bill.partner_id.id,
            "sanitized_acc_number": self.vendor_qr_iban,
            "allow_out_payment": False,
        }])
        self.assertEqual(bill.amount_total, 3949.75)
        self.assertIn("//S1/10/10201409", bill.narration)

    def test_swico_billing_info_fills_invoice_number_and_date(self):
        bill, _action = self._import(payload_text=self.k_address_payload)
        self.assertRecordValues(bill, [{"ref": "10201409", "invoice_date": date(2020, 7, 1)}])
        self.assertEqual(bill.invoice_line_ids.name, "Auftrag vom 15.06.2020")
        self.assertIn("Auftrag vom 15.06.2020", bill.narration)

    def test_swico_invoice_number_names_line_without_message(self):
        payload_text = _with_lines(self.k_address_payload, {29: "", 31: REAL_SWICO_BILLING_INFO})
        bill, _action = self._import(payload_text=payload_text)
        self.assertRecordValues(bill, [{"ref": "1092", "invoice_date": date(2026, 5, 31)}])
        self.assertEqual(bill.invoice_line_ids.name, "Invoice 1092")

    def test_unstructured_message_is_not_used_as_reference(self):
        bill, _action = self._import(payload_text=self.qrr_payload)
        self.assertFalse(bill.ref)
        self.assertEqual(bill.invoice_line_ids.name, TEST_MESSAGE)

    def test_swico_invoice_date_keeps_date_of_existing_draft_bill(self):
        draft_bill = self.env["account.move"].create({"move_type": "in_invoice", "invoice_date": "2026-01-15", "ref": "Manual"})
        bill, _action = self._import(payload_text=self.k_address_payload, move_id=draft_bill.id)
        self.assertRecordValues(bill, [{"ref": "10201409", "invoice_date": date(2026, 1, 15)}])

    def test_same_message_with_different_qr_references_is_no_duplicate(self):
        qrr_payloads = [
            _with_lines(self.k_address_payload, {28: reference, 29: "Quellensteuer", 31: ""})
            for reference in (SIX_QR_REFERENCE, OTHER_QR_REFERENCE)
        ]
        first_bill, _action = self._import(payload_text=qrr_payloads[0])
        second_bill, action = self._import(payload_text=qrr_payloads[1])
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(second_bill.partner_id, first_bill.partner_id)
        self.assertFalse(first_bill.ref or second_bill.ref)
        _third_bill, duplicate_action = self._import(payload_text=qrr_payloads[1])
        self.assertEqual(duplicate_action["tag"], "display_notification")
        self.assertIn(OTHER_QR_REFERENCE, duplicate_action["params"]["title"])
        self.assertEqual(duplicate_action["params"]["links"], [
            {"label": second_bill.display_name, "url": f"/odoo/account.move/{second_bill.id}"},
        ])

    def test_new_vendor_is_created_from_structured_address(self):
        payload_text = _with_iban(self.qrr_payload, self.other_vendor_qr_iban).replace(
            "Max Muster & Söhne (sample company)", "Muster Lieferant AG",
        )
        bill, _action = self._import(payload_text=payload_text)
        self.assertRecordValues(bill.partner_id, [{
            "name": "Muster Lieferant AG",
            "street": "Musterstrasse 123",
            "zip": "8000",
            "city": "Seldwyla",
        }])
        self.assertEqual(bill.partner_bank_id.sanitized_acc_number, self.other_vendor_qr_iban)

    def test_duplicate_reference_warns_and_still_creates_bill(self):
        first_bill, _action = self._import(payload_text=self.qrr_payload)
        bill_count = self.env["account.move"].search_count([("move_type", "=", "in_invoice")])
        second_bill, action = self._import(payload_text=self.qrr_payload)
        self.assertEqual(self.env["account.move"].search_count([("move_type", "=", "in_invoice")]), bill_count + 1)
        self.assertNotEqual(second_bill, first_bill)
        self._assert_six_qrr_bill(second_bill)
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn(SIX_QR_REFERENCE, action["params"]["title"])
        self.assertEqual(action["params"]["links"], [
            {"label": first_bill.display_name, "url": f"/odoo/account.move/{first_bill.id}"},
        ])

    def test_qr_bill_without_amount_creates_bill_without_lines(self):
        bill, action = self._import(payload_text=_read_fixture("qr_payload_non_no_amount.txt"))
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertFalse(bill.invoice_line_ids)
        self.assertEqual(bill.amount_total, 0.0)
        self.assertFalse(bill.payment_reference)
        self.assertEqual(bill.partner_bank_id.sanitized_acc_number, SIX_IBAN)

    def test_existing_draft_bill_is_completed(self):
        draft_bill = self.env["account.move"].create({"move_type": "in_invoice"})
        bill_count = self.env["account.move"].search_count([])
        bill, action = self._import(payload_text=self.qrr_payload, move_id=draft_bill.id)
        self.assertEqual(action["res_id"], draft_bill.id)
        self.assertEqual(bill, draft_bill)
        self._assert_six_qrr_bill(bill)
        self.assertEqual(self.env["account.move"].search_count([]), bill_count)

    def test_existing_draft_bill_lines_are_kept(self):
        draft_bill = self.env["account.move"].create({
            "move_type": "in_invoice",
            "invoice_line_ids": [Command.create({"name": "Manual line", "quantity": 1, "price_unit": 100.0, "tax_ids": [Command.clear()]})],
        })
        bill, _action = self._import(payload_text=self.qrr_payload, move_id=draft_bill.id)
        self.assertEqual(bill.invoice_line_ids.mapped("name"), ["Manual line"])
        self.assertEqual(bill.payment_reference, SIX_QR_REFERENCE)

    def test_posted_bill_cannot_be_completed(self):
        posted_bill = self.init_invoice("in_invoice", amounts=[100.0], post=True)
        with self.assertRaisesRegex(UserError, "draft vendor bill"):
            self._import(payload_text=self.qrr_payload, move_id=posted_bill.id)

    def test_invalid_input_raises_user_error_without_creating_moves(self):
        move_count = self.env["account.move"].search_count([])
        invalid_inputs = {
            "german iban": {"payload_text": _with_iban(self.qrr_payload, GERMAN_IBAN)},
            "foreign text": {"payload_text": "https://devarc.ch"},
            "empty input": {},
            "not a document": {"attachment": base64.b64encode(b"not an image"), "attachment_name": "photo.jpg"},
        }
        for case, wizard_vals in invalid_inputs.items():
            with self.subTest(case=case), self.assertRaises(UserError):
                self._import(**wizard_vals)
        self.assertEqual(self.env["account.move"].search_count([]), move_count)

    def test_qr_bill_payable_to_own_company_is_rejected(self):
        own_partners = {
            "company partner": self.env.company.partner_id,
            "company contact": self.env["res.partner"].create({"name": "Accounting", "parent_id": self.env.company.partner_id.id}),
        }
        for index, (case, own_partner) in enumerate(own_partners.items()):
            with self.subTest(case=case):
                iban = _swiss_iban(f"3199900055555555{index}")
                self.env["res.partner.bank"].create({"acc_number": iban, "partner_id": own_partner.id})
                move_count = self.env["account.move"].search_count([])
                partner_count = self.env["res.partner"].search_count([])
                with self.assertRaisesRegex(UserError, "your own company"):
                    self._import(payload_text=_with_iban(self.k_address_payload, iban))
                self.assertEqual(self.env["account.move"].search_count([]), move_count)
                self.assertEqual(self.env["res.partner"].search_count([]), partner_count)

    def test_import_button_is_always_shown_on_vendor_bill_list(self):
        import_action = self.env.ref("devarc_qr_invoice.action_devarc_qr_bill_import")
        bills_action = self.env.ref("account.action_move_in_invoice_type")
        arch = etree.fromstring(self.env["account.move"].get_view(bills_action.view_id.id, "list")["arch"])
        buttons = arch.xpath(f"//header/button[@name='{import_action.id}']")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get("display"), "always")
        self.assertEqual(buttons[0].get("type"), "action")

    def test_read_button_is_hidden_on_unsaved_bill(self):
        import_action = self.env.ref("devarc_qr_invoice.action_devarc_qr_bill_import")
        arch = etree.fromstring(self.env["account.move"].get_view(self.env.ref("account.view_move_form").id, "form")["arch"])
        button = arch.xpath(f"//header/button[@name='{import_action.id}']")[0]
        self.assertIn("not id", button.get("invisible"))

    def test_bank_account_and_vendor_of_other_company_are_ignored(self):
        other_company = self.setup_other_company(name="QR Other Company")["company"]
        other_vendor = self.env["res.partner"].create({
            "name": K_ADDRESS_VENDOR,
            "zip": "2501",
            "company_id": other_company.id,
        })
        other_bank_account = self.env["res.partner.bank"].create({
            "acc_number": self.vendor_qr_iban,
            "partner_id": other_vendor.id,
        })
        self.assertEqual(other_bank_account.company_id, other_company)
        self.env.user.company_ids |= other_company
        importing_env = self.env(context={**self.env.context, "allowed_company_ids": [self.env.company.id, other_company.id]})
        bill, _action = self._import(payload_text=self.k_address_payload, company=self.env.company, env=importing_env)
        self.assertEqual(bill.company_id, self.env.company)
        self.assertNotEqual(bill.partner_id, other_vendor)
        self.assertNotEqual(bill.partner_bank_id, other_bank_account)
        self.assertEqual(bill.partner_bank_id.sanitized_acc_number, self.vendor_qr_iban)
        self.assertFalse(bill.partner_id.company_id)

    def test_wizard_access_is_granted_to_invoicing_users(self):
        access = self.env["ir.model.access"].search([("model_id.model", "=", "devarc.qr.bill.import")])
        self.assertEqual(access.group_id, self.env.ref("account.group_account_invoice"))
        self.assertTrue(all(access.mapped(lambda rule: rule.perm_read and rule.perm_write and rule.perm_create)))


@tagged("post_install", "-at_install")
@unittest.skipUnless(pdfium and zxingcpp, "pypdfium2 and zxing-cpp are required to decode QR-bills")
class TestQrBillImportDocument(QrBillImportCommon, HttpCase):

    def test_pdf_import_creates_bill_with_attached_document(self):
        pdf_buffer = io.BytesIO()
        _qr_code_image(self.qrr_payload).save(pdf_buffer, "PDF")
        pdf_content = pdf_buffer.getvalue()
        bill, _action = self._import(attachment=base64.b64encode(pdf_content), attachment_name="six-qr-bill.pdf")
        self._assert_six_qrr_bill(bill)
        attachment = self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", bill.id)])
        self.assertEqual(attachment.name, "six-qr-bill.pdf")
        self.assertEqual(attachment.raw, pdf_content)
        self.assertEqual(bill.message_main_attachment_id, attachment)
