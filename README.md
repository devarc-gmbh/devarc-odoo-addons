# DevArc Odoo Addons

Odoo 18.0 Community Edition addons by [DevArc GmbH](https://devarc.ch/en), Pratteln, Switzerland.

| Addon | Version | Odoo | License | What it does |
|---|---|---|---|---|
| [devarc_qr_invoice](devarc_qr_invoice) | 18.0.2.1.0 | 18.0 CE | LGPL-3 | Import of vendor Swiss QR-bills from scanner text, PDF or photo, plus QR-bill checks for customer invoices on top of l10n_ch |
| [devarc_qr_reconcile](devarc_qr_reconcile) | 18.0.1.1.1 | 18.0 CE | AGPL-3 | Matches QRR/SCOR payments from camt.053/054 to customer invoices on top of OCA reconciliation |

The same addons are available for Odoo 19.0 on the `19.0` branch; `devarc_qr_invoice` is also available for Odoo 20.0 on the `20.0` branch.

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

   On Odoo 18 the OCA rule Perfect Match requires a partner, so a debit for a vendor bill without a partner is left to manual reconciliation.
4. Add the folder of this repository to `addons_path` in your Odoo configuration.
5. Update the Apps list and install **DevArc QR Invoice** and **DevArc QR Reconcile**.

## Tests

```
odoo-bin -d <db> -i devarc_qr_invoice,devarc_qr_reconcile --test-enable --test-tags=/devarc_qr_invoice,/devarc_qr_reconcile --stop-after-init
```

## License

- `devarc_qr_invoice`: LGPL-3, see [LICENSE](LICENSE).
- `devarc_qr_reconcile`: AGPL-3, because it depends on AGPL-3 OCA modules, see [devarc_qr_reconcile/LICENSE](devarc_qr_reconcile/LICENSE).

## Contact

Open an issue in this repository or write to info@devarc.ch.
