# Web UI MCP — first mapping-task primitive

Copy these updated files into the corresponding locations in your repository:

- `src/adapters/informatica.py`
- `src/server.py`
- `src/browser/playwright_driver.py`

## MCP tool

`open_mapping_task(project, folder, mapping_task, expected_asset_id=None)`

Example arguments:
- `project="staging"`
- `folder="jira"`
- `mapping_task="mt_stage_jira_issues"`

The tool navigates Explore, sets Items per Page to 100, opens the requested
project and folder, searches for the exact mapping-task name, clicks Edit, and
checks the editor title. It does not change or save the mapping.

## Safety notes

- Selectors are based on the HTML and selectors confirmed in this conversation.
- The pagination helper fails closed if it finds more than one
  `[data-testid="dropdown-bar"]`. If the live page has multiple dropdowns,
  inspect the pagination container and scope the locator.
- The current implementation assumes Explore search filters the listing after
  filling the search field. If the UI requires Enter or a Search button, add
  that verified action in `_search_mapping_task`.
- `expected_asset_id` is carried through but is not verified by this UI-only
  primitive; API-based asset resolution and ID cross-checking are a later step.
- Ambiguous matches and title mismatches raise errors rather than continuing.
