# ReqManTool architecture

This document describes how ReqManTool is built, so a new contributor can
find their way around without archaeology. For what's built vs. planned, see
[`BACKLOG.md`](BACKLOG.md). For the user-facing pitch, see the root
[`README.md`](../README.md).

## Goals and non-goals

ReqManTool is a small, local-first, DOORS-inspired requirements management
tool aimed at teams too small to justify an enterprise RM tool, but who
still want real structure: modules, hierarchical requirements, custom
attributes, directional traceability links, and ReqIF/CSV export.

Deliberate constraints that shape every design decision below:

- **Local-first, file-based storage.** Every project is a folder of JSON
  files on disk, not a database. This is a *feature*: history, backup,
  branching, and code review of requirements use ordinary git, the same as
  source code. Each project folder is its own git repository that
  ReqManTool commits to automatically (see "Project history (git)" below). The backend never assumes a
  network filesystem, a database server, or multiple concurrent writers.
- **Single active user per data directory.** There is no authentication and
  no locking. Two people editing the same requirement at once is
  last-write-wins. This is a known, accepted limitation (see `BACKLOG.md`),
  not an oversight — fixing it would mean adding real server-side state and
  contradicts "your data directory is just files you own."
- **No required external services.** SQLite/Postgres, message queues,
  object storage — none of it. The whole stack is a Python process and a
  static frontend bundle; `etc/start.sh` is the entire deployment story.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python, FastAPI, uvicorn | async REST + WebSocket in one process, good typing, small dependency footprint |
| Storage | Plain JSON files via `pathlib`/stdlib `json` | see "local-first" above; no ORM, no migrations |
| ReqIF export | [`reqif`](https://github.com/strictdoc-project/reqif) (PyPI) | correctness for a nontrivial XML dialect is not worth hand-rolling |
| Frontend | React + TypeScript + Vite | standard, fast dev loop, small footprint for a single-user local app |
| UI components | Mantine (`@mantine/core`, `/form`, `/modals`, `/notifications`) | batteries-included widgets (tables, modals, forms) without a large design-system investment |
| Server state | TanStack Query | request caching/invalidation, avoids hand-rolled loading/error state per screen |
| Live sync | Raw WebSocket + a JSON-RPC-shaped message protocol | reuses the same connection the RPC transport already needs; no separate SSE/pub-sub layer |

## System shape

```
┌─────────────────────┐        REST (JSON over HTTP)        ┌───────────────────────────┐
│                      │ ───────────────────────────────────▶│                           │
│   Frontend (Vite)    │                                      │   Backend (FastAPI)       │
│   localhost:5173     │◀───────────────────────────────────  │   localhost:8899          │
│                      │        WebSocket (/ws)               │                           │
└─────────────────────┘   - JSON-RPC 2.0 requests/responses   └────────────┬──────────────┘
                           - unsolicited "changed"/"root_changed"          │
                             broadcasts (live sync)                        │ reads/writes
                                                                            ▼
                                                                 ┌────────────────────┐
                                                                 │  Data directory     │
                                                                 │  (plain JSON files, │
                                                                 │  see layout below)  │
                                                                 └────────────────────┘
```

Every domain operation (create a project, add a requirement, link two
requirements, …) is reachable **two ways** — a REST endpoint and a
WebSocket JSON-RPC method — and both call the exact same
`ProjectService` method, so behavior can never drift between the two
transports (see "Backend layering" below). The frontend uses REST for all
data fetching/mutation (`frontend/src/api/queries.ts`, via TanStack Query)
and keeps one WebSocket connection open app-wide purely for **live sync**:
after any mutation, the backend broadcasts `{"event": "changed",
"project_id": ...}` to every *other* connected client, which invalidates
the relevant queries and refetches. Switching the data directory broadcasts
`{"event": "root_changed"}` instead, which clears the entire client cache
(the whole project/module/requirement set is now a different dataset).
Saving a project as a template broadcasts `{"event": "templates_changed"}`,
which refetches the template list.

The diagram is the development setup (Vite dev server + backend). In a
release package the backend also serves the built frontend from the same
origin (`reqman serve`, default `localhost:8899`), so there is one process
and one port; see "Packaging, configuration and install".

## Data model and disk layout

All persisted types are plain Python `@dataclass`es in
`backend/reqman/models.py`, each with `to_dict()`/`from_dict()` for JSON
(de)serialization. Field order in the dataclass is the JSON key order on
disk (via `dataclasses.asdict`), kept stable on purpose — see "Git-friendly
persistence" below.

```
<data-directory>/
  _templates/                     # user project templates (never a project)
    <template-id>.json            #   see "Project templates" below
    modules/                      # user module templates
      <template-id>.json          #   see "Module templates and heading skeletons"
    prompts/                      # user AI prompts
      <prompt-id>.json            #   see "AI prompt library and CSV import extensions"
  .reqman-staging/                # transient: a project created from a
                                  #   template is assembled here, then
                                  #   renamed into place (removed afterwards)
  <project-id>/                   # one git repository per project (subprojects
                                  #   are sibling folders, never nested)
    .git/                         # created by ReqManTool (see "Project history")
    project.json                  # format_version (first key), then
                                   #   Project: id, name, description,
                                   #   parent_project_id (only if set),
                                   #   references (only if non-empty),
                                   #   locked, template_id (each only if set),
                                   #   attributes (incl. system ones),
                                   #   id_counters, link_configurations, views,
                                   #   module_order (only if set),
                                   #   row_styles (only if non-empty)
    modules/
      <module-id>/
        module.json                # Module: id, name, prefix, description,
                                   #   type (only if not "requirements")
        requirements/
          <requirement-id>.json    # Requirement: id, attributes,
                                    #   parent_id, order
    links/
      <link-config-id>.json        # {"pairs": [{"source_id", "target_id"}, ...]}
                                    # the "link module" — source of truth
                                    # for one link configuration's pairs
```

- **Project** (`project.json`) — `id` (slug, see below), `name`,
  `description`, `parent_project_id: str | None` (see "Subprojects" below;
  the key is written right after `description` and **only when set**, so a
  top-level project's file keeps exactly the keys it had before subprojects
  existed and old files are not rewritten with a new `null` line),
  `attributes: list[AttributeDefinition]` (applies to every
  module in the project — there is no per-module attribute schema),
  `id_counters: dict[prefix, int]` (requirement id allocation state, one
  counter per module *prefix*, shared across modules that use the same
  prefix), `link_configurations: list[LinkConfiguration]`,
  `views: list[View]` (view names are unique per project, compared
  case-insensitively after stripping, like template view names; see
  "Views" below),
  `module_order` (the module tree in display order: `{"module": id}` and
  `{"folder": id, "name", "children": [...]}` entries since project format
  7, module id strings before; written only when non-empty — see "Module
  order and module folders" below), `row_styles: list[RowStyle]`
  (written last and only when non-empty — see "Row styles" below).
- **AttributeDefinition** — `id`, `name`, `type` (`string | text | integer
  | boolean | enum | date`), `required`, `default`, `choices` (enum only),
  `system: bool`, then `modules` and `module_types` (each written only when
  non-empty; see "Module types" below). `system=True` marks an attribute the service manages
  automatically — currently only the read-only mirrors that link
  configurations create (see "Link configurations" below);
  `ProjectService._validate_attributes` rejects any attempt to set a
  system attribute directly through `add_requirement`/`update_requirement`.
  `name` heads the attribute's column in the CSV/XLSX exports and is how the
  CSV import matches columns, so `add_attribute_definition` stores it
  stripped and rejects (400) an empty name, `Id`/`Module`/`Parent` (the
  reserved export columns), or a name another attribute of the project
  already has — all compared case-insensitively. `create_link_configuration`
  applies the same rule to the link name (its mirror attribute heads an
  export column too), with errors starting "Link name". Existing projects
  that already have such names are not rewritten; their CSV import treats
  `Id`/`Module`/`Parent` as the reserved columns and needs the attribute's
  column headed with its `id`.
  `add_attribute_definition` also rejects (400) a `type` outside
  `string | text | integer | boolean | enum | date` (checked first; "Unknown
  attribute type '<type>'; expected one of …"), an `enum` without a
  non-empty list of string `choices`, `choices` on any other type (they must
  be `null`), a `default` that is not a valid value for the type (same
  check as requirement values below; `null` means no default), and a blank
  default (`""` or whitespace only) on a `required` attribute ("Attribute
  '<Name>' is required, so its default must not be empty"), which could
  never satisfy the required check. Stored legacy definitions with an
  unknown type are not rewritten; their values are not type-checked.
- **Attribute values** — `ProjectService._attribute_problems` is the single
  validation path for requirement attributes, used by REST and WebSocket
  add/update and by the CSV import (per row, after cells are parsed into
  typed values). Besides unknown/system attributes, it checks value types:
  `integer` must be a JSON integer (not a boolean or float, not a numeric
  string), `boolean` a JSON boolean, `enum` one of `choices`, `string`/`text`
  a string, `date` an existing calendar date written `YYYY-MM-DD` (a string;
  `2026-02-30`, `2026-1-1` or `02/01/2026` are rejected with "<Name>: '<v>'
  is not a date (expected YYYY-MM-DD)"). `null` (unset) is accepted for every type except as the value of
  a required attribute; violations are 400 errors. Required attributes must
  not be blank (`null`, `""` or whitespace only — "required attribute
  '<Name>' is empty"): on create the check runs on the merged result, so an
  omitted attribute may be filled by its default, but an explicit blank
  value overrides the default and is rejected; on update only the
  attributes being written are checked. Defaults are merged into a new
  requirement by `_merged_attributes`, which never applies defaults of
  system attributes and skips a stored default that is invalid for its type
  (legacy data; the stored definition is not rewritten).
- **Module** (`module.json`) — `id`, `name`, `prefix` (e.g. `"REQ"` — the
  namespace requirement ids in this module are allocated from), `description`,
  `type` (`requirements | test_cases | test_execution |
  operational_scenarios`; written, and returned
  by the API, only when it is not the default `requirements` — see "Module
  types" below).
- **Requirement** (`<id>.json`) — `id`, `attributes: dict[str, Any]` (keyed
  by `AttributeDefinition.id`), `parent_id: str | None` (another
  requirement in the *same module*, forming a tree — see "Nesting" below),
  `order: int` (position among siblings sharing `parent_id`).
- **LinkConfiguration** — `id`, `name`, `source_module_id`,
  `target_module_id`, `target_project_id` (written only when set: the
  referenced project holding the target module of a cross-project
  configuration). Defines one *directional*, named link type between two
  specific modules — see "Link configurations" below and "Project
  references and cross-project links".
- **LinkPair** — `source_id`, `target_id`. One row per actual link,
  persisted per configuration in `links/<config-id>.json`.

### Id generation

Nothing in ReqManTool asks the user to type an id:

- **Project/module ids** are slugs derived from the name
  (`_unique_slug` in `service.py`): lowercase, non-alphanumeric runs
  collapsed to `-`, and a `-2`, `-3`, … suffix appended on collision.
  Ids are never truncated: a name whose id (with any suffix) would be
  longer than the file system allows for a folder name is rejected
  (`ValueError`: REST 400, WebSocket -32602, nothing written), and the
  collision search gives up with the same error after 10 000 candidates.
- **Requirement ids** are `<module.prefix>-<sequence>`, zero-padded to 4
  digits (`REQ-0001`). The sequence is tracked in
  `Project.id_counters[prefix]` — **per prefix, project-wide**, not per
  module — so two modules sharing a prefix continue one sequence and never
  collide. This is what makes requirement ids safe to use as the sole
  cross-module reference (links, `parent_id`) without a module qualifier.
  Counters only ever increase — removed/purged ids are never reused.

### Nesting

Any requirement can have children (`parent_id` pointing at another
requirement) — this is how "chapters" work: a chapter is just a plain
requirement that happens to have children, not a distinct node type.
Nesting is **scoped to one module**; `parent_id` must reference a
requirement in the same module (enforced in
`ProjectService.add_requirement`/`move_requirement`). Reparenting
(`move_requirement`) rejects self-parenting and moving under one's own
descendant (`_descendant_ids` walks the parent chain to detect the cycle).
Removing a requirement with children **promotes** them one level (their
`parent_id` becomes the removed requirement's own `parent_id`) rather than
cascading the delete or leaving orphans. Siblings carry an explicit `order`
(see `reorder_requirement`, `move_requirement`, `copy_requirement` — all
three keep it consistent: append-at-end on move/copy, adjacent swap on
reorder). Move also takes an optional `index` (REST body field and
`requirement.move` param; a non-negative int, else 400 / -32602): the
0-based position among the new parent's children after the move, clamped
to the end. With it, that parent's children are renumbered `0..n-1` in
(`order`, id) order — one write set, one commit, one broadcast; the Module
outline's drag-and-drop uses it. Without it the requirement is appended and
nothing else is rewritten.

### Link configurations ("link modules")

Free-form linking (any type string, any target, no direction) was replaced
by **DOORS-style link configurations**: a project-level `LinkConfiguration`
names a link type and pins it to an ordered pair of modules
(`source_module_id` → `target_module_id`). Once a project has any
configuration, creating a link always goes through one —
`ProjectService.link_requirements` checks that the source/target
requirements' actual modules match the configuration exactly (this is what
enforces direction).

Each configuration's pairs are the **source of truth**, stored centrally at
`links/<config-id>.json` (the "link module") — not on the requirement
files. On every link/unlink, `_refresh_link_attribute` recomputes a
**read-only mirror** on each side: the linked requirement gets an
auto-generated `system=True` attribute (id = the configuration's id) whose
value is a `"; "`-joined list of `"> target_id"` (outgoing) / `"< source_id"`
(incoming) entries. Modeling the mirror *as a normal project attribute*
(rather than inventing a parallel "link columns" concept) means the
existing per-attribute table column and ReqIF-attribute-export machinery
picks it up for free — see `frontend/src/components/EditableAttributeCell.tsx`'s
`attr.system` branch (renders read-only, parses the mirror string into
colored `>`/`<` badges) and `reqif_export.py` (every project attribute,
system or not, becomes an exported `SPEC-OBJECT` value; link configurations
*additionally* become `SPEC-RELATION-TYPE`/`SPEC-RELATION`s from the
central pairs store).

### Module types and date attributes

Project format 5 gives every module a **type**: `requirements` (the
default, and what every module was before), `test_cases`,
`test_execution` or (project format 9) `operational_scenarios` (operational
scenarios / ConOps). It is chosen at creation — `POST /projects/{id}/modules`
and WS `module.create` take an optional `type`; anything else than these
values is 400 / -32602 ("Unknown module type …") — and cannot be changed
later. `module.json` stores it only when it is not `requirements`, and the
API omits it the same way (the frontend reads it with `moduleTypeOf`). An
unknown value in a hand-edited file reads as `requirements`. The UI shows
it as an icon (`components/ModuleTypeIcon.tsx`: document, flask, checklist,
route)
in the Project tab of the sidebar, the module tab bar and the project page.

- **Attribute scope by type**: `AttributeDefinition.module_types` lists
  module types an attribute applies to, next to `modules` (module ids). The
  attribute applies to a module listed in `modules` *or* of a type listed in
  `module_types`; with both empty it applies everywhere
  (`ProjectService._attribute_applies`, mirrored by `attributeAppliesTo`).
  `add_attribute_definition` (`module_types` body field / WS param) and
  `set_attribute_modules` (`PUT .../attributes/{attr}/modules` body
  `{modules, module_types, drop_values}`, WS `attribute.set_modules`) take
  it; `set_attribute_modules` replaces the whole scope (an omitted
  `module_types` means none). Types are stored in canonical order, duplicates
  collapse, unknown types are refused. Required attributes cannot be scoped
  by type either (`_check_required_scope`).
- **Link type pairs**: a new link configuration (API or template) must join
  `requirements → requirements`, `test_cases → requirements`,
  `test_execution → test_cases`, `requirements → operational_scenarios`
  (requirements derived from the scenarios they come from),
  `test_cases → operational_scenarios` (scenario-based acceptance tests) or
  `operational_scenarios → operational_scenarios` (refinement)
  (`LINK_TYPE_PAIRS`,
  `_check_link_types`); anything else is 400 / -32602 and nothing is
  written. Configurations already stored are never checked, so existing
  (all-`requirements`) projects and hand-edited data keep loading and
  linking. The New link configuration dialog disables targets that do not
  fit the chosen source.
- **Templates**: a template module may carry `type` and a template attribute
  `module_types` (both optional and added without a `format_version` bump:
  an older ReqManTool ignores them; version 2 came later with module
  template references). Save as template writes them when not
  default; `GET /templates` lists each module's `type`. The built-in
  `RBC_Template` makes StRS/SyRS/SwRS `requirements`, SysTest/SwTest
  `test_cases` and adds SysTestExec/SwTestExec (`test_execution`, links
  "Executes SysTest"/"Executes SwTest") with execution attributes scoped by
  type: Result (Passed/Failed/Blocked/Not Run, default Not Run), Executed By,
  Execution Date (`date`), Build/SW Version Under Test, Evidence/Log Ref, a
  Test Execution view and row styles for Passed/Failed/Blocked. It starts
  with OpScen (`operational_scenarios`, prefix OPS, module template
  `builtin:operational_scenarios`, linked from StRS by "Derived from
  OpScen") with scenario attributes scoped to it: Actors, Trigger,
  Preconditions (shared with the test modules), Scenario Steps,
  Postconditions and Operational Level/Mode (ETCS levels and modes).
- **Date values** are ISO strings, so they sort and compare as text. The
  editors use the browser's native date picker (`<input type="date">`; no
  extra dependency). Exports: CSV writes the string (re-importable; the CSV
  import strips blanks and validates `YYYY-MM-DD`), XLSX writes a real date
  cell formatted `yyyy-mm-dd`, DOCX the string, ReqIF an
  `ATTRIBUTE-DEFINITION-DATE` on a `DATATYPE-DEFINITION-DATE` (declared only
  when the project has a date attribute) with `THE-VALUE`
  `YYYY-MM-DDT00:00:00` (an `xsd:dateTime` without a time zone); a stored
  value that is not a valid date (hand-edited) is omitted from ReqIF and
  written as text to XLSX.

### Reports

Four read-only reports are computed by `ProjectService` (`service.py`,
"reports" section) from the stored modules, requirements and link pairs.
They write nothing and broadcast nothing; REST and WebSocket return the same
JSON (errors: unknown project/module/configuration 404 / -32001, anything
else invalid 400 / -32602). The UI shows them as pages (`pages/
TraceabilityPage.tsx`, `VerificationReportPage.tsx`, `ExecutionReportPage.tsx`,
`ImpactAnalysisPage.tsx`) reached from the Reports menu of the menu bar and
the sidebar's project / module context menus (actions `report.traceability`,
`module.verificationReport`, `module.executionReport`, `report.impact`; the
impact analysis is also in the requirement navigation and context menus as
`requirement.impactAnalysis`, which seeds it with that requirement). Their
routes use
`:reportModuleId`, not `:moduleId`, so opening a report does not open the
module or add a tab. The queries live under `['projects', id, 'reports',
…]`, so a live-sync `changed` event refetches an open report.

| Report | REST | WebSocket | Export |
|---|---|---|---|
| Traceability matrix | `GET /projects/{id}/reports/traceability?config=a&config=b` | `report.traceability {project_id, config_ids}` | `…/traceability/export/{csv,xlsx}?config=…` |
| Verification report | `GET /projects/{id}/modules/{m}/reports/verification` | `report.verification {project_id, module_id}` | `…/verification/export/{docx,xlsx}` |
| Test execution report | `GET /projects/{id}/modules/{m}/reports/execution` | `report.execution {project_id, module_id}` | `…/execution/export/{docx,xlsx}` |
| Impact analysis | `GET /projects/{id}/reports/impact?requirement=r&requirement=s&depth=3&direction=both` | `report.impact {project_id, requirement_ids, depth, direction}` | `…/impact/export/{csv,xlsx,docx}?requirement=…&depth=…&direction=…` |

Exports are REST downloads only, like the project exports; they are written
by `reqman/report_export.py` (duck-typed over the report dictionaries, like
the other exporters). The report CSV prefixes a cell starting with `=`, `+`,
`-`, `@`, tab or CR with `'` (it is never re-imported, unlike the
requirements CSV).

- **Reportable objects**: every report leaves out objects whose Object Type
  is Heading, Information, Definition, Title, Comment or Note
  (`REPORT_EXCLUDED_OBJECT_TYPES`; the Object Type attribute is found like the
  roles below, by the name "Object Type"). Their count is returned as
  `excluded`. Links to or from them are ignored. Items are listed in tree
  order with their plain ObjectText and nesting `level`.
- **Attributes by role, not by id**: the reports never hard-code RBC ids. For
  each role (`REPORT_ATTRIBUTE_ROLES`) they take the first non-system attribute
  that applies to the module, has one of the role's types and whose name or id
  — lower-cased, punctuation and spaces collapsed — equals one of the role's
  names, in priority order: result (`enum`/`string`: Result, Test Result,
  Execution Result, Verdict, Outcome), execution date (`date`: Execution Date,
  Executed On, Date Executed, Test Date, Run Date, Date; else the module's
  only date attribute), executed by (Executed By, Executor, Tester, Tested By,
  Run By), version (Build/SW Version Under Test, Version Under Test, …, Build,
  Version; else the first name containing the word "version" or "build"),
  evidence (Evidence/Log Ref, Evidence, Evidence Ref, Log Ref, Log; else a name
  containing "evidence"), SIL (sil, Safety Integrity Level, Integrity Level;
  `silk`) and Safety Related (Safety Related, Safety Relevance;
  `safety_related`). The verification and execution reports return which
  attribute they used per role (`attributes`), and the pages show it; the
  impact analysis returns it for SIL and Safety Related per module
  (`modules`).
- **Result values** are compared the same way (`REPORT_RESULT_WORDS`):
  Passed/Pass/OK/Success → passed, Failed/Fail/Failure/NOK/Error → failed,
  Blocked → blocked, Not Applicable/N/A → not_applicable; anything else,
  including no value and Not Run, is not_run.
- **Latest execution** (`_latest_execution`): among the execution records of
  a test case (objects of `test_execution` modules linked to it by any link
  configuration between the two modules), only *executed* records count —
  result passed, failed or blocked (a Not Run record is a planned run). The
  latest is the one with the greatest Execution Date (a record without a
  valid date sorts before every dated one), then the later module in the
  project's module order, then the later position in the module's tree
  order. Deterministic for the same data.

**Traceability matrix** (`traceability_matrix`): `config_ids` is one link
configuration or a chain of up to 10 (`MAX_TRACE_CHAIN`), each after the
first joining the module the chain has reached. Links point downstream →
upstream (a test case verifies a requirement), so the chain starts at the
first configuration's *target* module — unless the second configuration
joins only its source module, then it starts at the source (both
"Satisfies StRS, Satisfies SyRS, Verifies SwRS, Executes SwTest" → StRS → SyRS →
SwRS → SwTest → SwTestExec and "Verifies SyRS, Satisfies StRS" → SysTest →
SyRS → StRS work). The UI mirrors this to offer only configurations that
continue the chain and keeps the chain in the URL. Result: the modules with
their items; per step the grid links (row → column), covered rows ("N of M
covered", %), uncovered rows and orphan columns (no link from the previous
module); the trace of every first-module item through the chain (`reached`
ids per later module, `complete` when it reaches the last one); and the
unverified items — items of each `requirements` or `operational_scenarios`
module of the chain (`TRACE_VERIFIED_TYPES`: scenario coverage by tests) that
reach no item of the next `test_cases` module in the chain. CSV: the trace
table; XLSX: Summary, Trace, one "Matrix n" grid per step and Gaps
(uncovered, orphans, unverified). The UI draws grids up to 1000 × 200.

**Verification report** (`verification_report`, `requirements` modules
only; other types → 400): the covering test cases of a requirement are the
objects of `test_cases` modules linked to it directly (any configuration
between the modules, either direction). A test case's result is its latest
execution; a test case with *no* execution record falls back to its own
result attribute (e.g. the RBC "Test Result") when that says Passed, Failed
or Blocked (`source: "test_case"`). Requirement status: `not_covered` (no
covering test case), else `failed` (any covering test failed), else
`verified` (all passed), else `not_run` (some test blocked or without an
execution). Totals and percentages per status; DOCX (summary table +
requirement table) and XLSX (Summary + one row per requirement/test case).

**Test execution report** (`execution_report`, `test_execution` modules
only): the test cases in scope are the objects of the `test_cases` modules
linked to the module. Each one's status is its latest execution among this
module's records (`not_run` when it has records but none executed,
`not_planned` when no record links to it). Counts: test cases total /
planned (with a record) / executed (% of planned) / passed / failed / blocked
/ not run / not planned, pass rate (% of executed), records per result;
the failed and blocked test cases with the requirements they verify (objects
of `requirements` modules linked to the test case); records linked to no test
case; the distinct builds/versions and executors over all records and the
first/last execution date of the executed ones. DOCX and XLSX (Summary, Test
cases, Failed and blocked, Records).

**Impact analysis** (`impact_analysis`, project scope): the starting
requirements (`requirement_ids`, at least one and at most
`MAX_IMPACT_SEEDS` = 100, duplicates collapsed, unknown → 404, an object
the reports leave out → 400) are walked outwards over the link pairs for
`depth` (1..`MAX_IMPACT_DEPTH` = 10, default 3) steps in `direction`
(`upstream | downstream | both`, default `both`). A link pair's target is
upstream of its source (a SwRS derives from a SyRS, a test case verifies a
requirement), so upstream follows the edge to the target and downstream to
the source; like the traceability matrix only the project's own link
configurations count (cross-project ones are left out) and only links
between reportable objects — headings, information and definitions are
neither walked nor listed. One breadth-first walk per direction, so every
reached requirement has its shortest distance and the chain reached by
(`via` = the previous step, `path` = the ids from the seed on); the seeds
are pre-visited, so nothing is ever walked back to (`distance >= 1`).
Each result lists the requirement (`id`, `module_id`, `text`, `level`),
its `direction`, `distance`, `via`, `path`, its `sil` and `safety_related`
values (found by role per module, `null` when the module has no such
attribute) and the `tests` linked to it (reportable objects of
`test_cases` modules linked to it, any configuration either way, as
`{id, module_id, text}`); `seeds` describes the starting requirements the
same way, `summary` counts them (`seeds`, `reached`, `upstream`,
`downstream`, `max_distance`), `modules` lists the modules touched with
the attributes used, and `data_source` marks a baseline. Results are
ordered by direction (upstream first), then distance, then module/tree
order. Computed from the current data, never stored. Exports: CSV (one
sheet of rows), XLSX (Summary + Requirements) and DOCX (summary, starting
requirements, affected-requirement table) — each row ends with an empty
**Change reference** column for the EN 50128 §6.6 change record, and the
DOCX repeats the note to fill it in per requirement.

### Test result import

Automated test results become execution records of a `test_execution`
module (`ProjectService.preview_test_results` / `import_test_results`,
parser `reqman/test_results_import.py`). Formats: JUnit XML (`<testsuites>`
or `<testsuite>`; nested suites; `<failure>`/`<error>` → failed,
`<skipped>` → not run, else passed) and Robot Framework `output.xml`
(`<robot>`, RF 3–7: the test's own `<status status=PASS|FAIL|SKIP|NOT RUN>`,
`<tag>`s). The format is told by the document element.

- **Safe XML without a new dependency**: the file is parsed with the
  standard library's expat parser directly (`defusedxml` is not a
  dependency); any DOCTYPE, entity declaration or external entity reference
  refuses the file (result files never contain one), so entity expansion and
  XXE cannot happen. Subtrees never read (Robot keywords and messages, JUnit
  `system-out`/`system-err`) are skipped while parsing. At most 64 MiB and
  10 000 tests.
- **Target**: the records are linked through a link configuration joining
  the execution module to a `test_cases` module (`config_id`; default the
  first such configuration). None → 400 ("not linked to a test cases
  module"); a configuration that does not join the two → 400; unknown → 404.
- **Matching** (`match_attribute`): `@id` (the test case's requirement id)
  or a `string`/`integer`/`enum` attribute of the test cases module. Default:
  the attribute found by the `test_id` role (Test ID, Test Case ID,
  Automation ID, External ID, …; see `REPORT_ATTRIBUTE_ROLES`), else `@id`.
  Values are compared like report attribute names (lower case, punctuation
  collapsed). A result's keys are its name, qualified name (JUnit
  `classname.name`, Robot `Suite.Sub.Test`), Robot tags and JUnit property
  values. A test case whose value *equals* a key matches; failing that, one
  whose value is a run of whole words of a key (`SWT-0001 Login works` and
  `test_swt_0001_login` both contain `SWT-0001`). Only reportable test cases
  (not headings…) are candidates. A result matching no test case
  (`unmatched`) or several (`ambiguous`) is skipped and reported, never an
  error.
- **Records**: one per matched result, appended at the module root in file
  order, server-allocated ids. Values by role (as the reports find them):
  ObjectText = the test name; result = the result attribute's enum choice for
  the category (`string` attribute: Passed/Failed/Not Run); execution date =
  the `execution_date` option, else the file's (JUnit `timestamp`, Robot
  `start`/`starttime`, else `generated`), else today; executed by /
  build-version = the user's input (optional); evidence = `"<file name>:
  <qualified test name>"`. Defaults of other attributes apply as for a new
  requirement. A role without an attribute in the module is a warning (value
  not stored), except the result attribute, which is required.
- **All-or-nothing**: every record is validated with `_attribute_problems`
  before anything is written (e.g. a version not among an enum's choices, a
  result category the enum has no choice for); any problem, or no matched
  result at all, refuses the whole file (400 / -32602, at most 20 problems
  listed). Then the record files and project (id counters) are written,
  the link pairs file once, and the linked test cases' link mirror attribute.
  One `changed` broadcast; with history on, one commit.
- **Preview** (read-only, no broadcast): the format, the configuration and
  match attribute used plus the alternatives (`link_configurations`,
  `match_options`), the role attributes, each result with its state and test
  case, the test cases the file does not cover, and the `errors` that would
  refuse the import and `warnings`. The UI (`ImportTestResultsModal`, opened
  by `module.importTestResults` — module context menu and File ▸ Import, only
  for `test_execution` modules) re-runs the preview whenever the file or a
  matching option changes and imports with the previewed configuration and
  match attribute.

### Verification status

A computed, read-only "Verification status" of each requirement of a
`requirements` module: the verification report's status (`verified`,
`failed`, `not_run`, `not_covered`; headings and other excluded objects have
none). `GET /projects/{id}/modules/{m}/verification-status` / WS
`report.verification_status` → `{project_id, module_id, statuses: {id:
status}}`; other module types 400 / -32602. It is not an attribute
definition: never written to requirement files or project.json, so it cannot
be edited, imported (a CSV column of that name is an unknown column unless
the project defines such an attribute) or stored in a view (`View.
attribute_ids` / shared column widths). Exports leave it out: the CSV export
is re-importable and the verification report already exports it (DOCX/XLSX).

UI: View ▸ "Verification status column" (also in the column header view
menu; enabled in requirements modules) shows it as the last column of the
table in every view, a per-browser preference (`reqman.verificationColumn`,
off by default; its width is per browser like other columns, key
`@verification`, and never saved to a view). The detail panel always shows
it for a requirements module. Query key `['projects', id, 'reports',
'verification-status', module]`: every successful mutation of this client
invalidates the `reports` queries (with git status and baselines,
`invalidateGitStatus`), and live sync refetches them for other clients'
changes.

### Subprojects

`Project.parent_project_id` forms a generic → specific product-line tree
in the EN 50129 sense (e.g. RTE → RBC_GP → RBC_GA_NCBIR → RBC_SA_NCBIR →
RBC_NCBIR); `None` means top level. It is a grouping: attributes and
requirements stay within one project. The parent is referenced implicitly,
so link configurations may target its modules (see "Project references and
cross-project links"); reparenting away from a parent that a link
configuration targets is refused (400) unless the parent is also listed in
`references`.
`ProjectService._check_parent` enforces the rules for `create_project` and
`set_project_parent` (`PUT /projects/{id}/parent`): the parent must be the
id of an existing project as listed in the data directory (404 otherwise;
a path such as `a/../b` or an absolute path is never a project id), not the
project itself, and not one of its descendants (400 — the only way to form
a cycle is reparenting). `ProjectStore` treats any project id that is not a
single plain path component as not found. Data written by hand can still
hold a dangling or cyclic parent: the ancestor walk only follows listed
project ids and stops at a missing, unreadable or already-visited project,
but a parent (or, when reparenting, any ancestor) written by a newer
ReqManTool is refused with `NewerFormatError` (REST 409, WebSocket -32602,
nothing written) for both "create under" and "move under", since its own
parent cannot be read to rule out a cycle; `list_projects` reports such projects as top level (in the response
only; the stored value is kept, and `GET /projects/{id}` returns it as
stored), so the sidebar tree always terminates. Reparenting such a project
(dangling or in a cycle) to `null` repairs it; the "Change parent project"
dialog compares the choice with the stored parent from `GET /projects/{id}`,
so choosing "None (top level)" is possible even though the list already
shows the project as top level.
There is no project-removal operation (purge only deletes requirements), so
the app itself never leaves a dangling parent.

### Project references and cross-project links

Project format 6. `Project.references` lists the other projects a project
references explicitly (written right after `parent_project_id`, only when
non-empty); the parent is referenced implicitly. The *effective* references
(`ProjectService._effective_references`: parent first, then `references`,
no duplicates, never the project itself) are the projects whose modules the
project's link configurations may target — an ancestor, a sibling or a
shared library project (e.g. a SUBSET-026 project).

- **Managing** (`set_project_references`): `PUT /projects/{id}/references`
  `{"references": [ids] | null}`, WS `project.set_references` `{id,
  references}` (both broadcast `changed`) replace the explicit list. Each id
  must be a listed project (404 otherwise), loadable (newer format 409,
  broken 400), not the project itself (400); duplicates collapse; at most
  100 (`MAX_PROJECT_REFERENCES`). Dropping a reference (or reparenting away
  from the implicit parent) that a link configuration of the project still
  targets is **refused** (400, "cannot stop referencing project … Remove
  them first"): the user removes those configurations, which deletes their
  links. `GET /projects/{id}/references` (WS `project.references`,
  read-only) lists the effective references (`implicit` for the parent,
  the number of configurations targeting each, `error` when one is missing
  or unloadable) and `referenced_by` (every project referencing this one,
  with its configurations targeting it and their link counts). UI: the
  project page's References card and button and the sidebar project menu's
  "References…" (action `project.references`, dialog
  `ProjectReferencesModal`).
- **Cross-project link configurations** (`create_link_configuration` with
  `target_project_id`, REST body field / WS param): the target project must
  be an effective reference (400 otherwise), the target module must exist
  there (404), and the module types must form one of `LINK_TYPE_PAIRS` like
  any configuration. `target_project_id` equal to the project itself means a
  local configuration. The configuration, its `links/<config-id>.json` pairs
  (plain requirement ids; the configuration names the target project) and
  its mirror attribute all live in the **source (referencing) project**.
- **Mirrors: source side stored, target side computed on read.** The source
  requirement's mirror lists project-qualified targets (`"> rbc-gp:SYRS-0001"`;
  requirement ids are only unique per project, and a requirement id never
  contains `:`). Nothing is written into the target project when a link is
  created or removed: its repository belongs to another project (possibly a
  shared library referenced by many projects), so writing mirrors there
  would commit into another project's history on every link change. The
  incoming side is computed: `GET /projects/{id}/incoming-links?module_id=`
  (WS `link.incoming`, read-only) scans the projects referencing this one;
  the requirement detail panel shows these links ("… (from another
  project)", `<` badges that open the source requirement).
- **Linking**: `link_requirements`/`unlink_requirements` take the target as
  a plain id or qualified with the configuration's target project
  (`rbc-gp:SYRS-0001`; another qualifier is 400). Linking checks that the
  target project is still referenced and the target is in the configured
  module of that project. Unlinking a cross-project link whose target no
  longer exists still works (`target` is `null` in the result).
  `link_options` takes `from_project_id` (REST query parameter / WS param):
  across projects, the options are the cross-project configurations of
  either project targeting the other one, each with the `project_id`
  holding it (where the link is created) and `source_project_id` /
  `target_project_id`. Drag-and-drop linking (`useDropLink`) uses it, so a
  row dragged from a window or tab of another project links when a
  configuration exists.
- **Removing requirements: cascade, not refusal.** Removing a requirement,
  purging its module or its project also deletes the link pairs that
  *other* projects' cross-project configurations hold to it, and refreshes
  those projects' source mirrors (`_incoming_link_cleanup` /
  `_apply_incoming_cleanup`) — the same cleanup as for links within a
  project, so no project keeps a link to a requirement that no longer
  exists. These writes happen in the referencing projects, which therefore
  auto-commit them in their own repositories ("Link SYRS-0001 →
  lib:S-0001 removed (Refines)"); every referencing project to clean up is
  checked for a git sync conflict first (409 before anything is written).
  Projects that cannot be loaded are skipped (never written). The API
  broadcasts `changed` for every project the operation wrote
  (`ProjectService.take_touched_projects`, used by
  `ConnectionManager.broadcast`), and the frontend refetches references and
  incoming links of every project on any `changed` event and after every
  mutation (`invalidateCrossProject`).
- **Where cross-project configurations are left out** (their target module
  is not a module of the project): reports (`_report_links`,
  `_report_linked_modules`; a traceability chain containing one is 400),
  the test result import, the ReqIF export's `SPEC-RELATION`s (the mirror
  attribute, with qualified targets, is exported as an attribute value like
  in CSV/XLSX/DOCX) and save-as-template (configuration and view columns
  dropped). Commit messages qualify cross-project targets.
- Navigation: a qualified link end opens the requirement in its project
  (`commands.goToRequirement`); badge tooltips look the requirement up in
  that project.

### Project lifecycle: lock and remove

- **Lock** (project format 8): `Project.locked` = `{by, email, at, reason,
  baseline?}` (`models.ProjectLock`), written right after `references`,
  only when set. `by`/`email` is the git identity commits use
  (`GitHistory.user_identity`: `REQMAN_GIT_AUTHOR_*`, else git's config,
  also without a repository, else the login name; git is not run with
  history off), `at` an ISO 8601 UTC timestamp, `reason` free text
  (whitespace collapsed, at most 500 characters), `baseline` the baseline
  name for "lock after baseline". A hand-edited truthy non-object value
  still means locked (never lost by mistake). `PUT /projects/{id}/lock`
  `{reason}` (WS `project.lock` `{id, reason}`) and `DELETE
  /projects/{id}/lock` (WS `project.unlock` `{id}`) both broadcast
  `changed` and commit ("Project locked: …" / "Project unlocked");
  unlocking an unlocked project writes nothing.
- **The guard**: `_records_history`, which wraps every public mutation
  whose first parameter is `project_id`, calls
  `ProjectService._require_unlocked` before anything is written, so every
  change of a locked project — project settings, parent, references, row
  styles, attributes, modules, module folders and order, requirements,
  links, link configurations, views and column widths, CSV / test result
  imports, module templates, purges — is refused with
  `ProjectLockedError` (a `ConflictError`): REST **423**, WebSocket error
  code **-32004** (`PROJECT_LOCKED_RPC_CODE`). Only `unlock_project` is
  exempt (`_allowed_while_locked`). Not data changes and so allowed: reading,
  exports, reports, save as template, creating a baseline, git enable /
  push / pull / remote settings (a pull may bring in another clone's
  unlock), creating a subproject *under* a locked project, linking from
  another project to a locked one (the link lives in the referencing
  project).
- **Cascades into a locked project are refused**: removing a requirement,
  or purging a module or project, whose requirements a locked referencing
  project links to would delete links there; `_check_incoming_cleanup`
  refuses the originating operation (423) before anything is written.
- **Remove**: `DELETE /projects/{id}` `{confirm_name, data_dir}` (WS
  `project.remove` `{id, confirm_name, data_dir}`) deletes the project
  folder with everything in it — including its own `.git`, so its history
  and baselines go too. `confirm_name` must be the project name (400);
  `data_dir`, when given, must still be the server's data directory (409:
  project ids repeat across data folders, so a confirmation opened before
  a folder switch can never remove a project of another folder); refused
  while locked (423), and while the project has subprojects (stored
  parent) or another project references it (explicitly; a subproject
  references it implicitly) — 409 listing them; unloadable projects are
  read best-effort from their raw file so they still block. With no
  referencing project, no other project holds links to it, so nothing
  outside the folder needs cleaning. `ProjectStore.delete_project` touches
  nothing outside the folder: it must be a real directory (no symlink or
  junction) directly in the data directory; it is renamed into
  `.reqman-staging/` in one step (gone from the list at once, never
  half-deleted) and then deleted (`leftover` names what could not be
  deleted). Broadcast: `{"event": "changed", "project_id": id, "removed":
  true}` (`ConnectionManager.broadcast_removed`); clients drop the
  project's cached queries, refetch the list, close its module tabs, drop
  a clipboard taken from it and leave its pages (`handleProjectRemoved`,
  `subscribeProjectRemoved`). `GET /projects/{id}/removal` (WS
  `project.removal_info`, read-only) answers what would happen:
  `subprojects`, `referenced_by`, `locked`, `can_remove`, the git
  `history` status and a server-written `recovery` hint (a remote: clone
  it back, and what is not pushed is lost; nested in an enclosing
  repository: restore it from there; otherwise not recoverable without a
  backup).
- **Baseline glue**: `POST /projects/{id}/baselines` takes `lock` and
  `lock_reason` (WS `baseline.create` too): the project is locked first
  (reason default "Baseline <name>", `baseline` recorded), then the
  baseline is tagged, so the baseline is exactly the locked state; a
  failed baseline undoes the lock. The response adds `locked`. Verification
  and test execution reports carry `data_source` (`history`, HEAD
  `commit`, `pending_changes`, the `baselines` tagged at that commit, the
  `latest_baseline`, `locked`), shown under the report summary and in the
  DOCX/XLSX header ("Data: …"); reports are still always computed from the
  current data.
- **UI**: project menu (sidebar project node, Edit menu) "Lock project…",
  "Unlock project", "Remove project…" (`project.lock` / `project.unlock` /
  `project.remove`); the project page has Lock/Unlock and Remove buttons
  and a lock alert; a lock badge (`ProjectLockBadge`) shows on the project
  page, module page, workspace tree and Project tab. Registry actions
  that change data are marked `edits: true`; `isActionAvailable` (used by
  every menu, shortcut and button) makes them unavailable while their
  project is locked (`AppState.lockedProjectIds`, from the projects list
  and the open project). Inline cell editing, the detail panel, row
  drop-to-link, outline and module-tree drag and drop and the project
  page's editing buttons are disabled too. The remove dialog
  (`RemoveProjectModal`) captures the data folder when opened, closes on a
  folder switch, refuses while the current folder differs and sends it
  as `data_dir`.

### Project templates

A template is a JSON file holding a project's *structure* — attribute
definitions, modules, link configurations between those modules, and views
— and no requirements except heading skeletons (see below).
`backend/reqman/templates.py` documents the format (`format_version` 2;
version 1 is still read) and checks its shape; `ProjectService` applies the
domain rules.

- **Ids are namespaced by location**, so templates can never collide or
  shadow each other: `builtin:<stem>` for the read-only files shipped in
  `backend/reqman/templates/` (currently `builtin:rbc_template`, the
  ERTMS/ETCS RBC structure for EN 50126/50128/50129) and `user:<stem>` for
  `<data-directory>/_templates/<stem>.json`. The file stem is the identity;
  the `id` key inside the file is informational. Lookups pick from the
  listed files, never by joining a raw id into a path.
- **`_templates/` is never a project**: project ids are slugs
  (`[a-z0-9-]`), which can never be `_templates` or `.reqman-staging`, and
  `ProjectStore` skips both names when listing, checking or loading
  projects, even if a `project.json` is put there by hand.
- **Validation is the create path**: `_build_from_template` builds the
  project in memory with the same helpers as `add_attribute_definition`,
  `create_module`, `create_link_configuration` and `create_view`
  (attribute name/type/choices/default rules, prefix rule, link-name rule,
  view references), so a template can never produce a project those
  operations would reject. `templates.py` adds template-only checks:
  required keys and types, unique module keys/names, link keys and view
  names, link `source_module`/`target_module` must be module keys, and view
  entries must be `object_text`, a template attribute id, or `link:<key>`
  (the mirror attribute of that link configuration). Duplicate module
  prefixes are allowed, as in projects (they share one id counter).
- **Compatibility**: unknown keys are ignored at every level, so a later
  optional key (such as a module's `type` or an attribute's
  `module_types`) needs no `format_version` bump. `format_version` is bumped only for incompatible changes; a
  template with a missing, non-integer or newer `format_version` is invalid.
  Version 2 lets a module entry reference a module template
  (`module_template`) and omit `name`/`prefix`, which a version 1 reader
  would reject. A version 1 file is migrated in memory (`migrate_v1`: its
  module `module_template`/`skeleton` keys, which version 1 ignored, are
  dropped); files on disk are never rewritten, and templates saved or
  imported now are written as version 2.
- **Listing** (`GET /templates`): built-in templates, then user templates,
  each sorted by name, each fully validated. An invalid file (unreadable,
  bad or too deeply nested JSON (over 100 levels, `MAX_TEMPLATE_JSON_DEPTH`), a string that is not valid UTF-8 such as a
  lone `\ud800` escape, wrong shape, unsupported version, rule violation) is listed with
  the reason in `error` (and logged at debug level) instead of breaking the
  list; creating from it fails with the same reason.
- **Create from template** (`POST /projects` with `template_id`) is
  all-or-nothing: everything is validated in memory first, then
  `project.json`, the modules and empty link-pair files are written into
  `.reqman-staging/<random>/` and moved into the data directory with one
  `os.rename`. The staging directory is always removed, so a failure leaves
  nothing behind. Module ids are slugged from the module names, link
  configuration ids from the link names (each with its system mirror
  attribute), and view `link:<key>` entries are mapped to those ids.
- **Save as template** (`POST /projects/{id}/save-as-template`) writes
  `_templates/<slug-of-name>.json` (atomic write, two-space indent, trailing
  newline, fixed key order): non-system attributes except `object_text`,
  modules (`key` = module id, in the project's module order), link configurations (between
  module keys), views (mirror attributes as `link:<key>`; references to
  attributes the project no longer has are dropped) and row styles (rules
  that no longer resolve are dropped; `row_styles` written only when
  non-empty) and each module's headings as its `skeleton` (written only when
  non-empty; see below). Other requirements, id
  counters and the parent are not saved. The result is round-tripped
  through the loader before it is written. A name already used by any
  template (built-in or user, case-insensitive) is rejected with 409; a
  file is never overwritten.
- **Import a template file** (`POST /templates/import`, WS
  `template.import`; File ▸ Load template ▸ Import template file) saves an
  uploaded template JSON document as a user template
  (`ProjectService.import_template`). It is checked exactly like a loaded
  template (UTF-8, an optional BOM, at most 1 MiB of UTF-8 over REST and
  WebSocket alike, no lone surrogate escapes, shape, `format_version`,
  every project rule) and then written like Save as template: `_templates/<slug-of-name>.json`
  (slug suffixed if the file name is taken), the file's `id` key replaced by
  that stem, canonical key order (unknown keys are not kept). Invalid input
  → 400, a name used by any template → 409, nothing is written in either
  case; success broadcasts `templates_changed`.

### Module templates and heading skeletons

A **module template** (`templates.ModuleTemplate`, `"kind": "module"`, its
own `format_version` 1) describes one module: default name, prefix,
description and type, the attributes that apply to it (no `required`, no
link configurations, nothing cross-project), an optional view and a heading
skeleton. Built-in ones live in `backend/reqman/templates/modules/`
(`requirements`, `test_cases`, `test_execution`, `operational_scenarios`),
user ones in
`<data-directory>/_templates/modules/`; ids are namespaced like project
templates (`builtin:<stem>` / `user:<stem>`) in a namespace of their own.
A module template file is rejected as a project template and vice versa.

A **heading skeleton** is a nested list of `{"heading", "text"?,
"children"?}` nodes (at most 2000). When the module is created, every node
becomes a requirement in tree order with ObjectText = the heading and
Object Type = Heading; a node's `text` becomes its first child with Object
Type = Information. Ids are allocated by the server as usual, defaults
apply, and every object is validated like `add_requirement` (a required
attribute without a default makes the skeleton fail). Object Type is the
first non-system enum/string attribute named (or with id) "Object Type"; an
enum needs `Heading` (and, for texts, `Information`) choices — the
template is refused otherwise. The chapter lists are data in the template
files.

- **Attribute merge** (`_merge_template_attributes`, shared by "Add module
  from template" and project templates referencing a module template):
  matched by name, case-insensitively. An existing attribute must have the
  same type (a link mirror attribute never matches) and is never changed,
  except that a scoped attribute not applying to the new module gets the
  module added to its `modules` list — harmless, as the module is new and
  empty. A missing attribute is added *optional* and scoped to the new
  module only (id = the template's, or a free slug of it), so no other
  module and no existing requirement is affected. Enum choices are never
  edited. The template's view is added unless a view of that name exists.
- **Add module from template** (`POST /projects/{id}/modules/from-template`,
  WS `module.create_from_template`; sidebar project/folder menu "New module
  from template", File ▸ Load template, or the Template picker of the New
  module dialog): body `template_id`, optional `name`/`prefix`/
  `description` overrides and `folder_id`. Everything (module, merge,
  view, skeleton) is built and validated in memory first; then the module
  file, the requirement files and `project.json` are written — one history
  commit, one `changed` broadcast. Returns the module, `added_attributes`,
  `extended_attributes`, `view_id` and `headings` (objects created).
- **Save module as template** (`POST
  /projects/{id}/modules/{module}/save-as-template`, WS
  `module.save_as_template`; module menu): name, description, optional
  `view_id`. Saves the module's name/prefix/description/type, the non-system
  attributes that apply to it (except ObjectText), the view (references to
  unsaved attributes dropped) and its headings: requirements whose Object
  Type reads Heading, nested as in the module (a heading below a
  non-heading is left out), a heading's first Information child as its
  `text`, rich text reduced to plain text. Round-tripped and dry-run before
  writing; a name used by any module template → 409; broadcasts
  `templates_changed`.
- **Apply chapter template** (`POST
  /projects/{id}/modules/{module}/apply-skeleton`, WS
  `module.apply_skeleton`; read-only preview `POST
  .../apply-skeleton/preview`, WS `module.preview_skeleton`; module menu and
  Edit ▸ "Apply chapter template…", which shows the preview): body
  `template_id` (a module template; null/omitted: `builtin:<module type>`)
  and optional `module_key` — then `template_id` is a *project* template
  (its own namespace, so `builtin:rbc_template` is never read as a module
  template) and the source is that module entry's skeleton, else the one of
  the module template it references (the rule `_build_from_template`
  applies when the project is created). That is how an existing project
  gets the template's specific chapters instead of the generic ones of a
  plain module template; an unknown key is 400, a missing template 404.
  The dialog lists every project template's modules and pre-selects the
  entry of the template the project was created from (`Project.template_id`,
  matched by module prefix, then by name). Gives an *existing* module the
  skeleton's heading tree
  (`ProjectService.apply_module_skeleton`, planned by
  `_module_skeleton_plan`): level by level, a skeleton node whose title
  (case-insensitive, whitespace collapsed) is already a heading under the
  same parent is descended into; any other node is added with its subtree
  and `text`, appended after the existing siblings. Existing requirements
  are never moved, changed or deleted, so applying again adds nothing (and
  writes nothing). Only the headings are applied: the template's attributes
  and view are not merged, except that Object Type is made to apply to the
  module if it did not. Lock, permission and module-lock rules are those of
  every module write (`_records_history`); one commit, one `changed`
  broadcast. Returns `{template_id, module_key, created: [ids], headings:
  [{id, heading, parent_id, level, under_existing}]}` (the preview:
  `{template_id, module_key, headings, requirements}` with provisional ids;
  `module_key` is null unless the source is a module of a project
  template). The RBC template gives
  every module an RBC/CENELEC chapter structure (OpScen, StRS, SyRS, SwRS,
  the test specifications and the test execution campaigns).
- **Listing** (`GET /module-templates`, WS `module_template.list`): like
  project templates, every entry is dry-run on an empty project and an
  invalid one is listed with `error`. The frontend fetches it lazily (when
  the New module dialog's template picker opens, or right away for "New
  module from template") and refetches it on `templates_changed`.
- **Project templates** (version 2) may reference a module template per
  module (`module_template`): name/prefix/description/type default to the
  module template's (a different `type` is refused), its attributes are
  merged after the template's own attributes and scopes, its skeleton is
  created (a module's own `skeleton` replaces it; `[]` for none) and its
  view is added after the template's views. Skeleton requirements are
  written into the staging directory with the rest of the project; ids come
  from the new project's counters. `RBC_Template` references the three
  built-in module templates and carries RBC-specific skeletons for SyRS
  (functional chapters, interfaces) and SwRS (EN 50128 software
  requirements specification structure).

### AI prompt library and CSV import extensions

ReqManTool never calls an AI service. The user copies a generated prompt
into any AI chat and pastes the CSV answer back into the CSV import.

- **Prompts** (`reqman/prompts.py`) are JSON files: built-in ones in
  `backend/reqman/prompts/<stem>.json` (`builtin:<stem>`, read-only) and
  user ones in `<data-directory>/_templates/prompts/<stem>.json`
  (`user:<stem>`). They sit under `_templates/`, so they are never listed as
  a project, and in a subfolder, so the project template listing never sees
  them. Their own namespace: a prompt and a template may share a stem. Keys,
  in written order: `kind` (`"prompt"`), `format_version` (1), `name`
  (unique among all prompts, case-insensitive), `description`, `selection`
  and `free_text` (`none`/`optional`/`required`), `free_text_label`,
  `import_mode` (`create`/`update`/`none` = a review answer, not CSV) and
  `body`. An unknown `{{placeholder}}` makes a prompt invalid; an invalid
  file is still listed with `error` (like templates).
- **Prompt CRUD is user data outside projects**: create/update/delete/import
  (`ProjectService.create_prompt` etc.) are not `_records_history` methods,
  so no project lock, sync check or commit applies; they broadcast
  `{"event":"prompts_changed"}` (REST and WebSocket alike). Built-in prompts
  cannot be changed or deleted (400); names are unique (409). Update keeps
  the file stem; export returns the canonical file; import never overwrites.
- **Rendering** (`render_prompt`, read-only, never broadcast) fills the
  placeholders server-side: project and target-module name/description/
  type/prefix; `csv_header` (the exact import header of the target module:
  `Id`, `Parent`, every non-system attribute that applies to the module by
  module or module type, headed by its name or its id where the name is
  reserved or ambiguous, then one `Link: <name>` column per link
  configuration starting in the module); `attribute_guide` (type, required,
  enum choices, `YYYY-MM-DD` dates, defaults); `link_columns` (target
  module, and for a cross-project configuration the `<project>:<id>` form);
  `import_rules` (server-allocated ids, file Id only a Parent key, one CSV
  code block and no prose; update-mode rules for `import_mode` `update`);
  the selection (`selected_csv`, `selected_ids`, `selection_module_name`:
  requirement ids of one module, optionally with descendants, in tree
  order; ids of another project are written `<project>:<id>`) and
  `free_text`. A prompt with `selection`/`free_text` `required` refuses to
  render without them.
- **CSV import extensions** (`_csv_import_plan`, shared by
  `preview_csv_import` and `import_csv`; still all-or-nothing):
  - A Markdown code fence around the CSV (first non-blank line ```` ``` ````
    or ```` ```csv ````) is stripped.
  - `Link: <configuration name or id>` columns create links of a link
    configuration whose source module is the import module, including
    cross-project ones (targets plain or `<project>:<id>`, which must name
    the configuration's target project; the target project must still be
    referenced). Each target must exist in the configuration's target module;
    for a configuration within the module a target may be an Id key of the
    file. Links that already exist are skipped. Mirror columns (headed with
    the configuration name, as in an export) stay ignored. Only the
    importing project is written (pairs and mirrors).
  - `mode=update`: a row whose Id is an existing requirement of the module
    updates it with its non-empty cells (empty cells leave values unchanged;
    rich text whose plain projection equals the cell is unchanged), checked
    like update requirement. Its Parent must be empty or its current parent:
    the import never moves requirements. Other rows are created; their
    Parent may also name an existing requirement of the module (create mode
    keeps the old rule: Parent is a row of the file).
  - The preview (`POST .../import/csv/preview`,
    `requirement.preview_csv_import`) returns `created` (provisional ids),
    `updated` (per attribute old/new, plain text), `unchanged` and `links`
    (`exists`). The UI imports only after a preview of the same input.
  - The import result keeps its keys (`imported`, `requirement_ids`,
    `ignored_columns`); update mode adds `updated`, `updated_ids`,
    `unchanged`, link columns add `linked`. The import is a
    `_records_history` mutation, so a locked project refuses it (423).
- **UI**: Tools ▸ AI prompts, and "AI prompts" in the requirement and module
  context menus (shared action registry), open the Prompts dialog: pick a
  prompt, the target module, the requirements (none / the selected one, with
  or without children / all of the module), optional free text, Generate
  preview, Copy. A notice that copied requirements leave the machine once
  pasted into an AI chat must be acknowledged once per browser
  (localStorage) before Copy. "Paste AI result" (also Tools ▸ Paste AI
  result) opens the CSV import with a paste-text box and the prompt's import
  mode. The Manage tab adds, edits, duplicates, deletes, exports and imports
  user prompts.

### Format versions

`project.json` and the app config file (`config.json`, see "Packaging,
configuration and install") start with
an integer `format_version` (`reqman/formats.py`: `PROJECT_FORMAT`,
`CONFIG_FORMAT`; project currently 10, config 1). Module, requirement and
link-pair files have no version of their own; they are governed by their
project's version. Templates keep their own, separate `format_version`.
Project format 2 added `AttributeDefinition.modules` (per-module attribute
applicability: the module ids an attribute applies to, written only when
non-empty); the 1 → 2 step drops any stray `modules` key, since version 1
attributes applied to every module. Project format 3 added
`Project.row_styles`; the 2 → 3 step drops any stray `row_styles` key, so
an existing project gets no rules (only new projects from a template that
declares them, such as the RBC template, get the defaults). Project format
4 marked rich text values (nothing rewritten). Project format 5 added
module types (`module.json` `type`), `AttributeDefinition.module_types` and
the `date` attribute type (see "Module types and date attributes"); the
4 → 5 step drops any stray `module_types` key and does not rewrite module
files (a version 4 `module.json` has no `type`). Every write that can store
one of them also saves `project.json` (creating a module stores the module
order, adding an attribute or a template project saves the project), so a
file using them is always in a version 5 project. Project format 6 added
`Project.references` and `LinkConfiguration.target_project_id` (see
"Project references and cross-project links"); the 5 → 6 step drops stray
keys of those names and rewrites no module, requirement or link-pair file.
Project format 7 added module folders (see "Module order and module
folders"): the 6 → 7 step turns every `module_order` id string into
`{"module": id}` in the same position (lossless), drops non-string entries
(a version 6 reader ignored them) and a non-list `module_order`, and
rewrites no module, requirement or link-pair file. The bump makes an older
ReqManTool refuse a version 7 project instead of dropping its folders (and
every module in one) from the order on save. Project format 8 added
`Project.locked` (see "Project lifecycle: lock and remove"); the 7 → 8 step
drops a stray `locked` key and rewrites no module, requirement or link-pair
file. The bump makes an older ReqManTool refuse a version 8 project instead
of dropping the lock on save, which would silently unlock it. Project
format 9 added the `operational_scenarios` module type (see "Module types
and date attributes"); the 8 → 9 step drops `operational_scenarios` from
attribute `module_types` (a version 8 reader matched no module with it) and
rewrites no module, requirement or link-pair file. The bump makes an older
ReqManTool refuse a version 9 project instead of reading operational
scenario modules as requirements modules and dropping their type on save.
Project format 10 added `Project.template_id` (the project template a
project was created from; see "Apply chapter template" above); the 9 → 10
step drops a stray `template_id` key and rewrites no module, requirement or
link-pair file — a version 9 project simply has no remembered origin. The
bump makes an older ReqManTool refuse a version 10 project instead of
forgetting the template on save.

Only optional attributes can be scoped to modules: a required attribute
always applies to every module. `ProjectService._check_required_scope`
enforces it for `add_attribute_definition` (required with a non-empty
`modules`), `set_attribute_modules` (a non-empty list for a required
attribute; `[]`/`null` stays allowed) and templates (a required template
attribute with `modules` makes the template invalid). REST answers 400 and
WebSocket -32602 with the same message, and nothing is written. No API
changes an existing attribute's `required` flag, so a scoped attribute
cannot become required later. A hand-edited `project.json` with a required,
scoped attribute still loads: the attribute is treated as applying to every
module (`_attribute_applies`, mirrored by `attributeAppliesTo` in the
frontend), the stored list is kept on disk until the scope is reset to all
modules, and save-as-template writes no `modules` for it.

- A file without the key is version 1 (the format written before versions
  existed) and loads unchanged.
- Loading never rewrites a file: `formats.upgrade` applies the registered
  migration steps (`FileFormat.migrations[n]`: version `n` to `n + 1`) in
  memory; the current version is written by the next real save, so reads
  leave git diffs quiet.
- A file with a newer version than the tool supports raises
  `NewerFormatError` ("... was written by a newer ReqManTool ...": REST
  409, WebSocket -32602). `ProjectStore` checks the project's stored
  version before every read or write of the project's files, and
  `config.save_data_dir` checks the config file, so such files are never
  misread or overwritten. A newer config file stops the server at start
  (unless `REQMAN_DATA_DIR`/`--dir` is given) rather than silently using
  another data directory.
- A `project.json` that cannot be loaded for any other reason (not valid
  UTF-8 JSON, not a JSON object, an invalid `format_version`, a missing
  required key or a value of the wrong shape, or a file this process cannot
  read: no permission, a directory) raises `ValueError` (REST 400, WebSocket
  -32602) on every read or write of that project's files, never treated as
  version 1. The per-file check runs the same upgrade and validation as
  loading the project, so it fails exactly when loading does. An unreadable
  file is reported as "Project '<id>' has an unreadable project.json:
  <reason>", without its absolute path.
- **Project list stubs**: `GET /projects` (WS `project.list`) never fails as
  a whole because of one project. A project whose `project.json` cannot be
  loaded (newer version, invalid JSON, wrong shape, invalid
  `format_version`, unreadable, or a project folder this process cannot
  search) is listed as `{id, name, error}`
  (`ProjectStub`; `name` is the stored name if usable, else the id), always
  top level since its stored parent cannot be trusted; its readable
  subprojects stay under it. A folder this process cannot search is listed
  only if its name is a project slug, so system folders such as
  `lost+found` or `.Spotlight-V100` are not. A stub is fully read-only:
  opening it, reading, listing or changing any of its modules, requirements
  or links (including ones that do not exist), and purging it or one of its
  modules fail with the same error (no write, no broadcast). Purging a
  project, or a module of a project, that does not exist is 404 (WebSocket
  -32001); purging a module that does not exist in a loadable project
  purges nothing (`{"purged": 0}`). The sidebar shows it greyed,
  with a warning icon and the error inline (and in a tooltip on hover,
  focus or touch), and no modules or "New module" entry; clicking or
  activating it opens the project page, which shows the load error. Parent
  pickers list it disabled.
- Every data file (`project.json`, module, requirement, link-pair, template
  and config files) is read as `utf-8-sig` (an optional UTF-8 BOM is
  ignored) and written as plain UTF-8 without a BOM.
- Bump a version for a change an older reader would misinterpret, and for
  any new persisted key that must survive a round-trip through an older
  tool: `Project.from_dict`/`to_dict` drop unknown keys, so a versioned
  tool that loads and saves a project would silently delete a key added
  later without a bump. `module_order` was added without a bump only
  because the tools it could be lost through predate versioning and cannot
  be protected anyway; from this release on, new keys bump the version
  (module folders did: project format 7). A project migration that changes
  module, requirement or link-pair files must upgrade those files too.

### Views

A view is a project element in `project.json`, so it travels with the
project (git, copies, templates, baselines). `View` keys, in written
order: `id`, `name`, `attribute_ids` (the view's attribute columns *in
display order*; the Id column always comes first), and, written only when
set, `column_widths` (column key to pixels, 40..4000; keys `"@id"` or one
of `attribute_ids`), `single_line` (column keys shown on one line
instead of wrapping; every other column wraps) and `modules` (the module
ids the view is offered on, in module display order; empty: every module,
like `AttributeDefinition.modules`). `column_widths`, `single_line` and
`modules` were added without a `PROJECT_FORMAT` bump: they are display
settings, absent keys mean the old behaviour, and an older ReqManTool that
rewrites the project only drops them (the view itself, its columns and
their order survive; a scoped view is then offered on every module).
Modules are never deleted (purge removes requirements only) and renames
keep ids, so scopes need no cleanup. Both are stored in the view's column order whatever
order a client sends.

`ProjectService.update_view(project_id, view_id, changes)` (REST `PUT
/projects/{p}/views/{v}`, WebSocket `view.update` with `project_id`, `id`
and the fields) changes any non-empty subset of `name` (same uniqueness
rule; a view may keep its own name in another case), `attribute_ids`
(defined attributes, no duplicates), `column_widths` and `single_line`
(null or empty clears; keys must be columns of the resulting view) and
`modules` (known module ids; null or empty: every module).
Fields not given keep their value; when only `attribute_ids` changes,
widths and single-line entries of removed columns are dropped. Unknown
fields, an empty change set or any invalid value raise `ValueError`
(400 / -32602) before anything is written; a missing view is 404 /
-32001; success broadcasts `changed`. `create_view` (`POST
/projects/{p}/views`, `view.create`) also takes `single_line` and
`modules` and now rejects duplicate columns. Views are listed unfiltered;
the client offers on a module page only the views that apply to it
(`applicableViews`, `frontend/src/api/applicability.ts`). `PUT .../views/{v}/column-widths` and `DELETE
.../views/{v}` are unchanged. Project and module templates carry a view's
ordered columns and, optionally, its `column_widths` / `single_line` keyed
by template references (see `templates.py`); project template views also
carry `modules` as module keys.

In the UI the saved view is the source of truth on the module and
baseline pages: its columns appear in `attribute_ids` order with its
widths and wrap. Column drags, resizes and wrap toggles in a named view
are an unsaved per-browser draft (`frontend/src/hooks/columnLayout.ts`,
`viewLayoutDraft`), flagged "Unsaved layout"; View menu and column header
menu: "Save layout to view", "Revert to saved view" and "Save current
layout as new view…". The view editor dialog (View ▸ "Edit view", the
pencil next to the view selector, the project page's views list) renames
a view, picks its columns and their order (up/down), per-column wrap and
"Applies to modules", and deletes it (with confirmation). "All attributes"
stays a per-browser layout. Row
heights and the Verification status column are always per browser. The
hierarchy indent and expand arrow sit in the Object Text column wherever
it is ordered (else the first column).

### Row styles

`project.row_styles` is an ordered list of `{attribute_id, value,
font_color, background_color, bold}` rules: rows whose enum attribute
`attribute_id` has `value` get that font color, background color
(`#rrggbb`, stored lower-case, or null) and bold. The first matching rule
wins. `ProjectService.set_row_styles` (`PUT /projects/{id}/row-styles`
`{"row_styles": [...] or null}`, WS `project.set_row_styles` `{id,
row_styles}`, both broadcast `changed`) replaces the whole list and checks
it in `_checked_row_styles`: a list of at most 200 objects with only the
five keys, a known non-system enum attribute, a value among its choices,
colors `#rrggbb` or null, a boolean `bold`, at least one of the three set,
and no duplicate (attribute, value) pair. Invalid → REST 400 / WS -32602,
nothing written. Templates may declare `row_styles` (shape checked by
`templates.parse_template`, rules by the service when the template is
validated); the RBC template styles Object Type Heading green and
Information blue. Rules that hand-edited data no longer resolves are kept on
disk but ignored where styles apply (`_effective_row_styles`, mirrored by
`frontend/src/components/rowStyles.ts`): the requirements table, the Module
outline, the baseline view (the baseline's own rules), baseline compare
(`row_styles` per module entry: requirement id → rule, from the side the
requirement is listed for) and the DOCX export (heading and attribute
paragraphs: font color, shading, bold). In dark mode the frontend darkens
backgrounds and lightens font colors to a 4.5:1 contrast; stored colors and
the DOCX export are unchanged. Edited in View ▸ Row styles or the project
page's Row styles button.

### Module order and module folders

`project.module_order` stores the module tree in display order (project
format 7): a list of entries, each either a module `{"module": "<id>"}` or a
folder `{"folder": "<id>", "name": "...", "children": [...]}` whose
children are entries again (`models.ModuleFolder`, written in that key
order). Folders are purely organisational: module ids, requirement ids,
links, link configurations and attribute scopes never depend on them.
Pure tree helpers live in `reqman/module_tree.py`; the rules in
`ProjectService`.

- **Tolerant reading** (`module_tree.effective_tree`, used by
  `_module_tree`/`_module_order`): module entries naming a missing module or
  repeating one are skipped, a folder repeating an earlier folder id is
  dissolved into its place (its entries are kept), empty folders are kept,
  and modules the tree does not list follow at the top level in id order, so
  older projects (no key) list in id order as before. Folders nested deeper
  than 64 levels in a hand-edited file are ignored on read
  (`MAX_READ_FOLDER_DEPTH`). The flat module order (`GET
  /projects/{id}/modules`, exports, reports, save-as-template `modules`) is
  the tree depth-first. Reading never rewrites the file.
- **Tree** `GET /projects/{id}/module-tree` (WS `module_tree.get`): the
  effective tree in the `project.json` entry shape, every module once.
- **Folders**: `POST /projects/{id}/module-folders` `{name, parent_id?}`
  (WS `module_folder.create`) appends an empty folder to the top level or to
  folder `parent_id`; the id is server-allocated (a slug of the name,
  suffixed on collision with any folder of the project). Names are
  non-empty, at most 200 characters, stripped, and unique among sibling
  folders (case-insensitive). `PUT /projects/{id}/module-folders/{fid}/name`
  `{name}` (WS `module_folder.rename`) renames (id unchanged). `DELETE
  /projects/{id}/module-folders/{fid}` (WS `module_folder.delete`) deletes
  an empty folder only; a folder holding modules or folders is refused.
  `POST .../module-folders/{fid}/move` `{folder_id?, index?}` (WS
  `module_folder.move`) moves a folder with its content into another folder
  (null: the top level), never into itself or a descendant, keeping sibling
  names unique; `POST .../module-folders/{fid}/reorder` `{direction}` (WS
  `module_folder.reorder`) moves it one place among its siblings. Nesting is
  limited to 16 levels (`MAX_MODULE_FOLDER_DEPTH`).
- **Modules**: `POST /projects/{id}/modules` takes an optional `folder_id`
  (new modules are appended to that folder or the top level). `POST
  /projects/{id}/modules/{module_id}/move` `{folder_id?, index?}` (WS
  `module.move`) moves a module into a folder (null: the top level) at the
  0-based `index` among that folder's entries (absent or past the end:
  appended). `POST /projects/{id}/modules/{module_id}/reorder`
  (`{"direction": "up" | "down"}`, WS `module.reorder`) moves a module one
  place among the entries (modules and folders) of its own folder.
- Every change stores the whole effective tree; a no-op (a move to where the
  entry already is, a reorder past either end) writes nothing. Invalid
  input → REST 400 / WS -32602, an unknown folder → 404 / -32001, nothing
  written. Every mutation broadcasts `changed`. Commit messages
  (`history._describe_module_tree`) name folder additions, removals,
  renames and moves and module moves ("Module 'x' moved to folder 'Y'"); a
  pure reordering is "Module order changed". A format 6 → 7 upgrade alone
  produces no message.
- **Templates** may declare an optional `module_tree` (module keys and
  folders with template-local keys, see `templates.py`); a project created
  from it gets server-allocated folder ids slugged from the names, and
  modules the tree does not place follow at the top level. The RBC template
  declares Requirements (StRS, SyRS, SwRS), Tests (SysTest, SwTest) and Test
  Execution (SysTestExec, SwTestExec). Save as template writes the
  project's folders (folder ids as keys) when there is at least one.
- **Frontend**: the Project tab of the sidebar shows the tree (folders
  collapsible, collapse state per browser); context menus offer New folder,
  Rename folder, Delete folder (disabled while not empty), Move to folder
  and Move up/down for folders and modules, and module and folder rows can
  be dragged onto a folder row (or the project row for the top level).
  Module pickers (link configuration, reports, test result import,
  traceability chain) list modules grouped by folder path.

### Git-friendly persistence

Every project folder is meant to be a git repository (branches, history,
code review of requirement changes — see "Local-first" above and "Project
history (git)" below). Every JSON write
(`ProjectStore.save_*`/`save_link_pairs`/`config.save_data_dir`) uses
`indent=2` and a trailing newline, and dataclass field order is stable, so
diffs are minimal and readable instead of single-line blobs or
reordered-key noise.

Each `ProjectStore` write is atomic: the JSON goes to a temporary file in
the same directory, which is flushed and synced and then replaces the
target with `os.replace`. Syncing uses `fcntl(F_FULLFSYNC)` where it exists
(macOS, where `os.fsync` only reaches the drive cache), falling back to
`os.fsync` if that fails, and `os.fsync` everywhere else. The directory is not `fsync`ed, so after a power
loss right after a write the target may still hold its previous, complete
contents.
Operations that create several requirements (`add_requirement`,
`copy_requirement`, `import_csv`) go through
`ProjectService._write_new_requirements`: all requirement files are written
first, and `project.json` (with the advanced `id_counters`) last. If a write
fails, the requirement files already written by that operation are deleted
again and the error is re-raised, so a failed operation leaves the module
and the counters unchanged. `_allocate_requirement_id` also skips any id that
already exists on disk, so a crash between the two steps can never cause an
id to be reused.

## Project history (git)

Every project folder is its own git repository (modules are plain folders,
never submodules), driven through the `git` command line
(`backend/reqman/git_repo.py`, no Python dependency). Subprojects are
sibling folders in the data directory (`parent_project_id` is only a field),
so a project's repository never contains another project's files.

- **States** (`GET /projects/{id}/git`, WS `project.git_status`): `ready`
  (own repository on branch `current`: every change is committed), `paused`
  (own repository on another branch, a detached HEAD, or busy — see below:
  files are saved, no commits), `no_repo` (plain files; "Enable history (git)" creates the
  repository), `nested`, `foreign` (see below), `git_missing` (plain files,
  warning shown), `off` (`REQMAN_GIT=off`) and `error` (git cannot inspect
  the folder, e.g. "dubious ownership").
- **Paused while a merge, cherry-pick, revert or rebase is in progress, or
  with unresolved conflicts.** If `MERGE_HEAD`, `CHERRY_PICK_HEAD`,
  `REVERT_HEAD`, `sequencer/`, `rebase-merge/` or `rebase-apply/` exists
  in the git dir, or `git ls-files -u` lists unmerged entries (anywhere,
  not only data files, e.g. after a `git stash pop` conflict — a deliberate,
  conservative choice: `commit --only` would still work outside a merge,
  but the user is visibly mid-resolution), the project is `paused` with a
  message naming the operation ("A merge is in progress in the project
  repository: auto-commit is paused … Finish or abort it and resolve any
  conflicts to resume history"). Staging would otherwise mark the user's
  conflicts as resolved with the markers inside. The check runs again right
  before `git add`, so an operation started meanwhile skips the commit. Files are still saved;
  nothing is staged or committed, "Enable history" is 409, and the first
  change after the user finished commits everything saved meanwhile. The
  check costs a few file checks plus `git ls-files -u` twice per mutation.
- **Nested work trees are never touched.** If the data directory lies
  inside another repository's work tree (`git rev-parse --show-toplevel`
  from the project folder's parent succeeds — for example a data directory
  inside the ReqManTool checkout, or a data directory the user versions as
  one repository), the project is `nested`: no `git init`, no commit, even
  if the project folder has a repository of its own. The status names the
  enclosing repository and tells the user to move the data directory.
  Every writing git command names the project's repository explicitly
  (`--git-dir`/`--work-tree`) and inherited `GIT_DIR`/`GIT_WORK_TREE`/...
  variables are dropped, so git can never fall back to an outer repository.
  Discovery crosses file systems (`GIT_DISCOVERY_ACROSS_FILESYSTEM=1`), so
  an enclosing repository on another mount is still found.
- **Only the folder's own `.git` directory.** The project's repository is
  used only if `<project folder>/.git` is a real directory (not a symlink)
  and `git rev-parse --absolute-git-dir` and `--git-common-dir` both
  resolve to it (compared with the folder's real path plus `.git`, without
  following a `.git` link). A `.git` *symlink* or *file* (`gitdir:
  ../other/.git`, a separate git dir) or a linked worktree (`git worktree
  add`) would make a commit or `git checkout -b current` change another
  repository's HEAD, index and refs, so such a folder is `foreign`: "the
  project folder's .git points to another repository (separate git dir or
  linked worktree); history is not supported for it". No init, commit or
  checkout ever; files are saved as plain files; "Enable history" is 409
  (WS -32602). Supporting separate git dirs is a later product question.
- **New projects** (blank, from a template, subprojects) get `git init`,
  `HEAD` → `refs/heads/current`, `*.tmp` in `.git/info/exclude` (the
  temporary files of atomic writes) and an initial commit "Create project
  '<name>'". A template project is created in `.reqman-staging/` and
  renamed in; the repository is created after the rename. Existing projects
  get "Enable history (git)" (`POST /projects/{id}/git/enable`, WS
  `project.git_enable`, broadcasts `changed`): a folder without a
  repository is initialised the same way ("Enable history (git) for project
  '<name>'"); a repository of the user's on another branch gets a new
  `current` branch at its HEAD (`git checkout -b`, which changes no file);
  an already enabled project only commits pending changes. A nested or
  foreign folder, a merge/cherry-pick/revert/rebase in progress or
  unresolved conflicts, a detached HEAD, or an existing `current` branch
  that is not checked out is refused with 409 before anything changes (ReqManTool
  never switches branches, since that changes files); git turned off or
  missing is 400. A step that fails part-way (e.g. the repository was
  created but the initial commit failed) is 400 with the error recorded as
  `last_error`, and still broadcasts `changed` (REST and WebSocket), since
  the status changed.
- **Auto-commit.** Every public mutating `ProjectService` method is wrapped
  in `_records_history`: `ProjectStore.recording_writes` notes the projects
  whose files the operation wrote or deleted, and after the operation
  succeeded each is committed (REST and WebSocket alike; a failed operation
  commits nothing). Only data files are staged, committed and counted as
  pending: `git status`, `git add -A` and `git commit --only` take the
  pathspecs `:(literal)project.json`, `:(glob)modules/*/module.json`,
  `:(glob)modules/*/requirements/[!.]*.json` and `:(glob)links/[!.]*.json`
  (those that match a changed file), and the status output is filtered the
  same way, so `.DS_Store`, macOS AppleDouble files (`._X.json`, also
  skipped when the store lists requirements), `Thumbs.db`, editor backups (`*~`, `*.swp`), other
  files in the folder and anything the user staged themselves are left
  alone; deleted data files are still staged. Signing is off
  (`commit.gpgsign=false`) and **no hook ever runs**: every git command gets
  `-c core.hooksPath=/dev/null` (overriding the repository's
  `.git/hooks` and any `core.hooksPath` from repository, global or system
  config; on Windows, where git would look in a `dev\null` folder of the
  drive root, it is an empty directory made once per server process with
  `tempfile.mkdtemp` in the user's temp folder, made again under the same
  name if it goes missing, and `/dev/null` for the rest of the process if
  it cannot be made or is no real directory),
  inherited `GIT_CONFIG_PARAMETERS`/`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*`/
  `GIT_CONFIG_VALUE_*` are dropped so they cannot override it, and `git
  init` runs with `--template=` so no template hook (`init.templateDir`) is
  copied into a new repository. `-c core.fsmonitor=false -c gc.auto=0 -c
  maintenance.auto=false` keep a configured fsmonitor program and automatic
  gc/maintenance from running too. `.gitattributes` filters (e.g. Git LFS)
  are honoured on purpose: they define how the user's repository stores
  files. An auto-commit never waits for input or a hook. Inherited `GIT_*_PATHSPECS` variables are dropped too; commands
  given a requirement file path run with `GIT_LITERAL_PATHSPECS=1`. Data
  files edited by hand are included in the next commit and described like
  any other change.
- **Messages** are generated from the changed files (`history.py`:
  `describe_changes`, `commit_message`), e.g. "SYRS-0012: Status Draft →
  Reviewed; SYRS-0013 added": requirement added/removed/moved/reordered and
  per-attribute old → new values (link mirror attributes are left out; the
  link file entry "Link A → B added (Satisfies)" covers them), modules,
  attributes, link configurations, views and module order. Entries go into
  the subject while it stays within 72 characters ("; +N more"), the full
  list into the body (at most 200 lines). Auto-commits end with a
  `ReqMan-Session: <id>` trailer (server edition: followed by
  `ReqMan-User: <user id>`).
- **Squash.** A change amends the previous commit instead of adding one
  when that commit has the `ReqMan-Session` trailer (so never the initial
  commit or a user's commit), exactly one parent, the same author name and
  email, the same `ReqMan-User` trailer (server edition: the squash window
  is per logged-in user; local commits carry none), a committer time within the squash window (default 10 minutes,
  measured from the previous change, so a session lasts while changes keep
  coming), and no ref other than `refs/heads/current` contains it (no tag
  such as `baseline/<name>`, no `baseline` or other branch, no
  remote-tracking branch, no stash). The amended message describes the
  whole session (the session's parent → now). If a session's changes cancel
  out, the session commit is dropped (`git reset --soft HEAD~1`).
- **Author**: `REQMAN_GIT_AUTHOR_NAME`/`REQMAN_GIT_AUTHOR_EMAIL`, each
  falling back to what git itself would use (`git var GIT_AUTHOR_IDENT`:
  `GIT_AUTHOR_*`, the repository's and the user's git config), then to the
  login name and `<login>@localhost`.
- **Settings are environment variables** (read at start-up,
  `GitSettings.from_env`): `REQMAN_GIT=off` turns history off,
  `REQMAN_GIT_SQUASH_MINUTES` sets the window (`0` never squashes),
  `REQMAN_GIT_AUTHOR_NAME`/`_EMAIL` the author, `REQMAN_GIT_EXECUTABLE`
  the git command. They are deliberately not keys of the config file:
  a new persisted key needs a `CONFIG_FORMAT` bump, after which an older
  ReqManTool refuses to start with that config file, which is too high a
  price for settings that have good defaults; a settings UI (later) can
  add them with a bump and a migration.
- **Failures never lose data.** The files are written before git runs. A
  git error, or any unexpected error (e.g. while describing a hand-edited
  file; the message generator also tolerates wrong JSON types), never fails
  the mutation or project creation: it is logged and kept in memory as `last_error` (shown in the
  status, cleared by the next successful commit); since every commit
  stages all changed data files, the next change retries it. The status
  also reports `pending_changes` (data files that differ from HEAD), which
  survives a restart.
- **Concurrency and cost.** Writing git operations are serialized per
  project folder by a lock. Reads (status, history) take no lock and run
  with `GIT_OPTIONAL_LOCKS=0`. A mutation costs about 11–12 short git
  processes (state checks, status, HEAD, squash checks, the in-progress
  re-check, add, diff, cat-file, commit),
  roughly 80 ms on macOS; mutations run on the server's event loop, so git
  runs inline before the response.
- **History** (`GET /projects/{p}/modules/{m}/requirements/{r}/history`,
  WS `requirement.history`, read-only, no broadcast): `git log
  --first-parent` of the requirement file (newest first, at most
  `HISTORY_LIMIT` = 200 commits, `truncated` beyond) with each revision read
  through one `git cat-file --batch`; the JSON is compared revision by
  revision into `entries` (`change`: `added`/`modified`/`removed`, `diffs`
  per attribute, parent and order). **First-parent semantics:** only the
  commits of `current`'s first-parent chain are listed, each compared with
  its first parent, so a merge is listed with what it brought into
  `current` (its author and subject), the commits of a merged side branch
  are not listed themselves, and diverged branches cause no phantom diffs.
  No `--follow`: requirement files are never renamed (ids are never reused
  and requirements never move between modules), and rename detection would
  mistake a requirement removed and another added in one squashed commit
  for a rename. As a guard, history stops at a revision whose content names
  another requirement id; the oldest own revision is then listed as
  `added` (not `truncated`). `blame` maps each current attribute to the
  last listed commit that changed it. `created` / `modified` are the
  commit (author, dates, subject) of the newest listed `added` entry and of
  the newest entry, or null — the requirement's "created by" and "last
  changed by", computed from git and never stored in the requirement file
  (the detail panel shows them). `uncommitted` is true when the file
  differs from HEAD. A project without a readable repository (off, git
  missing, no repository, nested, foreign) answers `available: false` with
  the `reason`, not an error; a missing requirement is 404.
- **Baselines** (`POST/GET /projects/{id}/baselines`, WS
  `baseline.create`/`baseline.list`): a baseline is a named, immutable
  snapshot. Creating one (only in state `ready`; otherwise 409, or 400 when
  git is off/missing, like "Enable history") first commits any pending data
  changes (so the baseline covers exactly what is on disk), then creates an
  annotated tag `baseline/<name>` at HEAD whose message carries the
  description plus parseable trailers (`Reqman-Baseline`,
  `Reqman-Baseline-Date`, `Reqman-Tool-Version`, `Reqman-Project-Format`;
  tagger = the configured author), then **fast-forwards**
  `refs/heads/baseline` to HEAD (`git update-ref` with a compare-and-swap
  on its old value; the branch is created if missing, and never merged). If
  `baseline` is not an ancestor of `current` (moved by hand), the request is
  refused with 409 and nothing changes. Steps are ordered so a mid-way
  failure stays consistent: the pending-changes commit is exactly what an
  auto-commit would have made; a failed branch update deletes the tag again;
  a failure is 400 with `last_error` recorded and still broadcasts `changed`.
  Since tag and branch then contain HEAD, the squash rule (ref containment)
  guarantees the baselined commit is never amended — creating a baseline
  also closes the squash window. Names: trimmed, 1–100 characters of
  `A-Za-z0-9._-`, starting with a letter or digit, no `..`, not ending in
  `.` or `.lock`, not `current`/`compare` (reserved), checked with
  `git check-ref-format`; a duplicate is 409. Baselines are listed from the
  `baseline/*` tags, newest first; a lightweight or foreign tag (made by
  hand) is listed too with reduced fields (`annotated: false`, no
  description/versions).
- **Browsing a baseline** (`GET /projects/{id}/baselines/{name}` — project +
  modules — and `GET .../baselines/{name}/modules/{m}/requirements`; WS
  `baseline.get`/`baseline.requirements`; read-only, no mutation accepts a
  baseline): the data files are read from the tag's commit in memory
  (`ls-tree` + `cat-file --batch`), never through a checkout, and parsed
  with the normal model path (in-memory format upgrade; a baseline written
  by a newer ReqManTool is 409). A missing baseline or module is 404.
- **Comparing** (`GET /projects/{id}/baselines/compare?from=<name>&to=<name|current>`,
  WS `baseline.compare` `{project_id, from, to}`; read-only): `current`
  means the working files on disk including uncommitted changes. The two
  snapshots are compared requirement-level (`history.build_snapshot`/
  `compare_snapshots`): per module, requirement ids added/removed and
  per-attribute/parent/order diffs for modified ones (link-mirror/system
  attributes are left out — the `links` section lists added/removed pairs
  per configuration instead); modules added/removed and attribute
  definition changes are reported at summary level, plus a numeric
  `summary`. Both sides are read with one `ls-tree` and one `cat-file
  --batch` (or straight from disk), so a few thousand requirements compare
  in memory without extra git calls.
- **Recent commits** (`GET /projects/{id}/git/log?limit=` — default 20, cap
  200 — WS `project.git_log`, read-only): the first-parent log of
  `current`, newest first, for the project Git panel.
- **Frontend**: `GitStatusPanel.tsx` is the project Git panel (state,
  branch, pending changes, warnings, Enable, the last commits, and the
  baselines table with Create — `CreateBaselineModal.tsx` — Browse and
  Compare). `BaselinePage.tsx` (`/projects/{id}/baselines/{name}`) shows a
  baseline read-only ("Baseline <name> (read-only)": module picker plus the
  requirement tree, no editing, no context menus);
  `BaselineComparePage.tsx` (`/projects/{id}/baselines/compare?from=&to=`)
  picks both sides (baseline or current) and renders the summary and
  per-requirement change list. Baseline and git-log queries live under the
  project's query-key prefix, so live sync refreshes them; every local
  mutation invalidates the `git` and `baselines` keys.
- **Commit browser** (read-only, never broadcast; rules in
  `ProjectService.git_commits`/`git_commit`/`get_commit_snapshot`/
  `commit_requirements`, git work in `GitHistory.list_commits`/
  `commit_change`/`commit_files`):
  - List: `GET /projects/{id}/git/commits?module_id=&requirement_id=&offset=&limit=&author=&text=&since=&until=`
    (WS `commit.list` with the same names plus `project_id`). The
    first-parent history of `current`, newest first, at project scope, module
    scope (`git rev-list -- modules/<id>/`) or requirement scope (its file;
    needs `module_id`); the module/requirement must exist (404). `author` and
    `text` (message) are case-insensitive fixed strings (`--fixed-strings
    --regexp-ignore-case`, ≤ 200 chars, no control characters), `since`/
    `until` inclusive `YYYY-MM-DD` committer dates (server time zone; years
    outside git's 1970–2099 are clamped). Paging: `offset` + `limit`
    (default 50, cap 200) and `has_more`. Each entry is parsed from the raw
    commit object (`rev-list` for the ids, one `cat-file --batch` for the
    objects — no `--format` separators, so `\x1e`/`\x1f` in a message or
    name cannot split records), with its parents, the `baseline/*` tags on
    it, and `changed_requirements`: distinct requirement files changed
    against the first parent (one `diff-tree --stdin -z --name-only` over
    `DATA_PATHSPECS` for the whole page; within the scope's folder/file).
  - Details: `GET /projects/{id}/git/commits/{commit}` (WS `commit.get`
    `{project_id, commit}`). `commit` must be a full 40/64-hex object id
    (anything else, or no such commit, is 404 — never a ref or rev
    expression). The commit is compared with its first parent (nothing for
    a root commit) with the baseline compare engine (`compare_snapshots`,
    row styles included), from partial snapshots: the changed data files
    (`diff-tree -z` raw output; blobs read by object id) plus `project.json`
    and the touched modules' `module.json` on both sides (for names). Both
    project.json versions go through the format migrations first (newer
    format is 409; unreadable ones are compared as they are). `notes` adds
    `describe_changes` lines for changed project/module files (module
    renames, module tree reorders/moves, views, …). Also returns the full
    `message`, author/committer, `baselines` and `changed_files`.
  - Browse at a commit: `GET .../git/commits/{commit}/snapshot` and
    `.../git/commits/{commit}/modules/{m}/requirements` (WS
    `commit.snapshot`/`commit.requirements`): the baseline browse mechanism
    (`_tree_files`: `ls-tree -z` + `cat-file --batch` by blob id) at any
    commit.
  - Frontend: `CommitBrowserPage.tsx` (`/projects/{id}/commits?module=&requirement=&author=&text=&since=&until=`)
    with the shared `CommitList.tsx` (infinite query, "Load more");
    `CommitDetailsPage.tsx` (`/projects/{id}/commits/{commit}`) renders the
    shared `CompareResult.tsx` (also used by `BaselineComparePage`);
    `BaselinePage.tsx` doubles as the read-only "browse at commit" page
    (`/projects/{id}/commits/{commit}/browse`). Reachable from the Git panel
    (commit hashes, "Open the commit browser"), the requirement History tab
    (hash links, "Show in the commit browser"), the registry actions
    `project.commits`/`module.commits`/`requirement.commits` (sidebar
    project/module menus, requirement left- and right-click menus, View
    menu). Query keys live under `['projects', id, 'git', 'commits', …]`, so
    live sync and git-status invalidation refetch the lists.
- **Remote URLs** (set remote, "Add project from remote"; validated in
  `GitHistory._check_remote_url` before git runs): `ssh://`,
  `git+ssh://`/`ssh+git://`, `git://` and `https://` URLs, the scp-like
  `[user@]host:path` SSH syntax, and local folders such as a NAS mount or
  network share — an absolute path or a `file://` URL with an absolute
  path. Refused: `<transport>::` remote helpers (`ext::`, `fd::` run
  commands from the URL; like git, the name may start with a digit), a
  leading `-`, other schemes (`http://`, ...), upper-case schemes (git
  matches them case-sensitively: `FILE://` would be a remote helper) and
  relative paths. `ProjectService` also refuses a local folder (symlinks
  resolved) inside the data directory or equal to it, for set remote and
  for "Add project from remote": pushing there would write another
  project's refs outside its lock. A push to a local folder runs
  `--receive-pack="git -c core.hooksPath=<no hooks> -c ... receive-pack"`:
  git drops our `-c` overrides when it spawns receive-pack in the target
  repository, whose hooks and config would otherwise run programs on this
  machine. Besides the hooks, the command overrides
  `core.alternateRefsCommand=true` (run to list an alternate's refs),
  `receive.denyCurrentBranch=refuse` and `core.fsmonitor=false` (an
  `updateInstead` checkout in a non-bare target runs its filters and
  fsmonitor; a push to its checked-out branch is now rejected, 400), and
  `receive.autogc=false`, `gc.auto=0`, `maintenance.auto=false` (no gc or
  maintenance in the target); see `_LOCAL_RECEIVE_PACK_CONFIG`. Never for
  ssh/git/https, where the command would run on the server; push URLs
  mixing a local folder and a network remote are refused (400). On Windows
  a `C:`-prefixed URL is always a local path. git
  itself is restricted too: every command runs with
  `GIT_ALLOW_PROTOCOL=ssh:git:https` (never inherited) and `-c
  protocol.ext.allow=never -c protocol.fd.allow=never`; only the remote
  operations (ls-remote, fetch, push, clone) widen the allowlist to
  `ssh:git:https:file`. git's "transport 'x' not allowed" (e.g. a remote
  configured outside the app) is reported as "Remote transport 'x' is not
  supported; use an ssh, git, https or local-folder remote".
- Not yet (later slices): `merge --squash` of user branches, a settings UI.

## GitLab and GitHub integration

Builds on the git remotes. `backend/reqman/providers.py` is the one
provider abstraction (`Provider`, with `GitLabProvider` for GitLab REST v4
— gitlab.com or a self-hosted URL — and `GitHubProvider` for GitHub REST —
github.com via api.github.com, GitHub Enterprise via `<url>/api/v3`).
Every provider call runs server-side; the browser never talks to a
provider and never receives a token.

- **Connections** (`ProviderConnection`: id, provider, name, web URL, user
  name, created) are app-level settings in `connections.json` beside the
  config file (`config.connections_file()`, so `REQMAN_CONFIG_FILE`
  relocates it too), with its own `format_version`
  (`CONNECTIONS_FORMAT`); never in project data or git. Adding one checks
  the token with `GET /user` first; provider errors are passed on verbatim
  (`ProviderError`, 400). The URL must be `https://` (`http://` only for a
  loopback host).
- **Tokens** (local edition: a personal access token) live in the OS
  keychain through the `keyring` package (`KeyringTokenStore`, service
  "ReqManTool provider token", account = connection id), cached in process
  memory. `REQMAN_TOKEN_STORE=memory` keeps them in memory only (tests,
  scratch servers); a `ProjectService` built without a `ProviderService`
  has no connections and in-memory tokens. Responses only carry
  `has_token`.
- **Login account (server edition)**: a user signed in through a `github`
  or `gitlab` provider publishes / pushes / pulls with their login OAuth
  token instead of a stored connection — the pseudo connection id `login`
  (`LOGIN_CONNECTION_ID`), resolved per request by
  `ProviderService.login_source` (`reqman_server/login_account.py`
  `LoginAccountSource`; the local edition installs none, so the `login`
  id answers a refusal). `token()`/`provider()` read it fresh on every
  call (sessions refresh expiring tokens with the refresh token; nothing
  is cached, stored in a file, logged or sent to the browser),
  `connection_for_remote`/`git_auth_env` fall back to it when no stored
  connection matches the remote's host, so Push / Pull / auto-push of a
  remote on the login host authenticate with that one git process's extra
  environment only. `GET /auth/me` reports it (`login_account`, or null
  when the login provider hosts no git repositories — a plain OIDC single
  sign-on); publish / provider browsing with it skips the server-wide
  `connections.use` permission (a project admin needs no stored
  connection).
- **HTTP**: stdlib `urllib` (`ProviderHttp`; httpx is a test dependency
  only), a 20 s timeout on every request, responses capped at 8 MiB, and
  redirects are never followed (a token header must not go to another
  host).
- **git authentication**: `GitHistory.remote_auth` (set by
  `ProjectService` to `ProviderService.git_auth_env`) gives every remote
  operation (ls-remote, fetch, push, clone, review push, auto-push) on an
  https URL under a connection's URL extra environment for that one git
  process: `GIT_CONFIG_COUNT=2`, `http.<connection URL>/.extraHeader =
  Authorization: Basic base64(oauth2|x-access-token:<token>)` and
  `http.<connection URL>/.followRedirects=false`. The token is never on a
  command line (visible in the process list), in `.git/config`, in the
  remote URL or in a log; git scopes the header to URLs under the
  connection's URL. `GIT_ALLOW_PROTOCOL` and the other `-c` rules are
  unchanged. SSH remotes keep using the user's ssh agent.
- **Publish project** (`ProjectService.publish_project`): needs an own
  repository with a commit and no remote (409 otherwise); creates the
  repository (owner namespace / group / organisation, name, private by
  default), sets its HTTPS clone URL as the remote (`set_remote`, probed
  with ls-remote) and pushes (`push`). A failure after the repository was
  created says so and is broadcast (`project_changed`). `connection_id`
  omitted (or `"login"`) publishes with the login account.
- **Push permissions**: Push / Pull need `project.write`; Push additionally
  needs `project.admin`, or the project's opt-in `remote.allow_editor_push`
  (git config key `reqman.allow-editor-push` next to `reqman.autopush`,
  set by a project admin in the Git panel: `PUT
  /projects/{id}/git/allow-editor-push`, WS
  `project.git_set_allow_editor_push`; cleared with the remote). Auto-push
  runs after a commit as the committing user's roles allow.
- **Add project from GitLab/GitHub**: lists the user's repositories (GitLab
  `membership=true` with server-side search; GitHub `/user/repos`,
  filtered by name over at most 500 recently updated repositories), then
  `add_project_from_provider` looks the clone URL up on the provider (never
  taken from the client) and reuses `add_project_from_remote`.
- **Review workflow** (`start_review`): `GitHistory.push_review_branch`
  fetches the remote's `current`, refuses when there is nothing to review
  or the remote has no `current` yet, pushes `current` to
  `review/<UTC timestamp>` (or a given branch; never `current`/`baseline`,
  never forced), then a merge request (GitLab) / pull request (GitHub) into
  `current` is opened — or the open one from that branch reused — with a
  generated description: commit count, base/head, the newest baseline, and
  the changed requirements per module from `compare_snapshots(base, head)`
  (at most 200 ids listed). The request is tracked per instance in
  `.git/config` (`reqman.review.branch/number/url/connection`, cleared when
  the remote is removed or changed) and reported in
  `status.remote.review`; `review_status` reads its state, approvals
  (GitLab approvals API; GitHub latest review per user) and merge status
  live from the provider. The description asks for a merge commit or
  fast-forward, so ReqManTool clones can fast-forward with Pull.
- **Locking**: publishing and reviews are git remote operations like Push
  and Pull: they write no project files, so they are allowed while a
  project is locked.
- **API**: REST `GET/POST /providers/connections`, `PUT/DELETE
  /providers/connections/{id}`, `GET /providers/connections/{id}/namespaces`,
  `GET /providers/connections/{id}/repositories?search=&page=` (`{id}` may be
  `login`), `POST /projects/from-provider`, `POST /projects/{id}/git/publish`,
  `PUT /projects/{id}/git/allow-editor-push`, `GET
  /projects/{id}/git/provider`, `GET|POST|DELETE /projects/{id}/git/review`;
  WebSocket `provider.connections`, `provider.add_connection`,
  `provider.update_connection`, `provider.remove_connection`,
  `provider.namespaces`, `provider.repositories`,
  `project.create_from_provider`, `project.git_publish`,
  `project.git_set_allow_editor_push`, `project.git_provider`,
  `project.git_review_status`,
  `project.git_review_start`, `project.git_review_forget`. Connection
  changes broadcast `{"event":"connections_changed"}`; project operations
  broadcast `changed`.
- **Frontend**: Tools ▸ GitLab / GitHub connections
  (`ProviderConnectionsModal`), File ▸ Add project from GitLab / GitHub
  (`AddProjectFromProviderModal`), File ▸ Publish project / Request review
  (`PublishProjectModal`, `StartReviewModal`; registry actions in
  `actions/providerActions.ts`), and the Git panel's Publish button and
  Review section (state, approvals, refresh, forget). The connection select
  (`ProviderConnectionSelect`) offers the login account first (server
  edition, from `login_account` of `GET /auth/me`, exposed as
  `useAuth().loginAccount`, id `login`), then the stored connections with a
  token; the Git panel has the "Allow editors to push" switch next to
  auto-push. The review status
  query (`['git-review', id]`) is outside the `projects` prefix so project
  changes do not call the provider API.
- Not yet: issue links (issue URL attribute, create an issue from a
  requirement), Gerrit as a login/publish provider (see the backlog follow-up).

## Packaging, configuration and install

ReqManTool ships as one Python package (`backend/pyproject.toml`,
distribution `reqmantool`, SPDX licence `AGPL-3.0-only`) with a `reqman`
console script (`reqman/cli.py`). The version comes from
`reqman.__version__` only.

- **Release build** (`etc/build-dist.sh`, run by
  `.github/workflows/release.yml` on a `v*` tag that must equal
  `v<__version__>`): `npm run build` without `VITE_*` overrides, copy
  `frontend/dist` to `backend/reqman/static`, write the build id to
  `backend/reqman/BUILD`, copy `LICENSE`, then `python -m build` (wheel +
  sdist, attached to a GitHub Release). The copies are removed again
  afterwards and are gitignored, so a checkout never serves a stale bundle by
  accident. `.github/workflows/ci.yml` runs pytest and the frontend build +
  lint on every push and pull request.
- **Single origin.** When a built frontend is available (`reqman/static`,
  or `REQMAN_FRONTEND_DIR`; an empty value turns it off), `app.py` serves
  it: `/` and top-level files (`/favicon.svg`, ...) from the bundle,
  `/assets/*` as static files, and a browser page load of a client-side
  route gets `index.html`. Several client routes are also API paths
  (`/projects/{id}`, `/projects/{id}/modules/{mid}`, baselines, the
  traceability report), so the fallback (`SpaNavigationMiddleware`) only
  applies to GET/HEAD requests whose `Accept` header names `text/html` *and*
  whose path matches `SPA_ROUTE` (the routes of `frontend/src/App.tsx`).
  `fetch()` calls, `/ws`, and downloads (`.../export/...`, never a client
  route) always reach the API; the top-level file route is registered last,
  so it never shadows an API route. `frontend/vite.config.ts` mirrors the
  same pattern for `vite preview`. Without a bundle (a plain checkout) the
  backend is API-only as before and `/` redirects to `/docs`.
- **Frontend base URL** (`frontend/src/api/client.ts`): `VITE_API_BASE_URL`
  / `VITE_WS_URL` win; otherwise the dev server (`npm run dev`) uses
  `http://localhost:8899` / `ws://localhost:8899/ws` and a production build
  uses the page origin (`''` and `ws(s)://<host>/ws`).
- **Settings** (`config.py`): `data_dir`, `host`, `port`, `cors_origins`,
  `source_url`, each resolved as CLI flag > env var (`REQMAN_DATA_DIR`,
  `REQMAN_HOST`, `REQMAN_PORT`, `REQMAN_CORS_ORIGINS`, `REQMAN_SOURCE_URL`)
  > config file > default (`localhost`, `8899`, `http://localhost:5173`,
  `https://github.com/bula173/ReqManTool`; data: `example_database/`
  in a checkout, the per-user data directory in an installed package).
  `source_url` (http(s) only, else start-up fails with `ValueError`) is
  added without a `CONFIG_FORMAT` bump, like `host`/`port`.
  `reqman config` prints the effective values and their sources.
- **Which config file**: `REQMAN_CONFIG_FILE` / `--config`; else, when
  running from a git checkout whose `.reqman/config.json` exists, that file
  (the pre-package location, kept so existing checkouts keep their data
  folder — there is no migration step); else the per-user file
  (`$XDG_CONFIG_HOME/reqman/config.json`, `~/Library/Application
  Support/ReqManTool/config.json`, `%APPDATA%\ReqManTool\config.json`).
  `connections.json` lives beside whichever file is in use.
- **Writing the config file**: `save_data_dir` (the in-app folder switcher)
  and `reqman init` keep every key — known keys in `CONFIG_KEYS` order,
  unknown keys after them — with two-space indentation and a trailing
  newline, and never overwrite a file with a newer `format_version`.
  `host`, `port` and `cors_origins` were added without a `CONFIG_FORMAT`
  bump: they are optional, and an older ReqManTool ignores them (it only
  reads `data_dir`), though its folder switcher would drop them on save.
- **`reqman init`** writes the per-user config file (or `--config`), fills
  missing keys with defaults, and creates the data directory; no root/admin
  rights. **`reqman service install|uninstall`** (`service_unit.py`) writes
  a systemd `--user` unit (`~/.config/systemd/user/reqman.service`) or a
  launchd agent (`~/Library/LaunchAgents/io.github.reqmantool.reqman.plist`)
  running `<python> -m reqman serve --config <config file>` and activates it
  with `systemctl --user enable --now` / `launchctl bootstrap gui/<uid>`;
  `--dry-run` prints everything and changes nothing, `--no-activate` only
  writes or removes the file.
- **Dependencies**: runtime in `pyproject.toml` `dependencies` and
  `backend/requirements.txt` (kept in sync, used by `etc/start-backend.sh`);
  test-only (`pytest`, `httpx`) in the `test` extra and
  `backend/requirements-dev.txt`.
- **Edition** (`mode`): `REQMAN_MODE` > config key `mode` > `local`
  (`local` or `server`, anything else fails start-up). Added without a
  `CONFIG_FORMAT` bump, like `source_url`. See "Server edition" below.

## Server edition

Two editions run from one codebase. **local** (the default) is the
community edition: one user, no login, identity from git config — exactly
the behavior described in the rest of this document. **server** is the
multi-user edition: one shared instance, every user logs in. It is
delivered in slices: slice 1 is the mode switch and authentication; slice
2 adds users, roles, project membership, permission enforcement and the
admin pages ("Roles, membership and permissions" below); slice 3 adds
module edit locks ("Module edit locks" below); slice 4 adds attribution
and the audit log ("Attribution and audit" below); slice 5 derives roles
from GitLab/GitHub repository permissions and adds the reviewer role and
the requirement status guards ("Roles from repository permissions" below).
The licence-file gate comes later.

### Separation

The server code lives in its own package, `backend/reqman_server/`,
meant to move to the separately licensed `reqman-server` package
(LICENSING.md). It imports `reqman`; `reqman` never imports it at module
level. `app.py` calls `extensions.load_server_edition()` only when
`SETTINGS.mode == "server"`; a missing package fails start-up with a clear
message (`EditionUnavailableError`; `reqman serve` checks it before
starting uvicorn). With `mode: local` no server code is imported or run.
The package is **not** in the `reqmantool` wheel (`packages.find` includes
`reqman*` only); until it moves it is used from a checkout (`backend/` on
the import path).

### Extension points (`reqman/extensions.py`)

- `ServerEdition` (`install(app, EditionContext)`, `public_info()`): the
  edition adds its routes and its middleware. `EditionContext` carries the
  `Settings`, the config file's raw values (the edition reads its own
  `server` section), the `HOOKS` registry and the frontend directory.
- **Request identity**: `RequestIdentity(user_id, name, email,
  server_admin)` in a `ContextVar` (`current_identity()`,
  `identity_scope()`). The edition's middleware sets it for each
  authenticated request and for the lifetime of a WebSocket connection
  (FastAPI copies the context into the thread pool of sync handlers).
  `GitHistory._identity` (commit author and committer, so also the
  per-user squash window) and `GitHistory.user_identity` (who locked a
  project) use it first; without one (always, in local mode) they fall back
  to `REQMAN_GIT_AUTHOR_*` / git config / login name as before.
- `IdentityProvider` (`authenticate(scope)`): the shape of a request
  authenticator.
- `PermissionGuard` (`check(identity, action, project_id)`, raises
  `AccessDeniedError`, a `PermissionError` subclass so an OS EACCES is
  never mistaken for a refusal; local: `AllowAllGuard`). Actions are the
  `ACTION_*` constants: `project.read` / `project.review` /
  `project.write` / `project.admin` / `project.approve` (with a project
  id), `project.create`, `library.write`,
  `connections.use`, `connections.manage`, `git.remote_url`,
  `server.config`. `check_permission(action, project_id)` asks the guard
  about the current identity and is a type test only while the guard is
  `AllowAllGuard` (`guard_active()`), so the local edition pays nothing.
  Called by `ProjectService` (below), `GET /config` and the live-sync
  broadcast.
- `ProjectEvents` (`project_created(identity, id)`,
  `project_removed(identity, id)`; local: `NoProjectEvents`): called by
  `ProjectService` after a project was created (blank, template, clone) or
  removed; a listener failure is logged, never raised.
- `Hooks.rpc_methods`: extra WebSocket JSON-RPC methods of the edition
  (sync or async callables taking the params; core names win). The core
  dispatcher maps their errors like its own.
- `EditionContext.broadcast(event)` (send a live-sync event to every
  socket) and `EditionContext.project_exists(id)`.
- `ModuleLockService` (`HOOKS.lock_service`;
  `check_write(identity, project_id, module_id)`, raises
  `ModuleLockedError`: REST 423, WebSocket -32005; local: `NoLocks`):
  asked by `ProjectService` before a write to a module's content
  (`check_module_write`, skipped without a call while `NoLocks` is
  installed: `module_locks_active()`). See "Module edit locks".
- `EditionContext.module_exists(project_id, module_id)`,
  `EditionContext.project_ids()` and `EditionContext.project_remote_url(id)`
  (the project's git remote, `GitHistory.remote_url`; `None` without one).
- `AuditTrail` (`HOOKS.audit`; `record(identity, action, project_id,
  details)`; local: `NoAudit`, skipped without a call): told by
  `ProjectService` (`extensions.audit(...)`) after `project.create`,
  `project.lock` (reason, baseline), `project.unlock` (only when it was
  locked), `project.remove` (name) and `project.purge` (requirement count)
  succeeded; a failing trail is logged, never raised.

### Configuration

The `server` section of the config file (`reqman_server/settings.py` has
a full example): `auth_providers` (required: a list of `{id, type:
"gitlab" | "github" | "oidc", name, url (gitlab, default
https://gitlab.com; github, default https://github.com) | issuer (oidc),
client_id, client_secret | client_secret_env, scopes, allow_http,
git_hosts, github_app (a `github` provider created as a **GitHub App**:
same login flow, client and roles, but fine-grained read-only permissions
installed per organisation / repository — project discovery then lists
the app's installations' repositories instead of `GET /user/repos`)}`,
`base_url` (public URL of the backend, used for the OAuth
`redirect_uri`; default: the request's base URL), `frontend_url` (where
the browser goes after login; default: same origin), `cookie_secure`
(default true; false for `http://localhost` development),
`session_max_age_hours` (12), `session_idle_minutes` (120),
`persist_sessions` (true), `state_dir` (default: the config file's
directory), `admins` (user names / e-mail addresses), `admin_groups`
(identity provider groups whose members become server admins at login),
`group_roles` (`[{group, role: viewer | reviewer | editor | project-admin,
projects: ["*" | project id, ...]}]`), `project_creation` (`everyone`, the
default, or `admins`), `module_lock_idle_minutes` (60),
`require_module_checkout` (true), `repo_roles` (`{enabled: true,
cache_minutes: 5, grace_minutes: 60}`, or `true`/`false`; see "Roles from
repository permissions"), `discovery` (`{enabled: true, cache_minutes: 15,
max_repos: 200}`, or `true`/`false`; project discovery in the login
provider's repositories, SERVER_SETUP.md §8). Environment:
`REQMAN_AUTH_<ID>_CLIENT_ID` / `_CLIENT_SECRET`, `REQMAN_SERVER_BASE_URL`,
`REQMAN_SERVER_FRONTEND_URL`, `REQMAN_SERVER_COOKIE_SECURE`,
`REQMAN_SERVER_STATE_DIR`, `REQMAN_SERVER_ADMINS`. The client secret is
never logged, returned or sent to the browser. A server without a provider
refuses to start.

### Login (OpenID Connect, `reqman_server/oidc.py`)

GitLab is an OpenID Connect provider, so the `gitlab` type is the generic
OIDC client with the GitLab URL as issuer. Authorization code flow:
issuer discovery (`/.well-known/openid-configuration`, whose `issuer` must
match), redirect with `state`, `nonce` and a PKCE `S256` challenge; the
pending login (state, nonce, verifier; single use, ten minutes) is bound
to the browser by a random value in a short-lived `reqman_login` cookie,
which blocks login CSRF. The callback exchanges the code at the token
endpoint (client secret by HTTP Basic, or `client_secret_post` if that is
the only listed method, plus the PKCE verifier) and validates the ID token:
`iss` = the discovered issuer, `aud` contains the client id (`azp` = client
id when there are several audiences), `exp` (60 s leeway), `iat` not in
the future, `nonce`, `sub`. Name, user name (`preferred_username` /
`nickname`) and e-mail come from the ID token, completed from the
userinfo endpoint when missing (its `sub` must match). The ID token's
signature is not verified: OIDC Core §3.1.3.7 lets a client rely on TLS
for an ID token received directly from the token endpoint, which is the
only way this client gets one — so every endpoint must be https (http only
for a local host, or `allow_http` for tests), and `alg: none` is refused.
HTTP uses `urllib` (timeout, 1 MiB limit): no new dependency. Groups (for
`admin_groups` / `group_roles`) come from the `groups` claim (Keycloak,
Entra ID, GitLab userinfo) or GitLab's `groups_direct` ID token claim;
when a group setting is configured and the ID token has neither, from the
userinfo endpoint. They are stored on the user at each login.

### Sessions, CSRF and the middleware

Sessions are server-side (`sessions.py`): a random token in the
`reqman_session` cookie (HttpOnly, SameSite=Lax, Secure unless
`cookie_secure: false`, Path=/), stored as its SHA-256 in memory and in
`sessions.json` (owner-only permissions; `last_seen` written at most once
a minute). A session ends at logout, after the maximum age, or when idle.
Each session has a CSRF token (synchronizer token) returned by
`GET /auth/me`; every state-changing request (not GET/HEAD/OPTIONS) must
send it as `X-CSRF-Token` (403 otherwise). `AuthMiddleware` (pure ASGI)
sits inside `SpaNavigationMiddleware` (page loads of client routes get
`index.html` without data) and inside CORS (added last in server mode,
with `allow_credentials`, so a 401 carries CORS headers). Public:
`/auth/login/*`, `/auth/callback/*`, `/auth/logout`, `/edition`,
`/version`, `/about*`, `/health`, `/`, `/assets/*` and top-level files of
the bundle. Anything else without a session: REST 401
`{"detail": "Not logged in"}`; the WebSocket handshake is refused (close
4401 before accept = HTTP 403). WebSocket handshakes must come from the
server's own origin or `cors_origins` / `frontend_url` / `base_url`
(4403 otherwise); the session is re-checked on each received message and
the socket closed with 4401 once it ended. `POST /config` and `/fs/*`
(server data folder and file system) are limited to server admins in the
middleware; everything else is decided by the permission guard.

### Users (`users.py`)

`users.json` in the state directory: `id`, `name`, `email`, `username`,
`provider`, `subject`, `server_admin`, `active`, `created_at`,
`last_login_at`, `groups`, keyed by `(provider, subject)`; created at the
first login, name/e-mail/groups refreshed at each login, re-read when the
file is edited by hand. Admins: users matching `server.admins` (user name,
or an e-mail not reported unverified) or in one of `server.admin_groups`;
without `admins` the first user ever becomes the admin. The flag is never
removed automatically (the admin page can). `active: false` refuses logins
and ends sessions.

### Roles, membership and permissions (slice 2)

**Storage.** Memberships are server state, `memberships.json` next to
`users.json` (`memberships.py`): a list of `{project_id, user_id, role,
added_at, added_by}` sorted by project and user, re-read after a hand
edit. Not in the project data or its git repository: projects stay
edition-agnostic and can move between a local and a server installation
unchanged. A removed project's entries are deleted (`project_removed`), so
a new project reusing the id starts without members. Switching the data
folder keeps the file (memberships are keyed by project id only).

**Roles** (`permissions.py`; slice 5 adds `reviewer` and moves link
configurations, views, attributes and baselines to project admins, see
below): `viewer` (read), `editor` (also change the
project's data: requirements, modules, folders, links, link
configurations, views, attributes, row styles, references, imports,
baselines, push/pull, reviews), `project-admin` (also lock/unlock, remove,
purge the project, change its parent, enable history, git remote and
auto-push, publish, and manage its members). A user's role in a project is
the highest of their membership and the `group_roles` rules matching
their groups. Server admins may do everything everywhere. Server-wide:
`project.create` per `project_creation` (the creator becomes the
project's admin through `project_created`), `library.write` (templates,
prompts) for every user, and everything else — the GitLab/GitHub
connections (`connections.use` / `.manage`), cloning from or setting a
remote to an arbitrary URL (`git.remote_url`, which could reach any path
or internal host of the server), the data folder (`server.config`) —
for server admins only. The guard looks the user up on every check, so a
deactivation, admin flag or membership change applies at once, also to
open WebSocket connections.

**Enforcement (one guard, REST + WebSocket parity).** `ProjectService` is
decorated with `_install_permission_checks`: every public method whose
first parameter is `project_id` checks, before it runs (and before the
lock and sync-conflict guards), `project.write` for `_records_history`
mutations, the action set with `_requires` (`project.admin` for lock,
unlock, remove, purge, parent, enable history, remote, auto-push,
publish; `project.write` for push, pull, reviews, baselines), else
`project.read`. Explicit checks cover the rest: `create_project`
(`project.create`, plus `project.write` on the parent of a subproject),
`set_project_parent` (`project.write` on the new parent), cloning
(`project.create` + `git.remote_url`, or `connections.use` from a
provider), provider connections, template/prompt libraries
(`library.write`), the cross-project reads (link target project, new
references, `link_options`' other project, the prompt's selection
project), and the cross-project cleanup cascade (`_check_incoming_cleanup`
needs `project.write` on every referencing project whose links would be
deleted — refused with 403 before anything is written). `list_projects`
lists only readable projects; a project whose parent is hidden is listed
at the top level (like a dangling parent). `project_references` and
`incoming_links` leave out (or, for an own reference, name without
details) projects the user cannot read; the connections list is empty for
users without `connections.use`. Errors: `AccessDeniedError` → REST 403,
WebSocket error code -32003. `GET /config` returns an opaque
`data-folder:<hash>` id with `redacted: true` instead of the path to users
without `server.config`; `remove_project` accepts that id in place of the
path. Live sync: a `changed` event of a project is sent only to sockets
whose user may read it (the identity is remembered per socket);
`removed` events go to all.

**Admin API** (`admin.py`): `GET /auth/permissions` (also `permissions`
in `/auth/me`: `{server_admin, can_create_projects, roles: {project id:
role}, default_role}`), `GET /admin/users`, `PUT /admin/users/{id}`
(`active`, `server_admin`; never one's own account; deactivating ends the
user's sessions), `GET /admin/projects/{id}/members` (memberships plus
users with a group-rule role, marked `source: "group"`), `PUT
/admin/projects/{id}/members` (`{user: e-mail | user name | id, role}`),
`DELETE /admin/projects/{id}/members/{user_id}`. Users: server admins;
members: project admins of that project and server admins; a project
admin cannot leave the project without a project admin. WebSocket (via
`HOOKS.rpc_methods`): `auth.permissions`, `admin.users.list`,
`admin.users.update`, `admin.members.list`, `admin.members.set`,
`admin.members.remove`. Every change broadcasts `{"event":
"permissions_changed"}`; clients refetch their roles, the admin pages and
every project query.

### Module edit locks (slice 3)

An editor checks a module out for exclusive editing ("Check out for
editing"): a lock with owner, `since`, the owner's `last_activity` and a
name (at most 500 characters; required when the lock is taken — it labels
the commits of the session, see below). Anyone who may change the module
may check it out (`project.review`, so a reviewer can sign off). While it
is held, every write to
the module's content by anyone else — server admins included; they break
the lock instead — is refused with `ModuleLockedError` (REST 423 with the
owner, time and reason in `detail`; WebSocket error code **-32005**, kept
apart from the project lock's -32004 so clients can tell them apart). The
owner's writes refresh the lock; one unused for
`server.module_lock_idle_minutes` (default 60) expires (checked lazily on
every read; clients refetch the lock list every minute). With
`server.require_module_checkout` (default true) a write to an existing
module that nobody checked out is refused too (423,
"must be checked out"), while module creation and project-level changes
are not affected; set it to `false` and an unlocked module stays freely
editable — a checkout becomes opt-in, like an exclusive edit in DOORS.

**Commits of a checkout.** A write to a checked-out module labels the
auto-commit it produces: the subject line is
`[module][username][name]` (the checkout's name and the owner's login
name), with the changed files (`SYRS-0001 added`, …) listed in the body
under the usual `ReqMan-Session` / `ReqMan-User` trailers. Without a
checkout the subject stays built from the changed files
(`reqman.history.commit_message`).

**Storage.** `module_locks.json` in the state directory
(`reqman_server/module_locks.py`): `{project_id, module_id, user_id,
since, last_activity, reason}` (Unix seconds), sorted by project and
module; activity is written at most once a minute; hand edits are picked
up. Not in the project data or its git repository.

**What a lock covers (the core's choke points, `ProjectService`).**
`_records_history` asks the lock service for every mutation whose second
parameter is `module_id` (requirement add, update, move, reorder, copy,
remove; module purge; CSV and test result imports) after the project lock
and sync-conflict guards, before anything is written. Moving or reordering
a module in the tree only changes project.json and is exempt
(`_module_structure_only`); folders, views, attributes, row styles and
creating modules or link configurations are project-level and not
covered. Explicit checks: creating or removing a link checks the
configuration's **source** module only (the link and its pair file belong
to the source side, as in a DOORS link module; the mirror attribute
refreshed on the target requirement is derived data); removing a link
configuration checks its source module; narrowing an attribute's scope
checks the modules whose values it removes; purging the project checks
every module. The cleanup cascades of removing a requirement (mirrors in
other modules, links of referencing projects) are derived and not checked.
Removing a project is not blocked by module locks; its locks are dropped
(`project_removed`).

**Lock, release, break.** Check out: `project.write` (editors); again by
the owner refreshes it and replaces the reason; held by someone else: 423.
Release: the owner only (read access is enough, so a user demoted to
viewer can still give a lock back; someone else: 403); idempotent
(`released: false` when there was none). Break: `project.admin` (project
admins of the project, server admins). A lock is also released when its
owner logs out and has no other live session, and when the owner is
deactivated; a session that merely expires leaves the lock to expire on
its own.

**API.** `GET /projects/{id}/module-locks` (`{project_id,
require_checkout, idle_minutes, locks: [{project_id, module_id, user_id,
user_name, user_email, since, last_activity, expires_at, reason}]}`; ISO
times), `PUT /projects/{id}/modules/{module_id}/lock` (`{reason?}`),
`DELETE /projects/{id}/modules/{module_id}/lock`, `POST
/projects/{id}/modules/{module_id}/lock/break` (`{project_id, module_id,
lock, released, previous_owner}`). WebSocket (`HOOKS.rpc_methods`):
`module.locks`, `module.lock`, `module.unlock`, `module.break_lock`.
Every change broadcasts `{"event": "module_locks_changed", "project_id":
...}`, delivered only to users who may read the project. None of these
routes exist in the local edition.

**Frontend.** `api/moduleLocks.ts` (`useModuleLocks`, key `['projects',
id, 'module-locks']`, so `changed` and `permissions_changed` refetch it
too; `useCheckOutModule`, `useReleaseModule`, `useBreakModuleLock`).
`AppState.moduleLocks` (the open project's locks, server edition only) and
`AppState.userId`; `isModuleReadOnly` (project read-only, or the module
checked out by someone else, or a required checkout nobody holds) is what
`isActionAvailable` checks for `edits` actions that change module content
(`Action.editsModule`; default for requirement and link actions; set on
CSV import, module purge, test result import and pasting an AI result).
The module actions `module.checkOut` (dialog with a reason),
`module.release` and `module.breakLock` (confirmation) are hidden in the
local edition (`isSupported`); they are in the module node menu, the Edit
menu and as buttons on the module page. `ModuleLockBadge` ("Locked by X"
/ "Checked out by you", tooltip with time and reason) is shown on the
module page, its sidebar node and its tab; the module page also shows a
read-only notice, and inline editing, drag-and-drop and the outline's
drag moves follow `isModuleReadOnly`.

### Attribution and audit (slice 4)

**Commit authorship.** Commits made for a request are authored and
committed as the logged-in user (`RequestIdentity` name and e-mail from the
identity provider; slice 1). Each auto-commit made for a logged-in user
also carries a `ReqMan-User: <user id>` trailer after `ReqMan-Session`, and
a change only squashes into a commit with the same trailer, so a change of
user B never amends user A's pending commit — even when both accounts share
a name and e-mail address. Local commits carry no `ReqMan-User` trailer and
squash exactly as before; a server change never squashes into a local
commit (or the other way round).

**Created by / modified by.** Not stored: there are no `created_by` /
`modified_by` keys in requirement files and no format bump. A new
requirement key would need a project format bump (an older ReqManTool would
drop it on save), would make every edit rewrite the file, and would
duplicate what git already records with the right author in both editions.
The requirement history (`GET .../requirements/{r}/history`) returns them
as `created` / `modified` (see "Git history"); in the local edition the
author is the git identity. They need history to be on; a requirement whose
creation is older than the listed history has `created: null`.

**Live sync.** In the server edition every `changed` event (also with
`removed: true`) carries `by: {user_id, name}` of the user whose request
made the change; the local edition sends none. The frontend types it and
does not show it yet.

**Audit log** (`reqman_server/audit.py`): `audit.jsonl` in the state
directory, append-only JSON lines (`O_APPEND`, owner-only permissions),
one per event: `{time, action, user_id, user_name, project_id?,
target_user_id?, target_user_name?, ip?, details?}` (absent keys have no
value; strings cut at 500 characters). Actions: `auth.login` (provider,
`first_login`), `auth.login_failed` (provider, reason; only for a callback
that matches a login started in this browser — the callback is public, so
anything else is not logged, and nobody can fill the log anonymously),
`auth.logout`; `admin.user_update` (the flags set), `admin.member_set`
(role, previous role; nothing when the role is unchanged),
`admin.member_remove` (previous role); `module.lock_break` (module, reason
and time of the broken lock; the previous owner is the target user); and
from the core through `HOOKS.audit`: `project.create`, `project.rename`,
`project.lock`, `project.unlock`, `project.remove`, `project.purge`. Locks released by a
logout or a deactivation have no entry of their own. A write failure is
logged, never fails the action. No rotation: an administrator can move the
file away (a new one starts with the next event). Unparsable lines (hand
edits, a write cut short) are skipped when reading.

**Read API** (server admins only; others 403 / -32003): `GET /admin/audit?
limit=&before=&action=&user_id=&project_id=` and WebSocket
`admin.audit.list` (same params): `{entries, next_before}`, newest first,
read backwards from the end of the file; `limit` 1–500 (default 100);
`before` is the byte offset `next_before` of the previous page returned
(null: no older entries), so pages stay stable while entries are
appended; `action` is an action or a prefix ending in `.` (`auth.`);
`user_id` matches the actor or the target. Frontend: an "Audit log" tab in
`AdminModal` for server admins (`useAuditLog`, an infinite query under
`['admin', 'audit', filter]`, so `permissions_changed` refetches it; a
filter by kind, Refresh, "Load older events").

### Roles from repository permissions, reviewer and status guards (slice 5)

**Source of truth** (`reqman_server/repo_access.py`). A project whose git
remote lives on the GitLab or GitHub instance a user logged in with is
*bound* to that repository for the user: their role is their permission on
the repository, read with their own OAuth access token. Mapping: GitLab
Guest / Planner / Reporter and GitHub Read → `viewer`; GitHub Triage →
`reviewer`; GitLab Developer and GitHub Write → `editor`; GitLab
Maintainer / Owner and GitHub Maintain / Admin → `project-admin`. A
repository the user can see without a membership (public or internal) is
`Read` → viewer; one they cannot see (404) gives no role. GitLab:
`GET /api/v4/projects/:path` (`permissions.project_access` /
`group_access`) and `GET /projects/:id/members/all/:user_id` (inherited
group memberships; the GitLab OIDC `sub` is the user id), the highest
wins. GitHub: `GET /repos/{owner}/{repo}` `permissions`. The calls are
`Provider.repository_permission` in `reqman/providers.py` (`oauth=True`:
`Authorization: Bearer`; `ProviderError.status` carries the HTTP status).
The remote URL is matched to the provider's URL: https by host and the
URL's path prefix, ssh / scp-like by host, plus the provider's `git_hosts`.

**Combination.** When the project is bound for the user, the repository
permission *is* the role (one source of truth, revoking access in GitLab
revokes it here): in-app memberships and group rules of that user do not
apply to it (the admin page marks them "Not applied"). Unbound projects (no
remote, a remote elsewhere, a user who logged in with another provider or
a generic OIDC one) keep the in-app membership and group rules. Server
admins stay in-app and may do everything. `/auth/me`'s `permissions.roles`
holds the bound projects too (`null`: no access — `default_role` does not
apply to them) and `permissions.repo_roles` says where each comes from
(`{provider_id, provider_type, provider_name, path, repository,
permission, role, checked_at, error}`).

**Tokens.** The login stores the token endpoint's `access_token`,
`refresh_token` and expiry in the server-side session (`sessions.json`,
0600; `repr=False`), never in a response, log or the browser. An expired
token (GitLab: two hours) or a 401 is renewed once with the refresh token
(`OidcClient.refresh_tokens` / `GitHubOAuthClient.refresh_tokens`, one
refresh at a time). GitLab logins request `read_api` in addition to the
configured scopes while `repo_roles` is on (`read_repository` does not
grant the REST API; `api` is accepted instead). A session from before this
slice holds no token: the user is told to log in again.

**Cache and refresh.** Per (user, project) for `cache_minutes` (5);
resolved for every bound project at login (eight lookups in parallel) and
on demand. A failed lookup is retried after 30 s; meanwhile the last good
role is kept for up to `grace_minutes` (60), else the user has no access
and the 403 says why. Remote URLs are re-read at most every minute.
"Sync access now": `POST /admin/access/sync` (`{project_id?}`; WS
`admin.access.sync`; one project: its project admins; all: server admins)
reads the remotes again and looks the roles up again, bypassing the cache
(a failed lookup keeps the last good role within the grace period), for
every user with a live session; `POST /auth/permissions/sync` (WS `auth.permissions.sync`) does it
for the caller. Both broadcast `permissions_changed`.

**GitHub login** (`reqman_server/github_oauth.py`). GitHub is plain OAuth
2.0, not OpenID Connect, so `type: "github"` has its own client with the
`OidcClient` interface: authorize with `state` and PKCE, back-channel code
exchange (GitHub's HTTP 200 + `error` answer fails the login), identity
from `GET /user` (numeric `id` = subject, `login` = user name; the primary
verified address of `GET /user/emails`), organizations as groups when a
group setting is configured. Default scopes `read:user user:email`; a
GitHub App needs "Metadata: read" (and installation on the repositories),
an OAuth App needs `repo` to see private repositories' permissions. A
`github` provider with `github_app: true` **is** a GitHub App: the same
client, flows and token refresh (its expiring user-to-server tokens are
renewed with the session's refresh token), only project discovery
differs — it lists `GET /user/installations` and `GET
/user/installations/{id}/repositories` (the installations the user can
access) instead of `GET /user/repos`, and "Publish project" then needs
the App's extra permissions (SERVER_SETUP.md §2, "GitHub App").

**Reviewer and the status guards.** `reviewer` ranks between viewer and
editor: read, plus requirement sign-off. The core (`ProjectService`, only
while a guard is active, so the local edition is unchanged) finds the
status attribute by name like a report role (`REPORT_ATTRIBUTE_ROLES
["status"]`: Status, Review status, Approval status, ...) and compares its
values like attribute names. `update_requirement` needs `project.review`;
a change of any other attribute (values that differ from the stored ones)
still needs `project.write`, so a reviewer can change only the status.
Each status change (update, add, CSV import; before anything is written):
to or from **Approved** needs `project.approve` (project admin; later an
approver role) and approval only from **Reviewed** (400 otherwise, so
approval never skips the review); to **Reviewed** needs `project.review`
and independence — not by the requirement's last content author, the
author of the newest commit in its git history that created it or changed
an attribute other than the status and the system (link mirror)
attributes, compared by e-mail (else name) with the identity commits get;
a new requirement cannot start as Reviewed; without readable history, or
with uncommitted changes of the file, the sign-off is refused (the last
author is unknown); any other status change needs `project.review`.
Refusals are `AccessDeniedError` (403 / -32003).

**Project admin only** (tightened `_requires`): baselines
(`create_baseline`), lock/unlock, attribute definitions
(`add_attribute_definition`, `set_attribute_modules`), views
(`create_view`, `update_view`, `set_view_column_widths`, `remove_view`) and
link configurations (`create_link_configuration`,
`remove_link_configuration`). Row styles, references and folders stay
`project.write`.

**Messages** name the missing role and its source: "You may not change
its content in project 'rbc-gp': it needs Developer on
gitlab.example.com/rbc/rbc-gp (you have Reporter on
gitlab.example.com/rbc/rbc-gp, viewer)"; in-app: "Your role in project
'x' is reviewer (membership): you may not change its content; it needs the
editor role (a reviewer may change only requirement statuses)".

**Frontend.** `ProjectRole` gains `reviewer` (role lists, admin role
pickers, `ProjectRoleBadge` with the repository source, an orange badge
when the last check failed). `ProjectAccess.canReview` / `repoRole`;
`roles` entries win over `default_role`. On the module page a reviewer can
edit the status cell only (`statusAttributeId`, `canChangeStatusOnly` in
`actions/registry.ts`; the server decides each change). View, attribute,
link configuration and baseline controls need `projectAdmin`. The members
tab names the bound repositories, lists users with a repository role
(`source: "repo"`, from the cache) and has "Sync access now" (also on the
Users tab, for all projects). The login page shows a GitHub button.

### API and frontend

`GET /edition` (WS `edition.get`, both editions, public): `{"mode":
"local"}` or `{"mode": "server", "providers": [{id, type, name,
login_url}]}`. Server edition only: `GET /auth/login/{provider}?next=`,
`GET /auth/callback/{provider}` (redirects to `frontend_url + next`, or to
`/?login_error=...`), `GET /auth/me` (`{user, csrf_token, expires_at}`),
`POST /auth/logout`, `GET /health`. The frontend's `AuthGate` asks
`/edition` first; local (or no answer) shows the app as before. In the
server edition the API client sends `credentials: 'include'` and the CSRF
header, a 401 (or WebSocket close 4401) shows `LoginPage`, and the header
has a `UserMenu` (name, e-mail, Administration…, Log out); the data
folder switcher is shown to server admins only. Login is a full-page round
trip, so the live-sync socket connects again with the new cookie.

Roles in the frontend: `AuthValue.access` (`auth/context.ts`;
`serverAccess` from `/auth/me`'s `permissions`, `LOCAL_ACCESS` allowing
everything in the local edition) is part of the action `AppState`.
`isActionAvailable` disables an `edits` action when its project is
read-only (`isProjectReadOnly`: locked, or the role is below editor) and
checks an action's `requires` (`projectEditor`, `projectAdmin`,
`createProjects`, `connections`, `serverAdmin`). The project and module
pages, the outline, the sidebar tree and the Git panel use the same
checks for their inline controls; `ProjectRoleBadge` shows the role next
to the project name. `AdminModal` (from the user menu, for server admins
and project admins): Users tab (Active / Server admin switches) and
Project members tab (add by e-mail or user name, change role, remove).

## Backend layering

```
backend/reqman/
  models.py       Plain dataclasses — the persisted shape. No behavior.
  store.py        ProjectStore — disk I/O only (read/write/list/delete
                   JSON files). No business rules, no validation beyond
                   "does this file exist."
  service.py      ProjectService — all business rules live here: id
                   generation, attribute validation, nesting/cycle
                   checks, link direction enforcement, cleanup cascades
                   (remove/purge/remove-link-configuration). This is the
                   ONE place REST and WebSocket both call into.
  app.py           FastAPI app: REST routes (thin — parse the request
                   body, call a ProjectService method via the `_wrap`
                   helper which turns FileNotFoundError→404 and
                   ValueError→400, broadcast on success) and the `/ws`
                   WebSocket endpoint (a JSON-RPC 2.0 dispatch table,
                   `RPC_METHODS`, mapping method names to the same
                   ProjectService calls).
  reqif_export.py  Pure function, no reqman.models/service import (duck
                   typing) — builds a ReqIF XML string from plain
                   project/module/requirement/link data. Kept import-
                   isolated on purpose: it's easy to unit-test standalone
                   and can't create a circular import with service.py.
                   Every "text" attribute is ATTRIBUTE-DEFINITION-XHTML
                   (DATATYPE-DEFINITION-XHTML declared only when the
                   project has one): rich values are written from their
                   canonical tree as one xhtml:div (u/s become styled
                   spans), plain values as escaped text with xhtml:br.
  providers.py     GitLab/GitHub connections, keychain tokens and the
                   provider REST clients (stdlib urllib); called only by
                   ProjectService. See "GitLab and GitHub integration".
  csv_export.py    Same pattern as reqif_export.py, much smaller.
  xlsx_export.py   Same columns and row order as csv_export.py; string
                   values are stored as string cells, never formulas.
                   CSV and XLSX write rich text as its plain projection
                   (richtext.to_plain).
  docx_export.py   Same pattern; Word document of the requirement tree.
                   Rich text values keep their formatting (bold, italic,
                   underline, strike, colour, size, line breaks, nested
                   bullet/numbered lists via Word's built-in List styles).
  richtext.py      Rich text of "text" attributes: sanitiser, plain
                   projection (to_plain) and the canonical block tree
                   (blocks) the formatted exports walk.
  ordering.py      tree_order(): the shared row order of the CSV and XLSX
                   exports (depth-first, siblings by order then id).
  csv_import.py    The inverse of csv_export.py, same duck-typed pattern:
                   validates CSV text all-or-nothing and returns rows;
                   ProjectService.import_csv allocates ids and writes them.
  templates.py     Project template format: namespaced ids, reading the
                   built-in templates/ folder, shape checks (no domain
                   rules — ProjectService validates by building the project
                   in memory). templates/ holds the built-in JSON files.
  git_repo.py      GitHistory: per-project git repositories through the
                   git CLI (state detection incl. nested work trees, init,
                   auto-commit with squash, status, requirement history).
                   GitSettings: the REQMAN_GIT* environment settings.
  history.py       Pure functions, duck-typed like csv_export.py: commit
                   message entries from changed files, and a requirement's
                   change list + attribute blame from its file revisions.
  config.py        App settings (data_dir, host, port, cors_origins):
                   config file lookup, platform default locations,
                   env > file > default resolution (load_settings), and
                   save_data_dir for the in-app folder switcher (keeps the
                   other keys) — deliberately separate from ProjectStore,
                   which only knows how to read/write *inside* whatever
                   directory it's given. Also frontend_dir(): the bundled
                   frontend to serve, if any.
  cli.py           The `reqman` console script: serve (default), init,
                   config, service install|uninstall. Flags are applied as
                   env vars before the app is imported.
  service_unit.py  Per-user service definitions: systemd --user unit
                   (Linux), launchd agent plist (macOS); dry-run/no-activate
                   modes.
  version.py       version_info() → {version, build} for GET /version and
                   WS version.get. version = reqman.__version__ (the one
                   version source; frontend/vite.config.ts parses it at
                   build time). build = REQMAN_BUILD env var, else the
                   package's BUILD file (release builds), else
                   "<short commit> (<commit date>)" from git (computed once
                   per process, pre-warmed in the FastAPI lifespan at
                   start-up; the backend has no build step, so the commit
                   date is used), else "dev".
                   about_info(source_url) → {version, build, license,
                   source_url, source_ref, source_link} for GET /about and
                   WS about.get (AGPL-3.0 §13 "Source code" link):
                   source_ref = the commit of a "<commit> (<date>)" build or
                   a release-tag build id, None for dev, "-dirty" builds or
                   a checkout with uncommitted tracked changes (git status,
                   cached, pre-warmed with the build id); source_link =
                   <source_url>/tree/<ref> or the repository.
  licenses.py      python_third_party(): installed runtime dependencies of
                   reqmantool (package metadata, or requirements.txt in a
                   checkout, following Requires-Dist; extras skipped) with
                   licence, homepage and licence-file texts; GET
                   /about/third-party and WS about.third_party. Computed
                   once per process.
```

**Why a service layer at all, instead of putting logic in the route
handlers?** Because every mutation is reachable over both REST and
WebSocket, and they must behave identically (same validation, same
broadcast timing, same error mapping). Business logic in `app.py` would
have to be duplicated or awkwardly shared between two very different route
styles (FastAPI path operations vs. a dict-of-lambdas RPC table);
centralizing it in `ProjectService` means `app.py` on both sides is a thin,
almost mechanical adapter.

### REST API surface

All routes are project-scoped (`/projects/{project_id}/...`) except
`/projects`, `/templates`, `/config`, `/fs/browse`, `/version` and `/about`. See `backend/reqman/app.py` for
the authoritative, current route list — the summary here groups them by
resource:

| Resource | Routes |
|---|---|
| Projects | `POST/GET /projects` (`GET` lists unloadable projects as `{id, name, error}` stubs; `POST` body: `name`, `description`, optional `template_id`, optional `parent_project_id`), `GET /projects/{id}`, `PUT /projects/{id}/parent` (`{"parent_project_id": id or null}`), `PUT /projects/{id}/name` (`{"name": str}` renames the display name, id/folder stay; WS `project.rename`; 400 empty, 423 locked, admin in the server edition), `GET/PUT /projects/{id}/references` (`{"references": [ids]}`; WS `project.references` / `project.set_references`), `PUT/DELETE /projects/{id}/lock` (lock `{reason}` / unlock; WS `project.lock` / `project.unlock`), `GET /projects/{id}/removal` (WS `project.removal_info`), `DELETE /projects/{id}` (`{confirm_name, data_dir}`; WS `project.remove`; broadcasts `changed` with `removed: true`) — see "Project lifecycle: lock and remove"; a change of a locked project is 423, `PUT /projects/{id}/row-styles` (`{"row_styles": [...]}`; WS `project.set_row_styles`; 400 invalid) |
| History (git) | `GET /projects/{id}/git` (status; WS `project.git_status`), `POST /projects/{id}/git/enable` (WS `project.git_enable`, broadcasts `changed`, also after a part-way failure; 409 nested/foreign/detached/merge etc. in progress/`current` not checked out, 400 git off or missing or a failed step), `GET /projects/{id}/git/log?limit=` (WS `project.git_log`), `GET .../modules/{m}/requirements/{r}/history` (WS `requirement.history`) — see "Project history (git)" |
| Baselines (git) | `POST /projects/{id}/baselines` (`{name, description, lock?, lock_reason?}`; WS `baseline.create`, broadcasts `changed`; 409 duplicate name / not `ready` / `baseline` moved by hand, 400 invalid name or git off/missing), `GET /projects/{id}/baselines` (WS `baseline.list`), `GET /projects/{id}/baselines/{name}` (WS `baseline.get`; 409 for a newer-format baseline), `GET .../baselines/{name}/modules/{m}/requirements` (WS `baseline.requirements`), `GET /projects/{id}/baselines/compare?from=&to=` (`to`/`from` = a baseline name or `current`; WS `baseline.compare`) — reads are read-only; see "Project history (git)" |
| Templates | `GET /templates` → `[{id, source, name, description, modules: [{key, name, prefix, type}], error}]`, `POST /projects/{id}/save-as-template` (`{name, description}`; 409 on a name clash; broadcasts `templates_changed`), `POST /templates/import` (raw template JSON request body, `Content-Type: application/json` required (415 otherwise), UTF-8 with optional BOM, at most 1 MiB (413); invalid → 400, name clash → 409; returns the template summary; broadcasts `templates_changed`; WS: `template.import` `{text}`) |
| Attributes | `POST /projects/{id}/attributes` (optional `modules`, `module_types`; WS `attribute.add`), `PUT /projects/{id}/attributes/{attr}/modules` (`{modules, module_types, drop_values}`; WS `attribute.set_modules`) |
| Link configurations | `POST/GET /projects/{id}/link-configurations` (create: optional `target_project_id`, a referenced project), `DELETE .../link-configurations/{config_id}` |
| Modules | `POST/GET /projects/{id}/modules` (create: `{name, prefix, description, type?}`; WS `module.create`), `GET .../modules/{id}`, `POST .../modules/{id}/purge`, `POST /projects/{project_id}/modules/{module_id}/reorder` (`{"direction": "up" \| "down"}`; WS: `module.reorder`) |
| Requirements | `POST/GET .../requirements`, `GET/PUT/DELETE .../requirements/{id}`, `PUT .../requirements/{id}/parent` (move; body `parent_id`, optional `index`), `POST .../requirements/{id}/reorder`, `POST .../requirements/{id}/copy` |
| Links | `POST/DELETE /projects/{id}/links`, `GET /projects/{id}/links/options?from_id=&to_id=&from_project_id=` (WS `requirement.link_options`), `GET /projects/{id}/incoming-links?module_id=` (WS `link.incoming`; read-only) |
| Export | `GET /projects/{id}/export/reqif`, `GET /projects/{id}/export/csv` |
| Import | `POST /projects/{id}/modules/{module_id}/import/csv` (raw UTF-8 CSV request body, `Content-Type: text/csv` required (415 otherwise), at most 10 MiB (413); WS: `requirement.import_csv`), `POST .../modules/{m}/import/test-results/preview` and `POST .../modules/{m}/import/test-results` (raw JUnit XML / Robot output.xml body, `Content-Type: application/xml` or `text/xml` (415 otherwise), at most 64 MiB (413), options as query parameters; WS `requirement.preview_test_results` (read-only) / `requirement.import_test_results` with `xml` — see "Test result import") |
| Verification status | `GET .../modules/{m}/verification-status` (WS `report.verification_status`; read-only, requirements modules only) |
| Purge | `POST /projects/{id}/purge` |
| Data directory | `GET/POST /config`, `GET /fs/browse` |
| Version | `GET /version` → `{version, build}` (WS: `version.get`, read-only, no broadcast) |
| About / licences | `GET /about` → `{version, build, license, source_url, source_ref, source_link}` (WS `about.get`), `GET /about/third-party` → `{packages: [{name, version, license, homepage, license_text}]}` (WS `about.third_party`) — read-only, no broadcast. The frontend's own npm list is a static file of the bundle, `assets/third-party-licenses.json`, emitted by `frontend/plugins/thirdPartyLicenses.ts` in `vite build` (served from memory by `vite` dev) |
| Live sync | `WS /ws` |

### WebSocket JSON-RPC

`/ws` speaks JSON-RPC 2.0: `{"jsonrpc": "2.0", "id": ..., "method":
"requirement.add", "params": {...}}` → `{"jsonrpc": "2.0", "id": ...,
"result": ...}` or `{"jsonrpc": "2.0", "id": ..., "error": {"code": ...,
"message": ...}}`. Method names mirror the REST resources
(`project.create`, `module.create/purge/reorder`, `requirement.add/update/remove/
move/reorder/copy/link/unlink`, `link_configuration.create/list/remove`,
…) — see `RPC_METHODS` in `app.py` for the exact table. Methods listed in
`MUTATING_METHODS` broadcast a `changed` event after success (one per
project the operation wrote: removing a requirement also cleans up links in
referencing projects), same as the REST side (`manager.broadcast`, excluding the sender socket so a client
doesn't get a redundant refresh of its own change). Templates and
subprojects: `template.list`, `project.create` (optional `template_id`,
`parent_project_id`), `project.set_parent` (`{id, parent_project_id}`,
broadcasts `changed`), `project.rename` (`{id, name}`, broadcasts
`changed`), `project.set_references` (`{id, references}`,
broadcasts `changed`), `project.references` (`{id}`, read-only),
`link.incoming` (`{project_id, module_id?}`, read-only),
`project.set_row_styles` (`{id, row_styles}`,
broadcasts `changed`), `project.save_as_template` (`{id, name,
description}`) and `template.import` (`{text}`: the template file's
content), both in `TEMPLATE_MUTATING_METHODS`: they broadcast
`templates_changed` instead of `changed`. A 409 conflict on the REST side
is a `-32602` error over WebSocket, like other validation errors; a change
of a locked project (REST 423) is error code `-32004`. Project lifecycle:
`project.lock` / `project.unlock` (`{id}`, broadcast `changed`),
`project.removal_info` (read-only) and `project.remove` (`{id,
confirm_name, data_dir}`, broadcasts `changed` with `removed: true`).

## Frontend structure

```
frontend/src/
  api/
    client.ts     fetch wrapper (ApiError, base URL from Vite env vars)
    types.ts       TypeScript mirrors of the backend dataclasses
    queries.ts      Every TanStack Query hook — one per REST call, plus
                    queryKeys and invalidation. This is the ONLY file that
                    should call `api.*` directly.
  hooks/
    useLiveSync.ts  The one app-wide WebSocket connection; invalidates
                    queries on "changed", the template list on
                    "templates_changed", clears the cache on "root_changed"
                    (except the version query: same backend), refetches the version query on reconnect (or on first
                    connect if it failed while the backend was down)
  actions/          The shared action registry (see "Actions, menus and
                    shortcuts" below): types.ts, registry.ts (all actions,
                    menu layouts, shortcut dispatch), appActions.ts
                    (File/View/Help), requirementActions.tsx (Edit and the
                    row context menu), purgeActions.tsx (Edit ▸ Purge
                    module / project), AppActionsProvider.tsx (state,
                    dialogs, global shortcuts), ActionMenuEntries.tsx (renders
                    a menu layout), shortcuts.ts (matching, display, typing
                    guard)
  layout/
    AppLayout.tsx   Header (title, menu bar, data-folder settings, color
                    scheme) + Sidebar + routed <Outlet/> + VersionFooter
                    (frontend version/build, backend-version mismatch
                    warning), all inside AppActionsProvider
    MenuBar.tsx     File / Edit / View / Help, rendered from MENU_BAR
    AppDialogs.tsx  The dialogs actions open (new project, data folder,
                    save as / import template, import CSV, shortcuts, about)
  buildInfo.ts      FRONTEND_VERSION_INFO from the Vite `define` globals
                    __REQMAN_VERSION__/__REQMAN_BUILD__ (declared in
                    vite-env.d.ts, computed in vite.config.ts)
  components/       Modals and small reusable pieces (one file per modal:
                    NewProjectModal, NewModuleModal, AddAttributeModal,
                    LinkConfigurationModal, RequirementFormModal, LinkModal,
                    MoveRequirementModal, FolderPickerModal,
                    SaveAsTemplateModal, ProjectParentModal,
                    CreateBaselineModal,
                    EditableAttributeCell, DataDirSettings, Sidebar).
                    NewProjectModal picks an optional template and parent;
                    Sidebar renders projects as a tree by
                    parent_project_id (projectTree.ts: forest building,
                    indented project options, descendant lookup)
  pages/
    HomePage.tsx     redirects to the first project, or an empty state
    ProjectPage.tsx  project overview: parent/subprojects, the Git panel
                     (GitStatusPanel: state, branch, pending changes,
                     warnings, Enable history, last commits, baselines with
                     create/browse/compare), attributes, link
                     configurations, views, modules; New subproject,
                     Change parent and Save as template actions
    BaselinePage.tsx read-only view of the project at a baseline
                     ("Baseline <name> (read-only)"); no editing, no
                     context menus (/projects/{id}/baselines/{name})
    BaselineComparePage.tsx
                     baseline ↔ baseline/current comparison: side pickers,
                     summary, per-module requirement changes, attribute
                     definition and link changes
                     (/projects/{id}/baselines/compare?from=&to=)
    ModulePage.tsx   the requirements tree/table — the largest page;
                     owns the tree-flattening logic and renders the
                     right-click context menu from the action registry;
                     the detail panel is RequirementDetailPanel (Details
                     tab with "last changed by/when" label tooltips from the
                     attribute blame, History tab with per-attribute diffs)
```

Server state (projects/modules/requirements/config/link-configurations)
lives entirely in TanStack Query's cache — there is no separate Redux/
Zustand store. Local UI-only state (which modal is open, collapsed tree
nodes) is plain `useState` in the owning component; it isn't lifted higher
than it needs to be. State that the menu bar, context menus and shortcuts
share — the requirement selection and copy/cut clipboard, the active
attribute view, the detail panel toggle (remembered per browser in
`localStorage`), the colour scheme and the app-wide dialogs — lives in
`AppActionsProvider`.

### Actions, menus and shortcuts

Every command reachable from more than one place is an `Action` in one
registry (`frontend/src/actions/registry.ts`): `id`, `element` (element
type), `label`, optional `icon` and `shortcuts`, `isAvailable(ctx)`,
`run(ctx)`, and optionally `isChecked(ctx)` (toggles) or `items(ctx)`
(a generated submenu, e.g. one entry per view or template). The context is
the provider's `AppState` (route ids, loaded project/module/requirements,
selection, clipboard, views, templates, `commands`) plus a `target` whose
type depends on the element type: nothing for `app` actions, a requirement
for `requirement` actions. Explicit targets are passed keyed by element
type (`run(id, { requirement: row })`, `<ActionMenuEntries targets=…>`), so
each action of a mixed menu gets a target of its own type; without an
entry, `resolveTarget` supplies the default. Menus are data (`MenuEntry[]` layouts:
`MENU_BAR`, `REQUIREMENT_CONTEXT_MENU`) rendered by `ActionMenuEntries`, so
the menu bar and the row context menu show the same labels, shortcut hints
and enabled state. The menu bar and shortcuts target the selected
requirement; a context menu passes the clicked row as its `requirement`
target. Toolbar buttons (Paste at root, header data-folder and
colour-scheme buttons, New project in the sidebar, New subproject on the
project page) call `run(id)` too. Import, Export and Purge have no toolbar
buttons: they are File ▸ Import ▸ CSV (module open), File ▸ Export ▸
ReqIF/CSV/XLSX/DOCX (project open; exports cover the whole project) and
Edit ▸ Purge module / Purge project (`purgeActions.tsx`: red, behind a
divider, with a confirmation dialog), ready for later context menus.
The clipboard is scoped to the data folder it was filled in, and a data
folder switch (in this tab, or a `root_changed` live-sync event) clears the
clipboard and the selection, since ids repeat across folders.

Shortcuts are dispatched by one `keydown` listener in the provider: the
first registered action with a matching shortcut that is available runs;
a binding marked `whenNoTarget` only counts while the action's default
target is empty (so Ctrl/Cmd+V pastes as child of the selection — never
onto the clipboard requirement itself — or at the root only when nothing
is selected; with a selection it never falls through to the root). They are ignored while focus is in an input,
textarea, select, contenteditable element, the inline cell editor
(`data-inline-editor`), a combobox/listbox, an open menu or a dialog, and
Ctrl/Cmd+C/X are left to the browser while page text is selected.

To add a context menu for another element (sidebar node, column header,
tab): add the element type to `ActionTargets` with its target type, add its
actions to the registry, add a `resolveTarget` case for the menu bar /
shortcuts, and describe the menu as a `MenuEntry[]` layout rendered with
`targets={{ <element>: clicked }}`.

The menu bar follows the WAI-ARIA menubar pattern: `role="menubar"`, one
tab stop (roving tabindex), ArrowLeft/ArrowRight (Home/End) between the
top-level menus, ArrowDown opens one, and with a menu open ArrowLeft/
ArrowRight open the adjacent menu. Modified keys on a menu title (e.g.
Alt+ArrowDown) are left to the app shortcuts. Escape (also inside a
submenu) returns focus to the menu title; ArrowLeft in a submenu returns it
to the submenu's item (`SubMenu` in `ActionMenuEntries.tsx` works around a
Mantine 9.6 bug that would drop focus to `<body>`).

`ModulePage.tsx`'s requirements table is a **flattened tree**, not nested
JSX: `flattenTree()` walks the parent/child structure (sorted by `order`)
into a flat `{requirement, depth, hasChildren}[]`, respecting a
`collapsed: Set<string>` of hidden subtrees, and renders one `<Table.Tr>`
per visible row with `paddingLeft: depth * 20` on the first attribute cell.
This was a deliberate choice over recursive JSX so the result is still a
real `<table>` (sortable columns, consistent row actions, no manual
virtualization concerns) rather than a tree widget bolted onto a table.

## Extending the system

The `reqman-feature` and `reqman-validation` Claude Code skills in
`.claude/skills/` encode the implementation and validation checklist this
project follows — read them before making a change. In short:

1. Model change → `models.py` (+ `store.py` if new file I/O is needed).
2. Business rule → `service.py`. This is where validation, id allocation,
   cascade cleanup, and cross-cutting invariants belong — never in a route
   handler.
3. Expose it → a REST route *and* (if it's a mutation) an RPC method in
   `app.py`, both calling the same service method, both added to
   `MUTATING_METHODS`/broadcasting if they mutate.
4. Frontend → a typed function in `api/queries.ts`, then the UI.
5. Tests → `backend/tests/test_service.py` for business rules,
   `backend/tests/test_api.py` for the REST/WebSocket contract. Every
   feature in this codebase so far has both.

## Testing

- `backend/tests/test_service.py` — exercises `ProjectService` directly
  against a `ProjectStore` over a `tmp_path`, no HTTP involved. This is
  where most business-rule edge cases (cycle detection, direction
  enforcement, id allocation, cleanup cascades) are covered.
- `backend/tests/conftest.py` — runs every test with `REQMAN_GIT=off` and
  git isolated from the user's configuration (`GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_CONFIG_NOSYSTEM=1`, fixed author/committer). `test_git_history.py`,
  `test_git_history_edge_cases.py`, `test_git_history_safety.py` and
  `test_git_history_conflicts_and_isolation.py` opt in, with repositories
  in `tmp_path` only (never in the checkout).
- `backend/tests/test_api.py` — exercises the FastAPI app via
  `TestClient`, including WebSocket round-trips and the live-sync
  broadcast (opens two WebSocket connections, mutates via REST, asserts
  the second connection receives the `changed` event).
- Frontend: `npm run build` (TypeScript strictness catches most
  regressions) and `npm run lint`; there is no frontend test suite yet.

Run everything with `cd backend && .venv/bin/python -m pytest -q` and
`cd frontend && npm run build && npm run lint`.
