/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { WebClient } from "@web/webclient/webclient";
import { getGlobalRules } from "./model_rules";

const BODY_CLASS_MAP = {
    o_access_guard_hide_search_panel: (rules) => rules?.search_panel?.hide_panel,
    o_access_guard_hide_custom_filter: (rules) =>
        rules?.search_panel?.hide_custom_filter,
    o_access_guard_hide_custom_group: (rules) =>
        rules?.search_panel?.hide_custom_group,
    o_access_guard_hide_favorite_delete: (rules) =>
        rules?.search_panel?.hide_delete_favorite,
};

function updateBodyClasses() {
    const body = document.body;
    if (!body) {
        return;
    }
    const rules = getGlobalRules();
    for (const [className, predicate] of Object.entries(BODY_CLASS_MAP)) {
        const enabled = Boolean(rules && predicate(rules));
        body.classList.toggle(className, enabled);
    }
}

patch(WebClient.prototype, {
    setup() {
        super.setup(...arguments);
        updateBodyClasses();
    },
});
