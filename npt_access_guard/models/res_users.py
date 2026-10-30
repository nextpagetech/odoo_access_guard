import logging

from odoo import _, api, models
from odoo.exceptions import AccessDenied


class ResUsers(models.Model):
    _inherit = "res.users"

    _logger = logging.getLogger(__name__)

    def _invalidate_access_guard_caches(self):
        self.env['access.guard.management']._invalidate_access_guard_caches()

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if any('groups_id' in vals for vals in vals_list):
            users._invalidate_access_guard_caches()
        return users

    def write(self, vals):
        result = super().write(vals)
        if 'groups_id' in vals:
            self._invalidate_access_guard_caches()
        return result

    def unlink(self):
        result = super().unlink()
        self._invalidate_access_guard_caches()
        return result

    @classmethod
    def authenticate(cls, db, credential, user_agent_env):
        auth_info = super().authenticate(db, credential, user_agent_env)
        uid = auth_info.get("uid")
        if not uid:
            return auth_info
        with cls.pool.cursor() as cr:
            env = api.Environment(cr, uid, {})
            management = env["access.guard.management"]
            rules_map = management._get_user_model_rules(env.user)
            merged = management._merge_rule_maps(rules_map.get("__global__"))
            if merged.get("disable_login"):
                cls._logger.info("Login blocked by Access Guard for user %s", env.user.login)
                raise AccessDenied(_("Your login has been disabled."))
        return auth_info


class ResGroups(models.Model):
    _inherit = "res.groups"

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create(vals_list)
        if any('users' in vals for vals in vals_list):
            self.env['access.guard.management']._invalidate_access_guard_caches()
        return groups

    def write(self, vals):
        result = super().write(vals)
        if 'users' in vals:
            self.env['access.guard.management']._invalidate_access_guard_caches()
        return result

    def unlink(self):
        result = super().unlink()
        self.env['access.guard.management']._invalidate_access_guard_caches()
        return result
