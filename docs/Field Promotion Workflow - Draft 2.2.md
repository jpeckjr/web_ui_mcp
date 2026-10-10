# Field Promotion Workflow

Design doc · Draft 2.2 · 8 Oct 2026 (incorporates Joe's edits from 10/7/26; scope limited to existing objects on 10/8/26)

An end-to-end workflow, with your approval at each gate, for adding new source fields to existing Snowflake staging and provisioning tables, updating the existing Informatica mapping task in DEV, and promoting everything to PROD.

- **Owner:** Joe Peck
- **Status:** Design only
- **First build:** DDL generation

## Scope

- **In scope:** adding new fields to existing objects only:
  - Snowflake staging and provisioning tables that already exist;
  - IICS mapping tasks that already exist;
  - IICS connections that already exist.
- **Out of scope:**
  - new source objects;
  - new Snowflake tables or schemas;
  - new IICS mapping tasks, since each new object needs its own task;
  - creating or changing IICS connections.

A ticket that needs a new object is handled outside this workflow. Because every table already exists, the DDL this workflow generates is always `alter table … add column`.

If phase 4 finds that a target table doesn't exist, the run stops. It records the ticket as out of scope rather than generating `create table`.

---

## How it runs

Claude runs the workflow. Each system has its own MCP server, and MCP servers can't call each other, so Claude passes every hand-off between them (asset path, field list, DDL text).

Each ticket has a **run file**. It records what each phase took in, what it produced, and who approved it.

### Gates can wait for days

A run can stop at any gate and resume days later in a new session. To support that:

- **Saved after every phase.** The run file is written to disk after each phase. It is the only memory between sessions, so nothing depends on chat history.
- **Resumed from the run file.** A resume starts by reading `runs/<ticket>.json` and picking up at the recorded `phase`.
- **Re-checked before continuing.** Things can change while a gate waits, so the run checks them again:
  - Describe the Snowflake tables again to confirm the columns still match the approved DDL. They might have been added by someone else, or changed.
  - Compare the mapping task's `updateTime` from `search_objects` with the value recorded at the gate. If it changed, someone else edited the task, and the run stops and tells you.
  - The IICS browser session will probably have expired, so the Web UI MCP logs in again before any UI step.
- **Explicit approvals only.** An approval is recorded only when you give it. The run never moves past a gate on a timer.

### MCP servers

| MCP server | Used for | Status |
| --- | --- | --- |
| Atlassian | Read the Jira ticket requirements | Available |
| Informatica (dev) | Source metadata, preview, asset lookup, mapping task runs, export | Available; no export tool yet |
| Informatica (prod) | Import, mapping task runs, run status | Available; no import tool yet |
| Snowflake (dev / prod) | Describe tables, run DDL, check loaded data | Available |
| Web UI (Playwright MCP) | Quick update of an existing mapping task: refresh the source and target in the IICS UI and map the new fields | Steps 1–9 built; steps 10–30 need selectors |
| GitHub | Add the final DDL files to the team's DDL repo | Not set up |

### Run file (one per ticket)

```
runs/DP-4024.json
{
  "ticket": "DP-4024",
  "phase": "ddl_review",
  "source": { "connection": "cn_app_salesforce", "object": "Contract" },
  "asset_path": "staging/salesforce/mt_stage_salesforce_contract",
  "asset_update_time_at_gate": "2026-10-01T16:41:57Z",
  "tables": {
    "staging":      "SALESFORCE.CONTRACT",
    "provisioning": "SALESFORCE.CONTRACT"
  },
  "fields": [ { "source": "Identity_Org_Id__c", "pcType": "STRING", "precision": 255,
                "staging": "IDENTITY_ORG_ID__C varchar(255)",
                "provisioning": "IDENTITY_ORG_ID__C varchar(255)" } ],
  "ddl": { "staging": "...", "provisioning": "..." },
  "approvals": [ { "gate": "G2", "by": "jbpeck", "at": "..." } ],
  "history": [ { "phase": "source_metadata", "completed_at": "..." } ]
}
```

---

## Source metadata

Source metadata comes through the **IICS connection that the mapping task already uses**. In the UI, IICS loads a source's metadata when the connection is loaded in the mapping's Source transformation, and picking up new fields needs a manual refresh there. That manual refresh is why the Web UI MCP is part of the workflow.

Salesforce is the main source and the reference example. IICS DEV has Salesforce connections for the dev environments and one for prod.

When a field such as `Identity_Org_Id__c` is looked up, the request goes through the connection to Salesforce and returns the field's name and data type. The workflow uses two calls:

- **`get_source_fields`** returns the field name, `pcType`, precision and scale. These set the staging type.
- **`preview_source_data`** returns field names and labels, which are used to match the fields named in the Jira ticket.

**Purpose limit.** The source data is pulled only to carry field names and types through the process. No other analysis of Salesforce data is done.

The one exception is the provisioning date rule. If a new field is a string in staging, its own preview values may be checked to see whether they are dates (see Provisioning rules below). Only those values for the new fields are read. No other column's data is kept in the run file or shown at a gate.

---

## Phases

### 1. Read the Jira ticket — Jira

Pull the ticket and extract the source system, source object, requested fields, target table, and any notes on types or naming. You and the reporter agree on the source object and fields beforehand and state them clearly in the ticket.

- **Tool:** Atlassian MCP `jira_get_issue`
- **Gate 1:** You confirm the extracted requirements.

### 2. Identify source and connection — Manual for now

Use whichever connection the existing mapping task already uses, for example `cn_app_salesforce` and `Contract`. No connections are created or changed. Several Salesforce connections exist (`cn_app_salesforce` through `_5`), so the mapping task, not a naming guess, decides which one to use.

- **Now:** You confirm the connection, using the ticket and the mapping task.
- **Later:** Read the connection from the mapping task's definition automatically. See Gaps.

### 3. Get source metadata — Dev

Preview the object and fetch its field definitions. Compare what exists with the requirements: confirm each requested field exists, and suggest close matches for any that don't.

- **Tools:** Informatica dev `preview_source_data`, `get_source_fields`

### 4. Generate staging and provisioning DDL — Dev · First build

Describe the existing staging and provisioning tables. For each requested field that isn't already in the table, generate `alter table … add column`. If either table doesn't exist, stop: that is out of scope. The DDL follows `sql_formatting_standards.md`; see DDL rules below.

- **Tools:** Snowflake dev `describe_table`, `get_table_ddl`
- **Gate 2 (first review):** You review the source fields next to the proposed Snowflake columns, types and exact DDL.

### 5. Apply DDL in DEV — Dev

Run the approved DDL, then describe the tables again to confirm the columns exist with the expected types.

- **Tools:** Snowflake dev `add_columns` or `run_query`

### 6. Update the mapping task in IICS DEV — Dev

These steps were tested by hand. They now need to become a repeatable process, with a catalogue of the UI elements involved and a reliable selector for each.

Look up the asset path with Informatica dev `search_objects`, then work through the UI:

| Steps | Action | Status |
| --- | --- | --- |
| 1–9 | Explore → project → folder → find task → Edit, driven by the asset path | Built |
| 10–17 | Add the fields to `Query (p_source_object)`, then Validate and OK. The planning step supplies the new query text, so the query is never read from a screenshot | Needs selectors |
| 18–24 | Refresh the target (`#refreshCacheBtn`), search for the table and select it again | Needs selectors |
| 25–27 | Map each source field to its target column and take a screenshot | Needs selectors |

**Selector catalogue to collect** (outerHTML from the IICS editor):

- Sources tab, and the `Query (p_source_object)` text area
- Validate button, and the OK button on the validation dialog
- Targets tab, the target object link, and the search box and Search button in the object picker
- Input Parameters tab, one source-field row, and one target Expression cell

**Gate 3:** You approve the mapping screenshot. Then the task is saved (step 30).

### 7. Test run in DEV — Dev

Run the mapping task and poll its status. Then check the new columns in DEV staging: row count, and null rate for each new field.

- **Tools:** Informatica dev `run_mapping_task`, `get_mapping_task_status`, `get_activity_log`; Snowflake dev `run_query`
- **Gate 4:** You approve the DEV result before anything changes in PROD.

### 8. Apply DDL in PROD — Prod

Run the same approved DDL in PROD and confirm the columns with describe. This is the first write to PROD, so the exact statements are shown again just before they run, even though Gate 4 has passed.

- **Tools:** Snowflake prod `add_columns` or `run_query`

### 9. Export from DEV, import to PROD — Dev → Prod · New tools

Export the changed assets from IICS DEV as a ZIP file and import it into IICS PROD with the IICS v3 REST API:

1. Find the asset IDs with `GET /public/core/v3/objects` (already wrapped by `search_objects`).
2. Start the export: `POST /public/core/v3/export`, then poll the export job's status.
3. Download the ZIP: `GET /public/core/v3/export/<id>/package`.
4. Upload it to PROD: `POST /public/core/v3/import/package` (multipart).
5. Start the import job: `POST /public/core/v3/import/<id>`, then poll its status.

This needs two new MCP tools: `export_assets` on Informatica dev and `import_package` on Informatica prod.

Before importing, compare the assets in the ZIP with what was approved at Gate 3, so nothing extra reaches PROD. The exact request fields, such as the conflict-resolution settings, need confirming against the docs listed under References.

### 10. Run in PROD and check data — Prod

Run the mapping task in PROD. Then check the load: row count against the previous run, null rate for each new field, and any rejected rows.

- **Tools:** Informatica prod `run_mapping_task`, `get_mapping_task_status`; Snowflake prod `run_query`, `table_write_activity`
- **Gate 5:** You approve the PROD load.

### 11. Commit DDL to GitHub — GitHub · Not set up

Add the final DDL files to the repo your team uses to track DDL, on a branch, with a PR that references the Jira ticket. Until a GitHub MCP is connected, the run writes the files for you to commit yourself.

- **Gate 6:** You review the diff or PR. The workflow is then marked complete and the run file is closed.

---

## DDL rules

All generated DDL follows `sql_formatting_standards.md`. The rules that apply to `alter table`:

| Standard | Applied as |
| --- | --- |
| #1 2-space indent, no tabs | Column lines indented 2 spaces |
| #2 Lowercase keywords and functions | `alter table`, `add column`, and data types such as `varchar(255)`, `number(18,2)`, `date` |
| #4 / #16 Match source casing; `UPPER_SNAKE` fields | Database, schema, table and column names in upper case |
| #6 Leading commas | Second and later columns start with `,` |
| #7 Trailing semicolon on its own line | `;` alone on the last line |
| #15 Comments | One `--` line above each statement with the ticket and field source |
| #18 Single space between name and type | No aligning of data types |

### Statement shape

One statement per table, listing every new column for that table:

```sql
-- DP-4024: new fields from Salesforce Contract
alter table SALESFORCE.CONTRACT add column
  IDENTITY_ORG_ID__C varchar(255)
;
```

With several fields:

```sql
-- DP-4024: new fields from Salesforce Contract
alter table SALESFORCE.CONTRACT add column
  IDENTITY_ORG_ID__C varchar(255)
  ,BILLING_FREQUENCY_ANNUAL_MULTIPLIER__C number(18,2)
;
```

The database prefix is left out so the same statement runs in DEV and PROD. Each environment sets its database in its session context. Whether to fully qualify names is listed under Open questions.

Snowflake accepts several columns in one `add column` clause. The generator checks `describe_table` first and leaves out any column that already exists.
### Staging

- **Name:** the source field name in upper case, keeping the underscores and the Salesforce suffix. `Identity_Org_Id__c` becomes `IDENTITY_ORG_ID__C`.
- **Type:** from the source `pcType`, precision and scale:

| Source pcType | Staging type (proposed) |
| --- | --- |
| `STRING`, `NSTRING`, `TEXT` | `varchar(precision)` |
| `DECIMAL`, `HIPRECDECIMAL`, `DOUBLE` | `number(precision,scale)`, with precision capped at 38 |
| `INTEGER`, `BIGINT` | `number(38,0)` |
| `DATE` | `date` |
| `DATE/TIME`, `TIMESTAMP` | `timestamp_ntz(9)` |

This mapping is a starting point. The generator compares it with how existing columns in the same staging table are typed. Where they differ, the table's existing convention wins, and the plan notes the difference at Gate 2.

### Provisioning

Match the staging column, except when the staging type is a string and the new field's sample values are dates or timestamps. Then use the real type:

| New field's sample values (all non-empty) | Provisioning type |
| --- | --- |
| `1/1/26`, `01/01/2026`, `2026-01-01` | `date` |
| `2026-01-01T15:18:19.000Z`, `2026-01-01 15:18:19` | `timestamp_ntz(9)` |
| Mixed, empty, or fewer than the minimum number of samples | Keep the staging type and flag it at Gate 2 |

Two-digit years and the order of month and day are ambiguous: `1/2/26` could be January 2 or February 1. The plan states the format it assumed (for example `M/D/YY`) so you can confirm it at Gate 2.

---

## Gaps to close

| Gap | Needed for | Approach |
| --- | --- | --- |
| DDL generator | Phase 4 | Python module plus a Web UI MCP tool, extending `field_planning.py`; produces `alter table` in the standard format. **First build.** |
| Run file, resume and re-checks | All gates | One JSON file per ticket under `runs/`, written after every phase. Re-checks on resume as described in "Gates can wait for days". |
| IICS editor selectors | Phase 6, steps 10–30 | Collect the outerHTML listed in the phase 6 selector catalogue. |
| Export and import tools | Phase 9 | Add `export_assets` (dev) and `import_package` (prod) to the Informatica MCP using the v3 API. |
| Mapping task → connection lookup | Automating phase 2 | Read the connection from the v2 task or mapping definition. Needs investigation. |
| GitHub MCP | Phase 11 | Connect later. Until then, write the files for you to commit by hand. |
| Snowflake `search_objects` timeouts | Phase 4 | Use `describe_table` on known table names instead of an account-wide search. |

---

## Open questions

1. **Provisioning load.** What loads the provisioning tables from staging? If it's a separate mapping task or a SQL view, it also needs updating, especially for any string-to-date cast. It isn't in the workflow yet.
2. **Table names.** What are the database and schema names for staging and provisioning in DEV and PROD?
3. **Fully qualified names.** Should the DDL leave out the database prefix (`SALESFORCE.CONTRACT`) so one file runs in both environments, or name it in full (`DEV_STAGING.SALESFORCE.CONTRACT`) with one file per environment? The answer also decides the file layout in the DDL repo.
4. **Duplicate step.** "Run the mapping tasks in PROD" appears twice in the original list. Is the second one a taskflow run, or a duplicate?
5. **Export scope.** Should the export contain only the mapping task, or the mapping and other dependencies as well?
6. **Minimum sample size.** How many non-empty values must a field have before a string is retyped as a date? I suggest 5.
7. **Phase 6 note.** The draft has an unfinished note in phase 6: "the naming conventions from the existing …". What did you mean to add there?
---

## References

- Informatica: [Export and import](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/export-and-import.html)
- Informatica: [Exporting objects](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/exporting-objects.html)
- Informatica: [Importing objects](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/importing-objects.html)
- Informatica: [Starting an import job](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/importing-objects/starting-an-import-job.html)
- `sql_formatting_standards.md` (team SQL and DDL formatting standards)
- Checked through the connected `informatica` MCP server on 6 Oct 2026: `search_objects`, `get_source_fields`, `preview_source_data`, `get_tasks`.
