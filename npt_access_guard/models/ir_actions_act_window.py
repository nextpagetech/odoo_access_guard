import logging

from odoo import models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    def read(self, fields=None, load="_classic_read"):
        result = super().read(fields=fields, load=load)
        management = self.env['access.guard.management']
        rules_map = management._get_user_model_rules(self.env.user)
        for vals in result:
            model = vals.get('res_model')
            if not model:
                continue
            rules = management._merge_rule_maps(rules_map.get('__global__'), rules_map.get(model))
            if not rules:
                continue

            view_mode_block = set(rules.get('view_modes', []))
            view_id_block = set(rules.get('views', []))

            if 'view_mode' in vals and vals['view_mode']:
                modes = [m for m in vals['view_mode'].split(',') if m and m not in view_mode_block]
                if modes:
                    vals['view_mode'] = ','.join(modes)
            if 'views' in vals and vals['views']:
                filtered = [view for view in vals['views'] if view[1] not in view_mode_block and (not view[0] or view[0] not in view_id_block)]
                if filtered:
                    vals['views'] = filtered

            if 'context' in vals:
                ctx = vals['context']
                if isinstance(ctx, str):
                    eval_context = dict(self.env.context or {})
                    try:
                        evaluated = safe_eval(ctx, eval_context) if ctx else {}
                    except Exception as exc:
                        _logger.warning(
                            "Access Guard failed to evaluate action context (id=%s): %s",
                            vals.get('id'),
                            exc,
                        )
                        evaluated = {}
                    ctx_dict = dict(evaluated or {})
                else:
                    ctx_dict = dict(ctx or {})
                def flag(name, key, container=None):
                    value = rules.get(key) if container is None else rules.get(container, {}).get(key)
                    if value:
                        ctx_dict[name] = True
                flag('access_guard_hide_create', 'hide_create')
                flag('access_guard_hide_edit', 'hide_edit')
                flag('access_guard_hide_delete', 'hide_delete')
                flag('access_guard_hide_duplicate', 'hide_duplicate')
                flag('access_guard_hide_archive', 'hide_archive')
                flag('access_guard_hide_import', 'hide_import')
                flag('access_guard_hide_export', 'hide_export')
                flag('access_guard_hide_spreadsheet', 'hide_spreadsheet')
                flag('access_guard_hide_add_property', 'hide_add_property')
                flag('access_guard_hide_search_panel', 'hide_panel', 'search_panel')
                flag('access_guard_hide_custom_filter', 'hide_custom_filter', 'search_panel')
                flag('access_guard_hide_custom_group', 'hide_custom_group', 'search_panel')
                flag('access_guard_hide_delete_favorite', 'hide_delete_favorite', 'search_panel')
                vals['context'] = repr(ctx_dict)
        return result
