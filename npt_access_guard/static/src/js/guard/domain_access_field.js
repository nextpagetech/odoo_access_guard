/** @odoo-module **/

import { Domain } from "@web/core/domain";
import { domainContainsExpresssions } from "@web/core/tree_editor/condition_tree";
import { registry } from "@web/core/registry";
import { DomainField, domainField } from "@web/views/fields/domain/domain_field";

export class AccessGuardDomainField extends DomainField {
    static template = "npt_access_guard.DomainAccessField";

    allowExpressions() {
        return true;
    }

    isDynamicDomain(props = this.props) {
        return domainContainsExpresssions(this.debugDomain || this.getDomain(props)) === true;
    }

    async validateDynamicDomain(props = this.props) {
        const domain = this.debugDomain || this.getDomain(props);
        const model = this.getResModel(props);
        if (!model) {
            return false;
        }
        try {
            new Domain(domain);
            return Boolean(
                await this.orm.call("access.guard.management.domain", "validate_ui_domain", [
                    model,
                    domain,
                ])
            );
        } catch {
            return false;
        }
    }

    async checkProps(props = this.props) {
        if (!this.isDynamicDomain(props)) {
            return super.checkProps(props);
        }
        const isValid = await this.validateDynamicDomain(props);
        this.updateState({ isValid, recordCount: null });
    }

    async quickValidityCheck(props) {
        if (!this.isDynamicDomain(props)) {
            return super.quickValidityCheck(props);
        }
        return this.validateDynamicDomain(props);
    }
}

registry.category("fields").add("access_guard_domain", {
    ...domainField,
    component: AccessGuardDomainField,
});
