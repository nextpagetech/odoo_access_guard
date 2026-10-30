import copy
from html import escape

from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError


ACCESS_GUARD_CATEGORIES = [
    ("sales", "Sales"),
    ("purchase", "Purchase"),
    ("inventory", "Inventory"),
    ("manufacturing", "Manufacturing"),
    ("accounting", "Accounting"),
    ("hr", "HR"),
    ("readonly", "Read Only"),
    ("custom", "Custom"),
]


class AccessGuardManagement(models.Model):
    _inherit = "access.guard.management"

    category = fields.Selection(
        ACCESS_GUARD_CATEGORIES,
        string="Category",
        default="custom",
        help="Functional category used to group access rules and templates.",
    )
    template_id = fields.Many2one(
        "access.guard.template",
        string="Applied Template",
        readonly=True,
        copy=False,
    )
    conflict_count = fields.Integer(
        string="Warnings",
        compute="_compute_conflict_count",
    )

    def _get_readonly_target_models(self):
        self.ensure_one()
        models_seen = self.env["ir.model"]
        for field_name in (
            "model_rule_ids",
            "field_rule_ids",
            "domain_rule_ids",
            "ui_rule_ids",
            "chatter_rule_ids",
            "field_conditional_ids",
            "search_panel_rule_ids",
            "acl_rule_ids",
        ):
            records = self[field_name]
            if records:
                models_seen |= records.mapped("model_id")
        return models_seen.filtered(lambda model: model.model)

    @api.depends(
        "read_only",
        "menu_line_ids",
        "model_rule_ids",
        "field_rule_ids",
        "domain_rule_ids",
        "ui_rule_ids",
        "acl_rule_ids",
    )
    def _compute_conflict_count(self):
        for record in self:
            record.conflict_count = len(record._get_rule_warning_messages())

    def _readonly_domain_spec(self):
        self.ensure_one()
        return {
            "soft_restrict": False,
            "apply_relational": False,
            "allow_read": True,
            "allow_create": False,
            "allow_write": False,
            "allow_delete": False,
            "apply_filter": False,
            "domain": [],
            "enforce_company": not bool(self.apply_without_company),
        }

    @api.model
    def _get_user_model_rules(self, user, rule_ids=None):
        rules = copy.deepcopy(
            super()._get_user_model_rules(user, rule_ids=rule_ids) or {}
        )
        if not user:
            return rules
        readonly_domain = [("active", "=", True), ("read_only", "=", True)]
        focused_rule_ids = rule_ids.ids if hasattr(rule_ids, "ids") else rule_ids
        if focused_rule_ids is not None:
            readonly_domain.append(("id", "in", focused_rule_ids))
        readonly_rules = self.sudo().search(readonly_domain)
        for rule in readonly_rules:
            if not rule._applies_to_user(user):
                continue
            for model in rule._get_readonly_target_models():
                entry = rules.setdefault(model.model, self._empty_serialized_rule_entry())
                entry["hide_create"] = True
                entry["hide_edit"] = True
                entry["hide_delete"] = True
                entry["hide_import"] = True
                entry["domain_rules"].append(rule._readonly_domain_spec())
        return rules

    @api.model
    def _empty_serialized_rule_entry(self):
        return {
            "reports": [],
            "actions": [],
            "view_modes": [],
            "views": [],
            "hide_create": False,
            "hide_edit": False,
            "hide_delete": False,
            "hide_archive": False,
            "hide_duplicate": False,
            "hide_import": False,
            "hide_export": False,
            "hide_spreadsheet": False,
            "hide_add_property": False,
            "fields": {},
            "buttons": [],
            "smart_buttons": [],
            "tabs": [],
            "kanban_links": [],
            "chatter": {
                "hide_all": False,
                "hide_send_message": False,
                "hide_log_note": False,
                "hide_schedule_activity": False,
            },
            "search_panel": {
                "hide_panel": False,
                "hide_custom_filter": False,
                "hide_custom_group": False,
                "hide_delete_favorite": False,
                "filters": [],
                "groups": [],
            },
            "domain_rules": [],
            "disable_login": False,
            "field_conditionals": [],
            "disable_developer_mode": False,
            "overview": [],
        }

    def _access_guard_clone_values(self, target_vals=None, include_acl=True):
        self.ensure_one()
        vals = {
            "name": self.name,
            "active": self.active,
            "category": self.category,
            "read_only": self.read_only,
            "apply_by_group": self.apply_by_group,
            "apply_without_company": self.apply_without_company,
            "default_internal_user": self.default_internal_user,
            "default_portal_user": self.default_portal_user,
            "disable_developer_mode": self.disable_developer_mode,
            "global_hide_chatter": self.global_hide_chatter,
            "global_hide_send_message": self.global_hide_send_message,
            "global_hide_log_note": self.global_hide_log_note,
            "global_hide_schedule_activity": self.global_hide_schedule_activity,
            "global_hide_import": self.global_hide_import,
            "global_hide_export": self.global_hide_export,
            "global_hide_spreadsheet": self.global_hide_spreadsheet,
            "global_hide_add_property": self.global_hide_add_property,
            "global_hide_search_panel": self.global_hide_search_panel,
            "global_hide_custom_filter": self.global_hide_custom_filter,
            "global_hide_custom_group": self.global_hide_custom_group,
            "global_hide_delete_favorite": self.global_hide_delete_favorite,
            "global_disable_login": self.global_disable_login,
            "overview_mode": self.overview_mode,
            "overview_picking_type_ids": [Command.set(self.overview_picking_type_ids.ids)],
            "menu_line_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "menu_id": line.menu_id.id,
                    "include_children": line.include_children,
                })
                for line in self.menu_line_ids
            ],
            "model_rule_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "model_id": line.model_id.id,
                    "report_ids": [Command.set(line.report_ids.ids)],
                    "action_ids": [Command.set(line.action_ids.ids)],
                    "window_action_ids": [Command.set(line.window_action_ids.ids)],
                    "hide_view_mode_ids": [Command.set(line.hide_view_mode_ids.ids)],
                    "view_ids": [Command.set(line.view_ids.ids)],
                    "hide_create": line.hide_create,
                    "hide_edit": line.hide_edit,
                    "hide_delete": line.hide_delete,
                    "hide_archive": line.hide_archive,
                    "hide_duplicate": line.hide_duplicate,
                    "hide_import": line.hide_import,
                    "hide_export": line.hide_export,
                    "hide_spreadsheet": line.hide_spreadsheet,
                })
                for line in self.model_rule_ids
            ],
            "field_rule_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "model_id": line.model_id.id,
                    "field_ids": [Command.set(line.field_ids.ids)],
                    "invisible": line.invisible,
                    "read_only": line.read_only,
                    "required": line.required,
                    "remove_create_option": line.remove_create_option,
                    "remove_edit_option": line.remove_edit_option,
                    "remove_external_link": line.remove_external_link,
                })
                for line in self.field_rule_ids
            ],
            "domain_rule_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "model_id": line.model_id.id,
                    "soft_restrict": line.soft_restrict,
                    "apply_relational_fields": line.apply_relational_fields,
                    "perm_read": line.perm_read,
                    "perm_create": line.perm_create,
                    "perm_write": line.perm_write,
                    "perm_delete": line.perm_delete,
                    "apply_filter": line.apply_filter,
                    "domain_expression": line.domain_expression,
                })
                for line in self.domain_rule_ids
            ],
            "ui_rule_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "model_id": line.model_id.id,
                    "button_element_ids": [Command.set(line.button_element_ids.ids)],
                    "action_button_element_ids": [Command.set(line.action_button_element_ids.ids)],
                    "smart_button_element_ids": [Command.set(line.smart_button_element_ids.ids)],
                    "tab_element_ids": [Command.set(line.tab_element_ids.ids)],
                    "kanban_link_element_ids": [Command.set(line.kanban_link_element_ids.ids)],
                })
                for line in self.ui_rule_ids
            ],
            "field_conditional_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "model_id": line.model_id.id,
                    "apply_attrs": line.apply_attrs,
                    "apply_domain": line.apply_domain,
                    "attr_field_ids": [Command.set(line.attr_field_ids.ids)],
                    "attr_button_ids": [Command.set(line.attr_button_ids.ids)],
                    "attr_type": line.attr_type,
                    "attrs_domain": line.attrs_domain,
                    "domain_field_id": line.domain_field_id.id,
                    "domain_expression": line.domain_expression,
                })
                for line in self.field_conditional_ids
            ],
            "search_panel_rule_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "model_id": line.model_id.id,
                    "hide_search_panel": line.hide_search_panel,
                    "filter_item_ids": [Command.set(line.filter_item_ids.ids)],
                    "group_item_ids": [Command.set(line.group_item_ids.ids)],
                    "hide_custom_filter": line.hide_custom_filter,
                    "hide_custom_group": line.hide_custom_group,
                    "hide_delete_favorite": line.hide_delete_favorite,
                })
                for line in self.search_panel_rule_ids
            ],
            "chatter_rule_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "model_id": line.model_id.id,
                    "hide_chatter": line.hide_chatter,
                    "hide_send_message": line.hide_send_message,
                    "hide_log_note": line.hide_log_note,
                    "hide_schedule_activity": line.hide_schedule_activity,
                })
                for line in self.chatter_rule_ids
            ],
        }
        if include_acl:
            vals["acl_rule_ids"] = [
                Command.create({
                    "model_id": line.model_id.id,
                    "group_id": line.group_id.id,
                    "perm_read": line.perm_read,
                    "perm_write": line.perm_write,
                    "perm_create": line.perm_create,
                    "perm_unlink": line.perm_unlink,
                })
                for line in self.acl_rule_ids
            ]
        if target_vals:
            vals.update(target_vals)
        return vals

    def _get_rule_warning_messages(self):
        self.ensure_one()
        warnings = []
        if self.read_only and not self._get_readonly_target_models():
            warnings.append(_("Read-only is enabled, but no model-based lines are configured. Add at least one model/domain/field/button/ACL line."))
        if self.menu_line_ids and not (
            self.model_rule_ids
            or self.domain_rule_ids
            or self.acl_rule_ids
            or self.field_rule_ids
            or self.ui_rule_ids
        ):
            warnings.append(_("This rule only hides menus. Users may still access records through another menu, link, or RPC if normal security allows it."))
        domain_counts = {}
        for line in self.domain_rule_ids:
            if not line.model_id:
                continue
            domain_counts.setdefault(line.model_id.display_name, 0)
            domain_counts[line.model_id.display_name] += 1
            if not line.soft_restrict and not line.perm_write:
                warnings.append(_("Backend write is blocked on %s. Consider also hiding Edit for clearer UI.") % line.model_id.display_name)
            if not line.soft_restrict and not line.perm_create:
                warnings.append(_("Backend create is blocked on %s. Consider also hiding Create for clearer UI.") % line.model_id.display_name)
        for model_label, count in domain_counts.items():
            if count > 1:
                warnings.append(_("There are %s domain rules on %s. Access Guard combines readable filters with OR, so verify the result in Preview.") % (count, model_label))
        if self.apply_by_group and not self.group_ids:
            warnings.append(_("Apply By Group is enabled, but no groups are selected."))
        if not self.user_ids and not self.group_ids and not self.default_internal_user and not self.default_portal_user:
            warnings.append(_("No users, groups, or default audience are selected. This rule will not apply to anyone."))
        return warnings

    def action_create_template(self):
        self.ensure_one()
        existing = self.env["access.guard.template"].with_context(active_test=False).search([
            ("source_type", "=", "local_rule"),
            ("source_management_id", "=", self.id),
        ], order="id desc")
        if existing:
            action = {
                "type": "ir.actions.act_window",
                "name": _("Saved Templates"),
                "res_model": "access.guard.template",
                "view_mode": "list,form",
                "context": {"active_test": False},
            }
            if len(existing) == 1:
                action.update({"name": _("Access Template"), "res_id": existing.id, "view_mode": "form"})
            else:
                action["domain"] = [("id", "in", existing.ids)]
            return action
        template = self.env["access.guard.template"].create({
            "name": self.name,
            "category": self.category or "custom",
            "source_type": "local_rule",
            "source_management_id": self.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Access Template"),
            "res_model": "access.guard.template",
            "res_id": template.id,
            "view_mode": "form",
        }

    def action_preview_access(self):
        self.ensure_one()
        user = self.user_ids[:1] or self.env.user
        return {
            "type": "ir.actions.act_window",
            "name": _("Access Preview"),
            "res_model": "access.guard.preview.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_user_id": user.id,
                "default_rule_ids": [(6, 0, self.ids)],
            },
        }


class AccessGuardTemplate(models.Model):
    _name = "access.guard.template"
    _description = "Access Guard Template"
    _order = "category, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    category = fields.Selection(ACCESS_GUARD_CATEGORIES, default="custom", required=True)
    source_type = fields.Selection(
        [
            ("local_rule", "Local Rule"),
            ("imported", "Imported"),
        ],
        required=True,
        default="local_rule",
        index=True,
    )
    source_management_id = fields.Many2one(
        "access.guard.management",
        string="Source Rule",
        ondelete="restrict",
        help="Local rule copied when a locally created template is applied.",
    )
    description = fields.Text()
    rule_summary = fields.Char(compute="_compute_rule_summary")
    assigned_rule_ids = fields.One2many("access.guard.management", "template_id", string="Assigned Rules")
    assigned_rule_count = fields.Integer(compute="_compute_assigned_rule_count", string="Assigned Rules")

    @api.depends("assigned_rule_ids")
    def _compute_assigned_rule_count(self):
        for template in self:
            template.assigned_rule_count = self.env["access.guard.management"].with_context(
                active_test=False
            ).search_count([("template_id", "=", template.id)])

    def action_open_assigned_rules(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Assigned Rules"),
            "res_model": "access.guard.management",
            "view_mode": "list,form",
            "domain": [("template_id", "=", self.id)],
            "context": {"active_test": False},
        }

    @api.constrains("source_type", "source_management_id")
    def _check_source_configuration(self):
        for template in self:
            if template.source_type == "local_rule" and not template.source_management_id:
                raise ValidationError(_("A local template must have a source rule."))
            if template.source_type == "imported" and template.source_management_id:
                raise ValidationError(_("An imported template cannot have a local source rule."))

    def _local_source_rule(self):
        self.ensure_one()
        if self.source_type != "local_rule" or not self.source_management_id:
            raise UserError(
                _("Imported template application is not available yet. This template has no local source rule.")
            )
        return self.source_management_id

    @api.depends(
        "source_management_id.menu_line_ids",
        "source_management_id.model_rule_ids",
        "source_management_id.domain_rule_ids",
        "source_management_id.field_rule_ids",
        "source_management_id.ui_rule_ids",
    )
    def _compute_rule_summary(self):
        for record in self:
            source = record.source_management_id
            if not source:
                record.rule_summary = ""
                continue
            parts = [
                _("%s menus") % len(source.menu_line_ids),
                _("%s models") % len(source.model_rule_ids),
                _("%s domains") % len(source.domain_rule_ids),
                _("%s fields") % len(source.field_rule_ids),
                _("%s UI") % len(source.ui_rule_ids),
            ]
            record.rule_summary = ", ".join(parts)

    def action_apply_template(self):
        self.ensure_one()
        self._local_source_rule()
        return {
            "type": "ir.actions.act_window",
            "name": _("Apply Access Template"),
            "res_model": "access.guard.apply.template.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_template_id": self.id},
        }

    def action_open_source_rule(self):
        self.ensure_one()
        source = self._local_source_rule()
        return {
            "type": "ir.actions.act_window",
            "name": _("Source Access Rule"),
            "res_model": "access.guard.management",
            "res_id": source.id,
            "view_mode": "form",
        }


class AccessGuardApplyTemplateWizard(models.TransientModel):
    _name = "access.guard.apply.template.wizard"
    _description = "Apply Access Guard Template"

    template_id = fields.Many2one("access.guard.template", required=True)
    name = fields.Char(string="New Rule Name")
    apply_by_group = fields.Boolean(string="Apply By Group")
    user_ids = fields.Many2many("res.users", string="Users")
    group_ids = fields.Many2many("res.groups", string="Groups")
    company_ids = fields.Many2many("res.company", string="Companies")
    apply_without_company = fields.Boolean(string="Apply Without Company")
    active = fields.Boolean(default=True)
    make_read_only = fields.Boolean(string="Force Read Only")
    copy_acl_lines = fields.Boolean(
        string="Copy ACL Lines",
        default=True,
        help="Copy generated ACL rule lines from the template source rule.",
    )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        template_id = vals.get("template_id") or self.env.context.get("default_template_id")
        if template_id:
            template = self.env["access.guard.template"].browse(template_id)
            source = template.source_management_id
            vals.setdefault("name", _("%s - Applied") % template.name)
            if source:
                vals.setdefault("apply_by_group", source.apply_by_group)
                vals.setdefault("apply_without_company", source.apply_without_company)
                vals.setdefault("make_read_only", source.read_only)
        return vals

    @api.onchange("template_id")
    def _onchange_template_id(self):
        for wizard in self:
            if wizard.template_id:
                source = wizard.template_id.source_management_id
                wizard.name = _("%s - Applied") % wizard.template_id.name
                if source:
                    wizard.apply_by_group = source.apply_by_group
                    wizard.apply_without_company = source.apply_without_company
                    wizard.make_read_only = source.read_only

    def action_apply_template(self):
        self.ensure_one()
        if not self.user_ids and not self.group_ids:
            raise UserError(_("Select at least one user or group."))
        source = self.template_id._local_source_rule()
        vals = source._access_guard_clone_values(
            {
                "name": self.name or self.template_id.name,
                "active": self.active,
                "category": self.template_id.category,
                "template_id": self.template_id.id,
                "user_ids": [Command.set(self.user_ids.ids)],
                "apply_by_group": self.apply_by_group or bool(self.group_ids),
                "group_ids": [Command.set(self.group_ids.ids)],
                "company_ids": [Command.set(self.company_ids.ids)],
                "apply_without_company": self.apply_without_company,
                "read_only": self.make_read_only,
                "default_internal_user": False,
                "default_portal_user": False,
            },
            include_acl=self.copy_acl_lines,
        )
        rule = self.env["access.guard.management"].create(vals)
        return {
            "type": "ir.actions.act_window",
            "name": _("Access Rule"),
            "res_model": "access.guard.management",
            "res_id": rule.id,
            "view_mode": "form",
        }


class AccessGuardPreviewWizard(models.TransientModel):
    _name = "access.guard.preview.wizard"
    _description = "Access Guard Preview"

    user_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    rule_ids = fields.Many2many("access.guard.management", string="Focus Rules")
    preview_html = fields.Html(compute="_compute_preview_html", sanitize=False)
    conflict_html = fields.Html(compute="_compute_preview_html", sanitize=False)

    @api.depends("user_id", "company_id", "rule_ids")
    def _compute_preview_html(self):
        for wizard in self:
            wizard.preview_html = wizard._build_preview_html()
            wizard.conflict_html = wizard._build_conflict_html()

    def _management_env(self):
        context = dict(self.env.context)
        if self.company_id:
            context.update({
                "allowed_company_ids": [self.company_id.id],
                "company_id": self.company_id.id,
            })
        return self.env["access.guard.management"].with_context(context)

    def _effective_rules(self):
        self.ensure_one()
        if not self.user_id:
            return {}
        focused_rules = self.rule_ids if self.rule_ids else None
        return self._management_env()._get_user_model_rules(
            self.user_id,
            rule_ids=focused_rules,
        )

    def _applicable_rules(self):
        self.ensure_one()
        if not self.user_id:
            return self.env["access.guard.management"]
        management = self._management_env().sudo()
        domain = [("active", "=", True)]
        if self.rule_ids:
            domain.append(("id", "in", self.rule_ids.ids))
        return management.search(domain).filtered(
            lambda rule: rule._applies_to_user(self.user_id)
        )

    @staticmethod
    def _list_html(items):
        if not items:
            return "<p>No entries.</p>"
        return "<ul>%s</ul>" % "".join("<li>%s</li>" % item for item in items)

    def _build_preview_html(self):
        self.ensure_one()
        if not self.user_id:
            return "<p>Select a user.</p>"
        rules = self._effective_rules()
        applicable = self._applicable_rules()
        hidden_menus = applicable.mapped("menu_line_ids.menu_id")
        lines = [
            "<strong>User:</strong> %s" % escape(self.user_id.display_name),
            "<strong>Company:</strong> %s" % escape(self.company_id.display_name if self.company_id else _("Current context")),
            "<strong>Active rules:</strong> %s" % (", ".join(escape(name) for name in applicable.mapped("name")) or _("None")),
            "<strong>Hidden menus:</strong> %s" % len(hidden_menus),
        ]
        model_lines = []
        for model_name in sorted(name for name in rules if name != "__global__"):
            entry = rules.get(model_name) or {}
            badges = []
            for label, key in (
                ("hide create", "hide_create"),
                ("hide edit", "hide_edit"),
                ("hide delete", "hide_delete"),
                ("hide import", "hide_import"),
                ("hide export", "hide_export"),
            ):
                if entry.get(key):
                    badges.append(label)
            if entry.get("domain_rules"):
                badges.append(_("%s domain rule(s)") % len(entry["domain_rules"]))
            if entry.get("fields"):
                badges.append(_("%s field rule(s)") % len(entry["fields"]))
            if entry.get("buttons"):
                badges.append(_("%s button(s)") % len(entry["buttons"]))
            if entry.get("tabs"):
                badges.append(_("%s tab(s)") % len(entry["tabs"]))
            model_lines.append(
                "<strong>%s</strong>: %s"
                % (escape(model_name), escape(", ".join(badges) if badges else _("rule entry")))
            )
        content = "<p>%s</p>%s<h4>Model Effects</h4>%s" % (
            "<br/>".join(lines),
            "<h4>Hidden Menus</h4>%s" % self._list_html([escape(menu.display_name) for menu in hidden_menus[:25]]),
            self._list_html(model_lines),
        )
        return content

    def _build_conflict_html(self):
        self.ensure_one()
        warnings = []
        applicable = self._applicable_rules()
        for rule in applicable:
            for warning in rule._get_rule_warning_messages():
                warnings.append("<strong>%s</strong>: %s" % (escape(rule.name), escape(warning)))
        rules = self._effective_rules()
        for model_name, entry in sorted(rules.items()):
            if model_name == "__global__":
                continue
            domain_rules = entry.get("domain_rules") or []
            if len(domain_rules) > 1:
                warnings.append(
                    _("<strong>%s</strong>: multiple domain rules are active; preview the resulting access with a real record.") % escape(model_name)
                )
            if domain_rules and not entry.get("hide_edit"):
                if any(not spec.get("soft_restrict") and not spec.get("allow_write", True) for spec in domain_rules):
                    warnings.append(
                        _("<strong>%s</strong>: backend write is blocked but Edit is not hidden.") % escape(model_name)
                    )
            if domain_rules and not entry.get("hide_create"):
                if any(not spec.get("soft_restrict") and not spec.get("allow_create", True) for spec in domain_rules):
                    warnings.append(
                        _("<strong>%s</strong>: backend create is blocked but Create is not hidden.") % escape(model_name)
                    )
        if not warnings:
            return "<p>No obvious conflicts found.</p>"
        return self._list_html(warnings)
