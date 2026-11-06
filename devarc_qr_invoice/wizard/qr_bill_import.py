import base64
import binascii

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tools import plaintext2html

from odoo.addons.devarc_qr_invoice.tools.qr_bill_decoder import decode
from odoo.addons.devarc_qr_invoice.tools.qr_bill_payload import QrBillPayloadError, parse

MOVE_REF_MAX_LENGTH = 64
DEFAULT_ATTACHMENT_NAME = "qr-bill"


class DevarcQrBillImport(models.TransientModel):
    _name = "devarc.qr.bill.import"
    _description = "Import Swiss QR-bill"

    payload_text = fields.Text(
        string="QR-bill Text",
        help="Text of the Swiss QR-bill as returned by a QR code reader (SPC 0200 payload).",
    )
    attachment = fields.Binary(
        string="QR-bill Document",
        help="PDF or image containing the Swiss QR-bill. It is attached to the vendor bill.",
    )
    attachment_name = fields.Char(
        string="File Name",
        help="Name of the uploaded QR-bill document.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        help="Company in which the vendor bill, the vendor and its bank account are looked up or created.",
    )
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Draft Vendor Bill",
        domain="[('move_type', '=', 'in_invoice'), ('state', '=', 'draft')]",
        help="Draft vendor bill completed from the QR-bill. A new vendor bill is created when empty.",
    )

    @api.onchange("move_id")
    def _onchange_move_id(self):
        if self.move_id:
            self.company_id = self.move_id.company_id

    def action_import(self):
        self.ensure_one()
        if self.move_id and (self.move_id.move_type != "in_invoice" or self.move_id.state != "draft"):
            raise UserError(self.env._("Only a draft vendor bill can be completed from a QR-bill."))
        company = self.move_id.company_id or self.company_id
        payload = self._read_payload()
        currency = self._find_currency(payload)
        self._check_not_own_bank_account(payload)
        partner, bank_account = self._find_or_create_vendor(payload, company)
        bill = self._fill_bill(payload, company, partner, bank_account, currency)
        if self.attachment:
            self._attach_document(bill)
        return self._import_result_action(bill)

    def _read_payload(self):
        try:
            if self.attachment:
                payload_text = decode(self._attachment_bytes())
            elif self.payload_text and self.payload_text.strip():
                payload_text = self.payload_text
            else:
                raise UserError(self.env._("Scan the QR-bill text or upload a PDF or image of the QR-bill."))
            return parse(payload_text)
        except QrBillPayloadError as error:
            raise UserError(self.env._("The QR-bill could not be read: %(error)s", error=str(error))) from error

    def _attachment_bytes(self):
        try:
            return base64.b64decode(self.attachment, validate=True)
        except (binascii.Error, ValueError) as error:
            raise QrBillPayloadError("The uploaded document could not be read.") from error

    def _find_currency(self, payload):
        currency = self.env["res.currency"].search([("name", "=", payload.currency)], limit=1)
        if not currency:
            raise UserError(self.env._("Activate the currency %(currency)s to import this QR-bill.", currency=payload.currency))
        return currency

    def _check_not_own_bank_account(self, payload):
        own_partners = self.env.companies.partner_id.commercial_partner_id
        if self.env["res.partner.bank"].search_count([
            ("sanitized_acc_number", "=", payload.iban),
            ("partner_id", "child_of", own_partners.ids),
        ], limit=1):
            raise UserError(self.env._(
                "The QR-bill is payable to your own company (IBAN %(iban)s). It is not a vendor bill.",
                iban=payload.iban,
            ))

    def _find_or_create_vendor(self, payload, company):
        bank_account = self.env["res.partner.bank"].search([
            ("sanitized_acc_number", "=", payload.iban),
            *self.env["res.partner.bank"]._check_company_domain(company),
        ], limit=1)
        if bank_account:
            return bank_account.partner_id, bank_account
        partner = self._find_vendor_by_address(payload.creditor, company) or self._create_vendor(payload.creditor)
        bank_account = self.env["res.partner.bank"]._find_or_create_bank_account(
            account_number=payload.iban,
            partner=partner,
            company=company,
        )
        return partner, bank_account

    def _find_vendor_by_address(self, address, company):
        zip_code, _city = self._address_zip_city(address)
        if not zip_code:
            return self.env["res.partner"]
        partner = self.env["res.partner"].search([
            ("name", "=ilike", address.name),
            ("zip", "=", zip_code),
            ("id", "not in", self.env["res.company"]._get_company_partner_ids()),
            *self.env["res.partner"]._check_company_domain(company),
        ], limit=1)
        return partner.commercial_partner_id

    def _create_vendor(self, address):
        zip_code, city = self._address_zip_city(address)
        if address.address_type == "S":
            street = " ".join(part for part in (address.street_or_line_1, address.building_or_line_2) if part)
        else:
            street = address.street_or_line_1
        country = self.env["res.country"].search([("code", "=", address.country_code)], limit=1)
        return self.env["res.partner"].create({
            "name": address.name,
            "is_company": True,
            "street": street or False,
            "zip": zip_code or False,
            "city": city or False,
            "country_id": country.id,
            "supplier_rank": 1,
        })

    @api.model
    def _address_zip_city(self, address):
        if address.address_type == "S":
            return address.zip, address.city
        zip_code, _separator, city = address.building_or_line_2.partition(" ")
        return zip_code, city

    def _fill_bill(self, payload, company, partner, bank_account, currency):
        bill_vals = {
            "partner_id": partner.id,
            "partner_bank_id": bank_account.id,
            "currency_id": currency.id,
            "payment_reference": payload.reference if payload.reference_type in ("QRR", "SCOR") else False,
            "narration": self._bill_narration(payload),
        }
        bill = self.move_id
        if payload.invoice_number:
            bill_vals["ref"] = payload.invoice_number[:MOVE_REF_MAX_LENGTH]
        if payload.invoice_date and not bill.invoice_date:
            bill_vals["invoice_date"] = payload.invoice_date
        if payload.amount is not None and not bill.invoice_line_ids:
            bill_vals["invoice_line_ids"] = [Command.create({
                "name": payload.unstructured_message or self._invoice_line_name(payload),
                "quantity": 1,
                "price_unit": float(payload.amount),
                "tax_ids": [Command.clear()],
            })]
        if bill:
            bill.write(bill_vals)
            return bill
        return self.env["account.move"].with_company(company).create({
            "move_type": "in_invoice",
            "company_id": company.id,
            **bill_vals,
        })

    def _invoice_line_name(self, payload):
        if payload.invoice_number:
            return self.env._("Invoice %(number)s", number=payload.invoice_number)
        return payload.reference or payload.creditor.name

    @api.model
    def _bill_narration(self, payload):
        text = "\n".join(part for part in (payload.unstructured_message, payload.billing_info) if part)
        return plaintext2html(text) if text else False

    def _attach_document(self, bill):
        attachment = self.env["ir.attachment"].create({
            "name": self.attachment_name or DEFAULT_ATTACHMENT_NAME,
            "datas": self.attachment,
            "res_model": "account.move",
            "res_id": bill.id,
        })
        bill.message_post(
            body=self.env._("Swiss QR-bill imported."),
            attachment_ids=attachment.ids,
        )

    def _find_duplicate_bills(self, bill):
        reference_domains = []
        if bill.payment_reference:
            reference_domains.append([
                "&", "&",
                ("payment_reference", "=", bill.payment_reference),
                ("currency_id", "=", bill.currency_id.id),
                ("amount_total", "=", bill.amount_total),
            ])
        if bill.ref:
            reference_domains.append([("ref", "=", bill.ref)])
        if not reference_domains:
            return self.env["account.move"]
        reference_domain = ["|"] * (len(reference_domains) - 1) + [term for domain in reference_domains for term in domain]
        return self.env["account.move"].search([
            ("id", "!=", bill.id),
            ("move_type", "=", "in_invoice"),
            ("company_id", "=", bill.company_id.id),
            ("commercial_partner_id", "=", bill.commercial_partner_id.id),
            ("state", "!=", "cancel"),
            *reference_domain,
        ])

    def _import_result_action(self, bill):
        bill_action = self.env["ir.actions.act_window"]._for_xml_id("account.action_move_in_invoice_type")
        bill_action.update({
            "res_id": bill.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        })
        duplicates = self._find_duplicate_bills(bill)
        if not duplicates:
            return bill_action
        reference = bill.payment_reference or bill.ref
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning",
                "sticky": True,
                "title": self.env._("Possible duplicate vendor bill: reference %(reference)s", reference=reference),
                "message": self.env._("This reference is already used on:") + " %s" * len(duplicates),
                "links": [
                    {"label": duplicate.display_name, "url": f"/odoo/account.move/{duplicate.id}"}
                    for duplicate in duplicates
                ],
                "next": bill_action,
            },
        }
