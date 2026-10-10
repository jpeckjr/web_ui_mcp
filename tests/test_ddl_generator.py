"""Tests for src/ddl_generator.py, using shapes seen in DEV on 2026-10-08."""

import json

from src.ddl_generator import (
    detect_string_date,
    format_alter,
    generate_ddl,
    parse_columns,
    staging_type,
)

# Abbreviated Snowflake MCP describe_table output (same layout as the real one).
STAGING_DESCRIBE = """| POS | COLUMN_NAME | DATA_TYPE | MAX_LEN | PREC | SCALE | IS_NULLABLE | COLUMN_DEFAULT | COMMENT |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | LOAD_TIMESTAMP | TIMESTAMP_TZ | NULL | NULL | NULL | YES | NULL | NULL |
| 2 | ID | TEXT | 18 | NULL | NULL | NO | NULL | NULL |
| 31 | TAKE_RATE__C | NUMBER | NULL | 18 | 2 | YES | NULL | NULL |
| 34 | STATUS | TEXT | 255 | NULL | NULL | YES | NULL | NULL |
"""
PROV_DESCRIBE = """| POS | COLUMN_NAME | DATA_TYPE | MAX_LEN | PREC | SCALE | IS_NULLABLE | COLUMN_DEFAULT | COMMENT |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | CONTRACT_ID | TEXT | 18 | NULL | NULL | NO | NULL | NULL |
| 33 | TAKE_RATE | NUMBER | NULL | 18 | 2 | YES | NULL | NULL |
"""


def field(name, label, sf_type, pc, precision, scale=0):
    return {"name": name, "label": label, "type": sf_type, "pcType": pc,
            "precision": precision, "scale": scale, "parentObject": "Contract"}


SOURCE = [
    {"type": "text", "text": json.dumps(f)} for f in [
        field("Id", "Contract ID", "id", "STRING", 18),
        field("Identity_Org_Id__c", "Identity Org Id", "string", "STRING", 255),
        field("Take_Rate__c", "Take Rate", "percent", "HIPRECDECIMAL", 18, 2),
        field("Renewal_Date__c", "Renewal Date", "date", "DATE", 10),
        field("Is_Active__c", "Is Active", "boolean", "STRING", 5),
        field("NetRevenue__c", "Net Revenue", "currency", "HIPRECDECIMAL", 18, 2),
    ]
]


def test_parse_describe_markdown():
    cols = parse_columns({"result": STAGING_DESCRIBE})
    assert [c["name"] for c in cols] == ["LOAD_TIMESTAMP", "ID", "TAKE_RATE__C", "STATUS"]
    assert cols[0]["type"] == "timestamp_tz(9)"
    assert cols[1]["type"] == "varchar(18)"
    assert cols[2]["type"] == "number(18,2)"


def test_staging_types_follow_dev_conventions():
    assert staging_type(field("A", "", "string", "STRING", 255))[0] == "varchar(255)"
    assert staging_type(field("A", "", "reference", "STRING", 18))[0] == "varchar(18)"
    assert staging_type(field("A", "", "boolean", "STRING", 5))[0] == "number(1,0)"
    assert staging_type(field("A", "", "date", "DATE", 10))[0] == "timestamp_ntz(9)"
    assert staging_type(field("A", "", "datetime", "DATE/TIME", 29))[0] == "timestamp_ntz(9)"
    assert staging_type(field("A", "", "currency", "HIPRECDECIMAL", 18, 2))[0] == "number(18,2)"


def test_format_matches_team_standard():
    ddl = format_alter("salesforce", "contract",
                       [("A__C", "varchar(255)"), ("B__C", "number(18,2)")], "T-1: test")
    assert ddl == (
        "-- T-1: test\n"
        "alter table SALESFORCE.CONTRACT add column\n"
        "  A__C varchar(255)\n"
        "  ,B__C number(18,2)\n"
        ";"
    )


def test_generate_ddl_end_to_end():
    out = generate_ddl(
        object_name="Contract",
        field_names=["Identity_Org_Id__c", "Take_Rate__c", "Renewal_Date__c",
                     "Is_Active__c", "NetRevenue__c", "Not_A_Field__c"],
        source_fields=SOURCE,
        staging_columns={"result": STAGING_DESCRIBE},
        provisioning_columns={"result": PROV_DESCRIBE},
        ticket="T-1",
    )
    assert out["ok"]
    # Take_Rate__c already exists in both tables -> skipped.
    assert out["ddl"]["staging"] == (
        "-- T-1: new fields from Salesforce Contract\n"
        "alter table SALESFORCE.CONTRACT add column\n"
        "  IDENTITY_ORG_ID__C varchar(255)\n"
        "  ,RENEWAL_DATE__C timestamp_ntz(9)\n"
        "  ,IS_ACTIVE__C number(1,0)\n"
        "  ,NETREVENUE__C number(18,2)\n"
        ";"
    )
    assert out["ddl"]["provisioning"] == (
        "-- T-1: new fields from Salesforce Contract\n"
        "alter table SALESFORCE.CONTRACT add column\n"
        "  IDENTITY_ORG_ID varchar(255)\n"
        "  ,RENEWAL_DATE timestamp_ntz(9)\n"
        "  ,IS_ACTIVE number(1,0)\n"
        "  ,NET_REVENUE number(18,2)\n"
        ";"
    )
    assert out["apply"]["staging"][0] == (
        "alter table DEV_STAGING.SALESFORCE.CONTRACT add column IDENTITY_ORG_ID__C varchar(255)"
    )
    assert out["apply"]["provisioning"][-1] == (
        "alter table DEV_PROVISIONING.SALESFORCE.CONTRACT add column NET_REVENUE number(18,2)"
    )
    status = {r["source_field"]: (r["staging_status"], r["provisioning_status"])
              for r in out["review"]}
    assert status["Take_Rate__c"] == ("exists", "exists")
    assert out["not_found"][0]["requested"] == "Not_A_Field__c"


def test_missing_table_is_out_of_scope():
    out = generate_ddl("Contract", ["Identity_Org_Id__c"], SOURCE, staging_columns="")
    assert not out["ok"]


def test_string_date_detection():
    assert detect_string_date(["1/1/26", "2/3/26", "12/31/25", "6/7/26", "1/9/26"]) == {
        "type": "date", "format": "M/D/YY"}
    assert detect_string_date(["2026-01-01T15:18:19.000Z"] * 5)["type"] == "timestamp_ntz(9)"
    assert detect_string_date(["1/1/26", "x", "1/1/26", "1/1/26", "1/1/26"]) is None
    assert detect_string_date(["1/1/26"]) is None  # too few samples
