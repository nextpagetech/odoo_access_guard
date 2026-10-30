import base64
import hashlib
import json

from odoo import Command, release
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

from ..models.access_import import MAX_IMPORT_BYTES


@tagged("post_install", "-at_install")
class TestAccessGuardImport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wizard = cls.env["access.guard.import.wizard"]
        cls.Resolver = cls.env["access.guard.import.resolver"]
        cls.Template = cls.env["access.guard.template"]
        cls.env.user.write({
            "groups_id": [Command.link(cls.env.ref("npt_access_guard.group_access_guard_manager").id)]
        })
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.partner_name = cls.env["ir.model.fields"]._get("res.partner", "name")
        cls.partner_parent = cls.env["ir.model.fields"]._get("res.partner", "parent_id")
        cls.partner_view = cls.env.ref("base.view_partner_form")
        cls.partner_group = cls.env.ref("base.group_user")
        cls.partner_action = cls.env.ref("base.action_partner_form")
        cls.partner_menu = cls.env.ref("base.menu_administration")

        cls.warehouse = cls.env["stock.warehouse"].create({
            "name": "Import Validation Warehouse",
            "code": "IVW",
            "company_id": cls.env.company.id,
        })
        cls.picking_type = cls.warehouse.in_type_id
        cls._set_xml_id("import_picking_type", cls.picking_type)

    @classmethod
    def _set_xml_id(cls, name, record):
        cls.env["ir.model.data"].create({
            "module": "access_guard",
            "name": name,
            "model": record._name,
            "res_id": record.id,
        })

    def _payload(self, code="IMPORT_VALID", version="1.0"):
        return {
            "schema_version": "1.0",
            "template": {
                "code": code,
                "name": code.replace("_", " ").title(),
                "version": version,
                "category": "custom",
                "source_module_version": self.Template._source_module_version(),
            },
            "dependencies": {"required_modules": ["access_guard", "base"]},
            "general": {},
            "menus": [],
            "model_rules": [],
            "field_rules": [],
            "conditional_rules": [],
            "domain_rules": [],
            "ui_rules": [],
            "search_rules": [],
            "chatter_rules": [],
            "global": {},
            "overview": {"mode": "hide", "picking_types": []},
            "acl_rules": [],
        }

    def _hash(self, payload):
        canonical = self.Template._canonical_snapshot_json(payload)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _single_document(self, payload=None, **metadata_overrides):
        payload = payload or self._payload()
        metadata = {
            "access_guard_version": self.Template._source_module_version(),
            "exported_at": "2026-09-23T00:00:00Z",
            "exported_from_odoo_version": release.major_version,
            "portable_hash": self._hash(payload),
            "snapshot_hash": "test",
            "portability_status": "ready",
        }
        metadata.update(metadata_overrides)
        return {
            "format": "access_guard_template",
            "format_version": "1.0",
            "export_metadata": metadata,
            "dependencies": payload["dependencies"],
            "template": payload,
        }

    def _bundle_document(self, payloads):
        entries = []
        modules = set()
        for payload in payloads:
            modules.update(payload["dependencies"]["required_modules"])
            entries.append({
                "template_code": payload["template"]["code"],
                "template_version": payload["template"]["version"],
                "schema_version": payload["schema_version"],
                "portable_hash": self._hash(payload),
                "snapshot_hash": "test",
                "portability_status": "ready",
                "dependencies": payload["dependencies"],
                "template": payload,
            })
        return {
            "format": "access_guard_template_bundle",
            "format_version": "1.0",
            "export_metadata": {
                "access_guard_version": self.Template._source_module_version(),
                "exported_at": "2026-09-23T00:00:00Z",
                "exported_from_odoo_version": release.major_version,
                "template_count": len(entries),
            },
            "bundle_dependencies": {"required_modules": sorted(modules)},
            "templates": entries,
        }

    def _wizard(self, document, filename="template.json"):
        raw = json.dumps(document).encode("utf-8")
        return self.Wizard.create({
            "file_data": base64.b64encode(raw),
            "filename": filename,
        })

    def _analysis(self, payload):
        return self.Resolver.resolve_template(payload)

    def _result(self, analysis, path):
        return next(item for item in analysis["results"] if item["path"] == path)

    def test_01_valid_single_file(self):
        before = self.Template.search_count([])
        wizard = self._wizard(self._single_document(access_guard_version="18.0.0.0.0"))
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "ready")
        self.assertEqual(wizard.state, "ready")
        self.assertIn("READY_TO_IMPORT", wizard.import_plan)
        self.assertIn("older Access Guard version", wizard.validation_report)
        self.assertEqual(self.Template.search_count([]), before)

    def test_02_valid_bundle(self):
        wizard = self._wizard(self._bundle_document([
            self._payload("IMPORT_BUNDLE_A"), self._payload("IMPORT_BUNDLE_B")
        ]))
        wizard.action_validate()
        self.assertEqual(wizard.template_count, 2)
        self.assertEqual(wizard.overall_status, "ready")

    def test_03_invalid_json(self):
        wizard = self.Wizard.create({
            "file_data": base64.b64encode(b"{not-json"),
            "filename": "bad.json",
        })
        with self.assertRaisesRegex(UserError, "not valid UTF-8 JSON"):
            wizard.action_validate()

    def test_04_wrong_format(self):
        document = self._single_document()
        document["format"] = "something_else"
        with self.assertRaisesRegex(UserError, "Unsupported Access Guard import format"):
            self._wizard(document).action_validate()

    def test_05_unsupported_schema(self):
        payload = self._payload()
        payload["schema_version"] = "9.0"
        wizard = self._wizard(self._single_document(payload))
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "blocked")
        self.assertIn("unsupported", wizard.validation_report.lower())

    def test_06_wrong_odoo_major(self):
        wizard = self._wizard(self._single_document(exported_from_odoo_version="17.0"))
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "blocked")
        self.assertIn("only Odoo 18", wizard.validation_report)

    def test_07_hash_mismatch(self):
        document = self._single_document()
        document["export_metadata"]["portable_hash"] = "0" * 64
        wizard = self._wizard(document)
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "blocked")
        self.assertIn("hash mismatch", wizard.validation_report)

    def test_08_missing_required_module(self):
        payload = self._payload()
        payload["dependencies"]["required_modules"].append("access_guard_missing_test_module")
        payload["dependencies"]["required_modules"].sort()
        wizard = self._wizard(self._single_document(payload))
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "blocked")
        self.assertIn("access_guard_missing_test_module", wizard.validation_report)

    def test_09_model_resolution(self):
        payload = self._payload()
        payload["chatter_rules"] = [{"model": {"model": "res.partner"}}]
        result = self._result(self._analysis(payload), "chatter_rules.0.model")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["target_id"], self.partner_model.id)

    def test_10_missing_model(self):
        payload = self._payload()
        payload["chatter_rules"] = [{"model": {"model": "x.missing.model"}}]
        result = self._result(self._analysis(payload), "chatter_rules.0.model")
        self.assertEqual(result["status"], "blocked")

    def test_11_field_resolution(self):
        payload = self._payload()
        payload["field_rules"] = [{
            "model": {"model": "res.partner"},
            "fields": [{"model": "res.partner", "field": "name"}],
        }]
        result = self._result(self._analysis(payload), "field_rules.0.fields.0")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["target_id"], self.partner_name.id)

    def test_12_missing_field(self):
        payload = self._payload()
        payload["field_rules"] = [{
            "model": {"model": "res.partner"},
            "fields": [{"model": "res.partner", "field": "x_missing"}],
        }]
        self.assertEqual(
            self._result(self._analysis(payload), "field_rules.0.fields.0")["status"],
            "blocked",
        )

    def test_13_xml_id_resolution(self):
        payload = self._payload()
        payload["menus"] = [{"menu": {"xml_id": self.partner_menu.get_external_id()[self.partner_menu.id]}}]
        result = self._result(self._analysis(payload), "menus.0.menu")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["target_id"], self.partner_menu.id)

    def test_14_missing_xml_id(self):
        payload = self._payload()
        payload["menus"] = [{"menu": {"xml_id": None, "name": "Map Me"}}]
        result = self._result(self._analysis(payload), "menus.0.menu")
        self.assertEqual(result["status"], "mapping_required")

    def test_15_action_xml_id_resolution(self):
        payload = self._payload()
        action_xml_id = self.partner_action.get_external_id()[self.partner_action.id]
        payload["model_rules"] = [{
            "model": {"model": "res.partner"},
            "reports": [], "server_actions": [], "hidden_view_modes": [], "views": [],
            "window_actions": [{"xml_id": action_xml_id, "action_type": "ir.actions.act_window"}],
        }]
        result = self._result(self._analysis(payload), "model_rules.0.window_actions.0")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["target_id"], self.partner_action.id)

    def test_16_ui_name_method_resolution(self):
        element = self.env["access.guard.ui.element"].create({
            "model_id": self.partner_model.id,
            "type": "button",
            "match_mode": "name",
            "match_value": "action_import_validation",
            "label": "Validation Button",
            "view_type": "form",
            "view_id": self.partner_view.id,
        })
        payload = self._payload()
        payload["ui_rules"] = [{
            "model": {"model": "res.partner"},
            "button_elements": [{
                "model": "res.partner", "type": "button", "view_type": "form",
                "match_mode": "name", "match_value": "action_import_validation",
                "attribute_name": "", "view_xml_id": "base.view_partner_form",
                "label": "Validation Button", "active": True,
            }],
            "action_button_elements": [], "smart_button_elements": [],
            "tab_elements": [], "kanban_link_elements": [],
        }]
        result = self._result(self._analysis(payload), "ui_rules.0.button_elements.0")
        self.assertEqual(result["target_id"], element.id)

    def test_17_ui_action_xml_id_resolution(self):
        action_xml_id = self.partner_action.get_external_id()[self.partner_action.id]
        payload = self._payload()
        payload["ui_rules"] = [{
            "model": {"model": "res.partner"}, "button_elements": [],
            "action_button_elements": [{
                "model": "res.partner", "type": "action_button", "view_type": "form",
                "match_mode": "action_id", "match_value": action_xml_id,
                "attribute_name": "", "view_xml_id": "base.view_partner_form",
                "label": "Partners", "active": True,
                "action": {"xml_id": action_xml_id, "action_type": "ir.actions.act_window"},
            }],
            "smart_button_elements": [], "tab_elements": [], "kanban_link_elements": [],
        }]
        analysis = self._analysis(payload)
        action = self._result(analysis, "ui_rules.0.action_button_elements.0.action")
        element = self._result(analysis, "ui_rules.0.action_button_elements.0")
        self.assertEqual(action["target_id"], self.partner_action.id)
        self.assertEqual(element["status"], "resolvable_on_import")

    def test_18_fragile_string_ui_warning(self):
        payload = self._payload()
        payload["conditional_rules"] = [{
            "model": {"model": "res.partner"}, "attr_fields": [], "domain_field": None,
            "attr_buttons": [{
                "model": "res.partner", "type": "button", "view_type": "form",
                "match_mode": "string", "match_value": "Fragile Label", "attribute_name": "",
                "view_xml_id": None, "label": "Fragile Label", "active": True,
            }],
        }]
        result = self._result(self._analysis(payload), "conditional_rules.0.attr_buttons.0")
        self.assertEqual(result["status"], "warning")
        self.assertIn("language-sensitive", result["message"])

    def test_19_xpath_warning(self):
        payload = self._payload()
        payload["ui_rules"] = [{
            "model": {"model": "res.partner"},
            "button_elements": [{
                "model": "res.partner", "type": "button", "view_type": "form",
                "match_mode": "xpath", "match_value": "//button[1]", "attribute_name": "",
                "view_xml_id": None, "label": "First Button", "active": True,
            }],
            "action_button_elements": [], "smart_button_elements": [],
            "tab_elements": [], "kanban_link_elements": [],
        }]
        result = self._result(self._analysis(payload), "ui_rules.0.button_elements.0")
        self.assertEqual(result["status"], "warning")
        self.assertIn("XPath", result["message"])

    def test_20_picking_type_xml_id_resolution(self):
        payload = self._payload()
        payload["overview"]["picking_types"] = [{"xml_id": "npt_access_guard.import_picking_type"}]
        result = self._result(self._analysis(payload), "overview.picking_types.0")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["target_id"], self.picking_type.id)

    def _picking_fallback(self, **overrides):
        values = {
            "xml_id": None,
            "code": self.picking_type.code,
            "sequence_code": self.picking_type.sequence_code,
            "warehouse": {"code": self.warehouse.code, "name": self.warehouse.name},
            "company": {"name": self.env.company.name},
            "name": self.picking_type.name,
        }
        values.update(overrides)
        return values

    def test_21_picking_type_fallback_exact_match(self):
        payload = self._payload()
        payload["overview"]["picking_types"] = [self._picking_fallback()]
        result = self._result(self._analysis(payload), "overview.picking_types.0")
        self.assertEqual(result["status"], "resolved_with_fallback")
        self.assertEqual(result["target_id"], self.picking_type.id)

    def test_22_picking_type_fallback_ambiguous(self):
        duplicate = self.picking_type.copy({
            "name": "Ambiguous Import Type",
            "code": self.picking_type.code,
            "warehouse_id": self.warehouse.id,
            "company_id": self.env.company.id,
            "sequence_code": self.picking_type.sequence_code,
        })
        payload = self._payload()
        payload["overview"]["picking_types"] = [self._picking_fallback()]
        result = self._result(self._analysis(payload), "overview.picking_types.0")
        self.assertEqual(result["status"], "ambiguous")
        duplicate.unlink()

    def test_23_picking_type_fallback_missing(self):
        payload = self._payload()
        payload["overview"]["picking_types"] = [self._picking_fallback(code="internal", sequence_code="NO_MATCH")]
        result = self._result(self._analysis(payload), "overview.picking_types.0")
        self.assertEqual(result["status"], "mapping_required")

    def test_24_domain_ready(self):
        analysis = self.Resolver._new_analysis()
        result = self.Resolver.resolve_domain(
            analysis, "[('name', '=', 'Ready')]", "res.partner", "domain", "Domain"
        )
        self.assertEqual(result["status"], "resolved")

    def test_25_relational_numeric_domain_mapping(self):
        analysis = self.Resolver._new_analysis()
        result = self.Resolver.resolve_domain(
            analysis, "[('parent_id', '=', 7)]", "res.partner", "domain", "Domain"
        )
        self.assertEqual(result["status"], "mapping_required")
        self.assertEqual(result["target_model"], "res.partner")

    def test_26_invalid_domain_blocked(self):
        analysis = self.Resolver._new_analysis()
        syntax = self.Resolver.resolve_domain(
            analysis, "[('name', '=',]", "res.partner", "syntax", "Domain"
        )
        self.assertEqual(syntax["status"], "blocked")
        analysis = self.Resolver._new_analysis()
        field = self.Resolver.resolve_domain(
            analysis, "[('x_missing', '=', 1)]", "res.partner", "field", "Domain"
        )
        self.assertEqual(field["status"], "blocked")

    def _existing_template(self, code="IMPORT_DUPLICATE", version="1.0"):
        source = self.env["access.guard.management"].create({"name": code})
        return self.Template.create({
            "name": code,
            "template_code": code,
            "template_version": version,
            "source_management_id": source.id,
        })

    def test_27_duplicate_template_exact_match(self):
        template = self._existing_template("IMPORT_EXACT_DUPLICATE")
        status, operation, existing = self.Wizard._duplicate_status(
            template.template_code, template.template_version, template.portable_hash
        )
        self.assertEqual((status, operation, existing),
                         ("exact_duplicate", "SKIP_EXACT_DUPLICATE", template))

    def test_28_duplicate_version_conflict(self):
        template = self._existing_template("IMPORT_VERSION_CONFLICT")
        status, operation, _existing = self.Wizard._duplicate_status(
            template.template_code, template.template_version, "0" * 64
        )
        self.assertEqual(status, "version_conflict")
        self.assertEqual(operation, "BLOCK_VERSION_CONFLICT")

    def test_29_same_code_different_version(self):
        template = self._existing_template("IMPORT_OTHER_VERSION", "1.0")
        status, operation, _existing = self.Wizard._duplicate_status(
            template.template_code, "2.0", "0" * 64
        )
        self.assertEqual(status, "existing_other_version")
        self.assertEqual(operation, "REVIEW_EXISTING_OTHER_VERSION")

    def test_30_bundle_mixed_statuses(self):
        document = self._bundle_document([
            self._payload("IMPORT_MIXED_READY"), self._payload("IMPORT_MIXED_BAD")
        ])
        document["templates"][1]["portable_hash"] = "0" * 64
        wizard = self._wizard(document)
        wizard.action_validate()
        self.assertEqual(wizard.template_count, 2)
        self.assertEqual(set(wizard.result_line_ids.mapped("status")), {"ready", "blocked"})

    def test_31_manual_mapping_completes_validation(self):
        payload = self._payload("IMPORT_MANUAL_MAPPING")
        payload["menus"] = [{
            "sequence": 10,
            "menu": {"xml_id": None, "name": "Source Menu"},
            "include_children": False,
        }]
        wizard = self._wizard(self._single_document(payload))
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "mapping_required")
        line = wizard.mapping_line_ids.filtered(lambda item: item.reference_type == "menu")
        self.assertEqual(len(line), 1)
        line.target_menu_id = self.partner_menu
        wizard.action_validate_again()
        self.assertEqual(wizard.overall_status, "ready")
        self.assertFalse(wizard.mapping_line_ids)
        self.assertIn(str(self.partner_menu.id), wizard.import_plan)

    def test_32_normal_user_cannot_access_import(self):
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Import Validation Normal User",
            "login": "access.guard.import.normal@example.com",
            "groups_id": [Command.set([self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(AccessError):
            self.Wizard.with_user(user).create({"filename": "forbidden.json"})
        with self.assertRaises(AccessError):
            self.Resolver.with_user(user).resolve_template(self._payload("FORBIDDEN_RESOLVER"))

    def test_33_file_size_limit(self):
        wizard = self.Wizard.create({
            "file_data": base64.b64encode(b"x" * (MAX_IMPORT_BYTES + 1)),
            "filename": "too-large.json",
        })
        with self.assertRaisesRegex(UserError, "too large"):
            wizard.action_validate()

    def test_34_dynamic_domain_and_no_record_creation(self):
        analysis = self.Resolver._new_analysis()
        result = self.Resolver.resolve_domain(
            analysis,
            "[('create_uid', '=', uid), ('company_id', 'in', company_ids)]",
            "res.partner",
            "dynamic",
            "Dynamic domain",
        )
        self.assertEqual(result["status"], "resolved")
        payload = self._payload("IMPORT_NO_CREATION")
        before = self.Template.search_count([])
        self._wizard(self._single_document(payload)).action_validate()
        self.assertEqual(self.Template.search_count([]), before)

    def _import_payload(self, payload, filename="template.json"):
        wizard = self._wizard(self._single_document(payload), filename=filename)
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "ready")
        wizard.action_import_templates()
        self.assertEqual(wizard.state, "imported")
        return wizard, wizard.imported_template_ids

    def _full_payload(self, code="IMPORT_APPLY_FULL"):
        payload = self._payload(code)
        menu_xml_id = self.partner_menu.get_external_id()[self.partner_menu.id]
        action_xml_id = self.partner_action.get_external_id()[self.partner_action.id]
        payload.update({
            "general": {
                "disable_developer_mode": True,
                "read_only": True,
                "apply_without_company": False,
                "apply_by_group": True,
            },
            "menus": [{
                "sequence": 5,
                "menu": {"xml_id": menu_xml_id},
                "include_children": False,
            }],
            "model_rules": [{
                "sequence": 10,
                "model": {"model": "res.partner"},
                "reports": [],
                "server_actions": [],
                "window_actions": [{
                    "xml_id": action_xml_id,
                    "action_type": "ir.actions.act_window",
                }],
                "hidden_view_modes": [{"code": "list"}],
                "views": [{"xml_id": "base.view_partner_form"}],
                "hide_create": True,
                "hide_edit": True,
                "hide_delete": False,
                "hide_archive": False,
                "hide_duplicate": True,
                "hide_import": True,
                "hide_export": True,
                "hide_spreadsheet": False,
            }],
            "field_rules": [{
                "sequence": 10,
                "model": {"model": "res.partner"},
                "fields": [{"model": "res.partner", "field": "name"}],
                "invisible": False,
                "read_only": True,
                "required": False,
                "remove_create_option": True,
                "remove_edit_option": True,
                "remove_external_link": True,
            }],
            "conditional_rules": [{
                "sequence": 10,
                "model": {"model": "res.partner"},
                "apply_attrs": True,
                "attr_fields": [{"model": "res.partner", "field": "name"}],
                "attr_buttons": [],
                "attr_type": "readonly",
                "attrs_domain": "[('is_company', '=', True)]",
                "apply_domain": True,
                "domain_field": {"model": "res.partner", "field": "parent_id"},
                "domain_expression": "[('is_company', '=', True)]",
            }],
            "domain_rules": [{
                "sequence": 10,
                "model": {"model": "res.partner"},
                "soft_restrict": True,
                "apply_relational_fields": True,
                "perm_read": True,
                "perm_create": False,
                "perm_write": False,
                "perm_delete": False,
                "apply_filter": True,
                "domain_expression": "[('name', '!=', False)]",
            }],
            "ui_rules": [{
                "sequence": 10,
                "model": {"model": "res.partner"},
                "button_elements": [{
                    "model": "res.partner",
                    "type": "button",
                    "view_type": "form",
                    "match_mode": "name",
                    "match_value": "action_access_guard_imported_test",
                    "attribute_name": "",
                    "view_xml_id": "base.view_partner_form",
                    "label": "Imported Test Button",
                    "active": True,
                }],
                "action_button_elements": [],
                "smart_button_elements": [],
                "tab_elements": [],
                "kanban_link_elements": [],
            }],
            "search_rules": [{
                "sequence": 10,
                "model": {"model": "res.partner"},
                "hide_search_panel": False,
                "filter_items": [{"model": "res.partner", "field": "name", "type": "filter"}],
                "group_items": [],
                "hide_custom_filter": True,
                "hide_custom_group": False,
                "hide_delete_favorite": True,
            }],
            "chatter_rules": [{
                "sequence": 10,
                "model": {"model": "res.partner"},
                "hide_chatter": True,
                "hide_send_message": True,
                "hide_log_note": False,
                "hide_schedule_activity": True,
            }],
            "global": {
                "hide_chatter": True,
                "hide_send_message": True,
                "hide_import": True,
                "disable_login": False,
            },
            "overview": {
                "mode": "show",
                "picking_types": [{"xml_id": "npt_access_guard.import_picking_type"}],
            },
            "acl_rules": [{
                "model": "res.partner",
                "group_xml_id": "base.group_user",
                "perm_read": True,
                "perm_write": True,
                "perm_create": False,
                "perm_unlink": False,
            }],
        })
        return payload

    def _apply_imported(self, template, **overrides):
        target_user = self.env.ref("base.user_admin")
        values = {
            "template_id": template.id,
            "name": "%s Applied" % template.name,
            "user_ids": [Command.set(target_user.ids)],
            "group_ids": [Command.set(self.partner_group.ids)],
            "company_ids": [Command.set(self.env.company.ids)],
            "active": True,
            "make_read_only": True,
            "copy_acl_lines": True,
        }
        values.update(overrides)
        wizard = self.env["access.guard.apply.template.wizard"].create(values)
        wizard.action_preview_validate()
        action = wizard.action_apply_template()
        return wizard, self.env["access.guard.management"].browse(action["res_id"])

    def test_35_actual_import_is_inert_and_preserves_origin(self):
        payload = self._payload("IMPORT_ACTUAL_INERT")
        document = self._single_document(payload)
        document["export_metadata"].update({
            "template_description": "Imported safely",
            "portability_report": "Source portability report",
        })
        wizard = self._wizard(document, filename="inert.json")
        management_before = self.env["access.guard.management"].search_count([])
        acl_before = self.env["ir.model.access"].sudo().search_count([])
        wizard.action_validate()
        wizard.action_import_templates()
        template = wizard.imported_template_ids
        self.assertEqual(len(template), 1)
        self.assertEqual(template.source_type, "imported")
        self.assertFalse(template.source_management_id)
        self.assertEqual(template.imported_filename, "inert.json")
        self.assertEqual(template.description, "Imported safely")
        self.assertEqual(template.portability_report, "Source portability report")
        self.assertEqual(template.portable_hash, self._hash(payload))
        self.assertEqual(json.loads(template.portable_payload), payload)
        self.assertEqual(template.resolution_status, "ready")
        self.assertTrue(template.resolution_hash)
        self.assertEqual(self.env["access.guard.management"].search_count([]), management_before)
        self.assertEqual(self.env["ir.model.access"].sudo().search_count([]), acl_before)
        library_action = self.env.ref("npt_access_guard.action_access_guard_template")
        self.assertFalse(library_action.domain)
        self.assertIn(template, self.Template.search([]))
        open_action = wizard.action_open_imported_templates()
        self.assertEqual(open_action["res_model"], "access.guard.template")
        self.assertEqual(open_action["res_id"], template.id)
        self.assertEqual(open_action["view_mode"], "form")
        local_rule = self.env["access.guard.management"].create({"name": "Library Local Comparison"})
        local = self.Template.browse(local_rule.action_create_template()["res_id"])
        library_templates = self.Template.search([])
        self.assertIn(local, library_templates)
        self.assertIn(template, library_templates)

    def test_36_exact_duplicate_is_skipped(self):
        payload = self._payload("IMPORT_EXEC_DUPLICATE")
        _wizard, first = self._import_payload(payload)
        second_wizard = self._wizard(self._single_document(payload))
        second_wizard.action_validate()
        self.assertEqual(second_wizard.overall_status, "exact_duplicate")
        second_wizard.action_import_templates()
        self.assertFalse(second_wizard.imported_template_ids)
        self.assertEqual(second_wizard.skipped_template_ids, first)
        self.assertEqual(second_wizard.skipped_count, 1)
        self.assertIn("origin is unchanged", second_wizard.import_result_summary)
        self.assertEqual(second_wizard.action_open_imported_templates()["res_id"], first.id)
        self.assertEqual(second_wizard.action_open_imported_templates()["name"], "Existing Template")

    def test_37_other_version_is_created_and_conflict_is_blocked(self):
        payload_v1 = self._payload("IMPORT_EXEC_VERSION", "1.0")
        self._import_payload(payload_v1)
        payload_v2 = self._payload("IMPORT_EXEC_VERSION", "2.0")
        wizard_v2, template_v2 = self._import_payload(payload_v2)
        self.assertEqual(template_v2.template_version, "2.0")
        self.assertEqual(wizard_v2.imported_count, 1)
        changed = self._payload("IMPORT_EXEC_VERSION", "2.0")
        changed["global"] = {"hide_export": True}
        conflict = self._wizard(self._single_document(changed))
        conflict.action_validate()
        self.assertEqual(conflict.overall_status, "blocked")
        with self.assertRaises(UserError):
            conflict.action_import_templates()

    def test_38_bundle_import_is_atomic_when_revalidation_blocks(self):
        ready = self._payload("IMPORT_ATOMIC_READY")
        conflict_payload = self._payload("IMPORT_ATOMIC_CONFLICT")
        document = self._bundle_document([ready, conflict_payload])
        wizard = self._wizard(document)
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "ready")
        source = self.env["access.guard.management"].create({"name": "Race Conflict"})
        self.Template.create({
            "name": "Race Conflict",
            "template_code": "IMPORT_ATOMIC_CONFLICT",
            "template_version": "1.0",
            "source_management_id": source.id,
        })
        before = self.Template.search_count([("template_code", "=", "IMPORT_ATOMIC_READY")])
        with self.assertRaises(UserError):
            wizard.action_import_templates()
        self.assertEqual(
            self.Template.search_count([("template_code", "=", "IMPORT_ATOMIC_READY")]), before
        )

    def test_39_apply_imported_rebuilds_all_sections_and_acl(self):
        _import_wizard, template = self._import_payload(self._full_payload())
        acl_before = self.env["ir.model.access"].sudo().search_count([])
        apply_wizard, rule = self._apply_imported(template)
        self.assertEqual(apply_wizard.validation_status, "ready")
        self.assertIn("READY TO APPLY", apply_wizard.preview_html)
        self.assertIn("ACL grants", apply_wizard.preview_html)
        self.assertEqual(rule.template_id, template)
        self.assertEqual(template.assigned_rule_count, 1)
        self.assertEqual(template.action_open_assigned_rules()["domain"], [("template_id", "=", template.id)])
        self.assertEqual(rule.user_ids, self.env.ref("base.user_admin"))
        self.assertIn(self.partner_group, rule.group_ids)
        self.assertIn(self.env.company, rule.company_ids)
        self.assertTrue(rule.active)
        self.assertTrue(rule.read_only)
        self.assertTrue(rule.disable_developer_mode)
        self.assertEqual(len(rule.menu_line_ids), 1)
        self.assertEqual(len(rule.model_rule_ids), 1)
        self.assertEqual(len(rule.field_rule_ids), 1)
        self.assertEqual(len(rule.field_conditional_ids), 1)
        self.assertEqual(len(rule.domain_rule_ids), 1)
        self.assertEqual(len(rule.ui_rule_ids), 1)
        self.assertEqual(len(rule.search_panel_rule_ids), 1)
        self.assertEqual(len(rule.chatter_rule_ids), 1)
        self.assertTrue(rule.global_hide_chatter)
        self.assertTrue(rule.global_hide_import)
        self.assertEqual(rule.overview_mode, "show")
        self.assertEqual(rule.overview_picking_type_ids, self.picking_type)
        self.assertEqual(len(rule.acl_rule_ids), 1)
        self.assertTrue(rule.acl_rule_ids.access_id)
        self.assertEqual(self.env["ir.model.access"].sudo().search_count([]), acl_before + 1)

    def test_40_helper_records_are_created_once_and_reused(self):
        payload = self._full_payload("IMPORT_HELPER_REUSE")
        _wizard, template = self._import_payload(payload)
        ui_domain = [("match_value", "=", "action_access_guard_imported_test")]
        ui_before = self.env["access.guard.ui.element"].with_context(active_test=False).search_count(ui_domain)
        search_before = self.env["access.guard.search.item"].with_context(active_test=False).search_count([
            ("model_id", "=", self.partner_model.id),
            ("field_id", "=", self.partner_name.id),
            ("type", "=", "filter"),
        ])
        _first_wizard, first = self._apply_imported(template, copy_acl_lines=False)
        self.assertEqual(
            self.env["access.guard.ui.element"].with_context(active_test=False).search_count(ui_domain),
            ui_before + 1,
        )
        self.assertEqual(
            self.env["access.guard.search.item"].with_context(active_test=False).search_count([
                ("model_id", "=", self.partner_model.id),
                ("field_id", "=", self.partner_name.id),
                ("type", "=", "filter"),
            ]),
            search_before + 1,
        )
        _second_wizard, second = self._apply_imported(template, copy_acl_lines=False)
        self.assertEqual(first.ui_rule_ids.button_element_ids, second.ui_rule_ids.button_element_ids)
        self.assertEqual(first.search_panel_rule_ids.filter_item_ids, second.search_panel_rule_ids.filter_item_ids)

    def test_41_manual_relational_domain_is_rebuilt_with_target_id(self):
        payload = self._payload("IMPORT_DOMAIN_MAPPING_APPLY")
        payload["domain_rules"] = [{
            "sequence": 10,
            "model": {"model": "res.partner"},
            "soft_restrict": False,
            "apply_relational_fields": False,
            "perm_read": True,
            "perm_create": True,
            "perm_write": True,
            "perm_delete": True,
            "apply_filter": True,
            "domain_expression": "[('parent_id', '=', 987654)]",
        }]
        target = self.env["res.partner"].create({"name": "Mapped Domain Target"})
        wizard = self._wizard(self._single_document(payload))
        wizard.action_validate()
        mapping = wizard.mapping_line_ids.filtered(lambda item: item.reference_type == "domain_record")
        self.assertEqual(len(mapping), 1)
        mapping.write({"target_record_id": target.id})
        wizard.action_validate_again()
        self.assertEqual(wizard.overall_status, "ready")
        wizard.action_import_templates()
        template = wizard.imported_template_ids
        _apply_wizard, rule = self._apply_imported(template, copy_acl_lines=False)
        self.assertIn(str(target.id), rule.domain_rule_ids.domain_expression)
        self.assertNotIn("987654", rule.domain_rule_ids.domain_expression)

    def test_42_missing_domain_mapping_blocks_import_and_apply(self):
        payload = self._payload("IMPORT_DOMAIN_MAPPING_BLOCK")
        payload["domain_rules"] = [{
            "model": {"model": "res.partner"},
            "domain_expression": "[('parent_id', '=', 777777)]",
        }]
        wizard = self._wizard(self._single_document(payload))
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "mapping_required")
        with self.assertRaises(UserError):
            wizard.action_import_templates()

    def test_43_imported_reexport_preserves_hash_and_excludes_resolution(self):
        payload = self._full_payload("IMPORT_REEXPORT")
        _wizard, template = self._import_payload(payload)
        document, _filename = template._build_export_document()
        self.assertEqual(document["export_metadata"]["portable_hash"], self._hash(payload))
        self.assertEqual(document["template"], payload)
        serialized = json.dumps(document)
        self.assertNotIn("resolution_payload", serialized)
        self.assertNotIn("target_id", serialized)
        self.assertNotIn("imported_at", serialized)

    def test_44_non_manager_cannot_apply_imported_template(self):
        _wizard, template = self._import_payload(self._payload("IMPORT_APPLY_SECURITY"))
        normal_user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Imported Apply Normal User",
            "login": "access.guard.import.apply.normal@example.com",
            "groups_id": [Command.set([self.env.ref("base.group_user").id])],
        })
        apply_wizard = self.env["access.guard.apply.template.wizard"].create({
            "template_id": template.id,
            "user_ids": [Command.set(normal_user.ids)],
        })
        with self.assertRaises(AccessError):
            apply_wizard.with_user(normal_user).action_preview_validate()

    def test_45_apply_failure_rolls_back_helpers_rule_and_acl(self):
        payload = self._full_payload("IMPORT_APPLY_ROLLBACK")
        payload["menus"].append(dict(payload["menus"][0]))
        _wizard, template = self._import_payload(payload)
        helper_domain = [("match_value", "=", "action_access_guard_imported_test")]
        helper_before = self.env["access.guard.ui.element"].with_context(active_test=False).search_count(
            helper_domain
        )
        rules_before = self.env["access.guard.management"].search_count([])
        access_before = self.env["ir.model.access"].sudo().search_count([])
        apply_wizard = self.env["access.guard.apply.template.wizard"].create({
            "template_id": template.id,
            "user_ids": [Command.set(self.env.ref("base.user_admin").ids)],
            "copy_acl_lines": True,
        })
        apply_wizard.action_preview_validate()
        with mute_logger("odoo.sql_db"), self.assertRaises(Exception):
            apply_wizard.action_apply_template()
        self.assertEqual(self.env["access.guard.management"].search_count([]), rules_before)
        self.assertEqual(self.env["ir.model.access"].sudo().search_count([]), access_before)
        self.assertEqual(
            self.env["access.guard.ui.element"].with_context(active_test=False).search_count(helper_domain),
            helper_before,
        )

    def test_46_acl_failure_rolls_back_generated_access_and_helpers(self):
        payload = self._full_payload("IMPORT_ACL_ROLLBACK")
        payload["acl_rules"].append(dict(payload["acl_rules"][0]))
        _wizard, template = self._import_payload(payload)
        helper_domain = [("match_value", "=", "action_access_guard_imported_test")]
        helper_before = self.env["access.guard.ui.element"].with_context(active_test=False).search_count(
            helper_domain
        )
        rules_before = self.env["access.guard.management"].search_count([])
        access_before = self.env["ir.model.access"].sudo().search_count([])
        apply_wizard = self.env["access.guard.apply.template.wizard"].create({
            "template_id": template.id,
            "user_ids": [Command.set(self.env.ref("base.user_admin").ids)],
            "copy_acl_lines": True,
        })
        apply_wizard.action_preview_validate()
        with mute_logger("odoo.sql_db"), self.assertRaises(Exception):
            apply_wizard.action_apply_template()
        self.assertEqual(self.env["access.guard.management"].search_count([]), rules_before)
        self.assertEqual(self.env["ir.model.access"].sudo().search_count([]), access_before)
        self.assertEqual(
            self.env["access.guard.ui.element"].with_context(active_test=False).search_count(helper_domain),
            helper_before,
        )

    def test_47_helper_conflict_blocks_apply_without_partial_rule(self):
        payload = self._full_payload("IMPORT_HELPER_CONFLICT")
        _wizard, template = self._import_payload(payload)
        self.env["access.guard.ui.element"].create({
            "model_id": self.partner_model.id,
            "type": "button",
            "view_type": "list",
            "match_mode": "name",
            "match_value": "action_access_guard_imported_test",
            "attribute_name": "",
            "label": "Conflicting Imported Test Button",
        })
        rules_before = self.env["access.guard.management"].search_count([])
        apply_wizard = self.env["access.guard.apply.template.wizard"].create({
            "template_id": template.id,
            "user_ids": [Command.set(self.env.ref("base.user_admin").ids)],
            "copy_acl_lines": False,
        })
        apply_wizard.action_preview_validate()
        with self.assertRaisesRegex(UserError, "conflicting UI helper"):
            apply_wizard.action_apply_template()
        self.assertEqual(self.env["access.guard.management"].search_count([]), rules_before)

    def test_48_inactive_imported_ui_helper_remains_inactive(self):
        payload = self._full_payload("IMPORT_INACTIVE_HELPER")
        payload["ui_rules"][0]["button_elements"][0].update({
            "match_value": "action_access_guard_inactive_test",
            "label": "Inactive Imported Button",
            "active": False,
        })
        _wizard, template = self._import_payload(payload)
        _apply_wizard, rule = self._apply_imported(template, copy_acl_lines=False)
        helper = self.env["access.guard.ui.element"].with_context(active_test=False).search([
            ("model_id", "=", self.partner_model.id),
            ("match_value", "=", "action_access_guard_inactive_test"),
        ])
        self.assertEqual(len(helper), 1)
        self.assertFalse(helper.active)
        self.assertIn(helper, rule.ui_rule_ids.with_context(active_test=False).button_element_ids)

    def test_49_local_export_import_apply_roundtrip(self):
        source = self.env["access.guard.management"].create({
            "name": "Roundtrip Source Rule",
            "category": "sales",
            "global_hide_export": True,
            "global_hide_custom_filter": True,
            "menu_line_ids": [Command.create({
                "menu_id": self.partner_menu.id,
                "sequence": 7,
            })],
            "model_rule_ids": [Command.create({
                "model_id": self.partner_model.id,
                "hide_create": True,
                "hide_export": True,
            })],
            "field_rule_ids": [Command.create({
                "model_id": self.partner_model.id,
                "field_ids": [Command.set(self.partner_name.ids)],
                "read_only": True,
                "remove_external_link": True,
            })],
            "domain_rule_ids": [Command.create({
                "model_id": self.partner_model.id,
                "apply_filter": True,
                "domain_expression": "[('name', '!=', False)]",
            })],
        })
        local_template = self.Template.create({
            "name": "Roundtrip Template",
            "template_code": "IMPORT_ROUNDTRIP",
            "template_version": "1.0",
            "source_management_id": source.id,
            "description": "Roundtrip description",
        })
        document, _filename = local_template._build_export_document()
        original_hash = local_template.portable_hash
        original_snapshot = source._access_guard_build_snapshot()
        local_template.unlink()

        import_wizard = self._wizard(document, filename="roundtrip.json")
        import_wizard.action_validate()
        import_wizard.action_import_templates()
        imported = import_wizard.imported_template_ids
        self.assertEqual(imported.portable_hash, original_hash)
        self.assertEqual(imported.description, "Roundtrip description")
        _apply_wizard, applied = self._apply_imported(imported, copy_acl_lines=False)
        applied_snapshot = applied._access_guard_build_snapshot()
        for section in ("menus", "model_rules", "field_rules", "domain_rules", "global", "overview"):
            self.assertEqual(applied_snapshot[section], original_snapshot[section], section)

    def test_50_ui_view_without_xml_id_requires_explicit_mapping(self):
        payload = self._payload("IMPORT_UI_VIEW_MAPPING")
        payload["ui_rules"] = [{
            "model": {"model": "res.partner"},
            "button_elements": [{
                "model": "res.partner",
                "type": "button",
                "view_type": "form",
                "match_mode": "name",
                "match_value": "action_ui_view_mapping_test",
                "attribute_name": "",
                "view_xml_id": None,
                "view_fallback": {"name": "Source Partner View", "type": "form"},
                "label": "Mapped View Button",
                "active": True,
            }],
            "action_button_elements": [],
            "smart_button_elements": [],
            "tab_elements": [],
            "kanban_link_elements": [],
        }]
        wizard = self._wizard(self._single_document(payload))
        wizard.action_validate()
        self.assertEqual(wizard.overall_status, "mapping_required")
        mapping = wizard.mapping_line_ids.filtered(lambda line: line.reference_type == "view")
        self.assertEqual(len(mapping), 1)
        mapping.target_view_id = self.partner_view
        wizard.action_validate_again()
        self.assertEqual(wizard.overall_status, "ready")
        wizard.action_import_templates()
        _apply_wizard, rule = self._apply_imported(wizard.imported_template_ids, copy_acl_lines=False)
        self.assertEqual(rule.ui_rule_ids.button_element_ids.view_id, self.partner_view)

    def _resolve_test_domain(self, expression):
        return self.Resolver.resolve_domain(
            self.Resolver._new_analysis(), expression, "res.partner", "domain", "Domain"
        )

    def test_51_and_domain_operator(self):
        result = self._resolve_test_domain("['&', ('name', '=', 'Approved'), ('create_uid', '=', uid)]")
        self.assertEqual(result["status"], "resolved")

    def test_52_or_domain_operator(self):
        result = self._resolve_test_domain("['|', ('create_uid', '=', uid), ('parent_id.name', '=', 'Parent')]")
        self.assertEqual(result["status"], "resolved")

    def test_53_not_domain_operator(self):
        result = self._resolve_test_domain("['!', ('name', '=', 'Cancelled')]")
        self.assertEqual(result["status"], "resolved")

    def test_54_nested_domain_operators(self):
        result = self._resolve_test_domain(
            "['&', '|', ('name', '=', 'A'), ('parent_id.name', '=', 'B'), '!',"
            " ('active', '=', False)]"
        )
        self.assertEqual(result["status"], "resolved")

    def test_55_operator_with_numeric_relational_value_requires_mapping(self):
        result = self._resolve_test_domain("['|', ('name', '=', 'A'), ('parent_id', '=', 987654)]")
        self.assertEqual(result["status"], "mapping_required")
        self.assertEqual(result["target_model"], "res.partner")

    def test_56_unknown_field_under_operator_is_blocked(self):
        result = self._resolve_test_domain("['&', ('name', '=', 'A'), ('non_existing_field', '=', True)]")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("non_existing_field", result["message"])

    def test_57_context_dynamic_domain(self):
        result = self._resolve_test_domain("[('name', '=', context.get('partner_name'))]")
        self.assertEqual(result["status"], "resolved")
