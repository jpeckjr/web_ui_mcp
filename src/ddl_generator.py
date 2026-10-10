"""
DDL generator for the field-promotion workflow (phase 4).

Pure functions: no database or browser access. Claude gathers the inputs
from the other MCP servers and passes them in:

  * Informatica MCP  get_source_fields(connection, object, by="name")
        -> source_fields (field dicts or {"type","text"} wrappers)
  * Informatica MCP  preview_source_data(...)            (optional)
        -> preview, only used for the string-to-date rule
  * Snowflake MCP    describe_table("DB.SALESFORCE.<OBJECT>") for staging
        and provisioning -> staging_columns / provisioning_columns
        (the markdown table text, or a list of names / dicts)

Output: two `alter table` statements in the team format
(sql_formatting_standards.md: lowercase keywords and types, UPPER_SNAKE
names, leading commas, `;` on its own line, SCHEMA.TABLE with no database),
the per-column statements the Snowflake MCP needs (one column per call,
database added), and a Gate 2 review table.

Rules: docs/Field Promotion Workflow - Draft 2.3.md, "DDL rules".
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.field_planning import (
    normalise_source_fields,
    suggest_provisioning_name,
    summarise_preview,
)

MIN_DATE_SAMPLES = 5  # open question 7; default until decided


# ---------------------------------------------------------------------- #
# Existing table columns
# ---------------------------------------------------------------------- #
def parse_columns(raw: Any) -> list[dict]:
    """
    Normalise existing table columns to [{"name": ..., "type": ...}].

    Accepts the Snowflake MCP describe_table markdown table, its
    {"result": "..."} wrapper, a list of names, or a list of dicts.
    Types are rebuilt in lowercase team style (varchar(255), number(18,2),
    timestamp_ntz(9)); scale of timestamps is not in describe output and is
    assumed to be 9, which matches every table seen so far.
    """
    if raw is None:
        return []
    if isinstance(raw, dict) and "result" in raw:
        raw = raw["result"]
    if isinstance(raw, list):
        out = []
        for c in raw:
            if isinstance(c, str):
                out.append({"name": c.upper(), "type": None})
            elif isinstance(c, dict):
                name = c.get("name") or c.get("COLUMN_NAME")
                out.append({"name": str(name).upper(), "type": c.get("type")})
        return out

    text = str(raw)
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return []
    header = [h.strip().upper() for h in lines[0].strip().strip("|").split("|")]
    cols = []
    for line in lines[2:]:  # skip header and --- separator
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        row = dict(zip(header, cells))
        name = row.get("COLUMN_NAME")
        if not name:
            continue
        cols.append({"name": name.upper(), "type": _describe_type(row)})
    return cols


def _num(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "", "NULL") else None
    except ValueError:
        return None


def _describe_type(row: dict) -> str:
    dt = (row.get("DATA_TYPE") or "").upper()
    if dt in {"TEXT", "VARCHAR", "STRING"}:
        n = _num(row.get("MAX_LEN"))
        return f"varchar({n})" if n else "varchar"
    if dt in {"NUMBER", "DECIMAL", "NUMERIC"}:
        p, s = _num(row.get("PREC")), _num(row.get("SCALE"))
        return f"number({p},{s or 0})" if p else "number"
    if dt.startswith("TIMESTAMP"):
        return f"{dt.lower()}(9)"
    return dt.lower()


# ---------------------------------------------------------------------- #
# Types
# ---------------------------------------------------------------------- #
_SF_TEXT = {
    "string", "picklist", "multipicklist", "textarea", "email", "url",
    "phone", "combobox", "encryptedstring",
}


def staging_type(field: dict) -> tuple[str, str]:
    """
    Staging type for a new Salesforce field, following DEV_STAGING:
    ids 18 chars, text = Salesforce length, numbers keep precision/scale,
    checkbox = number(1,0), all dates = timestamp_ntz(9) (decided 8 Oct).
    Returns (type, basis).
    """
    sf = str(field.get("type") or "").lower()
    pc = str(field.get("pcType") or "").upper()
    p = int(field.get("precision") or 0)
    s = int(field.get("scale") or 0)

    if sf in {"id", "reference"}:
        return f"varchar({p or 18})", f"salesforce {sf}"
    if sf == "boolean":
        return "number(1,0)", "salesforce checkbox"
    if sf in {"date", "datetime"} or pc in {"DATE", "DATE/TIME", "DATETIME", "TIMESTAMP"}:
        return "timestamp_ntz(9)", "date rule (decided 8 Oct 2026)"
    if sf in {"double", "currency", "percent", "int", "long"} or pc in {
        "DECIMAL", "HIPRECDECIMAL", "DOUBLE", "INTEGER", "BIGINT", "NUMBER"
    }:
        if p:
            return f"number({min(p, 38)},{s})", f"salesforce {sf or pc.lower()}"
        return "number(38,0)", f"salesforce {sf or pc.lower()} (no precision)"
    if sf in _SF_TEXT or pc in {"STRING", "NSTRING", "TEXT", "NTEXT"}:
        return (f"varchar({p})" if p else "varchar"), f"salesforce {sf or pc.lower()}"
    return (f"varchar({p})" if p else "varchar"), f"unrecognised type {sf or pc!r}"


_DATE_FORMATS = [
    ("%m/%d/%y", "M/D/YY"), ("%m/%d/%Y", "M/D/YYYY"), ("%Y-%m-%d", "YYYY-MM-DD"),
]
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")


def detect_string_date(values: list[str]) -> dict | None:
    """Return {"type", "format"} if every non-empty sample is a date/timestamp."""
    vals = [v.strip() for v in values if v not in ("", None)]
    if len(vals) < MIN_DATE_SAMPLES:
        return None
    if all(_TS_RE.match(v) for v in vals):
        return {"type": "timestamp_ntz(9)", "format": "ISO timestamp"}
    for fmt, label in _DATE_FORMATS:
        try:
            for v in vals:
                datetime.strptime(v, fmt)
            return {"type": "date", "format": label}
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------- #
# DDL text
# ---------------------------------------------------------------------- #
def format_alter(schema: str, table: str, columns: list[tuple[str, str]],
                 comment: str) -> str:
    """alter table in the team format (sql_formatting_standards.md)."""
    lines = [f"-- {comment}", f"alter table {schema.upper()}.{table.upper()} add column"]
    for i, (name, typ) in enumerate(columns):
        lines.append(f"  {'' if i == 0 else ','}{name} {typ}")
    lines.append(";")
    return "\n".join(lines)


def apply_statements(database: str, schema: str, table: str,
                     columns: list[tuple[str, str]]) -> list[str]:
    """One fully qualified statement per column, for Snowflake MCP add_columns."""
    fq = f"{database.upper()}.{schema.upper()}.{table.upper()}"
    return [f"alter table {fq} add column {n} {t}" for n, t in columns]


# ---------------------------------------------------------------------- #
# Main entry
# ---------------------------------------------------------------------- #
def generate_ddl(
    object_name: str,
    field_names: list[str],
    source_fields: Any,
    staging_columns: Any,
    provisioning_columns: Any = None,
    preview: Any = None,
    ticket: str = "<ticket>",
    schema: str = "SALESFORCE",
    table: str | None = None,
    staging_database: str = "DEV_STAGING",
    provisioning_database: str = "DEV_PROVISIONING",
) -> dict:
    """
    Build staging and provisioning DDL for new fields on an existing object.

    Nothing is executed. Returns DDL text, per-column apply statements,
    a Gate 2 review table and everything that was skipped and why.
    """
    table = (table or object_name).upper()
    fields = normalise_source_fields(source_fields)
    by_name = {f["name"].lower(): f for f in fields}

    stg_cols = parse_columns(staging_columns)
    prov_cols = parse_columns(provisioning_columns) if provisioning_columns is not None else None

    if not stg_cols:
        return {
            "ok": False,
            "error": (
                f"Staging table {schema}.{table} has no columns in the input. "
                "Either describe_table was not passed, or the table does not "
                "exist, which is out of scope (existing objects only)."
            ),
        }
    if provisioning_columns is not None and not prov_cols:
        return {
            "ok": False,
            "error": f"Provisioning table {schema}.{table} not found; out of scope.",
        }

    stg_names = {c["name"] for c in stg_cols}
    prov_names = {c["name"] for c in prov_cols} if prov_cols is not None else set()
    stg_types = {c["name"]: c["type"] for c in stg_cols}

    review, not_found = [], []
    stg_add: list[tuple[str, str]] = []
    prov_add: list[tuple[str, str]] = []
    seen_prov: set[str] = set()

    for requested in field_names:
        f = by_name.get(requested.lower())
        if f is None:
            close = [
                x["name"] for x in fields
                if requested.lower().replace("_", "") in x["name"].lower().replace("_", "")
            ][:10]
            not_found.append({"requested": requested, "similar": close})
            continue

        api, label = f["name"], f.get("label")
        flags: list[str] = []

        # ---- staging ------------------------------------------------- #
        s_name = api.upper()
        s_type, s_basis = staging_type(f)
        if s_basis.startswith("unrecognised"):
            flags.append(f"Staging type guessed: {s_basis}.")
        s_exists = s_name in stg_names
        if s_exists:
            s_type = stg_types.get(s_name) or s_type
        else:
            stg_add.append((s_name, s_type))

        # ---- provisioning ------------------------------------------- #
        p_info = suggest_provisioning_name(api, label, object_name)
        p_name = p_info["name"]
        if p_info.get("needs_review"):
            flags.append(p_info.get("reason", "Provisioning name needs review."))

        p_type, p_basis = s_type, "same as staging"
        if preview is not None and s_type.startswith("varchar"):
            pv = summarise_preview(preview, api, max_samples=50)
            samples = [x.get(api, "") for x in pv.get("samples", [])]
            d = detect_string_date(samples)
            if d:
                p_type, p_basis = d["type"], f"string date ({d['format']}), confirm format"
                flags.append(f"Provisioning type changed to {p_type}: {d['format']}.")

        p_exists = None
        if prov_cols is not None:
            p_exists = p_name in prov_names
            if not p_exists and p_name not in seen_prov:
                prov_add.append((p_name, p_type))
                seen_prov.add(p_name)
            elif p_name in seen_prov:
                flags.append(f"Provisioning name {p_name} produced twice; rename one.")

        review.append({
            "source_field": api,
            "label": label,
            "salesforce_type": f.get("type"),
            "staging_column": s_name,
            "staging_type": s_type,
            "staging_status": "exists" if s_exists else "add",
            "provisioning_column": p_name,
            "provisioning_type": p_type,
            "provisioning_basis": f"{p_info['source']}; {p_basis}",
            "provisioning_status": (
                "not checked" if p_exists is None else ("exists" if p_exists else "add")
            ),
            "needs_review": bool(flags),
            "flags": flags,
        })

    comment = f"{ticket}: new fields from Salesforce {object_name}"
    out: dict[str, Any] = {
        "ok": True,
        "requires_confirmation": True,
        "object": object_name,
        "review": review,
        "not_found": not_found,
        "ddl": {},
        "apply": {},
    }
    if stg_add:
        out["ddl"]["staging"] = format_alter(schema, table, stg_add, comment)
        out["apply"]["staging"] = apply_statements(staging_database, schema, table, stg_add)
    if prov_add:
        out["ddl"]["provisioning"] = format_alter(schema, table, prov_add, comment)
        out["apply"]["provisioning"] = apply_statements(
            provisioning_database, schema, table, prov_add
        )
    if not stg_add and not prov_add:
        out["note"] = "Nothing to add: every requested field already exists or was not found."
    return out
