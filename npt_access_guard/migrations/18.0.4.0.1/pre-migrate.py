from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.cr.execute(
        """
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class rel_t ON rel_t.oid = c.conrelid
         WHERE rel_t.relname = 'access_guard_model_action_rel'
           AND c.conname = 'access_guard_model_action_rel_action_id_fkey'
        """
    )
    if env.cr.fetchone():
        env.cr.execute(
            """
            ALTER TABLE access_guard_model_action_rel
            DROP CONSTRAINT access_guard_model_action_rel_action_id_fkey
            """
        )
    env.cr.execute(
        """
        DELETE FROM access_guard_model_action_rel rel
         WHERE NOT EXISTS (
            SELECT 1
              FROM ir_actions act
             WHERE act.id = rel.action_id
         )
        """
    )
