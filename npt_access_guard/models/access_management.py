import ast

from lxml import etree
import json
import logging
from odoo import api, fields, models, Command, tools, _
from odoo.tools.safe_eval import safe_eval
from odoo.tools.sql import column_exists
from odoo.osv import expression
from odoo.exceptions import UserError, ValidationError
from psycopg2 import errors as psycopg2_errors

_logger = logging.getLogger(__name__)


class AccessGuardManagement(models.Model):
    _name = "access.guard.management"
    _description = "Access Guard Rule"
    _rec_name = "name"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    user_ids = fields.Many2many(
        "res.users",
        "access_guard_management_user_rel",
        "management_id",
        "user_id",
        string="Users",
    )
    apply_by_group = fields.Boolean(string="Apply By Group")
    group_ids = fields.Many2many(
        "res.groups",
        "access_guard_management_group_rel",
        "management_id",
        "group_id",
        string="Groups",
    )
    read_only = fields.Boolean(string="Read-Only")
    apply_without_company = fields.Boolean(string="Apply Without Company")
    default_internal_user = fields.Boolean(string="Default Internal User")
    default_portal_user = fields.Boolean(string="Default Portal User")
    company_ids = fields.Many2many(
        "res.company",
        "access_guard_management_company_rel",
        "management_id",
        "company_id",
        string="Companies",
        help="Only apply this rule when the active company is in the selected list.",
    )
    disable_developer_mode = fields.Boolean(string="Disable Developer Mode")
    menu_line_ids = fields.One2many(
        "access.guard.management.menu",
        "management_id",
        string="Menu Lines",
    )
    menu_ids = fields.Many2many(
        "ir.ui.menu",
        string="Hidden Menus",
        compute="_compute_menu_ids",
        inverse="_inverse_menu_ids",
        help="Select menus to hide. This field provides a fast multi-select interface.",
    )
    model_rule_ids = fields.One2many(
        "access.guard.management.model",
        "management_id",
        string="Model Rules",
    )
    field_rule_ids = fields.One2many(
        "access.guard.management.field",
        "management_id",
        string="Field Rules",
    )
    domain_rule_ids = fields.One2many(
        "access.guard.management.domain",
        "management_id",
        string="Domain Rules",
    )
    acl_rule_ids = fields.One2many(
        "access.guard.management.acl",
        "management_id",
        string="ACL Rules",
        help="Create ir.model.access entries for a chosen group and model.",
    )
    ui_rule_ids = fields.One2many(
        "access.guard.management.ui",
        "management_id",
        string="Button/Tab Rules",
    )
    chatter_rule_ids = fields.One2many(
        "access.guard.management.chatter",
        "management_id",
        string="Chatter Rules",
    )
    global_hide_chatter = fields.Boolean(string="Hide Chatter")
    global_hide_send_message = fields.Boolean(string="Hide Send Message")
    global_hide_log_note = fields.Boolean(string="Hide Log Notes")
    global_hide_schedule_activity = fields.Boolean(string="Hide Schedule Activity")
    global_hide_import = fields.Boolean(string="Hide Import")
    global_hide_export = fields.Boolean(string="Hide Export")
    global_hide_spreadsheet = fields.Boolean(string="Hide Spreadsheet")
    global_hide_add_property = fields.Boolean(string="Hide Add Property")
    global_hide_search_panel = fields.Boolean(string="Hide Search Panel")
    global_hide_custom_filter = fields.Boolean(string="Hide Custom Filter")
    global_hide_custom_group = fields.Boolean(string="Hide Custom Group")
    global_hide_delete_favorite = fields.Boolean(string="Hide Delete Favorite")
    global_disable_login = fields.Boolean(string="Disable Login")
    # Inventory Overview control (Operation Type cards)
    overview_mode = fields.Selection(
        [
            ('hide', 'Hide Selected'),
            ('show', 'Show Only Selected'),
        ],
        string='Overview Mode',
        default='hide',
        help="How to apply the selected operation types on the Inventory Overview."
    )
    overview_picking_type_ids = fields.Many2many(
        'stock.picking.type',
        'access_guard_management_overview_picking_rel',
        'management_id',
        'picking_type_id',
        string='Overview Operation Types',
        help="Select Inventory Overview cards (operation types) to hide or to be the only ones shown."
    )
    field_conditional_ids = fields.One2many(
        "access.guard.management.field.conditional",
        "management_id",
        string="Field Conditional Rules",
    )
    search_panel_rule_ids = fields.One2many(
        "access.guard.management.search",
        "management_id",
        string="Search Panel Rules",
    )
    rule_count = fields.Integer(compute="_compute_rule_count", string="Access Rules")

    def _compute_rule_count(self):
        for record in self:
            record.rule_count = len(record.menu_line_ids)

    def _compute_menu_ids(self):
        for record in self:
            record.menu_ids = record.menu_line_ids.mapped("menu_id")

    def _inverse_menu_ids(self):
        for record in self:
            desired = set(record.menu_ids.ids)
            existing_lines = record.menu_line_ids
            commands = []
            for line in existing_lines:
                menu = line.menu_id
                menu_id = menu.id if menu else False
                if not menu_id or menu_id not in desired:
                    commands.append(Command.delete(line.id))
            existing_map = {
                line.menu_id.id: line
                for line in existing_lines
                if line.menu_id and line.menu_id.id in desired
            }
            next_sequence = (
                max(existing_lines.mapped("sequence") or [0]) + 10
            )
            for menu in record.menu_ids:
                if menu.id not in existing_map:
                    commands.append(
                        Command.create(
                            {
                                "menu_id": menu.id,
                                "sequence": next_sequence,
                            }
                        )
                    )
                    next_sequence += 10
            if commands:
                record.menu_line_ids = commands

    def _domain_eval_context(self, user=None):
        user = user or self.env.user
        return {
            "true": True,
            "false": False,
            "null": None,
            "True": True,
            "False": False,
            "None": None,
            "user": user,
            "uid": user.id,
            "company_id": self.env.company.id if self.env.company else False,
            "company_ids": self.env.companies.ids,
            "context": dict(self.env.context),
        }

    def _safe_domain_value(self, value, user=None):
        if not value:
            return []
        try:
            if isinstance(value, str):
                try:
                    domain = safe_eval(value, self._domain_eval_context(user=user))
                except Exception:
                    try:
                        domain = json.loads(value)
                    except Exception:
                        return []
            else:
                domain = value
        except Exception:
            return []
        if isinstance(domain, (list, tuple)):
            return list(domain)
        return []

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._invalidate_menu_cache()
        return result

    def unlink(self):
        result = super().unlink()
        self._invalidate_menu_cache()
        return result

    def _applies_to_user(self, user):
        self.ensure_one()
        if not user:
            return False
        context = self.env.context
        allowed_companies = context.get('allowed_company_ids')
        if allowed_companies and not isinstance(allowed_companies, (list, tuple, set)):
            allowed_companies = [allowed_companies]
        active_company_id = context.get('company_id')
        if not active_company_id and allowed_companies:
            active_company_id = allowed_companies[0]
        if not active_company_id:
            active_company_id = user.company_id.id
        if self.company_ids:
            company_ids = set(self.company_ids.ids)
            if allowed_companies:
                if not any(cid in company_ids for cid in allowed_companies):
                    return False
            else:
                if active_company_id and active_company_id not in company_ids:
                    return False
                if not active_company_id and not self.apply_without_company:
                    return False
        else:
            if not active_company_id and not self.apply_without_company:
                return False
        if self.user_ids and user in self.user_ids:
            return True
        if self.apply_by_group and self.group_ids and (self.group_ids & user.groups_id):
            return True
        if self.default_internal_user and user.has_group("base.group_user"):
            return True
        if self.default_portal_user and user.has_group("base.group_portal"):
            return True
        return False

    def _get_hidden_menu_ids(self, user):
        self.ensure_one()
        if not self.menu_line_ids:
            return set()
        hidden = set()
        for line in self.menu_line_ids:
            if not line.menu_id:
                continue
            hidden.add(line.menu_id.id)
        return hidden

    @api.model
    def _is_developer_mode_disabled(self, user=None):
        user = user or self.env.user
        rules = self._get_user_model_rules(user)
        return bool((rules.get('__global__') or {}).get('disable_developer_mode'))


    @tools.ormcache()
    def _access_guard_cache_token(self):
        """Change identity whenever Odoo invalidates ORM caches."""
        return object()

    def _get_rule_cache_store(self):
        registry = self.env.registry
        token = self._access_guard_cache_token()
        cache = getattr(registry, "_access_guard_rule_cache", None)
        cached_token = getattr(registry, "_access_guard_rule_cache_token", None)
        if cache is None or cached_token is not token:
            cache = {}
            setattr(registry, "_access_guard_rule_cache", cache)
            setattr(registry, "_access_guard_rule_cache_token", token)
        return cache

    def _clear_rule_cache(self):
        cache = getattr(self.env.registry, "_access_guard_rule_cache", None)
        if cache is not None:
            cache.clear()

    @api.model
    def _invalidate_access_guard_caches(self):
        """Clear both registry-wide and transaction-local Access Guard caches."""
        self._clear_rule_cache()
        for env in list(self.env.transaction.envs):
            cache = getattr(env, '_access_guard_rules_cache', None)
            if cache is not None:
                cache.clear()
        clear_cache = getattr(self.env.registry, 'clear_cache', None)
        if callable(clear_cache):
            clear_cache()

    def _make_rule_cache_key(self, user):
        companies = self.env.context.get("allowed_company_ids")
        if not companies:
            companies_key = ()
        else:
            if not isinstance(companies, (list, tuple, set)):
                companies = [companies]
            try:
                companies_key = tuple(sorted(int(c) for c in companies if c))
            except Exception:
                companies_key = tuple(sorted(companies))
        company_id = self.env.context.get("company_id") or False
        return (user.id, companies_key, company_id)

    def _invalidate_menu_cache(self):
        self._invalidate_access_guard_caches()
        return None


    @api.model
    def _get_user_model_rules(self, user, rule_ids=None):
        if not user:
            return {}
        focused_rule_ids = rule_ids.ids if hasattr(rule_ids, 'ids') else rule_ids
        use_cache = rule_ids is None
        cache = self._get_rule_cache_store() if use_cache else None
        cache_key = self._make_rule_cache_key(user) if use_cache else None
        if use_cache:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        def _empty_entry():
            return {
                'reports': set(),
                'actions': set(),
                'view_modes': set(),
                'views': set(),
                'hide_create': False,
                'hide_edit': False,
                'hide_delete': False,
                'hide_archive': False,
                'hide_duplicate': False,
                'hide_import': False,
                'hide_export': False,
                'hide_spreadsheet': False,
                'hide_add_property': False,
                'fields': {},
                'buttons': {},
                'smart_buttons': {},
                'tabs': {},
                'kanban_links': {},
                'chatter': {
                    'hide_all': False,
                    'hide_send_message': False,
                    'hide_log_note': False,
                    'hide_schedule_activity': False,
                },
                'search_panel': {
                    'hide_panel': False,
                    'hide_custom_filter': False,
                    'hide_custom_group': False,
                    'hide_delete_favorite': False,
                    'filters': set(),
                    'groups': set(),
                },
                'domain_rules': [],
                'disable_login': False,
                'field_conditionals': [],
                'disable_developer_mode': False,
                # UI-only inventory overview config (do NOT use for security/domain)
                'overview': [],
            }

        rules_map = {}
        global_entry = _empty_entry()

        def _ensure_entry(model_name):
            entry = rules_map.get(model_name)
            if entry is None:
                entry = _empty_entry()
                rules_map[model_name] = entry
            return entry

        rule_domain = [('active', '=', True)]
        if focused_rule_ids is not None:
            rule_domain.append(('id', 'in', focused_rule_ids))
        for rule in self.sudo().search(rule_domain):
            if not rule._applies_to_user(user):
                continue

            for model_rule in rule.model_rule_ids:
                model = model_rule.model_id.model
                if not model:
                    continue
                entry = _ensure_entry(model)
                entry['reports'].update(model_rule.report_ids.ids)
                entry['actions'].update(model_rule.action_ids.ids)
                entry['actions'].update(model_rule.window_action_ids.ids)
                entry['view_modes'].update(model_rule.hide_view_mode_ids.mapped('code'))
                entry['views'].update(model_rule.view_ids.ids)
                for flag in (
                    'hide_create',
                    'hide_edit',
                    'hide_delete',
                    'hide_archive',
                    'hide_duplicate',
                    'hide_import',
                    'hide_export',
                    'hide_spreadsheet',
                ):
                    if getattr(model_rule, flag):
                        entry[flag] = True

            for field_rule in rule.field_rule_ids:
                model = field_rule.model_id.model
                if not model:
                    continue
                rule_fields = field_rule.field_ids.filtered(lambda f: f.model_id == field_rule.model_id)
                if not rule_fields:
                    continue
                entry = _ensure_entry(model)
                for field in rule_fields:
                    field_entry = entry['fields'].setdefault(field.name, {
                        'invisible': False,
                        'readonly': False,
                        'required': False,
                        'no_create': False,
                        'no_create_edit': False,
                        'no_open': False,
                    })
                    if field_rule.invisible:
                        field_entry['invisible'] = True
                    if field_rule.read_only:
                        field_entry['readonly'] = True
                    if field_rule.required:
                        field_entry['required'] = True
                    if field_rule.remove_create_option:
                        field_entry['no_create'] = True
                    if field_rule.remove_edit_option:
                        field_entry['no_create_edit'] = True
                    if field_rule.remove_external_link:
                        field_entry['no_open'] = True

            for domain_rule in rule.domain_rule_ids:
                model = domain_rule.model_id.model
                if not model:
                    continue
                payload = domain_rule._export_rule_spec(user=user)
                if not payload:
                    continue
                entry = _ensure_entry(model)
                entry['domain_rules'].append(payload)

            for ui_rule in rule.ui_rule_ids:
                model = ui_rule.model_id.model
                if not model:
                    continue
                entry = _ensure_entry(model)
                def _consume_action_binding(element):
                    if element.match_mode != 'action_id':
                        return False
                    try:
                        action_id = int(element.match_value)
                    except (TypeError, ValueError):
                        return True
                    if action_id:
                        entry['actions'].add(action_id)
                    return True
                for element in ui_rule.button_element_ids.filtered('active'):
                    if _consume_action_binding(element):
                        continue
                    spec = element._export_rule_spec()
                    if spec:
                        entry['buttons'][spec['key']] = spec
                for element in ui_rule.action_button_element_ids.filtered('active'):
                    if _consume_action_binding(element):
                        continue
                    spec = element._export_rule_spec()
                    if spec:
                        entry['buttons'][spec['key']] = spec
                for element in ui_rule.smart_button_element_ids.filtered('active'):
                    spec = element._export_rule_spec()
                    if spec:
                        entry['smart_buttons'][spec['key']] = spec
                for element in ui_rule.tab_element_ids.filtered('active'):
                    spec = element._export_rule_spec()
                    if spec:
                        entry['tabs'][spec['key']] = spec
                for element in ui_rule.kanban_link_element_ids.filtered('active'):
                    spec = element._export_rule_spec()
                    if spec:
                        entry['kanban_links'][spec['key']] = spec

            for chatter_rule in rule.chatter_rule_ids:
                model = chatter_rule.model_id.model
                if not model:
                    continue
                entry = _ensure_entry(model)
                chatter_entry = entry['chatter']
                if chatter_rule.hide_chatter:
                    chatter_entry['hide_all'] = True
                if chatter_rule.hide_send_message:
                    chatter_entry['hide_send_message'] = True
                if chatter_rule.hide_log_note:
                    chatter_entry['hide_log_note'] = True
                if chatter_rule.hide_schedule_activity:
                    chatter_entry['hide_schedule_activity'] = True

            for conditional_rule in rule.field_conditional_ids:
                model = conditional_rule.model_id.model
                if not model:
                    continue
                payload = {}
                if (
                    conditional_rule.apply_attrs
                    and conditional_rule.attr_type
                    and conditional_rule.attrs_domain
                ):
                    attr_fields = conditional_rule.attr_field_ids
                    if not attr_fields and getattr(conditional_rule, 'attr_field_id', False):
                        attr_fields = conditional_rule.attr_field_id
                    button_elements = conditional_rule.attr_button_ids.filtered('active')
                    if not attr_fields and not button_elements:
                        continue
                    payload['apply_attrs'] = True
                    if hasattr(attr_fields, 'mapped'):
                        payload['attrs_fields'] = attr_fields.mapped('name')
                    elif attr_fields:
                        payload['attrs_fields'] = [attr_fields.name]
                    if button_elements:
                        button_specs = []
                        for element in button_elements:
                            spec = element._export_rule_spec()
                            if spec:
                                spec['element_type'] = element.type
                                button_specs.append(spec)
                        if button_specs:
                            payload['attrs_buttons'] = button_specs
                    payload['attrs_type'] = conditional_rule.attr_type
                    payload['attrs_domain'] = self._safe_domain_value(conditional_rule.attrs_domain, user=user)
                if (
                    conditional_rule.apply_domain
                    and conditional_rule.domain_field_id
                    and conditional_rule.domain_expression
                ):
                    payload['apply_domain'] = True
                    payload['domain_field'] = conditional_rule.domain_field_id.name
                    payload['domain_expression'] = self._safe_domain_value(conditional_rule.domain_expression, user=user)
                if payload:
                    entry = _ensure_entry(model)
                    entry['field_conditionals'].append(payload)

            if rule.global_hide_chatter:
                global_entry['chatter']['hide_all'] = True
            if rule.global_hide_send_message:
                global_entry['chatter']['hide_send_message'] = True
            if rule.global_hide_log_note:
                global_entry['chatter']['hide_log_note'] = True
            if rule.global_hide_schedule_activity:
                global_entry['chatter']['hide_schedule_activity'] = True
            if rule.global_hide_import:
                global_entry['hide_import'] = True
            if rule.global_hide_export:
                global_entry['hide_export'] = True
            if rule.global_hide_spreadsheet:
                global_entry['hide_spreadsheet'] = True
            if rule.global_hide_add_property:
                global_entry['hide_add_property'] = True
            if rule.global_hide_search_panel:
                global_entry['search_panel']['hide_panel'] = True
            if rule.global_hide_custom_filter:
                global_entry['search_panel']['hide_custom_filter'] = True
            if rule.global_hide_custom_group:
                global_entry['search_panel']['hide_custom_group'] = True
            if rule.global_hide_delete_favorite:
                global_entry['search_panel']['hide_delete_favorite'] = True
            if rule.global_disable_login:
                global_entry['disable_login'] = True
            if rule.disable_developer_mode:
                global_entry['disable_developer_mode'] = True
            for search_rule in rule.search_panel_rule_ids:
                model = search_rule.model_id.model
                if not model:
                    continue
                entry = _ensure_entry(model)
                panel_entry = entry['search_panel']
                if search_rule.hide_search_panel:
                    panel_entry['hide_panel'] = True
                if search_rule.hide_custom_filter:
                    panel_entry['hide_custom_filter'] = True
                if search_rule.hide_custom_group:
                    panel_entry['hide_custom_group'] = True
                if search_rule.hide_delete_favorite:
                    panel_entry['hide_delete_favorite'] = True
                filter_names = search_rule.filter_item_ids.mapped('field_name')
                if filter_names:
                    panel_entry['filters'].update(filter_names)
                group_names = search_rule.group_item_ids.mapped('field_name')
                if group_names:
                    panel_entry['groups'].update(group_names)

            # Inventory Overview configuration (stock.picking.type kanban).
            # NOTE: this is UI-only. We intentionally do NOT add domain_rules here,
            # to avoid impacting core flows (e.g., Purchases looking up Receipts).
            if rule.overview_picking_type_ids:
                ids = [pid for pid in rule.overview_picking_type_ids.ids if pid]
                if ids:
                    entry = _ensure_entry('stock.picking.type')
                    entry['overview'].append({
                        'mode': rule.overview_mode or 'hide',
                        'ids': ids,
                    })

        def _serialize_entry(values):
            return {
                'reports': list(values['reports']),
                'actions': list(values['actions']),
                'view_modes': list(values['view_modes']),
                'views': list(values['views']),
                'hide_create': values['hide_create'],
                'hide_edit': values['hide_edit'],
                'hide_delete': values['hide_delete'],
                'hide_archive': values['hide_archive'],
                'hide_duplicate': values['hide_duplicate'],
                'hide_import': values['hide_import'],
                'hide_export': values['hide_export'],
                'hide_spreadsheet': values['hide_spreadsheet'],
                'hide_add_property': values['hide_add_property'],
                'fields': {name: dict(conf) for name, conf in values['fields'].items()},
                'buttons': list(values['buttons'].values()),
                'smart_buttons': list(values['smart_buttons'].values()),
                'tabs': list(values['tabs'].values()),
                'kanban_links': list(values['kanban_links'].values()),
                'chatter': dict(values['chatter']),
                'search_panel': {
                    'hide_panel': values['search_panel']['hide_panel'],
                    'hide_custom_filter': values['search_panel']['hide_custom_filter'],
                    'hide_custom_group': values['search_panel']['hide_custom_group'],
                    'hide_delete_favorite': values['search_panel']['hide_delete_favorite'],
                    'filters': list(values['search_panel']['filters']),
                    'groups': list(values['search_panel']['groups']),
                },
                'domain_rules': [dict(rule) for rule in values['domain_rules']],
                'disable_login': values['disable_login'],
                'field_conditionals': [dict(rule) for rule in values['field_conditionals']],
                'disable_developer_mode': values['disable_developer_mode'],
                'overview': [dict(rule) for rule in values['overview']],
            }

        serialized = {'__global__': _serialize_entry(global_entry)} if any(
            (
                global_entry['reports'],
                global_entry['actions'],
                global_entry['view_modes'],
                global_entry['views'],
                global_entry['hide_create'],
                global_entry['hide_edit'],
                global_entry['hide_delete'],
                global_entry['hide_archive'],
                global_entry['hide_duplicate'],
                global_entry['hide_import'],
                global_entry['hide_export'],
                global_entry['hide_spreadsheet'],
                global_entry['hide_add_property'],
                any(global_entry['fields']),
                any(global_entry['buttons']),
                any(global_entry['smart_buttons']),
                any(global_entry['tabs']),
                any(global_entry['kanban_links']),
                any(global_entry['chatter'].values()),
                any(global_entry['search_panel'].values()),
                global_entry['domain_rules'],
                global_entry['disable_login'],
                global_entry['field_conditionals'],
                global_entry['disable_developer_mode'],
            )
        ) else {}
        for model, values in rules_map.items():
            serialized[model] = _serialize_entry(values)
        if use_cache:
            cache[cache_key] = serialized
        return serialized

    @api.model
    def _merge_rule_maps(self, *rule_sets):
        merged = {
            'reports': set(),
            'actions': set(),
            'view_modes': set(),
            'views': set(),
            'hide_create': False,
            'hide_edit': False,
            'hide_delete': False,
            'hide_archive': False,
            'hide_duplicate': False,
            'hide_import': False,
            'hide_export': False,
            'hide_spreadsheet': False,
            'hide_add_property': False,
            'fields': {},
            'buttons': {},
            'smart_buttons': {},
            'tabs': {},
            'kanban_links': {},
            'chatter': {
                'hide_all': False,
                'hide_send_message': False,
                'hide_log_note': False,
                'hide_schedule_activity': False,
            },
            'search_panel': {
                'hide_panel': False,
                'hide_custom_filter': False,
                'hide_custom_group': False,
                'hide_delete_favorite': False,
                'filters': set(),
                'groups': set(),
            },
            'domain_rules': [],
            'disable_login': False,
            'field_conditionals': [],
            'disable_developer_mode': False,
            'overview': [],
        }
        has_value = False
        for rules in rule_sets:
            if not rules:
                continue
            reports = rules.get('reports') or []
            if reports:
                merged['reports'].update(reports)
                has_value = True
            actions = rules.get('actions') or []
            if actions:
                merged['actions'].update(actions)
                has_value = True
            view_modes = rules.get('view_modes') or []
            if view_modes:
                merged['view_modes'].update(view_modes)
                has_value = True
            views = rules.get('views') or []
            if views:
                merged['views'].update(views)
                has_value = True
            for key in (
                'hide_create',
                'hide_edit',
                'hide_delete',
                'hide_archive',
                'hide_duplicate',
                'hide_import',
                'hide_export',
                'hide_spreadsheet',
                'hide_add_property',
            ):
                if rules.get(key):
                    merged[key] = True
                    has_value = True
            fields = rules.get('fields') or {}
            for field_name, conf in fields.items():
                existing = merged['fields'].setdefault(field_name, {})
                existing.update(conf)
                has_value = True
            for bucket_key in ('buttons', 'smart_buttons', 'tabs', 'kanban_links'):
                bucket = rules.get(bucket_key) or []
                if bucket:
                    has_value = True
                storage = merged[bucket_key]
                for item in bucket:
                    key = item.get('key')
                    if not key:
                        continue
                    storage[key] = dict(item)
            chatter_conf = rules.get('chatter') or {}
            for key, value in chatter_conf.items():
                if value:
                    merged['chatter'][key] = True
                    has_value = True
            search_conf = rules.get('search_panel') or {}
            if search_conf.get('hide_panel'):
                merged['search_panel']['hide_panel'] = True
                has_value = True
            if search_conf.get('hide_custom_filter'):
                merged['search_panel']['hide_custom_filter'] = True
                has_value = True
            if search_conf.get('hide_custom_group'):
                merged['search_panel']['hide_custom_group'] = True
                has_value = True
            if search_conf.get('hide_delete_favorite'):
                merged['search_panel']['hide_delete_favorite'] = True
                has_value = True
            filters_conf = search_conf.get('filters') or []
            if filters_conf:
                merged['search_panel']['filters'].update(filters_conf)
                has_value = True
            groups_conf = search_conf.get('groups') or []
            if groups_conf:
                merged['search_panel']['groups'].update(groups_conf)
                has_value = True
            domain_rules = rules.get('domain_rules') or []
            for spec in domain_rules:
                spec_data = dict(spec)
                merged['domain_rules'].append(spec_data)
                has_value = True
                if spec_data.get('soft_restrict'):
                    if not spec_data.get('allow_create', True):
                        merged['hide_create'] = True
                    if not spec_data.get('allow_write', True):
                        merged['hide_edit'] = True
                    if not spec_data.get('allow_delete', True):
                        merged['hide_delete'] = True
            if domain_rules:
                has_value = True
            if rules.get('disable_login'):
                merged['disable_login'] = True
                has_value = True
            if rules.get('disable_developer_mode'):
                merged['disable_developer_mode'] = True
                has_value = True
            field_conditionals = rules.get('field_conditionals') or []
            if field_conditionals:
                merged['field_conditionals'].extend(dict(item) for item in field_conditionals)
                has_value = True
            overview_conf = rules.get('overview') or []
            if overview_conf:
                merged['overview'].extend(dict(item) for item in overview_conf)
                has_value = True
        if not has_value:
            return {}
        return {
            'reports': list(merged['reports']),
            'actions': list(merged['actions']),
            'view_modes': list(merged['view_modes']),
            'views': list(merged['views']),
            'hide_create': merged['hide_create'],
            'hide_edit': merged['hide_edit'],
            'hide_delete': merged['hide_delete'],
            'hide_archive': merged['hide_archive'],
            'hide_duplicate': merged['hide_duplicate'],
            'hide_import': merged['hide_import'],
            'hide_export': merged['hide_export'],
            'hide_spreadsheet': merged['hide_spreadsheet'],
            'hide_add_property': merged['hide_add_property'],
            'fields': {name: dict(conf) for name, conf in merged['fields'].items()},
            'buttons': list(merged['buttons'].values()),
            'smart_buttons': list(merged['smart_buttons'].values()),
            'tabs': list(merged['tabs'].values()),
            'kanban_links': list(merged['kanban_links'].values()),
            'chatter': dict(merged['chatter']),
            'search_panel': {
                'hide_panel': merged['search_panel']['hide_panel'],
                'hide_custom_filter': merged['search_panel']['hide_custom_filter'],
                'hide_custom_group': merged['search_panel']['hide_custom_group'],
                'hide_delete_favorite': merged['search_panel']['hide_delete_favorite'],
                'filters': list(merged['search_panel']['filters']),
                'groups': list(merged['search_panel']['groups']),
            },
            'domain_rules': list(merged['domain_rules']),
            'disable_login': merged['disable_login'],
            'field_conditionals': list(merged['field_conditionals']),
            'disable_developer_mode': merged['disable_developer_mode'],
            'overview': list(merged['overview']),
        }


    def action_open_menu_wizard(self):
        self.ensure_one()
        if not self.id:
            raise UserError('Save the rule before adding menus.')
        action = self.env.ref('npt_access_guard.action_access_guard_menu_add_wizard').sudo().read()[0]
        action['context'] = {
            'default_management_id': self.id,
        }
        return action

    def action_view_rules(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Hidden Menus",
            "res_model": "access.guard.management.menu",
            "view_mode": "list,form",
            "domain": [("management_id", "=", self.id)],
            "context": {"default_management_id": self.id},
        }


class AccessGuardManagementMenu(models.Model):
    _name = "access.guard.management.menu"
    _description = "Access Guard Menu Rule"
    _order = "sequence, id"

    management_id = fields.Many2one(
        "access.guard.management",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    menu_id = fields.Many2one("ir.ui.menu", required=True, ondelete="cascade")
    include_children = fields.Boolean(
        default=False,
        help="(Deprecated) Previously used to hide child menus.",
    )

    _sql_constraints = [
        (
            "access_guard_management_menu_uniq",
            "unique(management_id, menu_id)",
            "Each menu can only be added once per rule.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.mapped('management_id')._invalidate_menu_cache()
        return lines

    def write(self, vals):
        result = super().write(vals)
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result

    def name_get(self):
        result = []
        for record in self:
            label = record.menu_id.display_name if record.menu_id else "Unknown Menu"
            result.append((record.id, label))
        return result


class AccessGuardManagementFieldConditional(models.Model):
    _name = 'access.guard.management.field.conditional'
    _description = 'Access Guard Field Conditional Rule'
    _order = 'sequence, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(compute='_compute_model_name', store=True, readonly=True)
    apply_attrs = fields.Boolean(string='Apply Attrs')
    apply_domain = fields.Boolean(string='Apply Domain')
    attr_field_ids = fields.Many2many(
        'ir.model.fields',
        'access_guard_management_field_conditional_attr_rel',
        'conditional_id',
        'field_id',
        string='Attrs On Fields',
        domain="[('model_id', '=', model_id)]",
    )
    attr_button_ids = fields.Many2many(
        'access.guard.ui.element',
        'access_guard_management_field_conditional_button_rel',
        'conditional_id',
        'element_id',
        string='Attrs On Buttons',
        domain="[('model_id', '=', model_id), ('type', 'in', ('button', 'action_button', 'smart_button'))]",
    )
    attr_field_id = fields.Many2one(
        'ir.model.fields',
        string='Attrs On Field',
        compute='_compute_attr_field_id',
        inverse='_inverse_attr_field_id',
        store=False,
    )
    attr_type = fields.Selection(
        [
            ('invisible', 'Invisible'),
            ('readonly', 'Read-Only'),
            ('required', 'Required'),
        ],
        string='Attrs Type',
    )
    attrs_domain = fields.Char(string='Attrs', help='Domain expression that controls the selected attribute.')
    domain_field_id = fields.Many2one(
        'ir.model.fields',
        string='Domain On Field',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('many2one', 'one2many', 'many2many'))]",
    )
    domain_expression = fields.Char(string='Domain', help='Domain applied to the selected field.')
    attrs_domain_display = fields.Char(string='Attrs', compute='_compute_domain_display', readonly=True)
    domain_expression_display = fields.Char(string='Domain', compute='_compute_domain_display', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        item_model = self.env['access.guard.search.item']
        Element = self.env['access.guard.ui.element']
        for vals in vals_list:
            model_id = vals.get('model_id')
            if model_id:
                item_model._ensure_items_for_model(model_id)
                Element._ensure_elements_for_model(model_id, target_types=['button', 'action_button', 'smart_button'])
        records = super().create(vals_list)
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        if 'model_id' in vals and vals['model_id']:
            self.env['access.guard.search.item']._ensure_items_for_model(vals['model_id'])
            self.env['access.guard.ui.element']._ensure_elements_for_model(
                vals['model_id'], target_types=['button', 'action_button', 'smart_button']
            )
        result = super().write(vals)
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result

    @api.onchange('model_id')
    def _onchange_model_id(self):
        Element = self.env['access.guard.ui.element']
        self.attr_field_ids = [(Command.clear())]
        self.attr_button_ids = [(Command.clear())]
        self.attr_field_id = False
        self.domain_field_id = False
        self.attrs_domain = False
        self.domain_expression = False
        if self.model_id:
            Element._ensure_elements_for_model(self.model_id, target_types=['button', 'action_button', 'smart_button'])
            domain = [('model_id', '=', self.model_id.id)]
            return {
                'domain': {
                    'attr_field_ids': domain,
                    'attr_field_id': domain,
                    'attr_button_ids': domain + [('type', 'in', ('button', 'action_button', 'smart_button'))],
                    'domain_field_id': domain + [('ttype', 'in', ('many2one', 'one2many', 'many2many'))],
                }
            }
        return {
            'domain': {
                'attr_field_ids': [],
                'attr_field_id': [],
                'attr_button_ids': [],
                'domain_field_id': [],
            }
        }

    @api.depends('model_id')
    def _compute_model_name(self):
        for record in self:
            record.model_name = record.model_id.model if record.model_id else False

    def _compute_domain_display(self):
        for record in self:
            record.attrs_domain_display = record.attrs_domain or ''
            record.domain_expression_display = record.domain_expression or ''

    @api.constrains('apply_attrs', 'attr_field_ids', 'attr_button_ids', 'attr_type', 'attrs_domain')
    def _check_attrs_targets(self):
        for record in self:
            if not record.apply_attrs:
                continue
            if not record.attr_type or not record.attrs_domain:
                continue
            if not record.attr_field_ids and not record.attr_button_ids:
                raise ValidationError(_("Select at least one field or button to apply attrs."))

    def init(self):
        super().init()
        cr = self.env.cr
        if not column_exists(cr, self._table, 'attr_field_id'):
            return
        cr.execute("SELECT to_regclass('access_guard_management_field_conditional_attr_rel')")
        if not cr.fetchone()[0]:
            return
        cr.execute(
            """
                INSERT INTO access_guard_management_field_conditional_attr_rel (conditional_id, field_id)
                SELECT id, attr_field_id
                FROM access_guard_management_field_conditional
                WHERE attr_field_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM access_guard_management_field_conditional_attr_rel rel
                      WHERE rel.conditional_id = access_guard_management_field_conditional.id
                        AND rel.field_id = access_guard_management_field_conditional.attr_field_id
                  )
            """
        )
        cr.execute("ALTER TABLE access_guard_management_field_conditional DROP COLUMN attr_field_id")


class AccessGuardManagementDomain(models.Model):
    _name = 'access.guard.management.domain'
    _description = 'Access Guard Domain Rule'
    _order = 'sequence, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(compute='_compute_model_name', store=True, readonly=True)
    soft_restrict = fields.Boolean(
        string='Soft Restrict',
        help=(
            'Do not enforce create, update, or delete denials in the backend; '
            'hide the corresponding record actions in the UI. Read permissions '
            'and filters still affect record visibility.'
        ),
    )
    apply_relational_fields = fields.Boolean(
        string='Apply On Relational Fields',
        help='Propagate this rule to related records opened from this model.',
    )
    perm_read = fields.Boolean(string='Read', default=True)
    perm_create = fields.Boolean(string='Create', default=True)
    perm_write = fields.Boolean(string='Update', default=True)
    perm_delete = fields.Boolean(string='Delete', default=True)
    apply_filter = fields.Boolean(string='Apply Filter')
    domain_expression = fields.Char(
        string='Filter',
        help='Domain expression restricting the records available to the rule targets.',
    )
    domain_expression_display = fields.Char(
        string='Filter',
        compute='_compute_domain_display',
        readonly=True,
    )

    @api.model
    def validate_ui_domain(self, model_name, domain):
        resolver = self.env['access.guard.import.resolver']
        resolver._check_resolver_access()
        if not isinstance(domain, str) or not model_name or model_name not in self.env:
            return False
        result = resolver.resolve_domain(
            resolver._new_analysis(), domain, model_name, 'domain_access', 'Domain Access filter'
        )
        if result['status'] not in ('resolved', 'mapping_required'):
            return False
        try:
            root = ast.parse(domain, mode='eval').body
            if not isinstance(root, (ast.List, ast.Tuple)):
                return False
            tokens = []
            for node in root.elts:
                if isinstance(node, ast.Constant) and node.value in expression.DOMAIN_OPERATORS:
                    tokens.append(node.value)
                elif isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) == 3:
                    field, operator, _value = node.elts
                    if not (
                        isinstance(field, ast.Constant) and isinstance(field.value, str)
                        and isinstance(operator, ast.Constant)
                        and isinstance(operator.value, str)
                        and operator.value in expression.TERM_OPERATORS
                    ):
                        return False
                    tokens.append((field.value, operator.value, False))
                else:
                    return False
            expression.normalize_domain(tokens)
        except (SyntaxError, ValueError, TypeError, AssertionError):
            return False
        return True

    @api.depends('model_id')
    def _compute_model_name(self):
        for record in self:
            record.model_name = record.model_id.model if record.model_id else False

    @api.depends('domain_expression')
    def _compute_domain_display(self):
        for record in self:
            record.domain_expression_display = record.domain_expression or ''

    @api.onchange('apply_filter')
    def _onchange_apply_filter(self):
        if not self.apply_filter:
            self.domain_expression = False

    @api.onchange('model_id')
    def _onchange_model_id(self):
        if not self.model_id:
            self.domain_expression = False

    def _export_rule_spec(self, user=None):
        self.ensure_one()
        management = self.management_id or self.env['access.guard.management']
        domain = []
        if self.apply_filter and self.domain_expression:
            domain = management._safe_domain_value(self.domain_expression, user=user)
        return {
            'soft_restrict': self.soft_restrict,
            'apply_relational': self.apply_relational_fields,
            'allow_read': self.perm_read,
            'allow_create': self.perm_create,
            'allow_write': self.perm_write,
            'allow_delete': self.perm_delete,
            'apply_filter': bool(domain),
            'domain': domain,
            'enforce_company': not bool(management.apply_without_company),
        }


class AccessGuardSearchItem(models.Model):
    _name = 'access.guard.search.item'
    _description = 'Access Guard Search Item'
    _order = 'model_id, type, label, id'
    _rec_name = 'label'

    TYPE_SELECTION = [
        ('filter', 'Filter'),
        ('group', 'Group By'),
    ]

    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    field_id = fields.Many2one('ir.model.fields', required=True, ondelete='cascade')
    field_name = fields.Char(related='field_id.name', store=True, readonly=True)
    type = fields.Selection(TYPE_SELECTION, required=True, default='filter')
    label = fields.Char(required=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'access_guard_search_item_unique',
            'unique(model_id, type, field_id)',
            'Each search panel item can only be defined once per model.',
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return result

    def unlink(self):
        result = super().unlink()
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return result

    def name_get(self):
        result = []
        for record in self:
            label = record.label or record.field_id.field_description or record.field_name
            result.append((record.id, label))
        return result

    @api.model
    def _ensure_items_for_model(self, model, target_types=None):
        self = self.sudo()
        if not model:
            return
        if isinstance(model, int):
            model = self.env['ir.model'].browse(model)
        if not model or not model.exists():
            return
        model = model.sudo()
        model_name = model.model
        if not model_name:
            return
        allowed_types = {'filter', 'group'}
        if target_types:
            allowed_types &= set(target_types)
        if not allowed_types:
            return

        existing = self.search([('model_id', '=', model.id)])
        existing_map = {(item.type, item.field_id.id): item for item in existing}

        View = self.env['ir.ui.view'].with_context(
            access_guard_skip_guard=True,
            access_guard_skip_search_rules=True,
        ).sudo()
        views = View.search([('model', '=', model_name), ('type', '=', 'search')])
        if not views:
            return

        model_env = self.env[model_name].with_context(
            access_guard_skip_guard=True,
            access_guard_skip_search_rules=True,
        ).sudo()

        field_model = self.env['ir.model.fields'].sudo()

        for view in views:
            arch = None
            if hasattr(model_env, 'get_view'):
                try:
                    view_info = model_env.get_view(view_id=view.id, view_type='search')
                    if isinstance(view_info, dict):
                        arch = view_info.get('arch')
                except Exception:
                    arch = None
            if not arch:
                arch = view.arch_db
            if isinstance(arch, bytes):
                arch = arch.decode()
            if not arch:
                continue
            try:
                arch_doc = etree.fromstring(arch)
            except Exception:
                continue
            for field_node in arch_doc.xpath(".//searchpanel/field[@name]"):
                field_name = field_node.get('name')
                if not field_name:
                    continue
                field = field_model.search(
                    [('model_id', '=', model.id), ('name', '=', field_name)],
                    limit=1,
                )
                if not field:
                    continue
                if field_node.get('groupby'):
                    item_type = 'group'
                else:
                    item_type = 'filter'
                if item_type not in allowed_types:
                    continue
                key = (item_type, field.id)
                label = field_node.get('string') or field.field_description or field_name
                if key in existing_map:
                    item = existing_map[key]
                    if item.label != label:
                        item.write({'label': label})
                    continue
                self.create({
                    'model_id': model.id,
                    'field_id': field.id,
                    'type': item_type,
                    'label': label,
                })


class AccessGuardManagementSearch(models.Model):
    _name = 'access.guard.management.search'
    _description = 'Access Guard Search Panel Rule'
    _order = 'sequence, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(compute='_compute_model_name', store=True, readonly=True)
    hide_search_panel = fields.Boolean(string='Hide Search Panel')
    filter_item_ids = fields.Many2many(
        'access.guard.search.item',
        'access_guard_search_rule_filter_rel',
        'rule_id',
        'item_id',
        string='Hide Filters',
        domain="[('model_id', '=', model_id), ('type', '=', 'filter')]",
    )
    group_item_ids = fields.Many2many(
        'access.guard.search.item',
        'access_guard_search_rule_group_rel',
        'rule_id',
        'item_id',
        string='Hide Groups',
        domain="[('model_id', '=', model_id), ('type', '=', 'group')]",
    )
    hide_custom_filter = fields.Boolean(string='Hide Custom Filter')
    hide_custom_group = fields.Boolean(string='Hide Custom Group')
    hide_delete_favorite = fields.Boolean(string='Hide Delete in Favorite Filters')

    _sql_constraints = [
        (
            'access_guard_management_search_unique',
            'unique(management_id, model_id)',
            'Each model can only be added once per rule.',
        )
    ]

    @api.depends('model_id')
    def _compute_model_name(self):
        for record in self:
            record.model_name = record.model_id.model if record.model_id else False

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.filter_item_ids = [(Command.clear())]
        self.group_item_ids = [(Command.clear())]
        if self.model_id:
            self.env['access.guard.search.item']._ensure_items_for_model(self.model_id)
            return {
                'domain': {
                    'filter_item_ids': [('model_id', '=', self.model_id.id), ('type', '=', 'filter')],
                    'group_item_ids': [('model_id', '=', self.model_id.id), ('type', '=', 'group')],
                }
            }
        return {
            'domain': {
                'filter_item_ids': [('id', '=', False)],
                'group_item_ids': [('id', '=', False)],
            }
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result

    @api.onchange('apply_attrs')
    def _onchange_apply_attrs(self):
        if not self.apply_attrs:
            self.attr_field_ids = [(Command.clear())]
            self.attr_field_id = False
            self.attr_type = False
            self.attrs_domain = False

    @api.onchange('apply_domain')
    def _onchange_apply_domain(self):
        if not self.apply_domain:
            self.domain_field_id = False
            self.domain_expression = False

    def name_get(self):
        result = []
        for record in self:
            model_label = record.model_id.display_name if record.model_id else "Unknown Model"
            field_labels = record.attr_field_ids.mapped('display_name')
            if field_labels:
                field_label = ", ".join(field_labels)
            else:
                field_label = record.domain_field_id.display_name if record.domain_field_id else ""
            label = f"{model_label} - {field_label}" if field_label else model_label
            result.append((record.id, label))
        return result

    def _compute_attr_field_id(self):
        for record in self:
            record.attr_field_id = record.attr_field_ids[:1].id if record.attr_field_ids else False

    def _inverse_attr_field_id(self):
        for record in self:
            if record.attr_field_id:
                record.attr_field_ids = [Command.set([record.attr_field_id.id])]
            else:
                record.attr_field_ids = [Command.clear()]

    def _safe_domain_value(self, value, user=None):
        if not value:
            return []
        management = self.management_id or self.env['access.guard.management']
        try:
            if isinstance(value, str):
                try:
                    domain = safe_eval(value, management._domain_eval_context(user=user))
                except Exception:
                    try:
                        domain = json.loads(value)
                    except Exception:
                        return []
            else:
                domain = value
        except Exception:
            return []
        if isinstance(domain, (list, tuple)):
            return list(domain)
        return []


class AccessGuardManagementChatter(models.Model):
    _name = 'access.guard.management.chatter'
    _description = 'Access Guard Chatter Rule'
    _order = 'sequence, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    hide_chatter = fields.Boolean(string='Chatter')
    hide_send_message = fields.Boolean(string='Send Message')
    hide_log_note = fields.Boolean(string='Log Notes')
    hide_schedule_activity = fields.Boolean(string='Schedule Activity')

    _sql_constraints = [
        (
            'access_guard_management_chatter_unique',
            'unique(management_id, model_id)',
            'Each model can only be added once per rule.',
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result

    def name_get(self):
        result = []
        for record in self:
            label = record.model_id.display_name if record.model_id else "Unknown Model"
            result.append((record.id, label))
        return result




class AccessGuardViewMode(models.Model):
    _name = 'access.guard.view.mode'
    _description = 'Access Guard View Mode'
    _order = 'name'

    name = fields.Char(required=True)
    code = fields.Char(required=True)

    _sql_constraints = [
        ('access_guard_view_mode_code_unique', 'unique(code)', 'Each view mode must be unique.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return result

    def unlink(self):
        result = super().unlink()
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return result


class AccessGuardUiElement(models.Model):
    _name = 'access.guard.ui.element'
    _description = 'Access Guard UI Element'
    _order = 'model_id, type, label, id'
    _rec_name = 'label'

    MATCH_MODES = [
        ('name', 'XML Name'),
        ('string', 'Label'),
        ('attribute', 'Attribute'),
        ('xpath', 'XPath'),
        ('action_id', 'Action ID'),
    ]

    TYPE_SELECTION = [
        ('button', 'Server Button'),
        ('action_button', 'Window Button'),
        ('smart_button', 'Smart Button'),
        ('page', 'Tab/Page'),
        ('kanban_link', 'Kanban Link'),
    ]

    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    type = fields.Selection(TYPE_SELECTION, required=True, default='button')
    match_mode = fields.Selection(MATCH_MODES, required=True, default='name')
    match_value = fields.Char(string='Match Value', required=True)
    attribute_name = fields.Char(string='Attribute Name', default='')
    label = fields.Char(required=True)
    view_type = fields.Selection(
        [('form', 'Form'), ('list', 'List'), ('kanban', 'Kanban')],
        required=True,
        default='form',
    )
    view_id = fields.Many2one('ir.ui.view', string='Source View')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'access_guard_ui_element_unique',
            'unique(model_id, type, match_mode, match_value, attribute_name)',
            'Each UI element can only be defined once per model.',
        )
    ]

    def name_get(self):
        result = []
        for record in self:
            label = record.label or record.match_value
            display = label
            if record.match_mode == 'name' and record.match_value and record.match_value != label:
                display = f"{label} ({record.match_value})"
            elif record.match_mode == 'attribute' and record.attribute_name:
                display = f"{label} [{record.attribute_name}={record.match_value}]"
            elif record.match_mode == 'xpath':
                display = f"{label} (xpath)"
            elif record.match_mode == 'action_id':
                display = f"{label} [action #{record.match_value}]"
            result.append((record.id, display))
        return result

    def _rule_cache_key(self):
        self.ensure_one()
        return f"{self.type}:{self.view_type}:{self.match_mode}:{self.attribute_name}:{self.match_value}"

    def _export_rule_spec(self):
        self.ensure_one()
        if not self.match_value:
            return False
        match = self.match_mode or 'name'
        spec = {
            'key': self._rule_cache_key(),
            'match': match,
            'value': self.match_value,
            'view_type': self.view_type or ('kanban' if self.type == 'kanban_link' else 'form'),
        }
        if match == 'attribute' and self.attribute_name:
            spec['attribute'] = self.attribute_name
        return spec

    def _invalidate_related_rules(self):
        if not self:
            return
        ui_rules = self.env['access.guard.management.ui'].search([
            '|',
            '|',
            '|',
            '|',
            ('button_element_ids', 'in', self.ids),
            ('action_button_element_ids', 'in', self.ids),
            ('smart_button_element_ids', 'in', self.ids),
            ('tab_element_ids', 'in', self.ids),
            ('kanban_link_element_ids', 'in', self.ids),
        ])
        if ui_rules:
            ui_rules.mapped('management_id')._invalidate_menu_cache()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(key in vals for key in (
            'model_id', 'match_mode', 'match_value', 'attribute_name', 'type',
            'view_type', 'view_id', 'active',
        )):
            self._invalidate_related_rules()
        return res

    def unlink(self):
        self._invalidate_related_rules()
        return super().unlink()

    @api.model
    def _ensure_elements_for_model(self, model, target_types=None):
        self = self.with_context(access_guard_skip_autoload=True).sudo()
        if not model:
            return
        if isinstance(model, int):
            model = self.env['ir.model'].browse(model)
        if not model or not model.exists():
            return
        model = model.sudo()
        model_name = model.model
        if not model_name:
            return

        allowed_types = {'button', 'action_button', 'smart_button', 'page', 'kanban_link'}
        if target_types:
            if isinstance(target_types, str):
                target_types = [target_types]
            target_types = [t for t in target_types if t in allowed_types]
        else:
            target_types = list(allowed_types)
        if not target_types:
            return

        view_types = set()
        if {'button', 'action_button', 'smart_button', 'page'} & set(target_types):
            view_types.add('form')
        if {'button', 'action_button'} & set(target_types):
            view_types.update({'tree', 'list'})
        if 'kanban_link' in target_types:
            view_types.add('kanban')
        view_types = list(view_types)
        monitored_domain = [
            ('model_id', '=', model.id),
            ('type', 'in', target_types),
        ]
        debug = bool(self.env.context.get('access_guard_debug'))
        before = None
        if debug:
            before = self.with_context(access_guard_skip_autoload=True).search_count(monitored_domain)
        View = self.env['ir.ui.view'].with_context(access_guard_skip_guard=True).sudo()
        views = View.search([('model', '=', model_name), ('type', 'in', view_types)])
        processed_views = len(views)
        model_env = self.env[model_name].with_context(
            access_guard_skip_guard=True,
            access_guard_skip_ui_rules=True,
        ).sudo()
        for view in views:
            arch = None
            if hasattr(model_env, 'get_view'):
                try:
                    view_info = model_env.get_view(view_id=view.id, view_type=view.type)
                    if isinstance(view_info, dict):
                        arch = view_info.get('arch')
                except Exception:
                    arch = None
            if not arch:
                arch = view._get_combined_arch() if hasattr(view, '_get_combined_arch') else view.arch_db
            if isinstance(arch, bytes):
                arch = arch.decode()
            if not arch:
                continue
            try:
                arch_doc = etree.fromstring(arch)
            except Exception:
                continue

            if view.type == 'form':
                if 'button' in target_types:
                    self._extract_form_buttons(model, view, arch_doc)
                if 'smart_button' in target_types:
                    self._extract_form_smart_buttons(model, view, arch_doc)
                if 'page' in target_types:
                    self._extract_form_pages(model, view, arch_doc)
            if view.type in ('tree', 'list') and {'button', 'action_button'} & set(target_types):
                self._extract_list_buttons(model, view, arch_doc)
            if view.type == 'kanban' and 'kanban_link' in target_types:
                self._extract_kanban_links(model, view, arch_doc)
        if {'button', 'action_button'} & set(target_types):
            self._extract_binding_actions(model, target_types)
        if debug:
            after = self.with_context(access_guard_skip_autoload=True).search_count(monitored_domain)
            created = after - before if before is not None else after
            _logger.info(
                "Access Guard UI discovery model=%s types=%s views=%s created=%s total=%s",
                model_name,
                target_types,
                processed_views,
                created,
                after,
            )

    @api.model
    def _extract_form_buttons(self, model, view, arch_doc):
        for node in arch_doc.xpath(".//button[@name]"):
            identifier = node.get('name')
            if not identifier:
                continue
            label = node.get('string') or identifier
            button_type = (node.get('type') or 'object').strip().lower()
            element_type = 'action_button' if button_type == 'action' else 'button'
            self._create_or_update_element(model, view, element_type, 'name', identifier, label)
        widget_label_map = {
            'generate_serials': 'Generate Serials/Lots',
            'import_lots': 'Import Serials/Lots',
        }
        for node in arch_doc.xpath(".//widget[@name]"):
            identifier = node.get('name')
            if not identifier or identifier not in widget_label_map:
                continue
            label = widget_label_map.get(identifier) or identifier
            self._create_or_update_element(model, view, 'button', 'name', identifier, label)

    @staticmethod
    def _is_smart_button_node(node):
        class_attr = node.get('class') or ''
        class_names = set(class_attr.split())
        return bool({'oe_stat_button', 'o_stat_button'} & class_names)

    @api.model
    def _extract_form_smart_buttons(self, model, view, arch_doc):
        for node in arch_doc.xpath(".//button[@name]"):
            if not self._is_smart_button_node(node):
                continue
            identifier = node.get('name')
            if not identifier:
                continue
            label = node.get('string')
            if not label:
                field_node = node.xpath(".//field[@string][1]")
                if field_node:
                    label = field_node[0].get('string')
            if not label:
                label = ''.join(node.itertext()).strip() or identifier
            self._create_or_update_element(model, view, 'smart_button', 'name', identifier, label)

    @api.model
    def _extract_form_pages(self, model, view, arch_doc):
        for node in arch_doc.xpath(".//page"):
            identifier = node.get('name') or node.get('string')
            if not identifier:
                continue
            match_mode = 'name' if node.get('name') else 'string'
            label = node.get('string') or identifier
            self._create_or_update_element(model, view, 'page', match_mode, identifier, label)

    @api.model
    def _extract_list_buttons(self, model, view, arch_doc):
        for node in arch_doc.xpath(".//button[@name]"):
            identifier = node.get('name')
            if not identifier:
                continue
            label = node.get('string') or identifier
            button_type = (node.get('type') or 'object').strip().lower()
            element_type = 'action_button' if button_type == 'action' else 'button'
            self._create_or_update_element(
                model,
                view,
                element_type,
                'name',
                identifier,
                label,
                view_type='list',
            )

    @api.model
    def _parse_binding_view_types(self, binding_view_types):
        if not binding_view_types:
            return set()
        if isinstance(binding_view_types, (list, tuple, set)):
            values = binding_view_types
        else:
            values = [item.strip() for item in str(binding_view_types).split(',')]
        normalized = {item for item in values if item}
        if 'tree' in normalized:
            normalized.add('list')
        return normalized

    @api.model
    def _binding_element_view_type(self, binding_view_types):
        view_types = self._parse_binding_view_types(binding_view_types)
        if not view_types:
            return 'form'
        if 'list' in view_types:
            return 'list'
        if 'form' in view_types:
            return 'form'
        return 'form'

    @api.model
    def _extract_binding_actions(self, model, target_types):
        target_types = set(target_types or [])
        if 'button' in target_types:
            server_actions = self.env['ir.actions.server'].sudo().search([
                ('binding_model_id', '=', model.id),
            ])
            for action in server_actions:
                if action.binding_type and action.binding_type != 'action':
                    continue
                view_type = self._binding_element_view_type(action.binding_view_types)
                self._create_or_update_element(
                    model,
                    None,
                    'button',
                    'action_id',
                    str(action.id),
                    action.name or str(action.id),
                    view_type=view_type,
                )
        if 'action_button' in target_types:
            window_actions = self.env['ir.actions.act_window'].sudo().search([
                ('binding_model_id', '=', model.id),
            ])
            for action in window_actions:
                view_type = self._binding_element_view_type(action.binding_view_types)
                self._create_or_update_element(
                    model,
                    None,
                    'action_button',
                    'action_id',
                    str(action.id),
                    action.name or str(action.id),
                    view_type=view_type,
                )

    @api.model
    def _extract_kanban_links(self, model, view, arch_doc):
        for node in arch_doc.xpath(".//a[@name] | .//button[@name]"):
            identifier = node.get('name')
            if not identifier:
                continue
            text_content = ''.join(node.itertext()).strip()
            label = node.get('string') or text_content or identifier
            self._create_or_update_element(model, view, 'kanban_link', 'name', identifier, label)

    def _create_or_update_element(
        self,
        model,
        view,
        element_type,
        match_mode,
        match_value,
        label,
        attribute_name='',
        view_type=None,
    ):
        self = self.with_context(access_guard_skip_autoload=True).sudo()
        if not match_value:
            return
        if not view_type:
            view_type = 'kanban' if element_type == 'kanban_link' else 'form'
        if view_type in ('tree', 'list'):
            view_type = 'list'
        existing = self.search([
            ('model_id', '=', model.id),
            ('type', '=', element_type),
            ('match_mode', '=', match_mode),
            ('match_value', '=', match_value),
            ('attribute_name', '=', attribute_name or ''),
        ], limit=1)
        if not existing and element_type in ('button', 'action_button'):
            existing = self.search([
                ('model_id', '=', model.id),
                ('type', 'in', ('button', 'action_button')),
                ('match_mode', '=', match_mode),
                ('match_value', '=', match_value),
                ('attribute_name', '=', attribute_name or ''),
            ], limit=1)
            if existing and existing.type != element_type:
                existing.sudo().write({'type': element_type})
        view_id = view.id if view else False
        vals = {
            'model_id': model.id,
            'type': element_type,
            'match_mode': match_mode,
            'match_value': match_value,
            'label': label or match_value,
            'attribute_name': attribute_name or '',
            'view_type': view_type,
            'view_id': view_id,
        }
        if existing:
            update_vals = {}
            if not existing.label and vals['label']:
                update_vals['label'] = vals['label']
            if view_id and not existing.view_id:
                update_vals['view_id'] = view_id
            if update_vals:
                existing.sudo().write(update_vals)
            return existing
        return self.sudo().create(vals)

    @api.model
    def _normalize_many2one_value(self, value):
        if hasattr(value, 'id'):
            return value.id
        if isinstance(value, (list, tuple)):
            return [self._normalize_many2one_value(v) for v in value]
        return value

    def _extract_filter_values(self, args):
        model_ids = set()
        types = set()
        for arg in args or []:
            if not isinstance(arg, (list, tuple)) or len(arg) < 3:
                continue
            field, operator, value = arg[0], arg[1], arg[2]
            if field == 'model_id':
                value = self._normalize_many2one_value(value)
                if operator == '=' and value:
                    if isinstance(value, list):
                        model_ids.update(filter(None, value))
                    else:
                        model_ids.add(value)
                elif operator in ('in', 'child_of'):
                    if isinstance(value, list):
                        model_ids.update(filter(None, value))
                    else:
                        model_ids.add(value)
            elif field == 'type':
                if operator == '=' and value:
                    types.add(value)
                elif operator in ('in', 'child_of') and isinstance(value, (list, tuple)):
                    types.update(filter(None, value))
        context = self.env.context

        def _merge(values, target):
            if not values:
                return
            if isinstance(values, (list, tuple, set)):
                target.update(filter(None, values))
            else:
                target.add(values)

        ctx_model_raw = context.get('default_model_id') or context.get('search_default_model_id')
        ctx_type_raw = context.get('default_type') or context.get('search_default_type')
        ctx_model_norm = self._normalize_many2one_value(ctx_model_raw)
        ctx_type_norm = ctx_type_raw
        _merge(ctx_model_norm, model_ids)
        _merge(ctx_type_norm, types)
        # allow passing explicit ids list via context
        ctx_model_list = context.get('access_guard_model_ids')
        _merge(self._normalize_many2one_value(ctx_model_list), model_ids)
        ctx_type_list = context.get('access_guard_element_types')
        _merge(ctx_type_list, types)
        if not model_ids and ctx_model_norm:
            if isinstance(ctx_model_norm, list):
                model_ids.update(filter(None, ctx_model_norm))
            else:
                model_ids.add(ctx_model_norm)
        if not types and ctx_type_norm:
            if isinstance(ctx_type_norm, (list, tuple, set)):
                types.update(filter(None, ctx_type_norm))
            else:
                types.add(ctx_type_norm)
        if context.get('access_guard_debug'):
            _logger.info(
                "Access Guard UI filter args=%s ctx_models=%s ctx_types=%s -> models=%s types=%s",
                args,
                context.get('access_guard_model_ids'),
                context.get('access_guard_element_types'),
                list(model_ids),
                list(types),
            )
        return list(model_ids), list(types)

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=80):
        domain = list(args or [])
        if not self.env.context.get('access_guard_skip_autoload'):
            model_ids, types = self._extract_filter_values(domain)
            if model_ids:
                for mid in model_ids:
                    self._ensure_elements_for_model(mid, types or None)
        search_name = name
        if name:
            name_domain = ['|', ('label', operator, name), ('match_value', operator, name)]
            domain = expression.AND([domain, name_domain]) if domain else name_domain
            search_name = ''
        return super().name_search(name=search_name, args=domain, operator=operator, limit=limit)

    def search(self, args=None, offset=0, limit=None, order=None, count=False):
        domain = list(args or [])
        if not self.env.context.get('access_guard_skip_autoload'):
            model_ids, types = self._extract_filter_values(domain)
            if model_ids:
                for mid in model_ids:
                    self._ensure_elements_for_model(mid, types or None)
        if count:
            return super().search_count(domain)
        return super().search(domain, offset=offset, limit=limit, order=order)


class AccessGuardManagementUi(models.Model):
    _name = 'access.guard.management.ui'
    _description = 'Access Guard Button/Tab Rule'
    _order = 'sequence, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    button_element_ids = fields.Many2many(
        'access.guard.ui.element',
        'access_guard_ui_rule_button_rel',
        'ui_rule_id',
        'element_id',
        string='Hide Server Buttons',
        domain="[('model_id', '=', model_id), ('type', '=', 'button')]",
    )
    action_button_element_ids = fields.Many2many(
        'access.guard.ui.element',
        'access_guard_ui_rule_action_button_rel',
        'ui_rule_id',
        'element_id',
        string='Hide Window Buttons',
        domain="[('model_id', '=', model_id), ('type', '=', 'action_button')]",
    )
    smart_button_element_ids = fields.Many2many(
        'access.guard.ui.element',
        'access_guard_ui_rule_smart_button_rel',
        'ui_rule_id',
        'element_id',
        string='Hide Smart Buttons',
        domain="[('model_id', '=', model_id), ('type', '=', 'smart_button')]",
    )
    tab_element_ids = fields.Many2many(
        'access.guard.ui.element',
        'access_guard_ui_rule_tab_rel',
        'ui_rule_id',
        'element_id',
        string='Hide Tab/Page',
        domain="[('model_id', '=', model_id), ('type', '=', 'page')]",
    )
    kanban_link_element_ids = fields.Many2many(
        'access.guard.ui.element',
        'access_guard_ui_rule_kanban_rel',
        'ui_rule_id',
        'element_id',
        string='Hide Kanban Link',
        domain="[('model_id', '=', model_id), ('type', '=', 'kanban_link')]",
    )

    _sql_constraints = [
        (
            'access_guard_management_ui_unique',
            'unique(management_id, model_id)',
            'Each model can only be added once per rule.',
        )
    ]

    @api.onchange('model_id')
    def _onchange_model_id(self):
        Element = self.env['access.guard.ui.element']
        for record in self:
            if record.model_id:
                Element._ensure_elements_for_model(record.model_id)
                record.button_element_ids = [(Command.set(
                    record.button_element_ids.filtered(
                        lambda e: e.model_id == record.model_id and e.type == 'button'
                    ).ids
                ))] if record.button_element_ids else [(Command.clear())]
                record.action_button_element_ids = [(Command.set(
                    record.action_button_element_ids.filtered(
                        lambda e: e.model_id == record.model_id and e.type == 'action_button'
                    ).ids
                ))] if record.action_button_element_ids else [(Command.clear())]
                record.smart_button_element_ids = [(Command.set(
                    record.smart_button_element_ids.filtered(
                        lambda e: e.model_id == record.model_id and e.type == 'smart_button'
                    ).ids
                ))] if record.smart_button_element_ids else [(Command.clear())]
                record.tab_element_ids = [(Command.set(
                    record.tab_element_ids.filtered(
                        lambda e: e.model_id == record.model_id and e.type == 'page'
                    ).ids
                ))] if record.tab_element_ids else [(Command.clear())]
                record.kanban_link_element_ids = [(Command.set(
                    record.kanban_link_element_ids.filtered(
                        lambda e: e.model_id == record.model_id and e.type == 'kanban_link'
                    ).ids
                ))] if record.kanban_link_element_ids else [(Command.clear())]
            else:
                record.button_element_ids = [(Command.clear())]
                record.action_button_element_ids = [(Command.clear())]
                record.smart_button_element_ids = [(Command.clear())]
                record.tab_element_ids = [(Command.clear())]
                record.kanban_link_element_ids = [(Command.clear())]

    @api.model_create_multi
    def create(self, vals_list):
        Element = self.env['access.guard.ui.element']
        for vals in vals_list:
            model_id = vals.get('model_id')
            if model_id:
                Element._ensure_elements_for_model(model_id)
        records = super().create(vals_list)
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        if 'model_id' in vals and vals['model_id']:
            self.env['access.guard.ui.element']._ensure_elements_for_model(vals['model_id'])
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result

class AccessGuardManagementModel(models.Model):
    _name = 'access.guard.management.model'
    _description = 'Access Guard Model Rule'
    _order = 'sequence, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(related='model_id.model', store=False, readonly=True)
    report_ids = fields.Many2many(
        'ir.actions.report',
        'access_guard_model_report_rel',
        'model_rule_id',
        'report_id',
        string='Reports',
    )
    action_ids = fields.Many2many(
        'ir.actions.server',
        'access_guard_model_action_rel',
        'model_rule_id',
        'action_id',
        string='Server Actions',
    )
    window_action_ids = fields.Many2many(
        'ir.actions.act_window',
        'access_guard_model_window_action_rel',
        'model_rule_id',
        'action_id',
        string='Window Actions',
    )
    hide_view_mode_ids = fields.Many2many(
        'access.guard.view.mode',
        'access_guard_model_view_mode_rel',
        'model_rule_id',
        'view_mode_id',
        string='Hide Views',
    )
    view_ids = fields.Many2many(
        'ir.ui.view',
        'access_guard_model_view_rel',
        'model_rule_id',
        'view_id',
        string='Views',
    )
    hide_create = fields.Boolean(string='Hide Create')
    hide_edit = fields.Boolean(string='Hide Edit')
    hide_delete = fields.Boolean(string='Hide Delete')
    hide_archive = fields.Boolean(string='Hide Archive/Unarchive')
    hide_duplicate = fields.Boolean(string='Hide Duplicate')
    hide_import = fields.Boolean(string='Hide Import')
    hide_export = fields.Boolean(string='Hide Export')
    hide_spreadsheet = fields.Boolean(string='Hide Spreadsheet')



    _sql_constraints = [
        (
            'access_guard_management_model_unique',
            'unique(management_id, model_id)',
            'Each model can only be added once per rule.',
        )
    ]

    @api.depends('model_id')
    def _compute_model_name(self):
        for record in self:
            record.model_name = record.model_id.model or False

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.ensure_one()
        if not self.model_id:
            self.report_ids = [(Command.clear())]
            self.action_ids = [(Command.clear())]
            self.window_action_ids = [(Command.clear())]
            self.view_ids = [(Command.clear())]
            return {
                'domain': {
                    'report_ids': [],
                    'action_ids': [],
                    'window_action_ids': [],
                    'view_ids': [],
                }
            }

        model_name = self.model_id.model
        report_ids = self.report_ids.filtered(lambda r: r.model == model_name).ids
        action_ids = self.action_ids.filtered(lambda a: a.model_id == self.model_id).ids
        window_action_ids = self.window_action_ids.filtered(
            lambda a: a.binding_model_id == self.model_id and a.binding_type == 'action'
        ).ids
        view_ids = self.view_ids.filtered(lambda v: v.model == model_name).ids

        self.report_ids = [(Command.set(report_ids))] if report_ids else [(Command.clear())]
        self.action_ids = [(Command.set(action_ids))] if action_ids else [(Command.clear())]
        self.window_action_ids = [(Command.set(window_action_ids))] if window_action_ids else [(Command.clear())]
        self.view_ids = [(Command.set(view_ids))] if view_ids else [(Command.clear())]

        return {
            'domain': {
                'report_ids': [('model', '=', model_name)],
                'action_ids': [('model_id', '=', self.model_id.id)],
                'window_action_ids': [
                    ('binding_model_id', '=', self.model_id.id),
                    ('binding_type', '=', 'action'),
                ],
                'view_ids': [('model', '=', model_name)],
            }
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result



class AccessGuardManagementField(models.Model):
    _name = 'access.guard.management.field'
    _description = 'Access Guard Field Rule'
    _order = 'sequence, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(compute='_compute_model_name', store=True, index=True)
    field_ids = fields.Many2many(
        'ir.model.fields',
        'access_guard_management_field_rel',
        'management_field_id',
        'field_id',
        string='Fields',
        required=True,
    )
    invisible = fields.Boolean(string='Invisible')
    read_only = fields.Boolean(string='Read-Only')
    required = fields.Boolean(string='Required')
    remove_create_option = fields.Boolean(string='Remove Create Option')
    remove_edit_option = fields.Boolean(string='Remove Edit Option')
    remove_external_link = fields.Boolean(string='Remove External Link')

    @api.constrains('field_ids', 'model_id', 'management_id')
    def _check_field_constraints(self):
        for record in self:
            if not record.field_ids:
                raise ValidationError('Select at least one field.')
            if not record.model_id or not record.management_id:
                continue
            invalid_fields = record.field_ids.filtered(lambda f: f.model_id != record.model_id)
            if invalid_fields:
                labels = ', '.join(
                    field.field_description or field.name or str(field.id)
                    for field in invalid_fields
                )
                raise ValidationError(f'The following fields do not belong to {record.model_id.display_name}: {labels}')
            other_fields = record.management_id.field_rule_ids.filtered(
                lambda r: r != record and r.model_id == record.model_id
            ).mapped('field_ids')
            if other_fields:
                overlap = set(record.field_ids.ids) & set(other_fields.ids)
                if overlap:
                    duplicates = self.env['ir.model.fields'].browse(list(overlap))
                    dup_labels = ', '.join(
                        field.field_description or field.name or str(field.id)
                        for field in duplicates
                    )
                    raise ValidationError(
                        f'The following fields are already configured in another field rule: {dup_labels}'
                    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        result = super().unlink()
        management._invalidate_menu_cache()
        return result

    @api.onchange('model_id')
    def _onchange_model_id(self):
        if not self.model_id:
            self.field_ids = [(Command.clear())]
            return {
                'domain': {
                    'field_ids': [('id', '=', False)],
                }
            }
        allowed_fields = self.field_ids.filtered(lambda f: f.model_id == self.model_id)
        if len(allowed_fields) != len(self.field_ids):
            self.field_ids = [(Command.set(allowed_fields.ids))] if allowed_fields else [(Command.clear())]
        return {
            'domain': {
                'field_ids': [('model_id', '=', self.model_id.id)],
            }
        }

    def init(self):
        super().init()
        cr = self.env.cr
        if not column_exists(cr, self._table, 'field_id'):
            return
        cr.execute("SELECT to_regclass('access_guard_management_field_rel')")
        if not cr.fetchone()[0]:
            return
        cr.execute(
            """
                INSERT INTO access_guard_management_field_rel (management_field_id, field_id)
                SELECT id, field_id
                FROM access_guard_management_field
                WHERE field_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM access_guard_management_field_rel rel
                      WHERE rel.management_field_id = access_guard_management_field.id
                        AND rel.field_id = access_guard_management_field.field_id
                  )
            """
        )
        cr.execute("ALTER TABLE access_guard_management_field DROP COLUMN field_id")



class AccessGuardMenuAddWizard(models.TransientModel):
    _name = 'access.guard.menu.add.wizard'
    _description = 'Add Hidden Menus Wizard'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    menu_ids = fields.Many2many(
        'ir.ui.menu',
        string='Menus',
        required=True,
    )
    def action_add_menus(self):
        self.ensure_one()
        management = self.management_id
        if not management:
            raise UserError('Please choose a rule to apply these menus to.')

        existing_menu_ids = set(management.menu_line_ids.mapped('menu_id').ids)
        to_create = []
        for menu in self.menu_ids:
            if menu.id in existing_menu_ids:
                continue
            to_create.append(
                {
                    'management_id': management.id,
                    'menu_id': menu.id,
                }
            )
        if to_create:
            self.env['access.guard.management.menu'].sudo().create(to_create)
        return {'type': 'ir.actions.act_window_close'}




class AccessGuardManagementAcl(models.Model):
    _name = 'access.guard.management.acl'
    _description = 'Access Guard ACL Rule'
    _order = 'model_id, group_id, id'

    management_id = fields.Many2one(
        'access.guard.management',
        required=True,
        ondelete='cascade',
    )
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(compute='_compute_model_name', store=True, readonly=True)
    group_id = fields.Many2one('res.groups', required=True, ondelete='cascade')

    perm_read = fields.Boolean(string='Read', default=True)
    perm_write = fields.Boolean(string='Write', default=False)
    perm_create = fields.Boolean(string='Create', default=False)
    perm_unlink = fields.Boolean(string='Delete', default=False)

    access_id = fields.Many2one(
        'ir.model.access',
        string='Generated Access',
        readonly=True,
        copy=False,
        help='ir.model.access record created/updated by this ACL line.'
    )

    _sql_constraints = [
        (
            'access_guard_management_acl_unique',
            'unique(management_id, model_id, group_id)',
            'Each model/group combination can only be added once per rule.',
        )
    ]

    @api.depends('model_id')
    def _compute_model_name(self):
        for record in self:
            record.model_name = record.model_id.model if record.model_id else False

    def _ensure_ir_model_access(self):
        # These are standard additive Odoo ACLs. They grant access to the
        # selected group independently of the parent rule's runtime audience.
        Access = self.env['ir.model.access'].sudo()
        for record in self:
            if not record.model_id or not record.group_id:
                continue
            vals = {
                'name': f"Access Guard ACL: {record.group_id.display_name} / {record.model_id.model}",
                'model_id': record.model_id.id,
                'group_id': record.group_id.id,
                'perm_read': bool(record.perm_read),
                'perm_write': bool(record.perm_write),
                'perm_create': bool(record.perm_create),
                'perm_unlink': bool(record.perm_unlink),
            }
            if record.access_id and record.access_id.exists():
                record.access_id.write(vals)
            else:
                access = Access.create(vals)
                record.access_id = access.id

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ensure_ir_model_access()
        records.mapped('management_id')._invalidate_menu_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._ensure_ir_model_access()
        self.mapped('management_id')._invalidate_menu_cache()
        return result

    def unlink(self):
        management = self.mapped('management_id')
        to_delete = self.mapped('access_id')
        result = super().unlink()
        if to_delete:
            to_delete.sudo().unlink()
        management._invalidate_menu_cache()
        return result

class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    root_id = fields.Many2one(
        'ir.ui.menu',
        string="Application",
        compute='_compute_root_id',
        compute_sudo=True,
        store=True,
    )

    @api.depends('parent_id')
    def _compute_root_id(self):
        for record in self:
            current = record
            while current.parent_id:
                current = current.parent_id
            record.root_id = current.id if current else False

    def init(self):
        super().init()
        cr = self.env.cr
        if not column_exists(cr, self._table, 'root_id'):
            cr.execute(f'ALTER TABLE "{self._table}" ADD COLUMN root_id integer')

    def _access_guard_hidden_menu_ids(self, debug=False):
        user = self.env.user
        if not user:
            return set()
        management_model = self.env["access.guard.management"].sudo()
        hidden = set()
        try:
            rules = management_model.search([("active", "=", True)])
            for rule in rules:
                if rule._applies_to_user(user):
                    hidden |= rule._get_hidden_menu_ids(user)
        except psycopg2_errors.UndefinedTable:
            return set()
        return hidden

    def _visible_menu_ids(self, debug=False):
        if self.env.context.get("access_guard_skip_guard"):
            return super()._visible_menu_ids(debug=debug)
        visible = super()._visible_menu_ids(debug=debug)
        hidden = self._access_guard_hidden_menu_ids(debug)
        if not hidden:
            return visible
        return {menu_id for menu_id in visible if menu_id not in hidden}

    def _get_access_guard_tree(self):
        menu_model = self.env["ir.ui.menu"].with_context(access_guard_skip_guard=True).sudo()
        result = set()
        queue = list(self.ids)
        while queue:
            menu_id = queue.pop()
            if menu_id in result:
                continue
            result.add(menu_id)
            children = menu_model.search([("parent_id", "=", menu_id)])
            if children:
                queue.extend(children.ids)
        return result

