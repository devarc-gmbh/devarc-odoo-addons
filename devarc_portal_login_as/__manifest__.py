# pylama:ignore=D100,C0114
{
    "name": "DevArc Portal Login As",
    "version": "19.0.1.0.0",
    "category": "Website/Portal",
    "summary": "Let authorised staff view the customer portal as a portal user for 30 minutes, with step-up password check, audit log and customer notification",
    "author": "DevArc GmbH",
    "maintainer": "DevArc GmbH",
    "support": "info@devarc.ch",
    "website": "https://devarc.ch",
    "license": "LGPL-3",
    "depends": [
        "bus",
        "portal",
        "mail",
    ],
    "data": [
        "security/portal_login_as_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "wizard/portal_login_as_wizard_views.xml",
        "views/portal_login_as_log_views.xml",
        "views/res_users_views.xml",
        "views/res_partner_views.xml",
        "views/portal_templates.xml",
    ],
    "images": ["static/description/banner.png"],
}
