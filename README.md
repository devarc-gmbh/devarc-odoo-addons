# DevArc Odoo Addons

Odoo 20.0 Community Edition addons by [DevArc GmbH](https://devarc.ch/en), Pratteln, Switzerland.

| Addon | Version | Odoo | License | What it does |
|---|---|---|---|---|
| [devarc_qr_invoice](devarc_qr_invoice) | 20.0.2.1.1 | 20.0 CE | LGPL-3 | Import of vendor Swiss QR-bills from scanner text, PDF or photo, plus QR-bill checks for customer invoices on top of l10n_ch |
| [devarc_portal_login_as](devarc_portal_login_as) | 20.0.1.0.0 | 20.0 CE | LGPL-3 | Authorised staff open the customer portal exactly as one portal user sees it, for 30 minutes, with a required reason, password re-check, audit log and customer notification |
| [ai_assistant_access](ai_assistant_access) | 20.0.1.1.0 | 20.0 CE | LGPL-3 | Read-only, optionally editable, access to a user's own Odoo data for any MCP capable AI agent (Claude Code, Codex, Copilot, Gemini CLI, Cursor, ...), with a paired key, hard blacklist, no delete call, rate limits and an audit trail |
| [fetchmail_imap_filter](fetchmail_imap_filter) | 20.0.1.0.0 | 20.0 CE | LGPL-3 | Fetches only the IMAP mails matching a folder and a search criteria that must start with UNSEEN, instead of every unread mail in the mailbox, for plain IMAP and Gmail |

All addons except `devarc_qr_reconcile` are also available for Odoo 18.0 and 19.0 on the `18.0` and `19.0` branches. `devarc_qr_reconcile` will come to 20.0 once OCA ports `account_reconcile_oca` to Odoo 20.0; until then it is available on the 18.0 and 19.0 branches.

## Installation

1. Clone the branch that matches your Odoo version:

   ```
   git clone -b 20.0 https://github.com/devarc-gmbh/devarc-odoo-addons.git
   ```
2. Install the Python packages used by `devarc_qr_invoice`:

   ```
   pip install zxing-cpp pypdfium2
   ```
3. Add the folder of this repository to `addons_path` in your Odoo configuration.
4. Update the Apps list and install the addons you need: **DevArc QR Invoice**, **Portal Login As**, **AI Assistant Access**, **Fetchmail IMAP Filter**.
5. For `ai_assistant_access`, each user pairs an AI agent in My Preferences > AI Assistant. Supported agents and their config files are listed in [ai_assistant_access/docs/clients.md](ai_assistant_access/docs/clients.md).

## Tests

```
odoo-bin -d <db> -i devarc_qr_invoice,devarc_portal_login_as,ai_assistant_access,fetchmail_imap_filter --test-enable --test-tags=/devarc_qr_invoice,/devarc_portal_login_as,/ai_assistant_access,/fetchmail_imap_filter --stop-after-init
```

## License

- `devarc_qr_invoice`: LGPL-3, see [LICENSE](LICENSE).
- `devarc_portal_login_as`: LGPL-3, see [LICENSE](LICENSE).
- `ai_assistant_access`: LGPL-3, see [LICENSE](LICENSE).
- `fetchmail_imap_filter`: LGPL-3, see [LICENSE](LICENSE).

## Contact and links

Open an issue in this repository or write to info@devarc.ch.

- Web: https://devarc.ch/en
- Blog: https://devarc.ch/blog
- YouTube (videos of these addons): https://www.youtube.com/@DevArc
- LinkedIn: https://www.linkedin.com/company/devarc-gmbh/
