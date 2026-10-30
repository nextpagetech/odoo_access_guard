from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.cr.execute("SELECT to_regclass('public.access_guard_ui_element')")
    if not env.cr.fetchone()[0]:
        return

    env.cr.execute("SELECT to_regclass('public.access_guard_ui_rule_action_button_rel')")
    if not env.cr.fetchone()[0]:
        env.cr.execute(
            """
            CREATE TABLE access_guard_ui_rule_action_button_rel (
                ui_rule_id INTEGER NOT NULL,
                element_id INTEGER NOT NULL,
                PRIMARY KEY (ui_rule_id, element_id)
            )
            """
        )
        env.cr.execute(
            """
            CREATE INDEX access_guard_ui_rule_action_button_rel_element_idx
            ON access_guard_ui_rule_action_button_rel (element_id, ui_rule_id)
            """
        )

    env.cr.execute(
        """
        UPDATE access_guard_ui_element el
           SET type = 'action_button'
         WHERE el.type = 'button'
           AND el.match_mode = 'name'
           AND el.match_value ~ '^[0-9]+$'
           AND EXISTS (
                SELECT 1
                  FROM ir_act_window win
                 WHERE win.id = el.match_value::integer
           )
        """
    )

    env.cr.execute(
        """
        INSERT INTO access_guard_ui_rule_action_button_rel (ui_rule_id, element_id)
        SELECT rel.ui_rule_id, rel.element_id
          FROM access_guard_ui_rule_button_rel rel
          JOIN access_guard_ui_element el ON el.id = rel.element_id
         WHERE el.type = 'action_button'
         ON CONFLICT DO NOTHING
        """
    )
    env.cr.execute(
        """
        DELETE FROM access_guard_ui_rule_button_rel rel
         USING access_guard_ui_element el
         WHERE el.id = rel.element_id
           AND el.type = 'action_button'
        """
    )
