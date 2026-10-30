/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Chatter } from "@mail/chatter/web_portal/chatter";
import { getModelRules } from "./model_rules";

const ACCESS_GUARD_CHATTER_CLASSES = [
    "o_access_guard_chatter_hidden",
    "o_access_guard_hide_send_message",
    "o_access_guard_hide_log_note",
    "o_access_guard_hide_schedule_activity",
];

function getChatterRules(model) {
    if (!model) {
        return {};
    }
    const rules = getModelRules(model);
    return rules ? rules.chatter || {} : {};
}

function computeClassList(rules) {
    const classes = new Set();
    if (!rules) {
        return classes;
    }
    if (rules.hide_all) {
        classes.add("o_access_guard_chatter_hidden");
    }
    if (rules.hide_send_message) {
        classes.add("o_access_guard_hide_send_message");
    }
    if (rules.hide_log_note) {
        classes.add("o_access_guard_hide_log_note");
    }
    if (rules.hide_schedule_activity) {
        classes.add("o_access_guard_hide_schedule_activity");
    }
    return classes;
}

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.accessGuardChatterClasses = new Set();
        this.accessGuardChatterPendingSync = false;
        this._applyAccessGuardChatterRules(this.props.threadModel);
    },

    changeThread(threadModel, threadId) {
        super.changeThread(...arguments);
        this._applyAccessGuardChatterRules(threadModel);
    },

    _onMounted() {
        super._onMounted();
        this._syncAccessGuardChatterClasses();
    },

    /**
     * Compute the chatter classes for the provided model and update the DOM.
     *
     * @private
     * @param {string} model
     */
    _applyAccessGuardChatterRules(model) {
        this.accessGuardChatterRules = getChatterRules(model);
        this.accessGuardChatterClasses = computeClassList(this.accessGuardChatterRules);
        this._syncAccessGuardChatterClasses();
    },

    /**
     * Apply or remove classes on the chatter root element based on the stored rules.
     *
     * @private
     */
    _syncAccessGuardChatterClasses() {
        const el = this.rootRef?.el;
        if (!el) {
            this.accessGuardChatterPendingSync = true;
            return;
        }
        this.accessGuardChatterPendingSync = false;
        for (const className of ACCESS_GUARD_CHATTER_CLASSES) {
            el.classList.remove(className);
        }
        for (const className of this.accessGuardChatterClasses) {
            el.classList.add(className);
        }
        this._syncAccessGuardChatterButtons(el);
    },

    /**
     * Update chatter button visibility using DOM toggles as a fallback in case CSS is not applied.
     *
     * @private
     * @param {HTMLElement} root
     */
    _syncAccessGuardChatterButtons(root) {
        if (!root || this.accessGuardChatterRules?.hide_all) {
            return;
        }
        const toggle = (selector, hidden) => {
            root.querySelectorAll(selector).forEach((node) => {
                node.classList.toggle("o_access_guard_hidden", Boolean(hidden));
            });
        };
        toggle(".o-mail-Chatter-sendMessage", this.accessGuardChatterRules?.hide_send_message);
        toggle(".o-mail-Chatter-logNote", this.accessGuardChatterRules?.hide_log_note);
        toggle(".o-mail-Chatter-activity", this.accessGuardChatterRules?.hide_schedule_activity);
        toggle(".o-mail-ActivityList", this.accessGuardChatterRules?.hide_schedule_activity);
    },
});
