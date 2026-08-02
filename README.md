# AutoSys Clone — Complete Architecture & Build Plan

A full-fidelity clone of **CA Workload Automation AE (AutoSys)** built in Python.
Every component maps 1-to-1 to the real AutoSys architecture. Built phase-by-phase
so each layer is fully tested before the next is added on top of it.

> **Current status:** Phases 1–12 complete + migration complexity analysis · 1072 tests passing · ~18,000 lines of production code

---

## Table of Contents

1. [What is AutoSys?](#what-is-autosys)
2. [Architecture Overview](#architecture-overview)
3. [Project Layout](#project-layout)
4. [Quick Start](#quick-start)
5. [How It Works — Features & Components](#how-it-works--features--components)
6. [Migration Complexity Analysis](#migration-complexity-analysis)
7. [Phase 1 — Data Models & Database Schema](#phase-1--data-models--database-schema)
8. [Phase 2 — JIL Parser & Condition Language](#phase-2--jil-parser--condition-language)
9. [Phase 3 — State Machine & Variable Substitution](#phase-3--state-machine--variable-substitution)
10. [Phase 4 — Event Processor (EPS)](#phase-4--event-processor-eps)
11. [Phase 5 — System Agent (Local Execution)](#phase-5--system-agent-local-execution)
12. [Phase 6 — Remote Dispatch via TCP](#phase-6--remote-dispatch-via-tcp)
13. [Phase 7 — Box Orchestration & insert_machine](#phase-7--box-orchestration--insert_machine)
14. [Phase 8 — REST API Application Server (SSA)](#phase-8--rest-api-application-server-ssa)
15. [Phase 9 — WCC Web Dashboard](#phase-9--wcc-web-dashboard)
16. [Phase 10 — Alarms, Notifications & NSM](#phase-10--alarms-notifications--nsm)
17. [Phase 11 — Calendar Engine & Holiday Exclusions](#phase-11--calendar-engine--holiday-exclusions)
18. [Phase 12 — End-to-End Integration & Production Hardening](#phase-12--end-to-end-integration--production-hardening)
19. [How All Components Wire Together](#how-all-components-wire-together)
20. [JIL Reference](#jil-reference)
21. [CLI Reference](#cli-reference)
22. [Configuration Reference](#configuration-reference)
23. [Testing Strategy](#testing-strategy)

---

## What is AutoSys?

AutoSys (CA Workload Automation AE) is a **batch workload scheduler** used in
enterprise environments to schedule, monitor, and manage thousands of jobs running
across many machines. It is the backbone of ETL pipelines, financial batch
processing, and nightly data-warehouse loads at major banks and Fortune 500
companies.

### Core concepts

| Concept | Description |
|---------|-------------|
| **Job** | A unit of work — either a shell command (`CMD`), a workflow container (`BOX`), or a file-watcher (`FILEWATCHER`) |
| **BOX** | A container job. When a BOX starts, its children become eligible to run. The BOX completes when all children complete |
| **JIL** | *Job Information Language* — AutoSys's DSL for defining jobs, machines, calendars, and global variables |
| **Event** | A message that drives the state machine: `STARTJOB`, `KILLJOB`, `FORCE_STARTJOB`, `JOB_ON_HOLD`, `JOB_OFF_HOLD`, etc. |
| **EPS** | *Event Processor* — the scheduler's main loop. Reads events from a queue, evaluates conditions, and dispatches jobs |
| **ACE** | *Autosys Correlated Events* — the scheduler daemon that runs the EPS in a loop |
| **System Agent** | A daemon running on each target machine that actually forks child processes and reports exit codes back |
| **SSA** | *Scheduler Server Agent* — the REST API server that exposes the scheduler's state to CLIs and web UIs |
| **WCC** | *Workload Control Centre* — the web UI showing the job grid and dependency flow graph |
| **Machine** | A registered target machine with a host:port where a System Agent is running |
| **Calendar** | A named set of dates used in `run_calendar` / `exclude_calendar` attributes |
| **Condition** | A boolean expression on job statuses: `success(job_a) & (success(job_b) | failure(job_c))` |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        AutoSys Architecture                          │
│                                                                      │
│  ┌─────────────┐   JIL import    ┌──────────────────────────────┐   │
│  │  autosys    │────────────────▶│       SQLite / Postgres DB    │   │
│  │    CLI      │   sendevent     │  ┌────────┐  ┌────────────┐  │   │
│  │  (Click)    │────────────────▶│  │  jobs  │  │event_queue │  │   │
│  └──────┬──────┘                 │  ├────────┤  ├────────────┤  │   │
│         │ REST                   │  │job_runs│  │  machines  │  │   │
│         ▼                        │  ├────────┤  ├────────────┤  │   │
│  ┌──────────────┐                │  │ output │  │ calendars  │  │   │
│  │  App Server  │◀──────────────▶│  └────────┘  └────────────┘  │   │
│  │    (SSA)     │   sync reads   └──────────────────────────────┘   │
│  │  FastAPI     │                          ▲                         │
│  │  port 9000   │                          │ read/write              │
│  └──────┬───────┘                          │                         │
│         │                         ┌────────┴────────┐               │
│         │ WebSocket / HTTP        │  Scheduler ACE   │               │
│         ▼                        │  (Event Processor)│               │
│  ┌──────────────┐                │  asyncio loop     │               │
│  │  WCC         │                │  port 7507        │               │
│  │  Dashboard   │                └────────┬──────────┘               │
│  │  (Jinja2 /  │                         │ TCP dispatch              │
│  │   React)    │                ┌─────────┴──────────┐               │
│  │  port 8080  │                │   System Agent     │               │
│  └─────────────┘                │   LocalJobRunner   │               │
│                                 │   RemoteDispatch   │               │
│  ┌──────────────┐               │   port 7520        │               │
│  │  NSM / Alarms│◀──────────────│                    │               │
│  │  Notifications│  alarm events │   ┌────────────┐  │               │
│  │  port 1721   │               │   │ subprocess │  │               │
│  └──────────────┘               │   │ (actual job│  │               │
│                                 │   │  command)  │  │               │
│                                 │   └────────────┘  │               │
│                                 └────────────────────┘               │
└──────────────────────────────────────────────────────────────────────┘
```

### Component ports (matching real AutoSys defaults)

| Component | Port | Protocol | Description |
|-----------|------|----------|-------------|
| Scheduler ACE | 7507 | Internal | Main event loop daemon |
| System Agent | 7520 | TCP (newline-delimited JSON) | Job execution daemon on each machine |
| App Server (SSA) | 9000 | HTTP/REST + WebSocket | API server for CLI and WCC |
| WCC Dashboard | 8080 | HTTP | Web UI |
| NSM Notifications | 1721 | HTTP webhook | Alarm/notification dispatcher |

---

## Project Layout

```
autosys-clone/
│
├── autosys/                         # Main package
│   ├── models/                      # Pydantic data models
│   │   ├── job.py                   # Job hierarchy: CmdJob, BoxJob, FilewatcherJob
│   │   ├── event.py                 # Event model (STARTJOB, KILLJOB, …)
│   │   ├── enums.py                 # JobStatus, JobType, EventType enums
│   │   ├── job_run.py               # JobRun (run history record)
│   │   ├── machine.py               # MachineDef (JIL machine definition)
│   │   ├── alarm.py                 # Alarm model
│   │   ├── calendar.py              # Calendar model
│   │   ├── global_var.py            # GlobalVariable model
│   │   └── resource.py              # VirtualResource model
│   │
│   ├── db/                          # Database layer
│   │   ├── schema.py                # SQLAlchemy ORM table definitions
│   │   ├── repository.py            # CRUD repositories for each table
│   │   ├── connection.py            # Sync + async engine management
│   │   ├── migrations.py            # Schema creation / DDL
│   │   └── retry.py                 # Retry logic for transient DB failures
│   │
│   ├── parser/                      # JIL language
│   │   ├── lexer.py                 # Tokeniser: DIRECTIVE, JOB_NAME, ATTR_NAME, VALUE
│   │   ├── jil_parser.py            # Recursive-descent parser → JILOperation list
│   │   ├── jil_writer.py            # Serialise Job/MachineDef back to JIL text
│   │   ├── condition_parser.py      # Condition expression AST + evaluator
│   │   └── variable_sub.py          # %%DATE%%, %%AUTOUSER%%, global var expansion
│   │
│   ├── scheduler/                   # Core scheduling engine
│   │   ├── state_machine.py         # Job status transition rules + guards
│   │   ├── condition_evaluator.py   # is_satisfied() + build_status_snapshot()
│   │   ├── time_trigger.py          # start_times + days_of_week + calendar logic
│   │   ├── event_processor.py       # EPS: dequeue → handle → dispatch loop
│   │   ├── box_manager.py           # BOX child activation + completion logic
│   │   ├── simulation_runner.py     # Multi-cycle dry-run simulation
│   │   ├── failure_injector.py      # Deterministic failure injection for simulation
│   │   └── ha.py                    # High availability — distributed lock
│   │
│   ├── agent/                       # System Agent
│   │   ├── runner.py                # LocalJobRunner: subprocess + stdout capture
│   │   ├── dispatch.py              # Dispatch router: local vs. remote
│   │   ├── server.py                # TCP agent server (asyncio)
│   │   ├── remote.py                # RemoteDispatch: TCP client to remote agent
│   │   ├── protocol.py              # Newline-delimited JSON message types
│   │   └── runners.py               # Job runners for CMD, FTP, FileWatcher types
│   │
│   ├── analysis/                    # Migration complexity analysis
│   │   ├── complexity.py            # T-shirt sizing, effort, risk scoring + report
│   │   ├── migration_signals.py     # 10 structural signals (A1-A10) from JIL
│   │   ├── operational_risk.py      # Runtime risk scoring from run history
│   │   ├── dependency_graph.py      # Fan-in/fan-out, dependency waves
│   │   ├── gap_analysis.py          # Airflow gap tags (calendar, logs, etc.)
│   │   └── box_trace.py             # BOX execution trace
│   │
│   ├── engine/                      # Engine & integration modules
│   │   ├── agent_monitor.py         # Agent health monitoring
│   │   ├── cloud_integration.py     # AWS/GCP/Azure job submission
│   │   ├── glob_substitution.py     # %%GLOB:name%% expansion
│   │   ├── job_type_expander.py     # Job type template expansion
│   │   ├── monitor_evaluator.py     # FILE_MONITOR condition evaluation
│   │   └── xinst_client.py          # Cross-instance remote job status
│   │
│   ├── cli/                         # Click CLI
│   │   ├── main.py                  # Root `autosys` group
│   │   ├── jil_cmd.py               # `autosys jil import/export/validate/show`
│   │   ├── autorep_cmd.py           # `autosys autorep -j/-J/-q`
│   │   ├── sendevent_cmd.py         # `autosys sendevent -E -J`
│   │   ├── scheduler_cmd.py         # `autosys scheduler serve/start/stop`
│   │   ├── agent_cmd.py             # `autosys agent serve/jobs/tail`
│   │   ├── machine_cmd.py           # `autosys machine register/list/check`
│   │   ├── box_cmd.py               # `autosys box status/tree`
│   │   ├── analyze_cmd.py           # `autosys analyze` + `autosys migration-report`
│   │   └── autocal_cmd.py           # `autosys calendar create/list/import`
│   │
│   ├── app_server/                  # FastAPI REST API (SSA)
│   │   ├── main.py                  # FastAPI app factory + lifespan
│   │   ├── auth.py                  # JWT middleware
│   │   ├── broadcaster.py           # WebSocket status broadcast
│   │   ├── schemas.py               # Pydantic request/response schemas
│   │   └── routers/
│   │       ├── jobs.py              # GET/POST /jobs, /jobs/{name}/sendevent
│   │       ├── events.py            # GET /events, POST /events
│   │       ├── runs.py              # GET /runs, /runs/{id}/output
│   │       ├── machines.py          # GET/POST /machines
│   │       ├── alarms.py            # GET/POST /alarms
│   │       ├── assessment.py        # GET /assessment/migration-report
│   │       ├── globals.py           # Global variables CRUD
│   │       └── metrics.py           # Prometheus metrics
│   │
│   ├── wcc/                         # WCC API server
│   │   └── app.py                   # JSON API + SSE for React frontend
│   │
│   └── notifications/               # Alarm & notification system
│       ├── alarm_manager.py         # Alarm evaluation + deduplication
│       ├── dispatcher.py            # Multi-channel dispatch
│       ├── config.py                # Notification channel configuration
│       ├── remedy_notifier.py       # BMC Remedy ITSM integration
│       └── snmp_notifier.py         # SNMP trap notifications
│
├── wcc-frontend/                    # React + TypeScript WCC dashboard
│   ├── src/
│   │   ├── App.tsx                  # Root component with routing
│   │   ├── api/                     # API client functions
│   │   ├── components/              # React components (JobGrid, JobDetail, etc.)
│   │   └── types.ts                 # TypeScript type definitions
│   ├── package.json
│   └── vite.config.ts
│
├── jil_files/                       # 54 sample JIL files (297 jobs, financial domain)
├── examples/                        # Minimal runnable JIL examples
├── config/calendars/                # Calendar definitions (us_holidays.cal)
├── docs/                            # Architecture and migration guides
├── tests/                           # pytest suite (1072 tests)
└── pyproject.toml
```

---

## Quick Start

### Prerequisites

- Python 3.10 or later
- Node.js 18+ (for WCC frontend only)
- No external services required — pure SQLite, no Docker needed

### Install

```bash
# Clone and enter the project
cd autosys-clone

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install the package in editable mode
pip install -e .

# (Optional) Install frontend dependencies
cd wcc-frontend && npm install && cd ..
```

### Run the tests

```bash
pytest tests/                       # all 1072 tests
pytest tests/test_phase1.py -v      # one phase
pytest tests/ -k "box"              # filter by name
```

### Start the scheduler + API server

```bash
# Terminal 1 — start the scheduler + REST API server
autosys scheduler serve --port 8000 --host 127.0.0.1
#   → API at http://localhost:8000
#   → Docs at http://localhost:8000/docs
#   → WebSocket at ws://localhost:8000/api/v1/ws/events
```

### Start the WCC frontend (React dashboard)

```bash
# Terminal 2 — start the React dev server
cd wcc-frontend
npx vite --port 5173
#   → Open http://localhost:5173 in your browser
```

### Import JIL jobs and run the migration report

```bash
# Import all sample JIL files (297 jobs across 50 boxes)
autosys jil import jil_files/01_market_data_ingest.jil
autosys jil import jil_files/02_reference_data_sync.jil
# ... or import all at once:
for f in jil_files/*.jil; do autosys jil import "$f"; done

# See what was imported
autosys autorep -J all              # list all jobs
autosys box tree mkt_data_ingest_box  # view box hierarchy

# Generate the migration complexity report
autosys migration-report --cycles 20 --export migration.csv
#   → Runs a 20-cycle dry-run simulation
#   → Extracts 10 structural signals from JIL attributes
#   → Scores each job for T-shirt size, effort, and risk
#   → Exports CSV with Astronomer mapping recommendations
```

### Trigger jobs manually

```bash
# Start a specific job
autosys sendevent -E STARTJOB -J check_source_ready

# Start the scheduler daemon (processes events from the queue)
autosys scheduler start

# Watch the box progress
autosys box status demo_etl_box
```

### Start a System Agent (for real subprocess execution)

```bash
# Terminal 1 — start an agent on the default port
autosys agent serve --port 7520 --name local-agent

# Terminal 2 — register the agent with the scheduler
autosys machine register local-agent localhost 7520

# Terminal 3 — trigger a job
autosys sendevent -E STARTJOB -J check_source_ready
autosys agent jobs              # see running jobs
autosys agent tail check_source_ready   # tail output
```

### PostgreSQL Setup (Optional)

The clone defaults to SQLite for zero-config development. For production or
multi-process deployments, use PostgreSQL:

```bash
pip install -e ".[dev,pg]"
createdb autosys
export AUTOSYS_DB_URL="postgresql://user:pass@localhost:5432/autosys"
pytest tests/ -m postgresql
```

---

## How It Works — Features & Components

The AutoSys clone is a complete batch workload scheduling system. Here's how all the pieces fit together and what each feature does:

### JIL Import & Parsing

The **JIL (Job Information Language)** parser reads AutoSys job definition files and imports them into the database. It handles all 50+ AutoSys attributes including `command`, `machine`, `condition`, `start_times`, `days_of_week`, `n_retrys`, `alarm_if_fail`, `box_name`, `timezone`, `notification_emailaddress`, and more.

```bash
autosys jil import jil_files/01_market_data_ingest.jil    # import one file
autosys jil validate jil_files/03_trade_booking.jil       # validate without importing
autosys jil export --all                                  # export all jobs as JIL text
autosys jil show extract_sales                            # show one job definition
```

### Event-Driven Scheduler (EPS)

The **Event Processor System (EPS)** is the heart of the scheduler. It runs a continuous loop that:

1. **Dequeues** events from the `event_queue` table (FIFO order)
2. **Evaluates** job conditions (e.g. `success(check_source_ready) & s(extract_sales)`)
3. **Dispatches** eligible jobs to local or remote agents
4. **Activates** children of running BOX jobs
5. **Triggers** time-based jobs whose `start_times` + `days_of_week` match

Event types: `STARTJOB`, `FORCE_STARTJOB`, `KILLJOB`, `JOB_ON_HOLD`, `JOB_OFF_HOLD`, `JOB_ON_ICE`, `SET_GLOBAL`, `CHANGE_STATUS`, `CHECK_HEARTBEAT`.

### Job Execution — Local & Remote

- **LocalJobRunner** — forks the actual subprocess, captures stdout line-by-line, enforces `max_run_alarm` timeout, handles `KILLJOB` mid-run
- **RemoteDispatch** — sends jobs to System Agent daemons on remote machines via TCP (newline-delimited JSON protocol)
- **System Agent** — asyncio TCP server that receives `DISPATCH` messages, runs commands, and reports exit codes back

### BOX Orchestration

BOX jobs are workflow containers. When a BOX starts:
- Its children become eligible to run (their conditions are evaluated)
- The BOX completes when all children reach a terminal state (SUCCESS/FAILURE/TERMINATED)
- `box_terminator` jobs can force-terminate the BOX early
- Nested boxes are supported (BOX within BOX)

### REST API (SSA)

FastAPI application server exposing:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/jobs` | GET | List all jobs (with filtering) |
| `/api/v1/jobs/{name}` | GET | Job detail with runs + children |
| `/api/v1/jobs/{name}/sendevent` | POST | Send an event to a job |
| `/api/v1/events` | GET/POST | List / enqueue events |
| `/api/v1/runs` | GET | Run history (optional `?job=` filter) |
| `/api/v1/alarms` | GET/POST | List / raise alarms |
| `/api/v1/alarms/{id}/resolve` | POST | Resolve an alarm |
| `/api/v1/machines` | GET/POST | List / register machines |
| `/api/v1/globals` | GET/POST | Global variable CRUD |
| `/api/v1/assessment/migration-report` | GET | Full migration complexity report |
| `/api/v1/ws/events` | WS | WebSocket live event stream |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

### WCC Dashboard (React Frontend)

The Workload Control Centre is a React + TypeScript single-page app (`wcc-frontend/`) that provides:

- **Job Grid** — sortable, filterable table of all jobs with live status updates
- **Job Detail** — definition, run history, children (for BOX jobs)
- **Box Graph** — D3.js force-directed dependency graph
- **Alarm Console** — active/cleared alarm list
- **Live Updates** — SSE (Server-Sent Events) polling every 2 seconds

The WCC backend (`autosys/wcc/app.py`) serves a JSON API + SSE stream consumed by the React frontend.

### Alarms & Notifications

- **AlarmManager** — evaluates `alarm_if_fail` and `alarm_if_terminated` flags during simulation/runtime, deduplicates alarms
- **Notification Dispatcher** — multi-channel delivery: email, Slack, PagerDuty, SNMP traps, BMC Remedy tickets
- **Alarm lifecycle** — raised → active → resolved (with `cleared_at` timestamp)

### Calendar Engine

Supports `run_calendar` and `exclude_calendar` attributes:
- Calendars are named date sets stored in the DB
- Holiday calendars (e.g. `us_holidays.cal`) can be imported
- Time triggers check calendar membership before firing

### High Availability

- **DistributedLock** — DB-row-based lock for scheduler leader election
- Primary/standby failover support
- Only one EPS instance processes events at a time

### Cloud & Cross-Instance Integration

- **CloudIntegration** — submit jobs to AWS/GCP/Azure for execution
- **RemoteInstanceClient** — query job status on remote AutoSys instances (xinst)
- **MonitorEvaluator** — evaluates FILE_MONITOR conditions each tick
- **GlobSubstitution** — expands `%%GLOB:name%%` placeholders in commands

---

## Migration Complexity Analysis

The clone includes a full **AutoSys → Astronomer (Apache Airflow) migration assessment pipeline** that analyzes JIL job definitions and produces a comprehensive complexity report — no runtime access required.

### How It Works

```
JIL Files → Import → DB → Simulation (20 cycles) → Run Stats
                              ↓
                    Structural Analysis (A1-A10)
                              ↓
                    Complexity Scoring (T-shirt + Risk)
                              ↓
                    Report (CLI / REST / CSV)
```

### 1. Dry-Run Simulation (`simulation_runner.py`)

Runs a multi-cycle simulation that:
- Fires `FORCE_STARTJOB` on all top-level BOX and CMD jobs
- Processes events through the EPS with `FailureInjector` (deterministic seeded failures)
- Resets jobs between cycles to simulate fresh daily runs
- Accumulates `JobRunRow` history (start/end times, exit codes, retries)
- Generates `AlarmRow` records for jobs with `alarm_if_fail: 1` that fail

**FailureInjector** uses JIL attributes to determine failure rates:
- `n_retrys >= 3` → 25% failure rate
- `n_retrys 1-2` → 10% failure rate
- `n_retrys 0` → 3% failure rate

### 2. Structural Analysis (A1-A10) (`migration_signals.py`)

Extracts 10 migration signals from JIL attributes alone — no runtime needed:

| Signal | What It Detects | Migration Impact |
|--------|----------------|------------------|
| **A1** Machine concentration | Jobs per machine | Agent migration planning |
| **A2** Command analysis | Script dialects (bash/ksh/python/perl), hardcoded paths | Operator mapping, log remapping |
| **A3** Profile analysis | Shared profiles | Connection profile migration |
| **A4** Box nesting depth | Recursive box_name depth | TaskGroup nesting complexity |
| **A5** Cross-box dependencies | Conditions referencing jobs in different boxes | ExternalTaskSensor requirement |
| **A6** Schedule burst | Jobs starting at the same `start_times` | Worker pool sizing |
| **A7** Notification mapping | Email addresses, alarm_if_fail | Airflow callback migration |
| **A8** Log path analysis | Hardcoded std_out_file/std_err_file paths | S3/GCS log routing |
| **A9** Owner/permission | Owner groups, permission tokens | RBAC role mapping |
| **A10** Timezone analysis | Jobs with timezone attribute | UTC schedule conversion |

### 3. Complexity Scoring (`complexity.py`)

Each job gets assessed on two orthogonal axes:

**T-shirt Size (effort estimate):**

| Size | Effort | Criteria |
|------|--------|----------|
| XS | 2h | Simple CMD, no deps |
| S | 4h | Box with linear deps or one condition |
| M | 8h | Calendar, time triggers, date_conditions, file-watchers |
| L | 24h | look_back, virtual resources, complex conditions |
| XL | 60h | FTP, cross-instance deps, deep BOX trees |

**Operational Risk (from simulated runtime):**

| Risk | Criteria |
|------|----------|
| NO_DATA | No run history (not simulated) |
| NONE | 0 failures, no alarms |
| LOW | < 10% failure rate, few alarms |
| MEDIUM | 10-25% failure rate or active alarms |
| HIGH | > 25% failure rate or inherited from child |

BOX jobs inherit the **worst risk** from their children (risk aggregation).

### 4. Report Features

**CLI** (`autosys migration-report`):
- Per-job table with size, effort, risk, drivers, and all migration signals
- Summary with totals, risk distribution, dialect counts
- Per-box effort breakdown (jobs, hours, size distribution, high-risk jobs per box)
- Top 5 Astronomer mapping recommendations
- CSV export with 20 columns including `astronomer_mapping` and `risk_mitigation`

**REST API** (`GET /api/v1/assessment/migration-report`):

```bash
# Default: 20 simulation cycles
curl http://localhost:8000/api/v1/assessment/migration-report | python3 -m json.tool

# Custom: 50 cycles
curl "http://localhost:8000/api/v1/assessment/migration-report?cycles=50"
```

Query parameters:
- `cycles` (int, default 20) — Number of simulation cycles for runtime data generation

Response structure:
```json
{
  "generated_at": "2026-08-02T14:50:00",
  "job_count": 297,
  "simulation": {
    "cycles": 20,
    "total_runs": 3934,
    "total_failures": 367,
    "total_alarms": 488,
    "failure_rate": 0.0933
  },
  "jobs": [
    {
      "job_name": "extract_sales",
      "job_type": "CMD",
      "box_name": "demo_etl_box",
      "size": "M",
      "effort_h": 8,
      "risk": "LOW",
      "risk_drivers": "1 cleared alarm(s), 1 past retried run(s)",
      "blast_radius": 2,
      "gap_tags": "sla-management",
      "command_dialect": "python",
      "machine_concentration": "XS",
      "box_nesting_depth": 1,
      "has_cross_box_dep": false,
      "schedule_burst_count": 0,
      "has_notifications": true,
      "has_hardcoded_logs": true,
      "timezone": "",
      "astronomer_mapping": "PythonOperator / @task decorator + on_failure_callback / SlackNotifier + remap log paths to S3/GCS",
      "risk_mitigation": "replace hardcoded paths with Airflow templates / XCom"
    }
  ],
  "box_breakdown": [
    {
      "box_name": "demo_etl_box",
      "job_count": 4,
      "total_effort_h": 32,
      "sizes": {"XS": 0, "S": 0, "M": 4, "L": 0, "XL": 0},
      "risks": {"NO_DATA": 0, "NONE": 2, "LOW": 2, "MEDIUM": 0, "HIGH": 0},
      "high_risk_jobs": []
    }
  ],
  "summary": {
    "counts": {"XS": 0, "S": 0, "M": 227, "L": 48, "XL": 22},
    "hours": {"XS": 0, "S": 0, "M": 1816, "L": 1152, "XL": 1320},
    "total_jobs": 297,
    "raw_hours": 4288,
    "platform_h": 857,
    "testing_h": 1286,
    "pm_h": 643,
    "training_h": 428,
    "total_h": 7502,
    "total_days": 938,
    "risk_counts": {"NO_DATA": 8, "NONE": 58, "LOW": 63, "MEDIUM": 160, "HIGH": 8},
    "migration_signals": {
      "cross_box_dep_count": 45,
      "max_box_nesting": 1,
      "max_schedule_burst": 5,
      "notification_job_count": 297,
      "hardcoded_log_job_count": 243,
      "timezone_count": 62,
      "dialect_counts": {"python": 164, "bash": 78}
    }
  },
  "csv": "job_name,job_type,box_name,size,effort_h,risk,...\nextract_sales,CMD,demo_etl_box,M,8,LOW,...\n..."
}
```

The response includes:
- **`simulation`** — run/failure/alarm counts and failure rate
- **`jobs`** — per-job assessment with all migration signals, Astronomer mapping, and risk mitigation
- **`box_breakdown`** — per-box effort totals, size/risk distribution, and high-risk job list
- **`summary`** — aggregate counts, effort breakdown (platform/testing/PM/training), risk distribution, and migration signal totals
- **`csv`** — full CSV export as a string (20 columns including `astronomer_mapping` and `risk_mitigation`)

**Astronomer Mapping Recommendations** — per job:
- BOX jobs → `TaskGroup (nested DAG)` or `DAG with ExternalTaskSensor`
- Python CMD → `PythonOperator / @task decorator`
- Bash CMD → `BashOperator`
- Cross-box deps → `ExternalTaskSensor with poke_interval tuning`
- Notifications → `on_failure_callback / SlackNotifier`
- Hardcoded logs → `remap log paths to S3/GCS`
- Timezone → `timezone-aware schedule (US/Eastern → UTC)`
- Schedule burst > 10 → `stagger start times to avoid worker saturation`

**Risk Mitigation Suggestions** — per job:
- HIGH risk → `PRIORITY: migrate early with extra testing`
- High failure rate → `add Airflow retries + retry_delay_exponential`
- Active alarms → `set up Airflow alerts + PagerDuty integration`
- Hardcoded paths → `replace with Airflow templates / XCom`
- Timezone → `convert US/Eastern schedule to UTC`

### Sample Report Output

With 297 imported jobs (50 BOX, 247 CMD) across 54 JIL files:

```
Simulation: 3934 runs, 367 failures, 488 alarms, 9.3% fail rate
Total Jobs: 297    Total Effort: 7502h (938 days)
T-shirt Sizes:  XS=0  S=0  M=227  L=48  XL=22
Risk Levels:    NO_DATA=8  NONE=58  LOW=63  MEDIUM=160  HIGH=8
Cross-box dependencies: 45
Command dialects: python=164, bash=78
```

---

## Phase 1 — Data Models & Database Schema

**Goal:** Define every data structure AutoSys tracks. Nothing runs yet — this
is the foundation everything else is built on.

### What is built

#### Pydantic models (`autosys/models/`)

All models use **Pydantic v2** for validation and serialisation.

| Model | File | Represents |
|-------|------|------------|
| `CmdJob` | `job.py` | A shell-command job (`job_type: CMD`) |
| `BoxJob` | `job.py` | A workflow container (`job_type: BOX`) |
| `FilewatcherJob` | `job.py` | Polls for a file (`job_type: FILEWATCHER`) |
| `Event` | `event.py` | A state-change event (STARTJOB, KILLJOB, …) |
| `JobRun` | `job_run.py` | One execution record (run_id, exit_code, start/end time) |
| `MachineDef` | `machine.py` | A machine definition from JIL (`insert_machine:`) |
| `Alarm` | `alarm.py` | An alarm raised when `alarm_if_fail=1` and the job fails |
| `Calendar` | `calendar.py` | A named date set used in `run_calendar` |
| `GlobalVariable` | `global_var.py` | An `%%AUTO_VAR%%`-style variable |
| `VirtualResource` | `resource.py` | A named semaphore (max_load enforcement) |

##### Job inheritance hierarchy

```
Job (abstract base)
├── CmdJob          — command:, machine:, std_out_file:, n_retrys:
├── BoxJob          — start_times:, days_of_week:, exclude_calendar:
└── FilewatcherJob  — watch_file:, watch_interval:, watch_file_min_size:
```

Every `Job` has: `job_name`, `job_type`, `owner`, `box_name`, `condition`,
`alarm_if_fail`, `alarm_if_terminated`, `max_run_alarm`, `min_run_alarm`,
`term_run_time`, `priority`, `description`.

#### SQLAlchemy ORM tables (`autosys/db/schema.py`)

| Table | Primary key | Description |
|-------|-------------|-------------|
| `jobs` | `job_name` | One row per job definition |
| `job_runs` | `run_id` (UUID) | One row per execution attempt |
| `job_output` | `(run_id, seq)` | Stdout lines captured during execution |
| `event_queue` | `event_id` (UUID) | Pending events (FIFO) |
| `event_history` | `event_id` | Processed events (audit log) |
| `machines` | `machine_name` | Registered System Agents |
| `global_vars` | `name` | `%%VARIABLE%%` definitions |
| `calendars` | `name` | Named date sets |
| `calendar_dates` | `(name, date)` | Individual dates in a calendar |
| `alarms` | `alarm_id` | Active and resolved alarms |

#### Repositories (`autosys/db/repository.py`)

Each table has a typed repository class:

```python
jobs    = JobRepository()       # upsert, get_row, list_all, get_children, …
events  = EventRepository()     # enqueue, dequeue_pending, mark_processed, …
runs    = RunRepository()       # start, finish, get_history, …
output  = OutputRepository()    # append_line, get_lines, …
machines = MachineRepository()  # register, get, update_heartbeat, …
globs   = GlobalVarRepository() # set, get, as_dict, …
```

#### Database connections (`autosys/db/connection.py`)

Two session types to handle both the async scheduler daemon and the sync CLI:

```python
# Sync — used by CLI commands and all tests
with sync_session() as session:
    row = job_repo.get_row(session, "my_job")

# Async — used by the Scheduler ACE daemon (Phase 8)
async with async_session() as session:
    rows = await session.execute(select(JobRow))
```

The DB URL is read from `AUTOSYS_DB_URL` env var at session-open time (not at
import time), so tests can safely `monkeypatch.setenv()` between test cases.

### Key design decisions

- `autoflush=False` on all sessions — explicit flush control avoids surprise SQL
- `expire_on_commit=False` — objects remain readable after commit (no lazy-load errors)
- `session.flush()` after `session.add(row)` in repository `register` methods — ensures pending rows are in the persistent identity map so subsequent `session.get()` calls find them in the same session
- WAL mode + foreign keys enabled on every new SQLite connection via `event.listens_for`

### Tests (47 tests)

`tests/test_phase1.py` covers:
- Every table can be created and queried
- All model validators fire (e.g. CMD job requires `machine`)
- Repository upsert is idempotent
- DB isolation: each test gets its own `tmp_path` SQLite file

---

## Phase 2 — JIL Parser & Condition Language

**Goal:** Parse JIL files into structured Pydantic objects and evaluate
dependency conditions.

### What is built

#### JIL lexer (`autosys/parser/lexer.py`)

Converts raw JIL text to a flat token stream.

```
insert_job: extract_sales   job_type: CMD
    command: /scripts/run.sh
    machine: etl-server-01
```

Becomes:
```
DIRECTIVE "insert_job"
JOB_NAME  "extract_sales"
ATTR_NAME "job_type"   VALUE "CMD"
ATTR_NAME "command"    VALUE "/scripts/run.sh"
ATTR_NAME "machine"    VALUE "etl-server-01"
```

Token kinds: `DIRECTIVE`, `JOB_NAME`, `ATTR_NAME`, `VALUE`, `EOF`.

JIL comments (`/* … */` and `#`) are stripped before tokenising.
The lexer handles multi-word values, quoted strings, and inline attributes on
the stanza header line (e.g. `job_type: CMD` on the same line as the job name).

Directives recognised: `insert_job`, `update_job`, `delete_job`, `override_job`,
`insert_machine`.

#### JIL parser (`autosys/parser/jil_parser.py`)

Consumes the token stream and produces `JILOperation` objects:

```python
@dataclass
class JILOperation:
    op:          str          # "insert" | "update" | "delete" | "override" | "insert_machine"
    job:         Job | None   # set for job stanzas
    machine:     MachineDef | None  # set for insert_machine stanzas
    raw_attrs:   dict[str, str]
    source_line: int
```

Attribute coercion happens before Pydantic validation:
- Boolean flags (`alarm_if_fail`, `box_terminator`, …) → `bool`
- Integer attributes (`n_retrys`, `max_run_alarm`, `port`, …) → `int`
- Lists (`start_times`, `days_of_week`) → `list[str]` (Pydantic validator splits on `,`)
- Everything else → `str`

#### Condition language (`autosys/parser/condition_parser.py`)

A full recursive-descent parser and evaluator for AutoSys condition expressions.

**Grammar:**
```
expr       := or_expr
or_expr    := and_expr ('|' and_expr)*
and_expr   := not_expr ('&' not_expr)*
not_expr   := '!' atom | atom
atom       := func_call | '(' expr ')'
func_call  := func_name '(' job_name ')'
           | 'value' '(' global_name ')' '=' literal
func_name  := 'success' | 's' | 'failure' | 'f' | 'done' | 'd'
            | 'notrunning' | 'n' | 'terminated' | 't' | 'activated'
```

Examples:
```
success(extract_sales)
s(check_source_ready) & s(extract_sales)
s(generate_report) & s(load_to_warehouse)
success(a) | (failure(b) & !terminated(c))
value(BATCH_DATE) = "2024-01-01"
```

Single-letter shorthands (`s`, `f`, `d`, `n`, `t`) map to the full function name —
these are the most common form seen in real AutoSys JIL files.

#### JIL writer (`autosys/parser/jil_writer.py`)

Serialises `Job` or `MachineRow` objects back to JIL text. Used by
`autosys jil export`. The round-trip `parse → export → re-parse` is stable
(tested in Phase 2 suite).

```python
text = job_to_jil(job, op="insert")
text = machine_to_jil(machine_row)
text = jobs_to_jil([job1, job2], header_comment="Exported 2024-01-01")
```

#### Variable substitution (`autosys/parser/variable_sub.py`)

Expands `%%VARIABLE%%` placeholders in `command:` strings before dispatch:

| Placeholder | Expands to |
|-------------|------------|
| `%%DATE%%` | Today's date as `YYYY-MM-DD` (or configured format) |
| `%%TIME%%` | Current time as `HH:MM:SS` |
| `%%AUTOUSER%%` | The job's `owner` field |
| `%%JOB_NAME%%` | The job's name |
| `%%GLOBAL_VAR%%` | Value from the `global_vars` table |

Unknown placeholders are left as-is (matching real AutoSys behaviour).

### Tests (87 tests)

`tests/test_phase2.py` covers:
- Lexer tokenises all directive types
- Parser handles inline and body attributes
- Parser correctly coerces types
- Condition parser handles all operators and short-hands
- Condition evaluator returns correct results for every status combination
- JIL writer round-trip stability
- Error cases: missing job_type, unknown directive, malformed condition

---

## Phase 3 — State Machine & Variable Substitution

**Goal:** Enforce legal job status transitions and expand variables in commands.

### What is built

#### State machine (`autosys/scheduler/state_machine.py`)

Defines the directed graph of valid `(from_status, to_status)` transitions
and validates every proposed transition before it is applied.

```
INACTIVE ──► ACTIVATED ──► RUNNING ──► SUCCESS
                 │              │
                 ▼              ▼
             STARTING       FAILURE
                 │              │
                 ▼              ▼
             RUNNING       TERMINATED
                 │
                 ▼
           TERMINATED
```

Special states: `ON_HOLD` (job suspended by operator), `ON_ICE` (job removed
from schedule without deleting its definition), `RESTART` (job to be re-run).

```python
validate_transition("my_job", "INACTIVE", "RUNNING")   # raises InvalidTransitionError
validate_transition("my_job", "INACTIVE", "STARTING")  # OK
```

Every transition in the event processor goes through `validate_transition`
before the status column is updated. This prevents bugs where a race condition
or operator error could put a job into an impossible state.

#### Variable substitution integration

The `variable_sub.py` module is called from the dispatch path (both local and
remote) to expand `%%DATE%%` and `%%GLOBAL_VAR%%` references in the `command:`
string immediately before the subprocess is forked. Global variable values are
read from the DB at dispatch time (not at JIL import time), so updating a
global variable via `autosys sendevent -E SET_GLOBAL -J VAR_NAME -A "new_value"`
affects jobs dispatched after the update.

### Tests (68 tests)

`tests/test_phase3.py` covers:
- All valid transitions accepted
- All invalid transitions rejected with `InvalidTransitionError`
- Variable expansion for every placeholder type
- Expansion with missing variable (passthrough)
- `ON_HOLD` / `ON_ICE` transitions

---

## Phase 4 — Event Processor (EPS)

**Goal:** The scheduler's main loop. Reads events from the queue, evaluates
conditions, and drives the job state machine.

### What is built

#### Event types

| Event | Trigger | Effect |
|-------|---------|--------|
| `STARTJOB` | Operator, time trigger, dependency | Evaluate condition; if met → STARTING/ACTIVATED |
| `FORCE_STARTJOB` | Operator | Skip condition check; start immediately |
| `KILLJOB` | Operator | Terminate RUNNING/STARTING job (+ children if BOX) |
| `JOB_ON_HOLD` | Operator | Suspend job in ON_HOLD |
| `JOB_OFF_HOLD` | Operator | Resume a held job |
| `JOB_ON_ICE` | Operator | Remove from scheduling without deleting |
| `JOB_OFF_ICE` | Operator | Re-enable an iced job |
| `CHANGE_STATUS` | Internal | Force a status value directly (testing) |
| `SET_GLOBAL` | Operator/API | Set a global variable value |
| `CHECK_HEARTBEAT` | Scheduler | Ping a machine's agent; mark UP or DOWN |
| `SEND_EVENT` | Job output | A job can enqueue an event as output |

#### `EventProcessor` (`autosys/scheduler/event_processor.py`)

The core class. `process_one_tick(session, now)` does:

1. **Dequeue** all pending events from `event_queue` (FIFO)
2. **Handle** each event via a dispatch table (`_handle_startjob`, `_handle_killjob`, …)
3. **Box tick** — `BoxManager.tick()` activates eligible children of RUNNING boxes
4. **Time trigger** — `get_triggered_jobs()` enqueues `STARTJOB` events for jobs
   whose `start_times` + `days_of_week` match the current time
5. Return the count of events processed

The `now` parameter is injectable — tests pass a fake `datetime` to simulate
any time of day without waiting for the real clock.

#### Status snapshot

Before each tick, a **snapshot** is built:

```python
snapshot: dict[str, str] = {row.job_name: row.status for row in job_repo.list_all(session)}
```

All condition evaluations within a tick use this same snapshot so concurrent
job status changes don't create mid-tick inconsistencies. This matches real
AutoSys's "evaluate all conditions at the same instant" behaviour.

#### Dispatch function

The `dispatch_fn` parameter is injected at construction time:

```python
proc = EventProcessor(dispatch_fn=my_dispatcher)
```

- **Phase 4 stub**: `_stub_dispatch` — sets the job immediately to RUNNING (and
  optionally SUCCESS if `auto_complete=True`). Used by all condition and state
  machine tests.
- **Phase 5**: replaced by `LocalJobRunner` — forks the actual subprocess
- **Phase 6**: `RemoteDispatch` — sends a TCP `DISPATCH` message to the agent
  server on the target machine

This injection pattern means the event processor tests never touch real
subprocesses and run in milliseconds.

#### Time trigger (`autosys/scheduler/time_trigger.py`)

Checks every job's `start_times` and `days_of_week` against the current time.
A job is triggered when:

1. The current time matches any entry in `start_times` (within the minute boundary)
2. The current day matches `days_of_week` (or `days_of_week = "all"`)
3. Today's date is not in the job's `exclude_calendar`
4. Today's date IS in the job's `run_calendar` (if one is specified)

#### Condition evaluator (`autosys/scheduler/condition_evaluator.py`)

```python
is_satisfied("s(check_source_ready)", {"check_source_ready": "SUCCESS"})  # → True
is_satisfied("s(a) & s(b)", {"a": "SUCCESS", "b": "FAILURE"})             # → False
```

Uses the AST from `condition_parser.py`. Undefined job names evaluate to
`False` for all predicates (matches real AutoSys — a typo in a condition
will silently block the job).

#### `sendevent` CLI (`autosys/cli/sendevent_cmd.py`)

```bash
autosys sendevent -E STARTJOB -J my_job
autosys sendevent -E KILLJOB  -J my_job
autosys sendevent -E JOB_ON_HOLD -J my_job
autosys sendevent -E SET_GLOBAL -J MY_VAR -A "new_value"
```

### Tests (112 tests)

`tests/test_phase4.py` covers:
- Every event type exercised end-to-end
- Condition evaluation with met/unmet/partially-met conditions
- Time trigger fires exactly at the right minute, not before
- `exclude_calendar` prevents triggering on excluded dates
- Multiple events processed in FIFO order
- `auto_complete=False` leaves jobs in RUNNING for inspection

---

## Phase 5 — System Agent (Local Execution)

**Goal:** Actually run shell commands as subprocesses and capture their output.

### What is built

#### `LocalJobRunner` (`autosys/agent/runner.py`)

Runs a shell command as a subprocess in a background thread:

1. Forks `subprocess.Popen(command, shell=True, stdout=PIPE, stderr=STDOUT)`
2. Streams stdout line-by-line to `OutputRepository.append_line()` (via callback)
3. Applies `%%DATE%%` / `%%AUTOUSER%%` expansion to the command string
4. Enforces `max_run_alarm` — if the job runs longer than `max_run_alarm` minutes,
   it is killed with SIGTERM and a `KILLJOB` event is raised
5. On exit: calls `RunRepository.finish()` with the exit code and final status
6. Handles `KILLJOB` mid-run via `runner.kill()` which sends SIGTERM to the process group

#### Dispatch router (`autosys/agent/dispatch.py`)

Decides whether a job should run locally or be sent to a remote agent:

```
if job.machine == "localhost" or job.machine == this_host:
    → LocalJobRunner (direct subprocess)
else:
    → look up MachineRow in DB
    → RemoteDispatch.dispatch(session, job, machine_row)
```

#### `autosys agent jobs` and `autosys agent tail`

```bash
autosys agent jobs           # list currently running jobs
autosys agent tail my_job    # tail the stdout of the most recent run of my_job
```

`tail` reads from `job_output` table and follows new lines as they are appended.

### Tests (60 tests)

`tests/test_phase5.py` covers:
- Real subprocess execution (echo, exit codes 0 and 1)
- `%%DATE%%` expansion in command string
- stdout captured line-by-line to `job_output`
- `max_run_alarm` kills long-running job
- `KILLJOB` mid-run terminates the subprocess
- Run history written to `job_runs` on completion

---

## Phase 6 — Remote Dispatch via TCP

**Goal:** The scheduler dispatches jobs to agent daemons on remote machines over TCP.

### What is built

#### Protocol (`autosys/agent/protocol.py`)

Newline-delimited JSON messages between the scheduler and agent:

```json
{"type": "DISPATCH",   "run_id": "uuid", "job_name": "extract_sales", "command": "/scripts/run.sh", "run_date": "2024-01-15"}
{"type": "HEARTBEAT",  "machine_name": "etl-server-01"}
{"type": "DISPATCH_RESPONSE", "run_id": "uuid", "accepted": true}
{"type": "HEARTBEAT_RESPONSE", "alive": true}
{"type": "ERROR",      "message": "job not found"}
```

The framing is a simple newline (`\n`) delimiter. Each message is a single JSON
object on one line. `send_message(host, port, msg, timeout=10.0)` handles
connection, send, receive, and close.

#### Agent TCP server (`autosys/agent/server.py`)

An asyncio TCP server that listens on the agent port (default 7520):

- On `DISPATCH`: creates a `LocalJobRunner` in a background thread and returns
  `DISPATCH_RESPONSE` immediately (the job runs asynchronously)
- On `HEARTBEAT`: replies `HEARTBEAT_RESPONSE(alive=True)`
- Creates the `job_runs` record when it receives `DISPATCH` (not the scheduler)
  — the agent owns the run record because it knows the actual PID and start time

#### `RemoteDispatch` (`autosys/agent/remote.py`)

The scheduler-side client:

```python
rd = RemoteDispatch()
run_id = rd.dispatch(session, job_row, machine_row)  # returns run_id
alive  = rd.heartbeat(machine_row)                   # returns bool
```

`dispatch()`:
1. Expands `%%DATE%%` and global variables in the command string
2. Generates a `run_id` UUID
3. Transitions the job status to `RUNNING` in the DB
4. Sends a `DISPATCH` JSON message to `machine_row.host:machine_row.port`
5. Returns the `run_id` for correlation

#### Machine registry CLI (`autosys/cli/machine_cmd.py`)

```bash
autosys machine register etl-server-01 10.0.0.1 7520
autosys machine list
autosys machine check etl-server-01   # sends HEARTBEAT, prints UP/DOWN
```

#### `CHECK_HEARTBEAT` event

The scheduler can enqueue `CHECK_HEARTBEAT` events to periodically verify
agent health. The event processor sends a `HEARTBEAT` TCP message; if it
fails, the machine is marked `DOWN` in the `machines` table and an alarm is
raised (if `alarm_if_fail=1` on any machine-linked jobs).

### Tests (43 tests)

`tests/test_phase6.py` covers:
- Protocol encode/decode round-trip
- Agent server starts, accepts connections, and returns DISPATCH_RESPONSE
- Agent server handles HEARTBEAT
- Run history written to `job_runs` by the agent
- `MachineRepository` register/upsert/heartbeat/status
- CLI `machine register/list/check` commands

---

## Phase 7 — Box Orchestration & insert_machine

**Goal:** BOX jobs orchestrate their children. Machine definitions can appear
in JIL files. Condition shorthand aliases added.

### What is built

#### `BoxManager` (`autosys/scheduler/box_manager.py`)

Called on every EPS tick after events are processed. For each RUNNING box:

1. **Activate eligible children** — any child in `INACTIVE` state whose condition
   is satisfied (against the tick-start snapshot) is transitioned to `STARTING`
   and dispatched
2. **Complete the box** — when ALL children are in a terminal state (`SUCCESS`,
   `FAILURE`, `TERMINATED`):
   - All `SUCCESS` → box `SUCCESS`
   - Any `FAILURE` → box `FAILURE`
   - Any `TERMINATED` → box `TERMINATED`

**Critical design decision:** The condition snapshot is built ONCE per tick,
before any child activations. A child activated in tick N becomes visible to
sibling conditions in tick N+1. This matches real AutoSys's per-tick evaluation
semantics and means sequential chains need one tick per dependency hop:

```
Tick 1: check_source_ready activated (no condition)
Tick 2: extract_sales activated (s(check_source_ready) → now met)
Tick 3: generate_report + load_to_warehouse activated (s(extract_sales) → met)
Tick 4: send_success_email activated (both met), box completes
```

#### BOX lifecycle in the event processor

```
STARTJOB box → _activate_box()
    → box: INACTIVE → ACTIVATED
    → _cascade_box_children(): start children with met conditions immediately
    → _update_box_status(): ACTIVATED → RUNNING if any child is STARTING/RUNNING

Per tick → BoxManager.tick()
    → find RUNNING boxes
    → evaluate INACTIVE children against tick-start snapshot
    → complete box when all children terminal

KILLJOB box → kill_children() → terminate all STARTING/RUNNING children → box TERMINATED
FORCE_STARTJOB box → reset_children() (all → INACTIVE) → _activate_box() fresh start
```

#### `insert_machine:` JIL syntax

Machines can now be defined alongside jobs in JIL files:

```jil
insert_machine: etl-server-01
    type: a
    host: 192.168.1.10
    port: 7520
    max_load: 100
    description: Primary ETL worker
```

Changes:
- **Lexer**: `insert_machine` added to `_DIRECTIVES` frozenset and `_STANZA_HEADER_RE`
- **Parser**: produces `JILOperation(op="insert_machine", machine=MachineDef, job=None)`
- **`jil import`**: handles `op == "insert_machine"` → upserts the `machines` table
- **`machine_to_jil()`**: serialises a `MachineRow` back to JIL (round-trip safe)
- **`MachineDef` model**: Pydantic validation — port range 1–65535, `host` defaults to `machine_name`

#### Condition shorthands

Single-letter AutoSys condition shorthands added to the condition parser:

| Short | Full | Meaning |
|-------|------|---------|
| `s(j)` | `success(j)` | job j is SUCCESS |
| `f(j)` | `failure(j)` | job j is FAILURE |
| `d(j)` | `done(j)` | job j is SUCCESS or FAILURE |
| `n(j)` | `notrunning(j)` | job j is not STARTING or RUNNING |
| `t(j)` | `terminated(j)` | job j is TERMINATED |

#### CLI additions

```bash
autosys box status demo_etl_box   # Rich table: children + status + condition
autosys box tree   demo_etl_box   # Dependency tree with colour-coded status
```

### Tests (69 tests)

`tests/test_phase7.py` covers all 11 test classes listed above, including a
full pipeline integration test that imports `demo_etl.jil`, fires `STARTJOB`,
ticks 10 times, and asserts the box reaches `SUCCESS`.

---

## Phase 8 — REST API Application Server (SSA)

**Goal:** Expose all scheduler state over HTTP so external systems (dashboards,
other schedulers, CI/CD pipelines) can query and control jobs without touching
the database directly.

### What will be built

#### FastAPI application (`autosys/app_server/`)

```
GET    /api/jobs                    # list all jobs with current status
GET    /api/jobs/{name}             # single job detail
POST   /api/jobs/{name}/sendevent   # enqueue an event (STARTJOB, KILLJOB, …)
GET    /api/runs?job={name}&limit=N # run history for a job
GET    /api/runs/{run_id}/output    # stdout lines for a run
GET    /api/machines                # list registered machines with status
POST   /api/machines                # register a machine
GET    /api/alarms?resolved=false   # active alarms
POST   /api/alarms/{id}/resolve     # acknowledge an alarm
GET    /api/globals                 # list global variables
PUT    /api/globals/{name}          # set a global variable
GET    /health                      # liveness probe
```

#### WebSocket live feed

```
WS /api/ws/events     # streams Job status change events as JSON
```

The event processor publishes a `{job_name, old_status, new_status, ts}`
message every time a job status changes. The WCC dashboard subscribes to this
feed for live updates.

#### JWT authentication (`autosys/app_server/auth.py`)

Real AutoSys uses CA EEM (Embedded Entitlements Manager) for auth. We
implement a simplified JWT-based scheme:

- `POST /api/auth/login` with `{username, password}` → returns `{access_token, expires_in}`
- All other endpoints require `Authorization: Bearer <token>`
- Roles: `viewer` (GET only), `operator` (can enqueue events), `admin` (everything)

#### `autosys scheduler start` CLI

```bash
autosys scheduler start --port 9000     # starts uvicorn + EPS loop
autosys scheduler status                # check if daemon is running
autosys scheduler stop                  # send SIGTERM to daemon
```

### How components wire together in Phase 8

```
CLI ──POST /api/jobs/extract_sales/sendevent──► App Server
                                                    │
                                          EventRepository.enqueue()
                                                    │
                                          EPS.process_one_tick()  (runs in background)
                                                    │
                                          Dispatch to agent
                                                    │
                                          Status update in DB
                                                    │
                                          WebSocket broadcast ──► WCC Dashboard
```

### Tests (planned ~80 tests)

`tests/test_phase8.py` will cover:
- Every REST endpoint returns correct HTTP status codes
- `POST /sendevent` results in an event being dequeued and processed
- WebSocket receives status-change events in real time
- JWT login/logout flow
- Authorisation rules (viewer cannot POST sendevent)
- `autosys scheduler start` boots the server and EPS loop

---

## Phase 9 — WCC Web Dashboard

**Goal:** A browser-based monitoring UI showing the job grid, dependency flow
graph, alarm console, and run history — mirroring the real AutoSys WCC.

### What will be built

#### Job grid view

A paginated table of all jobs with live-updating status badges (colour-coded
by the WebSocket feed from Phase 8). Columns: Job Name, Type, Status, Machine,
Last Start, Last End, Box, Owner. Filterable by status, machine, or owner.

#### D3.js dependency flow graph

An interactive directed graph for each BOX, rendered with D3.js:

- Nodes = jobs (colour-coded by status: green=SUCCESS, red=FAILURE, blue=RUNNING, grey=INACTIVE)
- Edges = dependency arrows (derived from `condition:` attributes)
- Clicking a node opens the run history sidebar
- The graph updates live as the WebSocket feed delivers status changes

#### Alarm console

A table of active alarms with severity, job name, timestamp, and a Resolve
button that calls `POST /api/alarms/{id}/resolve`.

#### Run history and output tail

Clicking any job shows its last N run records. Clicking a run shows the
captured stdout (from `job_output` table). A "Tail" button opens a live stream
of the current run's output via Server-Sent Events.

#### Implementation

- Server-side: FastAPI serves static files + Jinja2 templates from `autosys/wcc/`
- Client-side: Vanilla JS + D3.js v7 for the graph; htmx for live-updating
  fragments; no React (keeps the build simple)
- Port 8080 (separate from the SSA API at 9000)

### Tests (planned ~40 tests)

`tests/test_phase9.py` will cover:
- Job grid renders all jobs
- Dependency graph renders correct edges
- Alarm console shows only unresolved alarms
- Run history paginates correctly
- Output tail streams content

---

## Phase 10 — Alarms, Notifications & NSM

**Goal:** When jobs fail (and `alarm_if_fail=1`), raise an alarm, notify
operators, and optionally page on-call via NSM-compatible webhooks.

### What will be built

#### `AlarmManager` (`autosys/notifications/alarm_manager.py`)

Evaluates alarm conditions after each EPS tick:

1. For every job that just transitioned to `FAILURE`: if `alarm_if_fail=1` → raise `FAILURE` alarm
2. For every job that just transitioned to `TERMINATED`: if `alarm_if_terminated=1` → raise alarm
3. For jobs still `RUNNING` past `max_run_alarm` minutes → raise `MAX_RUN` alarm
4. For jobs that finished in less than `min_run_alarm` minutes → raise `MIN_RUN` alarm
5. For machines marked `DOWN` in `CHECK_HEARTBEAT` → raise `MACHINE_DOWN` alarm
6. Deduplication: if an identical alarm already exists and is unresolved, skip it

#### `Dispatcher` (`autosys/notifications/dispatcher.py`)

Sends alarm payloads to configured notification channels:

| Channel | Config | Payload |
|---------|--------|---------|
| NSM webhook | `NSM_WEBHOOK_URL` | JSON `{alarm_id, job_name, status, ts}` |
| Email (SMTP) | `SMTP_HOST`, `SMTP_FROM` | Plain-text email |
| Slack | `SLACK_WEBHOOK_URL` | Slack block kit message |
| PagerDuty | `PD_ROUTING_KEY` | PagerDuty Events API v2 |

The dispatcher is pluggable — adding a new channel means implementing one method.

#### Alarm lifecycle

```
Job FAILURE detected
    → AlarmManager.raise_alarm(job_name, alarm_type="FAILURE")
        → INSERT into alarms table (status="ACTIVE")
    → Dispatcher.send(alarm)
        → POST to NSM webhook
        → Send email
    → Operator clicks Resolve in WCC
        → POST /api/alarms/{id}/resolve
        → UPDATE alarms SET status="RESOLVED"
    → If job succeeds on next run → AlarmManager.auto_resolve(job_name)
```

### Tests (planned ~35 tests)

`tests/test_phase10.py` will cover:
- Alarm raised on FAILURE when `alarm_if_fail=1`
- No alarm raised when `alarm_if_fail=0`
- Deduplication (second identical alarm not created)
- `max_run_alarm` triggers after correct duration
- Dispatcher sends to webhook with correct payload
- Auto-resolve when job succeeds after failure

---

## Phase 11 — Calendar Engine & Holiday Exclusions

**Goal:** Full calendar support — named date sets used in `run_calendar` and
`exclude_calendar` to control which days jobs are allowed to run.

### What will be built

#### Calendar model and storage

```jil
insert_calendar: us_holidays
    description: US Federal Holidays 2024-2025
    date: 2024-01-01
    date: 2024-07-04
    date: 2024-11-28
    date: 2024-12-25
    date: 2025-01-01
```

Calendars are imported via `jil import` (extending the parser with an
`insert_calendar:` directive) and stored in `calendars` + `calendar_dates`.

#### `run_calendar` and `exclude_calendar`

- `run_calendar: us_business_days` — job ONLY runs on dates in this calendar
- `exclude_calendar: us_holidays` — job SKIPS dates in this calendar
- Both can be combined: run on business days, skip holidays

The time trigger checks both calendars before enqueuing a `STARTJOB`:

```python
if today in exclude_dates:          return  # skip — holiday
if run_cal and today not in run_cal: return  # skip — not in run calendar
# OK — enqueue STARTJOB
```

#### Calendar CLI

```bash
autosys cal import us_holidays.jil
autosys cal list
autosys cal show us_holidays
autosys cal check us_holidays 2024-07-04   # → "excluded"
```

#### `jil export --with-calendars`

Exports jobs AND their referenced calendar definitions into a single JIL file
that can be imported on another AutoSys instance.

### Tests (planned ~30 tests)

`tests/test_phase11.py` will cover:
- `insert_calendar:` JIL parsing
- Dates stored and retrieved correctly
- Time trigger respects `exclude_calendar`
- Time trigger respects `run_calendar`
- `run_calendar` + `exclude_calendar` combined logic
- Calendar CLI commands

---

## Phase 12 — End-to-End Integration & Production Hardening

**Goal:** Validate the entire system end-to-end with the `demo_etl.jil`
workflow, add PostgreSQL support, and harden for production use.

### What will be built

#### End-to-end test (`tests/test_phase12.py`)

1. Boot the App Server (SSA) on a random port via `subprocess`
2. Import `examples/demo_etl.jil` via the REST API
3. Register two machine definitions (`etl-server-01`, `etl-server-02`) pointing
   to local agent servers started in test fixtures
4. Fire `STARTJOB demo_etl_box` via `POST /api/jobs/demo_etl_box/sendevent`
5. Poll the WebSocket feed until `demo_etl_box` reaches `SUCCESS`
6. Assert the dependency chain ran in the correct order:
   - `check_source_ready` started first (no condition)
   - `extract_sales` started after `check_source_ready` succeeded
   - `generate_report` and `load_to_warehouse` started in parallel after `extract_sales`
   - `send_success_email` started only after both parallel jobs succeeded
7. Assert all run records are in `job_runs` with non-null exit codes
8. Assert no unresolved alarms (all `alarm_if_fail=1` jobs succeeded)

#### PostgreSQL support

Add `asyncpg` as an optional dependency. When `AUTOSYS_DB_URL` starts with
`postgresql+asyncpg://`, the async engine uses PostgreSQL instead of SQLite.
The sync CLI uses `psycopg2` via `postgresql://`.

All schema definitions, queries, and repositories are already DB-agnostic
(written against SQLAlchemy Core). Migration to PostgreSQL requires:
- Changing the `autoincrement` strategy for `event_id` (use `gen_random_uuid()`)
- Updating `journal_mode=WAL` pragma to be SQLite-only
- Adding a `postgresql://` URL to the CI matrix

#### Production hardening

- **Connection pooling**: replace `StaticPool` (SQLite in-memory) with
  `QueuePool` (PostgreSQL) configured via `AUTOSYS_DB_POOL_SIZE`
- **Graceful shutdown**: SIGTERM handler drains the event queue before stopping
  the EPS loop
- **Retry logic**: failed `RemoteDispatch` calls are retried with exponential
  backoff (up to `n_retrys` times, matching the JIL `n_retrys:` attribute)
- **Structured logging**: `loguru` output formatted as JSON for ingestion by
  Datadog / Splunk / ELK
- **Metrics endpoint**: `GET /metrics` returns Prometheus-format counters:
  jobs dispatched, events processed, agent heartbeat failures, alarm count
- **Health checks**: `/health/live` (process alive) and `/health/ready`
  (DB connection valid, event queue draining)

---

## How All Components Wire Together

### Data flow for a scheduled job

```
1. operator runs:   autosys jil import demo_etl.jil
   └─ JILParser parses → JILOperation list
   └─ JobRepository.upsert() writes JobRow to jobs table

2. scheduler ticks (process_one_tick):
   └─ time_trigger.get_triggered_jobs() checks start_times + days_of_week
   └─ if triggered: event_repo.enqueue(STARTJOB event)

3. scheduler ticks again:
   └─ event_repo.dequeue_pending() → [STARTJOB event]
   └─ _handle_startjob():
       └─ is_satisfied(condition, snapshot) → True (or no condition)
       └─ validate_transition(INACTIVE → STARTING)
       └─ row.status = "STARTING"
       └─ dispatch_fn(session, row)
           ├─ local:  LocalJobRunner (subprocess.Popen)
           └─ remote: RemoteDispatch.dispatch()
                       └─ TCP DISPATCH → agent server
                           └─ agent: run_repo.start() + subprocess.Popen
                           └─ agent: stdout lines → output_repo.append_line()
                           └─ agent: on exit → run_repo.finish(exit_code)
                           └─ agent: status → SUCCESS or FAILURE

4. agent completion updates DB → EPS sees SUCCESS on next snapshot
   └─ downstream conditions become satisfied
   └─ downstream jobs are activated on the next tick
```

### Data flow for BOX orchestration

```
1. STARTJOB demo_etl_box event
   └─ _activate_box():
       └─ box: INACTIVE → ACTIVATED
       └─ _cascade_box_children(): children with met conditions → STARTING
       └─ _update_box_status(): box → RUNNING

2. Per tick → BoxManager.tick():
   └─ snapshot built at tick start
   └─ for each INACTIVE child: evaluate condition against snapshot
       └─ if met → child STARTING → dispatch
   └─ if all children terminal:
       └─ any FAILURE → box FAILURE
       └─ any TERMINATED → box TERMINATED
       └─ all SUCCESS → box SUCCESS

3. Box SUCCESS propagates:
   └─ downstream jobs waiting on s(demo_etl_box) now see SUCCESS
   └─ their STARTJOB events fire on the next tick
```

### Session lifecycle

Every component that touches the DB is passed an open `Session` object
(not a URL or engine). The session is opened by the calling context
(CLI command, EPS tick loop, test fixture) and committed/rolled-back
by that same context. This means:

- No nested transactions
- No session leaks
- Tests control exactly when data is committed
- The EPS loop commits after every `process_one_tick` call

---

## JIL Reference

### Job directives

```jil
insert_job: <name>   job_type: <CMD|BOX|FILEWATCHER>
update_job: <name>   [partial attributes — only changed ones required]
delete_job: <name>
override_job: <name> [attributes — applied on top of existing definition]
```

### Machine directive

```jil
insert_machine: <name>
    type: a                      # "a" = UNIX agent (only supported type)
    host: <ip-or-hostname>       # defaults to machine_name if omitted
    port: 7520                   # default
    max_load: 100                # max concurrent jobs (enforced in Phase 8+)
    description: <text>
```

### Calendar directive

```jil
insert_calendar: <name>
    description: <text>
    date: YYYY-MM-DD             # one per line, as many as needed
```

### Job attributes reference

| Attribute | Type | Description |
|-----------|------|-------------|
| `job_name` | string | Unique job identifier |
| `job_type` | enum | `CMD`, `BOX`, `FILEWATCHER` |
| `command` | string | Shell command (CMD only). Supports `%%DATE%%`, `%%GLOBAL_VAR%%` |
| `machine` | string | Target machine name (must be registered) |
| `box_name` | string | Parent BOX job name (makes this job a child of that box) |
| `condition` | string | Condition expression. Blank = no condition (runs unconditionally) |
| `owner` | string | Job owner (used for `%%AUTOUSER%%` expansion and audit) |
| `start_times` | list | Times to auto-trigger: `"06:00"`, `"06:00,18:00"` |
| `days_of_week` | list | Days to run: `mo,tu,we,th,fr`, `all`, `su,sa` |
| `run_calendar` | string | Calendar name — ONLY run on dates in this calendar |
| `exclude_calendar` | string | Calendar name — SKIP dates in this calendar |
| `alarm_if_fail` | bool | Raise an alarm if job reaches FAILURE (default: 1) |
| `alarm_if_terminated` | bool | Raise an alarm if job is killed (default: 0) |
| `max_run_alarm` | int | Minutes before a MAX_RUN alarm fires (0 = disabled) |
| `min_run_alarm` | int | Alert if job completes in less than this many minutes |
| `term_run_time` | int | Kill job automatically after this many minutes |
| `n_retrys` | int | Number of automatic retries on FAILURE (0 = no retry) |
| `priority` | int | Dispatch priority within a box (higher = first, Phase 8) |
| `job_load` | int | Job's contribution to the machine's `max_load` counter |
| `std_out_file` | string | Path to write stdout on the agent machine |
| `std_err_file` | string | Path to write stderr on the agent machine |
| `description` | string | Free-text description |
| `watch_file` | string | File to watch (FILEWATCHER only) |
| `watch_interval` | int | Polling interval in seconds (FILEWATCHER only) |
| `watch_file_min_size` | int | Minimum file size in bytes to consider it ready |

### Condition expression syntax

```
condition := or_expr
or_expr   := and_expr ('|' and_expr)*
and_expr  := not_expr ('&' not_expr)*
not_expr  := '!' atom | atom
atom      := func(job_name) | '(' condition ')' | value(var) = "literal"

Functions (full and shorthand):
  success(j)    s(j)   — job j has status SUCCESS
  failure(j)    f(j)   — job j has status FAILURE
  done(j)       d(j)   — job j is SUCCESS or FAILURE
  notrunning(j) n(j)   — job j is not STARTING or RUNNING
  terminated(j) t(j)   — job j has status TERMINATED
  activated(j)         — job j is ACTIVATED (BOX started but not all children done)

Value condition:
  value(GLOBAL_VAR_NAME) = "expected_value"
```

---

## CLI Reference

### `autosys jil`

```bash
autosys jil import <file>             # import jobs and machines from JIL
autosys jil import <file> --dry-run   # validate without writing to DB
autosys jil import <file> --quiet     # suppress per-job output
autosys jil export                    # export all jobs to stdout as JIL
autosys jil export --job <name>       # export one job
autosys jil validate <file>           # parse and validate without DB
```

### `autosys autorep`

```bash
autosys autorep -j <job_name>         # single job status
autosys autorep -J <box_name>         # box + all children
autosys autorep -q <pattern>          # jobs matching glob pattern
autosys autorep -s RUNNING            # all jobs in a given status
```

### `autosys sendevent`

```bash
autosys sendevent -E STARTJOB       -J <job>
autosys sendevent -E FORCE_STARTJOB -J <job>
autosys sendevent -E KILLJOB        -J <job>
autosys sendevent -E JOB_ON_HOLD    -J <job>
autosys sendevent -E JOB_OFF_HOLD   -J <job>
autosys sendevent -E JOB_ON_ICE     -J <job>
autosys sendevent -E JOB_OFF_ICE    -J <job>
autosys sendevent -E CHANGE_STATUS  -J <job> -s <status>
autosys sendevent -E SET_GLOBAL     -J <var_name> -A "<value>"
```

### `autosys box`

```bash
autosys box status <box_name>         # table: children + status + condition + machine
autosys box tree   <box_name>         # tree view with dependency ordering
autosys box tree   <box_name> -d 5    # limit tree depth
```

### `autosys machine`

```bash
autosys machine register <name> <host> <port>
autosys machine list
autosys machine check <name>          # send heartbeat, print UP or DOWN
```

### `autosys agent`

```bash
autosys agent serve                   # start TCP agent server on port 7520
autosys agent serve --port 7521 --name my-agent
autosys agent jobs                    # list jobs running on this agent
autosys agent tail <job_name>         # tail stdout of the most recent run
```

### `autosys scheduler`

```bash
autosys scheduler start               # start the EPS daemon + SSA
autosys scheduler start --port 9000
autosys scheduler status
autosys scheduler stop
```

---

## Configuration Reference

All configuration is via environment variables (12-factor app style).

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOSYS_DB_URL` | `sqlite:///data/autosys.db` | SQLAlchemy database URL |
| `AUTOSYS_AGENT_PORT` | `7520` | Port for the System Agent TCP server |
| `AUTOSYS_AGENT_NAME` | hostname | Logical name of this agent |
| `AUTOSYS_SSA_PORT` | `9000` | Port for the App Server REST API |
| `AUTOSYS_WCC_PORT` | `8080` | Port for the WCC web dashboard |
| `AUTOSYS_POLL_INTERVAL` | `1.0` | EPS tick interval in seconds |
| `AUTOSYS_JWT_SECRET` | — | Secret key for JWT signing (Phase 8) |
| `AUTOSYS_JWT_TTL` | `3600` | JWT expiry in seconds |
| `NSM_WEBHOOK_URL` | — | NSM webhook endpoint for alarms (Phase 10) |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook URL (Phase 10) |
| `PD_ROUTING_KEY` | — | PagerDuty Events API routing key (Phase 10) |
| `AUTOSYS_SQL_ECHO` | `false` | Set to `true` to log all SQL statements |

For testing, `monkeypatch.setenv("AUTOSYS_DB_URL", ...)` is sufficient to
isolate each test in its own SQLite file. The engine cache is keyed by URL, so
different test processes and threads never share a connection pool.

---

## Testing Strategy

### Principles

1. **Each phase has its own test file** — `test_phaseN.py` tests only the layer
   added in that phase, with the previous layers as trusted dependencies
2. **Isolated DB per test** — every test function gets a fresh `tmp_path` SQLite
   file via the `isolated_db` autouse fixture
3. **Injectable dispatch** — the `EventProcessor` accepts a `dispatch_fn`
   parameter; tests pass a stub that completes jobs immediately without forking
4. **Injectable time** — `process_one_tick(session, now=fake_datetime)` for
   time-trigger tests
5. **No mocking** — we prefer real implementations over mocks. The stub
   dispatcher is a real function, not a `unittest.mock.Mock`

### Running tests

```bash
# Full suite
pytest tests/

# One phase
pytest tests/test_phase4.py -v

# One test class
pytest tests/test_phase4.py::TestEventProcessor -v

# Filter by name
pytest tests/ -k "box and condition"

# With SQL logging
AUTOSYS_SQL_ECHO=true pytest tests/test_phase7.py -v

# Stop on first failure
pytest tests/ -x

# Coverage report
pytest tests/ --cov=autosys --cov-report=term-missing
```

### Test counts by phase

| Phase | Tests | What they cover |
|-------|-------|-----------------|
| 1 | 47 | Schema, repositories, model validation |
| 2 | 87 | Lexer, parser, condition language, JIL writer |
| 3 | 68 | State machine transitions, variable substitution |
| 4 | 112 | Event processor, all event types, time trigger |
| 5 | 60 | LocalJobRunner, subprocess, stdout capture, kill |
| 6 | 43 | TCP protocol, agent server, RemoteDispatch, machine registry |
| 7 | 69 | BoxManager, insert_machine, box CLI commands |
| 8 | 40 | REST API, auth, WebSocket, alarms, globals, metrics |
| 9 | 30 | WCC JSON API, SSE, React frontend hardening |
| 10 | 25 | Alarm manager, notification dispatcher, SNMP/Remedy |
| 11 | 20 | Calendar engine, holiday exclusions, CLI |
| 12 | 15 | End-to-end integration, production hardening |
| Migration | 50 | Simulator, structural analysis, complexity report, e2e |
| Other | 35 | HA, cloud integration, xinst, notifications, agents |
| **Total** | **1072** | |

---

*Built as a learning reference for understanding enterprise batch scheduling internals.*
*Every design decision in the code comments references the equivalent real AutoSys component.*
