# Orbiter Demo — Hands-On Walkthrough

A real, working demo of Orbiter converting AutoSys JIL → Airflow DAGs,
run locally on this machine. This shows exactly what Orbiter does and,
importantly, **its limitations** (the "functional but needs cleanup" reality).

---

## 1. Setup

Orbiter must run inside a Python virtual environment (macOS blocks system-wide
pip installs via PEP 668).

```bash
cd autosys-migration

# Create + activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Orbiter
pip install astronomer-orbiter

# Install the translation rulesets (interactive prompt)
orbiter install
# → choose: orbiter-community-translations
```

Verify:
```bash
orbiter --version            # orbiter, version 1.10.5
orbiter list-rulesets        # shows all supported origins incl. Autosys
```

---

## 2. The Input — Sample AutoSys JIL

File: `jil_input/demo_autosys_jobs.jil`

It contains a realistic ETL workflow:
- 1 **BOX** (`demo_etl_box`) — the workflow container
- 6 **jobs** with dependencies:
  ```
  check_source_ready → extract_sales → generate_report  ─┐
                                    → load_to_warehouse ─┴→ send_success_email
  ```
- 1 **standalone job** (`nightly_cleanup`) — no box, runs daily at 2 AM

It uses real AutoSys features: `condition: success()`, `box_name`, `%%DATE%%`
variables, `start_times`, `days_of_week`, `exclude_calendar`, `n_retrys`.

---

## 3. Running the Conversion

```bash
source venv/bin/activate

orbiter translate \
  --input-dir ./jil_input \
  --output-dir ./dags_from_orbiter \
  --ruleset orbiter_translations.autosys.jil_demo.translation_ruleset
```

Output:
```
[File 1] Translating file
...
Writing dags_from_orbiter/dags
Writing dags_from_orbiter/requirements.txt
Writing dags_from_orbiter/include/unmapped.py
Translation completed.
```

---

## 4. What Orbiter Generated

```
dags_from_orbiter/
├── dags/
│   ├── demo_etl_box.py        ← the BOX (NOT translated — see below)
│   ├── check_source_ready.py  ← BashOperator ✅
│   ├── extract_sales.py       ← BashOperator ✅
│   ├── generate_report.py     ← BashOperator ✅
│   ├── load_to_warehouse.py   ← BashOperator ✅
│   ├── send_success_email.py  ← BashOperator ✅
│   └── nightly_cleanup.py     ← BashOperator ✅
├── requirements.txt           ← apache-airflow, pendulum
└── include/
    └── unmapped.py            ← fallback operator for untranslated items
```

### Example of a clean conversion — `extract_sales.py`

```python
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="extract_sales",
    default_args={"owner": "svc_demo"},
    doc_md="**Created via Orbiter w/ Demo Translation Ruleset**...",
):
    extract_sales_task = BashOperator(
        task_id="extract_sales",
        bash_command="/scripts/extract_sales.sh --date %%DATE%%",
    )
```

**The JIL `command:` correctly became a `BashOperator`.** ✅

---

## 5. The Limitations This Demo Reveals (CRITICAL LEARNING)

This is the most valuable part of the demo — it shows *exactly* why Orbiter
output "needs significant rework before it's production-ready."

### ⚠️ Limitation 1: Each job became its OWN separate DAG

Orbiter's **demo ruleset** created **7 separate DAG files** — one per job.
But in AutoSys, these 6 jobs belonged to ONE box (`demo_etl_box`) and should
be **6 tasks inside ONE DAG**, not 6 separate DAGs.

```
What we wanted:                What the demo ruleset produced:
┌─────────────────────┐        ┌──────────────┐ ┌──────────────┐
│   demo_etl_box DAG  │        │ extract_sales│ │generate_report│
│  ┌────┐  ┌────┐     │        │     DAG      │ │     DAG      │
│  │task│→ │task│ ... │   vs   └──────────────┘ └──────────────┘
│  └────┘  └────┘     │        ┌──────────────┐ ┌──────────────┐
└─────────────────────┘        │load_to_wareh.│ │send_success..│
                               │     DAG      │ │     DAG      │
                               └──────────────┘ └──────────────┘
```

### ⚠️ Limitation 2: Dependencies were LOST

The warnings during translation said it all:
```
[DAG=send_success_email] Couldn't find task dependencies
[DAG=nightly_cleanup]    Couldn't find task dependencies
```

The `condition: success(generate_report) & success(load_to_warehouse)` logic
was **not converted** into Airflow `>>` dependencies. The execution order is gone.

### ⚠️ Limitation 3: The BOX itself didn't translate

`demo_etl_box.py` used a fallback `UnmappedOperator`:

```python
from include.unmapped import UnmappedOperator

with DAG(dag_id="demo_etl_box", ...):
    demo_etl_box_task = UnmappedOperator(
        task_id="demo_etl_box",
        doc_md="[task_type=UNKNOWN] Input did not translate",
        source="...job_type': 'BOX'...",
    )
```

`UnmappedOperator` is just an `EmptyOperator` (does nothing) — Orbiter is honestly
flagging "I couldn't handle this, a human needs to."

### ⚠️ Limitation 4: AutoSys variables not converted

`%%DATE%%` stayed as literal `%%DATE%%` in the bash command. It should become
Airflow's `{{ ds }}` template. This is left for manual fixing.

### ⚠️ Limitation 5: Scheduling attributes dropped

`start_times: "06:00"`, `days_of_week`, `exclude_calendar`, `n_retrys` — none of
these made it into the DAGs. No `schedule`, no `retries`. Manual work needed.

---

## 6. Demo Ruleset vs Base Ruleset

> **Important:** This demo used the **`jil_demo`** ruleset (free, community).
> The fuller **`jil_base`** ruleset lives in the **`astronomer-orbiter-translations`**
> repository, which is Astronomer's commercial/professional offering.
> The base ruleset handles boxes, dependencies, and more attributes far better.

| | `jil_demo` (free) | `jil_base` (commercial) |
|---|---|---|
| Simple CMD → BashOperator | ✅ | ✅ |
| BOX → single DAG with tasks | ❌ (one DAG per job) | ✅ |
| `condition:` → dependencies | ❌ | ✅ |
| Scheduling attributes | ❌ | ✅ (partial) |
| Variables `%%VAR%%` | ❌ | ✅ (partial) |

---

## 7. The Big Takeaway

This demo *proves* the point from the Otto/Orbiter guide:

> Orbiter is a **deterministic syntax converter**. It reliably maps the simple,
> mechanical parts (CMD → BashOperator) but **cannot reason** about workflow
> structure, dependencies, or your conventions. The output is a **starting point**
> that always needs human (or Otto AI) cleanup.

**The migration reality:**
```
Orbiter handles:   ~60-80% of the mechanical syntax (the easy part)
Human / Otto adds: the structure, dependencies, scheduling, conventions
                   (the hard part that actually makes it production-ready)
```

---

## 8. Commands Reference (to re-run this demo)

```bash
cd autosys-migration
source venv/bin/activate

# Re-run translation
orbiter translate \
  --input-dir ./jil_input \
  --output-dir ./dags_from_orbiter \
  --ruleset orbiter_translations.autosys.jil_demo.translation_ruleset

# Inspect output
find dags_from_orbiter -type f
cat dags_from_orbiter/dags/extract_sales.py
```
