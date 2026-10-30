def migrate(cr, version):
    cr.execute("SELECT to_regclass('public.access_guard_template')")
    if not cr.fetchone()[0]:
        return

    cr.execute(
        "ALTER TABLE access_guard_template "
        "ADD COLUMN IF NOT EXISTS source_type varchar"
    )
    cr.execute(
        """
        UPDATE access_guard_template
           SET source_type = CASE
               WHEN source_management_id IS NULL THEN 'imported'
               ELSE 'local_rule'
           END
         WHERE source_type IS NULL
            OR source_type NOT IN ('local_rule', 'imported')
        """
    )
    cr.execute(
        "ALTER TABLE access_guard_template "
        "ALTER COLUMN source_management_id DROP NOT NULL"
    )
    cr.execute(
        "ALTER TABLE access_guard_template "
        "DROP CONSTRAINT IF EXISTS access_guard_template_code_unique"
    )
    cr.execute(
        "ALTER TABLE access_guard_template "
        "DROP CONSTRAINT IF EXISTS access_guard_template_access_guard_template_code_unique"
    )
