# AutoSys — Complete Developer Guide

## 1. What is AutoSys?

**Broadcom AutoSys Workload Automation (WLA)** is an enterprise **job scheduler**.
Its job: run automated tasks (scripts, programs, database queries, file transfers) at the
right time, on the right machine, in the right order — reliably, 24/7, at enterprise scale.

Think of it as **the OS cron job — but for an entire enterprise**, with dependencies,
alerting, retries, calendars, and cross-machine coordination built in.

> Companies use AutoSys to run thousands of daily jobs: ETL pipelines, payroll processing,
> report generation, file transfers, database maintenance, batch billing — all automated.

---

## 2. AutoSys Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    AutoSys Architecture                 │
│                                                         │
│   ┌──────────────┐        ┌─────────────────────────┐   │
│   │  WCC / GUI   │◄──────►│   Event Server (EEM)    │   │
│   │ (Ops/Define) │        │  (Scheduler brain)      │   │
│   └──────────────┘        └────────────┬────────────┘   │
│                                        │                │
│              ┌─────────────────────────┼──────────────┐ │
│              ▼                         ▼              ▼ │
│       ┌────────────┐           ┌────────────┐  ┌──────┐ │
│       │  Agent on  │           │  Agent on  │  │ ...  │ │
│       │  Server A  │           │  Server B  │  └──────┘ │
│       └────────────┘           └────────────┘           │
└─────────────────────────────────────────────────────────┘
```

| Component | Role |
|---|---|
| **Event Server (EEM)** | The brain. Tracks all job states, fires jobs when conditions are met. Runs on a dedicated server backed by Oracle/MSSQL. |
| **WCC** (Workload Control Center) | The GUI. Ops team uses it to monitor, define, and manage jobs via a browser. |
| **Agents** | Lightweight daemon processes installed on every target server. They receive commands from the Event Server and execute jobs locally. |
| **JIL** | The language used to define everything (see Section 3). |

---

## 3. The JIL Language (Job Information Language)

JIL is the **core of AutoSys**. It is a **declarative, text-based config language**.
You write a text file describing a job, feed it to AutoSys, and it manages execution.

### 3.1 Basic JIL Structure

Every JIL stanza starts with `insert_job` and lists attributes:

```jil
/* This is a comment */
insert_job: job_name   job_type: CMD
owner: svc_account
command: /opt/scripts/run_etl.sh
machine: prod-server-01
std_out_file: /logs/job_name.out
std_err_file: /logs/job_name.err
alarm_if_fail: 1
```

### 3.2 Job Types

| `job_type` | What it does | Airflow equivalent |
|---|---|---|
| `CMD` | Runs a shell command / script | `BashOperator` |
| `FTP` | File transfer (FTP/SFTP) | `SFTPOperator` |
| `BOX` | Container of jobs (a workflow) | A **DAG** itself |
| `DB` | Runs a database stored procedure/query | `PostgresOperator`, `OracleOperator` |
| `OMTF` | Monitors a file/condition | `FileSensor` |
| `i5` / `PeopleSoft` / `SAP` | System-specific job types | Custom operators |

### 3.3 The BOX — AutoSys's Workflow Container

A `BOX` is the equivalent of an Airflow DAG. It groups related jobs:

```jil
/* Define the Box (workflow container) */
insert_job: daily_etl_box   job_type: BOX
owner: svc_etl
start_times: "06:00"
days_of_week: mo,tu,we,th,fr

/* Job 1 — inside the box */
insert_job: extract_data   job_type: CMD
box_name: daily_etl_box
command: /scripts/extract.sh
machine: etl-server-01

/* Job 2 — depends on Job 1 */
insert_job: transform_data   job_type: CMD
box_name: daily_etl_box
command: /scripts/transform.sh
machine: etl-server-01
condition: success(extract_data)

/* Job 3 — depends on Job 2 */
insert_job: load_data   job_type: CMD
box_name: daily_etl_box
command: /scripts/load.sh
machine: etl-server-02
condition: success(transform_data)
```

This defines the pipeline: `extract_data → transform_data → load_data`

---

## 4. Key JIL Attributes — The Full Reference

### 4.1 Scheduling Attributes

| Attribute | What it does | Example |
|---|---|---|
| `start_times` | Time(s) to run | `"06:00,18:00"` |
| `start_mins` | Minute offsets past the hour | `"0,15,30,45"` |
| `days_of_week` | Days to run | `"mo,we,fr"` or `"all"` |
| `run_calendar` | Named custom calendar | `"biz_days_2024"` |
| `exclude_calendar` | Exclude these dates | `"us_holidays"` |
| `date_conditions` | Enable complex date logic | `"1"` (enables it) |
| `days_of_month` | Specific days of month | `"1,15"` (1st and 15th) |
| `look_back` | How far back to look for missed runs (hours) | `"4"` |

### 4.2 Dependency / Condition Attributes

| Attribute | What it does | Example |
|---|---|---|
| `condition` | Dependency on another job's state | `success(job_a) & success(job_b)` |
| `box_name` | Which box this job belongs to | `daily_etl_box` |

**`condition` operators:**

```
success(job_name)    — runs if job_name completed successfully
failure(job_name)    — runs if job_name failed
done(job_name)       — runs if job_name is done (success or fail)
notrunning(job_name) — runs if job_name is not currently running
& (AND)              — both conditions must be true
| (OR)               — either condition can be true
```

### 4.3 Execution Attributes

| Attribute | What it does | Example |
|---|---|---|
| `command` | The shell command to run | `/opt/etl/run.sh -d 2024-01-01` |
| `machine` | Target server (agent must be installed there) | `prod-server-01` |
| `owner` | OS user to run the command as | `svc_etl` |
| `std_out_file` | Redirect stdout to file | `/logs/job.out` |
| `std_err_file` | Redirect stderr to file | `/logs/job.err` |
| `max_run_alarm` | Alert if job runs longer than N minutes | `120` |
| `min_run_alarm` | Alert if job runs shorter than N minutes | `5` |
| `alarm_if_fail` | Send alert on failure | `1` |
| `n_retrys` | Retry count on failure | `3` |

### 4.4 Variable Attributes

```jil
/* Define a global variable */
insert_glob_var: ENV_NAME
value: PRODUCTION

/* Use it in a job with %%VAR_NAME%% syntax */
insert_job: my_job   job_type: CMD
command: /scripts/run.sh %%ENV_NAME%%
machine: server01
```

`%%VAR_NAME%%` is the syntax to reference variables inside JIL commands.

---

## 5. Events and `sendevent` — The Hardest Part

AutoSys is **event-driven**. Jobs can be triggered by events pushed from outside,
not just by time. The `sendevent` command is how one job (or an external system)
wakes up another.

### 5.1 How sendevent Works

```bash
# An external system or another job fires this shell command:
sendevent -E STARTJOB -J downstream_job_name

# Or change a job's status manually:
sendevent -E CHANGE_STATUS -S SUCCESS -J some_job
```

| Event | Meaning |
|---|---|
| `STARTJOB` | Force-start a specific job immediately |
| `CHANGE_STATUS` | Manually override a job's status |
| `KILLJOB` | Kill a currently running job |
| `ON_HOLD` / `ON_ICE` | Suspend a job (temporarily / indefinitely) |
| `SET_GLOBAL` | Set a global variable's value at runtime |
| `SEND_SIGNAL` | Send an OS signal to a running process |

### 5.2 Why This Is the Biggest Migration Challenge

`sendevent` is a **push-based trigger**. An external system can instantly start a
downstream job with one shell command. **Airflow has no native equivalent** — it is
pull/poll based. This is the 🔴 RED gap in the migration strategy.

**Migration options:**
- Use an **event bus** (Kafka, RabbitMQ, AWS SQS) as a bridge
- Use **Airflow Datasets** (Airflow 2.4+) for DAG-to-DAG triggering
- Use **Airflow's REST API** to trigger DAGs externally

---

## 6. Calendars

AutoSys has a powerful built-in calendar engine — one of its strongest features.

```jil
/* Define a custom calendar */
insert_calendar: us_trading_days
description: "NYSE trading days only"
days: j1,j3,j5,...   /* specific Julian dates */

/* Use it in a job */
insert_job: trading_report   job_type: CMD
run_calendar: us_trading_days
command: /scripts/trading_report.sh
machine: report-server
```

Built-in calendar keywords include:
- `"last working day of month"`
- `"all days except holidays"`
- `"first monday of quarter"`

This is 🟡 YELLOW in migration — Airflow can replicate it but requires writing
custom Python `Timetable` classes (config becomes code).

---

## 7. Agents

An **AutoSys Agent** is a daemon process installed on every server where jobs run.
The Event Server tells the Agent what to execute; the Agent runs it locally and
reports the result back.

```
Event Server → (TCP) → Agent on Server X → executes command → reports status back
```

**Key migration implication:**
- AutoSys: `machine: server-X` pins a job to a specific physical server.
- Airflow/Astronomer: jobs run in **Kubernetes pods** (ephemeral containers) — no
  concept of "run specifically on server-X".
- **Transition strategy:** use Airflow's `SSHOperator` to SSH into legacy servers
  and run scripts there while the full migration is in progress.

---

## 8. Job States (Status Machine)

Every AutoSys job transitions through these states:

```
INACTIVE
   │
   ▼
ACTIVATED ──────► ON_HOLD   (manually paused — will run when released)
   │         └──► ON_ICE    (frozen — won't run even if triggered)
   ▼
STARTING
   │
   ▼
RUNNING
   │
   ├──► SUCCESS
   ├──► FAILURE
   └──► TERMINATED
```

| State | Meaning |
|---|---|
| `INACTIVE` | Not yet triggered |
| `ACTIVATED` | Conditions met, queued to start |
| `STARTING` | Agent received the command |
| `RUNNING` | Job is actively executing |
| `SUCCESS` | Completed with exit code 0 |
| `FAILURE` | Completed with non-zero exit code |
| `ON_HOLD` | Temporarily suspended; will run when released |
| `ON_ICE` | Completely frozen; ignores all triggers |
| `WAIT_REPLY` | Waiting for an external event (`sendevent`) |
| `TERMINATED` | Killed manually |

> **Migration use:** During Phase 5 (Decommission), the ops team puts migrated
> AutoSys jobs `ON_HOLD` first, then `ON_ICE`, before permanently deleting them.

---

## 9. CLI Tools — How to Extract Data (Phase 1)

These are the native AutoSys commands used in **Phase 1: Discover & Analyze**:

| Command | What it does |
|---|---|
| `autorep -J ALL -q` | Export ALL job definitions as raw JIL text |
| `autorep -J job_name` | Show a single job's current definition |
| `autorep -c calendar_name` | Show a calendar definition |
| `job_depends -j job_name -w` | Show full dependency tree for a job |
| `autobacklog` | Show job run history / backlog |
| `sendevent -E STARTJOB -J job_name` | Force-start a job (ops use) |
| `jil < input.jil` | Import/apply a JIL file into AutoSys |

**Most important command for migration:**
```bash
autorep -J ALL -q > all_jobs.jil
```
This dumps every single job definition into one text file — your raw material for
the entire migration analysis.

---

## 10. Real-World JIL Example (Full Workflow)

An end-of-day financial batch — the kind of thing you will migrate:

```jil
/* ============================================================
   BOX: End-of-Day Financial Batch
   Runs weekdays at 6 PM, excluding US holidays
   ============================================================ */
insert_job: eod_financial_box   job_type: BOX
owner: svc_finance
start_times: "18:00"
days_of_week: mo,tu,we,th,fr
exclude_calendar: us_holidays
alarm_if_fail: 1
max_run_alarm: 240

/* Step 1: Check input files arrived */
insert_job: eod_file_check   job_type: CMD
box_name: eod_financial_box
owner: svc_finance
command: /finance/scripts/check_input_files.sh
machine: fin-server-01
std_out_file: /logs/eod/file_check.out
std_err_file: /logs/eod/file_check.err
alarm_if_fail: 1

/* Step 2: Run calculations — needs step 1 */
insert_job: eod_calculations   job_type: CMD
box_name: eod_financial_box
owner: svc_finance
command: /finance/scripts/run_calc.sh %%TRADE_DATE%%
machine: fin-server-02
condition: success(eod_file_check)
max_run_alarm: 90
n_retrys: 2

/* Step 3A: Generate PDF report (parallel with 3B) */
insert_job: eod_pdf_report   job_type: CMD
box_name: eod_financial_box
owner: svc_finance
command: /finance/scripts/gen_pdf.sh
machine: fin-server-01
condition: success(eod_calculations)

/* Step 3B: Load to database (parallel with 3A) */
insert_job: eod_db_load   job_type: DB
box_name: eod_financial_box
owner: svc_finance
db_connection_string: %%DB_CONN_PROD%%
command: EXEC sp_load_eod_results
machine: db-server-01
condition: success(eod_calculations)

/* Step 4: Send notification — waits for BOTH 3A and 3B */
insert_job: eod_notify   job_type: CMD
box_name: eod_financial_box
owner: svc_finance
command: /finance/scripts/send_email.sh
machine: fin-server-01
condition: success(eod_pdf_report) & success(eod_db_load)
```

**Execution flow:**
```
eod_file_check
      │
      ▼
eod_calculations
      │
      ├──────────────────┐
      ▼                  ▼
eod_pdf_report    eod_db_load
      │                  │
      └────────┬─────────┘
               ▼
          eod_notify
```

---

## 11. Can You Run AutoSys Locally?

**No — not practically.**

- AutoSys is **proprietary Broadcom software** costing $50K–$200K+/year.
- Requires Oracle or MSSQL as its backend database.
- No free tier, no community edition, no Docker image.

**What you do instead:**

1. **Work with raw JIL text files** — someone on the client side runs
   `autorep -J ALL -q > all_jobs.jil` and hands you the file. You parse it locally.
2. **Build a Python JIL parser + visualizer** — reads JIL text, resolves dependencies,
   draws a graph. More useful for migration than a live AutoSys instance.
3. **Ask for read-only access to a non-prod AutoSys environment** — if the client has
   a dev/test AutoSys instance, read-only WCC access lets you browse jobs visually.

---

## 12. The 10 Things to Remember About AutoSys

| # | Key Concept |
|---|---|
| 1 | **JIL** = the config language. Text files. `insert_job` stanzas. |
| 2 | **BOX** = a workflow container → maps to an Airflow DAG. |
| 3 | **CMD / FTP / DB** = job types → map to Airflow Operators. |
| 4 | **`condition:`** = dependency logic (`success()`, `failure()`, `&`, `|`). |
| 5 | **`machine:`** = which server (Agent) runs the job. |
| 6 | **`sendevent`** = push-based event trigger → hardest thing to migrate (🔴 RED). |
| 7 | **Calendars** = powerful built-in scheduling (business days, holidays). |
| 8 | **`%%VAR%%`** = variable syntax → maps to Airflow Variables. |
| 9 | **`autorep -J ALL -q`** = the command to export everything. |
| 10 | **WCC** = the GUI for operations teams (monitor, force-run, hold jobs). |
