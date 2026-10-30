from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.cr.execute("SELECT to_regclass('public.access_guard_model_action_rel')")
    if not env.cr.fetchone()[0]:
        return

    env.cr.execute("SELECT to_regclass('public.access_guard_model_window_action_rel')")
    if not env.cr.fetchone()[0]:
        env.cr.execute(
            """
            CREATE TABLE access_guard_model_window_action_rel (
                model_rule_id INTEGER NOT NULL,
                action_id INTEGER NOT NULL,
                PRIMARY KEY (model_rule_id, action_id)
            )
            """
        )
        env.cr.execute(
            """
            CREATE INDEX access_guard_model_window_action_rel_action_idx
            ON access_guard_model_window_action_rel (action_id, model_rule_id)
            """
        )

    env.cr.execute(
        """
        INSERT INTO access_guard_model_window_action_rel (model_rule_id, action_id)
        SELECT rel.model_rule_id, rel.action_id
          FROM access_guard_model_action_rel rel
          JOIN ir_act_window win ON win.id = rel.action_id
         ON CONFLICT DO NOTHING
        """
    )
    env.cr.execute(
        """
        DELETE FROM access_guard_model_action_rel rel
         WHERE EXISTS (
            SELECT 1
              FROM ir_act_window win
             WHERE win.id = rel.action_id
         )
        """
    )
    env.cr.execute(
        """
        DELETE FROM access_guard_model_action_rel rel
         WHERE NOT EXISTS (
            SELECT 1
              FROM ir_act_server srv
             WHERE srv.id = rel.action_id
         )
        """
    )

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
        ALTER TABLE access_guard_model_action_rel
        ADD CONSTRAINT access_guard_model_action_rel_action_id_fkey
        FOREIGN KEY (action_id)
        REFERENCES ir_act_server (id)
        ON DELETE CASCADE
        """
    )

    env.cr.execute(
        """
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class rel_t ON rel_t.oid = c.conrelid
         WHERE rel_t.relname = 'access_guard_model_window_action_rel'
           AND c.conname = 'access_guard_model_window_action_rel_action_id_fkey'
        """
    )
    if not env.cr.fetchone():
        env.cr.execute(
            """
            ALTER TABLE access_guard_model_window_action_rel
            ADD CONSTRAINT access_guard_model_window_action_rel_action_id_fkey
            FOREIGN KEY (action_id)
            REFERENCES ir_act_window (id)
            ON DELETE CASCADE
            """
        )
    env.cr.execute(
        """
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class rel_t ON rel_t.oid = c.conrelid
         WHERE rel_t.relname = 'access_guard_model_window_action_rel'
           AND c.conname = 'access_guard_model_window_action_rel_model_rule_id_fkey'
        """
    )
    if not env.cr.fetchone():
        env.cr.execute(
            """
            ALTER TABLE access_guard_model_window_action_rel
            ADD CONSTRAINT access_guard_model_window_action_rel_model_rule_id_fkey
            FOREIGN KEY (model_rule_id)
            REFERENCES access_guard_management_model (id)
            ON DELETE CASCADE
            """
        )
