# Architecture Mapping — AutoSys (CA WA AE) vs Astronomer (Airflow)

A side-by-side translation of the **CA Workload Automation AE architecture**
(see `ae_arch.webp`) to its **Astronomer / Apache Airflow** equivalent.

The goal: show that the sprawling ~12-box AutoSys enterprise mesh collapses into
a much simpler, cloud-native Airflow architecture.

---

## 1. The Two Architectures Side by Side

### AutoSys (CA WA AE) — what the diagram shows

```
                          ┌─────────────────┐
   ┌──────────────┐       │  Service Desk   │  incident tickets (8080)
   │  WCC (Web UI)│       └─────────────────┘
   │ Apache Tomcat│       ┌─────────────────┐
   │ 8443 / 8080  │       │  NSM Event /    │  event + notification (CCI 1721)
   └──────┬───────┘       │  Notification   │
          │ SSA 7163      └─────────────────┘
          │               ┌─────────────────┐
   ┌──────▼───────┐       │  Spectrum AM    │  infra monitoring (443)
   │ Application  │       └─────────────────┘
   │ Server (SSA) │       ┌─────────────────┐
   └──────┬───────┘       │  EEM (security) │  RBAC / auth (5250)
          │ 7507          └─────────────────┘
   ┌──────▼───────┐
   │ Scheduler    │◄──── reads events / scans for start conditions
   │ (ACE) "brain"│
   └──────┬───────┘
          │ 7520 / 5250
   ┌──────▼───────┐       ┌─────────────────┐
   │ System Agent │◄─────►│ RDBMS           │  Event Server: all job defs + events
   │ (runs jobs)  │       │ (Event Server)  │
   └──────────────┘       └─────────────────┘
        +  CLI / JIL / autorep / API / SDK
```

### Astronomer (Airflow) — the equivalent

```
   ┌──────────────┐
   │ Astronomer / │  monitor + manage DAGs (replaces WCC)
   │ Airflow UI   │
   └──────┬───────┘
          │
   ┌──────▼───────┐       ┌─────────────────┐
   │ Airflow      │◄─────►│ Metadata DB     │  all DAG runs, task state, config
   │ Scheduler    │       │ (Postgres)      │  (replaces Event Server / RDBMS)
   │ "the brain"  │       └─────────────────┘
   └──────┬───────┘
          │ queues tasks
   ┌──────▼───────┐
   │ Workers      │  run the actual tasks (replaces System Agents)
   │ (K8s / Celery)│
   └──────────────┘
        +  astro CLI / Python DAGs / Airflow REST API
        +  Astronomer Alerts + Observe (replaces NSM/Spectrum/Service Desk)
        +  Astronomer RBAC + secret backends (replaces EEM)
```

---

## 2. Component-by-Component Mapping

| # | AutoSys Component | Role in AutoSys | Astronomer / Airflow Equivalent |
|---|---|---|---|
| 1 | **Scheduler (ACE)** | The brain. Scans Event Server, evaluates start conditions, dispatches jobs | **Airflow Scheduler** — parses DAGs, evaluates dependencies/schedules, queues tasks |
| 2 | **Application Server (SSA)** | Middleman between agents/clients and Event Server | **Airflow internal API / executor layer** (no separate box to manage on Astro) |
| 3 | **System Agent** | Runs jobs on target machines | **Workers** — Kubernetes pods (KubernetesExecutor) or Celery workers |
| 4 | **RDBMS / Event Server** | Stores ALL job/monitor/report definitions + events | **Metadata Database (Postgres)** — stores DAG runs, task instances, Variables, Connections |
| 5 | **WCC (Web UI / Tomcat)** | Web command center to monitor/administer jobs | **Airflow UI + Astronomer UI** |
| 6 | **CLI / JIL / autorep** | Define + manage + report on jobs | **`astro` CLI + Python DAG files + Airflow CLI** |
| 7 | **API / SDK / SSA** | Programmatic access | **Airflow REST API + Astronomer API** |
| 8 | **Web Services Consumer + Tomcat** | SOAP/REST external entry point | **Airflow REST API** (trigger DAGs, query state) |

---

## 3. Enterprise Integrations (the right-side boxes)

These are **separate CA/Broadcom products**, not core AutoSys. They collapse into
Astronomer's built-in features or standard Airflow providers.

| AutoSys Box | Color | Purpose | Astronomer / Airflow Replacement |
|---|---|---|---|
| **Service Desk** | teal | Auto-create incident tickets on failure | Airflow `on_failure_callback` → PagerDuty / ServiceNow / Opsgenie providers |
| **NSM Event Agent** | red | Event routing via CCI | Airflow **Datasets** / event-driven scheduling; Kafka/SQS providers |
| **NSM Notification Services** | red | Notification routing | **Astronomer Alerts** + Slack/email/PagerDuty providers |
| **Spectrum AM** | purple | Infrastructure monitoring | **Astronomer Observe** + lineage; Datadog/Prometheus providers |
| **EEM** | yellow | Security / entitlements / RBAC | **Astronomer RBAC** (Workspaces, Deployments, Teams) + secret backends |
| **CA CCI** | pink | Bridge to mainframe (CA 7 / z/OS) | Mainframe providers (e.g. `apache-airflow-providers-...`) or REST/SSH operators |

---

## 4. Protocol / Connection Mapping

| AutoSys Protocol (arrow) | Purpose | Airflow / Astronomer Equivalent |
|---|---|---|
| **TCP/IP** | Core component traffic | Internal Airflow gRPC/HTTP (managed by Astro) |
| **RDBMS Port** | DB queries to Event Server | SQLAlchemy connection to Postgres metadata DB |
| **SSA** (7163) | Secure CLI/SDK adapter | HTTPS to Airflow API / `astro` auth |
| **CCI** (1721) | Mainframe + NSM bridge | Provider packages / REST / message queues |
| **EEM** (5250) | Security traffic | OAuth / SSO via Astronomer |
| **SOAP** (9443) | Web services | Airflow REST API (JSON) |

---

## 5. The Big Picture

```
   AutoSys: ~12 boxes, 7 protocols, a dozen ports, multiple CA products
            ────────────────────────────────────────────────────────►
   Airflow: Scheduler + Workers + Metadata DB + UI
            (everything else = providers + Astronomer built-ins)
```

**Key takeaways for the migration:**

1. **3 core runtime pieces** in Airflow (Scheduler, Workers, Metadata DB) replace
   the AutoSys Scheduler + Application Server + Agents + Event Server.

2. **The enterprise integration mesh disappears** — NSM, Spectrum, Service Desk,
   EEM, and CCI-to-mainframe all become either Astronomer built-in features
   (alerts, observability, RBAC) or standard Airflow provider packages.

3. **No port/instance management** — On Astro you don't manage ACE instance IDs,
   dual Event Servers, Shadow Schedulers, or the dozen ports in the diagram.
   Astronomer manages the infrastructure; you manage **Python DAGs**.

4. **High Availability** — AutoSys needs a manually configured Shadow Scheduler
   + Dual Event Servers. Astro provides HA scheduler + managed Postgres out of
   the box.

5. **What you actually migrate** = the **job definitions** (JIL) → **DAGs**.
   Everything else (the surrounding architecture) is provided by the platform.
