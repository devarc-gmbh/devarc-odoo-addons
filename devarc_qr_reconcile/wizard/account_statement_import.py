from odoo import fields, models


class AccountStatementImport(models.TransientModel):
    _inherit = "account.statement.import"

    devarc_imported_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Imported Journal",
        readonly=True,
        help="Journal of the bank statements created by this import.",
    )

    def _import_file(self):
        result = super()._import_file()
        statements = self.env["account.bank.statement"].browse(result["statement_ids"])
        self.devarc_imported_journal_id = statements.journal_id[:1]
        return result

    def import_file_and_reconcile_button(self):
        action = super().import_file_and_reconcile_button()
        reconcile_action = action["params"]["next"] if action["type"] == "ir.actions.client" else action
        journal_id = reconcile_action["context"]["default_journal_id"] or self.devarc_imported_journal_id.id
        reconcile_action["context"].update(default_journal_id=journal_id, active_id=journal_id)
        return action
