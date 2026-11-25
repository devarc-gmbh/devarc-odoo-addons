# DevArc Odoo Addons

Odoo 18.0 Community Edition addons by [DevArc GmbH](https://devarc.ch/en), Pratteln, Switzerland.

| Addon | Version | Odoo | License | What it does |
|---|---|---|---|---|
| [devarc_qr_invoice](devarc_qr_invoice) | 18.0.2.1.1 | 18.0 CE | LGPL-3 | Import of vendor Swiss QR-bills from scanner text, PDF or photo, plus QR-bill checks for customer invoices on top of l10n_ch |
| [devarc_qr_reconcile](devarc_qr_reconcile) | 18.0.1.1.2 | 18.0 CE | AGPL-3 | Matches QRR/SCOR payments from camt.053/054 to customer invoices on top of OCA reconciliation |
| [devarc_portal_login_as](devarc_portal_login_as) | 18.0.1.0.0 | 18.0 CE | LGPL-3 | Authorised staff open the customer portal exactly as one portal user sees it, for 30 minutes, with a required reason, password re-check, audit log and customer notification |
| [ai_assistant_access](ai_assistant_access) | 18.0.1.1.0 | 18.0 CE | LGPL-3 | Read-only, optionally editable, access to a user's own Odoo data for any MCP capable AI agent (Claude Code, Codex, Copilot, Gemini CLI, Cursor, ...), with a paired key, hard blacklist, no delete call, rate limits and an audit trail |
| [fetchmail_imap_filter](fetchmail_imap_filter) | 18.0.1.0.0 | 18.0 CE | LGPL-3 | Fetches only the IMAP mails matching a folder and a search criteria that must start with UNSEEN, instead of every unread mail in the mailbox, for plain IMAP and Gmail |

The same addons are available for Odoo 19.0 on the `19.0` branch. All of them except `devarc_qr_reconcile` are also available for Odoo 20.0 on the `20.0` branch.

## Installation

1. Clone the branch that matches your Odoo version:

   ```
   git clone -b 18.0 https://github.com/devarc-gmbh/devarc-odoo-addons.git
   ```
2. Install the Python packages used by `devarc_qr_invoice`:

   ```
   pip install zxing-cpp pypdfium2
   ```
3. For `devarc_qr_reconcile` clone these OCA repositories on branch 18.0 and add them to `addons_path`:
   - [OCA/bank-statement-import](https://github.com/OCA/bank-statement-import/tree/18.0)
     (`account_statement_import_camt`, `account_statement_import_camt54`, `account_statement_import_file_reconcile_oca`)
   - [OCA/account-reconcile](https://github.com/OCA/account-reconcile/tree/18.0) (`account_reconcile_oca`)
4. Add the folder of this repository to `addons_path` in your Odoo configuration.
5. Update the Apps list and install the addons you need: **DevArc QR Invoice**, **DevArc QR Reconcile**, **Portal Login As**, **AI Assistant Access**, **Fetchmail IMAP Filter**.
6. For `ai_assistant_access`, each user pairs an AI agent in My Preferences > AI Assistant. Supported agents and their config files are listed in [ai_assistant_access/docs/clients.md](ai_assistant_access/docs/clients.md).

## Tests

```
odoo-bin -d <db> -i devarc_qr_invoice,devarc_qr_reconcile,devarc_portal_login_as,ai_assistant_access,fetchmail_imap_filter --test-enable --test-tags=/devarc_qr_invoice,/devarc_qr_reconcile,/devarc_portal_login_as,/ai_assistant_access,/fetchmail_imap_filter --stop-after-init
```

## License

- `devarc_qr_invoice`: LGPL-3, see [LICENSE](LICENSE).
- `devarc_portal_login_as`: LGPL-3, see [LICENSE](LICENSE).
- `ai_assistant_access`: LGPL-3, see [LICENSE](LICENSE).
- `fetchmail_imap_filter`: LGPL-3, see [LICENSE](LICENSE).
- `devarc_qr_reconcile`: AGPL-3, because it depends on AGPL-3 OCA modules, see [devarc_qr_reconcile/LICENSE](devarc_qr_reconcile/LICENSE).

## Contact and links

Open an issue in this repository or write to info@devarc.ch.

- Web: https://devarc.ch/en
- Blog: https://devarc.ch/blog
- YouTube (videos of these addons): https://www.youtube.com/@DevArc
- LinkedIn: https://www.linkedin.com/company/devarc-gmbh/
