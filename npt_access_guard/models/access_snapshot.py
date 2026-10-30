import hashlib
import json
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.modules.module import get_manifest


SNAPSHOT_SCHEMA_VERSION = "1.0"
SNAPSHOT_SECTIONS = (
    "general",
    "menus",
    "model_rules",
    "field_rules",
    "conditional_rules",
    "domain_rules",
    "ui_rules",
    "search_rules",
    "chatter_rules",
    "global",
    "overview",
    "acl_rules",
)


class AccessGuardManagementSnapshot(models.Model):
    _inherit = "access.guard.management"

    @api.model
    def _access_guard_external_id(self, record):
        if not record:
            return None
        external_ids = record.sudo()._get_external_ids().get(record.id, [])
        return sorted(external_ids)[0] if external_ids else None

    @api.model
    def _access_guard_named_ref(self, record, **values):
        if not record:
            return None
        record = record.sudo().with_context(lang=False)
        result = {
            "id": record.id,
            "xml_id": self._access_guard_external_id(record),
            "name": record.name or "",
        }
        result.update(values)
        return result

    @api.model
    def _access_guard_model_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(record, model=record.model or "")

    @api.model
    def _access_guard_field_ref(self, record):
        if not record:
            return None
        record = record.sudo().with_context(lang=False)
        return {
            "id": record.id,
            "xml_id": self._access_guard_external_id(record),
            "model": record.model or "",
            "name": record.name or "",
            "label": record.field_description or "",
            "type": record.ttype or "",
        }

    @api.model
    def _access_guard_menu_ref(self, record):
        return self._access_guard_named_ref(
            record,
            parent_xml_id=self._access_guard_external_id(record.parent_id) if record and record.parent_id else None,
        )

    @api.model
    def _access_guard_view_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(
            record,
            model=record.model or "",
            type=record.type or "",
        )

    @api.model
    def _access_guard_report_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(
            record,
            model=record.model or "",
            report_name=record.report_name or "",
            report_type=record.report_type or "",
        )

    @api.model
    def _access_guard_server_action_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(
            record,
            model=record.model_id.model if record.model_id else "",
            state=record.state or "",
        )

    @api.model
    def _access_guard_window_action_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(
            record,
            res_model=record.res_model or "",
            binding_model=record.binding_model_id.model if record.binding_model_id else "",
            binding_type=record.binding_type or "",
            view_mode=record.view_mode or "",
        )

    @api.model
    def _access_guard_view_mode_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(record, code=record.code or "")

    @api.model
    def _access_guard_group_ref(self, record):
        return self._access_guard_named_ref(record)

    @api.model
    def _access_guard_company_ref(self, record):
        return self._access_guard_named_ref(record)

    @api.model
    def _access_guard_warehouse_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(
            record,
            code=record.code or "",
        )

    @api.model
    def _access_guard_picking_type_ref(self, record):
        if not record:
            return None
        return self._access_guard_named_ref(
            record,
            code=record.code or "",
            sequence_code=record.sequence_code or "",
            warehouse=self._access_guard_warehouse_ref(record.warehouse_id),
            company=self._access_guard_company_ref(record.company_id),
        )

    @api.model
    def _access_guard_ui_element_ref(self, record):
        if not record:
            return None
        return {
            "id": record.id,
            "xml_id": self._access_guard_external_id(record),
            "model": self._access_guard_model_ref(record.model_id),
            "type": record.type or "",
            "match_mode": record.match_mode or "",
            "match_value": record.match_value or "",
            "attribute_name": record.attribute_name or "",
            "label": record.with_context(lang=False).label or "",
            "view_type": record.view_type or "",
            "view": self._access_guard_view_ref(record.view_id),
            "active": bool(record.active),
        }

    @api.model
    def _access_guard_search_item_ref(self, record):
        if not record:
            return None
        return {
            "id": record.id,
            "xml_id": self._access_guard_external_id(record),
            "model": self._access_guard_model_ref(record.model_id),
            "field": self._access_guard_field_ref(record.field_id),
            "type": record.type or "",
            "label": record.with_context(lang=False).label or "",
            "active": bool(record.active),
        }

    @api.model
    def _access_guard_sorted_refs(self, records, serializer):
        values = [serializer(record) for record in records if record]
        return sorted(
            values,
            key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        )

    def _access_guard_build_snapshot(self, schema_version=SNAPSHOT_SCHEMA_VERSION):
        self.ensure_one()
        rule = self.sudo().with_context(lang=False)
        model_ref = self._access_guard_model_ref
        field_ref = self._access_guard_field_ref
        ui_ref = self._access_guard_ui_element_ref
        search_ref = self._access_guard_search_item_ref
        sorted_refs = self._access_guard_sorted_refs

        payload = {
            "schema_version": schema_version,
            "general": {
                "name": rule.name or "",
                "category": rule.category or "custom",
                "active": bool(rule.active),
                "disable_developer_mode": bool(rule.disable_developer_mode),
                "read_only": bool(rule.read_only),
                "apply_without_company": bool(rule.apply_without_company),
                "default_internal_user": bool(rule.default_internal_user),
                "default_portal_user": bool(rule.default_portal_user),
                "apply_by_group": bool(rule.apply_by_group),
            },
            "menus": [
                {
                    "sequence": line.sequence,
                    "menu": self._access_guard_menu_ref(line.menu_id),
                    "include_children": bool(line.include_children),
                }
                for line in rule.menu_line_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "model_rules": [
                {
                    "sequence": line.sequence,
                    "model": model_ref(line.model_id),
                    "reports": sorted_refs(line.report_ids, self._access_guard_report_ref),
                    "server_actions": sorted_refs(line.action_ids, self._access_guard_server_action_ref),
                    "window_actions": sorted_refs(line.window_action_ids, self._access_guard_window_action_ref),
                    "hidden_view_modes": sorted_refs(line.hide_view_mode_ids, self._access_guard_view_mode_ref),
                    "views": sorted_refs(line.view_ids, self._access_guard_view_ref),
                    "hide_create": bool(line.hide_create),
                    "hide_edit": bool(line.hide_edit),
                    "hide_delete": bool(line.hide_delete),
                    "hide_archive": bool(line.hide_archive),
                    "hide_duplicate": bool(line.hide_duplicate),
                    "hide_import": bool(line.hide_import),
                    "hide_export": bool(line.hide_export),
                    "hide_spreadsheet": bool(line.hide_spreadsheet),
                }
                for line in rule.model_rule_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "field_rules": [
                {
                    "sequence": line.sequence,
                    "model": model_ref(line.model_id),
                    "fields": sorted_refs(line.field_ids, field_ref),
                    "invisible": bool(line.invisible),
                    "read_only": bool(line.read_only),
                    "required": bool(line.required),
                    "remove_create_option": bool(line.remove_create_option),
                    "remove_edit_option": bool(line.remove_edit_option),
                    "remove_external_link": bool(line.remove_external_link),
                }
                for line in rule.field_rule_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "conditional_rules": [
                {
                    "sequence": line.sequence,
                    "model": model_ref(line.model_id),
                    "apply_attrs": bool(line.apply_attrs),
                    "attr_fields": sorted_refs(line.attr_field_ids, field_ref),
                    "attr_buttons": sorted_refs(line.attr_button_ids, ui_ref),
                    "attr_type": line.attr_type or False,
                    "attrs_domain": line.attrs_domain or False,
                    "apply_domain": bool(line.apply_domain),
                    "domain_field": field_ref(line.domain_field_id),
                    "domain_expression": line.domain_expression or False,
                }
                for line in rule.field_conditional_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "domain_rules": [
                {
                    "sequence": line.sequence,
                    "model": model_ref(line.model_id),
                    "soft_restrict": bool(line.soft_restrict),
                    "apply_relational_fields": bool(line.apply_relational_fields),
                    "perm_read": bool(line.perm_read),
                    "perm_create": bool(line.perm_create),
                    "perm_write": bool(line.perm_write),
                    "perm_delete": bool(line.perm_delete),
                    "apply_filter": bool(line.apply_filter),
                    "domain_expression": line.domain_expression or False,
                }
                for line in rule.domain_rule_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "ui_rules": [
                {
                    "sequence": line.sequence,
                    "model": model_ref(line.model_id),
                    "button_elements": sorted_refs(line.button_element_ids, ui_ref),
                    "action_button_elements": sorted_refs(line.action_button_element_ids, ui_ref),
                    "smart_button_elements": sorted_refs(line.smart_button_element_ids, ui_ref),
                    "tab_elements": sorted_refs(line.tab_element_ids, ui_ref),
                    "kanban_link_elements": sorted_refs(line.kanban_link_element_ids, ui_ref),
                }
                for line in rule.ui_rule_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "search_rules": [
                {
                    "sequence": line.sequence,
                    "model": model_ref(line.model_id),
                    "hide_search_panel": bool(line.hide_search_panel),
                    "filter_items": sorted_refs(line.filter_item_ids, search_ref),
                    "group_items": sorted_refs(line.group_item_ids, search_ref),
                    "hide_custom_filter": bool(line.hide_custom_filter),
                    "hide_custom_group": bool(line.hide_custom_group),
                    "hide_delete_favorite": bool(line.hide_delete_favorite),
                }
                for line in rule.search_panel_rule_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "chatter_rules": [
                {
                    "sequence": line.sequence,
                    "model": model_ref(line.model_id),
                    "hide_chatter": bool(line.hide_chatter),
                    "hide_send_message": bool(line.hide_send_message),
                    "hide_log_note": bool(line.hide_log_note),
                    "hide_schedule_activity": bool(line.hide_schedule_activity),
                }
                for line in rule.chatter_rule_ids.sorted(key=lambda item: (item.sequence, item.id))
            ],
            "global": {
                "hide_chatter": bool(rule.global_hide_chatter),
                "hide_send_message": bool(rule.global_hide_send_message),
                "hide_log_note": bool(rule.global_hide_log_note),
                "hide_schedule_activity": bool(rule.global_hide_schedule_activity),
                "hide_import": bool(rule.global_hide_import),
                "hide_export": bool(rule.global_hide_export),
                "hide_spreadsheet": bool(rule.global_hide_spreadsheet),
                "hide_add_property": bool(rule.global_hide_add_property),
                "hide_search_panel": bool(rule.global_hide_search_panel),
                "hide_custom_filter": bool(rule.global_hide_custom_filter),
                "hide_custom_group": bool(rule.global_hide_custom_group),
                "hide_delete_favorite": bool(rule.global_hide_delete_favorite),
                "disable_login": bool(rule.global_disable_login),
            },
            "overview": {
                "mode": rule.overview_mode or False,
                "picking_types": sorted_refs(
                    rule.overview_picking_type_ids,
                    self._access_guard_picking_type_ref,
                ),
            },
            "acl_rules": [
                {
                    "model": model_ref(line.model_id),
                    "group": self._access_guard_group_ref(line.group_id),
                    "perm_read": bool(line.perm_read),
                    "perm_write": bool(line.perm_write),
                    "perm_create": bool(line.perm_create),
                    "perm_unlink": bool(line.perm_unlink),
                }
                for line in rule.acl_rule_ids.sorted(
                    key=lambda item: (item.model_id.model or "", item.group_id.name or "", item.id)
                )
            ],
        }
        return payload


class AccessGuardTemplateSnapshot(models.Model):
    _inherit = "access.guard.template"

    template_code = fields.Char(required=True, index=True, copy=False)
    template_version = fields.Char(required=True, default="1.0")
    schema_version = fields.Char(required=True, default=SNAPSHOT_SCHEMA_VERSION, readonly=True)
    snapshot_payload = fields.Text(readonly=True, copy=False)
    snapshot_date = fields.Datetime(readonly=True, copy=False)
    snapshot_hash = fields.Char(readonly=True, copy=False, index=True)
    source_module_version = fields.Char(readonly=True, copy=False)
    snapshot_valid = fields.Boolean(compute="_compute_snapshot_metadata")
    snapshot_summary = fields.Text(compute="_compute_snapshot_metadata")

    _sql_constraints = [
        (
            "access_guard_template_code_version_unique",
            "unique(template_code, template_version)",
            "Template code and version must be unique together.",
        ),
    ]

    @api.model
    def _normalize_template_code(self, value):
        code = re.sub(r"[^A-Z0-9]+", "_", (value or "").upper()).strip("_")
        if not code:
            raise ValidationError(_("Template code must contain at least one letter or number."))
        return code

    @api.model
    def _generate_template_code(self, value, template_version="1.0", reserved=None):
        reserved = reserved or set()
        base = self._normalize_template_code(value or "TEMPLATE")
        code = base
        suffix = 2
        while (code, template_version) in reserved or self.with_context(active_test=False).search_count(
            [("template_code", "=", code), ("template_version", "=", template_version)], limit=1
        ):
            code = "%s_%s" % (base, suffix)
            suffix += 1
        return code

    @api.model
    def _canonical_snapshot_json(self, payload):
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @api.model
    def _pretty_snapshot_json(self, payload):
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)

    @api.model
    def _source_module_version(self):
        return (get_manifest("access_guard") or {}).get("version") or ""

    @api.model
    def _validate_snapshot_data(self, payload, template_code):
        if not template_code:
            raise ValidationError(_("Template code is required."))
        if not isinstance(payload, dict):
            raise ValidationError(_("Snapshot payload must be a JSON object."))
        if not payload.get("schema_version"):
            raise ValidationError(_("Snapshot schema_version is required."))
        missing = [section for section in SNAPSHOT_SECTIONS if section not in payload]
        if missing:
            raise ValidationError(
                _("Snapshot is missing required sections: %s") % ", ".join(missing)
            )

        def require_reference(reference, label, technical_keys):
            if not isinstance(reference, dict) or not reference.get("id"):
                raise ValidationError(_("%s has no database identity.") % label)
            if not any(reference.get(key) for key in technical_keys):
                raise ValidationError(_("%s has no semantic identity.") % label)

        for line in payload["menus"]:
            require_reference(line.get("menu"), _("Menu reference"), ("xml_id", "name"))
        for section in (
            "model_rules",
            "field_rules",
            "conditional_rules",
            "domain_rules",
            "ui_rules",
            "search_rules",
            "chatter_rules",
            "acl_rules",
        ):
            for line in payload[section]:
                require_reference(line.get("model"), _("Model reference"), ("model",))
        for line in payload["model_rules"]:
            for key, label, identity in (
                ("reports", _("Report reference"), ("xml_id", "report_name", "name")),
                ("server_actions", _("Server action reference"), ("xml_id", "name")),
                ("window_actions", _("Window action reference"), ("xml_id", "name", "res_model")),
                ("hidden_view_modes", _("View mode reference"), ("code",)),
                ("views", _("View reference"), ("xml_id", "name")),
            ):
                for reference in line.get(key, []):
                    require_reference(reference, label, identity)
        for line in payload["field_rules"]:
            for reference in line.get("fields", []):
                require_reference(reference, _("Field reference"), ("name",))
        for line in payload["conditional_rules"]:
            for reference in line.get("attr_fields", []):
                require_reference(reference, _("Conditional field reference"), ("name",))
            for element in line.get("attr_buttons", []):
                require_reference(element, _("Conditional UI element"), ("match_value",))
                require_reference(element.get("model"), _("Conditional UI model"), ("model",))
            if line.get("domain_field"):
                require_reference(line["domain_field"], _("Conditional domain field"), ("name",))
        for line in payload["ui_rules"]:
            for key in (
                "button_elements",
                "action_button_elements",
                "smart_button_elements",
                "tab_elements",
                "kanban_link_elements",
            ):
                for element in line.get(key, []):
                    require_reference(element, _("UI element reference"), ("match_value",))
                    require_reference(element.get("model"), _("UI element model"), ("model",))
        for line in payload["search_rules"]:
            for key in ("filter_items", "group_items"):
                for item in line.get(key, []):
                    require_reference(item, _("Search item reference"), ("label", "xml_id"))
                    require_reference(item.get("field"), _("Search item field"), ("model", "name"))
        for line in payload["acl_rules"]:
            require_reference(line.get("group"), _("ACL group reference"), ("xml_id", "name"))
        for picking_type in payload["overview"].get("picking_types", []):
            require_reference(picking_type, _("Picking type reference"), ("xml_id", "name", "code"))
        try:
            self._canonical_snapshot_json(payload)
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Snapshot is not JSON serializable: %s") % error) from error
        return True

    @api.model
    def _snapshot_values_from_source(self, source, template_code, schema_version=None):
        if not source or not source.exists():
            raise UserError(_("The template has no source rule to snapshot."))
        schema_version = schema_version or SNAPSHOT_SCHEMA_VERSION
        payload = source._access_guard_build_snapshot(schema_version=schema_version)
        self._validate_snapshot_data(payload, template_code)
        canonical = self._canonical_snapshot_json(payload)
        return {
            "schema_version": schema_version,
            "snapshot_payload": self._pretty_snapshot_json(payload),
            "snapshot_date": fields.Datetime.now(),
            "snapshot_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "source_module_version": self._source_module_version(),
        }

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        reserved = set()
        for incoming in vals_list:
            vals = dict(incoming)
            template_version = vals.get("template_version") or "1.0"
            code = vals.get("template_code")
            if code:
                code = self._normalize_template_code(code)
            else:
                code = self._generate_template_code(
                    vals.get("name"), template_version=template_version, reserved=reserved
                )
            vals["template_code"] = code
            reserved.add((code, template_version))
            if vals.get("source_type", "local_rule") == "local_rule":
                source = self.env["access.guard.management"].browse(vals.get("source_management_id")).exists()
                vals.update(
                    self._snapshot_values_from_source(
                        source,
                        code,
                        schema_version=vals.get("schema_version") or SNAPSHOT_SCHEMA_VERSION,
                    )
                )
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        vals = dict(vals)
        if "template_code" in vals:
            vals["template_code"] = self._normalize_template_code(vals["template_code"])
        return super().write(vals)

    def _snapshot_payload_dict(self):
        self.ensure_one()
        if not self.snapshot_payload:
            return {}
        try:
            return json.loads(self.snapshot_payload)
        except (TypeError, ValueError):
            return {}

    def _validate_snapshot_payload(self):
        for template in self:
            payload = template._snapshot_payload_dict()
            if not payload:
                raise ValidationError(_("No snapshot is stored for template %s.") % template.name)
            template._validate_snapshot_data(payload, template.template_code)
        return True

    @api.depends("snapshot_payload", "template_code", "schema_version")
    def _compute_snapshot_metadata(self):
        labels = (
            ("menus", _("Menus")),
            ("model_rules", _("Model Rules")),
            ("field_rules", _("Field Rules")),
            ("conditional_rules", _("Conditional Rules")),
            ("domain_rules", _("Domain Rules")),
            ("ui_rules", _("UI Rules")),
            ("search_rules", _("Search Rules")),
            ("chatter_rules", _("Chatter Rules")),
            ("acl_rules", _("ACL Rules")),
        )
        for template in self:
            payload = template._snapshot_payload_dict()
            try:
                template._validate_snapshot_data(payload, template.template_code)
                valid = True
            except ValidationError:
                valid = False
            template.snapshot_valid = valid
            if not payload:
                template.snapshot_summary = _("No snapshot")
                continue
            lines = ["%s: %s" % (label, len(payload.get(key, []))) for key, label in labels]
            lines.append(
                "%s: %s"
                % (_("Overview Types"), len((payload.get("overview") or {}).get("picking_types", [])))
            )
            template.snapshot_summary = "\n".join(lines)

    def action_refresh_snapshot(self):
        for template in self:
            source = template._local_source_rule()
            template.write(
                template._snapshot_values_from_source(
                    source,
                    template.template_code,
                    schema_version=template.schema_version or SNAPSHOT_SCHEMA_VERSION,
                )
            )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_view_snapshot(self):
        self.ensure_one()
        if not self.snapshot_payload:
            raise UserError(_("This template does not have a snapshot yet. Use Refresh Snapshot first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Template Snapshot"),
            "res_model": "access.guard.template",
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("npt_access_guard.view_access_guard_template_snapshot").id,
            "target": "new",
        }
