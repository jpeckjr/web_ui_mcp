import json

import pytest

from src.field_planning import (
    add_field_to_query,
    plan_field_addition,
    summarise_preview,
    suggest_snowflake_type,
)

FIELD = {
    "name": "Identity_Org_Id__c",
    "label": "Identity Org Id",
    "parentObject": "Contract",
    "type": "string",
    "pcType": "STRING",
    "precision": 255,
    "scale": 0,
    "isCalculated": False,
}
ID_FIELD = {"name": "Id", "pcType": "STRING", "precision": 18, "scale": 0}

# Shape returned by the Informatica MCP: list of {"type","text"} wrappers.
WRAPPED = [{"type": "text", "text": json.dumps(f)} for f in (ID_FIELD, FIELD)]


def test_add_field_single_line():
    q = "SELECT Id, Name FROM Contract WHERE IsDeleted = false"
    assert add_field_to_query(q, "Identity_Org_Id__c") == (
        "SELECT Id, Name, Identity_Org_Id__c FROM Contract WHERE IsDeleted = false"
    )


def test_add_field_multi_line_keeps_indent():
    q = "SELECT\n    Id,\n    Name\nFROM Contract"
    assert add_field_to_query(q, "X__c") == (
        "SELECT\n    Id,\n    Name,\n    X__c\nFROM Contract"
    )


def test_add_field_follows_leading_comma_style():
    # Shape of the real mt_stage_salesforce_contract query.
    q = "select Id ,StartDate ,EndDate,Current_Term__c, Status ,Take_Rate__c from Contract"
    assert add_field_to_query(q, "Identity_Org_Id__c") == (
        "select Id ,StartDate ,EndDate,Current_Term__c, Status ,Take_Rate__c "
        ",Identity_Org_Id__c from Contract"
    )


def test_add_field_duplicate_rejected():
    with pytest.raises(ValueError):
        add_field_to_query("SELECT Id, identity_org_id__c FROM Contract", "Identity_Org_Id__c")


# Cases taken from DEV_STAGING vs DEV_PROVISIONING .SALESFORCE.CONTRACT
PROVISIONING_CASES = [
    # api name, label, expected provisioning name, needs_review
    ("Id", "Contract ID", "CONTRACT_ID", False),
    ("Identity_Org_Id__c", "Identity Org Id", "IDENTITY_ORG_ID", False),
    ("Contract_Migration_ID__c", "Migration ID", "CONTRACT_MIGRATION_ID", False),
    ("Active_Subscription_Product_Sell_Group__c", "Active Subscription Product Sell Group",
     "ACTIVE_SUBSCRIPTION_PRODUCT_SELL_GROUP", False),
    ("AccountId", "Account ID", "ACCOUNT_ID", False),
    ("ActivatedDate", "Activated Date", "ACTIVATED_DATE", False),
    ("BillingMethod__c", "Billing Method", "BILLING_METHOD", False),
    ("ContractNumber", "Contract Number", "CONTRACT_NUMBER", False),
    ("StartDate", "Contract Start Date", "CONTRACT_START_DATE", False),
    ("Name", "Contract Name", "CONTRACT_NAME", False),
    ("NetRevenue__c", "Net Revenue", "NET_REVENUE", False),
    # Known exceptions: real names are CONTRACT_TERM / CONTRACT_TERM_TYPE,
    # so these must be flagged (or produce a name the user corrects at Gate 2).
    ("ContractTermText__c", "Contract Term", "CONTRACT_TERM_TEXT", True),
    ("Take_Rate__c", None, "TAKE_RATE", False),
]


def test_provisioning_names():
    from src.field_planning import suggest_provisioning_name
    for api, label, expected, review in PROVISIONING_CASES:
        out = suggest_provisioning_name(api, label, "Contract")
        assert out["name"] == expected, (api, out)
        assert out["needs_review"] is review, (api, out)


def test_type_suggestion():
    assert suggest_snowflake_type(FIELD) == "VARCHAR(255)"
    assert suggest_snowflake_type({"pcType": "HIPRECDECIMAL", "precision": 18, "scale": 2}) == "NUMBER(18,2)"


def test_preview_only_returns_candidate_column():
    preview = {
        "fieldName": ["Id", "BillingStreet", "Identity_Org_Id__c"],
        "rows": [
            {"values": ["a1", "123 Main", "ORG1"]},
            {"values": ["a2", "456 Elm", ""]},
        ],
    }
    s = summarise_preview(preview, "Identity_Org_Id__c")
    assert s["rows_populated"] == 1
    assert s["samples"] == [{"Id": "a1", "Identity_Org_Id__c": "ORG1"}]
    assert "123 Main" not in json.dumps(s)


def test_plan_flags_missing_target_column():
    plan = plan_field_addition(
        "identity_org_id__c",  # case-insensitive lookup
        WRAPPED,
        current_query="SELECT Id FROM Contract",
        target_columns=["ID"],
    )
    assert plan["ok"]
    assert plan["source_field"]["name"] == "Identity_Org_Id__c"
    assert plan["target_column"] == {
        "name": "IDENTITY_ORG_ID__C",
        "suggested_type": "VARCHAR(255)",
        "exists": False,
    }
    assert plan["query"]["after"] == "SELECT Id, Identity_Org_Id__c FROM Contract"
    assert any("does not exist" in i for i in plan["issues"])


def test_plan_unknown_field():
    plan = plan_field_addition("Identity_Org", WRAPPED)
    assert not plan["ok"]
    assert "Identity_Org_Id__c" in plan["similar_fields"]
