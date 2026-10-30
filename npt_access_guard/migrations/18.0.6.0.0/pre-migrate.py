def migrate(cr, version):
    cr.execute("SELECT to_regclass('access_guard_template')")
    if not cr.fetchone()[0]:
        return

    cr.execute(
        """
        ALTER TABLE access_guard_template
            ADD COLUMN IF NOT EXISTS imported_at timestamp without time zone,
            ADD COLUMN IF NOT EXISTS imported_filename varchar,
            ADD COLUMN IF NOT EXISTS imported_access_guard_version varchar,
            ADD COLUMN IF NOT EXISTS import_format_version varchar,
            ADD COLUMN IF NOT EXISTS resolution_payload text,
            ADD COLUMN IF NOT EXISTS resolution_hash varchar,
            ADD COLUMN IF NOT EXISTS resolution_status varchar,
            ADD COLUMN IF NOT EXISTS resolution_date timestamp without time zone
        """
    )
    cr.execute(
        """
        UPDATE access_guard_template
           SET resolution_status = 'blocked'
         WHERE source_type = 'imported'
           AND resolution_status IS NULL
        """
    )
