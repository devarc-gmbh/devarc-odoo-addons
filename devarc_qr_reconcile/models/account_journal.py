from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    devarc_qr_reconcile_mode = fields.Selection(
        selection=[
            ("mark", "Mark matched invoice"),
            ("auto", "Reconcile exact matches"),
        ],
        string="QR Reference Matching",
        default="mark",
        required=True,
        help="Mark: statement lines carrying a QRR or SCOR reference of exactly one open "
        "customer invoice get that invoice linked and proposed, nothing is reconciled. "
        "Reconcile exact matches: additionally reconcile the line when its amount equals "
        "the invoice residual; partial payments and overpayments are only linked. A reference "
        "without exactly one open invoice links no invoice, the line is listed under the filter "
        "QR Reference Without Invoice.",
    )
