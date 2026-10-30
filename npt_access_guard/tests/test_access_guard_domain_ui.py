from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccessGuardDomainUI(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({
            'groups_id': [Command.link(cls.env.ref('npt_access_guard.group_access_guard_manager').id)]
        })
        cls.DomainRule = cls.env['access.guard.management.domain']

    def test_supported_dynamic_values(self):
        domains = (
            "[('user_id', '=', uid)]",
            "[('user_id', '=', user.id)]",
            "[('company_id', '=', company_id)]",
            "[('company_id', 'in', company_ids)]",
            "[('category_id', 'in', [uid])]",
            "[('name', '=', context.get('some_key'))]",
        )
        for domain in domains:
            with self.subTest(domain=domain):
                self.assertTrue(self.DomainRule.validate_ui_domain('res.partner', domain))

    def test_logical_operators_and_static_domain(self):
        self.assertTrue(self.DomainRule.validate_ui_domain(
            'res.partner', "['&', ('name', '=', 'A'), ('user_id', '=', uid)]"
        ))
        self.assertTrue(self.DomainRule.validate_ui_domain(
            'res.partner', "[('name', '=', 'A')]"
        ))

    def test_unknown_field_and_malformed_domain_are_invalid(self):
        domains = (
            "[('non_existing_field', '=', uid)]",
            "[('name', '=', uid)",
            "['&', ('name', '=', uid)]",
            "[('name', 'unknown_operator', uid)]",
            "[('name', '=', other_variable)]",
            "[('name', '=', context.pop('some_key'))]",
        )
        for domain in domains:
            with self.subTest(domain=domain):
                self.assertFalse(self.DomainRule.validate_ui_domain('res.partner', domain))
