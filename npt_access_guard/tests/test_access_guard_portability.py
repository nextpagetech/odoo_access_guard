import hashlib
import json

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAccessGuardPortability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Management = cls.env["access.guard.management"]
        cls.Template = cls.env["access.guard.template"]
        cls.ModelData = cls.env["ir.model.data"]
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.name_field = cls.env["ir.model.fields"]._get("res.partner", "name")
        cls.comment_field = cls.env["ir.model.fields"]._get("res.partner", "comment")
        cls.parent_field = cls.env["ir.model.fields"]._get("res.partner", "parent_id")
        cls.partner_view = cls.env.ref("base.view_partner_form")
        cls.base_group = cls.env.ref("base.group_user")
        cls.view_mode = cls.env.ref("npt_access_guard.access_guard_view_mode_form")

        cls.menu = cls.env["ir.ui.menu"].create({"name": "Portable Test Menu"})
        cls._set_xml_id("portable_test_menu", cls.menu)

        cls.report = cls.env["ir.actions.report"].create({
            "name": "Portable Partner Report",
            "model": "res.partner",
            "report_name": "npt_access_guard.portable_partner_report",
            "report_type": "qweb-pdf",
        })
        cls._set_xml_id("portable_partner_report", cls.report)
        cls.server_action = cls.env["ir.actions.server"].create({
            "name": "Portable Partner Server Action",
            "model_id": cls.partner_model.id,
            "state": "code",
            "code": "action = None",
        })
        cls._set_xml_id("portable_partner_server_action", cls.server_action)
        cls.window_action = cls.env["ir.actions.act_window"].create({
            "name": "Portable Partner Window",
            "res_model": "res.partner",
            "view_mode": "list,form",
            "binding_model_id": cls.partner_model.id,
            "binding_type": "action",
        })
        cls._set_xml_id("portable_partner_window_action", cls.window_action)
        cls.unmapped_window_action = cls.env["ir.actions.act_window"].create({
            "name": "Unmapped Portable Window",
            "res_model": "res.partner",
            "view_mode": "form",
        })

        Element = cls.env["access.guard.ui.element"]
        cls.mapped_action_element = Element.create({
            "model_id": cls.partner_model.id,
            "type": "action_button",
            "match_mode": "action_id",
            "match_value": str(cls.window_action.id),
            "label": "Mapped Window Action",
            "view_type": "form",
            "view_id": cls.partner_view.id,
        })
        cls.unmapped_action_element = Element.create({
            "model_id": cls.partner_model.id,
            "type": "action_button",
            "match_mode": "action_id",
            "match_value": str(cls.unmapped_window_action.id),
            "label": "Unmapped Window Action",
            "view_type": "form",
            "view_id": cls.partner_view.id,
        })

        SearchItem = cls.env["access.guard.search.item"]
        cls.filter_item = SearchItem.search([
            ("model_id", "=", cls.partner_model.id),
            ("field_id", "=", cls.name_field.id),
            ("type", "=", "filter"),
        ], limit=1)
        if not cls.filter_item:
            cls.filter_item = SearchItem.create({
                "model_id": cls.partner_model.id,
                "field_id": cls.name_field.id,
                "type": "filter",
                "label": "Partner Name",
            })

        cls.warehouse = cls.env["stock.warehouse"].create({
            "name": "Portable Test Warehouse",
            "code": "PTZ",
            "company_id": cls.env.company.id,
        })
        cls.xml_picking_type = cls.warehouse.int_type_id
        cls.fallback_picking_type = cls.warehouse.in_type_id
        cls._set_xml_id("portable_internal_picking_type", cls.xml_picking_type)

        cls.source = cls.Management.create({
            "name": "Portable Complete Rule",
            "category": "inventory",
            "user_ids": [Command.set(cls.env.user.ids)],
            "company_ids": [Command.set(cls.env.company.ids)],
            "apply_by_group": True,
            "group_ids": [Command.set(cls.base_group.ids)],
            "overview_mode": "show",
            "overview_picking_type_ids": [Command.set(
                (cls.xml_picking_type | cls.fallback_picking_type).ids
            )],
            "menu_line_ids": [Command.create({
                "menu_id": cls.menu.id,
                "include_children": True,
            })],
            "model_rule_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "report_ids": [Command.set(cls.report.ids)],
                "action_ids": [Command.set(cls.server_action.ids)],
                "window_action_ids": [Command.set(cls.window_action.ids)],
                "hide_view_mode_ids": [Command.set(cls.view_mode.ids)],
                "view_ids": [Command.set(cls.partner_view.ids)],
                "hide_create": True,
            })],
            "field_rule_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "field_ids": [Command.set(cls.comment_field.ids)],
                "remove_external_link": True,
            })],
            "field_conditional_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "apply_attrs": True,
                "attr_field_ids": [Command.set(cls.name_field.ids)],
                "attrs_domain": "[('company_type', '=', 'company')]",
                "apply_domain": True,
                "domain_field_id": cls.parent_field.id,
                "domain_expression": "[('create_uid', '=', uid), ('company_id', 'in', company_ids)]",
            })],
            "domain_rule_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "domain_expression": "[('parent_id', '=', %s)]" % cls.env.company.partner_id.id,
                "apply_filter": True,
            })],
            "ui_rule_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "action_button_element_ids": [Command.set(
                    (cls.mapped_action_element | cls.unmapped_action_element).ids
                )],
            })],
            "search_panel_rule_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "filter_item_ids": [Command.set(cls.filter_item.ids)],
            })],
            "acl_rule_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "group_id": cls.base_group.id,
                "perm_read": True,
                "perm_write": True,
                "perm_create": False,
                "perm_unlink": False,
            })],
        })
        cls.template = cls.Template.create({
            "name": "Portable Complete Template",
            "template_code": "PORTABLE_COMPLETE_TEMPLATE",
            "template_version": "2.0",
            "category": "inventory",
            "source_management_id": cls.source.id,
        })

    @classmethod
    def _set_xml_id(cls, name, record):
        existing = cls.ModelData.search([
            ("module", "=", "access_guard"),
            ("name", "=", name),
        ], limit=1)
        values = {"model": record._name, "res_id": record.id}
        if existing:
            existing.write(values)
        else:
            cls.ModelData.create({"module": "access_guard", "name": name, **values})

    def _payload(self):
        return json.loads(self.template.portable_payload)

    def _ui_element(self, label):
        elements = self._payload()["ui_rules"][0]["action_button_elements"]
        return next(element for element in elements if element["label"] == label)

    def _picking_type(self, xml_id):
        return next(
            item for item in self._payload()["overview"]["picking_types"]
            if item.get("xml_id") == xml_id
        )

    def test_01_model_ids_are_removed(self):
        model = self._payload()["model_rules"][0]["model"]
        self.assertEqual(model, {"model": "res.partner"})

    def test_02_fields_use_model_and_field(self):
        field = self._payload()["field_rules"][0]["fields"][0]
        self.assertEqual(field, {"model": "res.partner", "field": "comment"})

    def test_03_menu_uses_xml_id(self):
        menu = self._payload()["menus"][0]["menu"]
        self.assertEqual(menu, {"xml_id": "npt_access_guard.portable_test_menu"})

    def test_04_view_uses_xml_id(self):
        view = self._payload()["model_rules"][0]["views"][0]
        self.assertEqual(view, {"xml_id": "base.view_partner_form"})

    def test_05_actions_use_xml_ids(self):
        rule = self._payload()["model_rules"][0]
        self.assertEqual(rule["reports"][0]["xml_id"], "npt_access_guard.portable_partner_report")
        self.assertEqual(
            rule["server_actions"][0]["xml_id"],
            "npt_access_guard.portable_partner_server_action",
        )
        self.assertEqual(
            rule["window_actions"][0]["xml_id"],
            "npt_access_guard.portable_partner_window_action",
        )

    def test_06_acl_group_uses_xml_id(self):
        self.assertEqual(self._payload()["acl_rules"][0]["group_xml_id"], "base.group_user")

    def test_07_view_mode_uses_code(self):
        modes = self._payload()["model_rules"][0]["hidden_view_modes"]
        self.assertEqual(modes, [{"code": "form"}])

    def test_08_search_item_is_semantic(self):
        item = self._payload()["search_rules"][0]["filter_items"][0]
        self.assertEqual(item, {"model": "res.partner", "field": "name", "type": "filter"})

    def test_09_numeric_action_is_converted_to_xml_id(self):
        element = self._ui_element("Mapped Window Action")
        self.assertEqual(element["match_value"], "npt_access_guard.portable_partner_window_action")
        self.assertEqual(element["action"]["action_type"], "ir.actions.act_window")

    def test_10_action_without_xml_id_requires_mapping(self):
        element = self._ui_element("Unmapped Window Action")
        self.assertIsNone(element["match_value"])
        self.assertEqual(element["portability"]["status"], "mapping_required")
        self.assertEqual(self.template.portability_status, "mapping_required")

    def test_11_picking_type_with_xml_id_is_ready(self):
        item = self._picking_type("npt_access_guard.portable_internal_picking_type")
        self.assertEqual(item, {"xml_id": "npt_access_guard.portable_internal_picking_type"})

    def test_12_picking_type_without_xml_id_uses_fallback(self):
        item = self._picking_type(None)
        self.assertEqual(item["warehouse"]["code"], "PTZ")
        self.assertEqual(item["code"], self.fallback_picking_type.code)
        self.assertEqual(item["portability"], "warnings")

    def test_13_acl_excludes_generated_access_id(self):
        acl = self._payload()["acl_rules"][0]
        self.assertNotIn("access_id", acl)
        self.assertEqual(acl["model"], "res.partner")

    def test_14_local_ids_and_assignments_are_excluded(self):
        forbidden = {"id", "access_id", "user_ids", "company_ids", "group_ids"}

        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        inspect(self._payload())

    def test_15_plain_scalar_domain_is_ready(self):
        result = self.Template._analyze_domain_portability(
            "[('state', '=', 'done')]", "stock.picking"
        )
        self.assertEqual(result, {"status": "ready", "messages": []})

    def test_16_relational_numeric_domain_warns(self):
        result = self.Template._analyze_domain_portability(
            "[('picking_type_id', '=', 7)]", "stock.picking"
        )
        self.assertEqual(result["status"], "warnings")
        self.assertIn("picking_type_id", result["messages"][0])

    def test_17_dynamic_domain_variables_are_ready(self):
        result = self.Template._analyze_domain_portability(
            "[('create_uid', '=', uid), ('company_id', 'in', company_ids)]",
            "res.partner",
        )
        self.assertEqual(result, {"status": "ready", "messages": []})

    def test_18_dependencies_are_discovered_and_sorted(self):
        modules = self._payload()["dependencies"]["required_modules"]
        self.assertEqual(modules, sorted(set(modules)))
        self.assertIn("access_guard", modules)
        self.assertIn("base", modules)

    def test_19_same_snapshot_has_same_portable_hash(self):
        old_payload = self.template.portable_payload
        old_hash = self.template.portable_hash
        self.template._refresh_portable_payload()
        self.assertEqual(self.template.portable_payload, old_payload)
        self.assertEqual(self.template.portable_hash, old_hash)
        canonical = self.template._canonical_snapshot_json(self._payload())
        self.assertEqual(
            self.template.portable_hash,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def test_20_snapshot_refresh_updates_portable_payload(self):
        old_snapshot_hash = self.template.snapshot_hash
        old_portable_hash = self.template.portable_hash
        self.source.global_hide_export = not self.source.global_hide_export
        self.template.action_refresh_snapshot()
        self.assertNotEqual(self.template.snapshot_hash, old_snapshot_hash)
        self.assertNotEqual(self.template.portable_hash, old_portable_hash)

    def test_21_source_change_leaves_both_payloads_frozen(self):
        old_snapshot = self.template.snapshot_payload
        old_portable = self.template.portable_payload
        old_portable_hash = self.template.portable_hash
        self.source.name = "Changed Without Refresh"
        self.assertEqual(self.template.snapshot_payload, old_snapshot)
        self.assertEqual(self.template.portable_payload, old_portable)
        self.assertEqual(self.template.portable_hash, old_portable_hash)

    def test_22_payload_validation_report_and_views(self):
        self.assertTrue(self.template.portable_valid)
        self.assertIn("Mapping required:", self.template.portability_report)
        self.assertEqual(
            self.template.action_view_portable_payload()["view_id"],
            self.env.ref("npt_access_guard.view_access_guard_template_portable").id,
        )
        self.assertEqual(
            self.template.action_view_portability_report()["view_id"],
            self.env.ref("npt_access_guard.view_access_guard_template_portability_report").id,
        )
        invalid = self._payload()
        invalid["general"]["user_ids"] = [self.env.user.id]
        with self.assertRaises(ValidationError):
            self.Template._validate_portable_data(invalid)
