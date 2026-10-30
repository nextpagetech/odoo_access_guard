import { expect, test } from "@odoo/hoot";
import { contains, defineModels, fields, mountView, onRpc } from "@web/../tests/web_test_helpers";
import { Partner } from "@web/../tests/core/tree_editor/condition_tree_editor_test_helpers";

import "@npt_access_guard/js/guard/domain_access_field";

defineModels([Partner]);

const dynamicDomains = [
    "[('user_id', '=', uid)]",
    "[('user_id', '=', user.id)]",
    "[('company_id', '=', company_id)]",
    "[('company_id', 'in', company_ids)]",
    "[('approver_user_ids', 'in', [uid])]",
    "[('field_name', '=', context.get('some_key'))]",
];

async function mountDomainField(domain) {
    Partner._fields.user_id = fields.Many2one({ relation: "partner" });
    Partner._fields.company_id = fields.Many2one({ relation: "partner" });
    Partner._fields.approver_user_ids = fields.Many2many({ relation: "partner" });
    Partner._fields.field_name = fields.Char();
    Partner._records[0].foo = domain;
    await mountView({
        type: "form",
        resModel: "partner",
        resId: 1,
        arch: `<form><field name="foo" widget="access_guard_domain" options="{'model': 'partner'}"/></form>`,
    });
}

for (const domain of dynamicDomains) {
    test(`Domain Access accepts dynamic code: ${domain}`, async () => {
        onRpc("validate_ui_domain", ({ args }) => args[0] === "partner" && args[1] === domain);
        await mountDomainField(domain);
        expect(".o_field_domain_panel [role='status']").toHaveText("Valid domain");
        expect(".o_domain_selector_debug_container textarea").toHaveValue(domain);
        expect(".o_field_domain_panel [role='alert']").toHaveCount(0);
    });
}

test("Domain Access saves dynamic code unchanged", async () => {
    const original = "[('user_id', '=', uid)]";
    const changed = "[('user_id', '=', user.id)]";
    onRpc("validate_ui_domain", ({ args }) => args[0] === "partner" && [original, changed].includes(args[1]));
    onRpc("web_save", ({ args }) => expect(args[1].foo).toBe(changed));
    await mountDomainField(original);
    await contains(".o_domain_selector_debug_container textarea").edit(changed);
    await contains(".o_form_button_save").click();
});

test("Domain Access keeps the graphical builder for static values", async () => {
    await mountDomainField("[('foo', '=', 'A')]");
    expect(".o_domain_selector .o_tree_editor").toHaveCount(1);
    expect(".o_domain_show_selection_button").toHaveCount(1);
    expect(".o_domain_selector_debug_container textarea").toHaveValue("[('foo', '=', 'A')]");
});

test("Domain Access displays invalid dynamic fields and malformed code", async () => {
    onRpc("validate_ui_domain", () => false);
    await mountDomainField("[('non_existing_field', '=', uid)]");
    expect(".o_field_domain_panel [role='alert']").toHaveText("Invalid domain");
});

test("Domain Access displays invalid syntax", async () => {
    await mountDomainField("[('user_id', '=', uid)");
    expect(".o_field_domain_panel [role='alert']").toHaveText("Invalid domain");
});
