/** @odoo-module */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { Component } from "@odoo/owl";

class AccessGuardDashboard extends Component {}

AccessGuardDashboard.template = "npt_access_guard.AccessGuardDashboard";
AccessGuardDashboard.displayName = _t("Access Studio");

registry.category("actions").add(
    "access_guard_access_studio_dashboard",
    AccessGuardDashboard,
);
