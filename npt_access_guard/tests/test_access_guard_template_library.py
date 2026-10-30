from lxml import etree

from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAccessGuardTemplateLibrary(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({
            "groups_id": [Command.link(cls.env.ref("npt_access_guard.group_access_guard_manager").id)]
        })
        cls.Template = cls.env["access.guard.template"]
        cls.Rule = cls.env["access.guard.management"]

    def test_save_as_template_is_persistent_and_reopens_existing(self):
        source = self.Rule.create({"name": "Library Sales Rule", "category": "sales"})
        before = self.Template.search_count([])
        first_action = source.action_create_template()
        template = self.Template.browse(first_action["res_id"])
        self.assertEqual(self.Template.search_count([]), before + 1)
        self.assertEqual(first_action["res_model"], "access.guard.template")
        self.assertEqual(first_action["view_mode"], "form")
        self.assertEqual(template.source_type, "local_rule")
        self.assertEqual(template.source_management_id, source)
        self.assertEqual(template.category, "sales")
        self.assertTrue(template.snapshot_valid)
        self.assertTrue(template.portable_valid)
        self.assertTrue(template.snapshot_hash)
        self.assertTrue(template.portable_hash)
        self.assertEqual(
            self.Template.search([("id", "=", template.id)]), template,
        )

        second_action = source.action_create_template()
        self.assertEqual(second_action["res_id"], template.id)
        self.assertEqual(self.Template.search_count([]), before + 1)

        other_version = self.Template.create({
            "name": template.name,
            "template_code": template.template_code,
            "template_version": "2.0",
            "source_management_id": source.id,
        })
        versions_action = source.action_create_template()
        self.assertEqual(versions_action["view_mode"], "list,form")
        self.assertEqual(set(versions_action["domain"][0][2]), {template.id, other_version.id})

    def test_assigned_rules_are_linked_and_assignments_stay_on_rule(self):
        source = self.Rule.create({"name": "Library Assignment Source"})
        template = self.Template.browse(source.action_create_template()["res_id"])
        user = self.env.ref("base.user_admin")
        group = self.env.ref("base.group_user")
        company = self.env.company
        self.assertEqual(template.assigned_rule_count, 0)
        self.assertNotIn("user_ids", template._fields)
        self.assertNotIn("group_ids", template._fields)
        self.assertNotIn("company_ids", template._fields)

        wizard = self.env["access.guard.apply.template.wizard"].create({
            "template_id": template.id,
            "name": "Library Applied Team A",
            "user_ids": [Command.set(user.ids)],
            "group_ids": [Command.set(group.ids)],
            "company_ids": [Command.set(company.ids)],
            "copy_acl_lines": False,
        })
        wizard.action_preview_validate()
        action = wizard.action_apply_template()
        applied = self.Rule.browse(action["res_id"])
        self.assertEqual(action["res_model"], "access.guard.management")
        self.assertEqual(applied.template_id, template)
        self.assertEqual(applied.user_ids, user)
        self.assertEqual(applied.group_ids, group)
        self.assertEqual(applied.company_ids, company)
        self.assertEqual(template.assigned_rule_count, 1)

        second = self.Rule.create({"name": "Library Applied Team B", "template_id": template.id, "active": False})
        self.assertEqual(template.assigned_rule_count, 2)
        rules_action = template.action_open_assigned_rules()
        self.assertEqual(rules_action["res_model"], "access.guard.management")
        self.assertEqual(rules_action["domain"], [("template_id", "=", template.id)])
        self.assertEqual(rules_action["context"], {"active_test": False})
        self.assertEqual(
            set(self.Rule.with_context(**rules_action["context"]).search(rules_action["domain"]).ids),
            {applied.id, second.id},
        )
        self.assertEqual(source.user_ids, self.env["res.users"])

    def test_library_action_and_origin_specific_buttons(self):
        action = self.env.ref("npt_access_guard.action_access_guard_template")
        template_menu = self.env.ref("npt_access_guard.menu_access_guard_template")
        import_menu = self.env.ref("npt_access_guard.menu_access_guard_import_template")
        self.assertEqual(action.res_model, "access.guard.template")
        self.assertEqual(action.view_mode, "list,form")
        self.assertFalse(action.domain)
        self.assertIn("active_test", action.context)
        self.assertEqual(template_menu.action, action)
        self.assertEqual(import_menu.parent_id, template_menu.parent_id)
        self.assertFalse(self.env["ir.ui.menu"].search([("parent_id", "=", template_menu.id)]))

        view = etree.fromstring(self.env.ref("npt_access_guard.view_access_guard_template_form").arch_db.encode())
        local_buttons = (
            "action_refresh_snapshot", "action_open_source_rule", "action_view_snapshot"
        )
        imported_buttons = ("action_view_resolution",)
        for name in local_buttons:
            self.assertIn("local_rule", view.xpath("//button[@name='%s']" % name)[0].get("invisible"))
        for name in imported_buttons:
            self.assertIn("imported", view.xpath("//button[@name='%s']" % name)[0].get("invisible"))
        self.assertEqual(len(view.xpath("//button[@name='action_open_assigned_rules']")), 1)
