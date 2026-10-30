/** @odoo-module **/

import { session } from "@web/session";
import { onWillUnmount, useEffect } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { ListController } from "@web/views/list/list_controller";
import { FormController } from "@web/views/form/form_controller";
import { FormRenderer } from "@web/views/form/form_renderer";
import { SearchPanel } from "@web/search/search_panel/search_panel";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { Domain } from "@web/core/domain";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { StockKanbanRenderer } from "@stock/components/stock_overview/stock_overview";
import { DynamicRecordList } from "@web/model/relational_model/dynamic_record_list";
import { DynamicGroupList } from "@web/model/relational_model/dynamic_group_list";

const ACTION_BOOLEAN_KEYS = [
    "hide_create",
    "hide_edit",
    "hide_delete",
    "hide_archive",
    "hide_duplicate",
    "hide_import",
    "hide_export",
    "hide_spreadsheet",
    "hide_add_property",
];

const DEFAULT_CHATTER = {
    hide_all: false,
    hide_send_message: false,
    hide_log_note: false,
    hide_schedule_activity: false,
};

const DEFAULT_SEARCH_PANEL = {
    hide_panel: false,
    hide_custom_filter: false,
    hide_custom_group: false,
    hide_delete_favorite: false,
    filters: [],
    groups: [],
};

function normalizeRules(rules) {
    if (!rules) {
        return null;
    }
    return {
        ...rules,
        chatter: { ...DEFAULT_CHATTER, ...(rules.chatter || {}) },
        search_panel: { ...DEFAULT_SEARCH_PANEL, ...(rules.search_panel || {}) },
    };
}

function combineKeyedItems(...sources) {
    const combined = new Map();
    for (const source of sources) {
        if (!source) {
            continue;
        }
        for (const item of source) {
            const key = item && item.key;
            if (!key) {
                continue;
            }
            combined.set(key, { ...item });
        }
    }
    return Array.from(combined.values());
}

function combineFieldRules(...sources) {
    const combined = {};
    for (const source of sources) {
        if (!source) {
            continue;
        }
        for (const [fieldName, config] of Object.entries(source)) {
            combined[fieldName] = {
                ...(combined[fieldName] || {}),
                ...config,
            };
        }
    }
    return combined;
}

function mergeRules(globalRules, modelRules) {
    const g = normalizeRules(globalRules);
    const m = normalizeRules(modelRules);
    if (!g && !m) {
        return null;
    }
    const merged = {
        chatter: { ...DEFAULT_CHATTER },
        search_panel: { ...DEFAULT_SEARCH_PANEL },
    };
    let hasValue = false;

    for (const key of ACTION_BOOLEAN_KEYS) {
        const value = Boolean(g?.[key]) || Boolean(m?.[key]);
        merged[key] = value;
        if (value) {
            hasValue = true;
        }
    }

    for (const key of Object.keys(DEFAULT_CHATTER)) {
        const value = Boolean(g?.chatter?.[key]) || Boolean(m?.chatter?.[key]);
        merged.chatter[key] = value;
        if (value) {
            hasValue = true;
        }
    }

    for (const key of [
        "hide_panel",
        "hide_custom_filter",
        "hide_custom_group",
        "hide_delete_favorite",
    ]) {
        const value = Boolean(g?.search_panel?.[key]) || Boolean(m?.search_panel?.[key]);
        merged.search_panel[key] = value;
        if (value) {
            hasValue = true;
        }
    }
    const filterSet = new Set([
        ...(g?.search_panel?.filters || []),
        ...(m?.search_panel?.filters || []),
    ]);
    if (filterSet.size) {
        merged.search_panel.filters = Array.from(filterSet);
        hasValue = true;
    }
    const groupSet = new Set([
        ...(g?.search_panel?.groups || []),
        ...(m?.search_panel?.groups || []),
    ]);
    if (groupSet.size) {
        merged.search_panel.groups = Array.from(groupSet);
        hasValue = true;
    }

    const fields = combineFieldRules(g?.fields, m?.fields);
    if (Object.keys(fields).length) {
        merged.fields = fields;
        hasValue = true;
    }

    const buttons = combineKeyedItems(g?.buttons, m?.buttons);
    if (buttons.length) {
        merged.buttons = buttons;
        hasValue = true;
    }

    const smartButtons = combineKeyedItems(g?.smart_buttons, m?.smart_buttons);
    if (smartButtons.length) {
        merged.smart_buttons = smartButtons;
        hasValue = true;
    }

    const tabs = combineKeyedItems(g?.tabs, m?.tabs);
    if (tabs.length) {
        merged.tabs = tabs;
        hasValue = true;
    }

    const kanbanLinks = combineKeyedItems(g?.kanban_links, m?.kanban_links);
    if (kanbanLinks.length) {
        merged.kanban_links = kanbanLinks;
        hasValue = true;
    }

    const reports = new Set([...(g?.reports || []), ...(m?.reports || [])]);
    if (reports.size) {
        merged.reports = Array.from(reports);
        hasValue = true;
    }

    const actions = new Set([...(g?.actions || []), ...(m?.actions || [])]);
    if (actions.size) {
        merged.actions = Array.from(actions);
        hasValue = true;
    }

    const viewModes = new Set([...(g?.view_modes || []), ...(m?.view_modes || [])]);
    if (viewModes.size) {
        merged.view_modes = Array.from(viewModes);
        hasValue = true;
    }

    const views = new Set([...(g?.views || []), ...(m?.views || [])]);
    if (views.size) {
        merged.views = Array.from(views);
        hasValue = true;
    }

    merged.disable_login = Boolean(g?.disable_login) || Boolean(m?.disable_login);
    if (merged.disable_login) {
        hasValue = true;
    }

    // Preserve any UI-only extensions that don't affect security logic.
    // For now, we forward inventory overview config as-is so the client
    // side can decide how to interpret it.
    merged.overview = [
        ...(g?.overview || []),
        ...(m?.overview || []),
    ];
    if (merged.overview.length) {
        hasValue = true;
    }

    return hasValue ? merged : null;
}

export function getGlobalRules() {
    const rules = session.access_guard_model_rules || {};
    return mergeRules(rules.__global__, null);
}

export function getModelRules(model) {
    const rules = session.access_guard_model_rules || {};
    return mergeRules(rules.__global__, rules[model]);
}

function applyListRestrictions(controller, rules) {
    if (!rules) {
        return;
    }
    if (rules.hide_create) {
        controller.activeActions.create = false;
    }
    if (rules.hide_edit) {
        controller.activeActions.edit = false;
    }
    if (rules.hide_delete) {
        controller.activeActions.delete = false;
    }
    if (rules.hide_duplicate) {
        controller.activeActions.duplicate = false;
    }
    if (rules.hide_archive) {
        controller.archiveEnabled = false;
    }
    if (rules.hide_import) {
        controller.isImportEnable = false;
    }
    if (rules.hide_export) {
        controller.isExportEnable = false;
    }
    if (rules.hide_spreadsheet && "isSpreadsheetEnable" in controller) {
        controller.isSpreadsheetEnable = false;
    }
}

patch(ListController.prototype, {
    setup() {
        super.setup(...arguments);
        this.accessGuardRules = getModelRules(this.props.resModel);
        applyListRestrictions(this, this.accessGuardRules);
    },
    getStaticActionMenuItems() {
        const items = super.getStaticActionMenuItems(...arguments);
        const rules = this.accessGuardRules;
        if (!rules) {
            return items;
        }
        const updated = { ...items };
        if (rules.hide_export) {
            delete updated.export;
        }
        if (rules.hide_import) {
            delete updated.import;
        }
        if (rules.hide_archive) {
            delete updated.archive;
            delete updated.unarchive;
        }
        if (rules.hide_duplicate) {
            delete updated.duplicate;
        }
        if (rules.hide_delete) {
            delete updated.delete;
        }
        if (rules.hide_spreadsheet) {
            delete updated.spreadsheet;
        }
        return updated;
    },
});

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this.accessGuardRules = getModelRules(this.props.resModel);
        const rules = this.accessGuardRules;
        if (rules) {
            if (rules.hide_create) {
                this.canCreate = false;
            }
            if (rules.hide_edit) {
                this.canEdit = false;
            }
            if (rules.hide_delete) {
                this.archInfo.activeActions.delete = false;
            }
            if (rules.hide_duplicate) {
                this.archInfo.activeActions.duplicate = false;
            }
            if (rules.hide_add_property && this.archInfo.activeActions) {
                this.archInfo.activeActions.addPropertyFieldValue = false;
            }
        }
    },
});

function mapFieldIdsByName(archInfo) {
    const map = {};
    if (!archInfo?.fieldNodes) {
        return map;
    }
    for (const [fieldId, info] of Object.entries(archInfo.fieldNodes)) {
        const fieldName = info?.name;
        if (!fieldName) {
            continue;
        }
        if (!map[fieldName]) {
            map[fieldName] = [];
        }
        map[fieldName].push(fieldId);
    }
    return map;
}

function escapeSelector(value) {
    if (typeof CSS !== "undefined" && CSS.escape) {
        return CSS.escape(value);
    }
    return value.replace(/([!"#$%&'()*+,./:;<=>?@[\]^{|}~])/g, "\\");
}

function evaluateDomain(domain, record) {
    if (!domain || !domain.length) {
        return false;
    }
    try {
        const evalContext = record.evalContextWithVirtualIds || {};
        return new Domain(domain).contains(evalContext);
    } catch (_) {
        return false;
    }
}

const SEARCH_PANEL_BODY_CLASS_MAP = {
    o_access_guard_hide_search_panel: "hide_panel",
    o_access_guard_hide_custom_filter: "hide_custom_filter",
    o_access_guard_hide_custom_group: "hide_custom_group",
    o_access_guard_hide_favorite_delete: "hide_delete_favorite",
};

function clearSearchPanelBodyClasses(target) {
    if (!target._accessGuardBodyClasses) {
        return;
    }
    for (const className of target._accessGuardBodyClasses) {
        document.body.classList.remove(className);
    }
    target._accessGuardBodyClasses = [];
}

function applySearchPanelBodyClasses(target, panelRules = {}) {
    clearSearchPanelBodyClasses(target);
    const applied = [];
    for (const [className, key] of Object.entries(SEARCH_PANEL_BODY_CLASS_MAP)) {
        if (panelRules[key]) {
            document.body.classList.add(className);
            applied.push(className);
        }
    }
    target._accessGuardBodyClasses = applied;
}

patch(FormRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        const rules = getModelRules(this.props.record.resModel);
        const fieldConditionals = rules?.field_conditionals || [];
        const effectiveRules = fieldConditionals.filter((rule) => {
            if (!rule || !rule.apply_attrs || rule.attrs_type !== "invisible") {
                return false;
            }
            const attrsFields = rule.attrs_fields || (rule.attrs_field ? [rule.attrs_field] : []);
            return attrsFields.length && rule.attrs_domain && rule.attrs_domain.length;
        });
        if (!effectiveRules.length) {
            this.accessGuardConditionalRules = null;
            return;
        }
        this.accessGuardConditionalRules = effectiveRules;
        this.accessGuardFieldIdMap = mapFieldIdsByName(this.props.archInfo);
        useRecordObserver((record) => {
            this._accessGuardApplyConditionalVisibility(record);
        });
    },

    _accessGuardApplyConditionalVisibility(record) {
        if (!this.accessGuardConditionalRules?.length) {
            return;
        }
        const root = this.el;
        if (!root) {
            return;
        }
        for (const rule of this.accessGuardConditionalRules) {
            const attrsFields = rule.attrs_fields || (rule.attrs_field ? [rule.attrs_field] : []);
            if (!attrsFields.length) {
                continue;
            }
            const hidden = evaluateDomain(rule.attrs_domain, record);
            for (const fieldName of attrsFields) {
                const fieldIds = this.accessGuardFieldIdMap[fieldName];
                if (!fieldIds || !fieldIds.length) {
                    continue;
                }
                for (const fieldId of fieldIds) {
                    const selector = `#${escapeSelector(fieldId)}`;
                    const fieldNode = root.querySelector(selector);
                    if (!fieldNode) {
                        continue;
                    }
                    const widget = fieldNode.closest(".o_field_widget") || fieldNode;
                    widget.classList.toggle("o_access_guard_hidden", hidden);
                    const label = root.querySelector(`label[for='${escapeSelector(fieldId)}']`);
                    if (label) {
                        label.classList.toggle("o_access_guard_hidden", hidden);
                    }
                }
            }
        }
    },
});

patch(SearchPanel.prototype, {
    setup() {
        super.setup(...arguments);
        useEffect(
            (rootEl) => {
                if (!rootEl) {
                    return;
                }
                const panelRules =
                    getModelRules(this.env.searchModel?.resModel)?.search_panel || {};
                if (panelRules.hide_panel) {
                    rootEl.classList.add("o_access_guard_hidden");
                } else {
                    rootEl.classList.remove("o_access_guard_hidden");
                }
            },
            () => [this.root?.el, this.env.searchModel?.resModel]
        );
    },
});

const searchPanelSectionsDescriptor = Object.getOwnPropertyDescriptor(
    SearchPanel.prototype,
    "sections"
);
if (searchPanelSectionsDescriptor && searchPanelSectionsDescriptor.get) {
    const originalSections = searchPanelSectionsDescriptor.get;
    Object.defineProperty(SearchPanel.prototype, "sections", {
        get() {
            const sections = originalSections.call(this) || [];
            const rules = getModelRules(this.env.searchModel?.resModel);
            const panelRules = rules?.search_panel;
            if (!panelRules) {
                return sections;
            }
            if (panelRules.hide_panel) {
                return [];
            }
            const filterSet = new Set(panelRules.filters || []);
            const groupSet = new Set(panelRules.groups || []);
            if (!filterSet.size && !groupSet.size) {
                return sections;
            }
            return sections.filter((section) => {
                const fieldName = section.fieldName;
                if (!fieldName) {
                    return true;
                }
                if (section.groupBy) {
                    return !groupSet.has(fieldName);
                }
                if (section.type === "filter") {
                    return !filterSet.has(fieldName);
                }
                return true;
            });
        },
    });
}

patch(SearchBar.prototype, {
    setup() {
        super.setup(...arguments);
        const panelRules =
            getModelRules(this.env.searchModel?.resModel)?.search_panel || {};
        applySearchPanelBodyClasses(this, panelRules);
        onWillUnmount(() => {
            clearSearchPanelBodyClasses(this);
        });
    },
});

const originalSearchBarComputeState = SearchBar.prototype.computeState;
SearchBar.prototype.computeState = async function (options = {}) {
    const result = await originalSearchBarComputeState.call(this, options);
    const panelRules =
        getModelRules(this.env.searchModel?.resModel)?.search_panel || {};
    applySearchPanelBodyClasses(this, panelRules);
    return result;
};

// ---------------------------------------------------------------------------
// Inventory Overview (stock.picking.type kanban) – UI-only filtering
// ---------------------------------------------------------------------------

/**
 * Compute which stock.picking.type records should be kept for the
 * Inventory Overview kanban, based on Access Guard overview rules.
 *
 * - mode 'hide': hide selected, keep others
 * - mode 'show': show only selected
 *
 * This is intentionally client-side only and never changes the server-side
 * search domain, so functional flows (Purchases, Receipts, etc.) keep
 * seeing all operation types.
 */
function filterOverviewRecords(records) {
    if (!records || !records.length) {
        return records;
    }
    const rules = getModelRules("stock.picking.type");
    const overviewRules = rules?.overview || [];
    if (!overviewRules.length) {
        return records;
    }
    // Merge all overview rules into a single effective rule set.
    let mode = "hide";
    const idsToHide = new Set();
    const idsToShow = new Set();
    for (const rule of overviewRules) {
        const ruleMode = rule.mode || "hide";
        const ids = rule.ids || [];
        if (ruleMode === "show") {
            mode = "show";
            for (const id of ids) {
                idsToShow.add(id);
            }
        } else {
            for (const id of ids) {
                idsToHide.add(id);
            }
        }
    }
    if (mode === "show" && !idsToShow.size) {
        // Show-only with no ids: keep everything to avoid breaking flows.
        return records;
    }
    return records.filter((record) => {
        const id = record.resId;
        if (!id) {
            return true;
        }
        if (mode === "show") {
            return idsToShow.has(id);
        }
        // hide mode
        return !idsToHide.has(id);
    });
}

patch(StockKanbanRenderer.prototype, {
    /**
     * Filter Inventory Overview kanban records in-memory based on Access Guard
     * overview settings, without impacting server-side domains.
     */
    getGroupsOrRecords() {
        const { list } = this.props;
        if (list instanceof DynamicRecordList) {
            if (!list._accessGuardOverviewDone) {
                list._accessGuardOverviewDone = true;
                list.records = filterOverviewRecords(list.records);
            }
        } else if (list instanceof DynamicGroupList) {
            for (const group of list.groups || []) {
                if (group.list instanceof DynamicRecordList) {
                    const groupList = group.list;
                    if (!groupList._accessGuardOverviewDone) {
                        groupList._accessGuardOverviewDone = true;
                        groupList.records = filterOverviewRecords(groupList.records);
                    }
                }
            }
        }
        return super.getGroupsOrRecords(...arguments);
    },
});
