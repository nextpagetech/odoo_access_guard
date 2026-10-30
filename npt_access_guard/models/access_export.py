import base64
import hashlib
import json
import re
from urllib.parse import quote

from odoo import fields, models, release, _
from odoo.exceptions import AccessError, UserError, ValidationError


EXPORT_FORMAT_VERSION = "1.0"


class AccessGuardTemplateExport(models.Model):
    _inherit = "access.guard.template"

    def _check_export_access(self):
        if not self.env.user.has_group("npt_access_guard.group_access_guard_manager"):
            raise AccessError(_("Only Access Guard Managers can export templates."))

    def _export_portable_payload(self):
        self.ensure_one()
        if not self.portable_payload or (
            self.source_type == "local_rule" and not self.snapshot_payload
        ):
            raise UserError(
                _("Template %s has no frozen portable definition. Run Refresh Snapshot first.")
                % self.name
            )
        if not self.template_code or not self.template_version or not self.schema_version:
            raise UserError(
                _("Template %s is missing its code, template version, or schema version.")
                % self.name
            )
        if self.portability_status not in (
            "ready", "warnings", "mapping_required", "blocked"
        ):
            raise UserError(
                _("Template %s has no valid portability status. Run Refresh Snapshot.")
                % self.name
            )
        if self.portability_status == "blocked":
            raise UserError(
                _("Template %s is blocked and cannot be exported.\n\n%s")
                % (self.name, self.portability_report or _("Run Refresh Snapshot and review the portability report."))
            )
        try:
            payload = json.loads(self.portable_payload)
            self._validate_portable_data(payload)
        except (TypeError, ValueError, ValidationError) as error:
            raise UserError(
                _("Template %s has an invalid portable payload. Run Refresh Snapshot.\n\n%s")
                % (self.name, error)
            ) from error
        if not self.portable_valid:
            raise UserError(
                _("Template %s has an invalid portable payload. Run Refresh Snapshot.")
                % self.name
            )
        canonical = self._canonical_snapshot_json(payload)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not self.portable_hash or digest != self.portable_hash:
            raise UserError(
                _("Template %s failed its portable hash check. Run Refresh Snapshot before exporting.")
                % self.name
            )
        return payload

    def _validated_export_payloads(self):
        self._check_export_access()
        if not self:
            raise UserError(_("Select at least one Access Guard template to export."))
        blocked = []
        payloads = {}
        for template in self.sorted(key=lambda item: (item.template_code or "", item.id)):
            try:
                payloads[template.id] = template._export_portable_payload()
            except UserError as error:
                blocked.append("%s: %s" % (template.name, error))
        if blocked:
            raise UserError(
                _("The following templates cannot be exported:\n- %s")
                % "\n- ".join(blocked)
            )
        return payloads

    @staticmethod
    def _safe_filename_part(value, fallback):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-")
        return safe or fallback

    def _single_export_filename(self):
        self.ensure_one()
        code = self._safe_filename_part(self.template_code, "TEMPLATE")
        version = self._safe_filename_part(self.template_version, "1.0")
        return "ACCESS_GUARD_%s_v%s.json" % (code, version)

    def _exported_at(self):
        return fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    def _export_metadata(self, exported_at):
        return {
            "access_guard_version": self._source_module_version(),
            "exported_at": exported_at,
            "exported_from_odoo_version": release.major_version,
        }

    def _build_export_document(self, payloads=None, exported_at=None):
        payloads = payloads or self._validated_export_payloads()
        exported_at = exported_at or self._exported_at()
        metadata = self._export_metadata(exported_at)
        ordered = self.sorted(key=lambda item: (item.template_code or "", item.id))
        if len(ordered) == 1:
            template = ordered
            payload = payloads[template.id]
            metadata.update({
                "portability_status": template.portability_status,
                "portable_hash": template.portable_hash,
                "snapshot_hash": template.snapshot_hash,
                "template_description": template.description or False,
                "portability_report": template.portability_report or False,
            })
            return {
                "format": "access_guard_template",
                "format_version": EXPORT_FORMAT_VERSION,
                "export_metadata": metadata,
                "dependencies": payload.get("dependencies", {"required_modules": []}),
                "template": payload,
            }, template._single_export_filename()

        required_modules = set()
        entries = []
        for template in ordered:
            payload = payloads[template.id]
            dependencies = payload.get("dependencies", {"required_modules": []})
            required_modules.update(dependencies.get("required_modules") or [])
            entries.append({
                "template_code": template.template_code,
                "template_version": template.template_version,
                "schema_version": template.schema_version,
                "portable_hash": template.portable_hash,
                "snapshot_hash": template.snapshot_hash,
                "portability_status": template.portability_status,
                "description": template.description or False,
                "portability_report": template.portability_report or False,
                "dependencies": dependencies,
                "template": payload,
            })
        metadata["template_count"] = len(entries)
        return {
            "format": "access_guard_template_bundle",
            "format_version": EXPORT_FORMAT_VERSION,
            "export_metadata": metadata,
            "bundle_dependencies": {"required_modules": sorted(required_modules)},
            "templates": entries,
        }, "ACCESS_GUARD_TEMPLATES_%s.json" % len(entries)

    def _export_warning_text(self):
        lines = [
            _("The selected template configuration can be exported, but target-database mapping or review may be required during import."),
            "",
        ]
        for template in self.sorted(key=lambda item: (item.template_code or "", item.id)):
            if template.portability_status not in ("warnings", "mapping_required"):
                continue
            lines.extend([
                "%s: %s" % (template.name, template.portability_status.replace("_", " ").title()),
                template.portability_report or _("No detailed portability report is available."),
                "",
            ])
        return "\n".join(lines).strip()

    def _create_export_download(self, payloads=None):
        document, filename = self._build_export_document(payloads=payloads)
        content = json.dumps(
            document,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")
        wizard = self.env["access.guard.export.wizard"].create({
            "template_ids": [(6, 0, self.ids)],
            "file_data": base64.b64encode(content),
            "filename": filename,
        })
        return wizard._download_action()

    def _action_export_templates(self, export_anyway=False):
        payloads = self._validated_export_payloads()
        requires_confirmation = any(
            template.portability_status in ("warnings", "mapping_required")
            for template in self
        )
        if requires_confirmation and not export_anyway:
            wizard = self.env["access.guard.export.wizard"].create({
                "template_ids": [(6, 0, self.ids)],
                "warning_text": self._export_warning_text(),
            })
            return {
                "type": "ir.actions.act_window",
                "name": _("Confirm Template Export"),
                "res_model": "access.guard.export.wizard",
                "res_id": wizard.id,
                "view_mode": "form",
                "view_id": self.env.ref("npt_access_guard.view_access_guard_export_wizard_form").id,
                "target": "new",
            }
        return self._create_export_download(payloads=payloads)

    def action_export_template(self):
        self.ensure_one()
        return self._action_export_templates()

    def action_export_templates(self):
        return self._action_export_templates()


class AccessGuardExportWizard(models.TransientModel):
    _name = "access.guard.export.wizard"
    _description = "Access Guard Template Export"

    template_ids = fields.Many2many("access.guard.template", string="Templates", readonly=True)
    warning_text = fields.Text(readonly=True)
    file_data = fields.Binary(readonly=True, attachment=False)
    filename = fields.Char(readonly=True)

    def _download_action(self):
        self.ensure_one()
        if not self.file_data or not self.filename:
            raise UserError(_("No export file has been generated."))
        return {
            "type": "ir.actions.act_url",
            "url": (
                "/web/content?model=access.guard.export.wizard&id=%s"
                "&field=file_data&filename_field=filename&download=true&filename=%s"
            ) % (self.id, quote(self.filename)),
            "target": "self",
        }

    def action_export_anyway(self):
        self.ensure_one()
        if not self.template_ids:
            raise UserError(_("No templates were selected for export."))
        return self.template_ids._action_export_templates(export_anyway=True)
