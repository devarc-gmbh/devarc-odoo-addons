# DevArc Odoo Addons

Odoo 19.0 Community Edition addons by [DevArc GmbH](https://devarc.ch/en), Pratteln, Switzerland.

| Addon | Version | Odoo | License | What it does |
|---|---|---|---|---|
| [devarc_qr_invoice](devarc_qr_invoice) | 19.0.2.1.0 | 19.0 CE | LGPL-3 | Import of vendor Swiss QR-bills from scanner text, PDF or photo, plus QR-bill checks for customer invoices on top of l10n_ch |

## Installation

1. Clone the branch that matches your Odoo version:

   ```
   git clone -b 19.0 https://github.com/devarc-gmbh/devarc-odoo-addons.git
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

## Contact

Open an issue in this repository or write to info@devarc.ch.
