# pylama:ignore=D100,C0114
{
    "name": "DevArc QR Invoice",
    "version": "19.0.2.1.0",
    "category": "Accounting/Localizations",
    "summary": "Import vendor Swiss QR-bills from scanner text, PDF or photo, plus QR-bill checks for customer invoices on top of l10n_ch",
    "author": "DevArc GmbH",
    "maintainer": "DevArc GmbH",
    "support": "info@devarc.ch",
    "website": "https://devarc.ch",
    "license": "LGPL-3",
    "depends": [
        "account",
        "l10n_ch",
    ],
    "external_dependencies": {
        "python": ["zxing-cpp", "pypdfium2"],
    },
    "data": [
        "security/ir.model.access.csv",
        "wizard/qr_bill_import_views.xml",
        "views/account_move_views.xml",
    ],
    "images": ["static/description/banner.png"],
}
