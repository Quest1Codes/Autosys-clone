# Contributing to autosys-clone

Thank you for taking the time to contribute. This document covers everything
you need to get from zero to a passing pull request.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Environment Setup](#environment-setup)
3. [Project Layout](#project-layout)
4. [Running Tests](#running-tests)
5. [Linting and Formatting](#linting-and-formatting)
6. [Making Changes](#making-changes)
7. [Pull Request Checklist](#pull-request-checklist)
8. [Branching Convention](#branching-convention)
9. [Commit Messages](#commit-messages)

---

## Prerequisites

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Python | 3.10 | 3.12+ recommended |
| pip | latest | bundled with Python |
| git | 2.x | |

---

## Environment Setup

```bash
# 1. Clone the repository
git clone <repo-url>
cd autosys-clone

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. Install the package in editable mode with dev dependencies
pip install -e ".[dev]"

# 4. Verify the CLI is available
autosys --help
```

For PostgreSQL support (optional):

```bash
pip install -e ".[dev,pg]"
```

---

## Project Layout

```
autosys-clone/
├── autosys/                # Main package
│   ├── models/             # Pydantic data models
│   ├── parser/             # JIL lexer + parser
│   ├── scheduler/          # Event processor, state machine, box orchestration
│   ├── agent/              # Local runner + remote TCP dispatch
│   ├── app_server/         # FastAPI REST API (SSA)
│   ├── wcc/                # Web dashboard (WCC)
│   ├── cli/                # Click-based CLI commands
│   ├── db/                 # SQLAlchemy schema, repository, migrations
│   └── notifications/      # Alarm manager + notification dispatcher
├── tests/                  # pytest suite (one file per phase)
├── jil_files/              # Sample JIL definitions (financial-domain workflows)
├── examples/               # Minimal runnable JIL examples
├── config/                 # Calendar definitions and runtime config
├── explain/                # Interactive HTML documentation viewer
├── pyproject.toml          # Build system, dependencies, tool configuration
└── README.md               # Full architecture and phase-by-phase reference
```

---

## Running Tests

```bash
# Run all tests
pytest

# Run a specific phase
pytest tests/test_phase1.py

# Run with coverage report
pytest --cov=autosys --cov-report=term-missing

# Run only fast (non-integration) tests
pytest -m "not integration"
```

Tests are written with `pytest-asyncio` and execute against an in-memory
SQLite database — no external services are required.

---

## Linting and Formatting

This project uses **ruff** for linting and **black** for formatting. Both are
included in the `[dev]` extra.

```bash
# Lint (check only)
ruff check autosys/ tests/

# Lint with auto-fix
ruff check --fix autosys/ tests/

# Format
black autosys/ tests/

# Format check only (useful in CI)
black --check autosys/ tests/
```

Line-length target is **100 characters** (configured in `pyproject.toml`).

---

## Making Changes

1. **Branch** off `main` (see [Branching Convention](#branching-convention)).
2. **Write a failing test** for the behaviour you intend to add or fix.
3. **Implement** the change, making the test pass.
4. **Check** that the full test suite still passes: `pytest`.
5. **Lint and format** your code before committing.
6. **Open a pull request** against `main`.

### Compatibility rules

- Do not change the status integer codes in `autosys/models/enums.py` —
  they must match the values documented in the CA WA AE manual.
- JIL parsing edge-cases must follow the behaviour described in
  `.agents/rules/autosys.md` and the reference PDF; do not silently "fix"
  quirks that AutoSys itself exhibits.
- SQL column names in `autosys/db/schema.py` use the `ujo_` prefix to mirror
  the real AutoSys schema — preserve this convention.

---

## Pull Request Checklist

Before requesting a review, confirm all of the following:

- [ ] `pytest` passes with no failures or errors
- [ ] `ruff check autosys/ tests/` reports no violations
- [ ] `black --check autosys/ tests/` reports no reformatting needed
- [ ] New functionality has accompanying tests
- [ ] Docstrings are added or updated for any public API changes
- [ ] `pyproject.toml` version is bumped if this is a releasable change

---

## Branching Convention

| Branch type | Pattern | Example |
|-------------|---------|---------|
| Feature | `feat/<short-description>` | `feat/calendar-engine` |
| Bug fix | `fix/<short-description>` | `fix/box-condition-evaluator` |
| Documentation | `docs/<short-description>` | `docs/jil-reference` |
| Refactor | `refactor/<short-description>` | `refactor/db-repository` |

---

## Commit Messages

Follow the [Conventional Commits](https://www.conventionalcommits.org/)
specification:

```
<type>(<scope>): <short summary>

[optional body]
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`.

**Examples:**

```
feat(parser): support override_job JIL subcommand
fix(scheduler): resolve race condition in box_manager activation
docs(contributing): add PostgreSQL setup instructions
test(phase7): cover box with nested condition failure path
```
