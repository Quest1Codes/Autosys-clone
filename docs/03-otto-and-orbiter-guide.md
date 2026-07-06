# Otto and Orbiter — Astronomer's Migration Tools

These are two **different tools** from Astronomer that both help with migration,
but they work at different levels and serve different purposes.

---

## The One-Line Summary

| Tool | What it is | Analogy |
|---|---|---|
| **Orbiter** | A CLI tool that **syntax-converts** JIL/XML → Python DAGs | A code translator |
| **Otto** | An **AI agent** that understands Airflow deeply and produces production-quality DAGs, debugs failures, and learns your team's conventions | A senior Airflow engineer in your terminal |

---

## 1. Orbiter

### What it is

Orbiter is an **open-source CLI tool** by Astronomer that mechanically converts
legacy scheduler job definitions into Apache Airflow Python DAG files.

- GitHub: https://github.com/astronomer/orbiter
- Install: `pip install astronomer-orbiter`

### What it supports

Orbiter can convert from multiple legacy schedulers:

| Source | Format it reads |
|---|---|
| **AutoSys** | `.jil` text files |
| Control-M | XML |
| Apache Oozie | XML |
| Luigi | Python |
| DAG Factory | YAML |
| CRON | crontab files |

### How to use it (AutoSys → Airflow)

```bash
# Install
pip install astronomer-orbiter

# Run it against your JIL export
orbiter translate autosys --input all_jobs.jil --output ./dags/

# Output: Python DAG files written to ./dags/
```

### What Orbiter does well ✅

- Simple `CMD` jobs → `BashOperator`
- Linear `condition: success()` chains → `>>` dependencies
- Basic schedules (`start_times`, `days_of_week`) → `schedule`
- Global variables → Airflow Variables
- File watcher jobs → `FileSensor`
- Handles 60–80% of XS/S/M sized jobs automatically

### What Orbiter cannot do ❌

- `sendevent` push triggers (no Airflow equivalent — needs re-architecture)
- Complex custom AutoSys calendars (needs custom Python `Timetable`)
- Proprietary job types (`i5`, `PeopleSoft`, `SAP`)
- Business logic embedded inside JIL (needs full re-engineering)
- It produces **working** DAGs but not always **idiomatic** or
  **production-quality** ones — the output often needs manual cleanup

### The key limitation of Orbiter (and why Otto exists)

> "A translation script converts syntax. Sometimes that output is good, more often
> it's technically functional but not idiomatic, not optimized for your provider
> versions, and not written the way an experienced Airflow engineer would write it.
> You end up with converted DAGs that need significant rework before they're
> production-ready."
> — Astronomer

Orbiter is a **syntax converter**. It doesn't know your Airflow version, your team's
conventions, your provider stack, or Airflow best practices. It just mechanically maps
JIL attributes to Airflow equivalents. The output works, but it carries technical debt.

---

## 2. Otto

### What it is

Otto is Astronomer's **AI-powered data engineering agent** — think of it as a
senior Airflow engineer living in your terminal. It is **not just a migration tool**;
it covers the entire data engineering lifecycle.

- Currently in **Labs** (controlled preview, free with usage limits on any Astro plan)
- Accessed via `astro otto` in the Astro CLI (v1.42+)

### What makes Otto different from Orbiter

```
Orbiter:
  JIL file → (syntax conversion) → Python DAG
  ↑ mechanical, rule-based, fast, no context

Otto:
  JIL file + your Airflow version + your provider stack
           + your team conventions + Airflow best practices
           → (AI reasoning) → production-ready Python DAG
  ↑ understands both source AND destination deeply
```

### How to use Otto

```bash
# Requires Astro CLI v1.42+
astro login           # authenticate with your Astro account

# Launch Otto in your project folder
cd autosys-migration
astro otto

# Otto opens an interactive terminal session.
# You can prompt it like:
# "Convert this AutoSys JIL file to Airflow DAGs"
# "Why did my DAG fail yesterday?"
# "Review my DAGs for Airflow 3.x compatibility"
```

### What Otto can do

#### 1. Migration (most relevant to your project)
- Reads AutoSys JIL, Control-M XML, Automic/UC4 definitions
- Converts them to production-quality Python DAGs
- Understands your target Airflow version and provider stack
- Maps complex dependencies — surfaces hidden complexity **before** it
  becomes a production problem
- Every output DAG traces back to its source job — easy validation
- Currently in **early access** for migrations (request at astronomer.io)

#### 2. DAG Authoring
- Write new DAGs by describing what you want in plain English
- Otto uses your team's conventions (stored in Otto Memory) automatically
- Validates code against your running Airflow version

#### 3. Failure Investigation
- When a DAG fails, Otto checks task logs + run history
- Diagnoses root cause (code error, connection failure, timeout, etc.)
- Produces: root cause type, severity, blast radius, suggested fix
- Can now do this **automatically** when a DAG fails — diagnosis is
  ready before your team is even paged

#### 4. Airflow Upgrades
- Scans your entire DAG fleet for version compatibility issues
- Produces a prioritized upgrade plan with specific code changes
- Turns a multi-sprint planning problem into a reviewable execution plan

### Otto Memory — the key differentiator

Otto stores your team's conventions in memory files:

```
~/.astro/otto/sessions/       ← session history (JSONL)
.astro/memory/                ← shared project memory (team conventions)
~/.astro/memory/              ← your personal memory
```

Over time it learns:
- How your team names DAGs
- Which operators you prefer
- Your retry policy conventions
- Your connection naming patterns
- Your Airflow version and provider stack

**This means Otto gets smarter with every session** — the institutional knowledge
built during migration doesn't disappear when the project ends.

### Where Otto runs

| Surface | How to access |
|---|---|
| **Terminal** | `astro otto` (available now) |
| **Astro IDE** | Browser-based IDE (available now) |
| **Desktop app** | Coming soon |
| **MCP** | Coming soon — will work inside Cursor, Claude Code, etc. |

### Otto model support

Otto supports models from **OpenAI, Anthropic, and Google**. You can switch
mid-session with `/model`.

---

## 3. How They Fit Into Your Migration Project

```
Phase 1: Discover & Analyze
  → You: run  autorep -J ALL -q > all_jobs.jil
  → You: parse + T-shirt size the JIL inventory

Phase 3 & 4: Build & Execute
  → XS / S / M jobs:  Run Orbiter first (fast, automated)
                       Then clean up output manually
  → L / XL jobs:      Use Otto (AI-assisted, production-quality)
                       or hand-write from scratch

Ongoing (after migration):
  → Use Otto for: DAG authoring, failure diagnosis, Airflow upgrades
  → Otto Memory builds up your team's conventions over time
```

### Practical recommendation

| Scenario | Use |
|---|---|
| Bulk-converting hundreds of simple CMD jobs quickly | **Orbiter** |
| Complex jobs with `sendevent`, calendars, custom logic | **Otto** (early access) |
| Writing new DAGs during migration | **Otto** |
| Debugging DAG failures in dev | **Otto** |
| Planning an Airflow version upgrade | **Otto** |

---

## 4. Key Differences Summary

| | **Orbiter** | **Otto** |
|---|---|---|
| **Type** | Open-source CLI tool | AI agent (Labs, Astro plan) |
| **How it works** | Rule-based syntax conversion | AI reasoning with deep Airflow context |
| **Output quality** | Functional but needs cleanup | Production-ready, idiomatic |
| **Knows your conventions** | No | Yes (via Otto Memory) |
| **Knows your Airflow version** | No | Yes |
| **Migration support** | AutoSys, Control-M, Oozie, etc. | AutoSys, Control-M, Automic, etc. |
| **Beyond migration** | No | Yes — authoring, debugging, upgrades |
| **Cost** | Free (open-source) | Free during Labs (with usage limits) |
| **Access** | `pip install astronomer-orbiter` | `astro otto` (Astro CLI v1.42+) |
| **Status** | Stable, released | Labs / Early Access |
