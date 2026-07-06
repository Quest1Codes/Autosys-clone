const codebaseData = [
    {
        "id": "autosys_cli",
        "order": 1,
        "title": "1. User Input (CLI)",
        "description": "Users submit jobs or trigger events using command-line tools.",
        "kid_title": "1. The Menu & Waiters (CLI)",
        "kid_description": "When you go to a restaurant, you speak to a waiter. These files are the Waiters. They take your order (JIL) and send it to the kitchen.",
        "icon": "ph-terminal",
        "files": [
            {
                "name": "agent_cmd.py",
                "type": "Python",
                "path": "autosys/cli/agent_cmd.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 400,
                "module_doc": "autosys agent \u2014 System Agent commands and job output inspection.\n\nCommands\n--------\nautosys agent start          Run the EPS with real subprocess dispatch.\nautosys agent status         Show the current agent state (active jobs).\n\nautosys jobs tail <job>      Print captured stdout/stderr of the last run.\nautosys jobs history <job>   Show run history with exit codes and durations.\n\nThe ``agent start`` command is the Phase 5 replacement for\n``scheduler start``.  It uses the real ``AgentDispatch`` dispatcher so jobs\nare launched as actual OS subprocesses.\n\nMachine filtering\n-----------------\nPhase 5 only dispatches jobs whose ``machine`` attribute resolves to the\nlocal hostname (or is empty/localhost).  Jobs targeting other machines are\nleft in STARTING state.  Phase 6 adds SSH-based remote dispatch.\n\nExample\n-------\n\\b\n    # Terminal 1 \u2014 import jobs and queue a STARTJOB event\n    $ autosys jil import examples/demo_etl.jil\n    $ autosys sendevent -E STARTJOB -J check_source_ready\n\n    # Terminal 2 \u2014 run the agent (processes the queued event)\n    $ autosys agent start\n    [agent] Running 'check_source_ready'  cmd='...'\n    ^C\n\n    # View output\n    $ autosys jobs tail check_source_ready\n    [1]  Source check passed.\n\n    # View history\n    $ autosys jobs history check_source_ready\n    Run ID    Start                End                  Duration  Status  Exit\n    abc123\u2026   2026-06-25 06:00:00  2026-06-25 06:00:02  2.1 s     SUCCESS   0",
                "classes": [],
                "functions": [
                    {
                        "name": "agent_group",
                        "doc": "System Agent commands (real subprocess dispatch)."
                    },
                    {
                        "name": "agent_start",
                        "doc": "Run the Event Processor with real subprocess dispatch.\n\nJobs whose machine attribute resolves to the current host will be\nexecuted as OS subprocesses.  stdout/stderr is captured and stored in\nthe ``job_output`` table.\n\nUnlike ``scheduler start`` (which uses a stub dispatcher), this command\nruns jobs for real.  Use it on the machine that will execute the jobs.\n\nExample\n-------\n\\b\n    $ autosys agent start\n    Agent started on localhost (poll: 1.0s)  Ctrl+C to stop"
                    },
                    {
                        "name": "agent_serve",
                        "doc": "Start the System Agent TCP server (Phase 6 remote dispatch).\n\nListens on HOST:PORT for dispatch requests from the Scheduler ACE.\nRegisters this machine in the shared DB so the Scheduler can route\njobs targeting this machine name to this server.\n\nRun this on each worker machine that will execute jobs.  The Scheduler\nand the agent must share the same AUTOSYS_DB_URL (e.g. PostgreSQL).\n\nExample\n-------\n\\b\n    # On etl-server-01:\n    $ AUTOSYS_DB_URL=postgresql://... autosys agent serve \\\n        --machine etl-server-01 --host 0.0.0.0 --port 7520\n\n    # On the scheduler host:\n    $ autosys machine check etl-server-01\n    UP  etl-server-01  0.0.0.0:7520  active_jobs=[]  uptime=1.2s"
                    },
                    {
                        "name": "agent_run_once",
                        "doc": "Process all pending events once with real subprocess dispatch, then exit.\n\nWaits up to WAIT seconds for all dispatched jobs to finish.\nUseful for integration tests and one-shot batch runs.\n\nExample\n-------\n\\b\n    $ autosys agent run-once\n    Processed 1 event(s).\n    Waiting for jobs to complete (max 30s)...\n    check_source_ready: SUCCESS (exit 0, 1.2s)"
                    },
                    {
                        "name": "jobs_group",
                        "doc": "Job output and run history commands."
                    },
                    {
                        "name": "jobs_tail",
                        "doc": "Print captured stdout/stderr output from the most recent run of JOB_NAME.\n\nBy default shows all output from the most recent run.  Use --run-id\nto view a specific historical run, or --lines to limit output.\n\nExample\n-------\n\\b\n    $ autosys jobs tail check_source_ready\n    Job: check_source_ready  run_id: abc123de...\n    [1]  Starting source check\u2026\n    [2]  /data/sales.csv  OK  (1.24 GB)\n    [3]  Check complete."
                    },
                    {
                        "name": "jobs_history",
                        "doc": "Show run history for JOB_NAME (newest first).\n\nDisplays run_id, start/end times, duration, final status, and exit code.\n\nExample\n-------\n\\b\n    $ autosys jobs history check_source_ready\n    Run ID    Started              Ended                Dur    Status   Exit\n    abc123\u2026   2026-06-25 06:00:00  2026-06-25 06:00:02  2.1s   SUCCESS     0\n    def456\u2026   2026-06-24 06:00:00  2026-06-24 06:00:01  1.4s   SUCCESS     0"
                    }
                ],
                "description": "autosys agent \u2014 System Agent commands and job output inspection."
            },
            {
                "name": "autoping_cmd.py",
                "type": "Python",
                "path": "autosys/cli/autoping_cmd.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 13,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [
                    {
                        "name": "autoping",
                        "doc": "Verify connectivity between server, agent, and client."
                    }
                ],
                "description": "Python source file (13 lines)"
            },
            {
                "name": "scheduler_cmd.py",
                "type": "Python",
                "path": "autosys/cli/scheduler_cmd.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 317,
                "module_doc": "autosys scheduler \u2014 run the Event Processor daemon.\n\nCommands\n--------\nautosys scheduler start      Run the EPS in the foreground (Ctrl+C to stop).\nautosys scheduler serve      Run the EPS + REST API server together.\nautosys scheduler run-once   Process all pending events exactly once and exit.\nautosys scheduler status     Show pending event count and INACTIVE job counts.\n\nThe ``start`` command runs ``EventProcessor.run_forever()`` in an asyncio\nevent loop.  For production use, wrap it in a systemd unit or supervisor.\n\nThe ``run-once`` command is useful for:\n  - Manual debugging (\"what events are pending right now?\")\n  - Cron-based scheduling (fire every minute, process queue, exit)\n  - Integration tests that want deterministic event processing\n\nExample\n-------\n\\b\n    $ autosys jil import examples/demo_etl.jil\n    $ autosys sendevent -E STARTJOB -J check_source_ready\n    $ autosys scheduler run-once\n      Processed 1 event(s).\n      check_source_ready: INACTIVE \u2192 STARTING \u2192 RUNNING \u2192 SUCCESS\n\n    $ autosys autorep -J %",
                "classes": [],
                "functions": [
                    {
                        "name": "scheduler_group",
                        "doc": "Event Processor daemon commands."
                    },
                    {
                        "name": "scheduler_start",
                        "doc": "Run the Event Processor in the foreground.\n\nPolls the event queue every POLL_INTERVAL seconds and drives the job\nstate machine.  Press Ctrl+C to stop.\n\nIn Phase 4 the dispatcher is a stub that transitions RUNNING \u2192 SUCCESS\nimmediately (no real subprocess is launched).  Phase 5 adds real System\nAgent dispatch.\n\nExample\n-------\n\\b\n    $ autosys scheduler start\n    Event Processor started (poll interval: 1.0s)\n    ^C  Stopped."
                    },
                    {
                        "name": "scheduler_run_once",
                        "doc": "Process all currently pending events exactly once, then exit.\n\nUseful for debugging, testing, or cron-based scheduling.\nThe jobs' final statuses are shown after processing.\n\nExample\n-------\n\\b\n    $ autosys scheduler run-once\n    Processing pending events...\n\n      STARTJOB  \u2192  check_source_ready     INACTIVE \u2192 SUCCESS\n      SET_GLOBAL \u2192 RUN_DATE = 20260625\n\n    1 event(s) processed."
                    },
                    {
                        "name": "scheduler_status",
                        "doc": "Show a summary of the scheduler's current state.\n\nDisplays the count of pending events and the distribution of job statuses.\n\nExample\n-------\n\\b\n    $ autosys scheduler status\n    Pending events:    2\n    Jobs by status:\n      INACTIVE    5\n      RUNNING     1\n      SUCCESS     1"
                    },
                    {
                        "name": "scheduler_serve",
                        "doc": "Run the Event Processor and REST API server together.\n\nThis starts uvicorn with the FastAPI app.  The EPS runs as a background\nasyncio task inside the same event loop as the API server.  All REST\nendpoints and the WebSocket live feed are available immediately.\n\nExample\n-------\n\\b\n    $ autosys scheduler serve --port 9000\n    AutoSys App Server starting on http://0.0.0.0:9000\n    Docs: http://localhost:9000/docs\n    ^C  Stopped."
                    },
                    {
                        "name": "scheduler_wcc",
                        "doc": "Start the WCC (Workload Control Centre) web dashboard on port 8080.\n\nThe WCC reads directly from the same SQLite database as the scheduler.\nIt provides a live job grid, D3 dependency graphs, alarm console, and\nrun history viewer.\n\nExample\n-------\n\\b\n    $ autosys scheduler wcc --port 8080\n    AutoSys WCC Dashboard starting on http://0.0.0.0:8080\n    Open: http://localhost:8080\n    ^C  Stopped."
                    }
                ],
                "description": "autosys scheduler \u2014 run the Event Processor daemon."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/cli/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 5,
                "module_doc": "autosys.cli \u2014 CLI layer re-exports.",
                "classes": [],
                "functions": [],
                "description": "autosys.cli \u2014 CLI layer re-exports."
            },
            {
                "name": "autocal_cmd.py",
                "type": "Python",
                "path": "autosys/cli/autocal_cmd.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 23,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [
                    {
                        "name": "autocal_group",
                        "doc": "Manage calendars."
                    },
                    {
                        "name": "list_calendars",
                        "doc": "List all calendars."
                    }
                ],
                "description": "Python source file (23 lines)"
            },
            {
                "name": "machine_cmd.py",
                "type": "Python",
                "path": "autosys/cli/machine_cmd.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 253,
                "module_doc": "autosys machine \u2014 System Agent registry management.\n\nCommands\n--------\nautosys machine register <name>   Register a remote agent in the DB.\nautosys machine list               Show all registered agents and status.\nautosys machine check <name>       Ping an agent, update its health status.\nautosys machine unregister <name>  Remove a machine from the registry.\n\nBackground\n----------\nBefore the Scheduler can dispatch a job to a remote machine, that machine\nmust be registered.  Registration stores the agent's ``host:port`` in the\n``machines`` table so the Scheduler can look it up at dispatch time.\n\nIn real AutoSys, machines are defined via JIL using the ``insert_machine:``\nsyntax:\n\n    insert_machine: etl-server-01\n        type: a\n        port: 7520\n\nWe expose the same operation as a CLI command.  The JIL ``insert_machine``\nsyntax is parsed in Phase 7 (when we extend the JIL parser for machine\ndefinitions).\n\nTypical workflow\n----------------\n\\b\n    # On the scheduler host \u2014 register the agent\n    $ autosys machine register etl-server-01 \\\n        --host 192.168.1.10 --port 7520\n\n    # On etl-server-01 \u2014 start the agent\n    $ AUTOSYS_DB_URL=... autosys agent serve \\\n        --machine etl-server-01 --port 7520\n\n    # On the scheduler host \u2014 verify it's alive\n    $ autosys machine check etl-server-01\n    etl-server-01  UP   192.168.1.10:7520   active_jobs=0   uptime=12.3s\n\n    # Import a JIL with machine: etl-server-01\n    $ autosys jil import my_jobs.jil\n\n    # Start the job \u2014 it now dispatches remotely!\n    $ autosys sendevent -E STARTJOB -J my_cmd_job\n    $ autosys agent run-once --wait 60",
                "classes": [],
                "functions": [
                    {
                        "name": "machine_group",
                        "doc": "System Agent registry commands."
                    },
                    {
                        "name": "machine_register",
                        "doc": "Register or update a System Agent in the machines table.\n\nIf MACHINE_NAME already exists, updates host, port, and description.\n\nExample\n-------\n\\b\n    $ autosys machine register etl-server-01 --host 10.0.1.10 --port 7520\n    Registered: etl-server-01  \u2192  10.0.1.10:7520"
                    },
                    {
                        "name": "machine_list",
                        "doc": "List all registered System Agents with their health status.\n\nExample\n-------\n\\b\n    $ autosys machine list\n    Machine          Status    Host               Port   Last Heartbeat\n    etl-server-01    UP        10.0.1.10          7520   2026-06-29 06:01\n    etl-server-02    UNKNOWN   10.0.1.11          7520   \u2014"
                    },
                    {
                        "name": "machine_check",
                        "doc": "Send a heartbeat ping to MACHINE_NAME and display its status.\n\nUpdates the machine's ``status`` and ``last_heartbeat`` in the DB.\nExits with code 1 if the machine is DOWN or not registered.\n\nExample\n-------\n\\b\n    $ autosys machine check etl-server-01\n    etl-server-01  UP   10.0.1.10:7520   active_jobs=[]   uptime=42.3s"
                    },
                    {
                        "name": "machine_unregister",
                        "doc": "Remove MACHINE_NAME from the registry.\n\nJobs whose ``machine:`` attribute matches this name will no longer be\ndispatched (they will stay in STARTING state)."
                    }
                ],
                "description": "autosys machine \u2014 System Agent registry management."
            },
            {
                "name": "chase_cmd.py",
                "type": "Python",
                "path": "autosys/cli/chase_cmd.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 47,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [
                    {
                        "name": "chase",
                        "doc": "Verify if STARTING/RUNNING jobs are actually alive on target machine."
                    }
                ],
                "description": "Python source file (47 lines)"
            },
            {
                "name": "main.py",
                "type": "Python",
                "path": "autosys/cli/main.py",
                "kid_desc": "The Walkie-Talkie Base connecting everyone together.",
                "lines": 109,
                "module_doc": "autosys CLI \u2014 root Click group and entry point.\n\nThis module is registered as the ``autosys`` console script in pyproject.toml::\n\n    [project.scripts]\n    autosys = \"autosys.cli.main:autosys\"\n\nRunning ``autosys --help`` shows all top-level commands.\n\nDB initialisation\n-----------------\nThe root group's ``@click.pass_context`` callback runs before every\nsubcommand.  It:\n\n1.  Reads ``--db`` (or ``$AUTOSYS_DB_URL``) to get the database URL.\n2.  Sets ``AUTOSYS_DB_URL`` in the environment so that connection.py picks\n    it up via its env-var default (no global state needed).\n3.  Calls ``create_all_sync(drop_first=False)`` to ensure the schema exists\n    (idempotent \u2014 safe to call on every invocation).\n\nThis means operators can use any SQLite file they want simply by passing\n``--db sqlite:///path/to/custom.db`` or setting the env var, without\ntouching any config file.",
                "classes": [],
                "functions": [
                    {
                        "name": "autosys",
                        "doc": "AutoSys-clone: a learning implementation of CA Workload Automation AE.\n\n\b\nQuick start:\n    autosys jil import examples/demo_etl.jil\n    autosys autorep -J %\n    autosys sendevent -E STARTJOB -J check_source_ready"
                    }
                ],
                "description": "autosys CLI \u2014 root Click group and entry point."
            },
            {
                "name": "autorep_cmd.py",
                "type": "Python",
                "path": "autosys/cli/autorep_cmd.py",
                "kid_desc": "Asking the waiter for a status update on your food.",
                "lines": 243,
                "module_doc": "autosys autorep \u2014 display job status report.\n\nMirrors the real AutoSys ``autorep -J`` command.  Reads the current job\nstatus snapshot from the ``jobs`` table and formats it as a table.\n\nUsage\n-----\n    autosys autorep -J job_name       Single job (exact name).\n    autosys autorep -J %              All jobs  (% = SQL wildcard).\n    autosys autorep -J etl%           Jobs whose name starts with 'etl'.\n    autosys autorep -J % -q           Quiet: machine-parseable TSV format.\n\nOutput\n------\nReal AutoSys output looks like::\n\n    Job Name                      Last Start         Last End           ST  Run\n    ----------------------------  -----------------  -----------------  --  ---\n    demo_etl_box                  ----------         ----------         IN    0\n      check_source_ready          ----------         ----------         IN    0\n      extract_sales               ----------         ----------         IN    0\n\nWhere:\n  Last Start / Last End  \u2014 timestamp of the last run, or ``----------``\n  ST                     \u2014 two-letter status code (see _STATUS_ABBREV)\n  Run                    \u2014 number of completed runs\n\nWe reproduce this format faithfully using rich.Table with a monospace style.",
                "classes": [],
                "functions": [
                    {
                        "name": "_fmt_ts",
                        "doc": "Format a datetime or return the blank placeholder."
                    },
                    {
                        "name": "_get_status_name",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_abbrev",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_status_colour",
                        "doc": "Return a rich colour tag for the status abbreviation."
                    },
                    {
                        "name": "autorep",
                        "doc": "Display a job status report.\n\nJOB_PATTERN is an exact job name or a SQL LIKE pattern\n(``%`` for all jobs, ``etl%`` for jobs starting with 'etl').\n\nExample\n-------\n\\b\n    $ autosys autorep -J %\n    $ autosys autorep -J demo_etl_box\n    $ autosys autorep -J etl% --no-children"
                    },
                    {
                        "name": "_print_table",
                        "doc": "Render jobs as a rich-formatted table mimicking real autorep output."
                    },
                    {
                        "name": "_print_tsv",
                        "doc": "Print tab-separated values for scripting (no rich markup)."
                    },
                    {
                        "name": "_run_count",
                        "doc": "Return the number of completed runs for a job row."
                    }
                ],
                "description": "autosys autorep \u2014 display job status report."
            },
            {
                "name": "box_cmd.py",
                "type": "Python",
                "path": "autosys/cli/box_cmd.py",
                "kid_desc": "A tiny worker robot helping out in the 1. The Menu & Waiters (CLI) department.",
                "lines": 208,
                "module_doc": "autosys box \u2014 Box job status and dependency tree commands.\n\nCommands\n--------\nautosys box status <box>   Show the box and all its children with status.\nautosys box tree   <box>   Print a dependency tree of children.\n\nBackground\n----------\nA BOX job is AutoSys's unit of workflow orchestration.  It groups related\nCMD jobs that share a common start trigger and have dependencies on each\nother.  The BOX itself starts when a ``STARTJOB`` event fires; children\nstart automatically when the box is RUNNING and their conditions are met.\n\nExample box hierarchy::\n\n    demo_etl_box            [BOX]   RUNNING\n    \u251c\u2500\u2500 check_source_ready  [CMD]   SUCCESS\n    \u251c\u2500\u2500 extract_sales       [CMD]   RUNNING  (condition: s(check_source_ready))\n    \u2514\u2500\u2500 load_to_warehouse   [CMD]   INACTIVE (condition: s(extract_sales))\n\n``autosys box status demo_etl_box`` renders this as a Rich table.\n``autosys box tree   demo_etl_box`` renders the tree view above.",
                "classes": [],
                "functions": [
                    {
                        "name": "box_group",
                        "doc": "Box job inspection commands."
                    },
                    {
                        "name": "box_status",
                        "doc": "Show the status of BOX_NAME and all its direct children.\n\nDisplays job name, type, status, condition, and machine in a table.\nUseful for quickly seeing which step of a workflow is blocked.\n\nExample\n-------\n\\b\n    $ autosys box status demo_etl_box\n\n    Box: demo_etl_box  [RUNNING]\n    \u250c\u2500 Job Name \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u252c\u2500 Type \u2500\u252c\u2500 Status \u2500\u2500\u252c\u2500 Condition \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n    \u2502 check_source_ready          \u2502 CMD    \u2502 SUCCESS   \u2502                             \u2502\n    \u2502 extract_sales               \u2502 CMD    \u2502 RUNNING   \u2502 s(check_source_ready)       \u2502\n    \u2502 load_to_warehouse           \u2502 CMD    \u2502 INACTIVE  \u2502 s(extract_sales)            \u2502\n    \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2534\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2534\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2534\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518"
                    },
                    {
                        "name": "box_tree",
                        "doc": "Print a dependency tree for BOX_NAME showing execution order.\n\nChildren are displayed in topological order: jobs with no dependencies\nfirst, then jobs whose conditions reference those jobs.  Each node\nshows status with colour.\n\nExample\n-------\n\\b\n    $ autosys box tree demo_etl_box\n    demo_etl_box [RUNNING]\n    \u251c\u2500\u2500 check_source_ready [SUCCESS]\n    \u251c\u2500\u2500 extract_sales [RUNNING]    (condition: s(check_source_ready))\n    \u2514\u2500\u2500 load_to_warehouse [INACTIVE]  (condition: s(extract_sales))"
                    },
                    {
                        "name": "_format_node",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_build_tree_data",
                        "doc": "Recursively build (row, children_data) for tree rendering."
                    },
                    {
                        "name": "_populate_tree",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "autosys box \u2014 Box job status and dependency tree commands."
            },
            {
                "name": "jil_cmd.py",
                "type": "Python",
                "path": "autosys/cli/jil_cmd.py",
                "kid_desc": "The waiter's notepad where you write down new jobs.",
                "lines": 271,
                "module_doc": "autosys jil \u2014 JIL file operations.\n\nCommands\n--------\nautosys jil import FILE      Parse a .jil file and persist jobs to the DB.\nautosys jil export JOB_NAME  Read a job from the DB and print JIL text.\nautosys jil validate FILE    Parse and validate a .jil file without persisting.",
                "classes": [],
                "functions": [
                    {
                        "name": "jil_group",
                        "doc": "JIL file operations: import, export, validate."
                    },
                    {
                        "name": "jil_import",
                        "doc": "Parse FILE and persist all insert_job / update_job stanzas to the DB.\n\nMirrors the real AutoSys ``jil < file.jil`` command.  Each stanza\nproduces one DB upsert; the operation that was performed (inserted /\nupdated) is shown for each job.\n\nWith --dry-run the file is parsed and validated but nothing is written\nto the database.\n\nExample\n-------\n\\b\n    $ autosys jil import examples/demo_etl.jil\n    Importing demo_etl.jil ...\n\n      INSERTED  demo_etl_box          BOX\n      INSERTED  check_source_ready    CMD\n      ...\n\n    7 jobs imported (7 inserted, 0 updated, 0 deleted)."
                    },
                    {
                        "name": "jil_export",
                        "doc": "Read a job from the database and print it as JIL text.\n\nJOB_NAME is the exact name of the job to export.  Use --all to export\nevery job in the database at once (JOB_NAME is then ignored).\n\nThe output is valid JIL that can be piped back into ``jil import``.\n\nExample\n-------\n\\b\n    $ autosys jil export demo_etl_box\n    insert_job: demo_etl_box   job_type: BOX\n    owner: svc_demo\n    start_times: \"06:00\"\n    ...\n\n    $ autosys jil export --all > all_jobs.jil"
                    },
                    {
                        "name": "jil_validate",
                        "doc": "Parse and validate FILE without writing to the database.\n\nIdentical to ``jil import --dry-run``.  Use this as a quick sanity-\ncheck before deploying a JIL file to production.\n\nExample\n-------\n\\b\n    $ autosys jil validate examples/demo_etl.jil\n      OK  demo_etl_box\n      OK  check_source_ready\n      ...\n    JIL file is valid: 7 jobs defined."
                    }
                ],
                "description": "autosys jil \u2014 JIL file operations."
            },
            {
                "name": "sendevent_cmd.py",
                "type": "Python",
                "path": "autosys/cli/sendevent_cmd.py",
                "kid_desc": "Yelling across the restaurant to cancel an order.",
                "lines": 159,
                "module_doc": "autosys sendevent \u2014 enqueue an event into the AutoSys event queue.\n\nThis command mirrors the real AutoSys ``sendevent`` utility.  It writes a\nrow to the ``event_queue`` table; the Event Processor daemon (Phase 4) picks\nit up and drives the job state machine.\n\nSupported event types\n---------------------\nSTARTJOB        Start a job immediately (respects its run window).\nFORCE_STARTJOB  Start a job even if its conditions aren't met.\nKILLJOB         Send SIGTERM to a running job's process.\nCHANGE_STATUS   Manually override a job's status in the database.\nSET_GLOBAL      Upsert a global variable value.\nON_HOLD         Put a job on hold (won't start until taken off hold).\nON_ICE          Freeze a job (removed from consideration entirely).\nOFF_HOLD        Remove a job from hold.\nOFF_ICE         Unfreeze a job.\nCOMMENT         Add an audit comment to the event history.\nREPLY_RESPONSE  Answer a manual intervention prompt (for WAIT_REPLY jobs).\nALARM           Raise an alarm on a job.\nRELEASE_RESOURCE Manually free up stuck virtual load-balancing resources.\n\nReal AutoSys usage\n------------------\n    $ sendevent -E STARTJOB        -J extract_sales\n    $ sendevent -E FORCE_STARTJOB  -J extract_sales\n    $ sendevent -E KILLJOB         -J extract_sales\n    $ sendevent -E CHANGE_STATUS   -J extract_sales -s INACTIVE\n    $ sendevent -E SET_GLOBAL      -G MY_DATE       -v 20260625\n    $ sendevent -E ON_HOLD         -J extract_sales\n    $ sendevent -E COMMENT         -J extract_sales -c \"User authorized\"",
                "classes": [],
                "functions": [
                    {
                        "name": "sendevent",
                        "doc": "Send an event to the AutoSys event queue.\n\nThe event is written to the database immediately.  The Event Processor\ndaemon (Phase 4) will pick it up on its next poll tick and drive the\njob state machine accordingly.\n\nExample\n-------\n\\b\n    $ autosys sendevent -E STARTJOB -J extract_sales\n    $ autosys sendevent -E SET_GLOBAL -G RUN_DATE -v 20260625\n    $ autosys sendevent -E CHANGE_STATUS -J nightly_cleanup -s INACTIVE"
                    }
                ],
                "description": "autosys sendevent \u2014 enqueue an event into the AutoSys event queue."
            }
        ]
    },
    {
        "id": "autosys_parser",
        "order": 2,
        "title": "2. JIL Parsing",
        "description": "Raw JIL text is tokenized and parsed into Python objects.",
        "kid_title": "2. Translating the Order (Parser)",
        "kid_description": "Sometimes customers speak a weird language. These robots translate your words into an official 'Order Ticket'.",
        "icon": "ph-code",
        "files": [
            {
                "name": "condition_parser.py",
                "type": "Python",
                "path": "autosys/parser/condition_parser.py",
                "kid_desc": "Translates special requests like 'Only cook this if the fries are done'.",
                "lines": 632,
                "module_doc": "AutoSys condition expression parser.\n\nWhat AutoSys conditions look like\n-----------------------------------\nThe ``condition`` attribute of a JIL job holds a boolean expression that the\nScheduler ACE evaluates *every time* a relevant job changes state.  If the\nexpression evaluates to True AND the job's scheduling window is open, the\nScheduler places a STARTJOB event on the queue.\n\nGrammar (EBNF)\n--------------\ncondition   := or_expr\nor_expr     := and_expr  ('|' and_expr)*\nand_expr    := primary   ('&' primary)*\nprimary     := '(' condition ')'\n             | job_func\n             | value_cond\njob_func    := func_name '(' job_ref ')'\nfunc_name   := 'success' | 'failure' | 'done' | 'notrunning'\n             | 'terminated' | 'activated'\nvalue_cond  := 'value' '(' global_name ')' ('=' | '!=') '\"' string '\"'\njob_ref     := identifier          # may contain letters, digits, _, ., -, :\nglobal_name := identifier          # typically UPPERCASE by convention\n\nOperator precedence (highest to lowest)\n-----------------------------------------\n1. Parentheses   (...)\n2. AND           &\n3. OR            |\n\nSo ``success(a) | success(b) & success(c)``\n   = ``success(a) | (success(b) & success(c))``\n\nPublic API\n----------\nparse_condition(expr: str)  -> ConditionNode   \u2014 build AST\nevaluate(node, statuses, globals) -> bool      \u2014 walk AST against live data\ncondition_to_str(node)      -> str             \u2014 pretty-print AST back to string\n\nAST node types\n--------------\nConditionNode   \u2014 abstract base\n  JobCondNode   \u2014 success(job), failure(job), \u2026\n  ValueCondNode \u2014 value(GLOBAL) = \"x\"\n  AndNode       \u2014 left & right\n  OrNode        \u2014 left | right\n  NotNode       \u2014 reserved for future !expr support",
                "classes": [
                    {
                        "name": "ConditionNode",
                        "doc": "Abstract base for all condition AST nodes.",
                        "methods": []
                    },
                    {
                        "name": "JobCondNode",
                        "doc": "A function predicate on a specific job's current status.\n\nfunc      What this predicate tests\n--------  -----------------------------------------------------------------\nsuccess   status == SUCCESS\nfailure   status == FAILURE\ndone      status in {SUCCESS, FAILURE}   (completed either way)\nnotrunning status not in {STARTING, RUNNING, RESTART}\nterminated status == TERMINATED\nactivated  status == ACTIVATED  (BOX jobs only)",
                        "methods": []
                    },
                    {
                        "name": "ExitCodeCondNode",
                        "doc": "Compares a job's last exit code to an integer.\n\nReal AutoSys example:\n    condition: exitcode(extract_sales) = 0",
                        "methods": []
                    },
                    {
                        "name": "ValueCondNode",
                        "doc": "Compares a global variable's current value to a literal string.\n\nReal AutoSys example:\n    condition: value(BATCH_DATE) = \"20260625\"",
                        "methods": []
                    },
                    {
                        "name": "AndNode",
                        "doc": "Left & Right \u2014 both sub-conditions must be True.",
                        "methods": []
                    },
                    {
                        "name": "OrNode",
                        "doc": "Left | Right \u2014 at least one sub-condition must be True.",
                        "methods": []
                    },
                    {
                        "name": "NotNode",
                        "doc": "Reserved for future !expr syntax (not in standard AutoSys but useful).",
                        "methods": []
                    },
                    {
                        "name": "_CondTokenKind",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "_CondToken",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "ConditionSyntaxError",
                        "doc": "Raised when the condition expression cannot be parsed.",
                        "methods": [
                            "__init__"
                        ]
                    },
                    {
                        "name": "_CondParser",
                        "doc": "Recursive-descent parser for the AutoSys condition mini-language.\n\nInstantiate with the token list from _tokenize_condition(), then call\n.parse() to get the root ConditionNode.",
                        "methods": [
                            "__init__",
                            "_peek",
                            "_consume",
                            "_at_end",
                            "parse",
                            "_parse_or",
                            "_parse_and",
                            "_parse_primary",
                            "_parse_grouped",
                            "_parse_job_func",
                            "_parse_value_cond",
                            "_parse_exitcode_cond",
                            "_parse_not"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_tokenize_condition",
                        "doc": "Convert a condition expression string to a flat list of tokens.\n\nWhitespace is consumed and discarded.  IDENT tokens whose value matches\na function name (success, failure, \u2026) are reclassified as FUNC; the\nkeyword \"value\" becomes VALUE."
                    },
                    {
                        "name": "parse_condition",
                        "doc": "Parse an AutoSys condition expression into an AST.\n\nParameters\n----------\nexpr:\n    The raw condition string from a JIL ``condition:`` attribute, e.g.\n    ``\"success(check_source_ready)\"``\n    ``\"success(generate_report) & success(load_to_warehouse)\"``\n    ``\"success(a) & (success(b) | failure(c))\"``\n    ``\"value(BATCH_DATE) = \\\"20260625\\\"\"``\n\nReturns\n-------\nConditionNode\n    Root of the condition AST.\n\nRaises\n------\nConditionSyntaxError\n    If the expression cannot be parsed."
                    },
                    {
                        "name": "evaluate",
                        "doc": "Walk the condition AST and return True if all conditions are satisfied.\n\nCalled by the Event Processor (Phase 4) whenever a relevant job changes\nstate, to decide whether to enqueue a STARTJOB event.\n\nParameters\n----------\nnode:\n    Root AST node returned by ``parse_condition()``.\njob_statuses:\n    Mapping of job_name \u2192 current status string (e.g. \"SUCCESS\").\n    Jobs not in the dict are treated as INACTIVE.\nglobal_vars:\n    Mapping of global variable name \u2192 current value.\n    Names are normalised to UPPERCASE before lookup.\njob_exitcodes:\n    Mapping of job_name \u2192 current exit code.\n    If an exitcode condition references a job with no exitcode, it will fail to match.\n\nExamples\n--------\n>>> node = parse_condition(\"success(extract_sales)\")\n>>> evaluate(node, {\"extract_sales\": \"SUCCESS\"})\nTrue\n>>> evaluate(node, {\"extract_sales\": \"RUNNING\"})\nFalse\n\n>>> and_node = parse_condition(\"success(a) & success(b)\")\n>>> evaluate(and_node, {\"a\": \"SUCCESS\", \"b\": \"SUCCESS\"})\nTrue\n>>> evaluate(and_node, {\"a\": \"SUCCESS\", \"b\": \"FAILURE\"})\nFalse"
                    },
                    {
                        "name": "condition_to_str",
                        "doc": "Render a condition AST back to a human-readable expression string.\n\nUseful for logging and the WCC dependency graph view (Phase 10).\n\nExamples\n--------\n>>> node = parse_condition(\"success(a) & (success(b) | failure(c))\")\n>>> condition_to_str(node)\n'success(a) & (success(b) | failure(c))'"
                    },
                    {
                        "name": "list_job_dependencies",
                        "doc": "Return a flat list of job names referenced in the condition.\n\nDuplicates are removed; order is depth-first left-to-right.\nUsed by the WCC flow graph builder to draw dependency edges.\n\nExamples\n--------\n>>> node = parse_condition(\"success(a) & (success(b) | success(a))\")\n>>> list_job_dependencies(node)\n['a', 'b']"
                    }
                ],
                "description": "AutoSys condition expression parser."
            },
            {
                "name": "variable_sub.py",
                "type": "Python",
                "path": "autosys/parser/variable_sub.py",
                "kid_desc": "A tiny worker robot helping out in the 2. Translating the Order (Parser) department.",
                "lines": 269,
                "module_doc": "AutoSys %%VAR%% variable substitution engine.\n\nHow it works in real AutoSys\n-----------------------------\nWhen the Scheduler ACE dispatches a CMD job it expands the command string\n*just before* handing it to the System Agent.  Every ``%%TOKEN%%`` pattern is\nreplaced with either a built-in runtime value or a user-defined global.\n\nBuilt-in (read-only, resolved at dispatch time \u2014 NOT stored in the DB)\n-----------------------------------------------------------------------\n%%DATE%%    MMDDYYYY  e.g. \"06252026\"  \u2014 the *scheduled* run date\n%%YYYY%%    4-digit year               e.g. \"2026\"\n%%MM%%      2-digit month (01-12)      e.g. \"06\"\n%%DD%%      2-digit day   (01-31)      e.g. \"25\"\n%%TIME%%    HHMM of the dispatch time  e.g. \"0602\"\n%%AUTORUN%% \"Y\" if started by the Scheduler, \"N\" if FORCE_STARTJOB\n\nUser-defined\n------------\nAny name not in the built-in set is looked up in the ``globals`` dict (loaded\nfrom the ``global_variables`` table).  Names are case-insensitive; they are\nnormalised to UPPERCASE before lookup, matching AutoSys's own behaviour.\n\nError policy\n------------\nBy default, referencing an undefined variable raises ``UndefinedVariableError``.\nPass ``strict=False`` to leave unknown tokens unexpanded (they stay as\n``%%VARNAME%%`` in the output).  This is useful for the JIL *parser* which\nprocesses templates before run-time globals are available.",
                "classes": [
                    {
                        "name": "UndefinedVariableError",
                        "doc": "Raised when a %%VAR%% token has no value in the given context.",
                        "methods": [
                            "__init__"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "build_builtins",
                        "doc": "Return a dict of the six AutoSys built-in variables for a given run.\n\nParameters\n----------\nrun_date:\n    The *logical* scheduled date of the run (not necessarily today \u2014\n    AutoSys separates \"what date is this job running for\" from \"what time\n    is it now\").  Defaults to today's date.\ndispatch_time:\n    Wall-clock time when the Scheduler dispatches the job.\n    Used for %%TIME%%.  Defaults to ``datetime.now()``.\nautorun:\n    True  \u2192 job was started by the Scheduler (normal schedule trigger).\n    False \u2192 job was started via FORCE_STARTJOB (manual override).\n    Maps to %%AUTORUN%% = \"Y\" / \"N\"."
                    },
                    {
                        "name": "substitute",
                        "doc": "Expand all ``%%VAR%%`` tokens in *template* and return the result.\n\nResolution order\n----------------\n1. Built-in variables (DATE, YYYY, MM, DD, TIME, AUTORUN)\n2. User-defined globals (from the ``globals`` dict, normalised to\n   UPPERCASE)\n3. If still not found:\n   - ``strict=True``  \u2192 raise ``UndefinedVariableError``\n   - ``strict=False`` \u2192 leave token unchanged\n\nParameters\n----------\ntemplate:\n    The raw JIL attribute value, e.g.\n    ``\"/scripts/extract.sh --date %%DATE%%\"``\nglobals:\n    Dict of user-defined global variable values keyed by UPPERCASE name.\n    Comes from the ``global_variables`` DB table (loaded by the\n    Scheduler before dispatching).\nrun_date:\n    Logical run date for %%DATE%%, %%YYYY%%, %%MM%%, %%DD%%.\ndispatch_time:\n    Wall-clock time for %%TIME%%.\nautorun:\n    Source flag for %%AUTORUN%%.\nstrict:\n    Whether to raise on missing variables (True) or leave them as-is.\n\nReturns\n-------\nstr\n    The template with all resolved tokens expanded.\n\nExamples\n--------\n>>> from datetime import date\n>>> substitute(\n...     \"/data/sales_%%DATE%%.csv\",\n...     run_date=date(2026, 6, 25),\n... )\n'/data/sales_06252026.csv'\n\n>>> substitute(\"Hello %%NAME%%\", globals={\"NAME\": \"World\"})\n'Hello World'\n\n>>> substitute(\"Hello %%MISSING%%\", strict=False)\n'Hello %%MISSING%%'"
                    },
                    {
                        "name": "substitute_job_attrs",
                        "doc": "Return a copy of *attrs* with ``%%VAR%%`` tokens expanded in every\nexpandable attribute.\n\nCalled by the Scheduler ACE (Phase 7) just before dispatching a job\nto the System Agent.  The original attrs dict is not mutated.\n\nParameters\n----------\nattrs:\n    Raw job attribute dict as produced by the JIL parser (or read from\n    the ``jobs`` DB table).\nglobals, run_date, dispatch_time, autorun, strict:\n    Forwarded to :func:`substitute`.\n\nReturns\n-------\ndict[str, str]\n    A new dict with the same keys but expanded values for expandable\n    attributes."
                    },
                    {
                        "name": "list_variables",
                        "doc": "Return the unique variable names referenced in *template*, in order of\nfirst appearance, normalised to UPPERCASE.\n\nUseful for static analysis of JIL files to detect missing globals before\njob submission.\n\nExample\n-------\n>>> list_variables(\"/scripts/load.sh --date %%DATE%% --env %%ENV%%\")\n['DATE', 'ENV']"
                    }
                ],
                "description": "AutoSys %%VAR%% variable substitution engine."
            },
            {
                "name": "jil_parser.py",
                "type": "Python",
                "path": "autosys/parser/jil_parser.py",
                "kid_desc": "The smarter robot that understands the grammar and writes the ticket.",
                "lines": 461,
                "module_doc": "AutoSys JIL Parser.\n\nConverts the flat token stream from :mod:`autosys.parser.lexer` into a list\nof :class:`JILOperation` objects, each of which wraps a validated Pydantic\n:class:`~autosys.models.job.Job` model (or an Event / GlobalVariable).\n\nPipeline\n--------\n::\n\n    JIL text\n      \u2502\n      \u25bc  strip_comments + Lexer.tokenize()\n    Token stream\n      \u2502\n      \u25bc  JILParser.parse()\n    list[JILOperation]\n      \u2502\n      \u25bc  each op.job  is a validated CmdJob / BoxJob / \u2026 Pydantic model\n\nOperations recognised\n----------------------\ninsert_job   \u2192 JILOperation(op=\"insert\",   job=<Job>)\nupdate_job   \u2192 JILOperation(op=\"update\",   job=<Job>)\ndelete_job   \u2192 JILOperation(op=\"delete\",   job=<Job>)  # partial \u2014 name only\noverride_job \u2192 JILOperation(op=\"override\", job=<Job>)\n\nHow attribute coercion works\n-----------------------------\nRaw JIL values are always strings.  Before handing them to Pydantic, the\nparser coerces them to the right Python types based on the attribute name:\n\n- Boolean flags (``alarm_if_fail``, ``box_terminator``, \u2026)  \u2192  bool\n- Integer attributes (``n_retrys``, ``max_run_alarm``, \u2026)   \u2192  int\n- Everything else                                            \u2192  str\n\nPydantic handles ``start_times`` and ``days_of_week`` normalisation\n(comma-split, quote-strip, \"all\" expansion) via its own validators.",
                "classes": [
                    {
                        "name": "JILOperation",
                        "doc": "A single parsed JIL operation \u2014 either a job or a machine definition.\n\nAttributes\n----------\nop:\n    For jobs: ``\"insert\"``, ``\"update\"``, ``\"delete\"``, ``\"override\"``.\n    For machines: ``\"insert_machine\"``.\njob:\n    The validated Pydantic Job model (CmdJob, BoxJob, etc.).\n    ``None`` for machine operations \u2014 use ``machine`` instead.\nmachine:\n    The validated MachineDef model.  Set only when ``op == \"insert_machine\"``.\nraw_attrs:\n    The un-coerced key:value dict exactly as the lexer produced it.\nsource_line:\n    The 1-based source line where this stanza started.",
                        "methods": []
                    },
                    {
                        "name": "JILParseError",
                        "doc": "Raised when the parser encounters unexpected tokens or invalid JIL.",
                        "methods": [
                            "__init__"
                        ]
                    },
                    {
                        "name": "JILParser",
                        "doc": "Recursive-descent parser that converts a JIL token stream into\n:class:`JILOperation` objects.\n\nUsage\n-----\n::\n\n    from autosys.parser.jil_parser import JILParser\n\n    ops = JILParser().parse_text(open(\"jobs.jil\").read())\n    for op in ops:\n        print(op.op, op.job.job_name, op.job.job_type)",
                        "methods": [
                            "parse_text",
                            "parse",
                            "_parse_stanza",
                            "_peek",
                            "_at_end",
                            "_consume"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_coerce",
                        "doc": "Convert a raw JIL attribute value string to the right Python type.\n\nBoolean conversion:  \"1\", \"true\", \"yes\" (case-insensitive) \u2192 True\n                     anything else                          \u2192 False\nInteger conversion:  if int() succeeds use it, else keep the string\n                     (guards against malformed data)\nEverything else:     return as-is (a plain str)\n\nThe result is then handed to Pydantic's ``model_validate()``, so the\nvalidator in :class:`~autosys.models.job.Job` will further normalise\nvalues like ``start_times`` and ``days_of_week``."
                    },
                    {
                        "name": "parse_jil",
                        "doc": "Parse JIL *text* and return a list of :class:`JILOperation`.\n\nConvenience wrapper around ``JILParser().parse_text(text)``.\n\nExamples\n--------\n>>> ops = parse_jil('''\n... insert_job: my_box   job_type: BOX\n... owner: svc_demo\n... start_times: \"06:00\"\n... days_of_week: mo,tu,we,th,fr\n... ''')\n>>> len(ops)\n1\n>>> ops[0].op\n'insert'\n>>> ops[0].job.job_name\n'my_box'\n>>> ops[0].job.start_times\n['06:00']"
                    },
                    {
                        "name": "parse_jil_file",
                        "doc": "Read a JIL file from *path* and parse it.\n\nParameters\n----------\npath:\n    File system path to a ``.jil`` file.\n\nReturns\n-------\nlist[JILOperation]"
                    },
                    {
                        "name": "jobs_from_jil",
                        "doc": "Return just the :class:`~autosys.models.job.Job` models from a JIL text.\n\nConvenience helper for tests and the CLI.\n\n>>> jobs = jobs_from_jil('''\n... insert_job: cleanup   job_type: CMD\n... command: /scripts/cleanup.sh\n... machine: localhost\n... ''')\n>>> jobs[0].job_name\n'cleanup'\n>>> type(jobs[0]).__name__\n'CmdJob'"
                    }
                ],
                "description": "AutoSys JIL Parser."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/parser/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 2. Translating the Order (Parser) department.",
                "lines": 68,
                "module_doc": "autosys.parser \u2014 JIL parsing pipeline.\n\nImport surface\n--------------\n    from autosys.parser import parse_jil, parse_jil_file, jobs_from_jil\n    from autosys.parser import parse_condition, evaluate, condition_to_str\n    from autosys.parser import substitute, build_builtins\n    from autosys.parser import tokenize, Lexer, Token, TokenKind",
                "classes": [],
                "functions": [],
                "description": "autosys.parser \u2014 JIL parsing pipeline."
            },
            {
                "name": "jil_writer.py",
                "type": "Python",
                "path": "autosys/parser/jil_writer.py",
                "kid_desc": "A tiny worker robot helping out in the 2. Translating the Order (Parser) department.",
                "lines": 323,
                "module_doc": "JIL serializer \u2014 converts Pydantic Job models back to JIL text.\n\nUsed by:\n  autosys jil export <job_name>   \u2014 dump one job from the DB as JIL\n  autosys jil export --all        \u2014 dump all jobs (round-trip of import)\n\nOutput follows the exact format that AutoSys's own ``jil`` command produces\nwhen you run ``jil < /dev/null`` on a real system, so the output can be\nreimported unchanged.\n\nFormat rules\n-------------\n1.  Stanza header:   ``insert_job: NAME   job_type: TYPE``\n2.  Each set attribute on its own line:  ``attr_name: value``\n3.  Values containing spaces or special characters are double-quoted.\n4.  Boolean attributes use ``1`` / ``0``  (not true/false).\n5.  List attributes (days_of_week, start_times) are comma-joined.\n6.  None values and default-value attributes are omitted to keep the\n    output concise.\n7.  Stanzas are separated by a blank line.\n\nAttribute emit order\n---------------------\nFollows the AutoSys documentation order: identity \u2192 execution \u2192 BOX \u2192\nscheduling \u2192 dependencies \u2192 reliability \u2192 notifications \u2192 type-specific.",
                "classes": [],
                "functions": [
                    {
                        "name": "_needs_quoting",
                        "doc": "Return True if *value* must be wrapped in double-quotes in JIL output.\n\nAutoSys quotes values that contain:\n- Spaces or tabs  (common in commands and file paths)\n- Colons          (time values like \"06:00\")\n- Forward slashes (file paths \u2014 actually AutoSys doesn't quote these,\n  but quoting is harmless and safer)\n\nSingle-word values like \"CMD\", \"svc_demo\", \"etl-server-01\" are left\nunquoted."
                    },
                    {
                        "name": "_format_value",
                        "doc": "Convert a Python value to a JIL-compatible string.\n\nReturns None if the attribute should be skipped (default / empty)."
                    },
                    {
                        "name": "job_to_jil",
                        "doc": "Serialize a Pydantic Job model to a JIL stanza string.\n\nParameters\n----------\njob:\n    Any validated Pydantic Job subclass.\nop:\n    The JIL directive: ``\"insert\"``, ``\"update\"``, ``\"delete\"``,\n    ``\"override\"``.  Defaults to ``\"insert\"``.\n\nReturns\n-------\nstr\n    A complete JIL stanza with a trailing newline.\n    Example::\n\n        insert_job: demo_etl_box   job_type: BOX\n        owner: svc_demo\n        start_times: \"06:00\"\n        days_of_week: mo,tu,we,th,fr\n        exclude_calendar: us_holidays\n        alarm_if_fail: 1\n        max_run_alarm: 120"
                    },
                    {
                        "name": "jobs_to_jil",
                        "doc": "Serialize multiple Job models to a complete JIL file string.\n\nParameters\n----------\njobs:\n    List of Job models (typically all returned by JobRepository.list_all).\nop:\n    Directive for all jobs.  Defaults to ``\"insert\"``.\nheader_comment:\n    Optional ``/* ... */`` comment prepended to the file.\n\nReturns\n-------\nstr\n    Full JIL file text, stanzas separated by blank lines.\n\nExample\n-------\n>>> text = jobs_to_jil([box_job, cmd_job])\n>>> print(text)\ninsert_job: my_box   job_type: BOX\nowner: svc_demo\n\ninsert_job: my_cmd   job_type: CMD\ncommand: /scripts/run.sh\nmachine: etl-server-01"
                    },
                    {
                        "name": "machine_to_jil",
                        "doc": "Serialize a ``MachineRow`` (or any object with machine attrs) to JIL.\n\nOutput format::\n\n    insert_machine: etl-server-01\n        type: a\n        host: 192.168.1.10\n        port: 7520\n        max_load: 100\n\nParameters\n----------\nmachine_row:\n    A ``MachineRow`` DB row or a ``MachineDef`` Pydantic model.\n\nReturns\n-------\nstr\n    A single ``insert_machine:`` stanza."
                    }
                ],
                "description": "JIL serializer \u2014 converts Pydantic Job models back to JIL text."
            },
            {
                "name": "lexer.py",
                "type": "Python",
                "path": "autosys/parser/lexer.py",
                "kid_desc": "The tiny robot that chops your sentence into individual words.",
                "lines": 371,
                "module_doc": "AutoSys JIL (Job Information Language) Lexer.\n\nWhat JIL looks like\n--------------------\nJIL is a flat attribute-value language.  A *stanza* begins with a directive\n(``insert_job``, ``update_job``, etc.) and consists of attribute lines until\nthe next directive or end-of-file.\n\n::\n\n    /* block comment \u2014 may span multiple lines */\n\n    insert_job: extract_sales   job_type: CMD       \u2190 stanza header\n    box_name: demo_etl_box                          \u2190 attribute line\n    command: /scripts/extract.sh --date %%DATE%%    \u2190 value contains spaces\n    machine: etl-server-01\n    condition: success(check_source_ready)          \u2190 complex value\n    n_retrys: 2\n    alarm_if_fail: 1\n\nDesign decisions\n----------------\nTwo kinds of lines require different tokenisation:\n\n1. **Stanza header line** (starts with a directive keyword):\n   ``insert_job: NAME   job_type: CMD``\n   \u2014 Multiple key:value pairs on one line.  Values are single tokens\n     (no spaces).  The lexer emits DIRECTIVE + JOB_NAME + (ATTR_NAME + VALUE)*.\n\n2. **Attribute line** (starts with a regular key name):\n   ``command: /scripts/extract.sh --date %%DATE%%``\n   \u2014 One key per line; the value is everything after the colon (trimmed).\n     It may contain spaces, operators, quotes \u2014 anything.\n     The lexer emits ATTR_NAME + VALUE (a LINE_VALUE covering the whole rest\n     of the line).\n\nThis distinction mirrors how real AutoSys parses JIL.\n\nToken stream example for the stanza above\n-------------------------------------------\nDIRECTIVE  \"insert_job\"\nJOB_NAME   \"extract_sales\"\nATTR_NAME  \"job_type\"         \u2190 inline attribute on the header line\nVALUE      \"CMD\"              \u2190 INLINE_VALUE\nATTR_NAME  \"box_name\"\nVALUE      \"demo_etl_box\"     \u2190 LINE_VALUE\nATTR_NAME  \"command\"\nVALUE      \"/scripts/extract.sh --date %%DATE%%\"\nATTR_NAME  \"machine\"\nVALUE      \"etl-server-01\"\nATTR_NAME  \"condition\"\nVALUE      \"success(check_source_ready)\"\nATTR_NAME  \"n_retrys\"\nVALUE      \"2\"\nATTR_NAME  \"alarm_if_fail\"\nVALUE      \"1\"\nEOF",
                "classes": [
                    {
                        "name": "TokenKind",
                        "doc": "All token kinds produced by the JIL lexer.",
                        "methods": []
                    },
                    {
                        "name": "Token",
                        "doc": "A single JIL token with its source location.\n\n``line`` is 1-based and refers to the *original* source line\n(before comment removal).",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "LexError",
                        "doc": "Raised when a line cannot be tokenised.",
                        "methods": [
                            "__init__"
                        ]
                    },
                    {
                        "name": "Lexer",
                        "doc": "Tokenizes AutoSys JIL text into a flat list of :class:`Token` objects.\n\nUsage\n-----\n::\n\n    tokens = Lexer().tokenize(jil_text)\n    for tok in tokens:\n        print(tok)\n\nThe returned list always ends with an ``EOF`` token.",
                        "methods": [
                            "tokenize",
                            "_tokenize_line",
                            "_tokenize_stanza_header",
                            "_tokenize_attr_line"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "strip_comments",
                        "doc": "Remove ``/* ... */`` block comments from JIL text.\n\nNewlines *inside* comments are replaced with actual newlines so that\nall subsequent line numbers remain accurate.  All other comment text\nis replaced with spaces (so multi-token lines don't accidentally merge).\n\nExamples\n--------\n>>> strip_comments(\"/* top comment */\\ninsert_job: a   job_type: BOX\")\n'                 \\ninsert_job: a   job_type: BOX'\n\n>>> strip_comments(\"a: 1  /* inline */ b: 2\")\n'a: 1            b: 2'"
                    },
                    {
                        "name": "_strip_quotes",
                        "doc": "Remove surrounding double-quotes from a quoted value string."
                    },
                    {
                        "name": "tokenize",
                        "doc": "Tokenize JIL *text* \u2014 convenience wrapper around :class:`Lexer`.\n\n>>> tokens = tokenize('insert_job: my_job   job_type: CMD\\ncommand: echo hi')\n>>> [t.kind.value for t in tokens if t.kind != TokenKind.EOF]\n['DIRECTIVE', 'JOB_NAME', 'ATTR_NAME', 'VALUE', 'ATTR_NAME', 'VALUE']"
                    }
                ],
                "description": "AutoSys JIL (Job Information Language) Lexer."
            }
        ]
    },
    {
        "id": "autosys_models",
        "order": 3,
        "title": "3. Data Models",
        "description": "Core data structures representing jobs, machines, and events.",
        "kid_title": "3. The Ticket Shapes (Models)",
        "kid_description": "These files define exactly what a blank Order Ticket or Sticky Note looks like.",
        "icon": "ph-cube",
        "files": [
            {
                "name": "event.py",
                "type": "Python",
                "path": "autosys/models/event.py",
                "kid_desc": "The sticky notes slapped on tickets when statuses change (Cooking -> Done).",
                "lines": 523,
                "module_doc": "Pydantic models for the AutoSys Event Queue (EPS queue).\n\nBackground\n----------\nIn real AutoSys, almost every action that changes a job's state is driven by\nan **event**.  Operators issue events via ``sendevent`` on the command line;\nthe Scheduler ACE generates time-triggered events internally; System Agents\nemit completion events when subprocesses exit.  All of these flow through a\nsingle FIFO queue \u2014 the **Event Processor Service (EPS)** queue \u2014 which is\nconsumed, in strict order, by the EPS loop inside\n``scheduler_ace/event_processor.py``.\n\nThis module defines two models:\n\n``Event``\n    Represents one row in the live ``event_queue`` table.  The EPS reads\n    unprocessed events (``processed=False``) in ``created_at`` order,\n    transitions job states, and marks events as ``processed=True``.\n\n``EventHistory``\n    An append-only audit log entry derived from ``Event``.  Written to the\n    ``event_history`` table after the EPS processes each event.  Includes\n    an extra ``metadata_json`` blob for contextual data that is too\n    variable-shaped to normalise into columns (e.g. the previous status\n    before a CHANGE_STATUS event).\n\nEPS processing order\n~~~~~~~~~~~~~~~~~~~~\nThe EPS processes events **strictly in ``created_at`` ascending order**.\nThis ensures that, for example, a HOLD_JOB event that arrives before a\nSTARTJOB event is applied first \u2014 preserving the causal order an operator\nintended.  Within the same microsecond, ``event_id`` (UUID) provides a\ndeterministic tiebreaker (string-sorted).\n\nEvent lifecycle\n~~~~~~~~~~~~~~~\n1. Any component (CLI, REST API, Scheduler, Agent, internal retry logic)\n   creates an ``Event`` and inserts it into ``event_queue``.\n2. The EPS polling loop reads ``SELECT \u2026 WHERE processed=False ORDER BY\n   created_at ASC LIMIT <batch>``.\n3. For each event, the EPS calls the appropriate handler in the state machine,\n   updates job status, and sets ``processed=True`` + ``processed_at=<now>``.\n4. The EPS copies the completed event into ``event_history`` with any extra\n   audit context in ``metadata_json``, then deletes (or archives) the row\n   from ``event_queue``.\n\nCross-field validation\n~~~~~~~~~~~~~~~~~~~~~~\nNot every field is applicable to every event type.  The ``@model_validator``\nbelow enforces the same rules AutoSys enforces when you run ``sendevent``:\nmissing required fields surface as a ``ValueError`` with a clear message\nrather than silently being ignored or causing a cryptic downstream failure.",
                "classes": [
                    {
                        "name": "Event",
                        "doc": "A single event on the AutoSys EPS (Event Processor Service) queue.\n\nEvents are the *only* mechanism by which job states are changed.\nNo component may write directly to the job status table; instead it\nposts an ``Event`` and waits for the EPS to process it.  This design\nguarantees that all state transitions are serialised, auditable, and\nreproducible.\n\nReal AutoSys equivalents\n~~~~~~~~~~~~~~~~~~~~~~~~\n* ``sendevent -E STARTJOB -J <job>``            \u2192  EventType.STARTJOB\n* ``sendevent -E FORCE_STARTJOB -J <job>``      \u2192  EventType.FORCE_STARTJOB\n* ``sendevent -E KILLJOB -J <job>``             \u2192  EventType.KILLJOB\n* ``sendevent -E CHANGE_STATUS -J <job> -s <s>``\u2192  EventType.CHANGE_STATUS\n* ``sendevent -E SET_GLOBAL -G <name>=<value>`` \u2192  EventType.SET_GLOBAL\n* ``sendevent -E HOLD_JOB -J <job>``            \u2192  EventType.HOLD_JOB\n* ``sendevent -E JOB_OFF_HOLD -J <job>``        \u2192  EventType.JOB_OFF_HOLD\n* ``sendevent -E JOB_ON_ICE -J <job>``          \u2192  EventType.JOB_ON_ICE\n* ``sendevent -E JOB_OFF_ICE -J <job>``         \u2192  EventType.JOB_OFF_ICE\n\nValidation summary (enforced by ``@model_validator``)\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n+-------------------+------------------------------------------+\n| event_type        | required extra fields                    |\n+===================+==========================================+\n| CHANGE_STATUS     | new_status                               |\n+-------------------+------------------------------------------+\n| SET_GLOBAL        | global_name, global_value                |\n+-------------------+------------------------------------------+\n| STARTJOB          | job_name                                 |\n| FORCE_STARTJOB    | job_name                                 |\n| KILLJOB           | job_name                                 |\n| HOLD_JOB          | job_name                                 |\n| JOB_OFF_HOLD      | job_name                                 |\n| JOB_ON_ICE        | job_name                                 |\n| JOB_OFF_ICE       | job_name                                 |\n+-------------------+------------------------------------------+\n| CHECK_HEARTBEAT   | (no extra fields required)               |\n| SEND_ALERT        | (no extra fields required)               |\n+-------------------+------------------------------------------+",
                        "methods": [
                            "validate_event_fields"
                        ]
                    },
                    {
                        "name": "EventHistory",
                        "doc": "An immutable audit log record derived from a processed ``Event``.\n\nWritten to the ``event_history`` table by the EPS immediately after it\nfinishes processing an ``Event``.  Unlike the live ``event_queue`` table\n(which may eventually be pruned), the history table is **never deleted\nfrom** \u2014 it is the permanent paper trail for every state change, operator\naction, and scheduler decision.\n\nIn real AutoSys the equivalent is the ``ujo_job_hist`` Oracle table, which\nstores a row for every event that has ever affected a job, along with the\njob's status before and after the event.\n\nThe ``EventHistory`` row inherits all fields from ``Event`` (so you always\nknow *what* was requested and *when*) and adds ``metadata_json`` for the\nrich contextual data that varies per event type and cannot be practically\nnormalised into fixed columns.\n\nTypical ``metadata_json`` payloads by event type\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\nCHANGE_STATUS::\n\n    {\n        \"previous_status\": \"FAILURE\",\n        \"reason\": \"Manually overridden by operator after upstream data fix\",\n        \"operator\": \"john.smith\"\n    }\n\nKILLJOB::\n\n    {\n        \"signal_sent\": \"SIGTERM\",\n        \"pid\": 48291,\n        \"machine\": \"agent-prod-01\",\n        \"exit_code\": -15\n    }\n\nSTARTJOB (EPS evaluated conditions)::\n\n    {\n        \"condition_expression\": \"success(EXTRACT_JOB) & value(FEED_FLAG)=\\\"YES\\\"\",\n        \"condition_result\": true,\n        \"evaluated_at\": \"2024-03-15T06:00:00.123456\"\n    }\n\nSET_GLOBAL::\n\n    {\n        \"previous_value\": \"NO\",\n        \"jobs_re_evaluated\": [\"LOAD_JOB_A\", \"LOAD_JOB_B\"]\n    }\n\nProcessing errors::\n\n    {\n        \"error\": \"Job 'ETL_LOAD_SALES' not found in jobs table\",\n        \"traceback\": \"...\"\n    }",
                        "methods": []
                    }
                ],
                "functions": [],
                "description": "Pydantic models for the AutoSys Event Queue (EPS queue)."
            },
            {
                "name": "enums.py",
                "type": "Python",
                "path": "autosys/models/enums.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 236,
                "module_doc": "All enumerations used across the AutoSys clone.\n\nEvery enum maps 1-to-1 to a real AutoSys concept.  String values match\nthe exact strings AutoSys uses so that JIL files and DB records are\nhuman-readable and directly comparable to real AutoSys output.",
                "classes": [
                    {
                        "name": "JobType",
                        "doc": "The job_type field in a JIL definition.\n\nBOX       - A container/workflow that groups other jobs.\n            It activates on schedule; children run inside it.\nCMD       - Runs a shell command on a target machine via the System Agent.\nFTP       - Transfers files between machines using FTP/SFTP.\nFILEWATCH - Watches for a file to appear / reach a minimum size.\nCONNECT   - Tests network connectivity to a host:port.",
                        "methods": []
                    },
                    {
                        "name": "JobStatus",
                        "doc": "Current runtime state of a job.\n\nTransitions are enforced by state_machine.py (Phase 3).\n\nINACTIVE              - Default state; job has not yet been triggered\n                        for the current run cycle.\nWAIT_REPLY            - Waiting for an external event or dependency.\nON_HOLD               - Manually frozen by HOLD_JOB event; will not\n                        start until JOB_OFF_HOLD is sent.\nON_ICE                - Frozen for the entire current run cycle; resets\n                        to INACTIVE at the next cycle boundary.\nSTARTING              - ACE has dispatched the job to the System Agent\n                        but the agent has not yet confirmed start.\nRUNNING               - System Agent is actively executing the job.\nSUCCESS               - Job completed with exit code 0.\nFAILURE               - Job completed with non-zero exit code and all\n                        retries are exhausted.\nTERMINATED            - Job was killed by a KILLJOB event.\nRESTART               - Job failed but retries remain; ACE will\n                        re-dispatch shortly.\nREFRESH_DEPENDENCIES  - ACE is re-evaluating the job's condition\n                        expression after a dependency changed state.\nACTIVATED             - BOX-only: the BOX is open and children are\n                        eligible to run.\nQUE_WAIT              - Job is ready to run but is blocked waiting\n                        for a virtual resource slot (max_load).\nPEND_MACH             - Waiting for a machine to become available.\nRESWAIT               - Waiting for resource.\nON_NOEXEC             - Job bypassed execution.\nSUSPENDED             - Job suspended.",
                        "methods": []
                    },
                    {
                        "name": "EventType",
                        "doc": "All event types that can be placed on the event queue.\n\nThe Event Processor Service (EPS) in scheduler_ace/event_processor.py\nconsumes these and drives the state machine.\n\nSTARTJOB        - Start the job if all conditions are satisfied.\nFORCE_STARTJOB  - Start the job immediately, ignoring conditions.\nKILLJOB         - Send SIGTERM to the running process \u2192 TERMINATED.\nHOLD_JOB        - Transition job to ON_HOLD.\nJOB_OFF_HOLD    - Release a job from ON_HOLD \u2192 INACTIVE.\nJOB_ON_ICE      - Transition job to ON_ICE for this run cycle.\nJOB_OFF_ICE     - Release a job from ON_ICE \u2192 INACTIVE.\nCHANGE_STATUS   - Manually override a job's status (used by operators).\nSET_GLOBAL      - Set a named global variable (can trigger value()\n                  conditions on other jobs).\nCHECK_HEARTBEAT - Ping a System Agent and verify it is reachable.\nSEND_ALERT      - Raise an alarm manually without a job failure.",
                        "methods": []
                    },
                    {
                        "name": "AlarmType",
                        "doc": "Categories of alarms raised by the alarm_manager.\n\nALARM_IF_FAIL        - job_attribute: alarm_if_fail = 1\nALARM_IF_TERMINATED  - job attribute: alarm_if_terminated = 1\nMAX_RUN_ALARM        - job has been running longer than max_run_alarm\n                       minutes (watchdog timer).\nMIN_RUN_ALARM        - job finished in less than min_run_alarm minutes\n                       (unexpectedly short run \u2014 data quality signal).\nHEARTBEAT_FAIL       - System Agent did not respond to CHECK_HEARTBEAT.",
                        "methods": []
                    },
                    {
                        "name": "DayOfWeek",
                        "doc": "Allowed tokens in the JIL days_of_week attribute.\n\nExample JIL:  days_of_week: mo,tu,we,th,fr\nSpecial value ALL means every day of the week.",
                        "methods": [
                            "weekdays",
                            "all_days"
                        ]
                    },
                    {
                        "name": "FtpType",
                        "doc": "Direction of an FTP job transfer.",
                        "methods": []
                    },
                    {
                        "name": "NotificationType",
                        "doc": "How AutoSys delivers job notifications.",
                        "methods": []
                    },
                    {
                        "name": "MachineStatus",
                        "doc": "Liveness state of a System Agent (machine).",
                        "methods": []
                    },
                    {
                        "name": "EventSource",
                        "doc": "Records which component raised an event \u2014 useful for audit logs.",
                        "methods": []
                    }
                ],
                "functions": [],
                "description": "All enumerations used across the AutoSys clone."
            },
            {
                "name": "job_run.py",
                "type": "Python",
                "path": "autosys/models/job_run.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 331,
                "module_doc": "Pydantic model for tracking individual AutoSys job execution history.\n\nBackground\n----------\nIn real AutoSys, the **Scheduler ACE** (AutoSys Correlated Events) engine\nmaintains a run-time status table that is updated throughout a job's lifecycle.\nThis module provides the Python equivalent: a ``JobRun`` record written to the\n``job_runs`` table (one row per run *attempt*, including every retry).\n\nRelationship to other tables\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n* ``job_runs.job_name``  \u2192  ``jobs.job_name``   (FK to the job definition)\n* Each row is immutable once the run reaches a terminal state.\n  In-flight updates (status, pid, etc.) are made via ``UPDATE \u2026 WHERE run_id=\u2026``.\n\nRetry semantics\n~~~~~~~~~~~~~~~\nAutoSys retries are not separate job definitions \u2014 they are new ``JobRun``\nrows against the same ``job_name``, with ``retry_count`` incremented.\nThe Scheduler ACE creates the next ``JobRun`` row when it transitions the\njob through ``RESTART \u2192 STARTING``.\n\nDate tracking\n~~~~~~~~~~~~~\n``run_date`` stores the *logical* business date the job belongs to (i.e. the\ndate the Scheduler decided to fire the job), not necessarily the wall-clock\ndate.  This distinction matters for overnight jobs (e.g. a job scheduled for\n23:55 on Monday is logically a Monday run even if it finishes Tuesday at 00:02).\n\nOutput path substitution\n~~~~~~~~~~~~~~~~~~~~~~~~\nAutoSys supports ``%%DATE%%`` and ``%%AUTORUN%%`` tokens in std_out_file /\nstd_err_file paths.  The System Agent expands these at runtime, so the actual\nwritten path can differ from what is stored in the job definition.\n``stdout_path`` / ``stderr_path`` here store the *resolved* path.",
                "classes": [
                    {
                        "name": "JobRun",
                        "doc": "A single execution attempt of an AutoSys job.\n\nOne row is created per dispatch: the very first run produces a ``JobRun``\nwith ``retry_count=0``; if the job fails and has retries remaining the\nScheduler creates a second row with ``retry_count=1``, and so on.\n\nThe EPS (Event Processor Service) and the System Agent both write to this\nmodel throughout a run's lifecycle:\n\n1. EPS writes the initial row (status=STARTING) when it dispatches the job.\n2. The System Agent updates ``start_time`` and ``pid`` once the subprocess\n   is launched (status=RUNNING).\n3. The System Agent writes ``end_time`` and ``exit_code`` on completion\n   (status=SUCCESS / FAILURE / TERMINATED).\n\nThis record is the source of truth for the ``autorep -j <name>`` output\nand the AutoSys GUI job activity view.",
                        "methods": [
                            "duration_seconds",
                            "is_terminal"
                        ]
                    }
                ],
                "functions": [],
                "description": "Pydantic model for tracking individual AutoSys job execution history."
            },
            {
                "name": "job.py",
                "type": "Python",
                "path": "autosys/models/job.py",
                "kid_desc": "The blank Ticket that tells us what an order needs (name, owner).",
                "lines": 553,
                "module_doc": "Pydantic models for AutoSys job definitions.\n\nHierarchy\n---------\nJob           \u2014 base class holding all ~50 JIL attributes\n  \u251c\u2500\u2500 CmdJob       \u2014 job_type: CMD  (shell command)\n  \u251c\u2500\u2500 BoxJob       \u2014 job_type: BOX  (workflow container)\n  \u251c\u2500\u2500 FilewatchJob \u2014 job_type: FILEWATCH\n  \u251c\u2500\u2500 FtpJob       \u2014 job_type: FTP\n  \u2514\u2500\u2500 ConnectJob   \u2014 job_type: CONNECT\n\nThe discriminated-union helper ``parse_job()`` at the bottom lets\nyou build the right subclass from a raw dict produced by the JIL parser.\n\nReal AutoSys reference attributes covered\n------------------------------------------\nIdentity        : job_name, job_type, description, owner, permission, run_as_user\nExecution       : command, machine, run_window, profile,\n                  std_out_file, std_err_file, std_in_file\nBOX container   : box_name, box_success, box_failure, box_terminator\nScheduling      : start_times, start_mins, days_of_week,\n                  run_calendar, exclude_calendar,\n                  date_conditions, term_run_time\nDependencies    : condition\nReliability     : n_retrys, max_exit_success, max_run_alarm, min_run_alarm,\n                  alarm_if_fail, alarm_if_terminated\nResources       : job_load, max_load\nNotifications   : notification_msg, notification_emailaddress,\n                  notification_type, send_report\nFTP-specific    : ftp_server, ftp_user, ftp_type, ftp_src, ftp_dest\nFILEWATCH-spec. : watch_file, watch_file_min_size, watch_interval\nRuntime state   : status, last_start, last_end, last_run_date\nMetadata        : created_at, updated_at",
                "classes": [
                    {
                        "name": "Job",
                        "doc": "Base representation of an AutoSys job definition.\n\nEvery field corresponds to a JIL attribute.  Fields that are not\napplicable to a given job_type are left as None and validated by the\nconcrete subclasses.",
                        "methods": [
                            "normalise_start_times",
                            "normalise_days"
                        ]
                    },
                    {
                        "name": "CmdJob",
                        "doc": "job_type: CMD \u2014 runs a shell command on a System Agent.\n\nRequired JIL attributes: command, machine",
                        "methods": [
                            "require_command_and_machine"
                        ]
                    },
                    {
                        "name": "BoxJob",
                        "doc": "job_type: BOX \u2014 a workflow container for grouping child jobs.\n\nA BOX has its own schedule (start_times / days_of_week).  It\nactivates (\u2192 ACTIVATED state) at the scheduled time; child jobs\nthen run based on their individual conditions.\n\nBOX success/failure rules:\n  \u2022 BOX \u2192 SUCCESS when all non-box_terminator children reach SUCCESS\n    (or when the designated box_success job succeeds).\n  \u2022 BOX \u2192 FAILURE when any child reaches FAILURE\n    (or when the designated box_failure job fails).\n  \u2022 BOX \u2192 TERMINATED when a box_terminator child terminates.",
                        "methods": [
                            "no_command_allowed"
                        ]
                    },
                    {
                        "name": "FilewatchJob",
                        "doc": "job_type: FILEWATCH \u2014 polls for a file to appear / reach minimum size.\n\nThe System Agent checks for the file every watch_interval seconds.\nOnce found (and >= watch_file_min_size bytes), it reports SUCCESS.",
                        "methods": [
                            "require_watch_file"
                        ]
                    },
                    {
                        "name": "FtpJob",
                        "doc": "job_type: FTP \u2014 transfers files between machines.\n\nThe System Agent performs the transfer using the OS FTP/SFTP client.",
                        "methods": [
                            "require_ftp_fields"
                        ]
                    },
                    {
                        "name": "ConnectJob",
                        "doc": "job_type: CONNECT \u2014 tests TCP connectivity to machine:port.\n\nUses the machine attribute as the target host.\nSuccess means the TCP handshake completed within run_window (if set).",
                        "methods": [
                            "require_machine"
                        ]
                    },
                    {
                        "name": "SapJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "PeoplesoftJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "InformaticaJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "MicrofocusJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "WebserviceJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "RemotecmdJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "WolJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "UserdefinedJob",
                        "doc": "No docstring provided.",
                        "methods": []
                    }
                ],
                "functions": [
                    {
                        "name": "parse_job",
                        "doc": "Build the correct Job subclass from a raw dictionary.\n\nTypically called by the JIL parser after it has tokenised a JIL stanza.\n\nExample\n-------\n>>> j = parse_job({\n...     \"job_name\": \"extract_sales\",\n...     \"job_type\": \"CMD\",\n...     \"command\": \"/scripts/extract.sh\",\n...     \"machine\": \"etl-server-01\",\n... })\n>>> type(j).__name__\n'CmdJob'\n>>> j.status\n'INACTIVE'"
                    }
                ],
                "description": "Pydantic models for AutoSys job definitions."
            },
            {
                "name": "alarm.py",
                "type": "Python",
                "path": "autosys/models/alarm.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 326,
                "module_doc": "Pydantic model for AutoSys alarms.\n\nIn real AutoSys, the alarm manager (also called the Alarm Management Facility,\nor AMF) watches for job events that match alarm conditions and writes records to\nthe alarm log and to NSM (Network and Systems Management) integration endpoints.\nOperators inspect active alarms via the ``autorep -A`` command or through the\nAutoSys GUI alarm panel.\n\nAlarm life-cycle\n----------------\n1.  A job transitions to FAILURE, TERMINATED, or a runtime-duration threshold\n    (max_run_alarm / min_run_alarm) is crossed.\n2.  The alarm_manager (Phase 11 in this clone) evaluates the job's alarm\n    attributes (alarm_if_fail, alarm_if_terminated, max_run_alarm, min_run_alarm)\n    or detects a HEARTBEAT_FAIL from the agent monitor.\n3.  An ``Alarm`` record is created with ``cleared_at=None`` (active).\n4.  The notifier dispatches the alarm to the configured channel\n    (email / SNMP / NSM / Remedy) and sets ``notified=True``.\n5.  An operator (or an automated remediation script) acknowledges the alarm,\n    setting ``cleared_at`` and ``cleared_by``.\n\nThis module intentionally contains only the data model; all alarm-raising and\nnotification logic lives in ``alarm_manager.py`` (Phase 11).",
                "classes": [
                    {
                        "name": "Alarm",
                        "doc": "A single alarm record raised by the alarm_manager.\n\nAlarms are persistent records: once raised they remain active until\nexplicitly cleared.  They are the primary mechanism by which AutoSys\nnotifies operations teams that a job has deviated from expected behaviour.\n\nMapping to real AutoSys\n-----------------------\nReal AutoSys stores alarm records in the ``EVENT_DEMON`` table (or a\ndedicated alarm table depending on the version and site configuration).\nThe ``autorep -A`` command lists all active alarms; operators clear them\nvia ``sendevent -E CLEAR_ALARM -J <job_name>`` or through the GUI alarm\npanel.\n\nThis clone persists ``Alarm`` rows in the ``alarms`` database table, which\nis managed exclusively by the ``alarm_manager`` service.  The ``alarm_id``\nUUID is the primary key used for all cross-service references (e.g. in\nnotification dispatch logs and audit tables).\n\nField ordering follows the logical life-cycle of an alarm: identity first,\nthen the origin (which job / run triggered it), then classification, then\ncontent, then the temporal lifecycle fields, and finally the notification\ntracking flag.",
                        "methods": [
                            "is_active"
                        ]
                    }
                ],
                "functions": [],
                "description": "Pydantic model for AutoSys alarms."
            },
            {
                "name": "resource.py",
                "type": "Python",
                "path": "autosys/models/resource.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 324,
                "module_doc": "Pydantic model for AutoSys virtual resources.\n\nIn real AutoSys, virtual resources are a job-concurrency control mechanism.\nA virtual resource has a finite number of capacity units called its ``max_load``.\nEach job definition declares how many units it needs while executing via the\n``job_load`` JIL attribute.  The Scheduler ACE will not dispatch a job if\ndoing so would cause the resource's total consumed units to exceed ``max_load``.\nInstead the job enters ``QUE_WAIT`` state and is re-evaluated each time a\nrunning job finishes and frees some units.\n\nMental model: ``max_load`` is a counting semaphore's initial count.  Each\nrunning job decrements the semaphore by its ``job_load`` value.  A job that\nwould drive the count below zero waits in QUE_WAIT until enough jobs complete\nto bring the available units back up.\n\nReal AutoSys CLI reference\n--------------------------\n  caresource -A -r etl_resource -l 10   # create resource with max_load=10\n  caresource -U -r etl_resource -l 20   # update max_load to 20\n  caresource -D -r etl_resource         # delete resource\n  caresource -s                         # list all resources\n  autorep    -R etl_resource            # show current load and job queue\n\nJIL job attributes that interact with virtual resources\n-------------------------------------------------------\n  ``job_load: <n>``   \u2014 units consumed by this job while RUNNING (default 1)\n  ``max_load: <n>``   \u2014 the max_load cap, also writable at the job level in\n                        real AutoSys as a shorthand for a per-machine resource\n\nCommon operational use cases\n----------------------------\n* Limiting concurrent database-loading jobs to prevent overwhelming a target DB\n  with simultaneous bulk-insert sessions.\n* Capping the number of simultaneous FTP transfers on a shared WAN link to avoid\n  saturating bandwidth.\n* Serialising jobs that write to a shared NFS output directory to prevent file\n  corruption caused by concurrent writers.\n* Reserving compute capacity on a Hadoop edge node (a job_load=4 job for a heavy\n  MapReduce submission, with max_load=8, allowing at most two heavy jobs at once).\n\nThis clone manages virtual resources in the ``virtual_resources`` database table.\nThe ``current_load`` field is tracked in memory by the ``resource_manager`` service\n(Phase 8) and periodically checkpointed to the database.  When a job transitions\nfrom STARTING \u2192 RUNNING the resource_manager atomically increments\n``current_load`` by the job's ``job_load``.  When a job transitions to any\nterminal state (SUCCESS, FAILURE, TERMINATED) the resource_manager decrements\n``current_load`` and wakes the QUE_WAIT evaluator loop so that waiting jobs can\nbe reconsidered.",
                "classes": [
                    {
                        "name": "VirtualResource",
                        "doc": "A named virtual resource used for job concurrency control.\n\n``VirtualResource`` instances live in the ``virtual_resources`` table.\nJob definitions reference a resource by name via the ``job_load`` / machine\npairing (real AutoSys) or by a direct ``resource_name`` attribute (this\nclone's extended JIL).\n\nThe ``resource_manager`` service (Phase 8) is the sole writer of\n``current_load``.  All other services (the Scheduler ACE, the REST API,\nthe dashboard) treat ``VirtualResource`` as read-only and use\n``can_accept()`` / ``is_saturated`` to make dispatch or display decisions.\n\nMapping to real AutoSys\n-----------------------\nReal AutoSys ``caresource`` records contain the resource name and\n``max_load``.  The current usage (analogous to ``current_load`` here) is\ncomputed dynamically by the ACE from the count of RUNNING jobs that\nreference the resource multiplied by their respective ``job_load`` values.\nThis clone materialises ``current_load`` as an explicit field for two\nreasons:\n\n1. **Performance** \u2014 avoids a full table-scan of running jobs every time\n   the Scheduler needs to decide whether a QUE_WAIT job can be promoted.\n2. **Simplicity** \u2014 the QUE_WAIT release logic can compare a single\n   integer rather than re-aggregating job records.\n\nThe trade-off is that ``current_load`` must be kept in sync with the\nactual set of running jobs.  The ``resource_manager`` achieves this via\natomic increment/decrement operations wrapped in database transactions.",
                        "methods": [
                            "available_slots",
                            "is_saturated",
                            "can_accept"
                        ]
                    }
                ],
                "functions": [],
                "description": "Pydantic model for AutoSys virtual resources."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/models/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 46,
                "module_doc": "autosys.models \u2014 public re-exports.\n\nImport from here instead of individual submodules so the rest of the\ncodebase has a single stable import surface.\n\n    from autosys.models import Job, CmdJob, BoxJob, JobStatus, EventType",
                "classes": [],
                "functions": [],
                "description": "autosys.models \u2014 public re-exports."
            },
            {
                "name": "machine.py",
                "type": "Python",
                "path": "autosys/models/machine.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 79,
                "module_doc": "MachineDef \u2014 Pydantic model for a JIL ``insert_machine:`` definition.\n\nIn real AutoSys, machines can be defined three ways:\n  1. GUI (Workload Automation DE)\n  2. JIL file via ``insert_machine:`` stanza\n  3. ``autosys machine register`` CLI (our Phase 6 addition)\n\nThis model covers path 2.  The JIL parser creates a ``MachineDef`` for\neach ``insert_machine:`` stanza it encounters; the importer then upserts\na ``MachineRow`` in the ``machines`` table via ``MachineRepository``.\n\nReal AutoSys ``insert_machine`` attributes\n------------------------------------------\n::\n\n    insert_machine: etl-server-01\n        type: a              # \"a\" = UNIX agent (only type we support)\n        port: 7520\n        max_load: 100        # max concurrent jobs (not enforced in Phase 7)\n        description: ETL worker\n\nWe support a subset: ``type``, ``port``, ``max_load``, ``description``.\nUnknown attributes are silently ignored (forward-compatibility).\n\nThe ``host`` attribute is NOT part of real AutoSys JIL (the machine name\nIS the hostname in most installations).  We allow it as an extension so\nyou can separate the logical name from the IP:\n\n    insert_machine: etl-server-01\n        host: 192.168.1.10   # optional; defaults to machine_name\n        port: 7520",
                "classes": [
                    {
                        "name": "MachineDef",
                        "doc": "JIL machine definition \u2014 maps to a row in the ``machines`` table.\n\nAttributes\n----------\nmachine_name:\n    Logical name of the machine (the key used in ``machine:`` job attrs).\ntype:\n    Agent type.  ``\"a\"`` = UNIX/Linux agent (the only type we support).\n    Real AutoSys also has ``\"n\"`` (Windows NT) and ``\"m\"`` (mainframe).\nhost:\n    IP or hostname of the agent server.  Defaults to ``machine_name``\n    if omitted (works when the logical name IS the DNS hostname).\nport:\n    TCP port the System Agent listens on.  Default 7520 (same as\n    real AutoSys).\nmax_load:\n    Maximum number of concurrent jobs on this machine.  Real AutoSys\n    enforces this at the Scheduler level \u2014 we store it but don't\n    enforce it until Phase 8.\ndescription:\n    Free-text description stored in the DB.",
                        "methods": [
                            "_default_host"
                        ]
                    }
                ],
                "functions": [],
                "description": "MachineDef \u2014 Pydantic model for a JIL ``insert_machine:`` definition."
            },
            {
                "name": "calendar.py",
                "type": "Python",
                "path": "autosys/models/calendar.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 424,
                "module_doc": "Pydantic model for AutoSys named calendars.\n\nIn real AutoSys, a calendar is a named, ordered list of dates stored in the\nAutoSys database.  Job definitions reference calendars through two JIL\nattributes:\n\n  ``run_calendar: <name>``\n      The job is ONLY eligible to run on the dates listed in this calendar,\n      even if ``days_of_week`` and ``start_times`` would otherwise permit it.\n      Used for month-end jobs, quarter-close jobs, ad-hoc one-off runs, or any\n      schedule that cannot be expressed as a simple day-of-week + time pattern.\n\n  ``exclude_calendar: <name>``\n      The job is explicitly SKIPPED on the dates in this calendar, even when\n      ``days_of_week`` and ``start_times`` match.  Commonly used for public\n      holidays, site-wide maintenance windows, or blackout periods imposed by\n      downstream systems.\n\nThe two attributes are independent and can both be set on the same job.  The\nScheduler's time-trigger evaluator resolves them in this order:\n\n  1. Is today in ``run_calendar``?      (must be True if run_calendar is set)\n  2. Is today in ``exclude_calendar``?  (must be False if exclude_calendar is set)\n  3. Does today match ``days_of_week``? (must be True if days_of_week is set)\n\nAll three conditions must pass for a time-triggered STARTJOB event to fire.\n\nReal AutoSys CLI\n----------------\n  cacreate  -c us_holidays -f us_holidays.cal   # create calendar from file\n  caedit    -c us_holidays -f us_holidays.cal   # replace date list\n  cadestroy -c us_holidays                      # delete calendar\n  calist    -c us_holidays                      # list dates in calendar\n\nCalendar files (.cal)\n---------------------\nAutoSys allows exporting and importing calendars via plain-text ``.cal`` files.\nEach non-comment line contains a date and an optional trailing inline comment\nseparated by whitespace.  The ``Calendar.load_from_file()`` classmethod\nimplements this format.\n\nExample .cal file::\n\n    # us_holidays.cal \u2014 US Federal Public Holidays 2024\n    #\n    2024-01-01  # New Year's Day\n    2024-01-15  # Martin Luther King Jr. Day\n    2024-02-19  # Presidents' Day\n    2024-05-27  # Memorial Day\n    2024-06-19  # Juneteenth National Independence Day\n    2024-07-04  # Independence Day\n    2024-09-02  # Labor Day\n    2024-11-28  # Thanksgiving Day\n    2024-12-25  # Christmas Day\n\nThis clone stores ``Calendar`` rows in the ``calendars`` database table and\nmanages them via the ``calendar_manager`` service (Phase 9).",
                "classes": [
                    {
                        "name": "Calendar",
                        "doc": "A named AutoSys calendar \u2014 a labelled set of specific calendar dates.\n\nA ``Calendar`` instance is a standalone definition persisted in the\n``calendars`` database table.  Job definitions (``Job.run_calendar`` and\n``Job.exclude_calendar``) reference calendars by name.  The Scheduler's\ntime-trigger evaluator (``scheduler_ace/time_trigger.py``, Phase 2)\nresolves calendar names to ``Calendar`` objects at runtime to decide\nwhether the current date is an eligible run date for a given job.\n\nThe same ``Calendar`` model serves both ``run_calendar`` and\n``exclude_calendar`` semantics.  The interpretation \u2014 \"run only on these\ndates\" versus \"skip on these dates\" \u2014 is determined entirely by which\n``Job`` attribute references the calendar, not by anything stored in the\n``Calendar`` itself.\n\nMapping to real AutoSys\n-----------------------\nReal AutoSys:  ``cacreate -c business_days -f business_days.cal``\nThis clone:    ``POST /api/v1/calendars``  with a ``Calendar`` JSON body,\n               or ``calendar_manager.create_calendar(calendar)``.\n\nThe ``calendar_name`` is the lookup key.  If a job references a calendar\nname that does not exist in the ``calendars`` table, the Scheduler logs a\nwarning and treats the condition as unsatisfied (no STARTJOB event fires).",
                        "methods": [
                            "parse_dates",
                            "contains",
                            "load_from_file"
                        ]
                    }
                ],
                "functions": [],
                "description": "Pydantic model for AutoSys named calendars."
            },
            {
                "name": "global_var.py",
                "type": "Python",
                "path": "autosys/models/global_var.py",
                "kid_desc": "A tiny worker robot helping out in the 3. The Ticket Shapes (Models) department.",
                "lines": 372,
                "module_doc": "Pydantic model for AutoSys global variables.\n\nIn real AutoSys, global variables are named string values stored in the AutoSys\ndatabase (the ``GLOBAL_VAR`` table in the event daemon's schema).  They serve\ntwo distinct runtime purposes:\n\n1.  Command substitution (%%VARNAME%% tokens)\n    -----------------------------------------\n    At the exact moment a job's command is dispatched to the System Agent, the\n    Scheduler ACE expands all ``%%VARNAME%%`` tokens in the ``command`` string\n    by substituting the current value of the corresponding global variable.\n    For example, a job defined with::\n\n        command: /opt/etl/load.sh --date %%RUN_DATE%% --env %%TARGET_ENV%%\n\n    is expanded immediately before dispatch to something like::\n\n        /opt/etl/load.sh --date 2024-03-15 --env production\n\n    The System Agent receives and executes the already-expanded command.  The\n    original unexpanded command string is stored in the job definition; the\n    expanded version is recorded in the JobRun record for auditability.\n\n    This substitution is performed by ``command_expander.py`` (Phase 6) in this\n    clone.  It reads from the ``global_variables`` table AND resolves the\n    built-in read-only globals listed in ``AUTOSYS_BUILTIN_GLOBALS`` below.\n\n2.  Dependency condition predicates (value() expressions)\n    -------------------------------------------------------\n    Job conditions can test global variable values using the ``value()``\n    predicate in the ``condition`` JIL attribute.  For example::\n\n        condition: value(PIPELINE_ENABLED) = \"Y\"\n\n    or combined with job-status conditions::\n\n        condition: success(extract_sales) & value(TARGET_ENV) = \"production\"\n\n    The ``condition_evaluator.py`` (Phase 5) resolves ``value()`` predicates\n    by looking up the named variable in the ``global_variables`` table and\n    comparing the stored string value.\n\nSetting global variables\n------------------------\n* ``sendevent -E SET_GLOBAL -G VARNAME=value`` \u2014 operator CLI command.  This\n  places a SET_GLOBAL event on the event queue; the Event Processor Service\n  (EPS) dequeues it and writes the new value to the ``global_variables`` table,\n  then re-evaluates any job conditions that reference the changed variable.\n\n* A job can raise a SET_GLOBAL event as part of its completion logic by\n  including ``SET_GLOBAL`` in its post-execution configuration (specific to\n  this clone's extended JIL; see ``JobSetGlobalAction`` in Phase 7).\n\n* The ``global_var_manager`` seeds default globals from application\n  configuration at startup time, marking them as ``updated_by='startup'``.\n\nReal AutoSys CLI reference\n--------------------------\n  autoflags -s -g VARNAME=value   # set a global variable\n  autoflags -r -g VARNAME         # read a global variable's current value\n  autorep   -G VARNAME            # show global variable with change history\n\nThis clone stores ``GlobalVariable`` rows in the ``global_variables`` table\nand manages them via the ``global_var_manager`` service (Phase 7).  The table\nis keyed on ``name`` (always stored in UPPERCASE \u2014 see the ``uppercase_name``\nvalidator).  All writes are upserts: if a variable with the same name already\nexists, its ``value``, ``updated_at``, and ``updated_by`` fields are updated\nin place rather than inserting a duplicate.\n\nBuilt-in read-only globals\n--------------------------\nAutoSys defines a set of date/time globals that are substituted into command\nstrings at dispatch time.  These are NOT stored in the ``global_variables``\ntable \u2014 they are resolved dynamically by ``command_expander.py`` at the moment\nthe command string is expanded.  They are documented in the\n``AUTOSYS_BUILTIN_GLOBALS`` mapping at the bottom of this module.\n\nAttempting to read them from the ``global_variables`` table will return no\nrows.  In real AutoSys, attempting to overwrite them with ``SET_GLOBAL`` is\nsilently ignored.  This clone's ``global_var_manager`` enforces a stricter\npolicy: it raises a ``ValueError`` if a caller tries to persist a\n``GlobalVariable`` whose ``name`` matches a key in ``AUTOSYS_BUILTIN_GLOBALS``.",
                "classes": [
                    {
                        "name": "GlobalVariable",
                        "doc": "A single AutoSys global variable record.\n\n``GlobalVariable`` instances live in the ``global_variables`` table, one\nrow per variable name.  The ``name`` column is the primary key.\n\nAll writes are upserts: if a row with the same ``name`` already exists,\nthe ``global_var_manager`` updates ``value``, ``updated_at``, and\n``updated_by`` rather than inserting a new row.  This preserves the\nsimplicity of the data model (a global variable has exactly one current\nvalue) while still capturing the full audit trail through a separate\n``global_var_history`` table (managed by Phase 7).\n\nCase sensitivity\n----------------\nAutoSys global variable names are case-insensitive at the CLI and JIL\nlevels \u2014 ``%%run_date%%``, ``%%Run_Date%%``, and ``%%RUN_DATE%%`` all\nrefer to the same variable.  This clone stores and looks up names\nexclusively in UPPERCASE to enforce a single canonical form, prevent\nduplicate-variable bugs caused by case differences, and maintain\ncompatibility with real AutoSys's internal representation.\n\nThe ``uppercase_name`` field_validator (below) performs this normalisation\nautomatically, so callers never need to manually uppercase names before\nconstructing a ``GlobalVariable``.\n\nMapping to real AutoSys\n-----------------------\nReal AutoSys stores globals in the ``GLOBAL_VAR`` table of the event\ndaemon's database schema.  The ``autoflags`` command is the canonical\nread/write interface.  The Scheduler's command-expansion step (analogous to\n``command_expander.py`` in this clone) queries this table, together with\nthe built-in globals, to perform ``%%VAR%%`` substitution in job commands.",
                        "methods": [
                            "uppercase_name"
                        ]
                    }
                ],
                "functions": [],
                "description": "Pydantic model for AutoSys global variables."
            }
        ]
    },
    {
        "id": "autosys_db",
        "order": 4,
        "title": "4. Database Storage",
        "description": "The Event Server persists all jobs and state changes.",
        "kid_title": "4. The Filing Cabinet (DB)",
        "kid_description": "We have to save every single order in a huge metal filing cabinet. These files manage the cabinet.",
        "icon": "ph-database",
        "files": [
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/db/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 4. The Filing Cabinet (DB) department.",
                "lines": 30,
                "module_doc": "autosys.db \u2014 database layer re-exports.",
                "classes": [],
                "functions": [],
                "description": "autosys.db \u2014 database layer re-exports."
            },
            {
                "name": "connection.py",
                "type": "Python",
                "path": "autosys/db/connection.py",
                "kid_desc": "A tiny worker robot helping out in the 4. The Filing Cabinet (DB) department.",
                "lines": 208,
                "module_doc": "Database connection and session management.\n\nProvides two surfaces:\n  - Async (aiosqlite) \u2014 used by the Scheduler ACE daemon and FastAPI endpoints\n  - Sync  (regular SQLite) \u2014 used by CLI commands and tests\n\nBoth share the same SQLAlchemy metadata / schema so migrations only need\nto run once.\n\nUsage (async)\n-------------\n    from autosys.db.connection import async_session\n\n    async with async_session() as session:\n        result = await session.execute(select(JobRow))\n        jobs = result.scalars().all()\n\nUsage (sync / CLI)\n------------------\n    from autosys.db.connection import sync_session\n\n    with sync_session() as session:\n        job = session.get(JobRow, \"my_job\")\n\nEngine caching\n--------------\nEngines are cached *by URL* (not as module-level singletons) so that:\n  1. Different tests can use different SQLite files without interference.\n  2. The AUTOSYS_DB_URL environment variable is read at *session open time*,\n     not at module import time \u2014 allowing monkeypatch.setenv() to work.\n\nCall reset_engines() in test teardown to discard all cached engines and\nclose their connection pools.",
                "classes": [],
                "functions": [
                    {
                        "name": "_get_db_url",
                        "doc": "Return the SQLAlchemy database URL, reading the env var at call time.\n\nPriority order:\n1. AUTOSYS_DB_URL environment variable (allows tests to override)\n2. Default path: <project_root>/data/autosys.db"
                    },
                    {
                        "name": "reset_engines",
                        "doc": "Dispose all cached engines and clear the cache.\n\nCall this in test teardown (or the test fixture's autouse cleanup) so\nthat each test starts with a fresh connection pool pointing at the\ncorrect database file."
                    },
                    {
                        "name": "get_sync_engine",
                        "doc": "Return a sync Engine for the current AUTOSYS_DB_URL, creating it once\nper URL and caching it for reuse within the same process lifetime."
                    },
                    {
                        "name": "sync_session",
                        "doc": "Sync context manager that yields a SQLAlchemy Session.\n\nThe engine is resolved from AUTOSYS_DB_URL at the moment this context\nmanager is entered (not at module import time), so tests can safely\nchange the env var between invocations."
                    },
                    {
                        "name": "get_async_engine",
                        "doc": "Return an AsyncEngine for the current AUTOSYS_DB_URL, creating once\nper URL and caching it for the process lifetime."
                    }
                ],
                "description": "Database connection and session management."
            },
            {
                "name": "migrations.py",
                "type": "Python",
                "path": "autosys/db/migrations.py",
                "kid_desc": "A tiny worker robot helping out in the 4. The Filing Cabinet (DB) department.",
                "lines": 194,
                "module_doc": "Schema creation / migration helpers.\n\nIn real AutoSys, the DBA runs a Broadcom-supplied script that creates all\ntables in Oracle or SQL Server.  This module does the same for our SQLite\nclone.\n\nTwo entry points\n----------------\ncreate_all_sync()   \u2014 synchronous, used by CLI ``autosys init`` and tests\ncreate_all_async()  \u2014 async, used by the Scheduler ACE on startup\n\nAfter creation, seed_defaults() inserts the baseline rows every fresh\ninstallation needs: the localhost System Agent and the built-in calendars.",
                "classes": [],
                "functions": [
                    {
                        "name": "create_all_sync",
                        "doc": "Create all tables in the configured database (sync).\n\nParameters\n----------\ndrop_first:\n    If True, DROP all tables before re-creating them.\n    **Destructive** \u2014 only use in tests or fresh setups."
                    },
                    {
                        "name": "_seed_defaults_sync",
                        "doc": "Insert default rows that a fresh installation requires."
                    },
                    {
                        "name": "_ensure_localhost_agent",
                        "doc": "Register localhost as a System Agent if not already present."
                    },
                    {
                        "name": "_ensure_calendars",
                        "doc": "Load calendar .cal files from config/calendars/ into the DB."
                    },
                    {
                        "name": "list_tables_sync",
                        "doc": "Return the names of all tables currently in the database."
                    }
                ],
                "description": "Schema creation / migration helpers."
            },
            {
                "name": "repository.py",
                "type": "Python",
                "path": "autosys/db/repository.py",
                "kid_desc": "The Cabinet Keeper who puts tickets in and takes them out.",
                "lines": 749,
                "module_doc": "AutoSys database repository layer.\n\nAbstracts all SQLAlchemy row-level operations behind clean domain-facing\nfunctions.  Every function accepts an already-open SQLAlchemy ``Session``\nso the caller controls transaction boundaries.\n\nThree repositories\n-------------------\nJobRepository\n    insert_job / update_job / delete_job / autorep queries.\n\nEventRepository\n    Enqueue and dequeue events from the event_queue table.\n    This is the write side of the Event Processor's work queue.\n\nGlobalVarRepository\n    Upsert and read global variables (the SET_GLOBAL / GET_GLOBAL table).\n\nMapping helpers\n---------------\n_job_to_row_kwargs(job: Job) -> dict\n    Convert a Pydantic Job to DB column kwargs.\n    List fields (days_of_week, start_times) \u2192 comma-separated strings.\n\n_row_to_job(row: JobRow) -> Job\n    Convert a DB row back to the right Pydantic subclass.\n    Delegates to parse_job() which handles the type dispatch.",
                "classes": [
                    {
                        "name": "JobRepository",
                        "doc": "CRUD operations on the ``jobs`` table.\n\nAll methods are synchronous (use the sync_session() context manager from\nautosys.db.connection).  The async scheduler uses the same logic via\nasync_session in a later phase.",
                        "methods": [
                            "upsert",
                            "delete",
                            "get",
                            "get_row",
                            "list_all",
                            "search",
                            "list_by_pattern",
                            "count_children",
                            "get_children",
                            "get_running_boxes",
                            "get_box_row"
                        ]
                    },
                    {
                        "name": "EventRepository",
                        "doc": "Operations on the ``event_queue`` and ``event_history`` tables.\n\nIn real AutoSys, events enter the queue via:\n- ``sendevent`` CLI\n- The REST API (SSA)\n- The Scheduler ACE itself (internal state-change events)\n\nThe Event Processor (Phase 4) dequeues them and drives the state machine.",
                        "methods": [
                            "enqueue",
                            "dequeue_pending",
                            "mark_processed"
                        ]
                    },
                    {
                        "name": "GlobalVarRepository",
                        "doc": "Operations on the ``global_variables`` table.\n\nAutoSys global variables:\n- Are set via ``sendevent -E SET_GLOBAL -G name -v value``\n- Are expanded at dispatch time as ``%%VARNAME%%`` in command strings\n- Are queried via ``autorep -G varname``",
                        "methods": [
                            "set",
                            "get",
                            "list_all",
                            "as_dict"
                        ]
                    },
                    {
                        "name": "RunRepository",
                        "doc": "CRUD for ``job_runs`` \u2014 the audit log of every job execution.\n\nAutoSys retains full run history so operators can inspect previous runs,\ncompare durations, and audit exit codes.  ``autorep -J <job>`` in the\nreal system shows the last run; this repository provides ``list_runs``\nfor the full history.",
                        "methods": [
                            "start",
                            "finish",
                            "get_history",
                            "list_runs",
                            "latest_run_id"
                        ]
                    },
                    {
                        "name": "OutputRepository",
                        "doc": "Append and read captured output lines from ``job_output``.\n\nEach line the subprocess writes to stdout/stderr becomes one row here.\nThe System Agent writes lines in real-time (from the output-reader thread)\nso that ``autosys jobs tail`` can show partial output of a running job.",
                        "methods": [
                            "append",
                            "get_lines",
                            "get_lines_for_job"
                        ]
                    },
                    {
                        "name": "MachineRepository",
                        "doc": "CRUD for the ``machines`` table \u2014 the System Agent registry.\n\nIn real AutoSys, machine definitions are imported via JIL with\n``insert_machine:`` syntax or registered automatically when an agent\nstarts up and POSTs its address to the Scheduler.  Here we provide a\nclean Python API for both paths.\n\nThe Scheduler looks up machines here to find ``host:port`` when\ndispatching remote jobs.  The ``status`` column tracks agent health:\n  - ``\"UNKNOWN\"``  \u2014 registered but never pinged\n  - ``\"UP\"``       \u2014 heartbeat responded successfully\n  - ``\"DOWN\"``     \u2014 heartbeat failed (alarm raised if alarm_if_fail=1)",
                        "methods": [
                            "register",
                            "get",
                            "list_all",
                            "update_heartbeat",
                            "set_status"
                        ]
                    },
                    {
                        "name": "CalendarRepository",
                        "doc": "No docstring provided.",
                        "methods": [
                            "get",
                            "list_all",
                            "upsert",
                            "delete"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_job_to_row_kwargs",
                        "doc": "Convert a Pydantic Job to a flat kwargs dict for constructing a JobRow.\n\nTransformations:\n- list[str] fields \u2192 comma-separated string  (or None if empty)\n- None values \u2192 omitted entirely  (DB column default applies)\n- Enum values \u2192 their .value string  (already str for str-enums)"
                    },
                    {
                        "name": "_row_to_job",
                        "doc": "Convert a DB JobRow back to the right Pydantic Job subclass.\n\nReads every non-None column value into a dict, then delegates to\nparse_job() which dispatches on job_type and runs Pydantic validators.\nThe validators handle comma-separated \u2192 list conversion for schedule\nfields automatically."
                    }
                ],
                "description": "AutoSys database repository layer."
            },
            {
                "name": "schema.py",
                "type": "Python",
                "path": "autosys/db/schema.py",
                "kid_desc": "The metal folders inside the filing cabinet.",
                "lines": 518,
                "module_doc": "SQLAlchemy ORM table definitions \u2014 the RDBMS layer of the AutoSys clone.\n\nEach class here maps to one database table and mirrors a Pydantic model\nfrom autosys/models/.  Keeping them separate lets the engine layer work\nwith SQLAlchemy sessions while the API and parser work with Pydantic models.\n\nTable inventory\n---------------\n1.  JobRow           \u2014 job definitions (all ~50 JIL attributes)\n2.  JobRunRow        \u2014 per-run history (one row per run attempt / retry)\n3.  EventQueueRow    \u2014 pending events waiting for the EPS to process\n4.  EventHistoryRow  \u2014 immutable audit log of every event ever raised\n5.  GlobalVariableRow\u2014 AutoSys global variables (SET_GLOBAL / value() )\n6.  CalendarRow      \u2014 named calendars (run_calendar / exclude_calendar)\n7.  AlarmRow         \u2014 raised/cleared alarms\n8.  VirtualResourceRow \u2014 virtual resources (max_load / job_load)\n9.  MachineRow       \u2014 System Agent registry (machine name \u2192 host:port)\n\nRelationship diagram (conceptual)\n----------------------------------\nMachineRow  <\u2500\u2500  JobRow (machine FK)\nCalendarRow <\u2500\u2500  JobRow (run_calendar / exclude_calendar FK)\nJobRow      <\u2500\u2500  JobRunRow  (job_name FK)\nJobRow      <\u2500\u2500  EventQueueRow / EventHistoryRow (job_name FK)\nJobRunRow   <\u2500\u2500  AlarmRow  (run_id FK)",
                "classes": [
                    {
                        "name": "Base",
                        "doc": "Shared declarative base \u2014 all ORM classes inherit from this.",
                        "methods": []
                    },
                    {
                        "name": "JobRow",
                        "doc": "Stores one row per job definition.\n\nWhen the JIL parser processes an ``insert_job`` stanza it calls\n``db.upsert_job()``, which writes/updates a row here.\n``update_job`` and ``override_job`` also write here.\n``delete_job`` removes the row.\n\nThe ``status`` column is the *current* runtime state managed by the\nScheduler ACE state machine \u2014 it is updated in place (not in job_runs)\nso that a single ``SELECT * FROM jobs`` gives operators an instant\nsnapshot of the whole workload.",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "JobRunRow",
                        "doc": "One row per execution attempt (including retries).\n\nAutoSys keeps full run history so operators can audit every attempt.\nThe retry_count column lets you see which attempt succeeded or failed.\n\nImportant: the ``status`` here is the terminal status of *this run*.\nThe live status is on JobRow.status.",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "EventQueueRow",
                        "doc": "The event queue consumed by the Event Processor Service (EPS).\n\nThe Scheduler ACE event loop polls this table every tick.\nItems are soft-deleted (processed=True) rather than hard-deleted so\nthat the EventHistoryRow insert and the queue update can be done in\none transaction.\n\nSources that write here:\n  - CLI (sendevent command)\n  - REST API (/api/v1/events endpoint)\n  - Scheduler itself (time triggers, retry logic)\n  - System Agent callbacks (job completion)",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "EventHistoryRow",
                        "doc": "Immutable append-only copy of every event that was ever processed.\n\nThis is what ``autosys autorep -E`` or the WCC event history page\nqueries.  Rows are never updated or deleted.",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "GlobalVariableRow",
                        "doc": "AutoSys global variables \u2014 set via SET_GLOBAL events or ``set_global`` CLI.\n\nValues are readable in job commands as %%VARNAME%% and testable in\nconditions as ``value(VARNAME) = \"expected\"``.\n\nNote: built-in variables (%%DATE%%, %%YYYY%%, etc.) are NOT stored here \u2014\nthey are resolved at command-expansion time by variable_substitution.py.",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "CalendarRow",
                        "doc": "Named calendar \u2014 a list of dates used for scheduling.\n\nrun_calendar: job only runs on dates listed here.\nexclude_calendar: job SKIPS dates listed here (even if days_of_week matches).\n\nThe dates are stored as a JSON array of \"YYYY-MM-DD\" strings.",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "AlarmRow",
                        "doc": "Alarms raised by the alarm_manager.\n\nActive alarms (cleared_at IS NULL) show up in the WCC alarm console and\ncan be forwarded to NSM Event Management (port 1721).",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "VirtualResourceRow",
                        "doc": "Virtual resources control job concurrency on a machine.\n\nA job with job_load=2 on a machine with max_load=4 can have at most\ntwo such jobs running simultaneously.  Jobs that would exceed max_load\nwait in QUE_WAIT state until a slot is free.",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "MachineRow",
                        "doc": "Registry of System Agents known to the Scheduler.\n\nThe machine column in a JIL CMD job references machine_name here.\nThe Scheduler ACE looks up host:port to dispatch jobs to the correct\nSystem Agent process.\n\nMaps to: CA AutoSys \"machine definition\" (also defined via JIL with\ninsert_machine: syntax in real AutoSys).",
                        "methods": [
                            "__repr__"
                        ]
                    },
                    {
                        "name": "JobOutputRow",
                        "doc": "Stores captured stdout/stderr output from a single job execution.\n\nOne row per output line.  The System Agent writes to this table in real\ntime as the subprocess produces output.  This lets operators view partial\noutput of a running job via ``autosys jobs tail``.\n\nIn real AutoSys this data lives in flat files on the agent machine\n(the path is stored in ``job_runs.stdout_path``).  We centralise it in\nthe DB so that any CLI or API can read it without SSH access.\n\nColumns\n-------\nrun_id:\n    Foreign key \u2192 ``job_runs.run_id`` UUID.  All lines for one execution\n    share the same run_id.\njob_name:\n    Denormalised for fast ``WHERE job_name = ?`` queries from the tail\n    command without a join.\nline_no:\n    1-based sequential line number within this run.  Merged-stream output\n    (stdout + stderr interleaved) is ordered by ``line_no ASC``.\nstream:\n    ``'stdout'`` or ``'stderr'``.  Phase 5 merges both into stdout by\n    default (``stderr=subprocess.STDOUT``), but the column is kept for\n    completeness.\ncontent:\n    The raw text of the line (newline stripped).",
                        "methods": [
                            "__repr__"
                        ]
                    }
                ],
                "functions": [],
                "description": "SQLAlchemy ORM table definitions \u2014 the RDBMS layer of the AutoSys clone."
            }
        ]
    },
    {
        "id": "autosys_app_server",
        "order": 5,
        "title": "5. API Layer",
        "description": "The central nervous system routing data between DB, Scheduler, and Agent.",
        "kid_title": "5. The Walkie-Talkies (API)",
        "kid_description": "The central radio tower that lets the TVs, the Manager, and the Kitchen all talk to each other without shouting.",
        "icon": "ph-app-window",
        "files": [
            {
                "name": "auth.py",
                "type": "Python",
                "path": "autosys/app_server/auth.py",
                "kid_desc": "A tiny worker robot helping out in the 5. The Walkie-Talkies (API) department.",
                "lines": 114,
                "module_doc": "JWT authentication for the AutoSys App Server.\n\nReal AutoSys uses CA EEM (Embedded Entitlements Manager) \u2014 an LDAP-backed\nRBAC system with its own token format.  We implement a simplified JWT scheme\nthat maps to the same three roles:\n\n  viewer    \u2014 read-only (GET endpoints only)\n  operator  \u2014 can enqueue events (STARTJOB, KILLJOB, \u2026)\n  admin     \u2014 full access including DELETE and machine management\n\nAuth is controlled by the AUTOSYS_AUTH_ENABLED environment variable.\nWhen set to \"false\" (the default for development), all requests are treated\nas role \"admin\" without requiring a token.  Set to \"true\" for production.\n\nUsers are defined in AUTOSYS_USERS as a JSON mapping:\n  {\"admin\": {\"password\": \"secret\", \"role\": \"admin\"}}\n\nFor production, replace this with an LDAP lookup or EEM integration.",
                "classes": [],
                "functions": [
                    {
                        "name": "_b64url_encode",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_b64url_decode",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_sign",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "encode_token",
                        "doc": "Create a signed HS256 JWT."
                    },
                    {
                        "name": "decode_token",
                        "doc": "Verify and decode a JWT.  Raises ValueError on invalid/expired tokens."
                    },
                    {
                        "name": "authenticate",
                        "doc": "Return user dict if credentials are valid, else None."
                    }
                ],
                "description": "JWT authentication for the AutoSys App Server."
            },
            {
                "name": "deps.py",
                "type": "Python",
                "path": "autosys/app_server/deps.py",
                "kid_desc": "A tiny worker robot helping out in the 5. The Walkie-Talkies (API) department.",
                "lines": 78,
                "module_doc": "FastAPI dependency functions.\n\nAll routes use these via Depends() for:\n- Database sessions\n- Current user (auth)",
                "classes": [
                    {
                        "name": "CurrentUser",
                        "doc": "No docstring provided.",
                        "methods": [
                            "__init__",
                            "require_role"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "get_session",
                        "doc": "Yield a SQLAlchemy sync Session.  FastAPI runs sync endpoints in a\nthreadpool automatically, so blocking DB calls are safe here."
                    },
                    {
                        "name": "get_current_user",
                        "doc": "Validate the Bearer JWT and return the current user.\n\nIf AUTOSYS_AUTH_ENABLED is false (the default for development and tests),\nreturns an anonymous admin user so all endpoints work without a token."
                    }
                ],
                "description": "FastAPI dependency functions."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/app_server/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 5. The Walkie-Talkies (API) department.",
                "lines": 0,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [],
                "description": "Python source file (0 lines)"
            },
            {
                "name": "broadcaster.py",
                "type": "Python",
                "path": "autosys/app_server/broadcaster.py",
                "kid_desc": "A tiny worker robot helping out in the 5. The Walkie-Talkies (API) department.",
                "lines": 82,
                "module_doc": "WebSocket connection manager \u2014 live job-status broadcast.\n\nThe App Server keeps a set of open WebSocket connections.  Whenever the\nEvent Processor changes a job's status it calls broadcaster.publish(), which\nfans out the payload to all connected clients (the WCC dashboard subscribes).\n\nThread safety: publish() is called from the EPS background asyncio task, so\nit runs in the same event loop as the FastAPI server.  No locking required.",
                "classes": [
                    {
                        "name": "EventBroadcaster",
                        "doc": "Maintains a set of active WebSocket connections and broadcasts JSON\nmessages to all of them.\n\nUsage (inside a FastAPI WebSocket endpoint)::\n\n    @app.websocket(\"/api/v1/ws/events\")\n    async def ws_events(ws: WebSocket, broadcaster: EventBroadcaster = Depends(get_broadcaster)):\n        await broadcaster.connect(ws)\n        try:\n            while True:\n                await ws.receive_text()   # keep alive / echo\n        except WebSocketDisconnect:\n            broadcaster.disconnect(ws)",
                        "methods": [
                            "__init__",
                            "disconnect",
                            "publish_sync",
                            "connection_count"
                        ]
                    }
                ],
                "functions": [],
                "description": "WebSocket connection manager \u2014 live job-status broadcast."
            },
            {
                "name": "schemas.py",
                "type": "Python",
                "path": "autosys/app_server/schemas.py",
                "kid_desc": "A tiny worker robot helping out in the 5. The Walkie-Talkies (API) department.",
                "lines": 225,
                "module_doc": "API schemas \u2014 request/response DTOs.\n\nKept separate from domain models (autosys.models.*) so the API shape can\nevolve independently from the internal data model.",
                "classes": [
                    {
                        "name": "JobResponse",
                        "doc": "Summary view of a job \u2014 used in list responses.",
                        "methods": []
                    },
                    {
                        "name": "JobDetailResponse",
                        "doc": "Full job detail \u2014 returned by GET /api/v1/jobs/{name}.",
                        "methods": []
                    },
                    {
                        "name": "SendEventRequest",
                        "doc": "Body for POST /api/v1/jobs/{name}/sendevent.",
                        "methods": []
                    },
                    {
                        "name": "SendEventResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "EventResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "RunResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "OutputLineResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "MachineResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "RegisterMachineRequest",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "GlobalVarResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "SetGlobalRequest",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "TokenRequest",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "TokenResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "HealthResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "JILImportRequest",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "JILJobResult",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "JILImportResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "AlarmResponse",
                        "doc": "No docstring provided.",
                        "methods": []
                    },
                    {
                        "name": "WsStatusChange",
                        "doc": "Broadcast on the /api/v1/ws/events channel when a job status changes.",
                        "methods": []
                    }
                ],
                "functions": [],
                "description": "API schemas \u2014 request/response DTOs."
            },
            {
                "name": "main.py",
                "type": "Python",
                "path": "autosys/app_server/main.py",
                "kid_desc": "The Walkie-Talkie Base connecting everyone together.",
                "lines": 265,
                "module_doc": "AutoSys Application Server (SSA) \u2014 FastAPI entry point.\n\nResponsibilities\n----------------\n1. Mount all REST routers under /api/v1/\n2. Expose /api/v1/auth/token (JWT login)\n3. Expose /api/v1/ws/events  (WebSocket live status feed)\n4. Expose /health endpoints\n5. Optionally start the Event Processor as a background asyncio task\n   (used by ``autosys scheduler serve``)\n\nDesign notes\n------------\n- All REST endpoints use *sync* FastAPI route functions (def, not async def).\n  FastAPI runs them in a threadpool automatically, so SQLAlchemy sync sessions\n  are safe without wrapping in run_in_executor.\n- The WebSocket endpoint is async (required by Starlette).\n- The broadcaster singleton is stored on app.state so it can be injected\n  anywhere and replaced in tests.\n- The EPS background task is started in the lifespan context manager; cancelling\n  it on shutdown drains any in-flight tick gracefully.",
                "classes": [],
                "functions": [
                    {
                        "name": "get_broadcaster",
                        "doc": "FastAPI dependency \u2014 returns the module-level broadcaster."
                    },
                    {
                        "name": "create_app",
                        "doc": "Create and return the FastAPI application.\n\nParameters\n----------\nstart_eps:\n    If True, start the Event Processor as a background asyncio task in\n    the lifespan hook.  Used by ``autosys scheduler serve``.\neps_poll_interval:\n    EPS tick interval in seconds (only relevant when start_eps=True)."
                    },
                    {
                        "name": "_register_auth_routes",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_register_ws_routes",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_register_health_routes",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_mount_static",
                        "doc": "Serve the single-page JIL UI at GET /ui and its static assets."
                    }
                ],
                "description": "AutoSys Application Server (SSA) \u2014 FastAPI entry point."
            }
        ]
    },
    {
        "id": "autosys_scheduler",
        "order": 6,
        "title": "6. Scheduler Engine",
        "description": "Evaluates conditions and time triggers to start jobs.",
        "kid_title": "6. The Boss Manager (Scheduler)",
        "kid_description": "The Manager looks at the clock and the rules. He is the one who yells: 'Okay, start cooking the steak now!'",
        "icon": "ph-cpu",
        "files": [
            {
                "name": "event_processor.py",
                "type": "Python",
                "path": "autosys/scheduler/event_processor.py",
                "kid_desc": "A tiny worker robot helping out in the 6. The Boss Manager (Scheduler) department.",
                "lines": 967,
                "module_doc": "AutoSys Event Processor (EPS) \u2014 the heart of the scheduler.\n\nThis is the most important file in the codebase.  Everything else exists to\nsupport this loop.\n\nArchitecture\n------------\nThe Event Processor is a polling daemon that runs one \"tick\" every\n``poll_interval`` seconds (default: 1).  Each tick does two things:\n\n    1. **Process queued events** \u2014 read all unprocessed rows from\n       ``event_queue`` ordered by ``created_at ASC`` and run the\n       appropriate handler.\n\n    2. **Check time triggers** \u2014 for every INACTIVE job with a\n       ``start_times`` attribute, check if the current HH:MM matches and\n       the job hasn't already run today.  If so, enqueue a STARTJOB event\n       (processed in the next tick, preserving FIFO order).\n\nEvent handlers\n--------------\nSTARTJOB        \u2192 evaluate condition \u2192 if met, activate job (STARTING for CMD,\n                  ACTIVATED for BOX; then cascade children)\nFORCE_STARTJOB  \u2192 same, but skip condition check\nKILLJOB         \u2192 RUNNING/STARTING \u2192 TERMINATED\nHOLD_JOB        \u2192 INACTIVE/ACTIVATED \u2192 ON_HOLD\nJOB_OFF_HOLD    \u2192 ON_HOLD \u2192 INACTIVE\nJOB_ON_ICE      \u2192 INACTIVE \u2192 ON_ICE\nJOB_OFF_ICE     \u2192 ON_ICE \u2192 INACTIVE\nCHANGE_STATUS   \u2192 force-override to any status (operator escape hatch)\nSET_GLOBAL      \u2192 upsert global variable; then re-evaluate all waiting jobs\n\nDispatcher (Phase 4 stub)\n--------------------------\nIn Phase 4 the ``dispatch`` function is a stub that immediately transitions\nSTARTING \u2192 RUNNING (simulating an instant agent start) and optionally\nauto-completes the run.  Phase 5 will replace this with the real System\nAgent dispatch over a socket/REST call.\n\nBOX cascading\n-------------\nWhen a BOX job is activated:\n  1. BOX transitions INACTIVE \u2192 ACTIVATED.\n  2. Each child of the BOX that is INACTIVE and has satisfied conditions\n     is immediately started (STARTING).\n\nWhen a child of a BOX completes:\n  1. If ALL children are SUCCESS \u2192 BOX transitions to SUCCESS.\n  2. If ANY child is FAILURE (and no box_failure override) \u2192 BOX FAILURE.\n\nTestability\n-----------\n``EventProcessor.process_one_tick(session, now=...)`` is a pure synchronous\nfunction \u2014 it takes an open SQLAlchemy ``Session`` and an injectable\n``datetime`` (for time trigger tests).  No ``asyncio`` needed for unit tests.\n\nThe ``run_forever()`` coroutine wraps ``process_one_tick`` in an asyncio loop\nfor the production daemon.",
                "classes": [
                    {
                        "name": "EventProcessor",
                        "doc": "The AutoSys Event Processor Service (EPS).\n\nParameters\n----------\ndispatch_fn:\n    Called when a CMD job transitions to STARTING.  Defaults to the\n    Phase 4 stub that immediately makes the job RUNNING.\npoll_interval:\n    Seconds to sleep between ticks in the async daemon loop.\nauto_complete:\n    If True (default), the stub dispatcher immediately marks jobs as\n    SUCCESS after setting them to RUNNING.  Set to False to leave jobs\n    in RUNNING state so tests can inspect intermediate state.",
                        "methods": [
                            "__init__",
                            "_emit_status_change",
                            "process_one_tick",
                            "_handle_comment",
                            "_handle_reply_response",
                            "_handle_alarm",
                            "_handle_release_resource",
                            "stop",
                            "_handle_event",
                            "_handle_startjob",
                            "_handle_force_startjob",
                            "_handle_killjob",
                            "_handle_hold",
                            "_handle_off_hold",
                            "_handle_on_ice",
                            "_handle_off_ice",
                            "_handle_change_status",
                            "_handle_set_global",
                            "_handle_check_heartbeat",
                            "_activate_job",
                            "_activate_cmd",
                            "_activate_box",
                            "_cascade_box_children",
                            "_update_box_status"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_in_run_window",
                        "doc": "Return True if *now* falls within the HH:MM-HH:MM run_window string.\n\nAutoSys run_window format: \"HH:MM-HH:MM\" (e.g. \"08:00-18:00\").\nHandles overnight windows (e.g. \"22:00-06:00\") correctly.\nIf the window string is malformed, returns True (fail-open)."
                    },
                    {
                        "name": "_stub_dispatch",
                        "doc": "Phase 4 stub: immediately transitions STARTING \u2192 RUNNING.\n\nIn the real system the EPS sends a dispatch request to the System Agent,\nwhich starts the OS process and sends back a JOB_START acknowledgement.\nThat ACK triggers the STARTING \u2192 RUNNING transition.  Here we simulate\nthat ACK happening instantaneously for testing purposes."
                    }
                ],
                "description": "AutoSys Event Processor (EPS) \u2014 the heart of the scheduler."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/scheduler/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 6. The Boss Manager (Scheduler) department.",
                "lines": 26,
                "module_doc": "autosys.scheduler \u2014 Scheduler ACE components.",
                "classes": [],
                "functions": [],
                "description": "autosys.scheduler \u2014 Scheduler ACE components."
            },
            {
                "name": "time_trigger.py",
                "type": "Python",
                "path": "autosys/scheduler/time_trigger.py",
                "kid_desc": "The Manager checking his watch to see if it's 5:00 PM.",
                "lines": 208,
                "module_doc": "Time trigger \u2014 computes which INACTIVE jobs should fire at a given moment.\n\nIn real AutoSys, the Scheduler ACE evaluates schedule expressions on every\ntimer tick (default: every second).  A job fires when:\n\n    1. Its ``start_times`` list includes the current HH:MM.\n    2. Its ``days_of_week`` list includes today's weekday (or is empty = every day).\n    3. It has NOT already been started today (``last_run_date != today``).\n    4. The job is in a state that allows starting (INACTIVE, SUCCESS, FAILURE).\n    5. The ``exclude_calendar`` does not include today's date.\n       (Phase 4: calendar exclusion is a stub \u2014 Phase 5 adds full calendar support.)\n\nThe trigger fires at most once per minute \u2014 the granularity of AutoSys's\n``start_times`` attribute.  If the daemon misses a tick (e.g. due to restart),\nit will still catch up within one minute window after the scheduled time.\n\nUsage\n-----\n    from autosys.scheduler.time_trigger import get_triggered_jobs\n\n    now = datetime.now()\n    with sync_session() as session:\n        rows = job_repo.list_all(session)\n        for row in get_triggered_jobs(rows, now):\n            # enqueue a STARTJOB event for this job\n            ...",
                "classes": [],
                "functions": [
                    {
                        "name": "_should_run_today",
                        "doc": "Return True if this job is allowed to run on *today* based on\n``days_of_week``.\n\nRules:\n- No ``days_of_week`` \u2192 job runs every day.\n- ``all`` in the list \u2192 every day.\n- Otherwise \u2192 only the listed weekday abbreviations.\n\nParameters\n----------\nrow:\n    A ``JobRow`` (or any object with a ``days_of_week`` string attr).\ntoday:\n    The date to evaluate against."
                    },
                    {
                        "name": "_already_ran_today",
                        "doc": "Return True if this job has already been started today.\n\nChecks the ``last_run_date`` column (stored as \"YYYY-MM-DD\").\nThis prevents a job from firing twice in the same day when the\ndaemon restarts mid-day."
                    },
                    {
                        "name": "_get_matching_start_times",
                        "doc": "Return the start times from ``row.start_times`` that match the\ncurrent minute (*now*).\n\nAutoSys evaluates at 1-second granularity but ``start_times`` has\n1-minute resolution.  Any second within the HH:MM minute is a match.\nWe return the list of matching time strings for logging."
                    },
                    {
                        "name": "is_triggered",
                        "doc": "Return True if this job's schedule should fire right now.\n\nThis is the single predicate the Event Processor calls for every\nINACTIVE job on every tick.  If it returns True, the processor\nenqueues a STARTJOB event for the job.\n\nParameters\n----------\nrow:\n    A ``JobRow`` (or stub with the same attrs).\nnow:\n    The current datetime.  Injectable for testing.\ncalendars:\n    A dictionary of calendar names to Calendar models.\n\nReturns\n-------\nbool\n    True \u2192 create a STARTJOB event for this job."
                    },
                    {
                        "name": "get_triggered_jobs",
                        "doc": "Filter *rows* to those whose schedule fires at *now*.\n\nParameters\n----------\nrows:\n    A list of ``JobRow`` objects (from ``job_repo.list_all``).\nnow:\n    Current datetime (injectable for testing).\ncalendars:\n    A dictionary of calendar names to Calendar models.\n\nReturns\n-------\nlist[JobRow]\n    The subset of *rows* that should be started right now.\n\nExample\n-------\n>>> import datetime\n>>> rows = job_repo.list_all(session)\n>>> to_start = get_triggered_jobs(rows, datetime.datetime(2026, 6, 25, 6, 0))\n>>> [r.job_name for r in to_start]\n['demo_etl_box']"
                    }
                ],
                "description": "Time trigger \u2014 computes which INACTIVE jobs should fire at a given moment."
            },
            {
                "name": "box_manager.py",
                "type": "Python",
                "path": "autosys/scheduler/box_manager.py",
                "kid_desc": "The Combo Meal Handler. If fries burn, the whole combo fails.",
                "lines": 335,
                "module_doc": "BoxManager \u2014 orchestrates BOX job children.\n\nBackground: What is a BOX in AutoSys?\n--------------------------------------\nA BOX is a container job.  Its children are the jobs that have\n``box_name: <this_box>`` set in their JIL definition.\n\nReal AutoSys BOX lifecycle:\n\n  1. ``STARTJOB box_etl`` event fires.\n  2. BOX transitions: INACTIVE \u2192 STARTING \u2192 RUNNING.\n  3. All children become \"active\" \u2014 the Scheduler ACE begins evaluating\n     their start conditions on every tick.\n  4. Children without conditions (or whose conditions are already met)\n     start immediately.  Children with ``condition: s(sibling)`` wait.\n  5. When ALL children reach a terminal state:\n       - All SUCCESS            \u2192 BOX = SUCCESS\n       - Any FAILURE            \u2192 BOX = FAILURE  (unless restart logic applies)\n       - Any TERMINATED         \u2192 BOX = TERMINATED\n  6. The BOX's own terminal status propagates to any job that has\n     ``condition: s(box_etl)`` \u2014 e.g. a downstream report job.\n\nWhat BoxManager does per tick\n------------------------------\nCalled once per ``process_one_tick`` cycle after events are processed:\n\n1. Scan all RUNNING BOX jobs.\n2. For each INACTIVE child:\n   a. Evaluate start conditions (using the condition AST in the job's\n      ``condition`` field).\n   b. If conditions are satisfied (or the child has no condition):\n      transition to STARTING and invoke the dispatch function.\n3. For each box where ALL children are terminal:\n   determine and set the BOX's terminal status.\n\nKey design choices\n------------------\n- BoxManager is stateless.  All state lives in the DB.\n- It re-evaluates children every tick \u2014 idempotent.\n- It calls the same ``dispatch_fn`` as the event processor so local and\n  remote dispatch both work transparently.\n- Children reset to INACTIVE when ``FORCE_STARTJOB`` is sent to the BOX.\n  (Handled by the event processor's FORCE_STARTJOB handler which resets\n  child statuses before calling this module.)\n\nPhase 8 extensions\n------------------\n- ``max_load`` enforcement per machine\n- Restart-on-failure policy (``alarm_if_fail`` + ``n_retrys``)\n- Priority-based child ordering (``priority`` attribute)\n- Box abort: if a child fails and ``abort_on_failure`` is set, kill all\n  siblings",
                "classes": [
                    {
                        "name": "BoxManager",
                        "doc": "Per-tick box orchestrator.\n\nParameters\n----------\ndispatch_fn:\n    Called when a child job is ready to start.  Same contract as\n    ``EventProcessor.dispatch_fn``.\nkill_fn:\n    Called when a box is killed and running children must be killed.\n    Optional \u2014 if not provided, running children are just marked\n    TERMINATED without signalling their processes.\nauto_complete:\n    If True (default False) children are immediately marked SUCCESS\n    rather than dispatched.  Used by the stub dispatcher.",
                        "methods": [
                            "__init__",
                            "tick",
                            "_process_box",
                            "kill_children",
                            "reset_children",
                            "_conditions_met"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_stub_dispatch",
                        "doc": "Stub dispatcher \u2014 immediately marks CMD children SUCCESS (no subprocess)."
                    }
                ],
                "description": "BoxManager \u2014 orchestrates BOX job children."
            },
            {
                "name": "state_machine.py",
                "type": "Python",
                "path": "autosys/scheduler/state_machine.py",
                "kid_desc": "The Manager's Rulebook. Orders MUST go Waiting -> Cooking -> Done.",
                "lines": 195,
                "module_doc": "AutoSys job state machine.\n\nIn real AutoSys, the Scheduler ACE enforces a strict finite-state machine (FSM)\nfor every job.  No component is allowed to write directly to the status column;\nall transitions must go through the FSM.  This design guarantees audit-ability\nand prevents impossible states like STARTING \u2192 SUCCESS (skipping RUNNING).\n\nState diagram\n-------------\n\n                         \u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n                         \u2502  INACTIVE (default / reset)  \u2502\n                         \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518\n                 STARTJOB/time trigger  \u2502\n                 conditions met         \u2502\n                                        \u25bc\n                              \u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n               BOX jobs only  \u2502   ACTIVATED     \u2502\u25c4\u2500\u2500\u2500\u2500 BOX open, children eligible\n                              \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518\n                                       \u2502  CMD / dispatched\n                                       \u25bc\n                              \u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n                              \u2502    STARTING     \u2502  Agent notified, waiting for PID\n                              \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518\n                     agent confirms    \u2502\n                                       \u25bc\n                              \u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n                              \u2502    RUNNING      \u2502\n                              \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518\n                  exit code 0 \u2502        \u2502 non-zero / timeout\n                              \u25bc        \u25bc\n                          SUCCESS   FAILURE \u2500\u2500\u25ba RESTART (if n_retrys > 0)\n                              \u2502        \u2502              \u2502\n                              \u2502        \u2502              \u25bc\n                              \u2514\u2500\u2500\u25baINACTIVE\u25c4\u2500\u2500\u2500\u2500 re-dispatched\n\n\nHold / ice states (orthogonal to the main flow):\n  INACTIVE  \u2500\u2500\u25ba ON_HOLD  \u2500\u2500\u25ba INACTIVE\n  INACTIVE  \u2500\u2500\u25ba ON_ICE   \u2500\u2500\u25ba INACTIVE   (also reset automatically at cycle boundary)\n\nKILLJOB always sends RUNNING \u2192 TERMINATED \u2192 INACTIVE.",
                "classes": [
                    {
                        "name": "InvalidTransitionError",
                        "doc": "Raised when an event processor handler attempts an illegal state transition.\n\nThis mirrors the error AutoSys logs as::\n\n    CAUAJM_W_50050  Cannot transition job 'extract_sales'\n                    from RUNNING to INACTIVE  (reason: direct reset disallowed)\n\nParameters\n----------\njob_name:\n    The job that was being transitioned.\nfrom_status:\n    Current (illegal source) status.\nto_status:\n    Target (rejected) status.",
                        "methods": [
                            "__init__"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_norm_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "validate_transition",
                        "doc": "Raise ``InvalidTransitionError`` if the transition is illegal.\n\nIf ``force=True`` (used by CHANGE_STATUS events), the transition\nis allowed regardless of the FSM rules."
                    },
                    {
                        "name": "can_transition",
                        "doc": "Return True if the transition is legal (does not raise).\n\nUseful in condition checks before attempting a transition."
                    },
                    {
                        "name": "is_terminal",
                        "doc": "Return True if the status represents a completed run (SUCCESS/FAILURE/TERMINATED)."
                    },
                    {
                        "name": "is_startable",
                        "doc": "Return True if a STARTJOB event is allowed against this status."
                    }
                ],
                "description": "AutoSys job state machine."
            },
            {
                "name": "condition_evaluator.py",
                "type": "Python",
                "path": "autosys/scheduler/condition_evaluator.py",
                "kid_desc": "The Manager checking if the soup is done before cooking steak.",
                "lines": 166,
                "module_doc": "Condition evaluator \u2014 the dependency engine of AutoSys.\n\nThis module is the bridge between the JIL condition language and the\nScheduler ACE's job activation logic.  It answers one question:\n\n    \"Given the current status of all jobs, is this job's condition satisfied?\"\n\nIf yes, the Event Processor transitions the job to STARTING.\nIf no, the event is dropped and the job stays in its current state until a\nfuture status-change event re-triggers the check.\n\nCondition language recap\n------------------------\nConditions are JIL expressions evaluated by the recursive-descent parser in\n``autosys/parser/condition_parser.py``.  This module is a thin wrapper:\n\n    condition_str = \"success(extract_sales) & success(generate_report)\"\n    statuses = {\"extract_sales\": \"SUCCESS\", \"generate_report\": \"FAILURE\"}\n    is_satisfied(condition_str, statuses)  \u2192  False\n\nBuilt-in status predicates:\n    success(job)    \u2192  job.status == SUCCESS\n    failure(job)    \u2192  job.status == FAILURE\n    terminated(job) \u2192  job.status == TERMINATED\n    done(job)       \u2192  job.status in {SUCCESS, FAILURE, TERMINATED}\n    running(job)    \u2192  job.status == RUNNING\n    notrunning(job) \u2192  job.status not in {STARTING, RUNNING}\n\nBoolean operators:\n    &   AND  (higher precedence \u2014 binds tighter than |)\n    |   OR\n\nExamples\n--------\n    \"success(a)\"                                    \u2192  a finished OK\n    \"success(a) & success(b)\"                       \u2192  both finished OK\n    \"success(a) | success(b)\"                       \u2192  at least one OK\n    \"success(a) & (success(b) | failure(b))\"        \u2192  a OK and b is done\n    \"done(extract_sales)\"                           \u2192  extract finished (OK or failed)\n    \"success(box) & value(RUN_DATE) = \"20260625\"\" \u2192  box OK and global var set",
                "classes": [],
                "functions": [
                    {
                        "name": "is_satisfied",
                        "doc": "Return True if *condition_str* is satisfied given the current *job_statuses*.\n\nParameters\n----------\ncondition_str:\n    The raw JIL condition string from the job definition, e.g.\n    ``\"success(extract_sales) & success(generate_report)\"``.\n    ``None`` or empty string means \"no condition\" \u2192 always satisfied.\njob_statuses:\n    A mapping of ``{job_name: status_string}`` for every job currently\n    in the system.  The evaluator looks up job names from this dict.\nglobals_dict:\n    Optional ``{name: value}`` dict of AutoSys global variables.\n    Required for ``value(GLOBAL) = \"x\"`` conditions to work correctly.\ndate_conditions:\n    If True, only consider job statuses for jobs that ran *today*.\n    Any job whose last_run_date != today is treated as INACTIVE for\n    condition evaluation purposes.\ntoday_str:\n    Today's date as \"YYYY-MM-DD\".  Used when date_conditions=True.\n\nReturns\n-------\nbool\n    True  \u2192 condition is met, the job can be activated.\n    False \u2192 condition is not met, the job must keep waiting.\n\nNotes\n-----\nUndefined job names in the condition expression evaluate to False for\nall predicates (a job that doesn't exist is never SUCCESS, FAILURE, etc.).\nThis mirrors real AutoSys behaviour \u2014 a typo in a condition expression\nwill silently prevent the job from ever starting."
                    },
                    {
                        "name": "_ran_today",
                        "doc": "Placeholder: in a full implementation this would check job_runs.run_date.\nFor now we conservatively keep the current status (don't mask it).\nOnly INACTIVE jobs are definitively 'not run today'."
                    },
                    {
                        "name": "build_status_snapshot",
                        "doc": "Build a {job_name: status} snapshot of every job in the DB.\n\nThe Event Processor calls this once per tick so that all condition\nevaluations within a single tick see a consistent snapshot.  This\nmatches how the real AutoSys EPS batches events: it reads the world\nstate at the START of a tick, processes all queued events against that\nsnapshot, then writes back the resulting state changes in one pass.\n\nParameters\n----------\nsession:\n    An open SQLAlchemy Session.\n\nReturns\n-------\ndict[str, str]\n    Maps ``job_name \u2192 status`` for every row in the ``jobs`` table."
                    }
                ],
                "description": "Condition evaluator \u2014 the dependency engine of AutoSys."
            }
        ]
    },
    {
        "id": "autosys_agent",
        "order": 7,
        "title": "7. Agent Execution",
        "description": "Remote machines execute the commands and return exit codes.",
        "kid_title": "7. The Kitchen & Chef (Agent)",
        "kid_description": "The kitchen! The Chef actually cooks the food (runs the computer code) and says if it was a Success or if he burned it.",
        "icon": "ph-terminal-window",
        "files": [
            {
                "name": "runner.py",
                "type": "Python",
                "path": "autosys/agent/runner.py",
                "kid_desc": "The Actual Chef who cooks and returns a number (0=Perfect, 1=Burned).",
                "lines": 222,
                "module_doc": "LocalJobRunner \u2014 executes a single job's command as an OS subprocess.\n\nThis is the core of the System Agent.  In real AutoSys the System Agent is a\nlong-running C daemon (the \"Remote Agent\" process) on each worker machine that\nreceives dispatch requests over TCP and launches the job process.  Here we\nimplement the same contract as a Python class for local execution.\n\nLifecycle\n---------\n1. The ``AgentDispatch`` instantiates a ``LocalJobRunner`` per job.\n2. ``runner.run()`` is called in a **background thread** \u2014 it blocks until\n   the subprocess exits and returns the exit code.\n3. The output-reader thread reads stdout (merged with stderr) line-by-line\n   and calls ``output_callback`` for each line.\n4. ``runner.kill()`` can be called from any thread to SIGTERM \u2192 SIGKILL the\n   running process (used by the KILLJOB event handler).\n\n%%VAR%% expansion\n-----------------\nThe command string is expanded via ``autosys.parser.variable_sub.substitute``\n*before* being handed to the shell.  This includes ``%%DATE%%``, ``%%TIME%%``,\nuser-defined globals, and the ``%%AUTORUN%%`` flag.\n\nPlatform notes\n--------------\nWe use ``shell=True`` so the command string is interpreted by ``/bin/sh``,\nmatching AutoSys's behaviour (it also shells out via the local shell).  This\nmeans commands like ``\"echo hello && ls /tmp\"`` work as expected.",
                "classes": [
                    {
                        "name": "LocalJobRunner",
                        "doc": "Runs one job command locally via ``subprocess.Popen``.\n\nParameters\n----------\ncommand:\n    The shell command to execute (already %%VAR%%-expanded).\njob_name:\n    Used for log messages.\nrun_id:\n    UUID of the ``job_runs`` record for this execution.\nmax_run_secs:\n    If set, the process is killed after this many seconds\n    (derived from ``max_run_alarm`` minutes \u00d7 60).\noutput_callback:\n    Called once per output line with ``(run_id, line_no, stream, content)``.\n    Runs inside the output-reader thread.\n\nAttributes set after ``run()``\n--------------------------------\npid:\n    OS process ID.  ``None`` before the process starts.\nexit_code:\n    The process exit code (0 = success, non-zero = failure, \u22121 if killed).\n    ``None`` before the process finishes.",
                        "methods": [
                            "__init__",
                            "run",
                            "kill",
                            "_read_output"
                        ]
                    }
                ],
                "functions": [],
                "description": "LocalJobRunner \u2014 executes a single job's command as an OS subprocess."
            },
            {
                "name": "server.py",
                "type": "Python",
                "path": "autosys/agent/server.py",
                "kid_desc": "The Kitchen Window listening for Walkie-Talkie orders.",
                "lines": 467,
                "module_doc": "AutoSys System Agent TCP Server (Phase 6).\n\nThis is the daemon that runs on each worker machine and receives dispatch\nrequests from the Scheduler ACE.  In real AutoSys this is a C binary\ncalled the \"Remote Agent\" (cybAgent) that listens on TCP port 7520.\n\nHere we implement the same contract in Python using ``asyncio.start_server``.\nEach incoming connection is handled concurrently; the actual subprocess is\nlaunched in a background thread (via ``LocalJobRunner``) so the server is\nnever blocked waiting for a job to finish.\n\nStartup sequence\n----------------\n1. ``AgentServer.serve_forever()`` binds the TCP socket.\n2. The server registers itself in the ``machines`` table so the Scheduler\n   knows the agent is alive and what address to use.\n3. Each incoming connection is dispatched to ``_handle_client``.\n4. ``_handle_client`` reads one request, routes to the handler, writes\n   the response, and closes the connection.\n\nProtocol\n--------\nNewline-delimited JSON.  See ``autosys/agent/protocol.py``.\n\nDB access\n---------\nThe agent server uses ``sync_session()`` to write run records and output\nlines directly to the shared database.  In Phase 6 both the Scheduler\nand the agent share the same ``AUTOSYS_DB_URL`` (e.g. a PostgreSQL server\naccessible from both machines).  The SQLite URL in tests points to the\nsame file.\n\nStopping the server\n-------------------\nCall ``server.stop()`` from any thread.  The server drains in-flight\nconnections and exits.",
                "classes": [
                    {
                        "name": "AgentServer",
                        "doc": "System Agent TCP server.\n\nParameters\n----------\nmachine_name:\n    The logical name of this agent (e.g. ``\"etl-server-01\"``).\n    Must match the ``machine:`` attribute of jobs that should run here.\nhost:\n    IP address to bind (``\"0.0.0.0\"`` for all interfaces).\nport:\n    TCP port to listen on (default 7520 \u2014 same as real AutoSys).\ndb_url:\n    Override the DB URL (useful for tests).  Defaults to ``AUTOSYS_DB_URL``.",
                        "methods": [
                            "__init__",
                            "stop",
                            "_run_job_thread",
                            "_on_output",
                            "_register_machine",
                            "_update_heartbeat"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_now",
                        "doc": "Return current UTC time (matches schema column defaults)."
                    },
                    {
                        "name": "_exit_code_to_status",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "AutoSys System Agent TCP Server (Phase 6)."
            },
            {
                "name": "remote.py",
                "type": "Python",
                "path": "autosys/agent/remote.py",
                "kid_desc": "A tiny worker robot helping out in the 7. The Kitchen & Chef (Agent) department.",
                "lines": 232,
                "module_doc": "RemoteDispatch \u2014 schedules jobs on remote System Agent servers.\n\nThis module is the scheduler-side counterpart of ``server.py``.  When the\n``AgentDispatch`` router detects that a job's ``machine`` attribute names a\nregistered (non-local) agent, it delegates here.\n\n``RemoteDispatch`` opens a short-lived TCP connection to the agent, sends a\n``DispatchRequest``, and returns.  The agent runs the job in its own thread,\nwrites output and status directly to the shared DB, and the scheduler picks\nup the status change on its next tick \u2014 exactly the same way a local job\nworks, just with a TCP hop in the middle.\n\nWhy synchronous sockets?\n------------------------\nThe event processor's ``process_one_tick`` is a regular synchronous function.\nUsing ``asyncio.run()`` inside it would fail if it is already inside an event\nloop (the ``run_forever`` daemon uses asyncio).  Instead we use Python's\nbuilt-in blocking ``socket`` module:\n\n    - No extra dependencies\n    - Works in any thread context\n    - The agent server handles concurrency on its end\n\nFor Phase 7 (high-throughput, hundreds of jobs/second) you would switch to\nan async connection pool here \u2014 but correctness comes before performance.\n\nMachine lookup\n--------------\n``RemoteDispatch`` reads the ``machines`` table to find ``host:port`` for the\ntarget machine name.  If the machine is not registered, the dispatch is\nskipped and the job stays in STARTING.",
                "classes": [
                    {
                        "name": "RemoteDispatch",
                        "doc": "Dispatches a job to a registered remote System Agent.\n\nParameters\n----------\ntimeout:\n    Socket timeout in seconds for each message.  Real AutoSys uses a\n    configurable timeout (``AGENT_CONNECT_TIMEOUT``); we default to 10s.",
                        "methods": [
                            "__init__",
                            "dispatch",
                            "kill",
                            "heartbeat",
                            "status"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_now",
                        "doc": "Return current time in UTC."
                    },
                    {
                        "name": "_expand_command",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "RemoteDispatch \u2014 schedules jobs on remote System Agent servers."
            },
            {
                "name": "dispatch.py",
                "type": "Python",
                "path": "autosys/agent/dispatch.py",
                "kid_desc": "The Head Chef deciding which stove to use.",
                "lines": 431,
                "module_doc": "AgentDispatch \u2014 the real System Agent dispatcher for Phase 5.\n\nThis module replaces ``_stub_dispatch`` in ``EventProcessor`` when the\n``autosys agent start`` command is used.  Each job is launched as a real\nOS subprocess via ``LocalJobRunner``.\n\nHow it integrates with the Event Processor\n------------------------------------------\nThe ``EventProcessor`` has a ``dispatch_fn`` and a ``kill_fn`` parameter.\nIn Phase 4 (stub) these point to simple functions that immediately flip\nthe job to RUNNING/TERMINATED.  In Phase 5 we pass:\n\n    agent = AgentDispatch()\n    processor = EventProcessor(\n        dispatch_fn = agent.dispatch,\n        kill_fn     = agent.kill,\n    )\n\n``dispatch`` is called synchronously inside the EPS tick when a CMD job\nreaches STARTING.  It:\n  1. Expands %%VAR%% tokens in the command.\n  2. Creates a ``job_runs`` record.\n  3. Sets the job's DB status to RUNNING.\n  4. Launches ``LocalJobRunner.run()`` in a background thread.\n\n``kill`` is called by the KILLJOB handler and signals the running process.\n\nMachine filtering\n-----------------\nReal AutoSys dispatches to the System Agent on ``job.machine`` over TCP.\nIn Phase 5 we only support local execution (localhost + the current\nhostname).  Jobs targeting other machines are SKIPPED and left in\nSTARTING state with a warning.\n\nPhase 6 will add SSH-based remote dispatch so that jobs targeting\n``etl-server-01`` can actually run on that machine.",
                "classes": [
                    {
                        "name": "AgentDispatch",
                        "doc": "Unified dispatcher \u2014 routes to local execution or remote TCP agent.\n\nPhase 5: local_only=True (the default) \u2014 runs jobs on this machine only.\nPhase 6: local_only=False \u2014 looks up non-local machines in the machines\ntable and sends dispatch requests to the registered agent server.\n\nThread-safe: ``dispatch`` and ``kill`` may be called from the event\nprocessor tick while ``_run_job`` threads run concurrently.",
                        "methods": [
                            "__init__",
                            "dispatch",
                            "_dispatch_local",
                            "_dispatch_remote",
                            "kill",
                            "_run_job",
                            "_on_output_line"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_now",
                        "doc": "Return current time in UTC (matches schema column defaults)."
                    },
                    {
                        "name": "_exit_code_to_status",
                        "doc": "Map a subprocess exit code to 'SUCCESS' or 'FAILURE'.\n\nIf max_exit_success is set, any exit code <= max_exit_success is SUCCESS.\nOtherwise only exit code 0 is SUCCESS (AutoSys default)."
                    },
                    {
                        "name": "_is_local_machine",
                        "doc": "Return True if *machine* resolves to the current host.\n\nWe consider a job local if its machine attribute is:\n  - None / empty (JIL field omitted \u2014 defaults to local)\n  - \"localhost\" or \"127.0.0.1\"\n  - The current hostname (from ``socket.gethostname()``)"
                    },
                    {
                        "name": "_expand_command",
                        "doc": "Expand %%VAR%% tokens in the job's command.\n\nResolution order mirrors real AutoSys:\n  1. Built-in vars: %%DATE%%, %%YYYY%%, %%MM%%, %%DD%%, %%TIME%%,\n     %%AUTORUN%%\n  2. User-defined globals (from ``global_variables`` table)\n\nUnknown variables are left unexpanded (``strict=False``) rather than\nraising an error \u2014 the job should still run; undefined vars produce a\nliteral ``%%VAR%%`` in the command which will fail at the shell level\nwith a clear error message."
                    }
                ],
                "description": "AgentDispatch \u2014 the real System Agent dispatcher for Phase 5."
            },
            {
                "name": "protocol.py",
                "type": "Python",
                "path": "autosys/agent/protocol.py",
                "kid_desc": "A tiny worker robot helping out in the 7. The Kitchen & Chef (Agent) department.",
                "lines": 260,
                "module_doc": "AutoSys System Agent wire protocol \u2014 newline-delimited JSON over TCP.\n\nIn real AutoSys the Scheduler ACE communicates with System Agents over TCP\nport 7520 using a proprietary binary format.  We replace that with a simple\nnewline-delimited JSON (\"NDJSON\") protocol that is trivially inspectable with\n``nc`` or ``telnet`` \u2014 making it far more educational.\n\nProtocol mechanics\n------------------\nEach conversation is a single request \u2192 single response exchange over a\nshort-lived TCP connection:\n\n    client  \u2192  {\"type\": \"heartbeat\"}\n\n    server  \u2192  {\"type\": \"alive\", \"machine_name\": \"etl-server-01\"}\n\n    (connection closed)\n\nMessage types\n-------------\nheartbeat         \u2192  alive\nstatus            \u2192  status\ndispatch          \u2192  accepted | rejected\nkill              \u2192  killed\nget_output        \u2192  output\n\nWire format\n-----------\nEach message is a UTF-8-encoded JSON object on a single line (no embedded\nnewlines).  The terminating ``\\n`` is the frame delimiter \u2014 the same format\nused by NDJSON, JSON-RPC over streams, and most modern line-oriented\nprotocols.\n\nUsage\n-----\n    # Sender (scheduler side)\n    from autosys.agent.protocol import send_message\n    resp = send_message(\"etl-server-01\", 7520, HeartbeatRequest())\n    assert resp[\"type\"] == \"alive\"\n\n    # Receiver (agent side)\n    from autosys.agent.protocol import decode_message\n    msg = decode_message(line_bytes)\n    if msg[\"type\"] == \"dispatch\":\n        ...",
                "classes": [
                    {
                        "name": "HeartbeatRequest",
                        "doc": "Ping the agent to verify it is alive.\n\nSent by the Scheduler ACE when a ``CHECK_HEARTBEAT`` event is processed.\nThe agent responds with ``HeartbeatResponse``.  If no response arrives\nwithin the timeout, the Scheduler marks the machine as DOWN and raises\nan alarm.",
                        "methods": []
                    },
                    {
                        "name": "HeartbeatResponse",
                        "doc": "Agent \u2192 Scheduler: I am alive.",
                        "methods": []
                    },
                    {
                        "name": "StatusRequest",
                        "doc": "Request a summary of the agent's current state.\n\nUsed by ``autosys machine check <name>`` to show active jobs, uptime,\nand PID count without modifying any state.",
                        "methods": []
                    },
                    {
                        "name": "StatusResponse",
                        "doc": "Agent \u2192 Scheduler: current state snapshot.",
                        "methods": []
                    },
                    {
                        "name": "DispatchRequest",
                        "doc": "Ask the agent to execute a job command.\n\nSent by the Scheduler ACE when a CMD job is dispatched to a remote\nmachine.  The agent forks the process, updates the DB, and responds\nimmediately with ``DispatchResponse`` (the agent does NOT wait for the\njob to finish before responding).\n\nParameters\n----------\nrun_id:\n    UUID generated by the Scheduler for this execution attempt.\n    Written to ``job_runs.run_id`` so output and history queries\n    can use it as a join key.\njob_name:\n    Used for log messages and DB lookups.\ncommand:\n    Already ``%%VAR%%``-expanded command string (expansion happens on\n    the Scheduler side because the Scheduler owns the globals table).\nmax_run_secs:\n    If set, the agent kills the process after this many seconds\n    (from ``max_run_alarm \u00d7 60`` in the JIL definition).",
                        "methods": []
                    },
                    {
                        "name": "DispatchResponse",
                        "doc": "Agent \u2192 Scheduler: job accepted and process started.",
                        "methods": []
                    },
                    {
                        "name": "DispatchRejected",
                        "doc": "Agent \u2192 Scheduler: could not start the job.",
                        "methods": []
                    },
                    {
                        "name": "KillRequest",
                        "doc": "Ask the agent to terminate a running job.\n\nCorresponds to the KILLJOB event.  The Scheduler sends this after\nsetting the job's status to TERMINATED in the DB; the agent then\nsends SIGTERM (\u2192 SIGKILL after grace period) to the OS process.",
                        "methods": []
                    },
                    {
                        "name": "KillResponse",
                        "doc": "Agent \u2192 Scheduler: kill signal sent (or process not found).",
                        "methods": []
                    },
                    {
                        "name": "GetOutputRequest",
                        "doc": "Request captured output lines for a run.\n\nThe scheduler uses this for ``autosys jobs tail`` on output that was\nstored by a remote agent.  Typically the agent writes output directly\nto the shared DB, so this request is only needed when the DB is not\nshared (Phase 7+ with PostgreSQL or separate SQLite files per agent).",
                        "methods": []
                    },
                    {
                        "name": "GetOutputResponse",
                        "doc": "Agent \u2192 Scheduler: output lines from a single run.",
                        "methods": []
                    }
                ],
                "functions": [
                    {
                        "name": "encode_message",
                        "doc": "Serialize a message to wire bytes (UTF-8 JSON + newline)."
                    },
                    {
                        "name": "encode_dict",
                        "doc": "Serialize a plain dict (for error responses built without a model)."
                    },
                    {
                        "name": "decode_message",
                        "doc": "Deserialize one wire line to a plain dict.\n\nReturns an empty dict with ``type = \"parse_error\"`` if the line is\nnot valid JSON \u2014 the agent logs it and continues serving."
                    },
                    {
                        "name": "send_message",
                        "doc": "Send *message* to an agent server and return the parsed response.\n\nThis is a synchronous blocking call \u2014 suitable for use in the event\nprocessor tick (which is sync) without asyncio complications.\n\nParameters\n----------\nhost, port:\n    Agent server address.\nmessage:\n    Any Pydantic model from this module (e.g. ``HeartbeatRequest()``).\ntimeout:\n    Socket timeout in seconds.\n\nReturns\n-------\ndict\n    The decoded response dict, or ``{\"type\": \"error\", \"reason\": ...}``\n    on connection failure.\n\nRaises\n------\nDoes NOT raise \u2014 connection errors are returned as error dicts so the\ncaller can decide whether to log, alarm, or retry."
                    }
                ],
                "description": "AutoSys System Agent wire protocol \u2014 newline-delimited JSON over TCP."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/agent/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 7. The Kitchen & Chef (Agent) department.",
                "lines": 12,
                "module_doc": "autosys.agent \u2014 System Agent: subprocess dispatch, TCP server, remote routing.",
                "classes": [],
                "functions": [],
                "description": "autosys.agent \u2014 System Agent: subprocess dispatch, TCP server, remote routing."
            }
        ]
    },
    {
        "id": "autosys_wcc",
        "order": 8,
        "title": "8. Web Control Center",
        "description": "Web UI for monitoring the live state of the workflow.",
        "kid_title": "8. The TV Screens (Web UI)",
        "kid_description": "The giant TV screens in the lobby showing customers green checkmarks for finished food.",
        "icon": "ph-browser",
        "files": [
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/wcc/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 8. The TV Screens (Web UI) department.",
                "lines": 0,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [],
                "description": "Python source file (0 lines)"
            },
            {
                "name": "app.py",
                "type": "Python",
                "path": "autosys/wcc/app.py",
                "kid_desc": "The beautiful TV Screen showing all the orders.",
                "lines": 380,
                "module_doc": "WCC \u2014 Workload Control Centre web dashboard (Phase 9).\n\nA separate FastAPI application served on port 8080.  Renders Jinja2 HTML\ntemplates backed by direct DB access (same SQLite as the SSA).\n\nRoutes\n------\nGET  /                     Job grid \u2014 all jobs with live status\nGET  /jobs/{name}          Job detail \u2014 run history + stdout viewer\nGET  /boxes/{name}         Dependency flow graph (D3.js)\nGET  /alarms               Alarm console \u2014 active/resolved alarms\nGET  /api/sse/jobs         Server-Sent Events \u2014 live job status stream\nGET  /api/wcc/jobs         JSON \u2014 job list (used by SSE + D3 refresh)\nGET  /api/wcc/boxes/{name} JSON \u2014 box + children (used by D3 graph)\nGET  /api/wcc/runs         JSON \u2014 run history\nGET  /api/wcc/alarms       JSON \u2014 alarm list",
                "classes": [],
                "functions": [
                    {
                        "name": "_status_cls",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_extract_deps",
                        "doc": "Return job names referenced in a condition expression."
                    },
                    {
                        "name": "_fmt_dt",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_job_dict",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "create_wcc_app",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "WCC \u2014 Workload Control Centre web dashboard (Phase 9)."
            }
        ]
    },
    {
        "id": "autosys_notifications",
        "order": 9,
        "title": "9. Alerts & Alarms",
        "description": "Dispatches notifications if a workflow fails.",
        "kid_title": "9. The Fire Alarms (Alerts)",
        "kid_description": "Loud alarms that go off if the Chef burns the food or the kitchen catches fire!",
        "icon": "ph-bell",
        "files": [
            {
                "name": "dispatcher.py",
                "type": "Python",
                "path": "autosys/notifications/dispatcher.py",
                "kid_desc": "A tiny worker robot helping out in the 9. The Fire Alarms (Alerts) department.",
                "lines": 279,
                "module_doc": "Dispatcher \u2014 sends alarm payloads to configured notification channels.\n\nChannels\n--------\nNSM webhook     POST {alarm_id, job_name, alarm_type, message, ts}\nEmail (SMTP)    Plain-text message via smtplib\nSlack           Incoming-webhook block-kit message\nPagerDuty       Events API v2 trigger\n\nAll channels are optional.  If the relevant env-var is absent the channel\nis silently skipped.  Failures on individual channels are logged but do\nnot prevent the other channels from being tried.\n\nUsage\n-----\n    from autosys.notifications.dispatcher import Dispatcher\n    from autosys.notifications.config import NotificationConfig\n\n    cfg = NotificationConfig()\n    d = Dispatcher(cfg)\n    n_sent = d.send(alarm_row)",
                "classes": [
                    {
                        "name": "Dispatcher",
                        "doc": "Sends a single AlarmRow to all enabled notification channels.\n\nParameters\n----------\nconfig:\n    ``NotificationConfig`` instance.  If ``None``, a fresh one is\n    constructed (reads from environment).\nhttp_post_fn:\n    Injectable for tests \u2014 replaces ``urllib.request.urlopen`` calls.\n    Signature: ``(url: str, payload: dict) -> bool``\nsmtp_send_fn:\n    Injectable for tests \u2014 replaces the SMTP send logic.\n    Signature: ``(config, subject: str, body: str) -> bool``",
                        "methods": [
                            "__init__",
                            "send",
                            "_send_nsm",
                            "_send_email",
                            "_send_slack",
                            "_send_pagerduty",
                            "_build_payload"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_default_http_post",
                        "doc": "POST a JSON payload to *url*.  Returns True on 2xx, False otherwise."
                    },
                    {
                        "name": "_default_smtp_send",
                        "doc": "Send a plain-text email using smtplib."
                    }
                ],
                "description": "Dispatcher \u2014 sends alarm payloads to configured notification channels."
            },
            {
                "name": "config.py",
                "type": "Python",
                "path": "autosys/notifications/config.py",
                "kid_desc": "A tiny worker robot helping out in the 9. The Fire Alarms (Alerts) department.",
                "lines": 56,
                "module_doc": "Notification channel configuration \u2014 Phase 10.\n\nAll settings are read from environment variables so that no credentials\nare ever baked into source code.  Each channel is silently disabled when\nits required env-var is absent.\n\nEnv vars\n--------\nNSM_WEBHOOK_URL          URL of an NSM/webhook receiver.  JSON POST.\nSMTP_HOST                SMTP relay host (e.g. \"smtp.example.com\").\nSMTP_PORT                SMTP port (default: 25).\nSMTP_FROM                Sender address (e.g. \"autosys@example.com\").\nSMTP_TO                  Comma-separated recipient(s).\nSMTP_USE_TLS             \"true\" to enable STARTTLS (default: false).\nSLACK_WEBHOOK_URL        Slack incoming-webhook URL.\nPD_ROUTING_KEY           PagerDuty Events API v2 routing/integration key.",
                "classes": [
                    {
                        "name": "NotificationConfig",
                        "doc": "Reads all notification configuration from the process environment.\n\nAll attributes default to ``None`` / sensible fallback so the\ndispatcher can check ``if cfg.nsm_url`` before attempting a send.",
                        "methods": [
                            "__init__",
                            "any_enabled"
                        ]
                    }
                ],
                "functions": [],
                "description": "Notification channel configuration \u2014 Phase 10."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/notifications/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 9. The Fire Alarms (Alerts) department.",
                "lines": 0,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [],
                "description": "Python source file (0 lines)"
            },
            {
                "name": "alarm_manager.py",
                "type": "Python",
                "path": "autosys/notifications/alarm_manager.py",
                "kid_desc": "The Fire Alarm that rings if a job fails.",
                "lines": 301,
                "module_doc": "AlarmManager \u2014 evaluates alarm conditions and writes AlarmRow records.\n\nCalled at the end of each EPS tick via ``EventProcessor.process_one_tick``.\nIt checks the current DB state and:\n\n1. ALARM_IF_FAIL       \u2014 job is FAILURE + alarm_if_fail=True \u2192 raise alarm\n2. ALARM_IF_TERMINATED \u2014 job is TERMINATED + alarm_if_terminated=True \u2192 raise alarm\n3. MAX_RUN_ALARM       \u2014 job is RUNNING > max_run_alarm minutes \u2192 raise alarm\n4. MIN_RUN_ALARM       \u2014 job just finished < min_run_alarm minutes \u2192 raise alarm\n5. HEARTBEAT_FAIL      \u2014 machine is DOWN (checked via MachineRow) \u2192 raise alarm\n6. Deduplication       \u2014 if an identical active alarm exists, skip it\n7. Auto-resolve        \u2014 if job goes SUCCESS and has an active ALARM_IF_FAIL alarm,\n                         clear it automatically",
                "classes": [
                    {
                        "name": "AlarmManager",
                        "doc": "Evaluates alarm conditions for all jobs after an EPS tick.\n\nParameters\n----------\nnow_fn:\n    Callable that returns the current datetime.  Injectable for tests.",
                        "methods": [
                            "__init__",
                            "evaluate",
                            "_check_job",
                            "_check_min_run",
                            "_check_machines",
                            "_auto_resolve",
                            "_has_active_alarm",
                            "_raise_alarm"
                        ]
                    }
                ],
                "functions": [],
                "description": "AlarmManager \u2014 evaluates alarm conditions and writes AlarmRow records."
            }
        ]
    },
    {
        "id": "tests",
        "order": 10,
        "title": "10. Validation (Tests)",
        "description": "Test cases validating the end-to-end correctness of the workflow.",
        "kid_title": "10. The Health Inspector (Tests)",
        "kid_description": "The health inspector comes in to test the whole restaurant and make sure nobody gets sick.",
        "icon": "ph-check-circle",
        "files": [
            {
                "name": "test_phase10.py",
                "type": "Python",
                "path": "tests/test_phase10.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 584,
                "module_doc": "Phase 10 \u2014 Alarms, Notifications & NSM  (~37 tests).\n\nTestAlarmManagerFailure      \u2014 ALARM_IF_FAIL logic\nTestAlarmManagerTerminated   \u2014 ALARM_IF_TERMINATED logic\nTestAlarmManagerMaxRun       \u2014 MAX_RUN_ALARM logic\nTestAlarmManagerMinRun       \u2014 MIN_RUN_ALARM logic\nTestAlarmManagerMachineDown  \u2014 HEARTBEAT_FAIL for jobs on downed machines\nTestAlarmManagerDedup        \u2014 identical alarm not raised twice\nTestAlarmManagerAutoResolve  \u2014 auto-clear when job recovers to SUCCESS\nTestDispatcher               \u2014 pluggable channel delivery\nTestEPSIntegration           \u2014 alarms raised through the full EPS tick",
                "classes": [
                    {
                        "name": "TestAlarmManagerFailure",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_failure_alarm_raised_when_flag_set",
                            "test_failure_alarm_message_contains_job_name",
                            "test_failure_alarm_job_status_recorded",
                            "test_no_alarm_when_alarm_if_fail_false",
                            "test_no_alarm_for_inactive_job",
                            "test_no_alarm_for_success_job",
                            "test_alarm_not_raised_if_notified_is_false_by_default"
                        ]
                    },
                    {
                        "name": "TestAlarmManagerTerminated",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_terminated_alarm_raised",
                            "test_no_alarm_when_flag_false",
                            "test_terminated_alarm_status_recorded"
                        ]
                    },
                    {
                        "name": "TestAlarmManagerMaxRun",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_max_run_alarm_raised_after_threshold",
                            "test_no_max_run_alarm_within_threshold",
                            "test_no_max_run_alarm_if_no_attribute",
                            "test_max_run_alarm_message_contains_minutes"
                        ]
                    },
                    {
                        "name": "TestAlarmManagerMinRun",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_min_run_alarm_raised_for_short_run",
                            "test_no_min_run_alarm_for_normal_duration",
                            "test_no_min_run_alarm_old_run",
                            "test_min_run_alarm_has_run_id"
                        ]
                    },
                    {
                        "name": "TestAlarmManagerMachineDown",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_heartbeat_fail_raised_for_running_job_on_down_machine",
                            "test_heartbeat_fail_not_raised_for_up_machine",
                            "test_heartbeat_fail_not_raised_if_no_running_jobs",
                            "test_heartbeat_fail_message_contains_machine"
                        ]
                    },
                    {
                        "name": "TestAlarmManagerDedup",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_second_failure_alarm_not_created",
                            "test_second_max_run_alarm_not_created",
                            "test_different_alarm_types_not_deduplicated"
                        ]
                    },
                    {
                        "name": "TestAlarmManagerAutoResolve",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_alarm_auto_cleared_on_success",
                            "test_auto_resolve_sets_cleared_by_auto",
                            "test_no_auto_resolve_for_still_failing_job"
                        ]
                    },
                    {
                        "name": "TestDispatcher",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_mock_dispatcher",
                            "_make_alarm_row",
                            "test_send_nsm_webhook",
                            "test_send_slack_webhook",
                            "test_send_pagerduty",
                            "test_send_multiple_channels",
                            "test_notified_flag_set_on_success",
                            "test_notified_stays_false_when_no_channels",
                            "test_channel_failure_does_not_block_others",
                            "test_pagerduty_severity_error_for_failure",
                            "test_pagerduty_severity_critical_for_heartbeat",
                            "test_smtp_send_fn_called"
                        ]
                    },
                    {
                        "name": "TestEPSIntegration",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_make_eps",
                            "test_eps_raises_alarm_on_failure",
                            "test_eps_no_alarm_without_alarm_manager",
                            "test_eps_calls_dispatcher_for_new_alarm"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "session",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "am",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_make_job",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_make_run",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_make_machine",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_active_alarms",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Phase 10 \u2014 Alarms, Notifications & NSM  (~37 tests)."
            },
            {
                "name": "test_phase8.py",
                "type": "Python",
                "path": "tests/test_phase8.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 671,
                "module_doc": "Phase 8 \u2014 REST API Application Server (SSA)\n============================================\n\nTests cover every router and the key behaviours of the App Server:\n\n  TestHealthEndpoints       \u2014 /health + /health/ready\n  TestJobsRouter            \u2014 GET /jobs, GET /jobs/{name}, DELETE, sendevent\n  TestEventsRouter          \u2014 GET /events, GET /events/history\n  TestRunsRouter            \u2014 GET /runs, GET /runs/{run_id}, output\n  TestMachinesRouter        \u2014 GET/POST/DELETE /machines, heartbeat\n  TestGlobalsRouter         \u2014 GET/PUT/DELETE /globals\n  TestAuthRouter            \u2014 POST /auth/token, JWT validation\n  TestWebSocket             \u2014 /ws/events broadcast\n  TestCLISchedulerServe     \u2014 autosys scheduler serve --help\n  TestEventBroadcaster      \u2014 unit tests for the broadcaster class\n  TestStatusChangeBroadcast \u2014 EPS emits status-change events\n\nAll tests use FastAPI's TestClient (sync) via an isolated SQLite DB fixture.\nAuth is disabled (AUTOSYS_AUTH_ENABLED=false, the default) so no tokens needed.",
                "classes": [
                    {
                        "name": "TestHealthEndpoints",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_health_live_returns_200",
                            "test_health_live_body",
                            "test_health_ready_returns_200",
                            "test_health_ready_db_connected"
                        ]
                    },
                    {
                        "name": "TestJobsRouter",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_list_jobs_empty",
                            "test_list_jobs_returns_all",
                            "test_list_jobs_filter_by_status",
                            "test_list_jobs_filter_by_pattern",
                            "test_get_job_returns_detail",
                            "test_get_job_not_found",
                            "test_get_job_response_has_status",
                            "test_delete_job",
                            "test_delete_job_not_found",
                            "test_sendevent_startjob",
                            "test_sendevent_killjob",
                            "test_sendevent_unknown_type_returns_400",
                            "test_sendevent_creates_queue_entry",
                            "test_sendevent_force_startjob",
                            "test_list_jobs_filter_by_box"
                        ]
                    },
                    {
                        "name": "TestEventsRouter",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_list_events_empty",
                            "test_list_events_shows_pending",
                            "test_list_events_has_job_name",
                            "test_event_history_empty",
                            "test_event_history_shows_processed"
                        ]
                    },
                    {
                        "name": "TestRunsRouter",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_list_runs_empty",
                            "test_list_runs_shows_runs",
                            "test_list_runs_filter_by_job",
                            "test_get_run_not_found",
                            "test_get_run_by_id",
                            "test_get_run_output_empty",
                            "test_get_run_output_with_lines",
                            "test_get_run_output_not_found",
                            "test_run_has_duration_when_finished"
                        ]
                    },
                    {
                        "name": "TestMachinesRouter",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_list_machines_returns_200",
                            "test_register_machine",
                            "test_register_machine_idempotent",
                            "test_get_machine",
                            "test_get_machine_not_found",
                            "test_list_machines_after_register",
                            "test_delete_machine",
                            "test_delete_machine_not_found",
                            "test_machine_has_status_field"
                        ]
                    },
                    {
                        "name": "TestGlobalsRouter",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_list_globals_empty",
                            "test_set_global",
                            "test_get_global",
                            "test_get_global_not_found",
                            "test_update_global",
                            "test_delete_global",
                            "test_delete_global_not_found",
                            "test_list_globals_after_set"
                        ]
                    },
                    {
                        "name": "TestAuthRouter",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_login_valid_credentials",
                            "test_login_invalid_credentials",
                            "test_token_encode_decode_roundtrip",
                            "test_expired_token_rejected",
                            "test_tampered_token_rejected",
                            "test_auth_disabled_allows_access_without_token"
                        ]
                    },
                    {
                        "name": "TestEventBroadcaster",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_connection_count_starts_zero",
                            "test_publish_sync_no_loop_does_not_raise",
                            "test_publish_async_no_connections"
                        ]
                    },
                    {
                        "name": "TestStatusChangeBroadcast",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_on_status_change_called_on_startjob",
                            "test_on_status_change_called_on_killjob",
                            "test_emit_not_called_when_status_unchanged"
                        ]
                    },
                    {
                        "name": "TestCLISchedulerServe",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_serve_help_exits_0",
                            "test_serve_help_shows_port_option",
                            "test_serve_help_shows_poll_interval"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "client",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "session",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_cmd",
                        "doc": "Insert a minimal CMD job row directly."
                    },
                    {
                        "name": "_seed_box",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Phase 8 \u2014 REST API Application Server (SSA)"
            },
            {
                "name": "test_phase9.py",
                "type": "Python",
                "path": "tests/test_phase9.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 459,
                "module_doc": "Phase 9 \u2014 WCC Web Dashboard tests (~40 tests).\n\nTestAlarmAPI        \u2014 GET/POST /api/v1/alarms via the SSA\nTestWCCPages        \u2014 HTML page routes of the WCC app\nTestWCCJsonAPI      \u2014 JSON data routes of the WCC app\nTestExtractDeps     \u2014 _extract_deps() helper unit tests",
                "classes": [
                    {
                        "name": "TestAlarmAPI",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_list_alarms_empty",
                            "test_list_alarms_returns_active",
                            "test_list_alarms_filter_active_true",
                            "test_list_alarms_filter_active_false",
                            "test_list_alarms_filter_by_job",
                            "test_resolve_alarm_success",
                            "test_resolve_alarm_not_found",
                            "test_resolve_alarm_already_resolved",
                            "test_raise_alarm_via_post",
                            "test_alarm_response_schema"
                        ]
                    },
                    {
                        "name": "TestWCCPages",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_job_grid_empty_db",
                            "test_job_grid_shows_imported_jobs",
                            "test_job_grid_status_filter_inactive",
                            "test_job_grid_filter_no_match",
                            "test_job_grid_search_filter",
                            "test_job_grid_has_nav",
                            "test_job_detail_cmd_job",
                            "test_job_detail_box_shows_children",
                            "test_job_detail_box_has_graph_link",
                            "test_job_detail_not_found",
                            "test_box_graph_page_renders",
                            "test_box_graph_has_d3_script",
                            "test_box_graph_nodes_json_embedded",
                            "test_box_graph_edges_json_embedded",
                            "test_box_graph_not_found",
                            "test_alarm_console_all_clear",
                            "test_alarm_console_shows_active",
                            "test_alarm_console_filter_active"
                        ]
                    },
                    {
                        "name": "TestWCCJsonAPI",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_api_jobs_empty",
                            "test_api_jobs_returns_all",
                            "test_api_jobs_has_status_class",
                            "test_api_jobs_filter_running",
                            "test_api_box_nodes_edges",
                            "test_api_box_edges_correct",
                            "test_api_box_missing_returns_error",
                            "test_api_runs_empty",
                            "test_api_alarms_empty",
                            "test_api_alarms_active_count",
                            "test_api_alarms_filter_active",
                            "test_api_run_output_empty"
                        ]
                    },
                    {
                        "name": "TestExtractDeps",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_deps",
                            "test_none_returns_empty",
                            "test_empty_returns_empty",
                            "test_single_success",
                            "test_shorthand_s",
                            "test_compound_and",
                            "test_compound_or",
                            "test_failure_shorthand",
                            "test_done_shorthand",
                            "test_no_valid_predicates"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "ssa_client",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "wcc_client",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_import",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_alarm",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Phase 9 \u2014 WCC Web Dashboard tests (~40 tests)."
            },
            {
                "name": "test_phase11.py",
                "type": "Python",
                "path": "tests/test_phase11.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 90,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [
                    {
                        "name": "fresh_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "test_calendar_repo_crud",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "test_is_triggered_with_run_calendar",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "test_is_triggered_with_exclude_calendar",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Python source file (90 lines)"
            },
            {
                "name": "test_phase6.py",
                "type": "Python",
                "path": "tests/test_phase6.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 563,
                "module_doc": "Phase 6 test suite \u2014 Remote dispatch, TCP Agent Server, Machine Registry.\n\nCoverage\n--------\n1.  Protocol        \u2014 encode/decode, send_message error handling\n2.  AgentServer     \u2014 heartbeat, status, dispatch, kill, get_output\n3.  RemoteDispatch  \u2014 dispatch to running server, job reaches SUCCESS\n4.  AgentDispatch   \u2014 local/remote routing (local_only=False)\n5.  MachineRepository \u2014 register, list, get, update_heartbeat, set_status\n6.  EventProcessor  \u2014 CHECK_HEARTBEAT marks machine UP/DOWN\n7.  CLI machine     \u2014 register, list, check (against live server)\n8.  CLI agent serve \u2014 smoke test (starts server in thread)\n\nTest infrastructure\n-------------------\nThe ``agent_server`` fixture starts a real ``AgentServer`` in a background\nthread (its own asyncio event loop) and tears it down after each test.\nTests connect to it via ``send_message(host, port, ...)`` or via the full\nCLI pipeline.",
                "classes": [
                    {
                        "name": "_ServerHandle",
                        "doc": "Bundles the server object + thread + host/port for easy use in tests.",
                        "methods": [
                            "__init__"
                        ]
                    },
                    {
                        "name": "TestProtocol",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_encode_decode_heartbeat",
                            "test_encode_decode_dispatch_request",
                            "test_decode_invalid_json_returns_error",
                            "test_send_message_connection_refused_returns_error",
                            "test_send_message_timeout_returns_error"
                        ]
                    },
                    {
                        "name": "TestAgentServer",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_heartbeat_returns_alive",
                            "test_status_returns_status",
                            "test_dispatch_returns_accepted",
                            "test_dispatch_sets_job_running_then_success",
                            "test_dispatch_failure_job",
                            "test_dispatch_captures_output",
                            "test_kill_running_job",
                            "test_kill_nonexistent_job_found_false",
                            "test_dispatch_run_history_written",
                            "test_server_registers_machine_in_db",
                            "test_heartbeat_updates_last_heartbeat",
                            "test_unknown_message_type_returns_error"
                        ]
                    },
                    {
                        "name": "TestRemoteDispatch",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_heartbeat_returns_true",
                            "test_heartbeat_unreachable_returns_false",
                            "test_status_returns_dict",
                            "test_dispatch_to_remote_server"
                        ]
                    },
                    {
                        "name": "TestAgentDispatchRouting",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_local_job_runs_locally",
                            "test_remote_job_dispatched_to_server"
                        ]
                    },
                    {
                        "name": "TestMachineRepository",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_register_creates_row",
                            "test_register_upserts",
                            "test_list_all",
                            "test_get_returns_none_for_unknown",
                            "test_update_heartbeat_sets_timestamp",
                            "test_set_status_down",
                            "test_default_status_unknown",
                            "test_default_port_7520"
                        ]
                    },
                    {
                        "name": "TestCheckHeartbeat",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_check_heartbeat_marks_up",
                            "test_check_heartbeat_marks_down_on_unreachable",
                            "test_check_heartbeat_unknown_machine_is_noop"
                        ]
                    },
                    {
                        "name": "TestCLIMachineCommands",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_register_exit_0",
                            "test_register_shows_confirmation",
                            "test_list_exit_0",
                            "test_list_no_machines_message",
                            "test_list_shows_registered_machine",
                            "test_check_unknown_machine_exits_nonzero",
                            "test_check_live_agent_exits_0",
                            "test_check_dead_agent_exits_nonzero"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_free_port",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_cmd",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_get_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_set_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_wait_for_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_enqueue",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "agent_server",
                        "doc": "Start an AgentServer on a free port in a daemon thread.\n\nYields a _ServerHandle.  The server is stopped after the test."
                    }
                ],
                "description": "Phase 6 test suite \u2014 Remote dispatch, TCP Agent Server, Machine Registry."
            },
            {
                "name": "test_phase2.py",
                "type": "Python",
                "path": "tests/test_phase2.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 728,
                "module_doc": "Phase 2 test suite \u2014 JIL Parser pipeline.\n\nCoverage\n--------\n1.  Lexer          \u2014 comment stripping, stanza header, attribute lines,\n                     quoted values, multi-word command values\n2.  JIL Parser     \u2014 single/multi-stanza, all job types, attribute coercion,\n                     delete_job, unknown attributes forwarded cleanly\n3.  Condition      \u2014 tokeniser, AST shapes, precedence, evaluate(), round-trip\n                     condition_to_str(), list_job_dependencies()\n4.  Variable sub   \u2014 builtins, user globals, strict/lenient mode, batch sub\n5.  End-to-end     \u2014 parse the real demo_etl.jil from examples/ and assert\n                     every job's attributes are correct",
                "classes": [
                    {
                        "name": "TestStripComments",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_removes_single_line_block_comment",
                            "test_preserves_newlines_inside_comment",
                            "test_inline_comment_replaced_with_spaces"
                        ]
                    },
                    {
                        "name": "TestLexer",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_simple_box_stanza",
                            "test_directive_value_is_insert_job",
                            "test_job_name_token",
                            "test_inline_attribute_on_header",
                            "test_attribute_line_emits_two_tokens",
                            "test_quoted_value_strips_quotes",
                            "test_command_value_preserves_spaces",
                            "test_condition_value_is_whole_expression",
                            "test_days_of_week_comma_list_is_single_value",
                            "test_line_numbers_are_tracked",
                            "test_eof_is_last_token",
                            "test_comment_only_lines_produce_no_tokens",
                            "test_multiple_stanzas",
                            "test_update_job_directive",
                            "test_delete_job_directive"
                        ]
                    },
                    {
                        "name": "TestJILParser",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_parse_box_job",
                            "test_parse_cmd_job",
                            "test_parse_multiple_stanzas",
                            "test_delete_job_produces_partial_model",
                            "test_update_job_op_code",
                            "test_bool_coercion_alarm_if_fail",
                            "test_bool_coercion_zero_is_false",
                            "test_int_coercion_n_retrys",
                            "test_raw_attrs_preserved",
                            "test_days_of_week_all_expands",
                            "test_days_of_week_weekdays",
                            "test_source_line_is_recorded",
                            "test_jobs_from_jil_shortcut",
                            "test_parse_jil_file",
                            "test_parallel_jobs_share_box"
                        ]
                    },
                    {
                        "name": "TestConditionAST",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_simple_success",
                            "test_simple_failure",
                            "test_done_predicate",
                            "test_notrunning_predicate",
                            "test_terminated_predicate",
                            "test_activated_predicate",
                            "test_and_expression",
                            "test_or_expression",
                            "test_and_has_higher_precedence_than_or",
                            "test_parentheses_override_precedence",
                            "test_value_condition_equals",
                            "test_value_condition_not_equals",
                            "test_complex_condition",
                            "test_nested_parens",
                            "test_syntax_error_raises",
                            "test_syntax_error_empty_parens"
                        ]
                    },
                    {
                        "name": "TestConditionEvaluate",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_success_true_when_job_succeeded",
                            "test_success_false_when_job_running",
                            "test_failure_true_when_job_failed",
                            "test_done_true_for_success",
                            "test_done_true_for_failure",
                            "test_done_false_for_running",
                            "test_notrunning_true_for_inactive",
                            "test_notrunning_false_for_running",
                            "test_and_both_true",
                            "test_and_one_false",
                            "test_or_one_true",
                            "test_or_both_false",
                            "test_missing_job_treated_as_inactive",
                            "test_value_condition_equals_true",
                            "test_value_condition_equals_false",
                            "test_value_condition_not_equals",
                            "test_complex_fan_in_all_success",
                            "test_complex_fan_in_one_not_done"
                        ]
                    },
                    {
                        "name": "TestConditionHelpers",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_condition_to_str_simple",
                            "test_condition_to_str_and",
                            "test_condition_to_str_or",
                            "test_condition_to_str_value",
                            "test_list_job_dependencies_simple",
                            "test_list_job_dependencies_deduplicates",
                            "test_list_job_dependencies_value_node_not_included"
                        ]
                    },
                    {
                        "name": "TestBuildBuiltins",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_date_format",
                            "test_yyyy_mm_dd",
                            "test_time_format",
                            "test_autorun_y",
                            "test_autorun_n"
                        ]
                    },
                    {
                        "name": "TestSubstitute",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_date_substitution",
                            "test_yyyy_mm_dd_substitution",
                            "test_user_global_substitution",
                            "test_global_name_case_insensitive",
                            "test_strict_raises_on_undefined",
                            "test_lenient_leaves_token_unchanged",
                            "test_no_tokens_returns_unchanged",
                            "test_multiple_tokens_in_one_string",
                            "test_list_variables",
                            "test_list_variables_deduplicates",
                            "test_substitute_job_attrs_expands_command",
                            "test_substitute_job_attrs_does_not_mutate_input"
                        ]
                    },
                    {
                        "name": "TestDemoEtlJIL",
                        "doc": "Parse the actual demo_etl.jil from examples/ and assert every attribute\nof every job matches what the file says.\n\nThis is the most important test in Phase 2 \u2014 it proves the full pipeline\nworks on a realistic real-world JIL file.",
                        "methods": [
                            "ops",
                            "jobs_by_name",
                            "test_parses_7_jobs",
                            "test_all_ops_are_insert",
                            "test_box_job_present",
                            "test_box_job_type",
                            "test_box_start_time",
                            "test_box_days_of_week",
                            "test_box_exclude_calendar",
                            "test_box_alarm_if_fail",
                            "test_box_max_run_alarm",
                            "test_check_source_ready_type",
                            "test_check_source_ready_box",
                            "test_check_source_ready_command_has_date_var",
                            "test_check_source_ready_machine",
                            "test_check_source_ready_n_retrys",
                            "test_extract_sales_condition",
                            "test_generate_report_condition",
                            "test_load_to_warehouse_condition",
                            "test_load_to_warehouse_different_machine",
                            "test_send_success_email_condition",
                            "test_send_success_email_alarm_off",
                            "test_nightly_cleanup_present",
                            "test_nightly_cleanup_has_no_box",
                            "test_nightly_cleanup_runs_all_days",
                            "test_nightly_cleanup_start_time",
                            "test_send_email_condition_parseable",
                            "test_check_command_date_substitution",
                            "test_extract_command_date_substitution"
                        ]
                    }
                ],
                "functions": [],
                "description": "Phase 2 test suite \u2014 JIL Parser pipeline."
            },
            {
                "name": "test_phase3.py",
                "type": "Python",
                "path": "tests/test_phase3.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 632,
                "module_doc": "Phase 3 test suite \u2014 CLI commands and DB repository layer.\n\nCoverage\n--------\n1.  JobRepository    \u2014 upsert (insert / update), get, delete, list, pattern\n2.  EventRepository  \u2014 enqueue, dequeue_pending, mark_processed\n3.  GlobalVarRepository \u2014 set, get, as_dict\n4.  JIL writer       \u2014 job_to_jil, jobs_to_jil round-trip\n5.  CLI: jil import  \u2014 full file, dry-run, quiet, second import = update\n6.  CLI: jil export  \u2014 single job, --all\n7.  CLI: jil validate \u2014 valid file, parse error exit code\n8.  CLI: sendevent   \u2014 STARTJOB, SET_GLOBAL, missing -J error, missing job warning\n9.  CLI: autorep     \u2014 single job, %, TSV quiet mode",
                "classes": [
                    {
                        "name": "TestJobRepository",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_cmd_job",
                            "_box_job",
                            "test_insert_returns_inserted",
                            "test_second_upsert_returns_updated",
                            "test_get_returns_job_after_insert",
                            "test_get_returns_none_for_unknown",
                            "test_delete_returns_true_when_found",
                            "test_delete_returns_false_when_missing",
                            "test_list_all_returns_all_jobs",
                            "test_list_by_pattern_wildcard",
                            "test_update_preserves_status",
                            "test_days_of_week_list_round_trips",
                            "test_start_times_list_round_trips"
                        ]
                    },
                    {
                        "name": "TestEventRepository",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_event",
                            "test_enqueue_returns_uuid",
                            "test_dequeue_pending_returns_event",
                            "test_mark_processed_removes_from_pending",
                            "test_enqueue_writes_to_history",
                            "test_set_global_event_has_global_name"
                        ]
                    },
                    {
                        "name": "TestGlobalVarRepository",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_set_and_get",
                            "test_name_uppercased",
                            "test_second_set_updates_value",
                            "test_get_missing_returns_none",
                            "test_as_dict"
                        ]
                    },
                    {
                        "name": "TestJILWriter",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_box_job_header",
                            "test_cmd_job_header",
                            "test_command_emitted",
                            "test_machine_emitted",
                            "test_boolean_true_emitted_as_1",
                            "test_boolean_false_default_not_emitted",
                            "test_int_default_not_emitted",
                            "test_nondefault_int_emitted",
                            "test_days_of_week_comma_joined",
                            "test_start_times_quoted",
                            "test_update_op",
                            "test_jobs_to_jil_multiple",
                            "test_jobs_to_jil_round_trip_parseable",
                            "test_none_attrs_not_emitted"
                        ]
                    },
                    {
                        "name": "TestCLIJilImport",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_import_demo_jil_exit_0",
                            "test_import_reports_7_jobs",
                            "test_import_shows_inserted",
                            "test_import_second_time_shows_updated",
                            "test_import_dry_run_does_not_persist",
                            "test_import_dry_run_output_says_validation_ok",
                            "test_import_quiet_flag_no_per_job_lines",
                            "test_import_persists_to_db",
                            "test_import_cmd_job_command_preserved"
                        ]
                    },
                    {
                        "name": "TestCLIJilExport",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_export_single_job",
                            "test_export_missing_job_exits_nonzero",
                            "test_export_all_includes_all_jobs",
                            "test_export_contains_condition"
                        ]
                    },
                    {
                        "name": "TestCLIJilValidate",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_validate_valid_file_exits_0",
                            "test_validate_output_says_valid",
                            "test_validate_shows_ok_per_job",
                            "test_validate_does_not_write_to_db",
                            "test_validate_invalid_file_exits_nonzero"
                        ]
                    },
                    {
                        "name": "TestCLISendEvent",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_startjob_exit_0",
                            "test_startjob_shows_event_queued",
                            "test_startjob_shows_event_id",
                            "test_startjob_missing_j_exits_nonzero",
                            "test_startjob_enqueues_in_db",
                            "test_set_global_enqueues",
                            "test_set_global_missing_g_exits_nonzero",
                            "test_unknown_job_shows_warning_not_error",
                            "test_change_status_requires_s"
                        ]
                    },
                    {
                        "name": "TestCLIAutorep",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_autorep_single_job",
                            "test_autorep_all_shows_7_jobs",
                            "test_autorep_status_code_shown",
                            "test_autorep_no_match_exits_0",
                            "test_autorep_wildcard_pattern",
                            "test_autorep_quiet_tsv"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "Each test gets its own fresh SQLite DB so tests don't interfere.\n\nSets AUTOSYS_DB_URL to a temp file, resets the engine cache so the\nnew URL is picked up immediately, and creates the schema."
                    }
                ],
                "description": "Phase 3 test suite \u2014 CLI commands and DB repository layer."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "tests/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 0,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [],
                "description": "Python source file (0 lines)"
            },
            {
                "name": "test_phase7.py",
                "type": "Python",
                "path": "tests/test_phase7.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 768,
                "module_doc": "Phase 7 test suite \u2014 Box Orchestration, insert_machine JIL, MachineDef.\n\nCoverage\n--------\n1.  MachineDef model         \u2014 validation, host defaulting\n2.  JIL lexer                \u2014 insert_machine tokenised as DIRECTIVE\n3.  JIL parser               \u2014 insert_machine parsed as JILOperation(machine=...)\n4.  JIL writer               \u2014 machine_to_jil output\n5.  jil import (CLI)         \u2014 insert_machine stanzas upsert machines table\n6.  BoxManager               \u2014 activate children, complete box, kill children, reset\n7.  JobRepository            \u2014 get_children, get_running_boxes, get_box_row\n8.  EventProcessor           \u2014 box tick wired, FORCE_STARTJOB resets children,\n                               KILLJOB kills children, box completes automatically\n9.  CLI box status           \u2014 renders table\n10. CLI box tree             \u2014 renders tree\n11. Full pipeline            \u2014 import JIL with box + children, STARTJOB box,\n                               tick until box SUCCESS",
                "classes": [
                    {
                        "name": "TestMachineDef",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_basic_fields",
                            "test_host_defaults_to_machine_name",
                            "test_port_defaults_to_7520",
                            "test_type_defaults_to_a",
                            "test_max_load_defaults_to_100",
                            "test_port_validation_range",
                            "test_description_optional",
                            "test_explicit_host_not_overridden"
                        ]
                    },
                    {
                        "name": "TestLexerInsertMachine",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_insert_machine_tokenised_as_directive",
                            "test_machine_name_is_job_name_token",
                            "test_machine_with_port_attribute"
                        ]
                    },
                    {
                        "name": "TestParserInsertMachine",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_parse",
                            "test_insert_machine_produces_machine_op",
                            "test_machine_name_parsed",
                            "test_port_coerced_to_int",
                            "test_max_load_coerced_to_int",
                            "test_host_parsed",
                            "test_host_defaults_when_omitted",
                            "test_mixed_job_and_machine_stanzas",
                            "test_raw_attrs_preserved"
                        ]
                    },
                    {
                        "name": "TestMachineToJil",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_contains_insert_machine",
                            "test_contains_type",
                            "test_contains_port",
                            "test_host_included_when_different_from_name",
                            "test_host_omitted_when_same_as_name",
                            "test_roundtrip_parse"
                        ]
                    },
                    {
                        "name": "TestJILImportMachine",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_import_machine_exit_0",
                            "test_import_machine_shows_machine_action",
                            "test_import_machine_creates_db_row",
                            "test_import_mixed_jobs_and_machines",
                            "test_import_machine_idempotent"
                        ]
                    },
                    {
                        "name": "TestBoxManager",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_make_box",
                            "_bm_tick",
                            "test_empty_box_completes_immediately",
                            "test_child_without_condition_activated",
                            "test_child_with_met_condition_activated",
                            "test_child_with_unmet_condition_stays_inactive",
                            "test_box_completes_success_when_all_children_done",
                            "test_box_fails_if_child_fails",
                            "test_box_terminated_if_child_terminated",
                            "test_box_not_completed_while_child_running",
                            "test_kill_children_terminates_active",
                            "test_kill_children_leaves_terminal_untouched",
                            "test_reset_children_sets_inactive",
                            "test_sequential_chain_two_ticks"
                        ]
                    },
                    {
                        "name": "TestBoxRepositoryMethods",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_get_children_returns_children",
                            "test_get_children_empty_box",
                            "test_get_running_boxes_returns_running",
                            "test_get_running_boxes_excludes_non_box",
                            "test_get_box_row_returns_none_for_non_box",
                            "test_get_box_row_returns_row_for_box"
                        ]
                    },
                    {
                        "name": "TestEventProcessorBoxIntegration",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_startjob_box_activates_unconditional_child",
                            "test_startjob_box_chains_children",
                            "test_box_failure_propagates_from_failed_child",
                            "test_killjob_box_terminates_children",
                            "test_force_startjob_resets_children",
                            "test_box_not_completed_with_running_children",
                            "test_empty_box_completes_on_first_tick"
                        ]
                    },
                    {
                        "name": "TestCLIBoxStatus",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_box_status_exit_0",
                            "test_box_status_shows_box_name",
                            "test_box_status_shows_children",
                            "test_box_status_shows_condition",
                            "test_box_status_unknown_job_exits_nonzero",
                            "test_box_status_on_cmd_job_exits_nonzero",
                            "test_box_status_empty_box_message"
                        ]
                    },
                    {
                        "name": "TestCLIBoxTree",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_box_tree_exit_0",
                            "test_box_tree_shows_box_name",
                            "test_box_tree_shows_children",
                            "test_box_tree_unknown_job_exits_nonzero"
                        ]
                    },
                    {
                        "name": "TestFullBoxPipeline",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_demo_etl_jil_has_box",
                            "test_startjob_box_from_jil_completes",
                            "test_insert_machine_in_jil_then_job_uses_it"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_box",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_cmd",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_get_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_set_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_enqueue",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_tick",
                        "doc": "Process n ticks with auto_complete (stub) dispatcher."
                    },
                    {
                        "name": "_wait_status",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Phase 7 test suite \u2014 Box Orchestration, insert_machine JIL, MachineDef."
            },
            {
                "name": "test_jil_ui.py",
                "type": "Python",
                "path": "tests/test_jil_ui.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 231,
                "module_doc": "JIL UI API tests \u2014 POST /api/v1/jil/validate and /api/v1/jil/import.\n\nCoverage\n--------\nTestValidate\n    test_validate_valid_jil          \u2014 happy path, all jobs returned\n    test_validate_multi_type         \u2014 BOX + CMD stanzas recognised\n    test_validate_invalid_jil        \u2014 parse error returns success=False + error\n    test_validate_empty_content      \u2014 empty string \u2192 error (no stanzas or parse error)\n\nTestImport\n    test_import_dry_run              \u2014 dry_run=True: jobs listed, DB untouched\n    test_import_persists_to_db       \u2014 dry_run=False: jobs inserted in DB\n    test_import_update_existing      \u2014 second import of same job \u2192 updated count\n    test_import_delete_stanza        \u2014 delete_job stanza removes job from DB\n    test_import_invalid_jil          \u2014 parse error \u2192 success=False, DB untouched\n\nTestUIRoute\n    test_ui_endpoint_returns_html    \u2014 GET /ui \u2192 200 with HTML body",
                "classes": [
                    {
                        "name": "TestValidate",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_validate_valid_jil",
                            "test_validate_multi_type",
                            "test_validate_invalid_jil",
                            "test_validate_does_not_write_to_db",
                            "test_validate_counts_machines"
                        ]
                    },
                    {
                        "name": "TestImport",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_import_dry_run",
                            "test_import_persists_to_db",
                            "test_import_box_with_child",
                            "test_import_update_existing",
                            "test_import_delete_stanza",
                            "test_import_invalid_jil",
                            "test_import_dry_run_does_not_commit_even_with_delete",
                            "test_import_returns_machine_count"
                        ]
                    },
                    {
                        "name": "TestUIRoute",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_ui_endpoint_returns_html"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "client",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_post_validate",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_post_import",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_db_job_count",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "JIL UI API tests \u2014 POST /api/v1/jil/validate and /api/v1/jil/import."
            },
            {
                "name": "test_phase4.py",
                "type": "Python",
                "path": "tests/test_phase4.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 691,
                "module_doc": "Phase 4 test suite \u2014 Event Processor, State Machine, Time Trigger, Scheduler CLI.\n\nCoverage\n--------\n1.  StateMachine     \u2014 valid/invalid transitions, can_transition, helpers\n2.  ConditionEvaluator \u2014 is_satisfied with all predicate types\n3.  TimeTrigger       \u2014 fires at correct time, days_of_week, already-ran guard\n4.  EventProcessor    \u2014 STARTJOB, FORCE_STARTJOB, KILLJOB, HOLD_JOB, JOB_OFF_HOLD,\n                        JOB_ON_ICE, JOB_OFF_ICE, CHANGE_STATUS, SET_GLOBAL,\n                        BOX cascading, auto-complete, condition blocking\n5.  CLI: scheduler run-once  \u2014 processes queued events, reports changes\n6.  CLI: scheduler status    \u2014 shows pending count and job status summary",
                "classes": [
                    {
                        "name": "TestStateMachine",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_valid_transition_inactive_to_starting",
                            "test_valid_transition_starting_to_running",
                            "test_valid_transition_running_to_success",
                            "test_valid_transition_running_to_failure",
                            "test_valid_transition_running_to_terminated",
                            "test_valid_transition_inactive_to_on_hold",
                            "test_valid_transition_on_hold_to_inactive",
                            "test_valid_transition_inactive_to_on_ice",
                            "test_valid_transition_on_ice_to_inactive",
                            "test_valid_transition_inactive_to_activated",
                            "test_invalid_running_to_inactive",
                            "test_invalid_inactive_to_success",
                            "test_invalid_starting_to_activated",
                            "test_force_bypasses_validation",
                            "test_can_transition_true",
                            "test_can_transition_false",
                            "test_is_terminal_success",
                            "test_is_terminal_failure",
                            "test_is_terminal_terminated",
                            "test_is_terminal_running",
                            "test_is_startable_inactive",
                            "test_is_startable_success",
                            "test_is_startable_running",
                            "test_invalid_transition_error_message"
                        ]
                    },
                    {
                        "name": "TestConditionEvaluator",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_no_condition_always_satisfied",
                            "test_success_condition_met",
                            "test_success_condition_not_met",
                            "test_failure_condition_met",
                            "test_done_condition_success",
                            "test_done_condition_failure",
                            "test_done_condition_running",
                            "test_and_both_met",
                            "test_and_one_not_met",
                            "test_or_one_met",
                            "test_or_neither_met",
                            "test_and_binds_tighter_than_or",
                            "test_unknown_job_is_unsatisfied",
                            "test_malformed_condition_returns_false"
                        ]
                    },
                    {
                        "name": "TestTimeTrigger",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_make_row",
                            "test_fires_at_correct_time",
                            "test_does_not_fire_at_wrong_time",
                            "test_fires_on_allowed_weekday",
                            "test_does_not_fire_on_excluded_weekday",
                            "test_does_not_fire_on_weekend_when_weekdays_only",
                            "test_fires_every_day_when_no_dow",
                            "test_fires_every_day_when_all",
                            "test_does_not_fire_if_already_ran_today",
                            "test_fires_again_next_day",
                            "test_no_start_times_never_fires",
                            "test_running_job_does_not_retrigger",
                            "test_success_job_retriggers_next_day",
                            "test_get_triggered_jobs_filters_correctly"
                        ]
                    },
                    {
                        "name": "TestEventProcessor",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_startjob_no_condition_transitions_to_success",
                            "test_startjob_no_condition_transitions_to_starting_without_autocomplete",
                            "test_startjob_condition_satisfied",
                            "test_startjob_condition_not_satisfied",
                            "test_startjob_unknown_job_is_noop",
                            "test_startjob_running_job_is_skipped",
                            "test_startjob_marks_last_run_date",
                            "test_force_startjob_bypasses_condition",
                            "test_killjob_running_job_becomes_terminated",
                            "test_killjob_inactive_job_is_noop",
                            "test_hold_job_becomes_on_hold",
                            "test_off_hold_returns_to_inactive",
                            "test_hold_then_off_hold_returns_startable",
                            "test_on_ice_transitions",
                            "test_off_ice_returns_to_inactive",
                            "test_change_status_overrides_any_status",
                            "test_change_status_to_failure",
                            "test_set_global_upserts_variable",
                            "test_multiple_events_processed_in_order",
                            "test_processed_events_not_replayed",
                            "test_time_trigger_enqueues_startjob",
                            "test_time_trigger_does_not_fire_at_wrong_hour",
                            "test_box_activation_cascades_to_children",
                            "test_box_failure_when_child_fails"
                        ]
                    },
                    {
                        "name": "TestCLISchedulerRunOnce",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_run_once_exit_0",
                            "test_run_once_no_events_says_none",
                            "test_run_once_processes_startjob",
                            "test_run_once_shows_status_change",
                            "test_run_once_quiet_flag"
                        ]
                    },
                    {
                        "name": "TestCLISchedulerStatus",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_status_exit_0",
                            "test_status_shows_pending_count",
                            "test_status_shows_zero_pending",
                            "test_status_shows_job_counts_after_import"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_cmd",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_box",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_set_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_get_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_enqueue",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_tick",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Phase 4 test suite \u2014 Event Processor, State Machine, Time Trigger, Scheduler CLI."
            },
            {
                "name": "test_phase5.py",
                "type": "Python",
                "path": "tests/test_phase5.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 653,
                "module_doc": "Phase 5 test suite \u2014 System Agent, subprocess dispatch, output capture, run history.\n\nCoverage\n--------\n1.  LocalJobRunner     \u2014 stdout capture, exit codes, kill, max_run_alarm timeout\n2.  AgentDispatch      \u2014 dispatch lifecycle, RUNNING\u2192SUCCESS, RUNNING\u2192FAILURE,\n                         machine filtering, %%VAR%% expansion\n3.  RunRepository      \u2014 start/finish, list_runs, latest_run_id\n4.  OutputRepository   \u2014 append, get_lines, get_lines_for_job\n5.  EventProcessor     \u2014 kill_fn called by KILLJOB, real dispatch integration\n6.  CLI: agent run-once \u2014 processes events, waits for completion, shows results\n7.  CLI: jobs tail      \u2014 shows captured output\n8.  CLI: jobs history   \u2014 shows run table",
                "classes": [
                    {
                        "name": "TestLocalJobRunner",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_runner",
                            "test_exit_code_0_on_success",
                            "test_exit_code_nonzero_on_failure",
                            "test_stdout_captured",
                            "test_multiline_output_captured",
                            "test_line_numbers_sequential",
                            "test_pid_set_after_run",
                            "test_exit_code_set_after_run",
                            "test_kill_terminates_process",
                            "test_max_run_secs_kills_long_job",
                            "test_stderr_merged_into_stdout",
                            "test_no_output_callback_runs_cleanly"
                        ]
                    },
                    {
                        "name": "TestMachineFiltering",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_localhost_is_local",
                            "test_127_0_0_1_is_local",
                            "test_none_is_local",
                            "test_empty_string_is_local",
                            "test_remote_name_is_not_local",
                            "test_hostname_is_local",
                            "test_remote_job_stays_starting"
                        ]
                    },
                    {
                        "name": "TestAgentDispatch",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_local_job_becomes_running_then_success",
                            "test_failing_job_becomes_failure",
                            "test_run_history_record_created",
                            "test_run_history_has_start_and_end_times",
                            "test_run_history_has_pid",
                            "test_output_lines_stored_in_db",
                            "test_output_lines_have_sequential_numbers",
                            "test_variable_expansion_in_command",
                            "test_global_variable_expanded_in_command"
                        ]
                    },
                    {
                        "name": "TestRunRepository",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_start_creates_row",
                            "test_finish_updates_row",
                            "test_list_runs_newest_first",
                            "test_latest_run_id",
                            "test_latest_run_id_none_for_unknown_job"
                        ]
                    },
                    {
                        "name": "TestOutputRepository",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_append_and_get_lines",
                            "test_get_lines_ordered_by_line_no",
                            "test_get_lines_for_job_uses_latest_run",
                            "test_get_lines_empty_for_no_output"
                        ]
                    },
                    {
                        "name": "TestEventProcessorKillFn",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_killjob_calls_kill_fn",
                            "test_killjob_no_kill_fn_still_terminates"
                        ]
                    },
                    {
                        "name": "TestCLIAgentRunOnce",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "test_run_once_exit_0_no_events",
                            "test_run_once_no_events_message",
                            "test_run_once_processes_local_job",
                            "test_run_once_quiet_flag"
                        ]
                    },
                    {
                        "name": "TestCLIJobsTail",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "_run_job_and_wait",
                            "test_tail_exit_0",
                            "test_tail_shows_output",
                            "test_tail_shows_run_id",
                            "test_tail_no_output_message",
                            "test_tail_last_n_lines"
                        ]
                    },
                    {
                        "name": "TestCLIJobsHistory",
                        "doc": "No docstring provided.",
                        "methods": [
                            "_run",
                            "_run_job_and_wait",
                            "test_history_exit_0",
                            "test_history_shows_run_row",
                            "test_history_shows_exit_code",
                            "test_history_no_runs_message",
                            "test_history_unknown_job_exits_nonzero",
                            "test_history_shows_duration"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_seed_cmd",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_get_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_set_status",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_enqueue",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_wait_for_status",
                        "doc": "Poll DB until job reaches expected status or timeout."
                    },
                    {
                        "name": "_tick_with_agent",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Phase 5 test suite \u2014 System Agent, subprocess dispatch, output capture, run history."
            },
            {
                "name": "test_phase1.py",
                "type": "Python",
                "path": "tests/test_phase1.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 592,
                "module_doc": "Phase 1 test suite \u2014 DB schema + Pydantic models.\n\nWhat is tested\n--------------\n1.  Enums          \u2014 every enum has the expected members; string values match real AutoSys\n2.  Job models     \u2014 valid construction, subclass dispatch, validators (required fields,\n                     forbidden fields, days_of_week normalisation, start_times normalisation)\n3.  JobRun model   \u2014 duration_seconds, is_terminal properties\n4.  Event model    \u2014 validation rules per event_type, EventHistory inheritance\n5.  Alarm model    \u2014 is_active property\n6.  Calendar model \u2014 date parsing, contains(), load_from_file()\n7.  VirtualResource\u2014 available_slots, is_saturated, can_accept()\n8.  GlobalVariable \u2014 name uppercasing, AUTOSYS_BUILTIN_GLOBALS dict\n9.  DB schema      \u2014 create_all_sync() creates all 9 tables; seed inserts localhost + calendars\n10. Round-trip     \u2014 Pydantic \u2192 JobRow \u2192 SELECT \u2192 same values",
                "classes": [
                    {
                        "name": "TestEnums",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_job_type_members",
                            "test_job_status_has_17_states",
                            "test_event_type_has_15_members",
                            "test_alarm_type_members",
                            "test_day_of_week_helpers",
                            "test_all_enums_are_strings"
                        ]
                    },
                    {
                        "name": "TestJobModels",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_cmd_job_valid",
                            "test_cmd_job_missing_command_raises",
                            "test_cmd_job_missing_machine_raises",
                            "test_box_job_valid",
                            "test_box_job_with_command_raises",
                            "test_filewatch_job_valid",
                            "test_filewatch_missing_watch_file_raises",
                            "test_ftp_job_valid",
                            "test_ftp_job_missing_fields_raises",
                            "test_parse_job_returns_correct_subclass",
                            "test_start_times_from_comma_string",
                            "test_days_of_week_all_expands",
                            "test_days_of_week_from_comma_string",
                            "test_job_name_pattern_rejects_spaces",
                            "test_default_status_is_inactive"
                        ]
                    },
                    {
                        "name": "TestJobRunModel",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_duration_seconds_none_when_not_finished",
                            "test_duration_seconds_computed",
                            "test_is_terminal_for_success",
                            "test_is_terminal_for_failure",
                            "test_is_terminal_for_terminated",
                            "test_not_terminal_while_running",
                            "test_run_id_is_uuid",
                            "test_retry_count_defaults_to_zero"
                        ]
                    },
                    {
                        "name": "TestEventModel",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_startjob_requires_job_name",
                            "test_change_status_requires_new_status",
                            "test_set_global_requires_name_and_value",
                            "test_valid_startjob_event",
                            "test_valid_set_global_event",
                            "test_valid_change_status_event",
                            "test_force_startjob_requires_job_name",
                            "test_event_history_inherits_event"
                        ]
                    },
                    {
                        "name": "TestAlarmModel",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_alarm_is_active_when_not_cleared",
                            "test_alarm_inactive_when_cleared",
                            "test_alarm_id_is_uuid"
                        ]
                    },
                    {
                        "name": "TestCalendarModel",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_calendar_from_date_list",
                            "test_calendar_from_string_list",
                            "test_calendar_from_bulk_string",
                            "test_load_from_file",
                            "test_calendar_name_pattern"
                        ]
                    },
                    {
                        "name": "TestVirtualResourceModel",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_available_slots",
                            "test_is_saturated_false",
                            "test_is_saturated_true",
                            "test_can_accept_true",
                            "test_can_accept_false",
                            "test_current_load_defaults_to_zero"
                        ]
                    },
                    {
                        "name": "TestGlobalVariableModel",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_name_uppercased_automatically",
                            "test_name_already_upper_unchanged",
                            "test_builtin_globals_present",
                            "test_builtin_globals_are_strings",
                            "test_name_pattern_rejects_spaces"
                        ]
                    },
                    {
                        "name": "TestDBSchema",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_all_9_tables_created",
                            "test_machines_table_has_localhost",
                            "test_calendars_table_has_us_holidays"
                        ]
                    },
                    {
                        "name": "TestRoundTrip",
                        "doc": "No docstring provided.",
                        "methods": [
                            "test_job_pydantic_to_orm_and_back",
                            "test_event_queue_write_and_read",
                            "test_global_variable_write_and_read"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "fresh_db",
                        "doc": "Create all tables once for the whole test session (in-memory SQLite)."
                    }
                ],
                "description": "Phase 1 test suite \u2014 DB schema + Pydantic models."
            },
            {
                "name": "test_phase12.py",
                "type": "Python",
                "path": "tests/test_phase12.py",
                "kid_desc": "A tiny worker robot helping out in the 10. The Health Inspector (Tests) department.",
                "lines": 222,
                "module_doc": "No module docstring.",
                "classes": [
                    {
                        "name": "_ServerHandle",
                        "doc": "No docstring provided.",
                        "methods": [
                            "__init__"
                        ]
                    }
                ],
                "functions": [
                    {
                        "name": "_free_port",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_start_agent_server",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "isolated_db",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "agent_1",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "agent_2",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "app_server",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "test_phase12_end_to_end",
                        "doc": "No docstring provided."
                    }
                ],
                "description": "Python source file (222 lines)"
            }
        ]
    },
    {
        "id": "autosys",
        "order": 11,
        "title": "11. Core Setup",
        "description": "Root initialization files.",
        "kid_title": "11. The Foundation",
        "kid_description": "The concrete blocks the restaurant is built on.",
        "icon": "ph-gear",
        "files": [
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the 11. The Foundation department.",
                "lines": 2,
                "module_doc": "AutoSys Clone \u2014 root package.",
                "classes": [],
                "functions": [],
                "description": "AutoSys Clone \u2014 root package."
            }
        ]
    },
    {
        "id": "autosys_app_server_routers",
        "order": 99,
        "title": "autosys/app_server/routers",
        "description": "Files in autosys/app_server/routers",
        "kid_title": "The autosys/app_server/routers Room",
        "kid_description": "Another room in our massive restaurant.",
        "icon": "ph-folder",
        "files": [
            {
                "name": "jil.py",
                "type": "Python",
                "path": "autosys/app_server/routers/jil.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 135,
                "module_doc": "JIL router \u2014 validate and import JIL content via REST.\n\nPOST /api/v1/jil/validate   Parse JIL text; return parsed jobs without DB writes.\nPOST /api/v1/jil/import     Parse JIL text and persist to the database (supports dry_run).",
                "classes": [],
                "functions": [
                    {
                        "name": "_parse_or_error",
                        "doc": "Return (ops, None) on success, (None, error_message) on failure."
                    },
                    {
                        "name": "validate_jil",
                        "doc": "Parse JIL text and return the list of recognised stanzas.\nNothing is written to the database."
                    },
                    {
                        "name": "import_jil",
                        "doc": "Parse JIL text and persist stanzas to the database.\n\nPass ``dry_run: true`` to parse and validate without writing anything."
                    }
                ],
                "description": "JIL router \u2014 validate and import JIL content via REST."
            },
            {
                "name": "events.py",
                "type": "Python",
                "path": "autosys/app_server/routers/events.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 80,
                "module_doc": "Events router \u2014 inspect the event queue and history.\n\nSchema notes:\n  EventQueueRow  \u2014 has processed (bool) + processed_at, no status/attribute cols.\n                   status is derived: \"PENDING\" or \"PROCESSED\".\n                   attribute is derived from global_value (SET_GLOBAL events).\n  EventHistoryRow\u2014 has event_id, event_type, job_name, source, created_at,\n                   global_value, metadata_json.  No processed_at column.",
                "classes": [],
                "functions": [
                    {
                        "name": "_queue_row_to_resp",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_hist_row_to_resp",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "list_pending_events",
                        "doc": "Return all PENDING events in the queue (FIFO order)."
                    },
                    {
                        "name": "event_history",
                        "doc": "Return processed events from event_history, newest first."
                    }
                ],
                "description": "Events router \u2014 inspect the event queue and history."
            },
            {
                "name": "machines.py",
                "type": "Python",
                "path": "autosys/app_server/routers/machines.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 106,
                "module_doc": "Machines router \u2014 CRUD for registered System Agent machines.",
                "classes": [],
                "functions": [
                    {
                        "name": "_row_to_resp",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "list_machines",
                        "doc": "List all registered machines."
                    },
                    {
                        "name": "get_machine",
                        "doc": "Get a single machine by name."
                    },
                    {
                        "name": "register_machine",
                        "doc": "Register or update a machine."
                    },
                    {
                        "name": "delete_machine",
                        "doc": "Deregister a machine."
                    },
                    {
                        "name": "check_heartbeat",
                        "doc": "Trigger an immediate heartbeat check for a machine.\n\nAttempts to connect to the agent's TCP server and returns the updated\nmachine status (UP or DOWN)."
                    }
                ],
                "description": "Machines router \u2014 CRUD for registered System Agent machines."
            },
            {
                "name": "globals.py",
                "type": "Python",
                "path": "autosys/app_server/routers/globals.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 75,
                "module_doc": "Global variables router \u2014 CRUD for %%VARIABLE%% definitions.",
                "classes": [],
                "functions": [
                    {
                        "name": "_row_to_resp",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "list_globals",
                        "doc": "List all global variables."
                    },
                    {
                        "name": "get_global",
                        "doc": "Get a single global variable."
                    },
                    {
                        "name": "set_global",
                        "doc": "Create or update a global variable."
                    },
                    {
                        "name": "delete_global",
                        "doc": "Delete a global variable."
                    }
                ],
                "description": "Global variables router \u2014 CRUD for %%VARIABLE%% definitions."
            },
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/app_server/routers/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 0,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [],
                "description": "Python source file (0 lines)"
            },
            {
                "name": "jobs.py",
                "type": "Python",
                "path": "autosys/app_server/routers/jobs.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 173,
                "module_doc": "Jobs router \u2014 GET/DELETE jobs, POST sendevent.",
                "classes": [],
                "functions": [
                    {
                        "name": "_row_to_response",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "_row_to_detail",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "list_jobs",
                        "doc": "List all jobs, with optional filters."
                    },
                    {
                        "name": "get_job",
                        "doc": "Get full detail for a single job."
                    },
                    {
                        "name": "delete_job",
                        "doc": "Delete a job definition."
                    },
                    {
                        "name": "sendevent",
                        "doc": "Enqueue an event for a job.\n\nExamples: STARTJOB, KILLJOB, FORCE_STARTJOB, JOB_ON_HOLD, JOB_OFF_HOLD,\n          JOB_ON_ICE, JOB_OFF_ICE, CHANGE_STATUS, SET_GLOBAL"
                    }
                ],
                "description": "Jobs router \u2014 GET/DELETE jobs, POST sendevent."
            },
            {
                "name": "alarms.py",
                "type": "Python",
                "path": "autosys/app_server/routers/alarms.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 106,
                "module_doc": "Alarms router \u2014 list and resolve alarms raised by the scheduler.\n\nGET  /api/v1/alarms                 list alarms (filter by active=true/false)\nPOST /api/v1/alarms/{id}/resolve    clear an active alarm",
                "classes": [],
                "functions": [
                    {
                        "name": "_row_to_resp",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "list_alarms",
                        "doc": "List alarms, optionally filtered by active state or job name."
                    },
                    {
                        "name": "resolve_alarm",
                        "doc": "Clear an active alarm (mark as resolved)."
                    },
                    {
                        "name": "raise_alarm",
                        "doc": "Manually raise an alarm (admin only \u2014 mainly for testing)."
                    }
                ],
                "description": "Alarms router \u2014 list and resolve alarms raised by the scheduler."
            },
            {
                "name": "runs.py",
                "type": "Python",
                "path": "autosys/app_server/routers/runs.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/routers Room department.",
                "lines": 75,
                "module_doc": "Runs router \u2014 job execution history and stdout output.",
                "classes": [],
                "functions": [
                    {
                        "name": "_run_to_resp",
                        "doc": "No docstring provided."
                    },
                    {
                        "name": "list_runs",
                        "doc": "List run history, newest first."
                    },
                    {
                        "name": "get_run",
                        "doc": "Get a single run record by run_id."
                    },
                    {
                        "name": "get_run_output",
                        "doc": "Get captured stdout lines for a run."
                    }
                ],
                "description": "Runs router \u2014 job execution history and stdout output."
            }
        ]
    },
    {
        "id": "autosys_app_server_static",
        "order": 99,
        "title": "autosys/app_server/static",
        "description": "Files in autosys/app_server/static",
        "kid_title": "The autosys/app_server/static Room",
        "kid_description": "Another room in our massive restaurant.",
        "icon": "ph-folder",
        "files": [
            {
                "name": "index.html",
                "type": "File",
                "path": "autosys/app_server/static/index.html",
                "kid_desc": "A tiny worker robot helping out in the The autosys/app_server/static Room department.",
                "lines": 480,
                "description": "Static or configuration file (480 lines)"
            }
        ]
    },
    {
        "id": "autosys_wcc_static",
        "order": 99,
        "title": "autosys/wcc/static",
        "description": "Files in autosys/wcc/static",
        "kid_title": "The autosys/wcc/static Room",
        "kid_description": "Another room in our massive restaurant.",
        "icon": "ph-folder",
        "files": [
            {
                "name": "wcc.css",
                "type": "File",
                "path": "autosys/wcc/static/wcc.css",
                "kid_desc": "A tiny worker robot helping out in the The autosys/wcc/static Room department.",
                "lines": 244,
                "description": "Static or configuration file (244 lines)"
            }
        ]
    },
    {
        "id": "autosys_wcc_templates",
        "order": 99,
        "title": "autosys/wcc/templates",
        "description": "Files in autosys/wcc/templates",
        "kid_title": "The autosys/wcc/templates Room",
        "kid_description": "Another room in our massive restaurant.",
        "icon": "ph-folder",
        "files": [
            {
                "name": "jobs.html",
                "type": "File",
                "path": "autosys/wcc/templates/jobs.html",
                "kid_desc": "A tiny worker robot helping out in the The autosys/wcc/templates Room department.",
                "lines": 120,
                "description": "Static or configuration file (120 lines)"
            },
            {
                "name": "alarms.html",
                "type": "File",
                "path": "autosys/wcc/templates/alarms.html",
                "kid_desc": "A tiny worker robot helping out in the The autosys/wcc/templates Room department.",
                "lines": 74,
                "description": "Static or configuration file (74 lines)"
            },
            {
                "name": "base.html",
                "type": "File",
                "path": "autosys/wcc/templates/base.html",
                "kid_desc": "A tiny worker robot helping out in the The autosys/wcc/templates Room department.",
                "lines": 29,
                "description": "Static or configuration file (29 lines)"
            },
            {
                "name": "job_detail.html",
                "type": "File",
                "path": "autosys/wcc/templates/job_detail.html",
                "kid_desc": "A tiny worker robot helping out in the The autosys/wcc/templates Room department.",
                "lines": 140,
                "description": "Static or configuration file (140 lines)"
            },
            {
                "name": "box_graph.html",
                "type": "File",
                "path": "autosys/wcc/templates/box_graph.html",
                "kid_desc": "A tiny worker robot helping out in the The autosys/wcc/templates Room department.",
                "lines": 154,
                "description": "Static or configuration file (154 lines)"
            }
        ]
    },
    {
        "id": "autosys_engine",
        "order": 99,
        "title": "autosys/engine",
        "description": "Files in autosys/engine",
        "kid_title": "The autosys/engine Room",
        "kid_description": "Another room in our massive restaurant.",
        "icon": "ph-folder",
        "files": [
            {
                "name": "__init__.py",
                "type": "Python",
                "path": "autosys/engine/__init__.py",
                "kid_desc": "A tiny worker robot helping out in the The autosys/engine Room department.",
                "lines": 0,
                "module_doc": "No module docstring.",
                "classes": [],
                "functions": [],
                "description": "Python source file (0 lines)"
            }
        ]
    },
    {
        "id": ".agents_rules",
        "order": 99,
        "title": ".agents/rules",
        "description": "Files in .agents/rules",
        "kid_title": "The .agents/rules Room",
        "kid_description": "Another room in our massive restaurant.",
        "icon": "ph-folder",
        "files": [
            {
                "name": "autosys.md",
                "type": "File",
                "path": ".agents/rules/autosys.md",
                "kid_desc": "A tiny worker robot helping out in the The .agents/rules Room department.",
                "lines": 187,
                "description": "Static or configuration file (187 lines)"
            }
        ]
    }
];