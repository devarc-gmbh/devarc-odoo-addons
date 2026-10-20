from odoo.fields import Command

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

SIX_QR_IBAN = "CH4431999123000889012"
SIX_IBAN = "CH5800791123000889012"
SIX_QR_REFERENCE = "210000000003139471430009017"
SIX_SCOR_REFERENCE = "RF18539007547034"
SIX_CREDITOR_NAME = "Max Muster & Söhne (sample company)"
SIX_DEBTOR_NAME = "Simon Muster"
TEST_MESSAGE = "TEST - NEPLATIT Order from 15.10.2020"


class SwissQrBillCommon(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country("ch")
    def setUpClass(cls):
        super().setUpClass()
        cls.chf = cls.env.ref("base.CHF")
        cls.eur = cls.env.ref("base.EUR")
        cls.eur.active = True
        switzerland = cls.env.ref("base.ch")
        cls.env.company.partner_id.write({
            "name": SIX_CREDITOR_NAME,
            "street": "Musterstrasse 123",
            "zip": "8000",
            "city": "Seldwyla",
            "country_id": switzerland.id,
        })
        cls.debtor = cls.env["res.partner"].create({
            "name": SIX_DEBTOR_NAME,
            "street": "Musterstrasse 1",
            "zip": "8000",
            "city": "Seldwyla",
            "country_id": switzerland.id,
        })
        cls.qr_iban_account = cls._create_bank_account(SIX_QR_IBAN)
        cls.iban_account = cls._create_bank_account(SIX_IBAN)

    @classmethod
    def _create_bank_account(cls, acc_number):
        return cls.env["res.partner.bank"].create({
            "acc_number": acc_number,
            "partner_id": cls.env.company.partner_id.id,
            "allow_out_payment": True,
        })

    @classmethod
    def _create_qr_invoice(cls, bank_account, amount=1949.75, currency=None, partner=None, payment_reference=False, post=True):
        invoice = cls.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": (partner or cls.debtor).id,
            "partner_bank_id": bank_account.id,
            "currency_id": (currency or cls.chf).id,
            "invoice_date": "2026-10-18",
            "ref": TEST_MESSAGE,
            "payment_reference": payment_reference,
            "invoice_line_ids": [Command.create({
                "name": "TEST - NEPLATIT demo service",
                "quantity": 1,
                "price_unit": amount,
                "tax_ids": [Command.clear()],
            })],
        })
        if post:
            invoice.action_post()
        return invoice
