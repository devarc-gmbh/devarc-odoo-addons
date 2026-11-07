# DevArc QR Reconcile

Matches incoming Swiss QR-bill payments (QRR and SCOR references) from camt.053 and camt.054
bank files to open customer invoices.

## Dependencies

OCA modules, not part of Odoo and not installed by this repository:

- `account_reconcile_oca` from [OCA/account-reconcile](https://github.com/OCA/account-reconcile), branch 19.0
- `account_statement_import_camt`, `account_statement_import_camt54` and `account_statement_import_file_reconcile_oca` from
  [OCA/bank-statement-import](https://github.com/OCA/bank-statement-import), branch 19.0

Add both repositories to `addons_path` before installing the module.

## How it works

- The reference is read from the statement line reference, label and notes. Spaces are ignored,
  QRR needs a valid modulo 10 check digit, SCOR a valid ISO 11649 check.
- A line is linked to an invoice only when exactly one posted, open customer invoice of the same
  company has that payment reference and the line amount is positive. Two invoices with the same
  reference, a paid invoice, a negative amount or an unknown reference leave the line unlinked.
- Lines linked to an invoice are excluded from the generic OCA invoice matching query, this module
  proposes the invoice for them.
- Vendor payments (negative amounts) are left to OCA.

## Journal setting: QR Reference Matching

Set on the bank journal.

- **Mark matched invoice** (default): the invoice is linked on the statement line and proposed in
  the reconciliation widget. Nothing is reconciled automatically.
- **Reconcile exact matches**: the line is reconciled automatically only when all of these hold:
  exact amount equal to the invoice residual, one invoice, same currency (no foreign currency on the
  line), positive amount and, if the line has a partner, the same partner as the invoice.
  Partial payments, overpayments and other currencies are only linked and proposed.
- A reference that matches no open invoice or more than one (ambiguous) links no invoice in either
  mode. The line is listed under the filter *QR Reference Without Invoice* and is left to manual
  reconciliation.

## Filters

Bank statement lines: *QR Invoice Found* (linked open invoice, line not reconciled) and
*QR Reference Without Invoice* (reference found, no unique open invoice, line not reconciled).
The list also shows the QR invoice column.

## Limitations

- Importing the same camt file twice creates the lines twice, the OCA camt import has no
  deduplication.

## License

AGPL-3, because the dependencies are AGPL-3.

## Support

Video walkthrough: https://youtu.be/EXF_1ktpaHc

DevArc GmbH, https://devarc.ch, info@devarc.ch
