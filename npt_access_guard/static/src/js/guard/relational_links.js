/** @odoo-module **/

import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";

const patchedComponents = new WeakSet();

/**
 * Make Odoo's Sales product widget honor the standard `no_open` field option.
 * Sales normally forces its external button and readonly link to stay enabled.
 */
export function patchNoOpenAwareProductWidget(fieldDefinition) {
    const Component = fieldDefinition?.component;
    if (!Component || patchedComponents.has(Component)) {
        return;
    }

    patch(Component.prototype, {
        get hasExternalButton() {
            return this.props.canOpen !== false && super.hasExternalButton;
        },
        get isProductClickable() {
            return this.props.canOpen !== false && super.isProductClickable;
        },
    });
    patchedComponents.add(Component);
}

const fieldRegistry = registry.category("fields");
const widgetName = "sol_product_many2one";

patchNoOpenAwareProductWidget(fieldRegistry.get(widgetName, null));
fieldRegistry.addEventListener("UPDATE", ({ detail: { operation, key, value } }) => {
    if (operation === "add" && key === widgetName) {
        patchNoOpenAwareProductWidget(value);
    }
});
