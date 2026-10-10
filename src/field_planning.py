"""
Field-addition planning (no browser, no writes).

Inputs come from other MCP servers, passed through by the orchestrating
agent (Claude), because MCP servers cannot call each other:

  * Informatica MCP  get_source_fields(connection, object_name, by="name")
      -> list of field dicts (name, label, pcType, precision, scale, ...)
  * Informatica MCP  preview_source_data(connection, object_name, by="name")
      -> {"fieldName": [...], "rows": [{"values": [...]}, ...]}
  * Snowflake MCP    describe_table("DB.SCHEMA.TABLE")
      -> target column names
  * Web UI MCP       current source query read from the mapping task

The output is a plan the user must confirm before any UI change is made.
"""

from __future__ import annotations

import json
import re
from typing import Any


# ---------------------------------------------------------------------- #
# Input normalisation
# ---------------------------------------------------------------------- #
def normalise_source_fields(raw: Any) -> list[dict]:
    """
    Accept the get_source_fields output in any of the shapes it arrives in:
    a list of field dicts, a list of {"type": "text", "text": "<json>"}
    wrappers, or a JSON string of either.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        raw = [raw]

    fields: list[dict] = []
    for item in raw or []:
        if isinstance(item, dict) and "text" in item and "name" not in item:
            item = json.loads(item["text"])
        if isinstance(item, dict) and item.get("name"):
            fields.append(item)
    return fields


def _find_field(fields: list[dict], name: str) -> dict | None:
    exact = [f for f in fields if f.get("name") == name]
    if exact:
        return exact[0]
    folded = [f for f in fields if str(f.get("name", "")).lower() == name.lower()]
    return folded[0] if len(folded) == 1 else None


# ---------------------------------------------------------------------- #
# Preview summary (minimises data exposure)
# ---------------------------------------------------------------------- #
def summarise_preview(
    preview: Any,
    field_name: str,
    key_field: str = "Id",
    max_samples: int = 5,
) -> dict:
    """
    Reduce a preview_source_data payload to the candidate column only.

    Only the record key and the candidate field's values are returned, so
    addresses, account names, etc. from other columns are not surfaced.
    """
    if isinstance(preview, str):
        preview = json.loads(preview)

    names: list[str] = preview.get("fieldName") or []
    if field_name not in names:
        return {
            "field": field_name,
            "present_in_preview": False,
            "rows_previewed": len(preview.get("rows") or []),
        }

    idx = names.index(field_name)
    key_idx = names.index(key_field) if key_field in names else None

    values, samples = [], []
    for row in preview.get("rows") or []:
        vals = row.get("values") or []
        v = vals[idx] if idx < len(vals) else ""
        values.append(v)
        if v not in ("", None) and len(samples) < max_samples:
            samples.append({
                key_field: vals[key_idx] if key_idx is not None else None,
                field_name: v,
            })

    populated = sum(1 for v in values if v not in ("", None))
    return {
        "field": field_name,
        "present_in_preview": True,
        "rows_previewed": len(values),
        "rows_populated": populated,
        "samples": samples,
        "note": (
            "Preview returns a small, unfiltered sample. Zero populated rows "
            "does not mean the field is unused; metadata confirms existence."
            if populated == 0 else ""
        ),
    }


# ---------------------------------------------------------------------- #
# Source query (SOQL) editing
# ---------------------------------------------------------------------- #
_SELECT_RE = re.compile(r"^\s*select\s+(?P<cols>.+?)\s+from\s+", re.I | re.S)


def query_columns(query: str) -> list[str]:
    """Return the top-level SELECT column list of a simple SOQL query."""
    m = _SELECT_RE.match(query or "")
    if not m:
        raise ValueError("Could not find a top-level 'SELECT ... FROM' in the query.")
    cols = m.group("cols")
    if "(" in cols:
        # Sub-selects / functions: refuse rather than mis-parse.
        raise ValueError(
            "Query SELECT list contains parentheses (sub-query or function); "
            "edit manually."
        )
    return [c.strip() for c in cols.split(",") if c.strip()]


def add_field_to_query(query: str, field_name: str) -> str:
    """
    Append `field_name` to the end of the top-level SELECT list,
    preserving the original formatting of the rest of the query.
    """
    existing = {c.lower() for c in query_columns(query)}
    if field_name.lower() in existing:
        raise ValueError(f"{field_name!r} is already in the source query.")

    m = _SELECT_RE.match(query)
    cols_end = m.end("cols")
    cols_text = m.group("cols")

    # Keep multi-line formatting if the query puts one column per line.
    if "\n" in cols_text:
        last_line = cols_text.rstrip().splitlines()[-1]
        indent = re.match(r"\s*", last_line).group(0)
        insertion = f",\n{indent}{field_name}"
    else:
        # Follow the query's own comma style: "a ,b" (leading) vs "a, b".
        leading = len(re.findall(r"\s,\S", cols_text))
        trailing = len(re.findall(r"\S,\s", cols_text))
        insertion = f" ,{field_name}" if leading > trailing else f", {field_name}"

    return query[:cols_end] + insertion + query[cols_end:]


# ---------------------------------------------------------------------- #
# Target column suggestion
# ---------------------------------------------------------------------- #
def suggest_snowflake_type(field: dict) -> str:
    """Suggest a Snowflake type from IICS pcType / precision / scale."""
    pc = str(field.get("pcType", "")).upper()
    p = int(field.get("precision") or 0)
    s = int(field.get("scale") or 0)

    if pc in {"STRING", "NSTRING", "TEXT", "NTEXT"}:
        return f"VARCHAR({p})" if p else "VARCHAR"
    if pc in {"DECIMAL", "HIPRECDECIMAL", "DOUBLE", "NUMBER"}:
        return f"NUMBER({min(p, 38)},{s})" if p else "NUMBER"
    if pc in {"INTEGER", "SMALLINTEGER", "BIGINT"}:
        return "NUMBER(38,0)"
    # Decided 2026-10-08: new Salesforce date and date/time fields are
    # stored as timestamp_ntz(9) in staging (matches most existing columns).
    if pc in {"DATE", "DATE/TIME", "DATETIME", "TIMESTAMP"}:
        return "TIMESTAMP_NTZ(9)"
    if pc == "BOOLEAN":
        return "BOOLEAN"
    return "VARCHAR"


def _letters(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _label_to_name(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper()


def _split_camel(s: str) -> str:
    """ActivatedDate -> ACTIVATED_DATE; ContractTermText -> CONTRACT_TERM_TEXT."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.upper()


def suggest_provisioning_name(
    api_name: str,
    label: str | None,
    object_name: str | None = None,
) -> dict:
    """
    Propose a provisioning column name for a Salesforce field.

    Rules confirmed against DEV_PROVISIONING.SALESFORCE.CONTRACT (34 of 37
    non-key columns; the rest are flagged for review):

      1. Id                       -> <OBJECT>_ID              (CONTRACT_ID)
      2. API name has underscores -> drop __c, keep as is
                                     (Contract_Migration_ID__c -> CONTRACT_MIGRATION_ID;
                                      the label "Migration ID" is NOT used)
      3. Run-together API name    -> word breaks from the label when the label
                                     has the same letters
                                     (AccountId / "Account ID" -> ACCOUNT_ID)
      4. ...or the label is the object name + the API name
                                     (StartDate / "Contract Start Date"
                                      -> CONTRACT_START_DATE)
      5. Otherwise                -> split camelCase, needs_review=True

    Every result is shown at Gate 2; needs_review marks the uncertain ones.
    """
    base = re.sub(r"__c$", "", api_name, flags=re.I)
    obj = (object_name or "").strip()

    def result(name, source, review=False, reason=""):
        out = {"name": name, "source": source, "needs_review": review}
        if reason:
            out["reason"] = reason
        return out

    # 1. Record key.
    if base.lower() == "id":
        if obj:
            return result(f"{_split_camel(obj)}_ID", "key_rule")
        return result("ID", "key_rule", True, "Object name unknown; expected <OBJECT>_ID.")

    # 2. Already separated by underscores: just drop __c.
    if "_" in base:
        return result(base.upper(), "api_name")

    # 3 / 4. Run-together name: take word breaks from the label.
    if label:
        from_label = _label_to_name(label)
        if _letters(from_label) == _letters(base):
            return result(from_label, "label")
        if obj and _letters(from_label) == _letters(obj) + _letters(base):
            return result(from_label, "label_with_object_prefix")

    # 5. Fall back to splitting the API name.
    return result(
        _split_camel(base),
        "camel_case_split",
        True,
        (f"Label {label!r} does not match API name {api_name!r}"
         if label else "No label available")
        + "; confirm the provisioning name at Gate 2.",
    )


# ---------------------------------------------------------------------- #
# Plan
# ---------------------------------------------------------------------- #
def plan_field_addition(
    field_name: str,
    source_fields: Any,
    current_query: str | None = None,
    target_columns: list[str] | None = None,
    preview: Any | None = None,
) -> dict:
    """
    Validate a candidate source field and describe every change needed.

    Nothing is executed. The returned plan is shown to the user, and
    `requires_confirmation` stays True until they approve it.
    """
    fields = normalise_source_fields(source_fields)
    field = _find_field(fields, field_name)

    if field is None:
        close = [
            f["name"] for f in fields
            if field_name.lower().replace("_", "") in f["name"].lower().replace("_", "")
        ][:10]
        return {
            "ok": False,
            "error": f"{field_name!r} not found in source metadata.",
            "similar_fields": close,
        }

    exact_name = field["name"]
    target_col = exact_name.upper()
    issues: list[str] = []

    plan: dict[str, Any] = {
        "ok": True,
        "requires_confirmation": True,
        "source_field": {
            "name": exact_name,
            "label": field.get("label"),
            "object": field.get("parentObject"),
            "sf_type": field.get("type"),
            "pcType": field.get("pcType"),
            "precision": field.get("precision"),
            "scale": field.get("scale"),
            "is_calculated": field.get("isCalculated"),
        },
        "target_column": {
            "name": target_col,
            "suggested_type": suggest_snowflake_type(field),
            "exists": None,
        },
    }

    if target_columns is not None:
        upper_cols = {c.upper() for c in target_columns}
        exists = target_col in upper_cols
        plan["target_column"]["exists"] = exists
        if not exists:
            issues.append(
                f"Target column {target_col} does not exist in Snowflake. "
                "It must be added (Snowflake MCP add_columns) before the "
                "IICS target refresh will expose it."
            )

    if current_query is not None:
        try:
            plan["query"] = {
                "before": current_query,
                "after": add_field_to_query(current_query, exact_name),
            }
        except ValueError as exc:
            issues.append(str(exc))
            plan["query"] = {"before": current_query, "after": None}

    if preview is not None:
        plan["preview"] = summarise_preview(preview, exact_name)

    plan["issues"] = issues
    plan["next_steps"] = [
        "User confirms the field, target column name and type.",
        "If target column missing: add it in Snowflake (separate approval).",
        "Web UI: update source query, validate.",
        "Web UI: refresh target, map field, screenshot.",
        "User approves screenshot -> Web UI: save.",
    ]
    return plan
