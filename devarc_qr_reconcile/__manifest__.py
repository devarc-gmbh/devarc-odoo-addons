# pylama:ignore=D100,C0114
{
    "name": "DevArc QR Reconcile",
    "version": "18.0.1.1.1",
    "category": "Accounting/Localizations",
    "summary": "Match Swiss QRR and SCOR payments from camt statements to customer invoices",
    "author": "DevArc GmbH",
    "maintainer": "DevArc GmbH",
    "support": "info@devarc.ch",
    "website": "https://devarc.ch",
    "license": "AGPL-3",
    "depends": [
        "account_reconcile_oca",
        "account_statement_import_camt",
        "account_statement_import_camt54",
        "account_statement_import_file_reconcile_oca",
    ],
    "data": [
        "views/account_journal_views.xml",
        "views/account_bank_statement_line_views.xml",
    ],
    "installable": True,
}
