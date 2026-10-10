# Field Promotion Workflow

Design doc · Draft 2.3 · 8 Oct 2026 (Draft 2.2 plus results of the 8 Oct DEV test run)

An end-to-end workflow, with your approval at each gate, for adding new source fields to existing Snowflake staging and provisioning tables, updating the existing Informatica mapping task in DEV, and promoting everything to PROD.

- **Owner:** Joe Peck
- **Status:** IICS DEV update (phase 6) built and tested end to end; other phases still design
- **Next build:** DDL generator (phase 4), then the workflow skill

**What changed since Draft 2.2**

- Phase 6 is built. Every selector it uses is confirmed against IICS DEV.
- IICS maps a new field automatically when the source and target names match, so the drag-and-drop step is usually skipped.
- `open_mapping_task` now checks that it opened the right task by comparing the asset ID in the editor URL.
- Staging tables are fully reloaded on every run, so new columns need no backfill.
- The DDL rules now follow the column types actually used in `DEV_STAGING`.
- New sections cover rollback and the browser session.

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

Claude runs the workflow. Each system has its own MCP server, and MCP servers can't call each other, so Claude passes every hand-off between them (asset path, asset ID, field list, DDL text).

Each ticket has a **run file**. It records what each phase took in, what it produced, and who approved it.

### Gates can wait for days

A run can stop at any gate and resume days later in a new session. To support that:

- **Saved after every phase.** The run file is written to disk after each phase. It is the only memory between sessions, so nothing depends on chat history.
- **Resumed from the run file.** A resume starts by reading `runs/<ticket>.json` and picking up at the recorded `phase`.
- **Re-checked before continuing.** Things can change while a gate waits, so the run checks them again:
  - Describe the Snowflake tables again to confirm the columns still match the approved DDL.
  - Compare the mapping task's `updateTime` from `search_objects` with the value recorded at the gate. If it changed, someone else edited the task, and the run stops and tells you.
  - The IICS login will probably have expired. `open_mapping_task` detects this and asks you to sign in (see Browser session).
- **Explicit approvals only.** An approval is recorded only when you give it. The run never moves past a gate on a timer.

### Browser session

The Web UI MCP drives its own browser window, separate from your normal browser. Confirmed behaviour:

- **Minimize the window; don't close it.** If you close it, the next tool call opens a fresh, blank window and has to load IICS again. Nothing in IICS is affected either way.
- **Logins are kept** in the browser profile (`browser_profile/`) until IICS expires the session. After that, the IICS login screen appears and you sign in once in that window.
- **IICS opens automatically.** If the window isn't on IICS, `open_mapping_task` loads it first. If a login is needed, it stops with "IICS is not loaded yet… sign in, then run this tool again".
- **Loading can be slow.** A saved login sometimes takes more than 40 seconds to load. `open_informatica` then returns `ready: false`. Calling it again is enough.
- **`browser_profile/` contains your IICS session.** Keep it out of git and don't share it.

### MCP servers

| MCP server | Used for | Status |
| --- | --- | --- |
| Atlassian | Read the Jira ticket requirements | Available |
| Informatica (dev) | Source metadata, preview, asset lookup (`search_objects`), task IDs (`get_tasks`), runs and run history | Available; no export tool yet |
| Informatica (prod) | Import, mapping task runs, run status | Available; no import tool yet |
| Snowflake (dev / prod) | Describe tables, add columns, read-only queries for data checks | Available. `add_columns` adds **one column per call** and cannot drop columns |
| Web UI (Playwright MCP) | Update an existing mapping task in the IICS UI | **Built and tested in DEV** (see phase 6) |
| GitHub | Add the final DDL files to the team's DDL repo | Not set up |

### Run file (one per ticket)

```
runs/<ticket>.json
{
  "ticket": "<ticket>",
  "phase": "ddl_review",
  "source": { "connection": "cn_app_salesforce", "object": "Contract" },
  "asset_path": "staging/salesforce/mt_stage_salesforce_contract",
  "asset_id_dev": "40I6QamRUa7jvIzYGBnYYL",
  "task_id_v2_dev": "001K5P0Z0000000001DE",
  "asset_update_time_at_gate": "2026-10-08T18:54:23Z",
  "tables": {
    "staging":      "DEV_STAGING.SALESFORCE.CONTRACT",
    "provisioning": "<to confirm>"
  },
  "fields": [ { "source": "Identity_Org_Id__c", "pcType": "STRING", "precision": 255,
                "staging": "IDENTITY_ORG_ID__C varchar(255)",
                "provisioning": "IDENTITY_ORG_ID__C varchar(255)" } ],
  "ddl": { "staging": "...", "provisioning": "..." },
  "baseline": { "row_count": 195967, "last_load": "2026-09-25" },
  "approvals": [ { "gate": "G2", "by": "jbpeck", "at": "..." } ],
  "history": [ { "phase": "source_metadata", "completed_at": "..." } ]
}
```

The example values come from the 8 Oct DEV test. `asset_id` and `task_id_v2` are different in DEV and PROD, so each environment is recorded separately.

---

## Source metadata

Source metadata comes through the **IICS connection that the mapping task already uses**. In the UI, IICS loads a source's metadata when the connection is loaded in the mapping's Source transformation, and picking up new fields needs a manual refresh there. That manual refresh is why the Web UI MCP is part of the workflow.

Salesforce is the main source and the reference example. IICS DEV has Salesforce connections for the dev environments and one for prod.

When a field such as `Identity_Org_Id__c` is looked up, the request goes through the connection to Salesforce and returns the field's name and data type. The workflow uses two calls:

- **`get_source_fields`** returns the field name, `pcType`, precision and scale. These set the staging type.
- **`preview_source_data`** returns field names and labels, which are used to match the fields named in the Jira ticket.

**Purpose limit.** The source data is pulled only to carry field names and types through the process. No other analysis of Salesforce data is done.

The one exception is the provisioning date rule. If a new field is a string in staging, its own preview values may be checked to see whether they are dates (see Provisioning below). Only those values for the new fields are read. No other column's data is kept in the run file or shown at a gate.

---

## Phases

### 1. Read the Jira ticket — Jira

Pull the ticket and extract the source system, source object, requested fields, target table, and any notes on types or naming. You and the reporter agree on the source object and fields beforehand and state them clearly in the ticket.

- **Tool:** Atlassian MCP `jira_get_issue`
- **Gate 1:** You confirm the extracted requirements.

### 2. Identify source and connection — Manual for now

Use whichever connection the existing mapping task already uses. No connections are created or changed. Several Salesforce connections exist (`cn_app_salesforce` through `_5`), so the mapping task, not a naming guess, decides which one to use.

**Confirmed in the test:** the mapping task editor shows both connections:

- **Sources tab:** source connection, for example `cn_app_salesforce (Salesforce)`.
- **Targets tab:** target connection and object, for example `cn_snf_corpsys.staging.salesforce (Snowflake Data Cloud)` and `CONTRACT`.

Reading these from the editor is the simplest way to automate this phase. See Gaps.

### 3. Get source metadata — Dev

Preview the object and fetch its field definitions. Compare what exists with the requirements: confirm each requested field exists, and suggest close matches for any that don't.

- **Tools:** Informatica dev `preview_source_data`, `get_source_fields`

### 4. Generate staging and provisioning DDL — Dev · Next build

Describe the existing staging and provisioning tables. For each requested field that isn't already in the table, generate `alter table … add column`. If either table doesn't exist, stop: that is out of scope. The DDL follows `sql_formatting_standards.md` and the type conventions under DDL rules.

- **Tools:** Snowflake dev `describe_table`, `get_table_ddl`
- **Gate 2 (first review):** You review the source fields next to the proposed Snowflake columns, types and exact DDL.

### 5. Apply DDL in DEV — Dev

Run the approved DDL, then describe the tables again to confirm the columns exist with the expected types.

- **Tool:** Snowflake dev `add_columns`, previewed first (`confirm=false`), then applied (`confirm=true`).
- **One column per call.** `add_columns` can't parse Snowflake's multi-column `add column`. The DDL file keeps the standard multi-column statement, and the workflow applies it one column at a time.
- **New columns go at the end of the table.** That's normal for `alter table … add column`.

### 6. Update the mapping task in IICS DEV — Dev · Built and tested

Look up the task with Informatica dev `search_objects` (`type=='MTT' and location=='<project>/<folder>'`), then call the Web UI MCP tools in order. Each tool is limited to one job and reports what it did.

| Steps | Tool | What it does | Changes the task? |
| --- | --- | --- | --- |
| 1–9 | `open_mapping_task(asset_path, expected_asset_id)` | Loads IICS if needed, goes to Explore → All Projects → project → folder → task → **Edit**, then checks the asset ID in the editor URL | No |
| 10–11 | `read_source_query()` | Sources tab → query window → reads the full query → **Cancel** | No |
| 12–17 | `update_source_query(fields, dry_run)` | Adds fields to the end of the query in its own comma style → **Validate** → **OK** only on "The custom query is valid."; any other result cancels | Only with `dry_run=false` (not saved) |
| 18–24 | `refresh_target(target_object)` | Targets tab → target object picker → refresh → search → select the single exact match → **OK** | Refreshes target metadata (not saved) |
| 25–27 | `verify_mapping(fields, screenshot_path)` | Input Parameters tab → reads each target field's type and Expression; screenshot for Gate 3 | No |
| 30 | `save_mapping_task(expected_task, confirm=true)` | Clicks **Save**, never Run; refuses without `confirm=true` or if a different task is open | **Yes**, only after Gate 3 |

**Gate 3:** you review the `verify_mapping` result and screenshot, then approve the save.

After saving, `search_objects` confirms that the task's `updateTime` changed. Filter by `location` or `tag`: filters on `updateTime` returned HTTP 500 in DEV.

#### Confirmed behaviour

- **Automatic mapping.** After the target refresh, IICS mapped `Identity_Org_Id__c` → `IDENTITY_ORG_ID__C` by itself, matching names regardless of case. Both sides showed "39 of 39 mapped". Your step 26 (drag the field) is only needed when the source and target names differ. In that case `verify_mapping` reports the field as unmapped, and you map it by hand; dragging isn't automated.
- **Asset-ID check.** The editor URL is `…/mct/<v3 asset id>/edit`. `open_mapping_task` compares that ID with the one from `search_objects` and stops before any edit if they differ.
- **Plain text box.** The query window ("Edit Custom Query") uses a standard text box, so the full query is read and written directly. No screenshots or dragging the box larger (your old steps 12–14).
- **Opening tabs marks the task as changed.** Even without edits, the editor shows the unsaved-changes dot and an active Save button. If you close without approving, choose **not** to save.
- **Loading takes time.** Each tab and the target picker take about 10–20 seconds to load, and Validate takes a few seconds. The tools wait up to 45 seconds per step.

#### Selector catalogue (confirmed in IICS DEV, 8 Oct 2026)

| Element | Selector or check |
| --- | --- |
| Explore rows | `a.iics-explore__table__name__text`; arrival confirmed by URL change plus a heading line `<name> (<count>)` |
| Editor title | `.infaPanelTitle-name.page-title` |
| Tabs | `text="Sources"`, `text="Targets"`, `text="Input Parameters"` |
| Query link (opens the query window) | The query text on the Sources tab, `text=/^\s*select\s/i` |
| Query text box | `textarea` (visible) |
| Validate / result | `text="Validate"`; success text "The custom query is valid." |
| Target object link | `text="<TARGET_OBJECT>"` on the Targets tab |
| Target picker | Title "Select Target Object"; refresh `#refreshCacheBtn`; search `input[placeholder="Name, Label, Description, Type"]` + `text="Search"`; result "Displaying N matching object(s)" |
| Picker result row | Innermost table row with a radio button (the picker nests tables) |
| Field mapping rows | `<tr>` with cells Name, Type, Expression; counts "Target Fields: (N of N mapped)" |
| OK / Cancel / Save | `text="OK"`, `text="Cancel"`, `text="Save"` (visible). The general `click_element` tool refuses Run, Delete, Publish and Save |

### 7. Test run in DEV — Dev

1. **Baseline:** row count, non-null count of each new field, and last `LOAD_TIMESTAMP` in DEV staging.
2. **Run:** `run_mapping_task` with the task's **v2** ID, found with `get_tasks(MTT)`. The v3 asset ID doesn't work here.
3. **Poll** with `list_running_jobs` until the task is no longer listed, then read the result with `get_activity_log(task_id=…, row_limit=1)`.
4. **Check** DEV staging: row count against rows written, and for each new field the non-null count, null %, distinct values, and longest value against the column size.

- **Gate 4:** You approve the DEV result before anything changes in PROD.

**Confirmed in the test:**

- **Duration.** A run of `mt_stage_salesforce_contract` took about 2 minutes 40 seconds for about 197k rows.
- **Account.** Runs started through the MCP show `corporatereporting@…` as the starting account.
- **Full reload.** Every row had the same `LOAD_TIMESTAMP` afterwards, and the table's row count equalled the rows written. The task empties and reloads the table each run, so a new column is filled for every existing record straight away and needs no backfill.
- **Schedule.** The task also runs from the taskflow `tf_stage_salesforce_daily_night`, so a change to the DEV task is picked up by the next nightly run even if nobody runs it by hand.

### 8. Apply DDL in PROD — Prod

Run the same approved DDL in PROD and confirm the columns with describe. This is the first write to PROD, so the exact statements are shown again just before they run, even though Gate 4 has passed.

- **Tools:** Snowflake prod `add_columns` (preview, then apply, one column per call)
- **Order matters.** The PROD column must exist **before** the updated mapping task reaches PROD. Otherwise the PROD run fails, because the task maps to a column that isn't there. This also applies to anyone migrating the DEV task by other means.

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

Run the mapping task in PROD. Then check the load: row count against the previous run, null rate for each new field, and any rejected rows. The steps are the same as phase 7, using PROD IDs.

- **Tools:** Informatica prod `get_tasks`, `run_mapping_task`, `list_running_jobs`, `get_activity_log`; Snowflake prod `run_query`, `table_write_activity`
- **Gate 5:** You approve the PROD load.

### 11. Commit DDL to GitHub — GitHub · Not set up

Add the final DDL files to the repo your team uses to track DDL, on a branch, with a PR that references the Jira ticket. Until a GitHub MCP is connected, the run writes the files for you to commit yourself.

- **Gate 6:** You review the diff or PR. The workflow is then marked complete and the run file is closed.

---

## Rollback

Used after the 8 Oct test and confirmed to work. Do the steps in this order:

1. **IICS first.** Remove the field from the source query, Validate, refresh the target, and Save. The tools can only add fields, so this step is done by hand in the UI. `read_source_query` then confirms the query.
2. **Then Snowflake.** Drop the column. The Snowflake MCP can't drop columns by design, so someone with access runs:

```sql
-- rollback: remove <COLUMN>
alter table <DB>.<SCHEMA>.<TABLE> drop column
  <COLUMN>
;
```

If you reverse the order, any run of the task in between, including the nightly taskflow, fails because the task still maps to the dropped column. `describe_table` then confirms the column is gone.

---

## DDL rules

All generated DDL follows `sql_formatting_standards.md`. The rules that apply to `alter table`:

| Standard | Applied as |
| --- | --- |
| #1 2-space indent, no tabs | Column lines indented 2 spaces |
| #2 Lowercase keywords and functions | `alter table`, `add column`, and data types such as `varchar(255)`, `number(18,2)`, `timestamp_ntz(9)` |
| #4 / #16 Match source casing; `UPPER_SNAKE` fields | Schema, table and column names in upper case; no database prefix (`SALESFORCE.CONTRACT`) |
| #6 Leading commas | Second and later columns start with `,` |
| #7 Trailing semicolon on its own line | `;` alone on the last line |
| #15 Comments | One `--` line above each statement with the ticket and field source |
| #18 Single space between name and type | No aligning of data types |

### Statement shape

One statement per table, listing every new column for that table:

```sql
-- <ticket>: new fields from Salesforce Contract
alter table SALESFORCE.CONTRACT add column
  IDENTITY_ORG_ID__C varchar(255)
;
```

With several fields:

```sql
-- <ticket>: new fields from Salesforce Contract
alter table SALESFORCE.CONTRACT add column
  IDENTITY_ORG_ID__C varchar(255)
  ,BILLING_FREQUENCY_ANNUAL_MULTIPLIER__C number(18,2)
;
```

**Naming (decided 8 Oct 2026):** DDL files name objects as `SCHEMA.TABLE` only, with no database prefix, so one file serves DEV and PROD.

When the workflow applies a statement through the Snowflake MCP, it adds the environment's database at run time, for example `DEV_STAGING.SALESFORCE.CONTRACT`. The MCP has no `USE` command, so it needs the full name to know which database to change. The DDL file itself is not changed.

The generator checks `describe_table` first and leaves out any column that already exists.

### Staging

- **Name:** the source field name in upper case, keeping the underscores and the Salesforce suffix. `Identity_Org_Id__c` becomes `IDENTITY_ORG_ID__C`.
- **Type:** follow the conventions the staging tables already use. These were observed in `DEV_STAGING.SALESFORCE.CONTRACT` and replace the Draft 2.2 proposal:

| Source field | Staging type in use | Example column |
| --- | --- | --- |
| ID / reference (18 chars) | `varchar(18)` | `ID`, `ACCOUNTID` |
| Text, picklist | `varchar(<Salesforce length>)`, most often `varchar(255)` | `STATUS`, `TERM_TYPE__C` |
| Long text area | `varchar(<length>)`, e.g. `varchar(32000)` | `DESCRIPTION` |
| Number, currency, percent | `number(<precision>,<scale>)` from Salesforce | `TAKE_RATE__C number(18,2)`, `CURRENT_TERM__C number(28,0)` |
| Checkbox (boolean) | `number(1,0)` | `NON_RENEWABLE_CONTRACT__C` |
| Date or date/time | `timestamp_ntz(9)` in most columns | `ACTIVATEDDATE`, `NEXT_RENEWAL_DATE__C` |
| Load metadata, not from the source | `LOAD_TIMESTAMP timestamp_tz(9)`, first column | `LOAD_TIMESTAMP` |

- **`ID` is the primary key**, `not null`.
- **New date fields use `timestamp_ntz(9)`** (decided 8 Oct 2026). Existing tables are mixed: `STARTDATE` and `ENDDATE` are `timestamp_tz(9)`, the other date columns `timestamp_ntz(9)`. Existing columns are left as they are; only new Salesforce date and date/time fields default to `timestamp_ntz(9)`. They are stored as timestamps, not `date`.
- **The generator compares** its proposal with the existing columns of the same table. Where they differ, the table's convention wins and the plan notes it at Gate 2.
- **Numeric precision.** Salesforce reports currency and number fields as `number(28,2)`, for example `Contract_Annual_Subscription_Total__c`. Existing currency columns in the table are `number(18,2)`, for example `SUBSCRIPTION_TOTAL__C`. The generator uses the Salesforce precision, and you decide at Gate 2 whether to match the existing columns.
- **Type names differ in IICS.** It shows Snowflake `varchar(255)` as `nstring(255,0)` and timestamps as `date/time(29,9)`. That's expected and not a mismatch.

These conventions come from one table. The generator re-checks them against each target table rather than assuming them.

### Provisioning

#### Names

Staging columns use the source name exactly (upper case). Provisioning columns are cleaned up: the Salesforce `__C` suffix is dropped, and words run together get underscores. For example `NETREVENUE` becomes `NET_REVENUE`.

The rules below were checked against `DEV_PROVISIONING.SALESFORCE.CONTRACT` (8 Oct 2026). They give the right name for **34 of the 37** non-key columns. Getting close is the goal: every proposed name is shown at **Gate 2**, where you correct any that are wrong (decided 8 Oct 2026).

| Rule | When | Example (staging → provisioning) |
| --- | --- | --- |
| 1. Key field | API name is `Id` | `ID` → `CONTRACT_ID` (`<OBJECT>_ID`) |
| 2. Drop `__C` | API name already has underscores | `ACTIVE_SUBSCRIPTION_PRODUCT_SELL_GROUP__C` → `ACTIVE_SUBSCRIPTION_PRODUCT_SELL_GROUP`; `CONTRACT_MIGRATION_ID__C` → `CONTRACT_MIGRATION_ID` (label "Migration ID" ignored) |
| 3. Words from the label | Run-together API name, and the label has the same letters | `ACCOUNTID` ("Account ID") → `ACCOUNT_ID`; `BILLINGMETHOD__C` → `BILLING_METHOD`; `NETREVENUE__C` → `NET_REVENUE` |
| 4. Label adds the object name | Label is the object name plus the API name | `STARTDATE` ("Contract Start Date") → `CONTRACT_START_DATE`; `NAME` → `CONTRACT_NAME` |
| 5. Split the API name | Nothing above applies | `CONTRACTTERMTEXT__C` → `CONTRACT_TERM_TEXT`, **flagged** (actual: `CONTRACT_TERM`) |

The label comes from `get_source_fields`. The new test field would become `IDENTITY_ORG_ID`.

**Known exceptions in this table**, which no rule can derive and you correct at Gate 2:

- `TERM_TYPE__C` → `CONTRACT_TERM_TYPE`
- `CONTRACTTERMTEXT__C` → `CONTRACT_TERM`
- `STATUS` → `CONTRACT_STATUS_PICKLIST` (likely)

**Not from the source:** `ETL_CREATED_TIMESTAMP` and `ETL_UPDATED_TIMESTAMP` are load metadata. Staging uses `LOAD_TIMESTAMP` instead. The generator skips these. `CONTRACT_STATUS varchar(100)` has no matching staging column and is probably calculated during the provisioning load.

Code: `suggest_provisioning_name(api_name, label, object_name)` in `src/field_planning.py`, with test cases taken from this table.

#### Types

**Copy the staging type.** In `DEV_PROVISIONING.SALESFORCE.CONTRACT`, every column has the same type as its staging column, including both date types. The one exception is `DESCRIPTION`: `varchar(255)` in provisioning versus `varchar(32000)` in staging. Differences like that are shown at Gate 2.

**String dates:** no strings converted to dates appear in this table, but the rule stays for sources that send dates as text. When the staging type is a string and the new field's sample values are dates or timestamps, use the real type:

| New field's sample values (all non-empty) | Provisioning type |
| --- | --- |
| `1/1/26`, `01/01/2026`, `2026-01-01` | `date` |
| `2026-01-01T15:18:19.000Z`, `2026-01-01 15:18:19` | `timestamp_ntz(9)` |
| Mixed, empty, or fewer than the minimum number of samples | Keep the staging type and flag it at Gate 2 |

Two-digit years and the order of month and day are ambiguous: `1/2/26` could be January 2 or February 1. The plan states the format it assumed (for example `M/D/YY`) so you can confirm it at Gate 2.

**Table name:** DEV provisioning is `DEV_PROVISIONING.SALESFORCE.<OBJECT>`. The provisioning table has the same name as the staging table, `CONTRACT`. Its primary key is `<OBJECT>_ID`.

---

## Gaps to close

| Gap | Needed for | Approach |
| --- | --- | --- |
| ~~DDL generator~~ | Phase 4 | **Built 8 Oct 2026**: `src/ddl_generator.py` and the Web UI MCP tool `generate_ddl`. Tested with real DEV metadata for Contract. Still to add: comparing proposed number precision with similar columns in the table (see the numeric precision note under Staging). |
| Workflow skill | All phases | A saved skill holding the phases, gates and tool calls, so a run starts with "here's the Jira, start the field promotion". |
| Run file, resume and re-checks | All gates | One JSON file per ticket under `runs/`, written after every phase. |
| Read connections from the editor | Automating phase 2 | Small read-only tool: Sources and Targets tabs → connection, object. |
| Mapping fields with different names | Phase 6, step 26 | Drag-and-drop not automated. `verify_mapping` reports unmapped fields for manual mapping. |
| Remove a field from the query | Rollback | Not automated; done by hand in the UI. |
| `save_mapping_task` live test | Phase 6, step 30 | Built on the manual save that worked; not yet run as a tool. |
| Export and import tools | Phase 9 | Add `export_assets` (dev) and `import_package` (prod) to the Informatica MCP using the v3 API. |
| GitHub MCP | Phase 11 | Connect later. Until then, write the files for you to commit by hand. |
| ~~IICS editor selectors~~ | Phase 6 | **Done**: see the selector catalogue. |

---

## Open questions

1. **Provisioning load.** What loads the provisioning tables from staging? If it's a separate mapping task or a SQL view, it also needs updating, especially for any string-to-date cast. It isn't in the workflow yet.
2. **Table names.** Confirmed in DEV: staging `DEV_STAGING.SALESFORCE.<OBJECT>`, written through `cn_snf_corpsys.staging.salesforce`; provisioning `DEV_PROVISIONING.SALESFORCE.<OBJECT>`. Still needed: the PROD database names. The DDL files don't name the database, so this only matters when the workflow runs them in PROD.
3. ~~**Fully qualified names.**~~ **Decided 8 Oct 2026:** DDL files use `SCHEMA.TABLE` with no database prefix. One file covers both environments.
4. ~~**Date type for new fields.**~~ **Decided 8 Oct 2026:** new Salesforce date and date/time fields default to `timestamp_ntz(9)` in staging.
5. **Duplicate step.** "Run the mapping tasks in PROD" appears twice in the original list. Is the second one a taskflow run, or a duplicate?
6. ~~**Export scope.**~~ **Decided 9 Oct 2026:** export only the mapping task, without dependencies ("Include all dependencies" unchecked in the UI). The mapping template, connections and runtime environment already exist in PROD. This is the default in `export_assets`.
7. **Minimum sample size.** How many non-empty values must a field have before a string is retyped as a date? I suggest 5.
8. **Phase 6 note.** An earlier draft had an unfinished note in phase 6: "the naming conventions from the existing …". What did you mean to add there?

---

## Appendix: DEV test, 8 Oct 2026

A test to prove the workflow, not ticket work. All changes were rolled back the same day.

| Step | Result |
| --- | --- |
| Snowflake DEV | `IDENTITY_ORG_ID__C varchar(255)` added to `DEV_STAGING.SALESFORCE.CONTRACT` (preview, then apply) |
| IICS DEV, steps 1–30 | Query updated and validated; target refreshed; field mapped automatically; task saved (`updateTime` 2026-10-08 18:54:23Z) |
| DEV run 105 | Succeeded in 2 min 41 s; 197,043 rows read and written, 0 failed |
| Data check | 4,598 rows with a value (2.3%), 1,106 distinct, longest 24 characters |
| Rollback | You removed the field in IICS (saved 19:21:32Z) and dropped the column; both confirmed: query back to 37 columns, table back to 38 |
| Tools retest | `open_mapping_task`, `read_source_query`, `update_source_query` (dry run), `refresh_target`, `verify_mapping` all passed against IICS DEV |

---

## References

- Informatica: [Export and import](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/export-and-import.html)
- Informatica: [Exporting objects](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/exporting-objects.html)
- Informatica: [Importing objects](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/importing-objects.html)
- Informatica: [Starting an import job](https://docs.informatica.com/cloud-common-services/administrator/current-version/rest-api-reference/platform-rest-api-version-3-resources/importing-objects/starting-an-import-job.html)
- `sql_formatting_standards.md` (team SQL and DDL formatting standards)
- Web UI MCP code: `src/adapters/informatica.py` (navigation), `src/adapters/mapping_task_editor.py` (editor tools), `src/field_planning.py` (query and field planning)
