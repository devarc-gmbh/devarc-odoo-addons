import re

from odoo import api, fields, models
from odoo.tools import html2plaintext
from odoo.tools.misc import mod10r

from odoo.addons.account.tools.structured_reference import (
    is_valid_structured_reference_iso,
)

QRR_LENGTH = 27
SCOR_MAX_LENGTH = 25
MAX_REFERENCE_TOKENS = 9


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    devarc_qr_reference = fields.Char(
        string="QR Reference",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="QRR or SCOR reference found in the reference, label or notes of the line.",
    )
    devarc_qr_move_id = fields.Many2one(
        comodel_name="account.move",
        string="QR Invoice",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="The only open customer invoice whose payment reference equals the QR "
        "reference of this line.",
    )

    def _do_auto_reconcile(self, models):
        if not self.is_reconciled:
            self._devarc_qr_mark()
        if self._devarc_qr_owns_invoice_matching():
            models = models.filtered(lambda model: model.rule_type != "invoice_matching")
        result = super()._do_auto_reconcile(models)
        if self.devarc_qr_move_id and self.amount > 0 and not self.is_reconciled:
            self._devarc_qr_propose()
        return result

    @api.depends("reconcile_data", "is_reconciled")
    def _compute_reconcile_data_info(self):
        if self.env.su or self.has_access("write"):
            return super()._compute_reconcile_data_info()
        for line in self:
            if line.reconcile_data and not line.is_reconciled:
                line.reconcile_data_info = line.reconcile_data
            else:
                line.reconcile_data_info = line._default_reconcile_data(
                    from_unreconcile=line.is_reconciled
                )
        return None

    def _default_reconcile_data(self, from_unreconcile=False):
        if from_unreconcile or self.is_reconciled:
            return super()._default_reconcile_data(from_unreconcile=from_unreconcile)
        self._devarc_qr_mark()
        if not self._devarc_qr_owns_invoice_matching():
            return super()._default_reconcile_data(from_unreconcile=from_unreconcile)
        data = super(
            AccountBankStatementLine,
            self.with_context(devarc_qr_skip_invoice_matching=True),
        )._default_reconcile_data(from_unreconcile=from_unreconcile)
        if self._devarc_qr_proposal_present(data) or not self.devarc_qr_move_id:
            return data
        return self._devarc_qr_proposal() or data

    def _devarc_qr_owns_invoice_matching(self):
        self.ensure_one()
        return bool(self.devarc_qr_reference) and self.amount > 0

    def _devarc_qr_mark(self):
        for line in self:
            reference, move = line._devarc_qr_find_invoice()
            values = {"devarc_qr_reference": reference, "devarc_qr_move_id": move.id}
            if line.devarc_qr_reference != reference or line.devarc_qr_move_id != move:
                line.sudo().write(values)

    def _devarc_qr_find_invoice(self):
        self.ensure_one()
        no_move = self.env["account.move"]
        references = self._devarc_qr_extract_references()
        if not references:
            return False, no_move
        matches = {
            reference: moves
            for reference in references
            if (moves := self._devarc_qr_invoices_by_reference(reference))
        }
        if len(matches) != 1:
            return (next(iter(matches)) if matches else references[0]), no_move
        reference, moves = next(iter(matches.items()))
        if (
            self.amount <= 0
            or len(moves) != 1
            or moves.payment_state not in ("not_paid", "partial")
        ):
            return reference, no_move
        return reference, moves

    def _devarc_qr_invoices_by_reference(self, reference):
        candidates = self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_reference", "=ilike", "%".join(reference)),
            ]
        )
        return candidates.filtered(
            lambda move: self._devarc_qr_normalize(move.payment_reference) == reference
        )

    def _devarc_qr_extract_references(self):
        self.ensure_one()
        texts = [self.ref, self.payment_ref, html2plaintext(self.narration or "")]
        return list(dict.fromkeys(
            reference
            for text in texts
            for reference in self._devarc_qr_references_in_text(text or "")
        ))

    def _devarc_qr_references_in_text(self, text):
        tokens = re.findall(r"[0-9A-Z]+", text.upper())
        for start, token in enumerate(tokens):
            if token.isdigit():
                reference = self._devarc_qr_window(tokens, start, self._devarc_qr_is_qrr)
            elif token.startswith("RF"):
                reference = self._devarc_qr_window(
                    tokens, start, is_valid_structured_reference_iso
                )
            else:
                reference = False
            if reference:
                yield reference

    def _devarc_qr_window(self, tokens, start, is_valid):
        best = False
        candidate = ""
        for token in tokens[start : start + MAX_REFERENCE_TOKENS]:
            candidate += token
            if len(candidate) > QRR_LENGTH or (
                len(candidate) > SCOR_MAX_LENGTH and not candidate.isdigit()
            ):
                break
            if is_valid(candidate):
                best = candidate
        return best

    def _devarc_qr_is_qrr(self, candidate):
        return (
            len(candidate) == QRR_LENGTH
            and candidate.isdigit()
            and mod10r(candidate[:-1]) == candidate
        )

    def _devarc_qr_normalize(self, reference):
        return re.sub(r"[^0-9A-Z]", "", (reference or "").upper())

    def _devarc_qr_counterpart_line(self):
        self.ensure_one()
        return self.devarc_qr_move_id.line_ids.filtered(
            lambda aml: aml.account_id.account_type == "asset_receivable"
            and not aml.reconciled
        )

    def _devarc_qr_proposal_present(self, data):
        return any(item.get("kind") == "other" for item in (data or {}).get("data", []))

    def _devarc_qr_propose(self):
        self.ensure_one()
        data = self._default_reconcile_data()
        counterpart = self._devarc_qr_counterpart_line()
        proposed_ids = {
            aml_id
            for item in data.get("data", [])
            if item.get("kind") == "other"
            for aml_id in item.get("counterpart_line_ids", [])
        }
        if (
            len(counterpart) == 1
            and counterpart.id in proposed_ids
            and self._devarc_qr_is_exact_match()
            and data.get("can_reconcile")
        ):
            getattr(self, f"_reconcile_bank_line_{self.journal_id.reconcile_mode}")(
                self._prepare_reconcile_line_data(data["data"])
            )

    def _devarc_qr_proposal(self):
        self.ensure_one()
        counterpart = self._devarc_qr_counterpart_line()
        if len(counterpart) != 1:
            return False
        liquidity_lines, _suspense_lines, _other_lines = self._seek_for_lines()
        data = []
        reconcile_auxiliary_id = 1
        for line in liquidity_lines:
            reconcile_auxiliary_id, lines = self._get_reconcile_line(
                line,
                "liquidity",
                reconcile_auxiliary_id=reconcile_auxiliary_id,
                move=True,
            )
            data += lines
        reconcile_auxiliary_id, counterpart_data = self._get_reconcile_line(
            counterpart,
            "other",
            is_counterpart=True,
            max_amount=self.amount_currency or self.amount,
            move=True,
        )
        data += counterpart_data
        return self._recompute_suspense_line(
            data, reconcile_auxiliary_id, self.manual_reference
        )

    def _devarc_qr_is_exact_match(self):
        self.ensure_one()
        move = self.devarc_qr_move_id
        return (
            self.journal_id.devarc_qr_reconcile_mode == "auto"
            and not self.foreign_currency_id
            and self.currency_id == move.currency_id
            and self.amount > 0
            and self.currency_id.compare_amounts(self.amount, move.amount_residual) == 0
            and (
                not self.partner_id
                or self.partner_id.commercial_partner_id == move.commercial_partner_id
            )
        )
