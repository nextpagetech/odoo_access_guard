from odoo import Command
from odoo.http import request
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccessGuardRegressions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Management = cls.env['access.guard.management']
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.internal_group = cls.env.ref('base.group_user')
        cls.user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Access Guard Test User',
            'login': 'access_guard_test_user',
            'email': 'access.guard.test@example.com',
            'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'groups_id': [Command.set(cls.internal_group.ids)],
        })
        cls.other_user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Access Guard Other User',
            'login': 'access_guard_other_user',
            'email': 'access.guard.other@example.com',
            'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'groups_id': [Command.set(cls.internal_group.ids)],
        })

    def _model_rule(self, name, user=None, **flags):
        values = {
            'name': name,
            'user_ids': [Command.set((user or self.user).ids)],
            'model_rule_ids': [Command.create({
                'model_id': self.partner_model.id,
                **flags,
            })],
        }
        return self.Management.create(values)

    def _preview(self, rules=None):
        return self.env['access.guard.preview.wizard'].create({
            'user_id': self.user.id,
            'company_id': self.env.company.id,
            'rule_ids': [Command.set((rules or self.Management).ids)] if rules else False,
        })

    def test_disable_developer_mode_clears_supported_debug_variants(self):
        self.Management.create({
            'name': 'Disable Developer Mode',
            'user_ids': [Command.set(self.user.ids)],
            'disable_developer_mode': True,
        })
        self.assertTrue(self.Management._is_developer_mode_disabled(self.user))
        self.assertFalse(self.Management._is_developer_mode_disabled(self.other_user))

        with self.with_user(self.user.login):
            for debug_mode in ('1', 'assets', 'tests', 'assets,tests'):
                with self.debug_mode():
                    request.session.uid = self.user.id
                    request.session.debug = debug_mode
                    self.env['ir.http']._access_guard_enforce_debug_mode()
                    self.assertEqual(request.session.debug, '')

        with self.with_user(self.other_user.login):
            with self.debug_mode():
                request.session.uid = self.other_user.id
                request.session.debug = 'assets'
                self.env['ir.http']._access_guard_enforce_debug_mode()
                self.assertEqual(request.session.debug, 'assets')

    def test_preview_without_focus_uses_all_applicable_rules(self):
        create_rule = self._model_rule('Preview Create', hide_create=True)
        edit_rule = self._model_rule('Preview Edit', hide_edit=True)
        preview = self._preview()

        applicable = preview._applicable_rules()
        self.assertIn(create_rule, applicable)
        self.assertIn(edit_rule, applicable)
        rules = preview._effective_rules()
        self.assertTrue(rules['res.partner']['hide_create'])
        self.assertTrue(rules['res.partner']['hide_edit'])

    def test_preview_with_one_focus_rule(self):
        create_rule = self._model_rule('Focused Create', hide_create=True)
        self._model_rule('Unfocused Edit', hide_edit=True)
        preview = self._preview(create_rule)

        self.assertEqual(preview._applicable_rules(), create_rule)
        rules = preview._effective_rules()
        self.assertTrue(rules['res.partner']['hide_create'])
        self.assertFalse(rules['res.partner']['hide_edit'])

    def test_preview_with_multiple_focus_rules(self):
        create_rule = self._model_rule('Multi Create', hide_create=True)
        edit_rule = self._model_rule('Multi Edit', hide_edit=True)
        focused = create_rule | edit_rule
        preview = self._preview(focused)

        self.assertEqual(preview._applicable_rules(), focused)
        rules = preview._effective_rules()
        self.assertTrue(rules['res.partner']['hide_create'])
        self.assertTrue(rules['res.partner']['hide_edit'])

    def test_preview_ignores_focused_rule_not_applicable_to_user(self):
        other_rule = self._model_rule(
            'Other User Only', user=self.other_user, hide_create=True,
        )
        preview = self._preview(other_rule)

        self.assertFalse(preview._applicable_rules())
        rules = preview._effective_rules()
        self.assertNotIn('res.partner', rules)

    def test_user_group_membership_invalidates_effective_rules(self):
        group = self.env['res.groups'].create({'name': 'Access Guard Cache Group'})
        self.Management.create({
            'name': 'Group Cache Rule',
            'apply_by_group': True,
            'group_ids': [Command.set(group.ids)],
            'model_rule_ids': [Command.create({
                'model_id': self.partner_model.id,
                'hide_spreadsheet': True,
            })],
        })

        rules = self.Management._get_user_model_rules(self.user)
        self.assertFalse((rules.get('res.partner') or {}).get('hide_spreadsheet'))
        self.user.write({'groups_id': [Command.link(group.id)]})
        rules = self.Management._get_user_model_rules(self.user)
        self.assertTrue(rules['res.partner']['hide_spreadsheet'])
        self.user.write({'groups_id': [Command.unlink(group.id)]})
        rules = self.Management._get_user_model_rules(self.user)
        self.assertFalse((rules.get('res.partner') or {}).get('hide_spreadsheet'))

    def test_group_side_membership_invalidates_effective_rules(self):
        group = self.env['res.groups'].create({'name': 'Access Guard Group Side Cache'})
        self.Management.create({
            'name': 'Group Side Cache Rule',
            'apply_by_group': True,
            'group_ids': [Command.set(group.ids)],
            'model_rule_ids': [Command.create({
                'model_id': self.partner_model.id,
                'hide_duplicate': True,
            })],
        })

        self.Management._get_user_model_rules(self.user)
        group.write({'users': [Command.link(self.user.id)]})
        rules = self.Management._get_user_model_rules(self.user)
        self.assertTrue(rules['res.partner']['hide_duplicate'])
        group.write({'users': [Command.unlink(self.user.id)]})
        rules = self.Management._get_user_model_rules(self.user)
        self.assertFalse((rules.get('res.partner') or {}).get('hide_duplicate'))

    def test_inactive_ui_element_is_retained_but_ignored(self):
        element = self.env['access.guard.ui.element'].create({
            'model_id': self.partner_model.id,
            'type': 'button',
            'match_mode': 'name',
            'match_value': 'access_guard_test_button',
            'label': 'Access Guard Test Button',
            'view_type': 'form',
        })
        rule = self.Management.create({
            'name': 'Inactive UI Element',
            'user_ids': [Command.set(self.user.ids)],
            'ui_rule_ids': [Command.create({
                'model_id': self.partner_model.id,
                'button_element_ids': [Command.set(element.ids)],
            })],
        })
        rules = self.Management._get_user_model_rules(self.user)
        self.assertEqual(rules['res.partner']['buttons'][0]['value'], element.match_value)

        element.active = False
        rules = self.Management._get_user_model_rules(self.user)
        self.assertFalse(rules['res.partner']['buttons'])
        linked_elements = rule.ui_rule_ids.with_context(
            active_test=False,
        ).button_element_ids
        self.assertIn(element, linked_elements)

    def test_helper_updates_invalidate_cached_rules(self):
        view_mode = self.env['access.guard.view.mode'].create({
            'name': 'Access Guard Test View',
            'code': 'access_guard_test_old',
        })
        self.Management.create({
            'name': 'View Mode Cache',
            'user_ids': [Command.set(self.user.ids)],
            'model_rule_ids': [Command.create({
                'model_id': self.partner_model.id,
                'hide_view_mode_ids': [Command.set(view_mode.ids)],
            })],
        })
        rules = self.Management._get_user_model_rules(self.user)
        self.assertIn('access_guard_test_old', rules['res.partner']['view_modes'])
        view_mode.code = 'access_guard_test_new'
        rules = self.Management._get_user_model_rules(self.user)
        self.assertIn('access_guard_test_new', rules['res.partner']['view_modes'])
        self.assertNotIn('access_guard_test_old', rules['res.partner']['view_modes'])

        field = self.env['ir.model.fields']._get('res.partner', 'comment')
        item = self.env['access.guard.search.item'].create({
            'model_id': self.partner_model.id,
            'field_id': field.id,
            'type': 'filter',
            'label': 'Comment Test Filter',
        })
        self.Management._get_user_model_rules(self.user)
        self.assertTrue(getattr(self.env.registry, '_access_guard_rule_cache', {}))

        # Odoo's cache signal is how another worker learns that its process-local
        # Access Guard dictionary is stale.
        token = self.Management._access_guard_cache_token()
        self.env.registry.clear_cache()
        self.assertIsNot(token, self.Management._access_guard_cache_token())
        self.assertFalse(self.Management._get_rule_cache_store())

        self.Management._get_user_model_rules(self.user)
        item.label = 'Updated Comment Test Filter'
        self.assertFalse(getattr(self.env.registry, '_access_guard_rule_cache', {}))

    def test_menu_hiding_and_acl_lifecycle_still_work(self):
        menu = self.env['ir.ui.menu'].create({'name': 'Access Guard Test Menu'})
        rule = self.Management.create({
            'name': 'Existing Behavior',
            'user_ids': [Command.set(self.user.ids)],
            'menu_line_ids': [Command.create({'menu_id': menu.id})],
        })
        hidden = self.env['ir.ui.menu'].with_user(self.user)._access_guard_hidden_menu_ids()
        self.assertIn(menu.id, hidden)
        self.assertEqual(rule.rule_count, 1)

        group = self.env['res.groups'].create({'name': 'Access Guard ACL Group'})
        acl_line = self.env['access.guard.management.acl'].create({
            'management_id': rule.id,
            'model_id': self.partner_model.id,
            'group_id': group.id,
            'perm_read': True,
        })
        access = acl_line.access_id
        self.assertTrue(access.exists())
        acl_line.write({'perm_write': True})
        self.assertEqual(acl_line.access_id, access)
        self.assertTrue(access.perm_write)
        self.assertEqual(
            self.env['ir.model.access'].sudo().search_count([('id', '=', access.id)]),
            1,
        )
        acl_line.unlink()
        self.assertFalse(access.exists())
