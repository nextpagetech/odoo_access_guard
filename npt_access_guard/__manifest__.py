{
    "name": "Access Guard",
    "summary": "Manage Odoo access rules, UI restrictions and reusable security templates",
    "version": "18.0.6.0.0",
    "author": "Next Page Technologies Pvt Ltd",
    "website": "https://nextpagetechnologies.com",
    "support": "hello@nextpagetechnologies.com",
    "category": "Administration/Technical",
    "license": "LGPL-3",
    "price": 30.0,
    "currency": "USD",
    "depends": ["base", "web", "base_import", "stock"],
    "data": [
        "security/access_guard_security.xml",
        "security/ir.model.access.csv",
        "data/access_guard_view_modes.xml",
        "views/access_guard_views.xml",
        "views/access_guard_template_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "npt_access_guard/static/src/js/**/*",
            "npt_access_guard/static/src/xml/**/*",
            "npt_access_guard/static/src/scss/guard_chatter.scss",
        ],
        "web.assets_unit_tests": [
            "npt_access_guard/static/tests/**/*.test.js",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
    "auto_install": False,
}
