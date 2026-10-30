import ast
import copy
import hashlib
import json

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


PORTABILITY_LEVELS = {
    "ready": 0,
    "warnings": 1,
    "mapping_required": 2,
    "blocked": 3,
}
REPORT_CATEGORIES = (
    "models",
    "fields",
    "menus",
    "views",
    "actions",
    "groups",
    "view_modes",
    "search_items",
    "ui_elements",
    "operation_types",
    "domains",
    "acls",
)


class AccessGuardTemplatePortability(models.Model):
    _inherit = "access.guard.template"

    portable_payload = fields.Text(readonly=True, copy=False)
    portable_hash = fields.Char(readonly=True, copy=False, index=True)
    portable_valid = fields.Boolean(compute="_compute_portable_valid")
    portability_status = fields.Selection(
        [
            ("ready", "Ready"),
            ("warnings", "Warnings"),
            ("mapping_required", "Mapping Required"),
            ("blocked", "Blocked"),
        ],
        readonly=True,
        copy=False,
    )
    portability_report = fields.Text(readonly=True, copy=False)

    @api.model
    def _new_portability_analysis(self):
        return {
            "categories": {
                category: {
                    "total": 0,
                    "ready": 0,
                    "warnings": 0,
                    "mapping_required": 0,
                    "blocked": 0,
                }
                for category in REPORT_CATEGORIES
            },
            "problems": [],
            "required_modules": {"access_guard"},
        }

    @api.model
    def _record_portability(self, analysis, category, status="ready", message=None):
        status = status if status in PORTABILITY_LEVELS else "blocked"
        counts = analysis["categories"][category]
        counts["total"] += 1
        counts[status] += 1
        if message and message not in analysis["problems"]:
            analysis["problems"].append(message)

    @api.model
    def _highest_portability_status(self, current, candidate):
        current = current if current in PORTABILITY_LEVELS else "blocked"
        candidate = candidate if candidate in PORTABILITY_LEVELS else "blocked"
        if PORTABILITY_LEVELS[candidate] > PORTABILITY_LEVELS[current]:
            return candidate
        return current

    @api.model
    def _add_xml_dependency(self, analysis, xml_id):
        if xml_id and "." in xml_id:
            analysis["required_modules"].add(xml_id.split(".", 1)[0])

    @api.model
    def _portable_model_ref(self, reference, analysis):
        model_name = (reference or {}).get("model")
        if not model_name:
            self._record_portability(
                analysis, "models", "blocked", "A model reference has no technical model name."
            )
            return {"model": None, "portability": "blocked"}
        self._add_xml_dependency(analysis, reference.get("xml_id"))
        self._record_portability(analysis, "models")
        return {"model": model_name}

    @api.model
    def _portable_field_ref(self, reference, analysis):
        model_name = (reference or {}).get("model")
        field_name = (reference or {}).get("name")
        if not model_name or not field_name:
            self._record_portability(
                analysis, "fields", "blocked", "A field reference has no model or technical field name."
            )
            return {"model": model_name, "field": field_name, "portability": "blocked"}
        self._add_xml_dependency(analysis, reference.get("xml_id"))
        self._record_portability(analysis, "fields")
        return {"model": model_name, "field": field_name}

    @api.model
    def _portable_xml_ref(self, reference, analysis, category, label, fallback_keys):
        reference = reference or {}
        xml_id = reference.get("xml_id")
        if xml_id:
            self._add_xml_dependency(analysis, xml_id)
            self._record_portability(analysis, category)
            return {"xml_id": xml_id}
        fallback = {"xml_id": None}
        for key in fallback_keys:
            value = reference.get(key)
            if value not in (None, "", False):
                fallback[key] = value
        fallback["portability"] = "mapping_required"
        name = reference.get("name") or label
        self._record_portability(
            analysis,
            category,
            "mapping_required",
            '%s "%s" has no XML ID and requires target mapping.' % (label, name),
        )
        return fallback

    @api.model
    def _portable_menu_ref(self, reference, analysis):
        result = self._portable_xml_ref(
            reference,
            analysis,
            "menus",
            "Menu",
            ("name", "parent_xml_id"),
        )
        self._add_xml_dependency(analysis, (reference or {}).get("parent_xml_id"))
        return result

    @api.model
    def _portable_view_ref(self, reference, analysis):
        return self._portable_xml_ref(
            reference,
            analysis,
            "views",
            "View",
            ("model", "type", "name"),
        )

    @api.model
    def _portable_action_ref(self, reference, analysis, action_type):
        result = self._portable_xml_ref(
            reference,
            analysis,
            "actions",
            "Action",
            (
                "name",
                "model",
                "res_model",
                "report_name",
                "report_type",
                "state",
                "binding_model",
                "binding_type",
                "view_mode",
            ),
        )
        result["action_type"] = action_type
        return result

    @api.model
    def _portable_view_mode_ref(self, reference, analysis):
        code = (reference or {}).get("code")
        if not code:
            self._record_portability(
                analysis, "view_modes", "blocked", "A view mode reference has no unique code."
            )
            return {"code": None, "portability": "blocked"}
        self._record_portability(analysis, "view_modes")
        return {"code": code}

    @api.model
    def _portable_search_item_ref(self, reference, analysis):
        model_name = ((reference or {}).get("model") or {}).get("model")
        field = (reference or {}).get("field") or {}
        field_model = field.get("model") or model_name
        field_name = field.get("name")
        item_type = (reference or {}).get("type")
        if not field_model or not field_name or not item_type:
            self._record_portability(
                analysis,
                "search_items",
                "blocked",
                "A search item has no model, field, or item type.",
            )
            return {
                "model": field_model,
                "field": field_name,
                "type": item_type,
                "portability": "blocked",
            }
        self._add_xml_dependency(analysis, field.get("xml_id"))
        self._record_portability(analysis, "search_items")
        return {"model": field_model, "field": field_name, "type": item_type}

    @api.model
    def _resolve_action_id(self, element):
        try:
            action_id = int(element.get("match_value") or 0)
        except (TypeError, ValueError):
            return None, None
        preferred = {
            "button": ("ir.actions.server", "ir.actions.act_window"),
            "action_button": ("ir.actions.act_window", "ir.actions.server"),
        }.get(element.get("type"), ("ir.actions.act_window", "ir.actions.server"))
        for model_name in preferred:
            action = self.env[model_name].sudo().browse(action_id).exists()
            if action:
                return action, model_name
        return None, None

    @api.model
    def _portable_ui_element_ref(self, element, analysis):
        element = element or {}
        model_name = (element.get("model") or {}).get("model")
        result = {
            "model": model_name,
            "type": element.get("type") or "",
            "view_type": element.get("view_type") or "",
            "match_mode": element.get("match_mode") or "",
            "attribute_name": element.get("attribute_name") or "",
            "match_value": element.get("match_value") or "",
            "view_xml_id": None,
            "label": element.get("label") or "",
            "active": bool(element.get("active")),
        }
        messages = []
        status = "ready"
        if not model_name or not result["type"] or not result["match_mode"]:
            status = "blocked"
            messages.append("A UI element is missing its model, type, or match mode.")

        view = element.get("view")
        if view:
            portable_view = self._portable_view_ref(view, analysis)
            result["view_xml_id"] = portable_view.get("xml_id")
            if portable_view.get("portability"):
                status = self._highest_portability_status(status, "mapping_required")
                result["view_fallback"] = {
                    key: value
                    for key, value in portable_view.items()
                    if key not in ("xml_id", "portability")
                }

        match_mode = result["match_mode"]
        if match_mode == "action_id":
            action, action_type = self._resolve_action_id(element)
            if not action:
                status = "blocked"
                result["match_value"] = None
                messages.append('UI action "%s" does not exist in the source database.' % result["label"])
            else:
                action_ref = {
                    "xml_id": self.env["access.guard.management"]._access_guard_external_id(action),
                    "name": action.with_context(lang=False).name or "",
                }
                if action_type == "ir.actions.server":
                    action_ref.update({
                        "model": action.model_id.model if action.model_id else "",
                        "state": action.state or "",
                    })
                else:
                    action_ref.update({"res_model": action.res_model or ""})
                portable_action = self._portable_action_ref(
                    action_ref, analysis, action_type
                )
                result["match_value"] = portable_action.get("xml_id")
                result["action"] = portable_action
                if portable_action.get("portability"):
                    status = self._highest_portability_status(status, "mapping_required")
                    messages.append(
                        'UI action "%s" has no XML ID and requires target mapping.' % result["label"]
                    )
        elif match_mode == "string":
            status = self._highest_portability_status(status, "warnings")
            messages.append('UI element "%s" uses language-sensitive string matching.' % result["label"])
        elif match_mode == "xpath":
            status = self._highest_portability_status(status, "warnings")
            messages.append('UI element "%s" uses a structurally fragile XPath.' % result["label"])

        if messages:
            result["portability"] = {"status": status, "messages": messages}
        self._record_portability(analysis, "ui_elements", status)
        for message in messages:
            if message not in analysis["problems"]:
                analysis["problems"].append(message)
        return result

    @api.model
    def _portable_picking_type_ref(self, reference, analysis):
        reference = reference or {}
        xml_id = reference.get("xml_id")
        if xml_id:
            self._add_xml_dependency(analysis, xml_id)
            self._record_portability(analysis, "operation_types")
            return {"xml_id": xml_id}
        warehouse = reference.get("warehouse") or {}
        company = reference.get("company") or {}
        result = {
            "xml_id": None,
            "code": reference.get("code") or "",
            "sequence_code": reference.get("sequence_code") or "",
            "warehouse": {
                "code": warehouse.get("code") or "",
                "name": warehouse.get("name") or "",
            },
            "company": {"name": company.get("name") or ""},
            "name": reference.get("name") or "",
        }
        if result["code"] and result["warehouse"]["code"]:
            status = "warnings"
            message = (
                'Operation type "%s" has no XML ID; warehouse code "%s" and operation code "%s" '
                "will be used as fallback identity."
                % (result["name"], result["warehouse"]["code"], result["code"])
            )
        else:
            status = "mapping_required"
            message = 'Operation type "%s" has no deterministic portable identity.' % result["name"]
        result["portability"] = status
        self._record_portability(analysis, "operation_types", status, message)
        return result

    @api.model
    def _domain_field_metadata(self, model_name, field_path):
        current_model = model_name
        field = self.env["ir.model.fields"]
        for part in (field_path or "").split("."):
            if not current_model or not part:
                return field
            field = self.env["ir.model.fields"].sudo().search([
                ("model", "=", current_model),
                ("name", "=", part),
            ], limit=1)
            if not field:
                return field
            current_model = field.relation if field.ttype in ("many2one", "one2many", "many2many") else None
        return field

    @api.model
    def _numeric_ast_values(self, node):
        values = []
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, (int, float)):
                if not isinstance(child.value, bool):
                    values.append(child.value)
        return values

    @api.model
    def _analyze_domain_portability(self, expression, model_name, analysis=None, label="Domain"):
        analysis = analysis or self._new_portability_analysis()
        if not expression:
            result = {"status": "ready", "messages": []}
            self._record_portability(analysis, "domains")
            return result
        try:
            tree = ast.parse(expression, mode="eval")
        except (SyntaxError, TypeError, ValueError):
            message = "%s on %s is not valid Python domain syntax." % (label, model_name or "unknown model")
            self._record_portability(analysis, "domains", "blocked", message)
            return {"status": "blocked", "messages": [message]}

        messages = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) < 3:
                continue
            field_node = node.elts[0]
            if not isinstance(field_node, ast.Constant) or not isinstance(field_node.value, str):
                continue
            numeric_values = self._numeric_ast_values(node.elts[2])
            if not numeric_values:
                continue
            field_name = field_node.value
            field = self._domain_field_metadata(model_name, field_name)
            relational = field_name == "id" or (
                field and field.ttype in ("many2one", "one2many", "many2many", "reference", "many2one_reference")
            )
            unknown_id_like = not field and (field_name == "id" or field_name.endswith("_id"))
            if relational or unknown_id_like:
                message = (
                    "%s on %s contains numeric value(s) %s for relational field %s; "
                    "cross-database mapping may be required."
                    % (label, model_name or "unknown model", numeric_values, field_name)
                )
                if message not in messages:
                    messages.append(message)
        status = "warnings" if messages else "ready"
        self._record_portability(analysis, "domains", status)
        for message in messages:
            if message not in analysis["problems"]:
                analysis["problems"].append(message)
        return {"status": status, "messages": messages}

    @api.model
    def _overall_portability_status(self, analysis):
        highest = "ready"
        for counts in analysis["categories"].values():
            for status in PORTABILITY_LEVELS:
                if counts[status] and PORTABILITY_LEVELS[status] > PORTABILITY_LEVELS[highest]:
                    highest = status
        return highest

    def _build_portable_payload(self):
        self.ensure_one()
        snapshot = self._snapshot_payload_dict()
        self._validate_snapshot_data(snapshot, self.template_code)
        analysis = self._new_portability_analysis()

        def convert_many(values, converter):
            converted = [converter(value, analysis) for value in values or []]
            return sorted(
                converted,
                key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            )

        payload = {
            "schema_version": snapshot["schema_version"],
            "template": {
                "code": self.template_code,
                "name": self.name,
                "version": self.template_version,
                "category": self.category,
                "source_module_version": self.source_module_version,
            },
            "dependencies": {"required_modules": []},
            "general": copy.deepcopy(snapshot["general"]),
            "menus": [],
            "model_rules": [],
            "field_rules": [],
            "conditional_rules": [],
            "domain_rules": [],
            "ui_rules": [],
            "search_rules": [],
            "chatter_rules": [],
            "global": copy.deepcopy(snapshot["global"]),
            "overview": {
                "mode": snapshot["overview"].get("mode"),
                "picking_types": convert_many(
                    snapshot["overview"].get("picking_types"), self._portable_picking_type_ref
                ),
            },
            "acl_rules": [],
        }

        for line in snapshot["menus"]:
            payload["menus"].append({
                "sequence": line.get("sequence", 10),
                "menu": self._portable_menu_ref(line.get("menu"), analysis),
                "include_children": bool(line.get("include_children")),
            })

        for line in snapshot["model_rules"]:
            values = {
                key: copy.deepcopy(value)
                for key, value in line.items()
                if key not in ("model", "reports", "server_actions", "window_actions", "hidden_view_modes", "views")
            }
            values.update({
                "model": self._portable_model_ref(line.get("model"), analysis),
                "reports": convert_many(
                    line.get("reports"), lambda ref, report: self._portable_action_ref(ref, report, "ir.actions.report")
                ),
                "server_actions": convert_many(
                    line.get("server_actions"), lambda ref, report: self._portable_action_ref(ref, report, "ir.actions.server")
                ),
                "window_actions": convert_many(
                    line.get("window_actions"), lambda ref, report: self._portable_action_ref(ref, report, "ir.actions.act_window")
                ),
                "hidden_view_modes": convert_many(line.get("hidden_view_modes"), self._portable_view_mode_ref),
                "views": convert_many(line.get("views"), self._portable_view_ref),
            })
            payload["model_rules"].append(values)

        for line in snapshot["field_rules"]:
            values = {key: copy.deepcopy(value) for key, value in line.items() if key not in ("model", "fields")}
            values.update({
                "model": self._portable_model_ref(line.get("model"), analysis),
                "fields": convert_many(line.get("fields"), self._portable_field_ref),
            })
            payload["field_rules"].append(values)

        for line in snapshot["conditional_rules"]:
            model = self._portable_model_ref(line.get("model"), analysis)
            model_name = model.get("model")
            values = {
                key: copy.deepcopy(value)
                for key, value in line.items()
                if key not in ("model", "attr_fields", "attr_buttons", "domain_field")
            }
            values.update({
                "model": model,
                "attr_fields": convert_many(line.get("attr_fields"), self._portable_field_ref),
                "attr_buttons": convert_many(line.get("attr_buttons"), self._portable_ui_element_ref),
                "domain_field": self._portable_field_ref(line["domain_field"], analysis)
                if line.get("domain_field") else None,
                "attrs_domain_portability": self._analyze_domain_portability(
                    line.get("attrs_domain"), model_name, analysis, "Conditional attrs domain"
                ),
                "domain_expression_portability": self._analyze_domain_portability(
                    line.get("domain_expression"), model_name, analysis, "Conditional field domain"
                ),
            })
            payload["conditional_rules"].append(values)

        for line in snapshot["domain_rules"]:
            model = self._portable_model_ref(line.get("model"), analysis)
            values = {key: copy.deepcopy(value) for key, value in line.items() if key != "model"}
            values.update({
                "model": model,
                "domain_portability": self._analyze_domain_portability(
                    line.get("domain_expression"), model.get("model"), analysis, "Domain rule"
                ),
            })
            payload["domain_rules"].append(values)

        for line in snapshot["ui_rules"]:
            values = {
                "sequence": line.get("sequence", 10),
                "model": self._portable_model_ref(line.get("model"), analysis),
            }
            for key in (
                "button_elements", "action_button_elements", "smart_button_elements",
                "tab_elements", "kanban_link_elements",
            ):
                values[key] = convert_many(line.get(key), self._portable_ui_element_ref)
            payload["ui_rules"].append(values)

        for line in snapshot["search_rules"]:
            values = {
                key: copy.deepcopy(value)
                for key, value in line.items()
                if key not in ("model", "filter_items", "group_items")
            }
            values.update({
                "model": self._portable_model_ref(line.get("model"), analysis),
                "filter_items": convert_many(line.get("filter_items"), self._portable_search_item_ref),
                "group_items": convert_many(line.get("group_items"), self._portable_search_item_ref),
            })
            payload["search_rules"].append(values)

        for line in snapshot["chatter_rules"]:
            values = {key: copy.deepcopy(value) for key, value in line.items() if key != "model"}
            values["model"] = self._portable_model_ref(line.get("model"), analysis)
            payload["chatter_rules"].append(values)

        for line in snapshot["acl_rules"]:
            model = self._portable_model_ref(line.get("model"), analysis)
            group = line.get("group") or {}
            group_ref = self._portable_xml_ref(
                group, analysis, "groups", "ACL group", ("name",)
            )
            values = {
                "model": model.get("model"),
                "group_xml_id": group_ref.get("xml_id"),
                "perm_read": bool(line.get("perm_read")),
                "perm_write": bool(line.get("perm_write")),
                "perm_create": bool(line.get("perm_create")),
                "perm_unlink": bool(line.get("perm_unlink")),
            }
            acl_status = "ready"
            if group_ref.get("portability"):
                acl_status = "mapping_required"
                values["group_fallback"] = {"name": group.get("name") or ""}
                values["portability"] = acl_status
            self._record_portability(analysis, "acls", acl_status)
            payload["acl_rules"].append(values)

        payload["dependencies"]["required_modules"] = sorted(analysis["required_modules"])
        return payload, analysis

    @api.model
    def _validate_portable_data(self, payload):
        expected = {
            "schema_version", "template", "dependencies", "general", "menus",
            "model_rules", "field_rules", "conditional_rules", "domain_rules",
            "ui_rules", "search_rules", "chatter_rules", "global", "overview", "acl_rules",
        }
        if not isinstance(payload, dict) or not expected.issubset(payload):
            raise ValidationError(_("Portable payload is missing required sections."))
        identity = payload.get("template")
        if not isinstance(identity, dict) or not all(
            isinstance(identity.get(key), str) and identity.get(key)
            for key in ("code", "name", "version")
        ):
            raise ValidationError(_("Portable template identity is invalid."))
        category_values = dict(self._fields["category"].selection)
        if identity.get("category") not in category_values:
            raise ValidationError(_("Portable template category is invalid."))
        for section in (
            "menus", "model_rules", "field_rules", "conditional_rules", "domain_rules",
            "ui_rules", "search_rules", "chatter_rules", "acl_rules",
        ):
            if not isinstance(payload.get(section), list) or not all(
                isinstance(line, dict) for line in payload[section]
            ):
                raise ValidationError(_("Portable section %s must be a list of objects.") % section)
        if not isinstance(payload.get("general"), dict) or not isinstance(payload.get("global"), dict):
            raise ValidationError(_("Portable general/global sections are invalid."))
        overview = payload.get("overview")
        if not isinstance(overview, dict) or overview.get("mode") not in (False, None, "hide", "show"):
            raise ValidationError(_("Portable overview section is invalid."))
        if not isinstance(overview.get("picking_types"), list):
            raise ValidationError(_("Portable overview operation types must be a list."))
        modules = (payload.get("dependencies") or {}).get("required_modules")
        if not isinstance(modules, list) or modules != sorted(set(modules)) or "access_guard" not in modules:
            raise ValidationError(_("Portable dependency list is invalid."))

        forbidden = {"id", "access_id", "user_ids", "company_ids", "group_ids"}

        def inspect(value):
            if isinstance(value, dict):
                bad_keys = forbidden.intersection(value)
                if bad_keys:
                    raise ValidationError(
                        _("Portable payload contains local assignment/ID keys: %s")
                        % ", ".join(sorted(bad_keys))
                    )
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        inspect(payload)
        self._canonical_snapshot_json(payload)
        return True

    @api.model
    def _portability_report_text(self, analysis, status):
        labels = {
            "models": "Models",
            "fields": "Fields",
            "menus": "Menus",
            "views": "Views",
            "actions": "Actions",
            "groups": "Groups",
            "view_modes": "View Modes",
            "search_items": "Search Items",
            "ui_elements": "UI Elements",
            "operation_types": "Operation Types",
            "domains": "Domains",
            "acls": "ACLs",
        }
        lines = ["Portable Template Validation", ""]
        total_references = sum(item["total"] for item in analysis["categories"].values())
        ready_references = sum(item["ready"] for item in analysis["categories"].values())
        warnings = sum(item["warnings"] for item in analysis["categories"].values())
        mapping = sum(item["mapping_required"] for item in analysis["categories"].values())
        blocked = sum(item["blocked"] for item in analysis["categories"].values())
        lines.extend([
            "Total references: %s" % total_references,
            "Portable references: %s" % ready_references,
            "Warnings: %s" % warnings,
            "Mapping required: %s" % mapping,
            "Blocked: %s" % blocked,
            "",
        ])
        for category in REPORT_CATEGORIES:
            counts = analysis["categories"][category]
            lines.append(
                "%s: %s/%s ready, %s warnings, %s mapping required, %s blocked"
                % (
                    labels[category], counts["ready"], counts["total"], counts["warnings"],
                    counts["mapping_required"], counts["blocked"],
                )
            )
        lines.extend(["", "Status: %s" % status.replace("_", " ").title(), "", "Problems:"])
        if analysis["problems"]:
            lines.extend("- %s" % problem for problem in analysis["problems"])
        else:
            lines.append("- None")
        return "\n".join(lines)

    def _refresh_portable_payload(self):
        for template in self:
            try:
                payload, analysis = template._build_portable_payload()
                template._validate_portable_data(payload)
                status = template._overall_portability_status(analysis)
                canonical = template._canonical_snapshot_json(payload)
                values = {
                    "portable_payload": template._pretty_snapshot_json(payload),
                    "portable_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "portability_status": status,
                    "portability_report": template._portability_report_text(analysis, status),
                }
            except (ValidationError, ValueError, TypeError) as error:
                values = {
                    "portable_payload": False,
                    "portable_hash": False,
                    "portability_status": "blocked",
                    "portability_report": "Portable Template Validation\n\nStatus: Blocked\n\nProblems:\n- %s" % error,
                }
            template.with_context(access_guard_skip_portable_refresh=True).write(values)
        return True

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.filtered("snapshot_payload")._refresh_portable_payload()
        return records

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get("access_guard_skip_portable_refresh") and any(
            key in vals for key in ("template_code", "template_version", "name", "category")
        ):
            self.filtered("snapshot_payload")._refresh_portable_payload()
        return result

    def action_refresh_snapshot(self):
        result = super().action_refresh_snapshot()
        self._refresh_portable_payload()
        return result

    def _portable_payload_dict(self):
        self.ensure_one()
        if not self.portable_payload:
            return {}
        try:
            return json.loads(self.portable_payload)
        except (TypeError, ValueError):
            return {}

    @api.depends("portable_payload", "portable_hash", "portability_status")
    def _compute_portable_valid(self):
        for template in self:
            try:
                payload = template._portable_payload_dict()
                template._validate_portable_data(payload)
                template.portable_valid = template.portability_status != "blocked"
            except (ValidationError, TypeError, ValueError):
                template.portable_valid = False

    def action_view_portable_payload(self):
        self.ensure_one()
        if not self.portable_payload:
            raise UserError(_("This template has no portable payload. Refresh Snapshot first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Portable Template Payload"),
            "res_model": "access.guard.template",
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("npt_access_guard.view_access_guard_template_portable").id,
            "target": "new",
        }

    def action_view_portability_report(self):
        self.ensure_one()
        if not self.portability_report:
            raise UserError(_("This template has no portability report. Refresh Snapshot first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Template Portability Report"),
            "res_model": "access.guard.template",
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("npt_access_guard.view_access_guard_template_portability_report").id,
            "target": "new",
        }
