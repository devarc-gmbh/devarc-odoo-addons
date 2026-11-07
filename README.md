# DevArc Odoo Addons

Odoo 20.0 Community Edition addons by [DevArc GmbH](https://devarc.ch/en), Pratteln, Switzerland.

| Addon | Version | Odoo | License | What it does |
|---|---|---|---|---|
| [devarc_qr_invoice](devarc_qr_invoice) | 20.0.2.1.0 | 20.0 CE | LGPL-3 | Import of vendor Swiss QR-bills from scanner text, PDF or photo, plus QR-bill checks for customer invoices on top of l10n_ch |

`devarc_qr_reconcile` will come to 20.0 once OCA ports `account_reconcile_oca` to Odoo 20.0; until then it is available on the 18.0 and 19.0 branches.

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
4. Update the Apps list and install **DevArc QR Invoice**.

## Tests

```
odoo-bin -d <db> -i devarc_qr_invoice --test-enable --test-tags=/devarc_qr_invoice --stop-after-init
```

## License

`devarc_qr_invoice` is licensed under LGPL-3, see [LICENSE](LICENSE).

## Contact and links

Open an issue in this repository or write to info@devarc.ch.

- Web: https://devarc.ch/en
- Blog: https://devarc.ch/blog
- YouTube (videos of these addons): https://www.youtube.com/@DevArc
- LinkedIn: https://www.linkedin.com/company/devarc-gmbh/
