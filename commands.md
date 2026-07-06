# AutoSys → Astronomer Migration

A comprehensive knowledge base and engineering toolkit for migrating enterprise
workloads from **Broadcom AutoSys Workload Automation AE** to
**Astronomer (Apache Airflow)**.

This repository contains:

- **Strategy documentation** — phased migration approach, gap analysis, and
  estimation models.
- **Technology deep-dives** — developer guides for AutoSys, Astronomer,
  Orbiter, and Otto.
- **`autosys-clone/`** — a full-fidelity Python simulation of AutoSys (JIL
  parser, scheduler, REST API, CLI, WCC dashboard) used as a sandboxed
  migration source.

---

## Repository Layout

```
autosys-to-astronomer/
├── README.md                       ← you are here
├── docs/
│   ├── strategy-and-approach.md   ← migration strategy, estimation model, gap analysis
│   ├── 01-autosys-guide.md        ← AutoSys architecture, JIL, CLI reference
│   ├── 02-astronomer-guide.md     ← Astronomer / Airflow architecture, DAG concepts
│   ├── 03-otto-and-orbiter-guide.md ← Orbiter (syntax converter) and Otto (AI agent)
│   ├── 04-orbiter-demo-walkthrough.md ← step-by-step Orbiter demo
│   └── 05-architecture-mapping.md ← side-by-side AutoSys vs Airflow component map
├── autosys-clone/                  ← Python project: AutoSys simulator
│   ├── autosys/                   ← main package (models, parser, scheduler, …)
│   ├── tests/                     ← pytest suite (665 tests)
│   ├── jil_files/                 ← sample JIL definitions (financial-domain workflows)
│   ├── examples/                  ← minimal runnable JIL examples
│   ├── pyproject.toml
│   └── README.md                  ← autosys-clone developer reference
└── ae_arch.webp                    ← CA WA AE architecture reference diagram
```

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Migration Strategy](docs/strategy-and-approach.md) | Executive strategy, phased approach, T-shirt sizing, effort estimation model |
| [AutoSys Developer Guide](docs/01-autosys-guide.md) | JIL syntax, job types, scheduler internals, CLI tools (`autorep`, `sendevent`, `jil`) |
| [Astronomer / Airflow Guide](docs/02-astronomer-guide.md) | DAG authoring, Operators, Sensors, Connections, Timetables, GitOps deployment |
| [Otto & Orbiter Guide](docs/03-otto-and-orbiter-guide.md) | Orbiter (JIL→DAG converter) and Otto (AI migration agent) |
| [Orbiter Demo Walkthrough](docs/04-orbiter-demo-walkthrough.md) | End-to-end demo: parse a JIL file, generate DAGs, deploy to Astronomer |
| [Architecture Mapping](docs/05-architecture-mapping.md) | Component-by-component mapping: AutoSys EPS/SSA/WCC → Airflow Scheduler/API/UI |

---

## The AutoSys Simulator (`autosys-clone/`)

The `autosys-clone/` directory contains a production-quality Python
implementation of AutoSys built for learning, testing, and serving as a
concrete migration source. It implements:

| Component | Implementation |
|-----------|----------------|
| JIL Parser | Lexer + parser for all core JIL subcommands and attributes |
| Scheduler (EPS/ACE) | Async event-processor with state machine, time triggers, box orchestration |
| System Agent | Local job runner + remote TCP dispatch |
| App Server (SSA) | FastAPI REST API on port 9000 |
| WCC Dashboard | Jinja2-based web UI on port 8080 |
| Alarm / NSM | Notification dispatcher with configurable channels |
| CLI | `autosys` command replicating `jil`, `autorep`, `sendevent`, `chase`, `autoping`, `autocal` |
| Database | SQLAlchemy + async SQLite (Postgres-ready) |

### How to Run

> **Prerequisites:** Python 3.10+, Node.js 18+, npm 9+

#### 1 — Install the Python package

```bash
cd autosys-clone

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

#### 2 — Start the App Server (REST API + Scheduler, port 9000)

```bash
# Runs the FastAPI REST API and the background Event Processor together
autosys scheduler serve --port 9000
```

Swagger docs → <http://localhost:9000/docs>

#### 3 — Start the WCC Dashboard (legacy UI, port 8080)

Open a second terminal (with the venv activated):

```bash
autosys scheduler wcc --port 8080
```

WCC job grid → <http://localhost:8080/>

#### 4 — Start the React Frontend (port 5173)

Open a third terminal:

```bash
cd autosys-clone/wcc-frontend
npm install          # only needed once
npm run dev
```

React WCC → <http://localhost:5173/>  
**Login:** `admin` / `admin`

#### 5 — Import the production-scale JIL files

With all servers running, import the full financial-domain workflow suite
(54 files, ~300 jobs, 45 machines, 5 calendars, 18 global variables):

```bash
# From the autosys-clone/ directory
python - <<'EOF'
import sys, os, json, urllib.request, subprocess

TOKEN = json.loads(subprocess.check_output([
    'curl', '-s', '-X', 'POST', 'http://localhost:9000/api/v1/auth/token',
    '-H', 'Content-Type: application/json',
    '-d', '{"username":"admin","password":"admin"}'
]).decode())['access_token']

HEADERS = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}
JIL_DIR = 'jil_files'

priority = ['00_machines.jil', '00_global_variables.jil', '51_us_market_holidays.jil']
ordered  = priority + sorted(f for f in os.listdir(JIL_DIR)
                             if f.endswith('.jil') and f not in priority)

for fname in ordered:
    content = open(os.path.join(JIL_DIR, fname)).read()
    req = urllib.request.Request(
        'http://localhost:9000/api/v1/jil/import',
        data=json.dumps({'content': content}).encode(),
        headers=HEADERS, method='POST'
    )
    r = json.loads(urllib.request.urlopen(req).read())
    counts = {}
    for j in r.get('jobs', []): counts[j['action']] = counts.get(j['action'], 0) + 1
    print(f"  {'OK' if r['success'] else 'ERR'}  {fname:<45} {counts}")
EOF
```

#### Port summary

| Service | Port | URL |
|---------|------|-----|
| App Server (REST API + Scheduler) | 9000 | <http://localhost:9000/docs> |
| WCC Dashboard (legacy) | 8080 | <http://localhost:8080/> |
| React Frontend | 5173 | <http://localhost:5173/> |

#### Run the test suite

```bash
cd autosys-clone
pytest tests/ -q
```

---

See [`autosys-clone/README.md`](autosys-clone/README.md) for the full
component reference, phase-by-phase build plan, CLI reference, and JIL
attribute dictionary.

---

## Migration Approach at a Glance

This project treats the AutoSys → Astronomer migration as a
**re-architecture, not a lift-and-shift**. The key insight is that AutoSys
JIL is imperative and event-driven, while Airflow is declarative and
DAG-based. Conversion tools (Orbiter) handle mechanical translation; complex
workflows require intentional re-design.

**Five phases:**

1. **Discover & Analyze** — inventory JIL artifacts, size by complexity (XS → XL)
2. **Plan & Design** — define DAG factory, event-bus strategy, CI/CD pipeline
3. **Build & Test (Pilot)** — migrate one medium-complexity workflow end-to-end
4. **Execute (Iterative)** — migrate in waves by business unit
5. **Decommission & Optimize** — validate parallel runs, cut over, retire AutoSys

See [docs/strategy-and-approach.md](docs/strategy-and-approach.md) for the
full estimation model and gap analysis heatmap.
