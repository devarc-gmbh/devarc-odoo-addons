from odoo import models


class AccountReconcileModel(models.Model):
    _inherit = "account.reconcile.model"

    def _apply_rules(self, st_line, partner):
        rules = self
        if self.env.context.get("devarc_qr_skip_invoice_matching"):
            rules = self.filtered(lambda model: model.rule_type != "invoice_matching")
        return super(AccountReconcileModel, rules)._apply_rules(st_line, partner)
