from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _access_guard_enforce_debug_mode(cls, env=None):
        if not request or not request.db or not request.session.uid:
            return
        env = env or request.env
        management = env['access.guard.management']
        if management._is_developer_mode_disabled(env.user):
            request.session.debug = ''

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        # web.ir_http has now parsed any ?debug= value into the session.
        cls._access_guard_enforce_debug_mode()

    def session_info(self):
        # Authentication builds session_info with a fresh user environment
        # before request.env is necessarily updated to that user.
        self._access_guard_enforce_debug_mode(self.env)
        info = super().session_info()
        rules = self.env['access.guard.management']._get_user_model_rules(self.env.user)
        info['access_guard_model_rules'] = rules
        return info
