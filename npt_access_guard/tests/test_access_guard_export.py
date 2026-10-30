import base64
import json
from urllib.parse import parse_qs, urlparse

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAccessGuardExport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Management = cls.env["access.guard.management"]
        cls.Template = cls.env["access.guard.template"]
        manager_group = cls.env.ref("npt_access_guard.group_access_guard_manager")
        cls.env.user.write({"groups_id": [Command.link(manager_group.id)]})

        cls.ready_source = cls.Management.create({"name": "Ready Export Rule"})
        cls.ready = cls.Template.create({
            "name": "Ready Export Template",
            "template_code": "READY_EXPORT",
            "template_version": "1.0",
            "source_management_id": cls.ready_source.id,
        })
        cls.second_ready_source = cls.Management.create({"name": "Second Ready Rule"})
        cls.second_ready = cls.Template.create({
            "name": "Second Ready Template",
            "template_code": "SECOND_READY",
            "template_version": "2.0",
            "source_management_id": cls.second_ready_source.id,
        })

        partner_model = cls.env["ir.model"]._get("res.partner")
        string_element = cls.env["access.guard.ui.element"].create({
            "model_id": partner_model.id,
            "type": "button",
            "match_mode": "string",
            "match_value": "Export Test Label",
            "label": "Export Test Label",
            "view_type": "form",
            "view_id": cls.env.ref("base.view_partner_form").id,
        })
        cls.warning_source = cls.Management.create({
            "name": "Warning Export Rule",
            "ui_rule_ids": [Command.create({
                "model_id": partner_model.id,
                "button_element_ids": [Command.set(string_element.ids)],
            })],
        })
        cls.warning = cls.Template.create({
            "name": "Warning Export Template",
            "template_code": "WARNING_EXPORT",
            "template_version": "1.1",
            "source_management_id": cls.warning_source.id,
        })

        unmapped_menu = cls.env["ir.ui.menu"].create({"name": "Unmapped Export Menu"})
        cls.mapping_source = cls.Management.create({
            "name": "Mapping Export Rule",
            "menu_line_ids": [Command.create({"menu_id": unmapped_menu.id})],
        })
        cls.mapping = cls.Template.create({
            "name": "Mapping Export Template",
            "template_code": "MAPPING_EXPORT",
            "template_version": "3.2",
            "source_management_id": cls.mapping_source.id,
        })

        if cls.ready.portability_status != "ready":
            raise AssertionError("Ready export fixture is not portable")
        if cls.warning.portability_status != "warnings":
            raise AssertionError("Warning export fixture does not produce warnings")
        if cls.mapping.portability_status != "mapping_required":
            raise AssertionError("Mapping export fixture does not require mapping")

    def _wizard_from_action(self, action):
        if action["type"] == "ir.actions.act_window":
            return self.env[action["res_model"]].browse(action["res_id"])
        query = parse_qs(urlparse(action["url"]).query)
        return self.env[query["model"][0]].browse(int(query["id"][0]))

    def _download(self, templates, confirm=False):
        action = templates.action_export_templates()
        if action["type"] == "ir.actions.act_window":
            self.assertTrue(confirm)
            action = self._wizard_from_action(action).action_export_anyway()
        wizard = self._wizard_from_action(action)
        raw = base64.b64decode(wizard.file_data).decode("utf-8")
        return wizard, raw, json.loads(raw)

    def test_01_ready_template_exports_directly(self):
        action = self.ready.action_export_template()
        self.assertEqual(action["type"], "ir.actions.act_url")
        wizard, _raw, document = self._download(self.ready)
        self.assertTrue(wizard.file_data)
        self.assertEqual(document["format"], "access_guard_template")

    def test_02_warning_template_requires_confirmation(self):
        action = self.warning.action_export_template()
        self.assertEqual(action["type"], "ir.actions.act_window")
        wizard = self._wizard_from_action(action)
        self.assertIn("Warnings", wizard.warning_text)
        download = wizard.action_export_anyway()
        self.assertEqual(download["type"], "ir.actions.act_url")

    def test_03_mapping_template_requires_confirmation(self):
        action = self.mapping.action_export_template()
        self.assertEqual(action["type"], "ir.actions.act_window")
        wizard = self._wizard_from_action(action)
        self.assertIn("Mapping Required", wizard.warning_text)
        _download_wizard, _raw, document = self._download(self.mapping, confirm=True)
        self.assertEqual(
            document["export_metadata"]["portability_status"],
            "mapping_required",
        )

    def test_04_blocked_template_cannot_export(self):
        self.ready.write({
            "portability_status": "blocked",
            "portability_report": "Invalid source reference",
        })
        with self.assertRaisesRegex(UserError, "blocked"):
            self.ready.action_export_template()

    def test_05_missing_portable_payload_cannot_export(self):
        self.ready.write({"portable_payload": False, "portable_hash": False})
        with self.assertRaisesRegex(UserError, "Refresh Snapshot"):
            self.ready.action_export_template()

    def test_06_mismatched_hash_blocks_export(self):
        self.ready.portable_hash = "0" * 64
        with self.assertRaisesRegex(UserError, "hash check"):
            self.ready.action_export_template()

    def test_07_filename_is_safe_and_deterministic(self):
        self.ready.template_version = "1.0 beta/1"
        self.ready._refresh_portable_payload()
        wizard, _raw, _document = self._download(self.ready)
        self.assertEqual(
            wizard.filename,
            "ACCESS_GUARD_READY_EXPORT_v1.0_beta_1.json",
        )

    def test_08_export_is_valid_pretty_utf8_json(self):
        _wizard, raw, document = self._download(self.ready)
        self.assertIn("\n  ", raw)
        self.assertEqual(json.loads(raw), document)
        raw.encode("utf-8").decode("utf-8")

    def test_09_exported_template_equals_stored_payload(self):
        _wizard, _raw, document = self._download(self.ready)
        self.assertEqual(document["template"], json.loads(self.ready.portable_payload))

    def test_10_user_assignments_are_not_exported(self):
        self.ready_source.user_ids = [Command.set(self.env.user.ids)]
        self.ready.action_refresh_snapshot()
        _wizard, raw, _document = self._download(self.ready)
        self.assertNotIn('"user_ids"', raw)
        self.assertNotIn(self.env.user.login, raw)

    def test_11_company_assignments_are_not_exported(self):
        self.ready_source.company_ids = [Command.set(self.env.company.ids)]
        self.ready.action_refresh_snapshot()
        _wizard, raw, _document = self._download(self.ready)
        self.assertNotIn('"company_ids"', raw)

    def test_12_no_local_numeric_reference_keys_are_exported(self):
        _wizard, _raw, document = self._download(self.warning, confirm=True)
        forbidden = {"id", "access_id", "user_ids", "company_ids", "group_ids"}

        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        inspect(document["template"])

    def test_13_required_modules_are_included(self):
        _wizard, _raw, document = self._download(self.warning, confirm=True)
        modules = document["dependencies"]["required_modules"]
        self.assertEqual(modules, sorted(set(modules)))
        self.assertIn("access_guard", modules)
        self.assertIn("base", modules)

    def test_14_bulk_export_contains_multiple_templates(self):
        selected = self.ready | self.second_ready
        wizard, _raw, document = self._download(selected)
        self.assertEqual(wizard.filename, "ACCESS_GUARD_TEMPLATES_2.json")
        self.assertEqual(document["format"], "access_guard_template_bundle")
        self.assertEqual(document["export_metadata"]["template_count"], 2)
        self.assertEqual(len(document["templates"]), 2)

    def test_15_blocked_template_prevents_bundle_export(self):
        self.second_ready.write({
            "portability_status": "blocked",
            "portability_report": "Deliberately blocked",
        })
        with self.assertRaisesRegex(UserError, "Second Ready Template"):
            (self.ready | self.second_ready).action_export_templates()

    def test_16_bundle_dependencies_are_unioned_and_sorted(self):
        selected = self.ready | self.warning
        _wizard, _raw, document = self._download(selected, confirm=True)
        modules = document["bundle_dependencies"]["required_modules"]
        self.assertEqual(modules, sorted(set(modules)))
        self.assertIn("access_guard", modules)
        self.assertIn("base", modules)
        for entry in document["templates"]:
            self.assertIn("dependencies", entry)
            self.assertIn("portable_hash", entry)
            self.assertIn("portability_status", entry)

    def test_17_source_change_without_refresh_keeps_export_frozen(self):
        _wizard, _raw, before = self._download(self.ready)
        old_hash = self.ready.portable_hash
        self.ready_source.global_hide_export = True
        _wizard, _raw, after = self._download(self.ready)
        self.assertEqual(after["template"], before["template"])
        self.assertEqual(after["export_metadata"]["portable_hash"], old_hash)

    def test_18_refresh_snapshot_changes_export(self):
        _wizard, _raw, before = self._download(self.ready)
        self.ready_source.global_hide_export = True
        self.ready.action_refresh_snapshot()
        _wizard, _raw, after = self._download(self.ready)
        self.assertNotEqual(after["template"], before["template"])
        self.assertNotEqual(
            after["export_metadata"]["portable_hash"],
            before["export_metadata"]["portable_hash"],
        )
