import ast
import base64
import binascii
import hashlib
import json
import re

from markupsafe import Markup, escape

from odoo import api, fields, models, release, _
from odoo.exceptions import AccessError, UserError, ValidationError


IMPORT_FORMAT_VERSION = "1.0"
IMPORT_SCHEMA_VERSION = "1.0"
MAX_IMPORT_BYTES = 5 * 1024 * 1024

IMPORT_CATEGORIES = (
    "models",
    "fields",
    "menus",
    "views",
    "actions",
    "groups",
    "ui_elements",
    "search_items",
    "view_modes",
    "operation_types",
    "domains",
    "acls",
)

STATUS_BUCKETS = {
    "resolved": "ready",
    "resolved_with_fallback": "ready",
    "resolved_manually": "ready",
    "resolvable_on_import": "ready",
    "warning": "warnings",
    "mapping_required": "mapping_required",
    "ambiguous": "mapping_required",
    "missing": "blocked",
    "blocked": "blocked",
}

KNOWN_VIEW_MODES = {
    "activity", "calendar", "cohort", "form", "gantt", "graph", "grid",
    "hierarchy", "kanban", "list", "map", "pivot",
}

ACTION_MODELS = {
    "ir.actions.act_window",
    "ir.actions.report",
    "ir.actions.server",
}


class AccessGuardImportResolver(models.AbstractModel):
    _name = "access.guard.import.resolver"
    _description = "Access Guard Import Reference Resolver"

    @api.model
    def _check_resolver_access(self):
        if not self.env.user.has_group("npt_access_guard.group_access_guard_manager"):
            raise AccessError(_("Only Access Guard Managers can resolve template imports."))

    @api.model
    def _new_analysis(self):
        return {
            "categories": {
                category: {
                    "total": 0,
                    "ready": 0,
                    "warnings": 0,
                    "mapping_required": 0,
                    "blocked": 0,
                }
                for category in IMPORT_CATEGORIES
            },
            "results": [],
            "messages": [],
        }

    @api.model
    def _result(
        self,
        analysis,
        category,
        path,
        reference_type,
        source_reference,
        status,
        message,
        target_model=None,
        target_id=None,
        source_label=None,
        candidate_values=None,
    ):
        status = status if status in STATUS_BUCKETS else "blocked"
        bucket = STATUS_BUCKETS[status]
        counts = analysis["categories"][category]
        counts["total"] += 1
        counts[bucket] += 1
        result = {
            "category": category,
            "path": path,
            "reference_type": reference_type,
            "source_label": source_label or self._source_label(source_reference),
            "source_reference": source_reference,
            "status": status,
            "target_model": target_model,
            "target_id": target_id,
            "message": message,
        }
        if candidate_values:
            result["candidate_values"] = candidate_values
        analysis["results"].append(result)
        if bucket != "ready" or status == "resolved_with_fallback":
            analysis["messages"].append(message)
        return result

    @api.model
    def _source_label(self, reference):
        if isinstance(reference, dict):
            return (
                reference.get("label")
                or reference.get("name")
                or reference.get("xml_id")
                or reference.get("field")
                or reference.get("model")
                or reference.get("code")
                or "Reference"
            )
        return str(reference or "Reference")

    @api.model
    def _manual_result(
        self,
        analysis,
        mappings,
        category,
        path,
        reference_type,
        source_reference,
        target_model,
    ):
        mapping = (mappings or {}).get(path)
        if not mapping or not mapping.get("target_id"):
            return None
        mapped_model = mapping.get("target_model") or target_model
        if not mapped_model or mapped_model not in self.env:
            return None
        record = self.env[mapped_model].sudo().browse(mapping["target_id"]).exists()
        if not record:
            return None
        expected_models = set(target_model) if isinstance(target_model, (list, tuple, set)) else {target_model}
        if mapped_model not in expected_models and target_model != "ir.actions.actions":
            return None
        if target_model == "ir.actions.actions" and mapped_model not in ACTION_MODELS | {"ir.actions.actions"}:
            return None
        return self._result(
            analysis,
            category,
            path,
            reference_type,
            source_reference,
            "resolved_manually",
            _("Mapped manually to %(model)s(%(record_id)s).", model=mapped_model, record_id=record.id),
            mapped_model,
            record.id,
        )

    @api.model
    def _model_name(self, reference):
        if isinstance(reference, dict):
            return reference.get("model")
        return reference

    @api.model
    def _find_model(self, model_name):
        return self.env["ir.model"].sudo().search([("model", "=", model_name)], limit=1)

    @api.model
    def resolve_model(self, analysis, reference, path):
        self._check_resolver_access()
        model_name = self._model_name(reference)
        model = self._find_model(model_name) if model_name else self.env["ir.model"]
        if not model:
            return self._result(
                analysis, "models", path, "model", reference, "blocked",
                _("Model %(model)s is not available. Its providing module may be missing.", model=model_name or "<missing>"),
                "ir.model", source_label=model_name,
            )
        return self._result(
            analysis, "models", path, "model", reference, "resolved",
            _("Model %(model)s resolved.", model=model_name),
            "ir.model", model.id, model_name,
        )

    @api.model
    def _find_field(self, model_name, field_name):
        return self.env["ir.model.fields"].sudo().search([
            ("model", "=", model_name),
            ("name", "=", field_name),
        ], limit=1)

    @api.model
    def resolve_field(self, analysis, reference, path):
        self._check_resolver_access()
        reference = reference or {}
        model_name = reference.get("model")
        field_name = reference.get("field") or reference.get("name")
        field = self._find_field(model_name, field_name) if model_name and field_name else self.env["ir.model.fields"]
        label = "%s.%s" % (model_name or "?", field_name or "?")
        if not field:
            return self._result(
                analysis, "fields", path, "field", reference, "blocked",
                _("Field %(field)s is not available. Its model or providing module may be missing.", field=label),
                "ir.model.fields", source_label=label,
            )
        return self._result(
            analysis, "fields", path, "field", reference, "resolved",
            _("Field %(field)s resolved.", field=label),
            "ir.model.fields", field.id, label,
        )

    @api.model
    def _resolve_xml_id(self, xml_id):
        if not isinstance(xml_id, str) or "." not in xml_id:
            return self.env["ir.model.data"], None
        try:
            record = self.env.ref(xml_id, raise_if_not_found=False)
        except (ValueError, TypeError):
            record = None
        return record, record._name if record else None

    @api.model
    def resolve_xml_reference(
        self,
        analysis,
        reference,
        path,
        category,
        reference_type,
        target_model,
        mappings=None,
        fallback_available=False,
    ):
        self._check_resolver_access()
        manual = self._manual_result(
            analysis, mappings, category, path, reference_type, reference, target_model
        )
        if manual:
            return manual
        reference = reference or {}
        xml_id = reference.get("xml_id")
        record, actual_model = self._resolve_xml_id(xml_id)
        expected = set(target_model) if isinstance(target_model, (list, tuple, set)) else {target_model}
        if record and (
            actual_model in expected
            or target_model == "ir.actions.actions" and actual_model in ACTION_MODELS
        ):
            return self._result(
                analysis, category, path, reference_type, reference, "resolved",
                _("%(type)s %(xml_id)s resolved.", type=reference_type.replace("_", " ").title(), xml_id=xml_id),
                actual_model, record.id,
            )
        if record:
            return self._result(
                analysis, category, path, reference_type, reference, "blocked",
                _("XML ID %(xml_id)s points to %(actual)s, not the expected %(expected)s.",
                  xml_id=xml_id, actual=actual_model, expected=", ".join(sorted(expected))),
                target_model,
            )
        if fallback_available:
            return self._result(
                analysis, category, path, reference_type, reference, "mapping_required",
                _("XML ID %(xml_id)s was not found. Select a target %(type)s.",
                  xml_id=xml_id or "<missing>", type=reference_type.replace("_", " ")),
                target_model,
            )
        return self._result(
            analysis, category, path, reference_type, reference, "blocked",
            _("Required XML ID %(xml_id)s was not found and has no safe fallback.", xml_id=xml_id or "<missing>"),
            target_model,
        )

    @api.model
    def resolve_view_mode(self, analysis, reference, path):
        self._check_resolver_access()
        reference = reference or {}
        code = reference.get("code")
        if not code:
            return self._result(
                analysis, "view_modes", path, "view_mode", reference, "blocked",
                _("View mode code is missing."), "access.guard.view.mode",
            )
        modes = self.env["access.guard.view.mode"].sudo().search([("code", "=", code)])
        if len(modes) == 1:
            return self._result(
                analysis, "view_modes", path, "view_mode", reference, "resolved",
                _("View mode %(code)s resolved.", code=code),
                "access.guard.view.mode", modes.id, code,
            )
        if len(modes) > 1:
            return self._result(
                analysis, "view_modes", path, "view_mode", reference, "ambiguous",
                _("More than one view-mode helper uses code %(code)s.", code=code),
                "access.guard.view.mode", source_label=code,
            )
        if code in KNOWN_VIEW_MODES:
            return self._result(
                analysis, "view_modes", path, "view_mode", reference, "resolvable_on_import",
                _("View-mode helper %(code)s can be recreated during a future import.", code=code),
                "access.guard.view.mode", source_label=code,
                candidate_values={"code": code},
            )
        return self._result(
            analysis, "view_modes", path, "view_mode", reference, "blocked",
            _("Unknown view mode code %(code)s.", code=code),
            "access.guard.view.mode", source_label=code,
        )

    @api.model
    def resolve_search_item(self, analysis, reference, path):
        self._check_resolver_access()
        reference = reference or {}
        model_name = reference.get("model")
        field_name = reference.get("field")
        item_type = reference.get("type")
        model = self._find_model(model_name) if model_name else self.env["ir.model"]
        field = self._find_field(model_name, field_name) if model_name and field_name else self.env["ir.model.fields"]
        label = "%s.%s (%s)" % (model_name or "?", field_name or "?", item_type or "?")
        if not model or not field or item_type not in ("filter", "group"):
            return self._result(
                analysis, "search_items", path, "search_item", reference, "blocked",
                _("Search item %(item)s has a missing model, field, or invalid type.", item=label),
                "access.guard.search.item", source_label=label,
            )
        items = self.env["access.guard.search.item"].sudo().with_context(active_test=False).search([
            ("model_id", "=", model.id),
            ("field_id", "=", field.id),
            ("type", "=", item_type),
        ])
        if len(items) == 1:
            return self._result(
                analysis, "search_items", path, "search_item", reference, "resolved",
                _("Search item %(item)s resolved.", item=label),
                "access.guard.search.item", items.id, label,
            )
        if len(items) > 1:
            return self._result(
                analysis, "search_items", path, "search_item", reference, "ambiguous",
                _("Search item %(item)s has multiple target candidates.", item=label),
                "access.guard.search.item", source_label=label,
            )
        return self._result(
            analysis, "search_items", path, "search_item", reference, "resolvable_on_import",
            _("Search item %(item)s can be regenerated during a future import.", item=label),
            "access.guard.search.item", source_label=label,
            candidate_values={"model_id": model.id, "field_id": field.id, "type": item_type},
        )

    @api.model
    def resolve_picking_type(self, analysis, reference, path, mappings=None):
        self._check_resolver_access()
        manual = self._manual_result(
            analysis, mappings, "operation_types", path, "picking_type", reference, "stock.picking.type"
        )
        if manual:
            return manual
        reference = reference or {}
        if reference.get("xml_id"):
            record, actual_model = self._resolve_xml_id(reference["xml_id"])
            if record and actual_model == "stock.picking.type":
                return self._result(
                    analysis, "operation_types", path, "picking_type", reference, "resolved",
                    _("Operation type %(xml_id)s resolved.", xml_id=reference["xml_id"]),
                    "stock.picking.type", record.id,
                )
            if record:
                return self._result(
                    analysis, "operation_types", path, "picking_type", reference, "blocked",
                    _("Operation type XML ID %(xml_id)s points to %(model)s.",
                      xml_id=reference["xml_id"], model=actual_model),
                    "stock.picking.type",
                )
        code = reference.get("code")
        sequence_code = reference.get("sequence_code")
        warehouse_code = (reference.get("warehouse") or {}).get("code")
        if not code or not warehouse_code:
            return self._result(
                analysis, "operation_types", path, "picking_type", reference, "mapping_required",
                _("Operation type %(name)s has no XML ID or deterministic warehouse/code fallback.",
                  name=reference.get("name") or "<unnamed>"),
                "stock.picking.type",
            )
        domain = [("code", "=", code), ("warehouse_id.code", "=", warehouse_code)]
        if sequence_code:
            domain.append(("sequence_code", "=", sequence_code))
        company_name = (reference.get("company") or {}).get("name")
        companies = self.env["res.company"].sudo().search([("name", "=", company_name)]) if company_name else self.env["res.company"]
        if len(companies) == 1:
            domain.append(("company_id", "=", companies.id))
        candidates = self.env["stock.picking.type"].sudo().search(domain)
        if len(candidates) == 1:
            return self._result(
                analysis, "operation_types", path, "picking_type", reference, "resolved_with_fallback",
                _("Operation type %(name)s resolved by warehouse %(warehouse)s and code %(code)s; review this fallback mapping.",
                  name=reference.get("name") or candidates.display_name,
                  warehouse=warehouse_code, code=code),
                "stock.picking.type", candidates.id,
            )
        if len(candidates) > 1:
            return self._result(
                analysis, "operation_types", path, "picking_type", reference, "ambiguous",
                _("Operation type fallback matched %(count)s records; select the target explicitly.", count=len(candidates)),
                "stock.picking.type",
            )
        return self._result(
            analysis, "operation_types", path, "picking_type", reference, "mapping_required",
            _("No operation type matches warehouse %(warehouse)s and code %(code)s.",
              warehouse=warehouse_code, code=code),
            "stock.picking.type",
        )

    @api.model
    def _ui_candidate_domain(self, reference, model, view, match_value):
        domain = [
            ("model_id", "=", model.id),
            ("type", "=", reference.get("type")),
            ("view_type", "=", reference.get("view_type")),
            ("match_mode", "=", reference.get("match_mode")),
            ("match_value", "=", str(match_value or "")),
            ("attribute_name", "=", reference.get("attribute_name") or ""),
        ]
        domain.append(("view_id", "=", view.id if view else False))
        return domain

    @api.model
    def resolve_ui_element(self, analysis, reference, path, mappings=None):
        self._check_resolver_access()
        manual = self._manual_result(
            analysis, mappings, "ui_elements", path, "ui_element", reference, "access.guard.ui.element"
        )
        if manual:
            return manual
        reference = reference or {}
        model_name = reference.get("model")
        model = self._find_model(model_name) if model_name else self.env["ir.model"]
        match_mode = reference.get("match_mode")
        element_type = reference.get("type")
        view_type = reference.get("view_type")
        if not model or not match_mode or not element_type or not view_type:
            return self._result(
                analysis, "ui_elements", path, "ui_element", reference, "blocked",
                _("UI element %(label)s is missing a valid model, type, view type, or match mode.",
                  label=reference.get("label") or "<unnamed>"),
                "access.guard.ui.element",
            )

        view = self.env["ir.ui.view"]
        if reference.get("view_xml_id") or reference.get("view_fallback"):
            view_result = self.resolve_xml_reference(
                analysis,
                {
                    "xml_id": reference.get("view_xml_id"),
                    **(reference.get("view_fallback") or {}),
                    "model": model_name,
                },
                "%s.view" % path,
                "views",
                "view",
                "ir.ui.view",
                mappings=mappings,
                fallback_available=bool(reference.get("view_fallback")),
            )
            if view_result["target_id"]:
                view = self.env["ir.ui.view"].sudo().browse(view_result["target_id"])
            elif STATUS_BUCKETS[view_result["status"]] in ("mapping_required", "blocked"):
                return self._result(
                    analysis, "ui_elements", path, "ui_element", reference,
                    "mapping_required" if STATUS_BUCKETS[view_result["status"]] == "mapping_required" else "blocked",
                    _("UI element %(label)s cannot resolve its source view.", label=reference.get("label") or "<unnamed>"),
                    "access.guard.ui.element",
                )

        match_value = reference.get("match_value")
        if match_mode == "action_id":
            action_ref = reference.get("action") or {"xml_id": match_value}
            action_result = self.resolve_xml_reference(
                analysis, action_ref, "%s.action" % path, "actions", "action",
                "ir.actions.actions", mappings=mappings,
                fallback_available=bool(action_ref.get("name") or action_ref.get("res_model") or action_ref.get("model")),
            )
            if not action_result["target_id"]:
                return self._result(
                    analysis, "ui_elements", path, "ui_element", reference,
                    "mapping_required" if STATUS_BUCKETS[action_result["status"]] == "mapping_required" else "blocked",
                    _("UI action element %(label)s needs an action mapping.", label=reference.get("label") or "<unnamed>"),
                    "access.guard.ui.element",
                )
            match_value = str(action_result["target_id"])
        elif match_mode == "attribute" and (not reference.get("attribute_name") or not match_value):
            return self._result(
                analysis, "ui_elements", path, "ui_element", reference, "blocked",
                _("Attribute-matched UI element %(label)s requires attribute_name and match_value.",
                  label=reference.get("label") or "<unnamed>"),
                "access.guard.ui.element",
            )
        elif not match_value:
            return self._result(
                analysis, "ui_elements", path, "ui_element", reference, "blocked",
                _("UI element %(label)s has no match value.", label=reference.get("label") or "<unnamed>"),
                "access.guard.ui.element",
            )

        candidates = self.env["access.guard.ui.element"].sudo().with_context(active_test=False).search(
            self._ui_candidate_domain(reference, model, view, match_value)
        )
        if len(candidates) == 1:
            status = "warning" if match_mode in ("string", "xpath") else "resolved"
            message = _("UI element %(label)s resolved.", label=reference.get("label") or candidates.display_name)
            if match_mode == "string":
                message = _("UI element %(label)s resolved using fragile language-sensitive string matching.",
                            label=reference.get("label") or candidates.display_name)
            elif match_mode == "xpath":
                message = _("UI element %(label)s resolved using a structurally fragile XPath.",
                            label=reference.get("label") or candidates.display_name)
            return self._result(
                analysis, "ui_elements", path, "ui_element", reference, status, message,
                "access.guard.ui.element", candidates.id,
            )
        if len(candidates) > 1:
            return self._result(
                analysis, "ui_elements", path, "ui_element", reference, "ambiguous",
                _("UI element %(label)s has multiple target candidates.", label=reference.get("label") or "<unnamed>"),
                "access.guard.ui.element",
            )
        status = "warning" if match_mode in ("string", "xpath") else "resolvable_on_import"
        message = _("UI element %(label)s can be regenerated during a future import.",
                    label=reference.get("label") or "<unnamed>")
        if match_mode == "string":
            message = _("UI element %(label)s can be regenerated, but string matching is language-sensitive.",
                        label=reference.get("label") or "<unnamed>")
        elif match_mode == "xpath":
            message = _("UI element %(label)s can be regenerated, but XPath matching is structurally fragile.",
                        label=reference.get("label") or "<unnamed>")
        return self._result(
            analysis, "ui_elements", path, "ui_element", reference, status, message,
            "access.guard.ui.element",
            candidate_values={
                "model_id": model.id,
                "type": element_type,
                "view_type": view_type,
                "match_mode": match_mode,
                "match_value": str(match_value),
                "attribute_name": reference.get("attribute_name") or "",
                "view_id": view.id if view else None,
            },
        )

    @api.model
    def _validate_domain_ast(self, tree):
        supported_names = {
            "uid", "user", "company_id", "company_ids", "context", "context_today",
            "active_id", "active_ids", "active_model", "True", "False", "None",
        }
        allowed_nodes = (
            ast.Expression, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Constant,
            ast.Name, ast.Load, ast.UnaryOp, ast.USub, ast.UAdd, ast.Attribute,
            ast.Call, ast.keyword,
        )
        for node in ast.walk(tree):
            if not isinstance(node, allowed_nodes):
                return False, _("Unsupported expression element: %s") % type(node).__name__
            if isinstance(node, ast.Name) and node.id not in supported_names:
                return False, _("Unsupported dynamic name: %s") % node.id
            if isinstance(node, ast.Call):
                context_get = (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "context"
                    and node.func.attr == "get"
                )
                if not context_get and (
                    not isinstance(node.func, ast.Name) or node.func.id != "context_today"
                ):
                    return False, _("Only context_today() and context.get() are supported in imported domains.")
        return True, None

    @api.model
    def resolve_domain(self, analysis, expression, model_name, path, label="Domain", mappings=None):
        self._check_resolver_access()
        source = {"model": model_name, "expression": expression, "label": label}
        if expression in (None, "", False, []):
            return self._result(
                analysis, "domains", path, "domain", source, "resolved",
                _("%(label)s is empty and valid.", label=label), source_label=label,
            )
        text = expression if isinstance(expression, str) else repr(expression)
        try:
            tree = ast.parse(text, mode="eval")
        except (SyntaxError, TypeError, ValueError):
            return self._result(
                analysis, "domains", path, "domain", source, "blocked",
                _("%(label)s on %(model)s has invalid syntax.", label=label, model=model_name or "<missing>"),
                source_label=label,
            )
        safe, reason = self._validate_domain_ast(tree)
        if not safe:
            return self._result(
                analysis, "domains", path, "domain", source, "blocked",
                _("%(label)s on %(model)s is unsafe or unsupported: %(reason)s",
                  label=label, model=model_name or "<missing>", reason=reason),
                source_label=label,
            )

        numeric_relations = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) < 3:
                continue
            field_node = node.elts[0]
            if not isinstance(field_node, ast.Constant) or not isinstance(field_node.value, str):
                continue
            if field_node.value in ("&", "|", "!"):
                continue
            field_path = field_node.value
            current_model = model_name
            field = self.env["ir.model.fields"]
            for part in field_path.split("."):
                if part == "id" and current_model:
                    field = None
                    current_model = None
                    continue
                field = self._find_field(current_model, part) if current_model else self.env["ir.model.fields"]
                if not field:
                    return self._result(
                        analysis, "domains", path, "domain", source, "blocked",
                        _("%(label)s references unknown field path %(field)s on %(model)s.",
                          label=label, field=field_path, model=model_name or "<missing>"),
                        source_label=label,
                    )
                current_model = field.relation if field.ttype in ("many2one", "one2many", "many2many") else None
            value_node = node.elts[2]
            numbers = [
                child.value for child in ast.walk(value_node)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, (int, float))
                and not isinstance(child.value, bool)
            ]
            if numbers and (field_path == "id" or field_path.endswith(".id") or field and field.ttype in (
                "many2one", "one2many", "many2many", "reference", "many2one_reference"
            )):
                numeric_relations.append((field_path, numbers, field.relation if field else model_name))

        if numeric_relations:
            manual = self._manual_result(
                analysis, mappings, "domains", path, "domain_record", source,
                numeric_relations[0][2] or "ir.model",
            )
            if manual:
                return manual
            details = ", ".join("%s=%s" % (field_name, values) for field_name, values, _relation in numeric_relations)
            return self._result(
                analysis, "domains", path, "domain_record", source, "mapping_required",
                _("%(label)s contains local numeric relational values (%(details)s); map them before import.",
                  label=label, details=details),
                numeric_relations[0][2] or "ir.model", source_label=label,
            )
        return self._result(
            analysis, "domains", path, "domain", source, "resolved",
            _("%(label)s is valid for %(model)s.", label=label, model=model_name),
            source_label=label,
        )

    @api.model
    def resolve_template(self, payload, mappings=None):
        self._check_resolver_access()
        analysis = self._new_analysis()

        def resolve_model_line(line, path):
            return self.resolve_model(analysis, line.get("model"), "%s.model" % path)

        for index, line in enumerate(payload.get("menus") or []):
            ref = line.get("menu") or {}
            self.resolve_xml_reference(
                analysis, ref, "menus.%s.menu" % index, "menus", "menu", "ir.ui.menu",
                mappings=mappings, fallback_available=bool(ref.get("name") or ref.get("parent_xml_id")),
            )

        for index, line in enumerate(payload.get("model_rules") or []):
            path = "model_rules.%s" % index
            resolve_model_line(line, path)
            for key, model_name, ref_type in (
                ("reports", "ir.actions.report", "report"),
                ("server_actions", "ir.actions.server", "action"),
                ("window_actions", "ir.actions.act_window", "action"),
            ):
                for ref_index, ref in enumerate(line.get(key) or []):
                    self.resolve_xml_reference(
                        analysis, ref, "%s.%s.%s" % (path, key, ref_index), "actions", ref_type,
                        model_name, mappings=mappings,
                        fallback_available=bool(ref.get("name") or ref.get("model") or ref.get("res_model")),
                    )
            for ref_index, ref in enumerate(line.get("views") or []):
                self.resolve_xml_reference(
                    analysis, ref, "%s.views.%s" % (path, ref_index), "views", "view",
                    "ir.ui.view", mappings=mappings,
                    fallback_available=bool(ref.get("name") or ref.get("model")),
                )
            for ref_index, ref in enumerate(line.get("hidden_view_modes") or []):
                self.resolve_view_mode(analysis, ref, "%s.hidden_view_modes.%s" % (path, ref_index))

        for index, line in enumerate(payload.get("field_rules") or []):
            path = "field_rules.%s" % index
            resolve_model_line(line, path)
            for ref_index, ref in enumerate(line.get("fields") or []):
                self.resolve_field(analysis, ref, "%s.fields.%s" % (path, ref_index))

        for index, line in enumerate(payload.get("conditional_rules") or []):
            path = "conditional_rules.%s" % index
            model_result = resolve_model_line(line, path)
            model_name = self._model_name(line.get("model"))
            for ref_index, ref in enumerate(line.get("attr_fields") or []):
                self.resolve_field(analysis, ref, "%s.attr_fields.%s" % (path, ref_index))
            if line.get("domain_field"):
                self.resolve_field(analysis, line["domain_field"], "%s.domain_field" % path)
            for ref_index, ref in enumerate(line.get("attr_buttons") or []):
                self.resolve_ui_element(analysis, ref, "%s.attr_buttons.%s" % (path, ref_index), mappings)
            if line.get("apply_attrs") or line.get("attrs_domain"):
                self.resolve_domain(analysis, line.get("attrs_domain"), model_name,
                                    "%s.attrs_domain" % path, "Conditional attrs domain", mappings)
            if line.get("apply_domain") or line.get("domain_expression"):
                self.resolve_domain(analysis, line.get("domain_expression"), model_name,
                                    "%s.domain_expression" % path, "Conditional field domain", mappings)
            del model_result

        for index, line in enumerate(payload.get("domain_rules") or []):
            path = "domain_rules.%s" % index
            resolve_model_line(line, path)
            self.resolve_domain(
                analysis, line.get("domain_expression"), self._model_name(line.get("model")),
                "%s.domain_expression" % path, "Domain rule", mappings,
            )

        for index, line in enumerate(payload.get("ui_rules") or []):
            path = "ui_rules.%s" % index
            resolve_model_line(line, path)
            for key in (
                "button_elements", "action_button_elements", "smart_button_elements",
                "tab_elements", "kanban_link_elements",
            ):
                for ref_index, ref in enumerate(line.get(key) or []):
                    self.resolve_ui_element(analysis, ref, "%s.%s.%s" % (path, key, ref_index), mappings)

        for index, line in enumerate(payload.get("search_rules") or []):
            path = "search_rules.%s" % index
            resolve_model_line(line, path)
            for key in ("filter_items", "group_items"):
                for ref_index, ref in enumerate(line.get(key) or []):
                    self.resolve_search_item(analysis, ref, "%s.%s.%s" % (path, key, ref_index))

        for index, line in enumerate(payload.get("chatter_rules") or []):
            resolve_model_line(line, "chatter_rules.%s" % index)

        for index, ref in enumerate((payload.get("overview") or {}).get("picking_types") or []):
            self.resolve_picking_type(analysis, ref, "overview.picking_types.%s" % index, mappings)

        for index, line in enumerate(payload.get("acl_rules") or []):
            path = "acl_rules.%s" % index
            model_result = self.resolve_model(analysis, line.get("model"), "%s.model" % path)
            group_ref = {"xml_id": line.get("group_xml_id")}
            group_ref.update(line.get("group_fallback") or {})
            group_result = self.resolve_xml_reference(
                analysis, group_ref, "%s.group" % path, "groups", "group", "res.groups",
                mappings=mappings, fallback_available=bool(line.get("group_fallback")),
            )
            acl_status = "resolved"
            if STATUS_BUCKETS[model_result["status"]] == "blocked" or STATUS_BUCKETS[group_result["status"]] == "blocked":
                acl_status = "blocked"
            elif STATUS_BUCKETS[group_result["status"]] == "mapping_required":
                acl_status = "mapping_required"
            self._result(
                analysis, "acls", path, "acl", line, acl_status,
                _("ACL reference is %(status)s.", status=acl_status.replace("_", " ")),
                "ir.model.access",
            )
        return analysis


class AccessGuardImportWizard(models.TransientModel):
    _name = "access.guard.import.wizard"
    _description = "Access Guard Template Import Validation"

    state = fields.Selection(
        [
            ("upload", "Upload File"),
            ("summary", "Validation Summary"),
            ("mapping", "Resolve Mappings"),
            ("ready", "Ready to Import"),
        ],
        default="upload",
        required=True,
        readonly=True,
    )
    file_data = fields.Binary(string="JSON File", attachment=False)
    filename = fields.Char()
    detected_format = fields.Char(readonly=True)
    template_count = fields.Integer(readonly=True)
    overall_status = fields.Selection(
        [
            ("ready", "Ready to Import"),
            ("mapping_required", "Mapping Required"),
            ("blocked", "Blocked"),
            ("exact_duplicate", "Exact Duplicate"),
        ],
        readonly=True,
    )
    validation_report = fields.Text(readonly=True)
    validation_report_html = fields.Html(readonly=True, sanitize=False)
    import_plan = fields.Text(readonly=True)
    result_line_ids = fields.One2many(
        "access.guard.import.result", "wizard_id", string="Templates", readonly=True
    )
    mapping_line_ids = fields.One2many(
        "access.guard.import.mapping", "wizard_id", string="Mappings"
    )

    @api.model
    def _check_import_access(self):
        if not self.env.user.has_group("npt_access_guard.group_access_guard_manager"):
            raise AccessError(_("Only Access Guard Managers can validate template imports."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_import_access()
        return super().create(vals_list)

    def write(self, vals):
        self._check_import_access()
        return super().write(vals)

    def unlink(self):
        self._check_import_access()
        return super().unlink()

    def _decode_document(self):
        self.ensure_one()
        if not self.file_data:
            raise UserError(_("Upload an Access Guard JSON file first."))
        encoded = self.file_data.encode("ascii") if isinstance(self.file_data, str) else self.file_data
        if len(encoded) > ((MAX_IMPORT_BYTES + 2) // 3) * 4 + 16:
            raise UserError(_("The import file is too large. Maximum size is 5 MB."))
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError) as error:
            raise UserError(_("The uploaded file is not valid binary data.")) from error
        if len(raw) > MAX_IMPORT_BYTES:
            raise UserError(_("The import file is too large. Maximum size is 5 MB."))
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise UserError(_("The uploaded file is not valid UTF-8 JSON: %s") % error) from error
        if not isinstance(document, dict):
            raise UserError(_("The import JSON root must be an object."))
        return document

    def _validate_root_envelope(self, document):
        file_format = document.get("format")
        if file_format not in ("access_guard_template", "access_guard_template_bundle"):
            raise UserError(_("Unsupported Access Guard import format: %s") % (file_format or "<missing>"))
        if document.get("format_version") != IMPORT_FORMAT_VERSION:
            raise UserError(
                _("Unsupported import format version %(version)s. Supported version: %(supported)s.",
                  version=document.get("format_version") or "<missing>", supported=IMPORT_FORMAT_VERSION)
            )
        metadata = document.get("export_metadata")
        if not isinstance(metadata, dict):
            raise UserError(_("Export metadata is missing or malformed."))
        missing_metadata = [
            key for key in ("exported_from_odoo_version", "access_guard_version", "exported_at")
            if not metadata.get(key)
        ]
        if missing_metadata:
            raise UserError(_("Export metadata is missing: %s") % ", ".join(missing_metadata))
        if file_format == "access_guard_template":
            if not isinstance(document.get("template"), dict):
                raise UserError(_("Single-template export has no valid template payload."))
            if not isinstance(document.get("dependencies"), dict):
                raise UserError(_("Single-template export has no valid dependencies section."))
            if not metadata.get("portable_hash"):
                raise UserError(_("Single-template export metadata has no portable_hash."))
        else:
            if not isinstance(document.get("templates"), list):
                raise UserError(_("Template bundle has no valid templates list."))
            if not document["templates"]:
                raise UserError(_("Template bundle is empty."))
            if not isinstance(document.get("bundle_dependencies"), dict):
                raise UserError(_("Template bundle has no valid dependency section."))
        return file_format, metadata

    @api.model
    def _major_version(self, value):
        match = re.match(r"^\s*(\d+)", str(value or ""))
        return int(match.group(1)) if match else None

    @api.model
    def _version_tuple(self, value):
        return tuple(int(part) for part in re.findall(r"\d+", str(value or "")))

    def _version_warnings(self, metadata):
        warnings = []
        exported_odoo = metadata.get("exported_from_odoo_version")
        if self._major_version(exported_odoo) != 18:
            return warnings, _("Odoo version %(version)s is unsupported; only Odoo 18 exports are accepted.",
                               version=exported_odoo)
        exported_guard = metadata.get("access_guard_version")
        current_guard = self.env["access.guard.template"]._source_module_version()
        if exported_guard and current_guard and self._version_tuple(exported_guard) != self._version_tuple(current_guard):
            relation = "older" if self._version_tuple(exported_guard) < self._version_tuple(current_guard) else "newer"
            warnings.append(
                _("File was exported by an %(relation)s Access Guard version (%(source_version)s); target is %(target)s.",
                  relation=relation, source_version=exported_guard, target=current_guard)
            )
        return warnings, None

    def _entries(self, document, file_format):
        if file_format == "access_guard_template":
            payload = document["template"]
            identity = payload.get("template") or {}
            return [{
                "template_code": identity.get("code"),
                "template_version": identity.get("version"),
                "schema_version": payload.get("schema_version"),
                "portable_hash": document["export_metadata"].get("portable_hash"),
                "dependencies": document.get("dependencies"),
                "template": payload,
            }]
        return document["templates"]

    def _dependency_result(self, required_modules):
        if not isinstance(required_modules, list) or not all(isinstance(item, str) and item for item in required_modules):
            return [], [], _("Required module list is malformed.")
        modules = self.env["ir.module.module"].sudo().search([("name", "in", list(set(required_modules)))])
        states = {module.name: module.state for module in modules}
        missing = sorted(name for name in set(required_modules) if states.get(name) != "installed")
        installed = sorted(name for name in set(required_modules) if states.get(name) == "installed")
        return installed, missing, None

    def _duplicate_status(self, code, version, portable_hash):
        Template = self.env["access.guard.template"].with_context(active_test=False).sudo()
        existing = Template.search([
            ("template_code", "=", code),
            ("template_version", "=", version),
        ], limit=1)
        if existing and existing.portable_hash == portable_hash:
            return "exact_duplicate", "SKIP_EXACT_DUPLICATE", existing
        if existing:
            return "version_conflict", "BLOCK_VERSION_CONFLICT", existing
        other_version = Template.search([("template_code", "=", code)], limit=1)
        if other_version:
            return "existing_other_version", "REVIEW_EXISTING_OTHER_VERSION", other_version
        return "new", "CREATE_NEW", None

    def _mapping_overrides(self):
        overrides = {}
        for line in self.mapping_line_ids:
            target_model, target_id = line._selected_target()
            if target_model and target_id:
                overrides[line.mapping_key] = {
                    "target_model": target_model,
                    "target_id": target_id,
                }
        return overrides

    def _planned_mapping_overrides(self, template_index):
        self.ensure_one()
        if not self.import_plan:
            return {}
        try:
            plan = json.loads(self.import_plan)
        except (TypeError, ValueError):
            return {}
        templates = plan.get("templates") if isinstance(plan, dict) else None
        if not isinstance(templates, list) or template_index >= len(templates):
            return {}
        return {
            mapping["path"]: {
                "target_model": mapping.get("target_model"),
                "target_id": mapping.get("target_id"),
            }
            for mapping in templates[template_index].get("mappings") or []
            if mapping.get("path") and mapping.get("target_model") and mapping.get("target_id")
        }

    def _analyze_entry(self, entry, index, metadata, mappings):
        payload = entry.get("template") if isinstance(entry, dict) else None
        result = {
            "index": index,
            "code": entry.get("template_code") if isinstance(entry, dict) else None,
            "version": entry.get("template_version") if isinstance(entry, dict) else None,
            "schema_version": entry.get("schema_version") if isinstance(entry, dict) else None,
            "portable_hash": entry.get("portable_hash") if isinstance(entry, dict) else None,
            "hash_status": "blocked",
            "schema_status": "blocked",
            "dependency_status": "blocked",
            "duplicate_status": "new",
            "operation": "CREATE_NEW",
            "warnings": [],
            "errors": [],
            "required_modules": [],
            "missing_modules": [],
            "analysis": self.env["access.guard.import.resolver"]._new_analysis(),
        }
        if not isinstance(entry, dict) or not isinstance(payload, dict):
            result["errors"].append(_("Template entry %(index)s is malformed.", index=index + 1))
            result["status"] = "blocked"
            return result

        identity = payload.get("template") if isinstance(payload.get("template"), dict) else {}
        code = entry.get("template_code") or identity.get("code")
        version = entry.get("template_version") or identity.get("version")
        schema = entry.get("schema_version") or payload.get("schema_version")
        portable_hash = entry.get("portable_hash")
        result.update({"code": code, "version": version, "schema_version": schema, "portable_hash": portable_hash})
        missing = [
            name for name, value in (
                ("template_code", code), ("template_version", version),
                ("schema_version", schema), ("portable_hash", portable_hash),
            ) if not value
        ]
        if missing:
            result["errors"].append(_("Template entry is missing: %s") % ", ".join(missing))
        if identity.get("code") and code != identity.get("code"):
            result["errors"].append(_("Envelope template_code does not match the portable payload."))
        if identity.get("version") and version != identity.get("version"):
            result["errors"].append(_("Envelope template_version does not match the portable payload."))
        if payload.get("schema_version") and schema != payload.get("schema_version"):
            result["errors"].append(_("Envelope schema_version does not match the portable payload."))

        if schema == IMPORT_SCHEMA_VERSION:
            result["schema_status"] = "ready"
        else:
            result["errors"].append(
                _("Schema %(schema)s is unsupported; supported schema is %(supported)s.",
                  schema=schema or "<missing>", supported=IMPORT_SCHEMA_VERSION)
            )

        if portable_hash:
            canonical = self.env["access.guard.template"]._canonical_snapshot_json(payload)
            calculated = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if calculated == portable_hash:
                result["hash_status"] = "ready"
            else:
                result["errors"].append(
                    _("Portable payload hash mismatch. File may be modified or corrupted.")
                )

        dependencies = entry.get("dependencies") or payload.get("dependencies") or {}
        required_modules = dependencies.get("required_modules") if isinstance(dependencies, dict) else None
        installed, missing_modules, dependency_error = self._dependency_result(required_modules)
        result["required_modules"] = sorted(set(required_modules or [])) if isinstance(required_modules, list) else []
        result["missing_modules"] = missing_modules
        if dependency_error:
            result["errors"].append(dependency_error)
        elif missing_modules:
            result["errors"].append(_("Missing required modules: %s") % ", ".join(missing_modules))
        else:
            result["dependency_status"] = "ready"
        del installed

        if not result["errors"]:
            try:
                self.env["access.guard.template"]._validate_portable_data(payload)
            except (ValidationError, TypeError, ValueError) as error:
                result["errors"].append(_("Portable payload structure is invalid: %s") % error)

        duplicate, operation, existing = self._duplicate_status(code, version, portable_hash) if code else ("new", "CREATE_NEW", None)
        result["duplicate_status"] = duplicate
        result["operation"] = operation
        result["existing_template_id"] = existing.id if existing else None

        if not result["errors"]:
            result["analysis"] = self.env["access.guard.import.resolver"].resolve_template(payload, mappings=mappings)
        counts = result["analysis"]["categories"]
        blocked = sum(values["blocked"] for values in counts.values())
        mapping = sum(values["mapping_required"] for values in counts.values())
        warnings = sum(values["warnings"] for values in counts.values())
        if result["errors"] or blocked or duplicate == "version_conflict":
            result["status"] = "blocked"
        elif mapping:
            result["status"] = "mapping_required"
        elif duplicate == "exact_duplicate":
            result["status"] = "exact_duplicate"
        else:
            result["status"] = "ready"
        if warnings:
            result["warnings"].extend(result["analysis"]["messages"])
        if duplicate == "existing_other_version":
            result["warnings"].append(
                _("Another version of this template already exists; this version will be created separately.")
            )
        result["warnings"].extend(self._version_warnings(metadata)[0])
        return result

    def _sync_result_lines(self, results):
        self.result_line_ids.unlink()
        Result = self.env["access.guard.import.result"]
        for result in results:
            Result.create({
                "wizard_id": self.id,
                "template_index": result["index"],
                "template_code": result.get("code") or "<missing>",
                "template_version": result.get("version") or "<missing>",
                "status": result["status"],
                "duplicate_status": result["duplicate_status"],
                "operation": result["operation"],
                "summary": self._template_report_text(result),
            })

    def _sync_mapping_lines(self, results):
        existing = {line.mapping_key: line for line in self.mapping_line_ids}
        required = {}
        for result in results:
            for resolution in result["analysis"]["results"]:
                if STATUS_BUCKETS[resolution["status"]] != "mapping_required":
                    continue
                key = "template.%s.%s" % (result["index"], resolution["path"])
                required[key] = (result, resolution)
        for key, (template, resolution) in required.items():
            values = {
                "template_index": template["index"],
                "template_code": template.get("code") or "<missing>",
                "mapping_key": key,
                "reference_type": resolution["reference_type"],
                "source_label": resolution["source_label"],
                "source_payload": json.dumps(resolution["source_reference"], sort_keys=True, indent=2),
                "source_model": (resolution["source_reference"] or {}).get("model")
                if isinstance(resolution["source_reference"], dict) else False,
                "target_model": resolution.get("target_model")
                if isinstance(resolution.get("target_model"), str) else False,
                "status": resolution["status"],
                "message": resolution["message"],
            }
            if key in existing:
                existing[key].write(values)
            else:
                self.env["access.guard.import.mapping"].create({"wizard_id": self.id, **values})
        stale = self.mapping_line_ids.filtered(lambda line: line.mapping_key not in required)
        stale.unlink()

    def _template_report_text(self, result):
        lines = [
            "Template: %s" % (result.get("code") or "<missing>"),
            "Version: %s" % (result.get("version") or "<missing>"),
            "Hash: %s" % result["hash_status"],
            "Schema: %s" % result["schema_status"],
            "Dependencies: %s" % result["dependency_status"],
            "Duplicate: %s" % result["duplicate_status"],
            "Operation: %s" % result["operation"],
            "",
        ]
        labels = {
            "models": "Models", "fields": "Fields", "menus": "Menus", "views": "Views",
            "actions": "Actions", "groups": "Groups", "ui_elements": "UI Elements",
            "search_items": "Search Items", "view_modes": "View Modes",
            "operation_types": "Operation Types", "domains": "Domains", "acls": "ACL",
        }
        for category in IMPORT_CATEGORIES:
            counts = result["analysis"]["categories"][category]
            lines.append(
                "%s: %s/%s ready, %s warnings, %s mapping, %s blocked"
                % (labels[category], counts["ready"], counts["total"], counts["warnings"],
                   counts["mapping_required"], counts["blocked"])
            )
        lines.extend(["", "Overall: %s" % result["status"].replace("_", " ").upper()])
        messages = result["errors"] + list(dict.fromkeys(result["warnings"] + result["analysis"]["messages"]))
        if messages:
            lines.extend(["", "Messages:"] + ["- %s" % message for message in messages])
        return "\n".join(lines)

    def _report_html(self, results, overall):
        sections = [
            Markup("<h3>%s</h3><p><strong>%s</strong>: %s</p>")
            % (escape(_("Import Validation Report")), escape(_("Overall")), escape(overall.replace("_", " ").upper()))
        ]
        for result in results:
            sections.append(
                Markup("<section><h4>%s <small>v%s</small></h4><pre>%s</pre></section>")
                % (escape(result.get("code") or "<missing>"),
                   escape(result.get("version") or "<missing>"),
                   escape(self._template_report_text(result)))
            )
        return Markup("").join(sections)

    def _build_import_plan(self, results, overall):
        if overall not in ("ready", "exact_duplicate"):
            return False
        plan = {
            "format": "access_guard_import_plan",
            "format_version": IMPORT_FORMAT_VERSION,
            "generated_at": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_filename": self.filename,
            "status": "READY_TO_IMPORT",
            "templates": [],
        }
        for result in results:
            resolved = {}
            mapped = []
            for item in result["analysis"]["results"]:
                if item.get("target_id"):
                    resolved[item["category"]] = resolved.get(item["category"], 0) + 1
                if item["status"] == "resolved_manually":
                    mapped.append({
                        "path": item["path"],
                        "source": item["source_label"],
                        "target_model": item["target_model"],
                        "target_id": item["target_id"],
                    })
            plan["templates"].append({
                "template_code": result["code"],
                "template_version": result["version"],
                "portable_hash": result["portable_hash"],
                "operation": result["operation"],
                "resolved": resolved,
                "mappings": mapped,
                "dependencies": result["required_modules"],
                "status": "READY_TO_IMPORT",
            })
        return json.dumps(plan, sort_keys=True, indent=2)

    def _run_validation(self):
        self.ensure_one()
        self._check_import_access()
        document = self._decode_document()
        file_format, metadata = self._validate_root_envelope(document)
        _warnings, version_error = self._version_warnings(metadata)
        entries = self._entries(document, file_format)
        results = []
        for index, entry in enumerate(entries):
            mappings = self._planned_mapping_overrides(index)
            prefix = "template.%s." % index
            for line in self.mapping_line_ids.filtered(lambda item: item.template_index == index):
                target_model, target_id = line._selected_target()
                if target_id:
                    path = line.mapping_key[len(prefix):] if line.mapping_key.startswith(prefix) else line.mapping_key
                    mappings[path] = {"target_model": target_model, "target_id": target_id}
            result = self._analyze_entry(entry, index, metadata, mappings)
            if version_error:
                result["errors"].append(version_error)
                result["status"] = "blocked"
            results.append(result)
        self._sync_result_lines(results)
        self._sync_mapping_lines(results)

        if any(result["status"] == "blocked" for result in results):
            overall = "blocked"
        elif any(result["status"] == "mapping_required" for result in results):
            overall = "mapping_required"
        elif all(result["status"] == "exact_duplicate" for result in results):
            overall = "exact_duplicate"
        else:
            overall = "ready"
        report = "\n\n".join(self._template_report_text(result) for result in results)
        self.with_context(access_guard_import_internal=True).write({
            "detected_format": file_format,
            "template_count": len(results),
            "overall_status": overall,
            "validation_report": report,
            "validation_report_html": self._report_html(results, overall),
            "import_plan": self._build_import_plan(results, overall),
            "state": "ready" if overall == "ready" else ("mapping" if overall == "mapping_required" else "summary"),
        })
        return results

    def action_validate(self):
        self._run_validation()
        return self._reopen_action()

    def action_validate_again(self):
        self._run_validation()
        return self._reopen_action()

    def action_show_mappings(self):
        self.ensure_one()
        self._check_import_access()
        self.with_context(access_guard_import_internal=True).state = "mapping"
        return self._reopen_action()

    def action_back_to_summary(self):
        self.ensure_one()
        self._check_import_access()
        self.with_context(access_guard_import_internal=True).state = "summary"
        return self._reopen_action()

    def _reopen_action(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Import Template"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("npt_access_guard.view_access_guard_import_wizard_form").id,
            "target": "new",
        }


class AccessGuardImportResult(models.TransientModel):
    _name = "access.guard.import.result"
    _description = "Access Guard Import Validation Result"
    _order = "template_index, id"

    wizard_id = fields.Many2one("access.guard.import.wizard", required=True, ondelete="cascade")
    template_index = fields.Integer(readonly=True)
    template_code = fields.Char(readonly=True)
    template_version = fields.Char(readonly=True)
    status = fields.Selection(
        [
            ("ready", "Ready"),
            ("mapping_required", "Mapping Required"),
            ("blocked", "Blocked"),
            ("exact_duplicate", "Exact Duplicate"),
        ],
        readonly=True,
    )
    duplicate_status = fields.Selection(
        [
            ("new", "New"),
            ("exact_duplicate", "Exact Duplicate"),
            ("version_conflict", "Version Conflict"),
            ("existing_other_version", "Existing Other Version"),
        ],
        readonly=True,
    )
    operation = fields.Char(readonly=True)
    summary = fields.Text(readonly=True)


class AccessGuardImportMapping(models.TransientModel):
    _name = "access.guard.import.mapping"
    _description = "Access Guard Import Manual Mapping"
    _order = "template_index, reference_type, id"

    wizard_id = fields.Many2one("access.guard.import.wizard", required=True, ondelete="cascade")
    template_index = fields.Integer(readonly=True)
    template_code = fields.Char(readonly=True)
    mapping_key = fields.Char(required=True, readonly=True)
    reference_type = fields.Selection(
        [
            ("menu", "Menu"),
            ("view", "View"),
            ("action", "Action"),
            ("report", "Report"),
            ("group", "Group"),
            ("picking_type", "Operation Type"),
            ("ui_element", "UI Element"),
            ("domain_record", "Domain Record"),
            ("search_item", "Search Item"),
            ("view_mode", "View Mode"),
            ("model", "Model"),
            ("field", "Field"),
            ("other", "Other"),
        ],
        required=True,
        readonly=True,
    )
    source_label = fields.Char(readonly=True)
    source_payload = fields.Text(readonly=True)
    source_model = fields.Char(readonly=True)
    target_model = fields.Char(string="Target Model Name", readonly=True)
    status = fields.Selection(
        [("mapping_required", "Mapping Required"), ("ambiguous", "Ambiguous")],
        readonly=True,
    )
    message = fields.Text(readonly=True)
    target_menu_id = fields.Many2one("ir.ui.menu", string="Target Menu")
    target_view_id = fields.Many2one("ir.ui.view", string="Target View")
    target_action_id = fields.Many2one("ir.actions.actions", string="Target Action")
    target_group_id = fields.Many2one("res.groups", string="Target Group")
    target_picking_type_id = fields.Many2one("stock.picking.type", string="Target Operation Type")
    target_ui_element_id = fields.Many2one("access.guard.ui.element", string="Target UI Element")
    target_search_item_id = fields.Many2one("access.guard.search.item", string="Target Search Item")
    target_view_mode_id = fields.Many2one("access.guard.view.mode", string="Target View Mode")
    target_model_id = fields.Many2one("ir.model", string="Target Model")
    target_field_id = fields.Many2one("ir.model.fields", string="Target Field")
    target_record_id = fields.Integer(string="Target Record ID")
    target_record_reference = fields.Char(
        string="Target Reference",
        help="Technical target model is shown above. Enter an ID only for generic/domain mappings.",
    )

    def _selected_target(self):
        self.ensure_one()
        field_by_type = {
            "menu": ("target_menu_id", "ir.ui.menu"),
            "view": ("target_view_id", "ir.ui.view"),
            "action": ("target_action_id", "ir.actions.actions"),
            "report": ("target_action_id", "ir.actions.actions"),
            "group": ("target_group_id", "res.groups"),
            "picking_type": ("target_picking_type_id", "stock.picking.type"),
            "ui_element": ("target_ui_element_id", "access.guard.ui.element"),
            "search_item": ("target_search_item_id", "access.guard.search.item"),
            "view_mode": ("target_view_mode_id", "access.guard.view.mode"),
            "model": ("target_model_id", "ir.model"),
            "field": ("target_field_id", "ir.model.fields"),
        }
        field_info = field_by_type.get(self.reference_type)
        if field_info:
            record = self[field_info[0]]
            if record:
                if (
                    self.reference_type in ("action", "report")
                    and self.target_model in ACTION_MODELS
                    and self.env[self.target_model].sudo().browse(record.id).exists()
                ):
                    return self.target_model, record.id
                return record._name, record.id
        if self.target_record_id and self.target_model:
            return self.target_model, self.target_record_id
        return None, None
