import ast
import hashlib
import json

from markupsafe import Markup, escape

from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


RESOLUTION_STATUSES = [
    ("ready", "Ready"),
    ("mapping_required", "Mapping Required"),
    ("blocked", "Blocked"),
]


class AccessGuardImportedTemplate(models.Model):
    _inherit = "access.guard.template"

    imported_at = fields.Datetime(readonly=True, copy=False)
    imported_filename = fields.Char(readonly=True, copy=False)
    imported_access_guard_version = fields.Char(readonly=True, copy=False)
    import_format_version = fields.Char(readonly=True, copy=False)
    resolution_payload = fields.Text(readonly=True, copy=False)
    resolution_hash = fields.Char(readonly=True, copy=False, index=True)
    resolution_status = fields.Selection(
        RESOLUTION_STATUSES,
        readonly=True,
        copy=False,
    )
    resolution_date = fields.Datetime(readonly=True, copy=False)

    def _resolution_payload_dict(self):
        self.ensure_one()
        if not self.resolution_payload:
            return {}
        try:
            return json.loads(self.resolution_payload)
        except (TypeError, ValueError):
            return {}

    def _validate_resolution_payload(self):
        self.ensure_one()
        payload = self._resolution_payload_dict()
        if not isinstance(payload, dict) or payload.get("format") != "access_guard_resolution":
            raise UserError(_("The imported template has no valid target resolution."))
        references = payload.get("references")
        if not isinstance(references, dict):
            raise UserError(_("The imported template resolution is malformed."))
        canonical = self._canonical_snapshot_json(payload)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not self.resolution_hash or digest != self.resolution_hash:
            raise UserError(_("The imported template resolution hash is invalid."))
        if self.resolution_status != "ready":
            raise UserError(_("The imported template resolution is not ready to apply."))
        return payload

    def _resolution_reference(self, path):
        resolution = self._validate_resolution_payload()
        reference = (resolution.get("references") or {}).get(path)
        if not reference:
            raise UserError(_("Target resolution is missing for %s.") % path)
        return reference

    def _resolved_record(self, path, expected_model=None):
        reference = self._resolution_reference(path)
        target_model = reference.get("target_model")
        target_id = reference.get("target_id")
        if not target_model or not target_id or target_model not in self.env:
            raise UserError(_("Target resolution for %s is incomplete.") % path)
        if expected_model:
            allowed = set(expected_model) if isinstance(expected_model, (tuple, list, set)) else {expected_model}
            if target_model not in allowed:
                raise UserError(
                    _("Target resolution for %(path)s points to %(actual)s instead of %(expected)s.",
                      path=path, actual=target_model, expected=", ".join(sorted(allowed)))
                )
        record = self.env[target_model].sudo().browse(target_id).exists()
        if not record:
            raise UserError(_("The resolved target for %s no longer exists.") % path)
        return record

    def _resolution_mappings(self):
        self.ensure_one()
        payload = self._validate_resolution_payload()
        return {
            path: {
                "target_model": item.get("target_model"),
                "target_id": item.get("target_id"),
            }
            for path, item in (payload.get("references") or {}).items()
            if item.get("target_model") and item.get("target_id")
        }

    def _validate_imported_apply(self):
        self.ensure_one()
        if self.source_type != "imported":
            return True
        try:
            portable = json.loads(self.portable_payload or "")
            self._validate_portable_data(portable)
        except (TypeError, ValueError, ValidationError) as error:
            raise UserError(_("The imported portable payload is invalid: %s") % error) from error
        canonical = self._canonical_snapshot_json(portable)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not self.portable_hash or digest != self.portable_hash:
            raise UserError(_("The imported portable payload hash is invalid."))
        self._validate_resolution_payload()
        analysis = self.env["access.guard.import.resolver"].resolve_template(
            portable,
            mappings=self._resolution_mappings(),
        )
        blocked = []
        for result in analysis["results"]:
            if result["status"] in ("missing", "blocked", "mapping_required", "ambiguous"):
                blocked.append(result["message"])
        if blocked:
            raise UserError(
                _("Imported template references are no longer ready:\n- %s")
                % "\n- ".join(dict.fromkeys(blocked))
            )
        return True

    def action_apply_template(self):
        self.ensure_one()
        if not self.env.is_superuser() and not self.env.user.has_group(
            "npt_access_guard.group_access_guard_manager"
        ):
            raise AccessError(_("Only Access Guard Managers can apply templates."))
        if self.source_type == "imported":
            self._validate_imported_apply()
            return {
                "type": "ir.actions.act_window",
                "name": _("Apply Imported Access Template"),
                "res_model": "access.guard.apply.template.wizard",
                "view_mode": "form",
                "target": "new",
                "context": {"default_template_id": self.id},
            }
        return super().action_apply_template()

    def action_view_resolution(self):
        self.ensure_one()
        self._validate_resolution_payload()
        return {
            "type": "ir.actions.act_window",
            "name": _("Target Resolution"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("npt_access_guard.view_access_guard_template_resolution").id,
            "target": "new",
        }

    def _helper_ui_element(self, reference, path):
        item = self._resolution_reference(path)
        if item.get("target_id"):
            return self._resolved_record(path, "access.guard.ui.element")
        candidate = dict(item.get("candidate_values") or {})
        required = {"model_id", "type", "view_type", "match_mode", "match_value"}
        if not required.issubset(candidate):
            raise UserError(_("UI helper resolution is incomplete for %s.") % path)
        domain = [
            ("model_id", "=", candidate["model_id"]),
            ("type", "=", candidate["type"]),
            ("view_type", "=", candidate["view_type"]),
            ("match_mode", "=", candidate["match_mode"]),
            ("match_value", "=", str(candidate["match_value"])),
            ("attribute_name", "=", candidate.get("attribute_name") or ""),
            ("view_id", "=", candidate.get("view_id") or False),
        ]
        Helper = self.env["access.guard.ui.element"].sudo().with_context(active_test=False)
        helpers = Helper.search(domain)
        if len(helpers) > 1:
            raise UserError(_("Multiple equivalent UI helpers exist for %s.") % path)
        if helpers:
            return helpers
        unique_domain = [
            ("model_id", "=", candidate["model_id"]),
            ("type", "=", candidate["type"]),
            ("match_mode", "=", candidate["match_mode"]),
            ("match_value", "=", str(candidate["match_value"])),
            ("attribute_name", "=", candidate.get("attribute_name") or ""),
        ]
        if Helper.search_count(unique_domain):
            raise UserError(
                _("A conflicting UI helper already uses the same runtime identity for %s.") % path
            )
        candidate.update({
            "match_value": str(candidate["match_value"]),
            "label": reference.get("label") or str(candidate["match_value"]),
            "active": bool(reference.get("active", True)),
        })
        return Helper.create(candidate)

    def _helper_search_item(self, reference, path):
        item = self._resolution_reference(path)
        if item.get("target_id"):
            return self._resolved_record(path, "access.guard.search.item")
        candidate = dict(item.get("candidate_values") or {})
        required = {"model_id", "field_id", "type"}
        if not required.issubset(candidate):
            raise UserError(_("Search helper resolution is incomplete for %s.") % path)
        Helper = self.env["access.guard.search.item"].sudo().with_context(active_test=False)
        helpers = Helper.search([
            ("model_id", "=", candidate["model_id"]),
            ("field_id", "=", candidate["field_id"]),
            ("type", "=", candidate["type"]),
        ])
        if len(helpers) > 1:
            raise UserError(_("Multiple equivalent search helpers exist for %s.") % path)
        if helpers:
            return helpers
        candidate.update({
            "label": reference.get("label") or reference.get("field") or _("Imported Search Item"),
            "active": bool(reference.get("active", True)),
        })
        return Helper.create(candidate)

    def _helper_view_mode(self, reference, path):
        item = self._resolution_reference(path)
        if item.get("target_id"):
            return self._resolved_record(path, "access.guard.view.mode")
        code = (item.get("candidate_values") or {}).get("code") or reference.get("code")
        if not code:
            raise UserError(_("View-mode helper resolution is incomplete for %s.") % path)
        Helper = self.env["access.guard.view.mode"].sudo()
        helpers = Helper.search([("code", "=", code)])
        if len(helpers) > 1:
            raise UserError(_("Multiple view-mode helpers use code %s.") % code)
        return helpers or Helper.create({"code": code, "name": reference.get("name") or code.title()})

    @api.model
    def _domain_field(self, model_name, field_path):
        current_model = model_name
        field = self.env["ir.model.fields"]
        for part in (field_path or "").split("."):
            if part == "id":
                return None
            field = self.env["ir.model.fields"].sudo().search([
                ("model", "=", current_model),
                ("name", "=", part),
            ], limit=1)
            if not field:
                return field
            current_model = field.relation if field.ttype in ("many2one", "one2many", "many2many") else None
        return field

    def _mapped_domain(self, expression, model_name, path):
        if not expression:
            return expression
        resolution = self._resolution_reference(path)
        if resolution.get("status") != "resolved_manually":
            return expression
        target_id = resolution.get("target_id")
        if not target_id:
            raise UserError(_("Domain mapping is missing for %s.") % path)
        tree = ast.parse(expression, mode="eval")
        source_values = set()

        class DomainMapper(ast.NodeTransformer):
            def visit_Tuple(mapper, node):
                mapper.generic_visit(node)
                return mapper._map_condition(node)

            def visit_List(mapper, node):
                mapper.generic_visit(node)
                return mapper._map_condition(node)

            def _map_condition(mapper, node):
                if len(node.elts) < 3:
                    return node
                field_node = node.elts[0]
                if not isinstance(field_node, ast.Constant) or not isinstance(field_node.value, str):
                    return node
                field_name = field_node.value
                field = self._domain_field(model_name, field_name)
                relational = field_name == "id" or field_name.endswith(".id") or (
                    field and field.ttype in (
                        "many2one", "one2many", "many2many", "reference", "many2one_reference"
                    )
                )
                if not relational:
                    return node
                for child in ast.walk(node.elts[2]):
                    if isinstance(child, ast.Constant) and isinstance(child.value, (int, float)) \
                            and not isinstance(child.value, bool):
                        source_values.add(child.value)
                        child.value = target_id
                return node

        mapper = DomainMapper()
        tree = mapper.visit(tree)
        if len(source_values) != 1:
            raise UserError(
                _("Domain %(path)s requires one explicit mapping per source ID; found %(count)s IDs.",
                  path=path, count=len(source_values))
            )
        ast.fix_missing_locations(tree)
        return ast.unparse(tree.body)

    def _create_management_from_imported_template(self, assignments):
        self.ensure_one()
        self._validate_imported_apply()
        payload = self._portable_payload_dict()
        general = payload.get("general") or {}
        global_values = payload.get("global") or {}

        def record_id(path, model=None):
            return self._resolved_record(path, model).id

        ui_helpers = {}
        search_helpers = {}
        view_modes = {}

        def ui_helper(reference, path):
            if path not in ui_helpers:
                ui_helpers[path] = self._helper_ui_element(reference, path)
            return ui_helpers[path]

        def search_helper(reference, path):
            if path not in search_helpers:
                search_helpers[path] = self._helper_search_item(reference, path)
            return search_helpers[path]

        def view_mode(reference, path):
            if path not in view_modes:
                view_modes[path] = self._helper_view_mode(reference, path)
            return view_modes[path]

        vals = {
            "name": assignments.get("name") or self.name,
            "active": bool(assignments.get("active", True)),
            "category": self.category,
            "template_id": self.id,
            "read_only": bool(assignments.get("read_only")),
            "apply_by_group": bool(assignments.get("apply_by_group") or assignments.get("group_ids")),
            "apply_without_company": bool(assignments.get("apply_without_company")),
            "default_internal_user": False,
            "default_portal_user": False,
            "disable_developer_mode": bool(general.get("disable_developer_mode")),
            "user_ids": [Command.set(assignments.get("user_ids") or [])],
            "group_ids": [Command.set(assignments.get("group_ids") or [])],
            "company_ids": [Command.set(assignments.get("company_ids") or [])],
            "overview_mode": (payload.get("overview") or {}).get("mode") or "hide",
        }
        for key in (
            "hide_chatter", "hide_send_message", "hide_log_note", "hide_schedule_activity",
            "hide_import", "hide_export", "hide_spreadsheet", "hide_add_property",
            "hide_search_panel", "hide_custom_filter", "hide_custom_group",
            "hide_delete_favorite", "disable_login",
        ):
            vals["global_%s" % key] = bool(global_values.get(key))

        vals["menu_line_ids"] = [
            Command.create({
                "sequence": line.get("sequence", 10),
                "menu_id": record_id("menus.%s.menu" % index, "ir.ui.menu"),
                "include_children": bool(line.get("include_children")),
            })
            for index, line in enumerate(payload.get("menus") or [])
        ]

        model_commands = []
        for index, line in enumerate(payload.get("model_rules") or []):
            path = "model_rules.%s" % index
            model_commands.append(Command.create({
                "sequence": line.get("sequence", 10),
                "model_id": record_id("%s.model" % path, "ir.model"),
                "report_ids": [Command.set([
                    record_id("%s.reports.%s" % (path, item_index), "ir.actions.report")
                    for item_index, _item in enumerate(line.get("reports") or [])
                ])],
                "action_ids": [Command.set([
                    record_id("%s.server_actions.%s" % (path, item_index), "ir.actions.server")
                    for item_index, _item in enumerate(line.get("server_actions") or [])
                ])],
                "window_action_ids": [Command.set([
                    record_id("%s.window_actions.%s" % (path, item_index), "ir.actions.act_window")
                    for item_index, _item in enumerate(line.get("window_actions") or [])
                ])],
                "hide_view_mode_ids": [Command.set([
                    view_mode(item, "%s.hidden_view_modes.%s" % (path, item_index)).id
                    for item_index, item in enumerate(line.get("hidden_view_modes") or [])
                ])],
                "view_ids": [Command.set([
                    record_id("%s.views.%s" % (path, item_index), "ir.ui.view")
                    for item_index, _item in enumerate(line.get("views") or [])
                ])],
                **{
                    key: bool(line.get(key))
                    for key in (
                        "hide_create", "hide_edit", "hide_delete", "hide_archive",
                        "hide_duplicate", "hide_import", "hide_export", "hide_spreadsheet",
                    )
                },
            }))
        vals["model_rule_ids"] = model_commands

        vals["field_rule_ids"] = [
            Command.create({
                "sequence": line.get("sequence", 10),
                "model_id": record_id("field_rules.%s.model" % index, "ir.model"),
                "field_ids": [Command.set([
                    record_id("field_rules.%s.fields.%s" % (index, field_index), "ir.model.fields")
                    for field_index, _field in enumerate(line.get("fields") or [])
                ])],
                **{
                    key: bool(line.get(key))
                    for key in (
                        "invisible", "read_only", "required", "remove_create_option",
                        "remove_edit_option", "remove_external_link",
                    )
                },
            })
            for index, line in enumerate(payload.get("field_rules") or [])
        ]

        conditional_commands = []
        for index, line in enumerate(payload.get("conditional_rules") or []):
            path = "conditional_rules.%s" % index
            model_name = (line.get("model") or {}).get("model")
            conditional_commands.append(Command.create({
                "sequence": line.get("sequence", 10),
                "model_id": record_id("%s.model" % path, "ir.model"),
                "apply_attrs": bool(line.get("apply_attrs")),
                "apply_domain": bool(line.get("apply_domain")),
                "attr_field_ids": [Command.set([
                    record_id("%s.attr_fields.%s" % (path, field_index), "ir.model.fields")
                    for field_index, _field in enumerate(line.get("attr_fields") or [])
                ])],
                "attr_button_ids": [Command.set([
                    ui_helper(item, "%s.attr_buttons.%s" % (path, item_index)).id
                    for item_index, item in enumerate(line.get("attr_buttons") or [])
                ])],
                "attr_type": line.get("attr_type") or False,
                "attrs_domain": self._mapped_domain(
                    line.get("attrs_domain"), model_name, "%s.attrs_domain" % path
                ) if line.get("apply_attrs") or line.get("attrs_domain") else False,
                "domain_field_id": record_id("%s.domain_field" % path, "ir.model.fields")
                if line.get("domain_field") else False,
                "domain_expression": self._mapped_domain(
                    line.get("domain_expression"), model_name, "%s.domain_expression" % path
                ) if line.get("apply_domain") or line.get("domain_expression") else False,
            }))
        vals["field_conditional_ids"] = conditional_commands

        domain_commands = []
        for index, line in enumerate(payload.get("domain_rules") or []):
            path = "domain_rules.%s" % index
            model_name = (line.get("model") or {}).get("model")
            domain_commands.append(Command.create({
                "sequence": line.get("sequence", 10),
                "model_id": record_id("%s.model" % path, "ir.model"),
                "soft_restrict": bool(line.get("soft_restrict")),
                "apply_relational_fields": bool(line.get("apply_relational_fields")),
                "perm_read": bool(line.get("perm_read")),
                "perm_create": bool(line.get("perm_create")),
                "perm_write": bool(line.get("perm_write")),
                "perm_delete": bool(line.get("perm_delete")),
                "apply_filter": bool(line.get("apply_filter")),
                "domain_expression": self._mapped_domain(
                    line.get("domain_expression"), model_name, "%s.domain_expression" % path
                ),
            }))
        vals["domain_rule_ids"] = domain_commands

        ui_commands = []
        ui_field_map = {
            "button_elements": "button_element_ids",
            "action_button_elements": "action_button_element_ids",
            "smart_button_elements": "smart_button_element_ids",
            "tab_elements": "tab_element_ids",
            "kanban_link_elements": "kanban_link_element_ids",
        }
        for index, line in enumerate(payload.get("ui_rules") or []):
            path = "ui_rules.%s" % index
            command = {
                "sequence": line.get("sequence", 10),
                "model_id": record_id("%s.model" % path, "ir.model"),
            }
            for source_key, target_key in ui_field_map.items():
                command[target_key] = [Command.set([
                    ui_helper(item, "%s.%s.%s" % (path, source_key, item_index)).id
                    for item_index, item in enumerate(line.get(source_key) or [])
                ])]
            ui_commands.append(Command.create(command))
        vals["ui_rule_ids"] = ui_commands

        search_commands = []
        for index, line in enumerate(payload.get("search_rules") or []):
            path = "search_rules.%s" % index
            search_commands.append(Command.create({
                "sequence": line.get("sequence", 10),
                "model_id": record_id("%s.model" % path, "ir.model"),
                "hide_search_panel": bool(line.get("hide_search_panel")),
                "filter_item_ids": [Command.set([
                    search_helper(item, "%s.filter_items.%s" % (path, item_index)).id
                    for item_index, item in enumerate(line.get("filter_items") or [])
                ])],
                "group_item_ids": [Command.set([
                    search_helper(item, "%s.group_items.%s" % (path, item_index)).id
                    for item_index, item in enumerate(line.get("group_items") or [])
                ])],
                "hide_custom_filter": bool(line.get("hide_custom_filter")),
                "hide_custom_group": bool(line.get("hide_custom_group")),
                "hide_delete_favorite": bool(line.get("hide_delete_favorite")),
            }))
        vals["search_panel_rule_ids"] = search_commands

        vals["chatter_rule_ids"] = [
            Command.create({
                "sequence": line.get("sequence", 10),
                "model_id": record_id("chatter_rules.%s.model" % index, "ir.model"),
                "hide_chatter": bool(line.get("hide_chatter")),
                "hide_send_message": bool(line.get("hide_send_message")),
                "hide_log_note": bool(line.get("hide_log_note")),
                "hide_schedule_activity": bool(line.get("hide_schedule_activity")),
            })
            for index, line in enumerate(payload.get("chatter_rules") or [])
        ]

        vals["overview_picking_type_ids"] = [Command.set([
            record_id("overview.picking_types.%s" % index, "stock.picking.type")
            for index, _item in enumerate((payload.get("overview") or {}).get("picking_types") or [])
        ])]

        if assignments.get("copy_acl_lines", True):
            vals["acl_rule_ids"] = [
                Command.create({
                    "model_id": record_id("acl_rules.%s.model" % index, "ir.model"),
                    "group_id": record_id("acl_rules.%s.group" % index, "res.groups"),
                    "perm_read": bool(line.get("perm_read")),
                    "perm_write": bool(line.get("perm_write")),
                    "perm_create": bool(line.get("perm_create")),
                    "perm_unlink": bool(line.get("perm_unlink")),
                })
                for index, line in enumerate(payload.get("acl_rules") or [])
            ]

        with self.env.cr.savepoint():
            return self.env["access.guard.management"].create(vals)


class AccessGuardImportedApplyWizard(models.TransientModel):
    _inherit = "access.guard.apply.template.wizard"

    state = fields.Selection(
        [("configure", "Configure"), ("preview", "Preview")],
        default="configure",
        required=True,
        readonly=True,
    )
    template_source_type = fields.Selection(related="template_id.source_type", readonly=True)
    preview_html = fields.Html(readonly=True, sanitize=False)
    validation_status = fields.Selection(
        [("draft", "Not Validated"), ("ready", "Ready to Apply"), ("blocked", "Blocked")],
        default="draft",
        readonly=True,
    )

    def _check_apply_access(self):
        if not self.env.is_superuser() and not self.env.user.has_group(
            "npt_access_guard.group_access_guard_manager"
        ):
            raise AccessError(_("Only Access Guard Managers can apply templates."))

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        template_id = vals.get("template_id") or self.env.context.get("default_template_id")
        template = self.env["access.guard.template"].browse(template_id).exists()
        if template and template.source_type == "imported":
            general = template._portable_payload_dict().get("general") or {}
            vals.update({
                "apply_by_group": bool(general.get("apply_by_group")),
                "apply_without_company": bool(general.get("apply_without_company")),
                "make_read_only": bool(general.get("read_only")),
            })
        return vals

    @api.onchange(
        "template_id", "name", "user_ids", "group_ids", "company_ids", "active",
        "apply_by_group", "apply_without_company", "make_read_only", "copy_acl_lines",
    )
    def _invalidate_preview(self):
        for wizard in self:
            wizard.state = "configure"
            wizard.validation_status = "draft"
            wizard.preview_html = False

    def _assignment_values(self):
        self.ensure_one()
        return {
            "name": self.name or self.template_id.name,
            "user_ids": self.user_ids.ids,
            "group_ids": self.group_ids.ids,
            "company_ids": self.company_ids.ids,
            "active": self.active,
            "read_only": self.make_read_only,
            "apply_by_group": self.apply_by_group,
            "apply_without_company": self.apply_without_company,
            "copy_acl_lines": self.copy_acl_lines,
        }

    def _validate_assignments(self):
        self.ensure_one()
        self._check_apply_access()
        if not self.user_ids and not self.group_ids:
            raise UserError(_("Select at least one user or group."))
        if self.template_id.source_type == "imported":
            self.template_id._validate_imported_apply()
        return True

    def _preview_counts(self):
        self.ensure_one()
        if self.template_id.source_type == "imported":
            payload = self.template_id._portable_payload_dict()
            return {
                "Menus": len(payload.get("menus") or []),
                "Model Rules": len(payload.get("model_rules") or []),
                "Field Rules": len(payload.get("field_rules") or []),
                "Conditional Rules": len(payload.get("conditional_rules") or []),
                "Domain Rules": len(payload.get("domain_rules") or []),
                "UI Rules": len(payload.get("ui_rules") or []),
                "Search Rules": len(payload.get("search_rules") or []),
                "Chatter Rules": len(payload.get("chatter_rules") or []),
                "ACL Rules": len(payload.get("acl_rules") or []) if self.copy_acl_lines else 0,
            }
        source = self.template_id._local_source_rule()
        return {
            "Menus": len(source.menu_line_ids),
            "Model Rules": len(source.model_rule_ids),
            "Field Rules": len(source.field_rule_ids),
            "Conditional Rules": len(source.field_conditional_ids),
            "Domain Rules": len(source.domain_rule_ids),
            "UI Rules": len(source.ui_rule_ids),
            "Search Rules": len(source.search_panel_rule_ids),
            "Chatter Rules": len(source.chatter_rule_ids),
            "ACL Rules": len(source.acl_rule_ids) if self.copy_acl_lines else 0,
        }

    def _build_apply_preview(self):
        self.ensure_one()
        counts = self._preview_counts()
        rows = Markup("").join(
            Markup("<li><strong>%s:</strong> %s</li>") % (escape(label), count)
            for label, count in counts.items()
        )
        warnings = []
        if counts["ACL Rules"]:
            warnings.append(_("Template contains ACL grants. ACLs are additive for every member of their group."))
        if self.template_id.source_type == "imported":
            resolution = self.template_id._resolution_payload_dict()
            manual_count = sum(
                1 for item in (resolution.get("references") or {}).values()
                if item.get("status") == "resolved_manually"
            )
            if manual_count:
                warnings.append(_("%s target reference(s) were manually mapped.") % manual_count)
        warning_html = Markup("<p>%s</p>") % escape(_("None"))
        if warnings:
            warning_html = Markup("<ul>%s</ul>") % Markup("").join(
                Markup("<li>%s</li>") % escape(warning) for warning in warnings
            )
        return Markup(
            "<h3>%s <small>v%s</small></h3>"
            "<h4>%s</h4><p><strong>%s:</strong> %s<br/>"
            "<strong>%s:</strong> %s<br/><strong>%s:</strong> %s</p>"
            "<h4>%s</h4><ul>%s</ul><h4>%s</h4>%s"
            "<p><strong>%s</strong></p>"
        ) % (
            escape(self.template_id.name), escape(self.template_id.template_version),
            escape(_("Assignments")), escape(_("Users")), escape(", ".join(self.user_ids.mapped("name")) or _("None")),
            escape(_("Groups")), escape(", ".join(self.group_ids.mapped("name")) or _("None")),
            escape(_("Companies")), escape(", ".join(self.company_ids.mapped("name")) or _("All/No company filter")),
            escape(_("Will create")), rows, escape(_("Warnings")), warning_html,
            escape(_("READY TO APPLY")),
        )

    def action_preview_validate(self):
        self.ensure_one()
        self._validate_assignments()
        self.write({
            "state": "preview",
            "validation_status": "ready",
            "preview_html": self._build_apply_preview(),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Apply Access Template"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("npt_access_guard.view_access_guard_apply_template_wizard_form").id,
            "target": "new",
        }

    def action_back_to_configure(self):
        self.ensure_one()
        self.write({"state": "configure", "validation_status": "draft", "preview_html": False})
        return {
            "type": "ir.actions.act_window",
            "name": _("Apply Access Template"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("npt_access_guard.view_access_guard_apply_template_wizard_form").id,
            "target": "new",
        }

    def action_apply_template(self):
        self.ensure_one()
        self._validate_assignments()
        if self.template_id.source_type != "imported":
            return super().action_apply_template()
        if self.validation_status != "ready" or self.state != "preview":
            raise UserError(_("Preview and validate the imported template before applying it."))
        with self.env.cr.savepoint():
            rule = self.template_id._create_management_from_imported_template(self._assignment_values())
        return {
            "type": "ir.actions.act_window",
            "name": _("Access Rule"),
            "res_model": "access.guard.management",
            "res_id": rule.id,
            "view_mode": "form",
        }


class AccessGuardImportExecution(models.TransientModel):
    _inherit = "access.guard.import.wizard"

    state = fields.Selection(
        selection_add=[("imported", "Imported")],
        ondelete={"imported": "cascade"},
    )
    imported_template_ids = fields.Many2many(
        "access.guard.template",
        "access_guard_import_created_rel",
        "wizard_id",
        "template_id",
        string="Imported Templates",
        readonly=True,
    )
    skipped_template_ids = fields.Many2many(
        "access.guard.template",
        "access_guard_import_skipped_rel",
        "wizard_id",
        "template_id",
        string="Skipped Existing Templates",
        readonly=True,
    )
    imported_count = fields.Integer(readonly=True)
    skipped_count = fields.Integer(readonly=True)
    import_result_summary = fields.Text(readonly=True)

    def write(self, vals):
        if not self.env.context.get("access_guard_import_internal") and "file_data" in vals:
            vals = dict(vals)
            vals.update({
                "state": "upload",
                "overall_status": False,
                "validation_report": False,
                "validation_report_html": False,
                "import_plan": False,
                "imported_template_ids": [Command.clear()],
                "skipped_template_ids": [Command.clear()],
                "imported_count": 0,
                "skipped_count": 0,
                "import_result_summary": False,
            })
        return super().write(vals)

    @api.model
    def _resolution_document(self, result):
        references = {}
        for item in result["analysis"]["results"]:
            references[item["path"]] = {
                key: item.get(key)
                for key in (
                    "category", "reference_type", "source_label", "source_reference",
                    "status", "target_model", "target_id", "candidate_values",
                )
                if item.get(key) not in (None, False, "", [])
            }
        return {
            "format": "access_guard_resolution",
            "format_version": "1.0",
            "portable_hash": result["portable_hash"],
            "references": references,
        }

    def _entry_metadata(self, entry, metadata, file_format):
        if file_format == "access_guard_template":
            return {
                "description": metadata.get("template_description"),
                "portability_status": metadata.get("portability_status"),
                "portability_report": metadata.get("portability_report"),
            }
        return {
            "description": entry.get("description"),
            "portability_status": entry.get("portability_status"),
            "portability_report": entry.get("portability_report"),
        }

    def action_import_templates(self):
        self.ensure_one()
        self._check_import_access()
        results = self._run_validation()
        if self.overall_status not in ("ready", "exact_duplicate"):
            raise UserError(_("Complete validation and all required mappings before importing."))

        document = self._decode_document()
        file_format, metadata = self._validate_root_envelope(document)
        entries = self._entries(document, file_format)
        created = self.env["access.guard.template"]
        skipped = self.env["access.guard.template"]

        with self.env.cr.savepoint():
            for result in results:
                operation = result["operation"]
                if operation == "SKIP_EXACT_DUPLICATE":
                    existing = self.env["access.guard.template"].sudo().browse(
                        result.get("existing_template_id")
                    ).exists()
                    if not existing:
                        raise UserError(_("An exact duplicate disappeared during import. Validate again."))
                    skipped |= existing
                    continue
                if operation == "BLOCK_VERSION_CONFLICT" or result["status"] == "blocked":
                    raise UserError(_("A template version conflict or blocked reference prevents import."))
                if operation not in ("CREATE_NEW", "REVIEW_EXISTING_OTHER_VERSION"):
                    raise UserError(_("Unsupported import operation: %s") % operation)

                entry = entries[result["index"]]
                payload = entry["template"]
                identity = payload.get("template") or {}
                origin = self._entry_metadata(entry, metadata, file_format)
                resolution = self._resolution_document(result)
                resolution_json = self.env["access.guard.template"]._pretty_snapshot_json(resolution)
                resolution_hash = hashlib.sha256(
                    self.env["access.guard.template"]._canonical_snapshot_json(resolution).encode("utf-8")
                ).hexdigest()
                portability_status = origin.get("portability_status") or "ready"
                if portability_status not in ("ready", "warnings", "mapping_required"):
                    portability_status = "ready"
                portability_report = origin.get("portability_report") or (
                    "Imported Portable Template\n\n"
                    "The portable payload passed schema, dependency, hash, and target-reference validation."
                )
                template = self.env["access.guard.template"].create({
                    "name": identity.get("name") or result["code"],
                    "template_code": result["code"],
                    "template_version": result["version"],
                    "schema_version": result["schema_version"],
                    "category": identity.get("category") or "custom",
                    "description": origin.get("description") or False,
                    "source_type": "imported",
                    "source_management_id": False,
                    "source_module_version": identity.get("source_module_version")
                    or metadata.get("access_guard_version"),
                    "portable_payload": self.env["access.guard.template"]._pretty_snapshot_json(payload),
                    "portable_hash": result["portable_hash"],
                    "portability_status": portability_status,
                    "portability_report": portability_report,
                    "imported_at": fields.Datetime.now(),
                    "imported_filename": self.filename,
                    "imported_access_guard_version": metadata.get("access_guard_version"),
                    "import_format_version": document.get("format_version"),
                    "resolution_payload": resolution_json,
                    "resolution_hash": resolution_hash,
                    "resolution_status": "ready",
                    "resolution_date": fields.Datetime.now(),
                })
                created |= template

        summary = _("Imported: %(imported)s\nSkipped duplicates: %(skipped)s",
                    imported=len(created), skipped=len(skipped))
        if skipped:
            summary += "\n" + _(
                "Matching templates already exist in Access Templates. Skipped duplicates were not created again; their origin is unchanged."
            )
        self.with_context(access_guard_import_internal=True).write({
            "state": "imported",
            "imported_template_ids": [Command.set(created.ids)],
            "skipped_template_ids": [Command.set(skipped.ids)],
            "imported_count": len(created),
            "skipped_count": len(skipped),
            "import_result_summary": summary,
        })
        return self._reopen_action()

    def action_open_imported_templates(self):
        self.ensure_one()
        self._check_import_access()
        templates = self.imported_template_ids | self.skipped_template_ids
        if not templates:
            raise UserError(_("No imported or skipped templates are available to open."))
        action = {
            "type": "ir.actions.act_window",
            "name": (
                _("Imported Template") if len(templates) == 1 and self.imported_template_ids
                else _("Existing Template") if len(templates) == 1
                else _("Access Templates")
            ),
            "res_model": "access.guard.template",
            "view_mode": "list,form",
            "domain": [("id", "in", templates.ids)],
            "context": {"active_test": False},
        }
        if len(templates) == 1:
            action.update({"res_id": templates.id, "view_mode": "form"})
        return action
