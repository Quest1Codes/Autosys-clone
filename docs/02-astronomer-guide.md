# Astronomer (Apache Airflow) — Complete Developer Guide

## 1. What is Astronomer?

**Astronomer** is a **managed cloud platform built on top of Apache Airflow**.

To understand Astronomer, you must first understand the relationship:

```
Apache Airflow          ←  the open-source engine (like Linux kernel)
        │
        ▼
Astronomer Platform     ←  the managed product built on top (like Ubuntu)
        │
        ▼
Astro CLI               ←  the developer tool you use locally (like apt/brew)
```

| | Apache Airflow | Astronomer |
|---|---|---|
| **What it is** | Open-source workflow orchestrator | Managed enterprise platform |
| **Who makes it** | Apache Software Foundation | Astronomer Inc. |
| **How you get it** | Install yourself, manage yourself | Hosted/managed service |
| **Dev tool** | `airflow` CLI | `astro` CLI |
| **Cost** | Free | Paid (enterprise) |

> **In this migration project:** The client currently uses AutoSys. They are moving
> to Astronomer (which runs Airflow under the hood). You write Airflow code; Astronomer
> hosts and runs it.

---

## 2. The Core Concept: DAGs

Everything in Airflow revolves around a **DAG** — Directed Acyclic Graph.

```
Directed  = tasks flow in one direction (no cycles)
Acyclic   = a task cannot depend on itself (no loops)
Graph     = a network of connected tasks
```

**Visual example:**

```
extract_data ──► transform_data ──► load_data
                                        │
                              ┌─────────┴──────────┐
                              ▼                    ▼
                         send_report          notify_team
```

This is exactly what an AutoSys BOX with `condition: success()` chains represents.
The **DAG = AutoSys BOX**, and each **Task = AutoSys Job**.

---

## 3. Airflow Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   Astronomer / Airflow                    │
│                                                          │
│  ┌────────────┐    ┌────────────┐    ┌────────────────┐  │
│  │ Web Server │    │ Scheduler  │    │    Database    │  │
│  │  (UI)      │    │ (brain)    │    │  (PostgreSQL)  │  │
│  └────────────┘    └─────┬──────┘    └────────────────┘  │
│                          │                               │
│                   ┌──────▼──────┐                        │
│                   │   Executor  │                        │
│                   │(Celery / K8s│                        │
│                   └──────┬──────┘                        │
│                          │                               │
│          ┌───────────────┼───────────────┐               │
│          ▼               ▼               ▼               │
│   ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│   │  Worker 1  │  │  Worker 2  │  │  Worker 3  │        │
│   │ (K8s Pod)  │  │ (K8s Pod)  │  │ (K8s Pod)  │        │
│   └────────────┘  └────────────┘  └────────────┘        │
└──────────────────────────────────────────────────────────┘
```

| Component | Role | AutoSys equivalent |
|---|---|---|
| **Scheduler** | Reads DAG files, decides what runs when | Event Server (EEM) |
| **Web Server** | The UI you browse | WCC (GUI) |
| **Database** | Stores DAG runs, task states, logs | AutoSys internal DB |
| **Executor** | Decides HOW tasks run (Celery or K8s) | Agent dispatcher |
| **Worker** | Actually runs the task code | AutoSys Agent |
| **DAG file** | Python file defining the workflow | JIL file |

---

## 4. Your First DAG — Python Code

A DAG is just a **Python file** placed in the `dags/` folder. Airflow reads it
automatically.

```python
from datetime import datetime
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

# --- Define the DAG ---
with DAG(
    dag_id="daily_etl",                    # unique name (like AutoSys box_name)
    start_date=datetime(2024, 1, 1),       # when scheduling begins
    schedule="0 6 * * 1-5",               # cron: 6 AM, Mon-Fri
    catchup=False,                         # don't backfill missed runs
    tags=["etl", "sales"],
) as dag:

    # --- Task 1: Extract ---
    extract = BashOperator(
        task_id="extract_data",            # unique within this DAG
        bash_command="/scripts/extract.sh",
    )

    # --- Task 2: Transform ---
    transform = BashOperator(
        task_id="transform_data",
        bash_command="/scripts/transform.sh",
    )

    # --- Task 3: Load ---
    load = BashOperator(
        task_id="load_data",
        bash_command="/scripts/load.sh",
    )

    # --- Define dependencies (the >> operator means "then") ---
    extract >> transform >> load
```

**Equivalent AutoSys JIL:**
```jil
insert_job: daily_etl_box   job_type: BOX
start_times: "06:00"
days_of_week: mo,tu,we,th,fr

insert_job: extract_data   job_type: CMD
box_name: daily_etl_box
command: /scripts/extract.sh
machine: etl-server-01

insert_job: transform_data   job_type: CMD
box_name: daily_etl_box
command: /scripts/transform.sh
machine: etl-server-01
condition: success(extract_data)

insert_job: load_data   job_type: CMD
box_name: daily_etl_box
command: /scripts/load.sh
machine: etl-server-01
condition: success(transform_data)
```

---

## 5. DAG Parameters — The Full Reference

```python
with DAG(
    dag_id="my_dag",                        # unique identifier
    description="My ETL pipeline",          # human-readable description
    start_date=datetime(2024, 1, 1),        # when to start scheduling
    end_date=datetime(2024, 12, 31),        # optional: when to stop
    schedule="@daily",                      # when to run (see schedules below)
    catchup=False,                          # backfill missed runs? (usually False)
    max_active_runs=1,                      # max concurrent DAG runs
    default_args={                          # defaults applied to all tasks
        "owner": "data_team",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "email_on_failure": True,
        "email": ["alerts@company.com"],
    },
    tags=["etl", "finance"],
) as dag:
    ...
```

### 5.1 Schedule Intervals (AutoSys `start_times` equivalent)

| Airflow value | Meaning | AutoSys equivalent |
|---|---|---|
| `"@hourly"` | Every hour | `start_mins: "0"` |
| `"@daily"` | Once a day at midnight | `start_times: "00:00"` |
| `"@weekly"` | Once a week on Sunday | `days_of_week: su` |
| `"@monthly"` | First day of month | `days_of_month: 1` |
| `"0 6 * * 1-5"` | 6 AM weekdays (cron) | `start_times: "06:00"` + `days_of_week: mo,tu,we,th,fr` |
| `None` | Manual trigger only | AutoSys job with `on_ice` until manually started |
| `timedelta(hours=4)` | Every 4 hours | `start_mins` + `start_times` combination |

**Cron format reminder:**
```
┌───── minute (0-59)
│ ┌───── hour (0-23)
│ │ ┌───── day of month (1-31)
│ │ │ ┌───── month (1-12)
│ │ │ │ ┌───── day of week (0=Sun, 1=Mon ... 6=Sat)
│ │ │ │ │
* * * * *
```

---

## 6. Operators — The Task Types

An **Operator** defines what a task does. Think of it as the `job_type` in AutoSys.

### 6.1 Core Operators

| Operator | What it does | AutoSys job_type |
|---|---|---|
| `BashOperator` | Runs a shell command | `CMD` |
| `PythonOperator` | Runs a Python function | `CMD` (with Python script) |
| `EmailOperator` | Sends an email | `CMD` (with mail script) |
| `DummyOperator` | No-op placeholder / grouping | (no equivalent) |

```python
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

run_script = BashOperator(
    task_id="run_script",
    bash_command="python /opt/etl/process.py --date {{ ds }}",
)

def my_python_func():
    print("Hello from Python!")

run_python = PythonOperator(
    task_id="run_python",
    python_callable=my_python_func,
)
```

### 6.2 Provider Operators (from the Airflow Provider ecosystem)

Airflow has **1000+ operators** via installable provider packages:

| Provider | Operator | AutoSys equivalent |
|---|---|---|
| `apache-airflow-providers-postgres` | `PostgresOperator` | `DB` job type |
| `apache-airflow-providers-mysql` | `MySqlOperator` | `DB` job type |
| `apache-airflow-providers-oracle` | `OracleOperator` | `DB` job type |
| `apache-airflow-providers-sftp` | `SFTPOperator` | `FTP` job type |
| `apache-airflow-providers-http` | `HttpOperator` | Custom CMD calling curl |
| `apache-airflow-providers-amazon` | `S3CopyObjectOperator`, etc. | Custom CMD |
| `apache-airflow-providers-ssh` | `SSHOperator` | CMD on remote `machine:` |

```python
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.providers.ssh.operators.ssh import SSHOperator

# Run SQL — AutoSys DB job equivalent
run_sql = PostgresOperator(
    task_id="run_stored_proc",
    postgres_conn_id="prod_db",           # Airflow Connection (see Section 9)
    sql="EXEC sp_load_eod_results;",
)

# Run on a remote server — AutoSys agent + machine: equivalent
run_remote = SSHOperator(
    task_id="run_on_legacy_server",
    ssh_conn_id="fin_server_01",          # Airflow Connection
    command="/finance/scripts/run_calc.sh",
)
```

### 6.3 Sensors — Waiting for Things

A **Sensor** is a special operator that **waits** until a condition is true, then
lets downstream tasks proceed. This is how you handle AutoSys file-watcher jobs
(`job_type: OMTF` or `condition: f(filename)`).

| Sensor | Waits for | AutoSys equivalent |
|---|---|---|
| `FileSensor` | A file to appear on the filesystem | `condition: f(filename)` |
| `S3KeySensor` | A file in an S3 bucket | `condition: f(s3_path)` |
| `HttpSensor` | An HTTP endpoint to return success | External condition check |
| `SqlSensor` | A SQL query to return rows | DB condition check |
| `ExternalTaskSensor` | Another DAG's task to complete | `condition: success(other_job)` |

```python
from airflow.sensors.filesystem import FileSensor

wait_for_file = FileSensor(
    task_id="wait_for_input_file",
    filepath="/data/input/daily_feed_{{ ds }}.csv",
    poke_interval=60,      # check every 60 seconds
    timeout=3600,          # fail if file not found within 1 hour
)
```

---

## 7. Task Dependencies

Airflow uses Python operators to define task order. Three ways to do it:

```python
# Method 1: >> operator (most common, most readable)
extract >> transform >> load

# Method 2: << operator (reverse direction)
load << transform << extract

# Method 3: set_downstream / set_upstream (explicit)
extract.set_downstream(transform)
transform.set_downstream(load)

# Parallel tasks (fan-out then fan-in):
#   extract → transform → report
#                      → db_load
#                           ↓
#                         notify

transform >> [report, db_load] >> notify
```

**AutoSys `condition:` → Airflow `>>` mapping:**

| AutoSys JIL | Airflow Python |
|---|---|
| `condition: success(job_a)` | `job_a >> job_b` |
| `condition: success(a) & success(b)` | `[a, b] >> job_c` |
| No condition (parallel start) | Both tasks declared in DAG, no `>>` between them |

---

## 8. Templating — Dynamic Values (AutoSys `%%VAR%%` equivalent)

Airflow uses **Jinja2 templating** with `{{ }}` syntax inside string parameters.

```python
# AutoSys: command: /scripts/run.sh %%BUSINESS_DATE%%
# Airflow:
run_script = BashOperator(
    task_id="run_script",
    bash_command="/scripts/run.sh {{ ds }}",  # ds = execution date (YYYY-MM-DD)
)
```

### Built-in Template Variables

| Variable | Value | AutoSys equivalent |
|---|---|---|
| `{{ ds }}` | Execution date: `2024-01-15` | `%%BUSINESS_DATE%%` |
| `{{ ds_nodash }}` | Date no dashes: `20240115` | custom variable |
| `{{ ts }}` | Full timestamp: `2024-01-15T06:00:00` | — |
| `{{ dag.dag_id }}` | Name of this DAG | `%%AUTO_JOB_NAME%%` |
| `{{ task.task_id }}` | Name of this task | — |
| `{{ run_id }}` | Unique run identifier | — |
| `{{ var.value.MY_VAR }}` | Airflow Variable named `MY_VAR` | `%%MY_VAR%%` |

---

## 9. Variables and Connections (AutoSys Global Variables equivalent)

### 9.1 Airflow Variables

Simple key-value pairs stored in the Airflow database. Set via UI or CLI.

```bash
# CLI
airflow variables set BUSINESS_DATE 2024-01-15
airflow variables get BUSINESS_DATE
```

```python
# In a DAG
from airflow.models import Variable

business_date = Variable.get("BUSINESS_DATE")
db_host = Variable.get("DB_HOST", default_var="localhost")
```

### 9.2 Airflow Connections

Connections store **credentials + host info** for external systems (databases, APIs,
SSH servers). They replace AutoSys's global variables that hold connection strings.

```bash
# CLI
airflow connections add prod_postgres \
  --conn-type postgres \
  --conn-host db-prod.company.com \
  --conn-login etl_user \
  --conn-password secret123 \
  --conn-port 5432 \
  --conn-schema etl_db
```

```python
# In a DAG — just reference by conn_id, no credentials in code
run_sql = PostgresOperator(
    task_id="run_sql",
    postgres_conn_id="prod_postgres",    # ← references the connection
    sql="SELECT 1;",
)
```

> **Security best practice:** In Astronomer, connections are stored in a secret backend
> (e.g., HashiCorp Vault, AWS Secrets Manager) — credentials never live in the DB or code.

---

## 10. XCom — Passing Data Between Tasks

**XCom** (cross-communication) lets tasks share small values with each other.

```python
def extract_func(**context):
    record_count = 1500
    # Push a value to XCom
    context["ti"].xcom_push(key="record_count", value=record_count)

def load_func(**context):
    # Pull the value from a previous task
    count = context["ti"].xcom_pull(task_ids="extract_data", key="record_count")
    print(f"Loading {count} records")

extract = PythonOperator(task_id="extract_data", python_callable=extract_func)
load    = PythonOperator(task_id="load_data",    python_callable=load_func)

extract >> load
```

> XCom is for **small values** (strings, numbers, short lists).
> For large data, use intermediate storage (S3, database, filesystem).

---

## 11. The Astronomer Platform (What's on Top of Airflow)

Astronomer adds enterprise features on top of open-source Airflow:

```
Open-source Airflow features:
  ✓ DAG scheduling and execution
  ✓ Web UI
  ✓ Variables and Connections
  ✓ Task logs

Astronomer adds:
  ✓ Managed infrastructure (no ops overhead)
  ✓ Workspaces and multi-environment management (dev / staging / prod)
  ✓ Built-in CI/CD (deploy DAGs via git push)
  ✓ Astronomer Alerts (Slack, PagerDuty, etc.)
  ✓ Deployment-level secret backends (Vault, AWS Secrets Manager)
  ✓ Astro CLI (local development tool)
  ✓ Astronomer Orbiter (migration tool from AutoSys → Airflow)
  ✓ RBAC (Role-Based Access Control) across teams
  ✓ Usage analytics and SLA monitoring dashboards
```

### Astronomer Environments

```
astro workspace                    ← your organisation's top-level container
   │
   ├── deployment: dev             ← dev environment (your sandbox)
   ├── deployment: staging         ← integration testing
   └── deployment: prod            ← production (live traffic)
```

Each **deployment** is a fully independent Airflow instance with its own scheduler,
workers, database, and connections.

---

## 12. Astro CLI — Your Local Development Tool

**Astro CLI** is how you develop and test DAGs locally. It spins up a full Airflow
environment in Docker on your laptop.

### 12.1 Install and Initialise

```bash
# Install Astro CLI (macOS)
brew install astro

# Create a new Astronomer project
mkdir my-dags && cd my-dags
astro dev init

# Start local Airflow (runs in Docker)
astro dev start

# Open UI at http://localhost:8080
# Username: admin  Password: admin
```

### 12.2 Project Structure

```
my-dags/
├── dags/                   ← put your DAG Python files here
│   └── example_dag.py
├── plugins/                ← custom operators, hooks
├── include/                ← SQL files, configs, helper scripts
├── requirements.txt        ← Python packages to install
├── packages.txt            ← OS packages (apt-get)
├── Dockerfile              ← custom Airflow image (usually unchanged)
└── .astro/
    └── config.yaml         ← project config
```

### 12.3 Common Astro CLI Commands

| Command | What it does |
|---|---|
| `astro dev init` | Create a new project scaffold |
| `astro dev start` | Start local Airflow (Docker) |
| `astro dev stop` | Stop local Airflow |
| `astro dev restart` | Restart (picks up DAG changes) |
| `astro dev logs` | View scheduler/worker logs |
| `astro dev ps` | List running containers |
| `astro deploy` | Deploy DAGs to Astronomer cloud |
| `astro deployment list` | List your cloud deployments |

---

## 13. Running Airflow Locally — Step by Step

```bash
# Step 1: Install Docker Desktop (required)
# https://www.docker.com/products/docker-desktop/

# Step 2: Install Astro CLI
brew install astro

# Step 3: Create your project
mkdir autosys-migration && cd autosys-migration
astro dev init

# Step 4: Add your first DAG
cat > dags/my_first_dag.py << 'EOF'
from datetime import datetime
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="my_first_dag",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
) as dag:
    hello = BashOperator(task_id="hello", bash_command="echo Hello Airflow!")
    world = BashOperator(task_id="world", bash_command="echo World!")
    hello >> world
EOF

# Step 5: Start Airflow
astro dev start

# Step 6: Open browser → http://localhost:8080
# Login: admin / admin
# You will see your DAG listed and can trigger it manually
```

---

## 14. Astronomer Orbiter — The Migration Tool

**Orbiter** is an Astronomer-provided CLI tool that **auto-converts** legacy scheduler
definitions (including AutoSys JIL) into working Python DAGs.

```bash
# Install Orbiter
pip install astronomer-orbiter

# Run Orbiter on your AutoSys JIL export
orbiter translate autosys --input all_jobs.jil --output ./dags/

# Output: Python DAG files in ./dags/ ready to run in Airflow
```

**What Orbiter handles well (60–80% of jobs):**
- Simple CMD jobs → `BashOperator`
- Linear `condition: success()` chains → `>>` dependencies
- Basic schedules (`start_times`, `days_of_week`) → `schedule`
- Global variables → Airflow Variables

**What Orbiter cannot handle (must be done manually):**
- `sendevent` triggers (no Airflow equivalent)
- Complex custom calendars
- `job_type: i5` or proprietary job types
- Business logic embedded in JIL

---

## 15. The DAG Factory Pattern — Scaling to Thousands of Jobs

Instead of writing 10,000 individual Python DAG files (one per AutoSys BOX),
the **DAG Factory** pattern uses a single generator that reads YAML config files.

```yaml
# dags/config/sales_daily.yaml
dag_id: sales_daily
schedule: "0 6 * * 1-5"
tasks:
  - id: extract
    type: bash
    command: /scripts/extract_sales.sh
  - id: transform
    type: bash
    command: /scripts/transform_sales.sh
    depends_on: [extract]
  - id: load
    type: bash
    command: /scripts/load_sales.sh
    depends_on: [transform]
```

```python
# dags/dag_factory.py  — ONE file that generates ALL DAGs
import yaml, os
from datetime import datetime
from airflow import DAG
from airflow.operators.bash import BashOperator

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")

for filename in os.listdir(CONFIG_DIR):
    if not filename.endswith(".yaml"):
        continue

    with open(os.path.join(CONFIG_DIR, filename)) as f:
        config = yaml.safe_load(f)

    with DAG(
        dag_id=config["dag_id"],
        start_date=datetime(2024, 1, 1),
        schedule=config["schedule"],
        catchup=False,
    ) as dag:
        task_map = {}
        for task_cfg in config["tasks"]:
            task = BashOperator(
                task_id=task_cfg["id"],
                bash_command=task_cfg["command"],
            )
            task_map[task_cfg["id"]] = task

        for task_cfg in config["tasks"]:
            for dep in task_cfg.get("depends_on", []):
                task_map[dep] >> task_map[task_cfg["id"]]

    globals()[config["dag_id"]] = dag
```

**Adding a new workflow = add a new YAML file. No Python needed.**
This is how you migrate thousands of AutoSys BOXes scalably.

---

## 16. Handling `sendevent` — The Big Gap

AutoSys `sendevent` = push-based trigger. No native Airflow equivalent.

### Option A: Airflow Datasets (Airflow 2.4+ — preferred for DAG-to-DAG)

```python
from airflow import Dataset

# Producer DAG — "produces" a dataset when a task completes
sales_dataset = Dataset("s3://data/sales/daily_complete")

with DAG("producer_dag", schedule="@daily") as dag:
    produce = BashOperator(
        task_id="run_etl",
        bash_command="/scripts/etl.sh",
        outlets=[sales_dataset],          # ← marks this dataset as updated
    )

# Consumer DAG — automatically triggered when the dataset is updated
with DAG("consumer_dag", schedule=[sales_dataset]) as dag:
    consume = BashOperator(
        task_id="run_report",
        bash_command="/scripts/report.sh",
    )
```

### Option B: REST API Trigger (for external systems)

```bash
# Any external system can trigger an Airflow DAG via REST API
curl -X POST \
  http://airflow-host:8080/api/v1/dags/my_dag/dagRuns \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d '{"conf": {"business_date": "2024-01-15"}}'
```

This replaces `sendevent -E STARTJOB -J job_name`.

### Option C: Event Bus (Kafka / SQS — for enterprise scale)

```
External system
      │
      ▼ publishes message
   Kafka Topic / SQS Queue
      │
      ▼ Airflow Sensor polls
   KafkaConsumeSensor / SqsSensor
      │
      ▼ triggers downstream tasks
   DAG continues
```

---

## 17. AutoSys → Airflow: Complete Concept Mapping

| AutoSys | Airflow / Astronomer | Notes |
|---|---|---|
| **JIL file** | Python DAG file | JIL is config; DAG is code |
| **BOX job** | DAG | 1:1 mapping |
| **CMD job** | `BashOperator` | Direct equivalent |
| **DB job** | `PostgresOperator` etc. | Provider packages |
| **FTP job** | `SFTPOperator` | Provider package |
| **OMTF job (file watcher)** | `FileSensor` / `S3KeySensor` | Direct equivalent |
| **`condition: success()`** | `task_a >> task_b` | Direct equivalent |
| **`condition: a & b`** | `[a, b] >> task_c` | Direct equivalent |
| **`sendevent`** | Datasets / REST API / Event bus | 🔴 Requires re-architecture |
| **Custom calendars** | Custom `Timetable` class | 🟡 Requires Python code |
| **`machine: server-X`** | `SSHOperator` or K8s node selector | 🟡 Paradigm shift |
| **Global variables (`%%VAR%%`)** | Airflow Variables (`{{ var.value.VAR }}`) | Direct equivalent |
| **Connection strings** | Airflow Connections | Direct equivalent |
| **`max_run_alarm`** | `sla=timedelta(minutes=N)` | 🟡 Less mature |
| **`n_retrys`** | `retries=N` in `default_args` | Direct equivalent |
| **`alarm_if_fail`** | `email_on_failure=True` | Direct equivalent |
| **`ON_HOLD`** | Pausing a DAG in the UI | Direct equivalent |
| **WCC (GUI)** | Airflow Web UI / Astronomer UI | Direct equivalent |
| **`autorep -J ALL -q`** | `airflow dags list` | Similar CLI |

---

## 18. The 10 Things to Remember About Astronomer/Airflow

| # | Key Concept |
|---|---|
| 1 | **DAG** = a Python file defining a workflow = AutoSys BOX. |
| 2 | **Task** = one unit of work inside a DAG = AutoSys Job. |
| 3 | **Operator** = defines what a task does (`Bash`, `Python`, `Postgres`, etc.). |
| 4 | **`>>`** = dependency operator. `a >> b` means "run b after a succeeds". |
| 5 | **Sensor** = a special task that waits for a condition (file, HTTP, DB, etc.). |
| 6 | **`schedule`** = cron or preset string defining when the DAG runs (Airflow 2.4+). |
| 7 | **Variable** = key-value store. Referenced as `{{ var.value.NAME }}` in templates. |
| 8 | **Connection** = stores credentials for DBs, APIs, SSH hosts. |
| 9 | **Datasets** = the modern way to trigger one DAG from another (replaces `sendevent`). |
| 10 | **Astro CLI** = `astro dev start` gives you a full local Airflow in Docker. |
