import re


def _normalize_code(value):
    return re.sub(r"[^A-Z0-9]+", "_", (value or "").upper()).strip("_") or "TEMPLATE"


def migrate(cr, version):
    cr.execute("SELECT to_regclass('access_guard_template')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        "ALTER TABLE access_guard_template "
        "ADD COLUMN IF NOT EXISTS template_code varchar"
    )
    cr.execute(
        "SELECT id, name FROM access_guard_template "
        "WHERE template_code IS NULL OR template_code = '' ORDER BY id"
    )
    for template_id, name in cr.fetchall():
        code = "%s_%s" % (_normalize_code(name), template_id)
        cr.execute(
            "UPDATE access_guard_template SET template_code = %s WHERE id = %s",
            (code, template_id),
        )
