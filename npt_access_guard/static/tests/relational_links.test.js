import { expect, test } from "@odoo/hoot";
import { patchNoOpenAwareProductWidget } from "@npt_access_guard/js/guard/relational_links";

class BaseProductField {
    get hasExternalButton() {
        return Boolean(this.value);
    }

    get isProductClickable() {
        return true;
    }
}

class SaleProductField extends BaseProductField {
    get hasExternalButton() {
        return super.hasExternalButton || Boolean(this.value);
    }
}

patchNoOpenAwareProductWidget({ component: SaleProductField });

function makeField(canOpen) {
    const field = Object.create(SaleProductField.prototype);
    field.props = { canOpen };
    field.value = [1, "Test Product"];
    return field;
}

test("Sales product field suppresses links when no_open is active", () => {
    const field = makeField(false);

    expect(field.hasExternalButton).toBe(false);
    expect(field.isProductClickable).toBe(false);
});

test("Sales product field preserves links without no_open", () => {
    const field = makeField(true);

    expect(field.hasExternalButton).toBe(true);
    expect(field.isProductClickable).toBe(true);
});
