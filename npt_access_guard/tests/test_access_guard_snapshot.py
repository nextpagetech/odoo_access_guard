import hashlib
import json

from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestAccessGuardSnapshot(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Management = cls.env["access.guard.management"]
        cls.Template = cls.env["access.guard.template"]
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.name_field = cls.env["ir.model.fields"]._get("res.partner", "name")
        cls.comment_field = cls.env["ir.model.fields"]._get("res.partner", "comment")
        cls.parent_field = cls.env["ir.model.fields"]._get("res.partner", "parent_id")
        cls.partner_view = cls.env.ref("base.view_partner_form")
        cls.menu = cls.env["ir.ui.menu"].create({"name": "Snapshot Test Menu"})
        cls.group = cls.env["res.groups"].create({"name": "Snapshot Test Group"})
        cls.report = cls.env["ir.actions.report"].create({
            "name": "Snapshot Partner Report",
            "model": "res.partner",
            "report_name": "npt_access_guard.snapshot_partner_report",
            "report_type": "qweb-pdf",
        })
        cls.server_action = cls.env["ir.actions.server"].create({
            "name": "Snapshot Partner Action",
            "model_id": cls.partner_model.id,
            "state": "code",
            "code": "action = None",
        })
        cls.window_action = cls.env["ir.actions.act_window"].create({
            "name": "Snapshot Partner Window",
            "res_model": "res.partner",
            "view_mode": "list,form",
            "binding_model_id": cls.partner_model.id,
            "binding_type": "action",
        })
        cls.view_mode = cls.env.ref("npt_access_guard.access_guard_view_mode_form")
        cls.ui_element = cls.env["access.guard.ui.element"].create({
            "model_id": cls.partner_model.id,
            "type": "button",
            "match_mode": "name",
            "match_value": "action_snapshot_test",
            "attribute_name": "name",
            "label": "Snapshot Test Button",
            "view_type": "form",
            "view_id": cls.partner_view.id,
        })
        SearchItem = cls.env["access.guard.search.item"]
        SearchItem._ensure_items_for_model(cls.partner_model)
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
                "label": "Name Filter",
            })
        cls.group_item = SearchItem.search([
            ("model_id", "=", cls.partner_model.id),
            ("field_id", "=", cls.name_field.id),
            ("type", "=", "group"),
        ], limit=1)
        if not cls.group_item:
            cls.group_item = SearchItem.create({
                "model_id": cls.partner_model.id,
                "field_id": cls.name_field.id,
                "type": "group",
                "label": "Name Group",
            })
        cls.picking_type = cls.env["stock.picking.type"].search([], limit=1)
        if not cls.picking_type:
            warehouse = cls.env["stock.warehouse"].create({
                "name": "Snapshot Warehouse",
                "code": "SNP",
                "company_id": cls.env.company.id,
            })
            cls.picking_type = warehouse.int_type_id

        cls.source = cls.Management.create({
            "name": "Complete Snapshot Rule",
            "category": "inventory",
            "active": True,
            "user_ids": [Command.set(cls.env.user.ids)],
            "company_ids": [Command.set(cls.env.company.ids)],
            "apply_by_group": True,
            "group_ids": [Command.set(cls.group.ids)],
            "apply_without_company": False,
            "default_internal_user": True,
            "default_portal_user": False,
            "disable_developer_mode": True,
            "read_only": True,
            "global_hide_chatter": True,
            "global_hide_send_message": True,
            "global_hide_import": True,
            "global_hide_custom_filter": True,
            "global_disable_login": True,
            "overview_mode": "show",
            "overview_picking_type_ids": [Command.set(cls.picking_type.ids)],
            "menu_line_ids": [Command.create({
                "sequence": 7,
                "menu_id": cls.menu.id,
                "include_children": True,
            })],
            "model_rule_ids": [Command.create({
                "sequence": 8,
                "model_id": cls.partner_model.id,
                "report_ids": [Command.set(cls.report.ids)],
                "action_ids": [Command.set(cls.server_action.ids)],
                "window_action_ids": [Command.set(cls.window_action.ids)],
                "hide_view_mode_ids": [Command.set(cls.view_mode.ids)],
                "view_ids": [Command.set(cls.partner_view.ids)],
                "hide_create": True,
                "hide_edit": True,
                "hide_delete": True,
                "hide_archive": True,
                "hide_duplicate": True,
                "hide_import": True,
                "hide_export": True,
                "hide_spreadsheet": True,
            })],
            "field_rule_ids": [Command.create({
                "sequence": 9,
                "model_id": cls.partner_model.id,
                "field_ids": [Command.set(cls.comment_field.ids)],
                "invisible": True,
                "read_only": True,
                "required": False,
                "remove_create_option": True,
                "remove_edit_option": True,
                "remove_external_link": True,
            })],
            "field_conditional_ids": [Command.create({
                "sequence": 10,
                "model_id": cls.partner_model.id,
                "apply_attrs": True,
                "attr_field_ids": [Command.set(cls.name_field.ids)],
                "attr_button_ids": [Command.set(cls.ui_element.ids)],
                "attr_type": "readonly",
                "attrs_domain": "[('is_company', '=', True)]",
                "apply_domain": True,
                "domain_field_id": cls.parent_field.id,
                "domain_expression": "[('is_company', '=', True)]",
            })],
            "domain_rule_ids": [Command.create({
                "sequence": 11,
                "model_id": cls.partner_model.id,
                "soft_restrict": True,
                "apply_relational_fields": True,
                "perm_read": True,
                "perm_create": False,
                "perm_write": False,
                "perm_delete": False,
                "apply_filter": True,
                "domain_expression": "[('is_company', '=', True)]",
            })],
            "ui_rule_ids": [Command.create({
                "sequence": 12,
                "model_id": cls.partner_model.id,
                "button_element_ids": [Command.set(cls.ui_element.ids)],
            })],
            "search_panel_rule_ids": [Command.create({
                "sequence": 13,
                "model_id": cls.partner_model.id,
                "hide_search_panel": True,
                "filter_item_ids": [Command.set(cls.filter_item.ids)],
                "group_item_ids": [Command.set(cls.group_item.ids)],
                "hide_custom_filter": True,
                "hide_custom_group": True,
                "hide_delete_favorite": True,
            })],
            "chatter_rule_ids": [Command.create({
                "sequence": 14,
                "model_id": cls.partner_model.id,
                "hide_chatter": True,
                "hide_send_message": True,
                "hide_log_note": True,
                "hide_schedule_activity": True,
            })],
            "acl_rule_ids": [Command.create({
                "model_id": cls.partner_model.id,
                "group_id": cls.group.id,
                "perm_read": True,
                "perm_write": True,
                "perm_create": False,
                "perm_unlink": False,
            })],
        })
        cls.template = cls.Template.create({
            "name": "Complete Snapshot Template",
            "template_code": "complete snapshot template",
            "template_version": "1.2",
            "category": "inventory",
            "source_management_id": cls.source.id,
        })

    def _payload(self, template=None):
        return json.loads((template or self.template).snapshot_payload)

    def test_create_snapshot_from_basic_rule(self):
        source = self.Management.create({"name": "Basic Snapshot Rule"})
        action = source.action_create_template()
        template = self.Template.browse(action["res_id"])
        self.assertEqual(template.source_type, "local_rule")
        self.assertEqual(template.source_management_id, source)
        self.assertTrue(template.snapshot_payload)
        self.assertTrue(template.snapshot_hash)
        self.assertTrue(template.snapshot_date)
        self.assertTrue(template.snapshot_valid)
        self.assertEqual(template.template_code, "BASIC_SNAPSHOT_RULE")

    def test_imported_template_structure_has_no_local_source(self):
        template = self.Template.create({
            "name": "Imported Structure Only",
            "template_code": "IMPORTED_STRUCTURE_ONLY",
            "template_version": "1.0",
            "source_type": "imported",
        })
        self.assertEqual(template.source_type, "imported")
        self.assertFalse(template.source_management_id)
        self.assertFalse(template.snapshot_payload)
        with self.assertRaisesRegex(UserError, "portable payload is invalid"):
            template.action_apply_template()
        with self.assertRaisesRegex(UserError, "Imported template application is not available"):
            template.action_refresh_snapshot()

    def test_local_template_still_requires_source_rule(self):
        with self.assertRaisesRegex(UserError, "no source rule to snapshot"):
            self.Template.create({
                "name": "Invalid Local Template",
                "template_code": "INVALID_LOCAL_TEMPLATE",
            })
        with self.assertRaisesRegex(ValidationError, "cannot have a local source rule"):
            self.Template.create({
                "name": "Invalid Imported Template",
                "template_code": "INVALID_IMPORTED_TEMPLATE",
                "source_type": "imported",
                "source_management_id": self.source.id,
            })

    def test_template_code_is_unique_per_version(self):
        second_version = self.Template.create({
            "name": "Complete Snapshot Template Version 2",
            "template_code": self.template.template_code,
            "template_version": "2.0",
            "source_management_id": self.source.id,
        })
        self.assertEqual(second_version.template_code, self.template.template_code)
        self.assertNotEqual(second_version.template_version, self.template.template_version)
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Template.create({
                "name": "Duplicate Complete Snapshot Version 2",
                "template_code": self.template.template_code,
                "template_version": "2.0",
                "source_management_id": self.source.id,
            })

    def test_snapshot_has_every_top_level_section(self):
        payload = self._payload()
        expected = {
            "schema_version", "general", "menus", "model_rules", "field_rules",
            "conditional_rules", "domain_rules", "ui_rules", "search_rules",
            "chatter_rules", "global", "overview", "acl_rules",
        }
        self.assertEqual(set(payload), expected)

    def test_model_rule_serialization(self):
        line = self._payload()["model_rules"][0]
        self.assertEqual(line["model"]["model"], "res.partner")
        self.assertEqual(line["reports"][0]["report_name"], self.report.report_name)
        self.assertEqual(line["server_actions"][0]["model"], "res.partner")
        self.assertEqual(line["window_actions"][0]["res_model"], "res.partner")
        self.assertEqual(line["hidden_view_modes"][0]["code"], "form")
        self.assertTrue(line["hide_spreadsheet"])

    def test_field_rule_serialization(self):
        line = self._payload()["field_rules"][0]
        self.assertEqual(line["fields"][0]["name"], "comment")
        self.assertTrue(line["invisible"])
        self.assertTrue(line["remove_external_link"])

    def test_conditional_rule_serialization(self):
        line = self._payload()["conditional_rules"][0]
        self.assertEqual(line["attr_fields"][0]["name"], "name")
        self.assertEqual(line["attr_buttons"][0]["match_value"], "action_snapshot_test")
        self.assertEqual(line["domain_field"]["name"], "parent_id")
        self.assertTrue(line["apply_attrs"])
        self.assertTrue(line["apply_domain"])

    def test_domain_rule_serialization(self):
        line = self._payload()["domain_rules"][0]
        self.assertTrue(line["soft_restrict"])
        self.assertTrue(line["apply_relational_fields"])
        self.assertFalse(line["perm_write"])
        self.assertEqual(line["domain_expression"], "[('is_company', '=', True)]")

    def test_ui_element_semantic_serialization(self):
        element = self._payload()["ui_rules"][0]["button_elements"][0]
        self.assertEqual(element["model"]["model"], "res.partner")
        self.assertEqual(element["type"], "button")
        self.assertEqual(element["match_mode"], "name")
        self.assertEqual(element["match_value"], "action_snapshot_test")
        self.assertEqual(element["view"]["xml_id"], "base.view_partner_form")
        self.assertTrue(element["active"])

    def test_search_item_semantic_serialization(self):
        line = self._payload()["search_rules"][0]
        self.assertEqual(line["filter_items"][0]["field"]["name"], "name")
        self.assertEqual(line["filter_items"][0]["type"], "filter")
        self.assertEqual(line["group_items"][0]["type"], "group")

    def test_chatter_and_global_serialization(self):
        payload = self._payload()
        chatter = payload["chatter_rules"][0]
        self.assertTrue(chatter["hide_chatter"])
        self.assertTrue(chatter["hide_schedule_activity"])
        self.assertTrue(payload["global"]["hide_import"])
        self.assertTrue(payload["global"]["disable_login"])
        self.assertTrue(payload["general"]["disable_developer_mode"])

    def test_overview_picking_type_serialization(self):
        overview = self._payload()["overview"]
        picking_type = overview["picking_types"][0]
        self.assertEqual(overview["mode"], "show")
        self.assertEqual(picking_type["code"], self.picking_type.code)
        self.assertIn("warehouse", picking_type)
        self.assertIn("company", picking_type)

    def test_acl_serialization_excludes_generated_access(self):
        acl = self._payload()["acl_rules"][0]
        self.assertEqual(acl["model"]["model"], "res.partner")
        self.assertEqual(acl["group"]["name"], "Snapshot Test Group")
        self.assertTrue(acl["perm_write"])
        self.assertNotIn("access_id", acl)

    def test_deployment_assignments_are_excluded(self):
        payload = self._payload()
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn('"user_ids"', serialized)
        self.assertNotIn('"company_ids"', serialized)
        self.assertNotIn('"group_ids"', serialized)

    def test_unchanged_rule_has_same_snapshot_hash(self):
        payload = self.source._access_guard_build_snapshot()
        canonical = self.Template._canonical_snapshot_json(payload)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(digest, self.template.snapshot_hash)
        self.template.action_refresh_snapshot()
        self.assertEqual(digest, self.template.snapshot_hash)

    def test_changed_rule_changes_hash_after_refresh(self):
        old_hash = self.template.snapshot_hash
        self.source.global_hide_export = not self.source.global_hide_export
        self.assertEqual(self.template.snapshot_hash, old_hash)
        self.template.action_refresh_snapshot()
        self.assertNotEqual(self.template.snapshot_hash, old_hash)

    def test_source_change_does_not_mutate_frozen_snapshot(self):
        old_payload = self.template.snapshot_payload
        self.source.name = "Changed Source Name"
        self.assertEqual(self.template.snapshot_payload, old_payload)

    def test_refresh_updates_frozen_payload(self):
        self.source.name = "Refreshed Source Name"
        self.assertNotEqual(self._payload()["general"]["name"], self.source.name)
        self.template.action_refresh_snapshot()
        self.assertEqual(self._payload()["general"]["name"], self.source.name)

    def test_existing_apply_template_still_clones_source(self):
        wizard = self.env["access.guard.apply.template.wizard"].create({
            "template_id": self.template.id,
            "name": "Applied Snapshot Template",
            "group_ids": [Command.set(self.group.ids)],
            "apply_by_group": True,
            "copy_acl_lines": False,
        })
        action = wizard.action_apply_template()
        applied = self.Management.browse(action["res_id"])
        self.assertEqual(applied.template_id, self.template)
        self.assertEqual(len(applied.model_rule_ids), len(self.source.model_rule_ids))
        self.assertEqual(applied.model_rule_ids.hide_create, self.source.model_rule_ids.hide_create)

    def test_snapshot_validation_and_code_normalization(self):
        self.assertEqual(self.template.template_code, "COMPLETE_SNAPSHOT_TEMPLATE")
        self.assertTrue(self.template._validate_snapshot_payload())
        self.template.snapshot_payload = "{}"
        self.assertFalse(self.template.snapshot_valid)
        self.template.action_refresh_snapshot()
        self.assertTrue(self.template.snapshot_valid)
