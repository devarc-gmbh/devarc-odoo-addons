import base64

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tools.misc import file_path
from odoo.tools.safe_eval import safe_eval

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

QRR_EXACT = "210000000003139471430009017"
QRR_PARTIAL = "000000000000000000000010014"
QRR_OVERPAID = "000000000000000000000010043"
QRR_TWO_INVOICES = "000000000000000000000010059"
QRR_ONLY_IN_REF = "000000000000000000000010067"
QRR_UNKNOWN = "000000000000000000000099990"
SCOR_EXACT = "RF18539007547034"
QRR_VENDOR_BILL = "000000000000000000000070076"
JOURNAL_IBAN = "CH5604835012345678009"


@tagged("post_install", "-at_install")
class TestQrReconcile(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(
            context={**cls.env.context, "_test_account_reconcile_oca": True}
        )
        cls.company = cls.company_data["company"]
        cls.company.currency_id = cls.env.ref("base.CHF")
        cls.env["account.reconcile.model"].search(
            [
                ("auto_reconcile", "=", True),
                ("rule_type", "!=", "invoice_matching"),
                ("company_id", "=", cls.company.id),
            ]
        ).active = False
        cls.customer = cls.env["res.partner"].create({"name": "Muster Kunde AG"})
        bank_account = cls.env["res.partner.bank"].create(
            {
                "acc_number": JOURNAL_IBAN,
                "partner_id": cls.company.partner_id.id,
                "company_id": cls.company.id,
            }
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "QR Bank",
                "code": "QRBK",
                "type": "bank",
                "company_id": cls.company.id,
                "bank_account_id": bank_account.id,
            }
        )
        cls.invoice_exact = cls._create_qr_invoice(QRR_EXACT, 1949.75)
        cls.invoice_partial = cls._create_qr_invoice(
            "00 00000 00000 00000 00000 10014", 1949.75
        )
        cls.invoice_scor = cls._create_qr_invoice("RF18 5390 0754 7034", 120.0)
        cls.invoice_overpaid = cls._create_qr_invoice(QRR_OVERPAID, 1949.75)
        cls.invoice_twin_a = cls._create_qr_invoice(QRR_TWO_INVOICES, 300.0)
        cls.invoice_twin_b = cls._create_qr_invoice(QRR_TWO_INVOICES, 300.0)
        cls.invoice_ref_only = cls._create_qr_invoice(QRR_ONLY_IN_REF, 750.0)
        other_company = cls.setup_other_company()["company"]
        cls.invoice_other_company = cls._create_qr_invoice(
            QRR_EXACT, 1949.75, company=other_company
        )

    @classmethod
    def _create_qr_invoice(
        cls, payment_reference, amount, company=None, move_type="out_invoice"
    ):
        company = company or cls.company
        invoice = (
            cls.env["account.move"]
            .with_company(company)
            .create(
                {
                    "move_type": move_type,
                    "company_id": company.id,
                    "partner_id": cls.customer.id,
                    "invoice_date": "2026-10-01",
                    "currency_id": company.currency_id.id,
                    "payment_reference": payment_reference,
                    "invoice_line_ids": [
                        Command.create(
                            {
                                "name": "QR service",
                                "quantity": 1,
                                "price_unit": amount,
                                "tax_ids": [Command.clear()],
                            }
                        )
                    ],
                }
            )
        )
        invoice.action_post()
        return invoice

    def _import_camt(self, file_name):
        path = file_path(f"devarc_qr_reconcile/tests/fixtures/{file_name}")
        with open(path, "rb") as camt_file:
            content = base64.b64encode(camt_file.read())
        self.env["account.statement.import"].create(
            {"statement_filename": file_name, "statement_file": content}
        ).import_file_button()
        return self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)]
        )

    def _line_by_reference(self, lines, reference):
        return lines.filtered(lambda line: line.devarc_qr_reference == reference)

    def _proposed_counterpart_ids(self, line):
        return {
            aml_id
            for item in line.reconcile_data_info.get("data", [])
            if item.get("kind") == "other"
            for aml_id in item.get("counterpart_line_ids", [])
        }

    def _receivable(self, invoice):
        return invoice.line_ids.filtered(
            lambda aml: aml.account_id.account_type == "asset_receivable"
        )

    def test_camt054_bulk_entry_mark_mode(self):
        lines = self._import_camt("camt054_qrr_scor.xml")
        self.assertEqual(len(lines), 6)
        self.assertFalse(any(lines.mapped("is_reconciled")))

        exact = self._line_by_reference(lines, QRR_EXACT)
        self.assertEqual(exact.devarc_qr_move_id, self.invoice_exact)
        self.assertIn(
            self._receivable(self.invoice_exact).id,
            self._proposed_counterpart_ids(exact),
        )
        self.assertEqual(self.invoice_exact.payment_state, "not_paid")

        spaced = self._line_by_reference(lines, QRR_PARTIAL)
        self.assertEqual(spaced.devarc_qr_move_id, self.invoice_partial)

        scor = self._line_by_reference(lines, SCOR_EXACT)
        self.assertEqual(scor.devarc_qr_move_id, self.invoice_scor)
        self.assertFalse(scor.is_reconciled)

        unknown = self._line_by_reference(lines, QRR_UNKNOWN)
        self.assertEqual(len(unknown), 1)
        self.assertFalse(unknown.devarc_qr_move_id)
        self.assertFalse(self._proposed_counterpart_ids(unknown))

        twins = self._line_by_reference(lines, QRR_TWO_INVOICES)
        self.assertEqual(len(twins), 1)
        self.assertFalse(twins.devarc_qr_move_id)
        self.assertFalse(self._proposed_counterpart_ids(twins))
        self.assertFalse(twins.is_reconciled)

    def test_camt054_bulk_entry_auto_mode(self):
        self.journal.devarc_qr_reconcile_mode = "auto"
        lines = self._import_camt("camt054_qrr_scor.xml")
        self.assertEqual(len(lines), 6)

        exact = self._line_by_reference(lines, QRR_EXACT)
        self.assertTrue(exact.is_reconciled)
        self.assertEqual(self.invoice_exact.payment_state, "paid")
        self.assertEqual(self.invoice_exact.amount_residual, 0.0)
        self.assertEqual(self.invoice_other_company.amount_residual, 1949.75)

        scor = self._line_by_reference(lines, SCOR_EXACT)
        self.assertTrue(scor.is_reconciled)
        self.assertEqual(self.invoice_scor.payment_state, "paid")

        partial = self._line_by_reference(lines, QRR_PARTIAL)
        self.assertFalse(partial.is_reconciled)
        self.assertEqual(partial.devarc_qr_move_id, self.invoice_partial)
        self.assertIn(
            self._receivable(self.invoice_partial).id,
            self._proposed_counterpart_ids(partial),
        )
        self.assertEqual(self.invoice_partial.amount_residual, 1949.75)

        overpaid = self._line_by_reference(lines, QRR_OVERPAID)
        self.assertFalse(overpaid.is_reconciled)
        self.assertEqual(overpaid.devarc_qr_move_id, self.invoice_overpaid)
        self.assertEqual(self.invoice_overpaid.amount_residual, 1949.75)

        twins = self._line_by_reference(lines, QRR_TWO_INVOICES)
        self.assertFalse(twins.is_reconciled)
        self.assertFalse(twins.devarc_qr_move_id)
        self.assertEqual(self.invoice_twin_a.amount_residual, 300.0)
        self.assertEqual(self.invoice_twin_b.amount_residual, 300.0)

        unknown = self._line_by_reference(lines, QRR_UNKNOWN)
        self.assertFalse(unknown.is_reconciled)

    def test_camt053_reference_only_in_ref(self):
        self.journal.devarc_qr_reconcile_mode = "auto"
        lines = self._import_camt("camt053_qrr.xml")
        self.assertEqual(len(lines), 2)
        qr_line = self._line_by_reference(lines, QRR_ONLY_IN_REF)
        self.assertEqual(qr_line.ref, QRR_ONLY_IN_REF)
        self.assertNotIn(QRR_ONLY_IN_REF, qr_line.payment_ref or "")
        self.assertEqual(qr_line.devarc_qr_move_id, self.invoice_ref_only)
        self.assertTrue(qr_line.is_reconciled)
        self.assertEqual(self.invoice_ref_only.payment_state, "paid")
        other_line = lines - qr_line
        self.assertFalse(other_line.devarc_qr_reference)
        self.assertFalse(other_line.is_reconciled)

    def test_reference_found_in_narration_with_spaces(self):
        line = self.env["account.bank.statement.line"].create(
            {
                "journal_id": self.journal.id,
                "date": "2026-10-17",
                "amount": 1949.75,
                "payment_ref": "Zahlung Rechnung",
                "narration": "Structured Reference: 21 00000 00003 13947 14300 09017",
            }
        )
        self.assertEqual(line.devarc_qr_reference, QRR_EXACT)
        self.assertEqual(line.devarc_qr_move_id, self.invoice_exact)
        self.assertFalse(line.is_reconciled)

    def test_invalid_check_digit_is_ignored(self):
        line = self.env["account.bank.statement.line"].create(
            {
                "journal_id": self.journal.id,
                "date": "2026-10-17",
                "amount": 1949.75,
                "payment_ref": "210000000003139471430009018",
            }
        )
        self.assertFalse(line.devarc_qr_reference)
        self.assertFalse(line.devarc_qr_move_id)

    def test_other_company_invoice_is_not_matched(self):
        self.invoice_exact.button_draft()
        self.invoice_exact.button_cancel()
        line = self.env["account.bank.statement.line"].create(
            {
                "journal_id": self.journal.id,
                "date": "2026-10-17",
                "amount": 1949.75,
                "payment_ref": QRR_EXACT,
            }
        )
        self.assertEqual(line.devarc_qr_reference, QRR_EXACT)
        self.assertFalse(line.devarc_qr_move_id)

    def test_vendor_bill_qrr_debit_is_left_to_oca_matching(self):
        bill = self._create_qr_invoice(QRR_VENDOR_BILL, 500.0, move_type="in_invoice")
        lines = self._import_camt("camt054_qrr_debit.xml")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines.amount, -500.0)
        self.assertEqual(lines.payment_ref, QRR_VENDOR_BILL)
        self.assertEqual(lines.devarc_qr_reference, QRR_VENDOR_BILL)
        self.assertFalse(lines.devarc_qr_move_id)
        self.assertFalse(lines._devarc_qr_owns_invoice_matching())
        self.assertFalse(lines._devarc_qr_proposal())
        self.assertEqual(bill.move_type, "in_invoice")

    def test_refund_with_invoice_qrr_is_not_marked(self):
        refund = self.env["account.bank.statement.line"].create(
            {
                "journal_id": self.journal.id,
                "date": "2026-10-17",
                "amount": -1949.75,
                "payment_ref": QRR_EXACT,
            }
        )
        self.assertFalse(refund.devarc_qr_move_id)
        self.assertFalse(self._proposed_counterpart_ids(refund))
        self.assertFalse(refund.is_reconciled)
        self.assertEqual(self.invoice_exact.amount_residual, 1949.75)

    def _qr_invoice_found_domain(self):
        view = self.env.ref("account_statement_base.account_bank_statement_line_search")
        arch = self.env["account.bank.statement.line"].get_view(
            view_id=view.id, view_type="search"
        )["arch"]
        node = etree.fromstring(arch).xpath("//filter[@name='devarc_qr_invoice_found']")
        return safe_eval(node[0].get("domain"))

    def test_invoice_found_filter_hides_closed_invoice(self):
        line = self.env["account.bank.statement.line"].create(
            {
                "journal_id": self.journal.id,
                "date": "2026-10-17",
                "amount": 1949.75,
                "payment_ref": QRR_EXACT,
            }
        )
        self.assertEqual(line.devarc_qr_move_id, self.invoice_exact)
        statement_lines = self.env["account.bank.statement.line"]
        domain = self._qr_invoice_found_domain()
        self.assertIn(line, statement_lines.search(domain))
        self.invoice_exact.button_draft()
        self.invoice_exact.button_cancel()
        self.assertNotIn(line, statement_lines.search(domain))

    def test_readonly_user_opens_unmarked_line(self):
        line = (
            self.env["account.bank.statement.line"]
            .with_context(_test_account_reconcile_oca=False)
            .create(
                {
                    "journal_id": self.journal.id,
                    "date": "2026-10-17",
                    "amount": 1949.75,
                    "payment_ref": QRR_EXACT,
                }
            )
        )
        self.assertFalse(line.devarc_qr_move_id)
        auditor = new_test_user(
            self.env,
            login="qr_auditor",
            groups="base.group_user,account.group_account_readonly",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        line.invalidate_recordset()
        try:
            data = line.with_user(auditor).reconcile_data_info
        except AccessError as error:
            self.fail(f"Read-only user cannot open the line: {error}")
        self.assertTrue(data)
        self.assertEqual(line.devarc_qr_move_id, self.invoice_exact)

    def test_reconcile_after_import_opens_journal_of_imported_statement(self):
        path = file_path("devarc_qr_reconcile/tests/fixtures/camt053_qrr.xml")
        with open(path, "rb") as camt_file:
            content = base64.b64encode(camt_file.read())
        action = (
            self.env["account.statement.import"]
            .create({"statement_filename": "camt053_qrr.xml", "statement_file": content})
            .import_file_and_reconcile_button()
        )
        if action["type"] == "ir.actions.client":
            action = action["params"]["next"]
        self.assertEqual(action["context"]["active_id"], self.journal.id)
        self.assertEqual(action["context"]["default_journal_id"], self.journal.id)
        domain = safe_eval(action["domain"], {"active_id": action["context"]["active_id"]})
        self.assertTrue(self.env["account.bank.statement.line"].search(domain))
