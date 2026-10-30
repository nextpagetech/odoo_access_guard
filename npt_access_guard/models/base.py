from lxml import etree
import json
from odoo import api, models, _
from odoo.exceptions import AccessError
from odoo.osv import expression
from odoo.tools.safe_eval import safe_eval


class BaseModel(models.AbstractModel):
    _inherit = "base"

    def _access_guard_collect_rules(self):
        """Return merged Access Guard rules for the current model/user."""
        if self.env.su:
            return {}
        if self._name.startswith('access.guard.'):
            return {}

        env = self.env
        cache = getattr(env, '_access_guard_rules_cache', None)
        if cache is None:
            cache = {}
            setattr(env, '_access_guard_rules_cache', cache)

        user = env.user
        if not user:
            return {}

        companies = env.context.get('allowed_company_ids')
        if not companies:
            companies_key = ()
        else:
            if not isinstance(companies, (list, tuple, set)):
                companies = [companies]
            try:
                companies_key = tuple(sorted(int(c) for c in companies if c))
            except Exception:
                companies_key = tuple(sorted(companies))
        company_id = env.context.get('company_id') or False
        cache_key = (user.id, companies_key, company_id, self._name)
        if cache_key in cache:
            return cache[cache_key]

        management = env['access.guard.management'].with_context(access_guard_skip_domain_rules=True)
        rules_map = management._get_user_model_rules(user)
        rules = management._merge_rule_maps(rules_map.get('__global__'), rules_map.get(self._name))
        cache[cache_key] = rules or {}
        return cache[cache_key]

    def _access_guard_is_relational_context(self):
        ctx = self.env.context
        if ctx.get('access_guard_relational_domain'):
            return True
        active_model = ctx.get('active_model')
        if active_model and active_model != self._name:
            if ctx.get('active_id') or ctx.get('active_ids'):
                return True
        parent_model = ctx.get('default_parent_model')
        if parent_model and parent_model != self._name:
            return True
        return False

    def _access_guard_company_filter(self):
        field = self._fields.get('company_id')
        if not field:
            return []
        company = self.env.company
        if not company:
            return []
        return [('company_id', 'in', [company.id, False])]

    def _access_guard_skip_domain_for_model(self):
        if self._name != 'project.task':
            return False
        user = self.env.user
        return user.has_group('base.group_system')

    def _access_guard_normalize_domain_rule(self, domain):
        if self._name != 'project.task' or not domain:
            return domain
        current_user_id = self.env.user.id
        has_assignment_filter = False
        current_user_in_filter = False

        def _assignment_user_ids(leaf):
            if not isinstance(leaf, (list, tuple)) or len(leaf) < 3:
                return []
            field_name, operator, value = leaf[0], leaf[1], leaf[2]
            if field_name not in ('user_ids', 'user_id') or operator not in ('in', '='):
                return []
            if isinstance(value, models.BaseModel):
                return value.ids
            if isinstance(value, int):
                return [value]
            if isinstance(value, (list, tuple, set)):
                user_ids = []
                for item in value:
                    if isinstance(item, int):
                        user_ids.append(item)
                    elif isinstance(item, str) and item.isdigit():
                        user_ids.append(int(item))
                return user_ids
            return []

        def _scan(node):
            nonlocal has_assignment_filter, current_user_in_filter
            if isinstance(node, (list, tuple)) and len(node) >= 3 and isinstance(node[0], str) and node[0] not in {'&', '|', '!'}:
                user_ids = _assignment_user_ids(node)
                if user_ids:
                    has_assignment_filter = True
                    if current_user_id in user_ids:
                        current_user_in_filter = True
                return
            if isinstance(node, (list, tuple)):
                for item in node:
                    _scan(item)

        _scan(domain)
        if has_assignment_filter and not current_user_in_filter:
            return None

        def _normalize_leaf(leaf):
            if not isinstance(leaf, (list, tuple)) or len(leaf) < 3:
                return leaf
            field_name, operator, value = leaf[0], leaf[1], leaf[2]
            if field_name not in ('user_ids', 'user_id') or operator not in ('in', '='):
                return leaf
            user_ids = _assignment_user_ids(leaf)
            if current_user_id not in user_ids:
                return leaf
            if field_name == 'user_ids' and operator == 'in':
                return leaf
            if field_name == 'user_id':
                if 'user_id' in self._fields:
                    return ('user_id', '=', current_user_id)
                if 'user_ids' in self._fields:
                    return ('user_ids', 'in', current_user_id)
                return leaf
            return ('user_ids', 'in', current_user_id)

        def _walk(node):
            if isinstance(node, tuple):
                return _normalize_leaf(node)
            if not isinstance(node, list):
                return node
            if len(node) >= 3 and isinstance(node[0], str) and node[0] not in {'&', '|', '!'}:
                return _normalize_leaf(node)
            return [_walk(item) for item in node]

        return _walk(domain)

    def _access_guard_shared_assignment_domain(self, domain):
        if self._name != 'project.task' or not domain:
            return None
        current_user_id = self.env.user.id

        def _user_ids(value):
            if isinstance(value, models.BaseModel):
                return value.ids
            if isinstance(value, int):
                return [value]
            if isinstance(value, (list, tuple, set)):
                ids = []
                for item in value:
                    if isinstance(item, int):
                        ids.append(item)
                    elif isinstance(item, str) and item.isdigit():
                        ids.append(int(item))
                return ids
            return []

        def _scan(node):
            if isinstance(node, (list, tuple)) and len(node) >= 3 and isinstance(node[0], str) and node[0] not in {'&', '|', '!'}:
                field_name, operator, value = node[0], node[1], node[2]
                user_ids = _user_ids(value)
                if field_name == 'user_ids' and operator == 'in' and current_user_id in user_ids and len(user_ids) > 1:
                    return ('user_ids', 'in', user_ids)
                return None
            if isinstance(node, (list, tuple)):
                for item in node:
                    result = _scan(item)
                    if result:
                        return result
            return None

        return _scan(domain)

    def _access_guard_assignment_operator_for_user(self, domain):
        if self._name != 'project.task' or not domain:
            return None
        current_user_id = self.env.user.id

        def _user_ids(value):
            if isinstance(value, models.BaseModel):
                return value.ids
            if isinstance(value, int):
                return [value]
            if isinstance(value, (list, tuple, set)):
                ids = []
                for item in value:
                    if isinstance(item, int):
                        ids.append(item)
                    elif isinstance(item, str) and item.isdigit():
                        ids.append(int(item))
                return ids
            return []

        def _scan(node):
            if isinstance(node, (list, tuple)) and len(node) >= 3 and isinstance(node[0], str) and node[0] not in {'&', '|', '!'}:
                field_name, operator, value = node[0], node[1], node[2]
                if field_name in ('user_ids', 'user_id') and operator in ('=', 'in') and current_user_id in _user_ids(value):
                    return operator
                return None
            if isinstance(node, (list, tuple)):
                result = None
                for item in node:
                    item_result = _scan(item)
                    if item_result == '=':
                        return item_result
                    result = result or item_result
                return result
            return None

        return _scan(domain)

    def _access_guard_expand_task_assignee_domain(self, domain, shared_domain):
        if self._name != 'project.task' or not domain or not shared_domain:
            return domain
        current_user_id = self.env.user.id

        def _is_current_user_filter(field_name, operator, value):
            if field_name not in ('user_ids', 'user_id') or operator not in ('in', '='):
                return False
            if isinstance(value, int):
                return value == current_user_id
            if isinstance(value, str) and value.isdigit():
                return int(value) == current_user_id
            if isinstance(value, (list, tuple, set)):
                values = []
                for item in value:
                    if isinstance(item, int):
                        values.append(item)
                    elif isinstance(item, str) and item.isdigit():
                        values.append(int(item))
                return values == [current_user_id]
            return False

        def _walk(node):
            if isinstance(node, tuple):
                if len(node) >= 3 and _is_current_user_filter(node[0], node[1], node[2]):
                    return shared_domain
                return node
            if not isinstance(node, list):
                return node
            if len(node) >= 3 and isinstance(node[0], str) and node[0] not in {'&', '|', '!'}:
                if _is_current_user_filter(node[0], node[1], node[2]):
                    return shared_domain
                return node
            return [_walk(item) for item in node]

        return _walk(domain)

    def _access_guard_apply_domain_rules(self, domain, *, relational=False):
        """Apply Access Guard domain rules to the provided domain."""
        if self.env.su:
            return domain
        if self._name.startswith('access.guard.'):
            return domain
        if self._access_guard_skip_domain_for_model():
            return domain
        if self.env.context.get('access_guard_skip_domain_rules'):
            return domain

        relational = relational or self._access_guard_is_relational_context()
        rules = self._access_guard_collect_rules() or {}
        domain_rules = rules.get('domain_rules') or []
        if not domain_rules:
            return domain

        applicable_rule_entries = []
        has_exact_assignment_rule = False
        has_read_rule = False
        for spec in domain_rules:
            if relational and not spec.get('apply_relational'):
                continue
            if spec.get('allow_read', True):
                has_read_rule = True
            else:
                # Explicit deny takes precedence if nothing grants read later
                continue
            if spec.get('apply_filter') and spec.get('domain'):
                raw_rule_domain = list(spec['domain'])
                assignment_operator = self._access_guard_assignment_operator_for_user(raw_rule_domain)
                rule_domain = self._access_guard_normalize_domain_rule(raw_rule_domain)
                if rule_domain is None:
                    continue
                if assignment_operator == '=':
                    has_exact_assignment_rule = True
                if spec.get('enforce_company'):
                    company_filter = self._access_guard_company_filter()
                    if company_filter:
                        rule_domain = expression.AND([rule_domain, company_filter])
                applicable_rule_entries.append((rule_domain, assignment_operator))

        if not has_read_rule:
            return [('id', '=', False)]
        if has_exact_assignment_rule:
            applicable_rule_entries = [
                entry for entry in applicable_rule_entries
                if entry[1] != 'in'
            ]
        applicable_domains = [entry[0] for entry in applicable_rule_entries]
        if not applicable_domains:
            return domain

        shared_assignment_domain = None
        for rule_domain, assignment_operator in applicable_rule_entries:
            if assignment_operator == 'in':
                shared_assignment_domain = self._access_guard_shared_assignment_domain(rule_domain)
                if shared_assignment_domain:
                    break

        combined = applicable_domains[0]
        if len(applicable_domains) > 1:
            combined = expression.OR(applicable_domains)

        existing = list(domain or []) if domain else []
        if shared_assignment_domain:
            existing = self._access_guard_expand_task_assignee_domain(existing, shared_assignment_domain)
        if existing:
            return expression.AND([existing, combined])
        return combined

    def _access_guard_operation_error(self, operation):
        op_labels = {
            'read': _('read'),
            'write': _('modify'),
            'unlink': _('delete'),
            'create': _('create'),
        }
        op_label = op_labels.get(operation, operation)
        model_name = self._description or self._name
        return _("You are not allowed to %(op)s %(model)s records due to an Access Guard rule.") % {
            'op': op_label,
            'model': model_name,
        }

    def _access_guard_get_domain_specs(self, relational=False):
        rules = self._access_guard_collect_rules() or {}
        specs = rules.get('domain_rules') or []
        if not relational:
            return specs
        return [spec for spec in specs if spec.get('apply_relational')]

    def _access_guard_check_create_rights(self):
        if (
            self.env.su
            or self._name.startswith('access.guard.')
            or self._access_guard_skip_domain_for_model()
            or self.env.context.get('access_guard_skip_domain_rules')
        ):
            return
        specs = self._access_guard_get_domain_specs(relational=self._access_guard_is_relational_context())
        for spec in specs:
            if spec.get('soft_restrict'):
                continue
            if not spec.get('allow_create', True):
                if spec.get('apply_filter') and spec.get('domain'):
                    continue
                raise AccessError(self._access_guard_operation_error('create'))

    def _access_guard_check_record_rules(self, operation):
        if (
            not self
            or self.env.su
            or self._name.startswith('access.guard.')
            or self._access_guard_skip_domain_for_model()
            or self.env.context.get('access_guard_skip_domain_rules')
        ):
            return
        key_map = {
            'write': 'allow_write',
            'unlink': 'allow_delete',
            'create': 'allow_create',
        }
        key = key_map.get(operation)
        if not key:
            return
        relational = self._access_guard_is_relational_context()
        specs = self._access_guard_get_domain_specs(relational=relational)
        restricted = [
            spec
            for spec in specs
            if not spec.get('soft_restrict') and not spec.get(key, True)
        ]
        if not restricted:
            return
        ids = list(set(self.ids))
        if not ids:
            return
        Model = self.env[self._name].with_context(
            access_guard_skip_domain_rules=True,
            access_guard_skip_guard=True,
        )
        for spec in restricted:
            if spec.get('apply_filter') and spec.get('domain'):
                target_domain = expression.AND([[('id', 'in', ids)], list(spec['domain'])])
            else:
                target_domain = [('id', 'in', ids)]
            if Model.search(target_domain, limit=1):
                raise AccessError(self._access_guard_operation_error(operation))

    def _access_guard_check_read_rules(self):
        if (
            not self
            or self.env.su
            or self._name.startswith('access.guard.')
            or self._access_guard_skip_domain_for_model()
            or self.env.context.get('access_guard_skip_domain_rules')
        ):
            return
        relational = self._access_guard_is_relational_context()
        specs = self._access_guard_get_domain_specs(relational=relational)
        denying_specs = [spec for spec in specs if not spec.get('allow_read', True)]
        if not denying_specs:
            return
        allowed_domain = self._access_guard_apply_domain_rules([], relational=relational)
        if not allowed_domain:
            return
        guardless = self.env[self._name].with_context(
            access_guard_skip_domain_rules=True,
            access_guard_skip_guard=True,
        )
        ids = list(set(self.ids))
        if not ids:
            return
        domain = expression.AND([allowed_domain, [('id', 'in', ids)]])
        count = guardless.search_count(domain)
        if count != len(ids):
            raise AccessError(self._access_guard_operation_error('read'))

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        result = super().get_view(view_id=view_id, view_type=view_type, **options)
        if self.env.context.get('access_guard_skip_ui_rules'):
            return result
        if self.env.su or self._name.startswith('access.guard.'):
            return result

        management = self.env['access.guard.management'].with_context(access_guard_skip_domain_rules=True)
        rules_map = management._get_user_model_rules(self.env.user)
        if not rules_map:
            return result

        rules_cache = {}

        def _get_rules(model_name):
            if model_name in rules_cache:
                return rules_cache[model_name]
            merged = management._merge_rule_maps(rules_map.get('__global__'), rules_map.get(model_name))
            rules_cache[model_name] = merged or {}
            return rules_cache[model_name]

        base_rules = _get_rules(self._name)
        arch = etree.fromstring(result['arch'])

        if view_type == "search":
            if self.env.context.get('access_guard_skip_search_rules'):
                return result
            if not base_rules:
                return result
            updated = False
            search_rules = base_rules.get('search_panel') or {}
            hide_panel = search_rules.get('hide_panel')
            hidden_filters = set(search_rules.get('filters') or [])
            hidden_groups = set(search_rules.get('groups') or [])
            if hide_panel:
                for node in arch.xpath(".//searchpanel"):
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
                        updated = True
            else:
                if hidden_filters or hidden_groups:
                    for panel in arch.xpath(".//searchpanel"):
                        for field_node in list(panel.xpath("./field[@name]")):
                            field_name = field_node.get('name')
                            if not field_name:
                                continue
                            is_group = bool(field_node.get('groupby'))
                            if is_group and field_name in hidden_groups:
                                panel.remove(field_node)
                                updated = True
                            elif not is_group and field_name in hidden_filters:
                                panel.remove(field_node)
                                updated = True
            if updated:
                result['arch'] = etree.tostring(arch, encoding='unicode')
            return result

        def _safe_options_dict(value):
            if not value:
                return {}
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, str):
                try:
                    parsed = safe_eval(value, {"true": True, "false": False, "null": None})
                except Exception:
                    try:
                        parsed = json.loads(value)
                    except Exception:
                        return {}
                return parsed if isinstance(parsed, dict) else {}
            return {}

        def _safe_modifiers_dict(value):
            if not value:
                return {}
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except Exception:
                    try:
                        parsed = safe_eval(value, {"true": True, "false": False, "null": None})
                    except Exception:
                        return {}
                return parsed if isinstance(parsed, dict) else {}
            return {}

        def _is_safe_attribute(attr_name):
            return attr_name and all(ch.isalnum() or ch in {'_', '-'} for ch in attr_name)

        def _is_smart_button_node(node):
            class_attr = node.get('class') or ''
            class_names = set(class_attr.split())
            return bool({'oe_stat_button', 'o_stat_button'} & class_names)

        def _normalize_domain(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, (list, tuple)):
                return list(value)
            if isinstance(value, str):
                try:
                    parsed = safe_eval(value, {"true": True, "false": False, "null": None})
                except Exception:
                    return []
                if isinstance(parsed, bool):
                    return parsed
                if isinstance(parsed, (list, tuple)):
                    return list(parsed)
                return []
            return []

        def _combine_domains(existing, new, mode='and'):
            if not new:
                return existing
            normalized_new = _normalize_domain(new)
            if isinstance(normalized_new, bool):
                if mode == 'or':
                    return True if normalized_new else existing
                return False if not normalized_new else existing
            normalized_existing = _normalize_domain(existing)
            if isinstance(normalized_existing, bool):
                if mode == 'or':
                    return True if normalized_existing else normalized_new
                return False if not normalized_existing else normalized_new
            if not normalized_existing:
                return normalized_new
            try:
                if mode == 'or':
                    return expression.OR([normalized_existing, normalized_new])
                return expression.AND([normalized_existing, normalized_new])
            except Exception:
                return normalized_existing

        def _extract_domain_fields(domain):
            result_fields = set()

            def _walk(node):
                if isinstance(node, (list, tuple)):
                    if not node:
                        return
                    first = node[0]
                    if isinstance(first, str) and first not in {'&', '|', '!'} and len(node) >= 3:
                        result_fields.add(first)
                        for extra in node[3:]:
                            _walk(extra)
                    else:
                        for item in node:
                            _walk(item)

            _walk(domain)
            return result_fields

        def _apply_rules(arch_node, active_view_type, active_rules, fields_data=None, parent_model_name=None, model_name=None):
            if not active_rules:
                return False
            updated = False
            model_fields = None
            if model_name:
                try:
                    model_fields = set(self.env[model_name]._fields)
                except Exception:
                    model_fields = None
            view_fields = None
            view_hidden_fields = None
            if active_view_type in ("list", "tree"):
                view_fields = set()
                view_hidden_fields = set()
                for field_node in arch_node.xpath(".//field[@name]"):
                    field_name = field_node.get('name')
                    if not field_name:
                        continue
                    view_fields.add(field_name)
                    col_invisible = field_node.get('column_invisible')
                    invisible = field_node.get('invisible')
                    if col_invisible in {'1', 'True', 'true'} or invisible in {'1', 'True', 'true'}:
                        view_hidden_fields.add(field_name)

            def set_attr(name, value="false"):
                nonlocal updated
                if arch_node.get(name) != value:
                    arch_node.set(name, value)
                    updated = True

            def make_invisible(node):
                nonlocal updated
                modifiers = _safe_modifiers_dict(node.get('modifiers'))
                if not modifiers.get('invisible'):
                    modifiers['invisible'] = True
                    node.set('modifiers', json.dumps(modifiers))
                    updated = True
                if node.get('invisible') != '1':
                    node.set('invisible', '1')
                    updated = True

            def remove_node(node):
                nonlocal updated
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                    updated = True
            def _merge_expr(existing, new_expr, op='or'):
                if not existing:
                    return new_expr
                if not new_expr:
                    return existing
                return f"({existing}) {op} ({new_expr})"

            def _hide_label(field_name, expr=None):
                nonlocal updated
                for label_node in arch_node.xpath(".//label[@for=$name]", name=field_name):
                    if expr:
                        combined = _merge_expr(label_node.get('invisible'), expr, op='or')
                        label_node.set('invisible', combined)
                    else:
                        if label_node.get('invisible') != '1':
                            label_node.set('invisible', '1')
                    updated = True

            def match_nodes(tag_names, spec):
                if not spec:
                    return []
                match = spec.get('match') or 'name'
                value = spec.get('value')
                if not value:
                    return []
                try:
                    if match == 'xpath':
                        return arch_node.xpath(value)
                    if match == 'string':
                        expr = " | ".join([f".//{tag}[@string=$value]" for tag in tag_names])
                        return arch_node.xpath(expr, value=value)
                    if match == 'attribute':
                        attr = spec.get('attribute') or 'name'
                        if not _is_safe_attribute(attr):
                            return []
                        expr = " | ".join([f".//{tag}[@{attr}=$value]" for tag in tag_names])
                        return arch_node.xpath(expr, value=value)
                    expr = " | ".join([f".//{tag}[@name=$value]" for tag in tag_names])
                    return arch_node.xpath(expr, value=value)
                except etree.XPathError:
                    return []

            def _is_list_view_type(value):
                return value in ("list", "tree")

            def iter_specs(key, fallback_view_types=None):
                for item in active_rules.get(key, []) or []:
                    target = item.get('view_type')
                    if target and target != active_view_type:
                        if not (_is_list_view_type(target) and _is_list_view_type(active_view_type)):
                            if not fallback_view_types or target not in fallback_view_types:
                                continue
                    yield item

            if active_view_type in ("list", "form", "kanban", "calendar", "gantt", "pivot", "graph", "activity"):
                if active_rules.get('hide_create'):
                    set_attr('create')
                if active_rules.get('hide_edit'):
                    set_attr('edit')
                if active_rules.get('hide_delete'):
                    set_attr('delete')
                if active_rules.get('hide_duplicate'):
                    set_attr('duplicate')
            if active_view_type in ("list", "kanban") and active_rules.get('hide_archive'):
                set_attr('archive')
                set_attr('unarchive')
            if active_view_type == "list":
                if active_rules.get('hide_import'):
                    set_attr('import')
                if active_rules.get('hide_export'):
                    set_attr('export')
                if active_rules.get('hide_spreadsheet'):
                    set_attr('spreadsheet')
            if active_view_type in ("list", "tree"):
                for spec in iter_specs('buttons', fallback_view_types={'form'}):
                    for node in match_nodes(['button', 'widget'], spec):
                        make_invisible(node)

            field_rules = active_rules.get('fields', {})
            if field_rules:
                for field_node in arch_node.xpath(".//field[@name]"):
                    field_name = field_node.get('name')
                    config = field_rules.get(field_name)
                    if not config:
                        continue

                    modifiers = _safe_modifiers_dict(field_node.get('modifiers'))
                    attr_updated = False
                    if config.get('invisible'):
                        modifiers['invisible'] = True
                        if field_node.get('invisible') != '1':
                            field_node.set('invisible', '1')
                            attr_updated = True
                        if field_node.xpath('ancestor::list') or field_node.xpath('ancestor::tree'):
                            if field_node.get('column_invisible') != '1':
                                field_node.set('column_invisible', '1')
                                attr_updated = True
                            if field_node.get('optional') != 'hide':
                                field_node.set('optional', 'hide')
                                attr_updated = True
                        if active_view_type == "form":
                            _hide_label(field_name)
                    if config.get('readonly'):
                        modifiers['readonly'] = True
                        if field_node.get('readonly') != '1':
                            field_node.set('readonly', '1')
                            attr_updated = True
                    if config.get('required'):
                        modifiers['required'] = True
                        if field_node.get('required') != '1':
                            field_node.set('required', '1')
                            attr_updated = True

                    if modifiers:
                        field_node.set('modifiers', json.dumps(modifiers))
                        updated = True
                    if attr_updated:
                        updated = True

                    options_value = field_node.get('options')
                    options_dict = _safe_options_dict(options_value)
                    changed_options = False
                    if config.get('no_create') and not options_dict.get('no_create'):
                        options_dict['no_create'] = True
                        changed_options = True
                    if config.get('no_create_edit') and not options_dict.get('no_create_edit'):
                        options_dict['no_create_edit'] = True
                        changed_options = True
                    if config.get('no_open') and not options_dict.get('no_open'):
                        options_dict['no_open'] = True
                        changed_options = True
                    if changed_options:
                        field_node.set('options', str(options_dict))
                        updated = True

                    if fields_data is not None:
                        field_def = fields_data.get(field_name)
                        if field_def is not None:
                            existing_modifiers = _safe_modifiers_dict(field_def.get('modifiers'))
                            field_def_modifiers = dict(existing_modifiers)
                            if config.get('invisible'):
                                field_def['invisible'] = True
                                field_def_modifiers['invisible'] = True
                            if config.get('readonly'):
                                field_def['readonly'] = True
                                field_def_modifiers['readonly'] = True
                            if config.get('required'):
                                field_def['required'] = True
                                field_def_modifiers['required'] = True
                            if field_def_modifiers:
                                field_def['modifiers'] = field_def_modifiers
                            if config.get('no_create') or config.get('no_create_edit') or config.get('no_open'):
                                field_def_options = _safe_options_dict(field_def.get('options'))
                                if config.get('no_create'):
                                    field_def_options['no_create'] = True
                                if config.get('no_create_edit'):
                                    field_def_options['no_create_edit'] = True
                                if config.get('no_open'):
                                    field_def_options['no_open'] = True
                                field_def['options'] = field_def_options

            field_conditionals = active_rules.get('field_conditionals') or []
            if field_conditionals:
                def _leaf_expr(field_name, operator, value):
                    ops = {
                        '=': '==',
                        '!=': '!=',
                        '>': '>',
                        '<': '<',
                        '>=': '>=',
                        '<=': '<=',
                        'in': 'in',
                        'not in': 'not in',
                    }
                    py_op = ops.get(operator)
                    if not py_op:
                        return None
                    return f"({field_name} {py_op} {repr(value)})"

                def _domain_to_expr(domain, field_map=None):
                    if not domain:
                        return None
                    try:
                        normalized = expression.normalize_domain(domain)
                    except Exception:
                        return None
                    tokens = list(normalized)

                    def _parse(idx):
                        if idx >= len(tokens):
                            return None, idx
                        token = tokens[idx]
                        if token in ('&', '|'):
                            left, next_idx = _parse(idx + 1)
                            right, next_idx = _parse(next_idx)
                            if not left or not right:
                                return None, next_idx
                            op = 'and' if token == '&' else 'or'
                            return f"({left} {op} {right})", next_idx
                        if token == '!':
                            sub, next_idx = _parse(idx + 1)
                            if not sub:
                                return None, next_idx
                            return f"(not {sub})", next_idx
                        if isinstance(token, (list, tuple)) and len(token) >= 3:
                            field = token[0]
                            operator = token[1]
                            value = token[2]
                            if field_map is not None:
                                if field_map is True:
                                    field_ref = f"parent.{field}"
                                else:
                                    if field not in field_map:
                                        return None, idx + 1
                                    field_ref = field_map[field]
                            else:
                                field_ref = field
                            return _leaf_expr(field_ref, operator, value), idx + 1
                        return None, idx + 1

                    expr, _ = _parse(0)
                    return expr

                for rule in field_conditionals:
                    attrs_fields = rule.get('attrs_fields') or []
                    if not attrs_fields and rule.get('attrs_field'):
                        attrs_fields = [rule.get('attrs_field')]
                    button_specs = rule.get('attrs_buttons') or []
                    if (
                        active_view_type in ("form", "list", "tree")
                        and rule.get('apply_attrs')
                        and (attrs_fields or button_specs)
                    ):
                        domain_rules = _normalize_domain(rule.get('attrs_domain'))
                        attr_type = rule.get('attrs_type')
                        if attr_type and isinstance(domain_rules, list) and domain_rules:
                            expr = _domain_to_expr(domain_rules)
                            domain_fields = _extract_domain_fields(domain_rules)
                            if domain_fields:
                                field_map = {}
                                for name in domain_fields:
                                    if model_fields is not None and name not in model_fields:
                                        field_map[name] = f"context.get('access_guard_parent_{name}')"
                                        continue
                                    if active_view_type in ("list", "tree") and view_fields is not None:
                                        if name not in view_fields or (view_hidden_fields and name in view_hidden_fields):
                                            field_map[name] = f"context.get('access_guard_parent_{name}')"
                                        else:
                                            field_map[name] = name
                                    else:
                                        field_map[name] = name
                                expr = _domain_to_expr(domain_rules, field_map=field_map) or expr
                            xpath = ".//field[@name=$name]"
                            for field_name in attrs_fields:
                                for field_node in arch_node.xpath(xpath, name=field_name):
                                    if expr:
                                        existing_attr = field_node.get(attr_type)
                                        combined_attr = _merge_expr(existing_attr, expr, op='or')
                                        field_node.set(attr_type, combined_attr)
                                        updated = True
                                    if active_view_type == "form" and attr_type == 'invisible':
                                        effective_invisible = field_node.get('invisible')
                                        if effective_invisible:
                                            _hide_label(field_name, effective_invisible)
                                    if (
                                        attr_type == 'invisible'
                                        and active_view_type in ("list", "tree")
                                    ):
                                        existing_col = field_node.get('column_invisible')
                                        if expr:
                                            combined_col = _merge_expr(existing_col, expr, op='or')
                                            field_node.set('column_invisible', combined_col)
                                            if field_node.get('optional') != 'hide':
                                                field_node.set('optional', 'hide')
                                            updated = True
                            if button_specs and attr_type == 'invisible' and expr and active_view_type == "form":
                                for spec in button_specs:
                                    element_type = spec.get('element_type') or spec.get('type')
                                    tag_names = ['button'] if element_type == 'smart_button' else ['button', 'widget']
                                    for node in match_nodes(tag_names, spec):
                                        if element_type == 'smart_button':
                                            if not _is_smart_button_node(node):
                                                continue
                                        elif _is_smart_button_node(node):
                                            continue
                                        existing_attr = node.get('invisible')
                                        combined_attr = _merge_expr(existing_attr, expr, op='or')
                                        node.set('invisible', combined_attr)
                                        updated = True

                    if rule.get('apply_domain') and rule.get('domain_field'):
                        domain_rules = _normalize_domain(rule.get('domain_expression'))
                        if isinstance(domain_rules, list) and domain_rules:
                            xpath = ".//field[@name=$name]"
                            for field_node in arch_node.xpath(xpath, name=rule['domain_field']):
                                existing_domain = field_node.get('domain')
                                combined_domain = _combine_domains(existing_domain, list(domain_rules), mode='and')
                                if isinstance(combined_domain, list):
                                    field_node.set('domain', repr(combined_domain))
                                    updated = True
            if active_view_type == "form":
                for spec in iter_specs('buttons'):
                    for node in match_nodes(['button', 'widget'], spec):
                        make_invisible(node)
                for spec in iter_specs('smart_buttons'):
                    for node in match_nodes(['button'], spec):
                        if _is_smart_button_node(node):
                            make_invisible(node)
                for spec in iter_specs('tabs'):
                    for node in match_nodes(['page'], spec):
                        make_invisible(node)
                chatter_rules = active_rules.get('chatter') or {}
                if chatter_rules.get('hide_all'):
                    chatter_nodes = arch_node.xpath(".//chatter")
                    for node in chatter_nodes:
                        remove_node(node)
                    if chatter_nodes:
                        for wrapper in arch_node.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' oe_chatter ')]"):
                            if not wrapper.getchildren():
                                remove_node(wrapper)
            if active_view_type == "kanban":
                for spec in iter_specs('kanban_links'):
                    for node in match_nodes(['a', 'button'], spec):
                        remove_node(node)
            return updated

        def _append_context_key(context_value, key, value_expr):
            entry = f"'{key}': {value_expr}"
            if not context_value:
                return '{' + entry + '}'
            raw = context_value.strip()
            if raw.startswith('{') and raw.endswith('}'):
                inner = raw[1:-1].strip()
                if not inner:
                    return '{' + entry + '}'
                if entry in inner:
                    return raw
                return '{' + inner + ', ' + entry + '}'
            return context_value
        view_tags = {
            'list',
            'tree',
            'form',
            'kanban',
            'calendar',
            'graph',
            'pivot',
            'gantt',
            'activity',
        }

        def _normalize_view_type(tag_name):
            return 'list' if tag_name in ('list', 'tree') else tag_name

        def _apply_nested_rules(arch_node, model_name):
            updated = False
            if not model_name:
                return False
            try:
                model = self.env[model_name]
            except KeyError:
                return False
            for field_node in arch_node.xpath(".//field[@name]"):
                field_name = field_node.get('name')
                if not field_name:
                    continue
                field = model._fields.get(field_name)
                if not field or field.type not in ('one2many', 'many2many'):
                    continue
                comodel_name = field.comodel_name
                if not comodel_name:
                    continue
                child_rules = _get_rules(comodel_name)
                if child_rules:
                    conditional_rules = child_rules.get('field_conditionals') or []
                    if conditional_rules:
                        parent_fields = set()
                        for cond in conditional_rules:
                            if cond.get('apply_attrs') and cond.get('attrs_domain'):
                                domain_fields = _extract_domain_fields(cond.get('attrs_domain') or [])
                                for fname in domain_fields:
                                    if fname in model._fields:
                                        parent_fields.add(fname)
                        if parent_fields:
                            context_value = field_node.get('context')
                            for fname in sorted(parent_fields):
                                key = f"access_guard_parent_{fname}"
                                context_value = _append_context_key(context_value, key, fname)
                            if context_value:
                                field_node.set('context', context_value)
                                updated = True
                if not comodel_name:
                    continue
                for child in field_node:
                    if child.tag not in view_tags:
                        continue
                    child_view_type = _normalize_view_type(child.tag)
                    if child_rules:
                        if _apply_rules(
                            child,
                            child_view_type,
                            child_rules,
                            fields_data=None,
                            parent_model_name=model_name,
                            model_name=comodel_name,
                        ):
                            updated = True
                    if _apply_nested_rules(child, comodel_name):
                        updated = True
            return updated

        fields_data = result.get('fields')
        if not isinstance(fields_data, dict):
            fields_data = None

        updated = _apply_rules(
            arch,
            view_type,
            base_rules,
            fields_data=fields_data,
            parent_model_name=None,
            model_name=self._name,
        )
        if _apply_nested_rules(arch, self._name):
            updated = True

        if updated:
            result['arch'] = etree.tostring(arch, encoding='unicode')

        toolbar_content = result.get('toolbar')
        if toolbar_content:
            report_ids = set(base_rules.get('reports', []))
            action_ids = set(base_rules.get('actions', []))
            if report_ids and toolbar_content.get('print'):
                toolbar_content['print'] = [item for item in toolbar_content['print'] if item['id'] not in report_ids]
            if action_ids and toolbar_content.get('action'):
                toolbar_content['action'] = [item for item in toolbar_content['action'] if item['id'] not in action_ids]
        return result

    @api.model
    def get_views(self, views, options=None):
        result = super().get_views(views, options=options)
        if self.env.context.get('access_guard_skip_ui_rules'):
            return result
        if self.env.su or self._name.startswith('access.guard.'):
            return result
        if not (options or {}).get('toolbar'):
            return result

        management = self.env['access.guard.management'].with_context(access_guard_skip_domain_rules=True)
        rules_map = management._get_user_model_rules(self.env.user)
        if not rules_map:
            return result
        base_rules = management._merge_rule_maps(rules_map.get('__global__'), rules_map.get(self._name)) or {}
        report_ids = set(base_rules.get('reports', []))
        action_ids = set(base_rules.get('actions', []))
        if not report_ids and not action_ids:
            return result

        views_map = result.get('views') or {}
        for view in views_map.values():
            toolbar = view.get('toolbar')
            if not toolbar:
                continue
            if report_ids and toolbar.get('print'):
                toolbar['print'] = [
                    item for item in toolbar['print'] if item.get('id') not in report_ids
                ]
            if action_ids and toolbar.get('action'):
                toolbar['action'] = [
                    item for item in toolbar['action'] if item.get('id') not in action_ids
                ]
        return result

    def fields_view_get(self, view_id=None, view_type="form", toolbar=False, submenu=False):
        return self.get_view(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)

    @api.model
    def _search(self, domain, *search_args, **search_kwargs):
        adjusted_domain = self._access_guard_apply_domain_rules(domain or [])
        return super()._search(adjusted_domain, *search_args, **search_kwargs)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        adjusted_domain = self._access_guard_apply_domain_rules(domain or [])
        clean_groupby = []
        for gb in groupby or []:
            gb_field = gb.split(":", 1)[0] if isinstance(gb, str) else gb
            if gb_field in self._fields:
                clean_groupby.append(gb)
        return super().read_group(
            adjusted_domain,
            fields,
            clean_groupby,
            offset=offset,
            limit=limit,
            orderby=orderby,
            lazy=lazy,
        )

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        adjusted_domain = self._access_guard_apply_domain_rules(args or [], relational=True)
        return super().name_search(name=name, args=adjusted_domain, operator=operator, limit=limit)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._access_guard_check_record_rules('create')
        return records

    def check_access_rights(self, operation, raise_exception=True):
        result = super().check_access_rights(operation, raise_exception=raise_exception)
        if not raise_exception and not result:
            return False
        if operation == 'create':
            try:
                self._access_guard_check_create_rights()
            except AccessError:
                if raise_exception:
                    raise
                return False
        return result

    def _access_guard_enforce_access(self, operation):
        if operation == 'read':
            self._access_guard_check_read_rules()
        elif operation in ('write', 'unlink'):
            self._access_guard_check_record_rules(operation)

    def _check_access(self, operation: str):
        guardless = self.with_context(access_guard_skip_domain_rules=True)
        result = super(BaseModel, guardless)._check_access(operation)
        if result:
            return result
        try:
            self._access_guard_enforce_access(operation)
        except AccessError as exc:
            return self, lambda exc=exc: exc
        return None
