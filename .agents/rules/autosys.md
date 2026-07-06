---
trigger: always_on
---

# AutoSys Clone — Reference Rules (Always On)

This project (`autosys-astronomer`) is a clone of Broadcom AutoSys Workload
Automation (AE + WCC). Source manual (3,731 pages, AutoSys WA 12.0):
`/Users/Raghav/Quest1/autosys-astronomer/auotsys/ca-workload-automation-ae-amp-workload-control-center-validate-source.pdf`

For anything beyond this digest, consult the `autosys-manual` skill
(`.agents/skills/autosys-manual/`) — it loads the relevant chapter on demand
instead of bloating every request. This file only holds facts that should be
true on *every* request, because getting them wrong breaks compatibility
silently.

## 1. Architecture — components you must model

- **Server**: the machine AutoSys is installed on; hosts scheduler,
  application server, agent, web server, SDK, client.
- **Scheduler**: evaluates job dependencies/calendars and decides when jobs
  run. Named `<INSTANCE>_SCH` (e.g. instance `ACE` → scheduler `ACE_SCH`).
  Two schedulers must never share an instance name (non-HA: two running
  schedulers; HA: one primary, one shadow).
- **Application server**: the API layer clients talk to (JIL, autorep,
  sendevent, Web UI services, SDK-based binaries all go through it).
- **Event server**: the database.
- **Web server**: Apache Tomcat. Default web services port **9443**.
  Tomcat → CA EEM (security) default port **5250**.
- **Agent**: runs on every machine that executes jobs; supports command
  jobs, FTP jobs, file-watch, machine monitoring (CPU/disk/IP/process/log),
  SNMP.
- **Client**: any executable calling the application server API — JIL,
  autorep, Web UI backend, SDK-linked binaries.
- **Instance**: a licensed AutoSys install identified by a 3-char uppercase
  alphanumeric ID (env var `AUTOSERV`), default `ACE`. Multiple instances
  can share one binary install but must have distinct instance IDs.

## 2. Job object model

Everything is a "job" row (`ujo_job`, keyed by `joid`, distinct from the
human-readable `job_name`). Box jobs are containers; child jobs reference
their parent via `box_name`. Box jobs evaluate to SUCCESS when all child jobs
succeed (or `box_success` condition is true), and FAILURE when any child fails
(or `box_failure` condition is true). Job types seen in the manual: command jobs,
box jobs, file-watcher jobs, FTP jobs, application-services jobs (SAP,
PeopleSoft, Informatica, Micro Focus, web service, remote execution, wake-on-
LAN), plus user-defined job types (`insert_job_type` JIL subcommand).

### Job status — string names (JIL/CLI facing)
SUCCESS, FAILURE, TERMINATED, ON_ICE, ON_HOLD, ACTIVATED, INACTIVE,
STARTING, RUNNING, QUE_WAIT, RESWAIT, PEND_MACH, RESTART, WAIT_REPLY,
SUSPENDED, ON_NOEXEC.

### Job status — integer codes (as stored in `ujo_proc_event.status`)
```
1  RUNNING
3  STARTING
4  SUCCESS
5  FAILURE
6  TERMINATED
7  ON_ICE
8  INACTIVE
9  ACTIVATED
10 RESTART
11 ON_HOLD
12 QUE_WAIT
13 WAIT_REPLY
14 PEND_MACH
15 RESWAIT
16 ON_NOEXEC
17 SUSPENDED
```
(Code 2 is completely undocumented in the full manual — don't assume a value
for it.) `max_exit_success` on a job definition controls the exit-code
threshold that maps a process exit code to SUCCESS vs FAILURE — do not
hardcode "exit code 0 = success" as the only rule.

## 3. JIL — the definition language

JIL subcommands you must support if parsing/emitting job definitions:
`insert_job`, `update_job`, `delete_job`, `override_job` (one-time,
next-run-only override), `rename_job`; `insert_machine`, `update_machine`,
`delete_machine`; `insert_job_type`, `update_job_type`, `delete_job_type`;
`insert_monbro`/`update_monbro`/`delete_monbro`; `insert_blob`/`delete_blob`;
`insert_glob`/`delete_glob`; `insert_xinst`/`update_xinst`/`delete_xinst`
(external/cross-instance); `insert_resource`/`update_resource`/
`delete_resource` (virtual resources for load balancing);
`insert_connectionprofile`/`delete_connectionprofile`.

### Core attributes (non-exhaustive, see `jil-core-attributes.md` for full)
`insert_job`, `job_type`, `box_name`, `condition`, `description`, `group`,
`owner`, `resources`, `machine`, `command`, `auto_hold`, `avg_runtime`,
`date_conditions`, `run_calendar`, `exclude_calendar`, `days_of_week`,
`job_load`, `must_complete_times`, `must_start_times`, `priority`,
`run_window`, `start_times`, `start_mins`, `timezone`, `n_retrys`,
`envvars`, `chk_files`, `profile`, `ulimit`, `fail_codes`,
`max_exit_success`.

### Dependency / condition syntax
```
condition: [(]condition[)][(AND|OR)[(]condition[)]]
condition: [(]condition,look_back[)][(AND|OR)[(]condition,look_back[)]]
```
Operators (long form / short form, interchangeable but case must not be
mixed within one statement):
- `success(job)` / `s(job)` — job_name status is SUCCESS
- `failure(job)` / `f(job)` — status is FAILURE
- `terminated(job)` / `t(job)` — status is TERMINATED (killed)
- `done(job)` / `d(job)` — SUCCESS, FAILURE, or TERMINATED
- `notrunning(job)` / `n(job)` — anything except STARTING, RUNNING,
  WAIT_REPLY, RESTART, SUSPENDED
- `exitcode(job)=N` — exact exit code match
- Global variable: `VALUE(NAME)=something`
- Logical combinators: `AND`/`&`, `OR`/`|`; parentheses force precedence,
  left-to-right evaluation.
- Look-back: second argument in the parens, e.g. `success(job1,12)` =
  succeeded within the last 12 hours; `success(job1^PRD,24)` = cross-instance
  with look-back. Look-back does NOT apply to global-variable conditions.
- Cross-instance: `status(JOB_NAME^INSTANCE)`.
- If a condition references an undefined job, it evaluates **false**
  (doesn't error) — jobs dependent on it simply never run. Match this
  behavior exactly; don't throw where AutoSys silently no-ops.
- Multi-job AND conditions are NOT same-cycle aware: `condition:
  s(JobA)&s(JobB)` fires whenever both have ever most-recently succeeded,
  not necessarily from the same run/day. Grouping in a box is the documented
  workaround — replicate this quirk, don't "fix" it.

## 4. CLI utilities to replicate

- **`autorep`**: client-side reporting tool, reads only (never mutates).
  Reports jobs (`-J`), machines (`-M`), connection profiles (`-C`),
  applications (`-I`), groups (`-B`), global variables (`-G`), queued jobs
  (`-Q`), external instances (`-X`), virtual resources (`-V`), blobs
  (`-z`/`-jb`). Flags for detail level: `-d` (detail), `-s` (summary), `-q`
  (queue), `-p` (process/current), `-R run_num`. Requires read access to the
  object being reported.
- **`sendevent`**: the mutation/event-injection command — changes job
  status, starts/stops jobs, holds/off-holds, sets globals, posts comments,
  releases resources, sends alarms, answers manual-intervention prompts.
  `CHANGE_STATUS` sets status directly (e.g. force SUCCESS/FAILURE) but does
  **not** delete existing job history — replicate as an insert/append to
  event history, not a destructive update.
- **`jil`**: takes JIL script input (stdin or file) containing one or more
  subcommands + attribute statements, applies them to the DB.
- **`chase`**: verifies a job that is reported STARTING/RUNNING is actually
  alive on its target machine (detects stuck/orphaned jobs).
- **`autoping`**: verifies connectivity between server, agent, and client
  (three-way handshake check, not just agent reachability).
- **`autocal`**: manages calendars used by `run_calendar`/`exclude_calendar`.

## 5. Database — internal schema conventions

Internal tables use the `ujo_` prefix (see `database-table-names.md` for the
full extracted list of ~225 identifiers). Key ones:
- `ujo_job` — job definitions, PK `joid`, has `job_name` column.
- `ujo_job_status` — current-run status per job.
- `ujo_proc_event` — event/status history, joined to `ujo_job` via `joid`;
  `status` column stores the **integer** code from section 2, and
  `event_time_gmt` is a Unix timestamp (matches the `time0` utility's
  output format).
- `ujo_job_runs` — run history.
- `ujo_job_tree` — box/child hierarchy.
- `ujo_calendar` / `ujo_calendar_desc` — calendars.
- `ujo_cred*` — credential storage (owner/EEM credentials).
- `ujo_alarm`, `ujo_audit_*` — alarms and audit trail.

Don't invent column names beyond what's documented — if a schema detail
isn't in the skill's reference files, say so explicitly rather than guessing
a plausible-looking column name; a clone's on-disk compatibility depends on
getting these exactly right.

## 6. General rules for this project

- When JIL syntax, CLI flags, status semantics, or dependency-evaluation
  edge cases are ambiguous, check the skill's reference files or the source
  PDF before inventing behavior — AutoSys has decades of documented quirks
  (e.g. the AND-condition same-cycle issue above) that a "clean" reimplementation
  would accidentally break compatibility with.
- Preserve AutoSys's exact terminology (job/box/machine, JIL, condition,
  look-back, instance) in code, APIs, and docs rather than renaming things
  to more "modern" terms — this is a compatibility clone, not a reinterpretation.
- Treat `autorep` as strictly read-only and `sendevent`/`jil` as the only
  mutation paths, matching the manual's separation of concerns.
- Status codes are the single most load-bearing compatibility surface
  (external tools/scripts may depend on the exact integer values) — never
  renumber them.