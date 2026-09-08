const tutorialData = [
    {
        id: "intro",
        title: "1. The Big Picture",
        icon: "ph-globe",
        content: `
            <h3>What is this project?</h3>
            <p>This project is a high-fidelity clone of <strong>Broadcom AutoSys Workload Automation</strong>. It is designed to schedule, monitor, and report on complex dependency-based workloads across multiple machines.</p>
            <br>
            <h3>Core Architecture</h3>
            <p>The system is broken down into five distinct components, just like the real AutoSys:</p>
            <div class="tutorial-grid">
                <div class="tut-card">
                    <h4><i class="ph-database"></i> Event Server</h4>
                    <p>The database. It stores everything: job definitions, status history, calendar data, and global variables.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-cpu"></i> Scheduler</h4>
                    <p>The brain. It continuously evaluates job dependencies (conditions) and decides exactly <em>when</em> a job should run.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-app-window"></i> App Server</h4>
                    <p>The API layer. All clients (CLI, Web UI) talk to the App Server. It manages authentication and routes commands.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-terminal-window"></i> Agent</h4>
                    <p>The worker. Installed on remote machines, it receives commands from the App Server, executes them in a shell, and returns the exit code.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-users"></i> Client</h4>
                    <p>The user tools like <code>jil</code>, <code>autorep</code>, and <code>sendevent</code> used to interact with the system.</p>
                </div>
            </div>
            <br>
            <h3>System Flow Diagram</h3>
            <div class="mermaid">
            graph TD
                CLI[CLI Tools] -->|API Requests| AS[App Server]
                WCC[Web UI] -->|API Requests| AS
                AS <-->|Reads/Writes| DB[(Event Server / DB)]
                SCH[Scheduler] <-->|Polls Events & Updates Status| DB
                SCH -->|Dispatch Signal| AS
                AS -->|Execute Cmd| AGT[Remote Agent]
                AGT -->|Exit Code| AS
            </div>
        `
    },
    {
        id: "lifecycle",
        title: "2. The Job Lifecycle",
        icon: "ph-arrows-clockwise",
        content: `
            <h3>How a Job Runs</h3>
            <p>In AutoSys, a job goes through a very strict sequence of states. The Scheduler is a state machine that drives this.</p>
            <ol class="tut-steps">
                <li><strong>ACTIVATED:</strong> The job's dependencies are met, and it is ready to be processed.</li>
                <li><strong>STARTING:</strong> The Scheduler tells the App Server to dispatch the job to the target Agent.</li>
                <li><strong>RUNNING:</strong> The Agent confirms it has successfully launched the shell command. The job is now executing.</li>
                <li><strong>SUCCESS / FAILURE:</strong> The Agent returns an integer exit code. The Scheduler evaluates this (usually exit code 0 = SUCCESS, anything else = FAILURE) and updates the final state.</li>
            </ol>
            <br>
            <div class="mermaid">
            stateDiagram-v2
                [*] --> INACTIVE
                INACTIVE --> ACTIVATED : Conditions Met
                ACTIVATED --> STARTING : Scheduler Dispatches
                STARTING --> RUNNING : Agent Confirms Launch
                RUNNING --> SUCCESS : Exit Code <= max_exit_success
                RUNNING --> FAILURE : Exit Code > max_exit_success
                SUCCESS --> [*]
                FAILURE --> [*]
            </div>
            <br>
            <div class="tut-alert">
                <strong>Important:</strong> Status codes are stored as integers in the database (e.g., 4 = SUCCESS, 5 = FAILURE, 1 = RUNNING). The Web UI and CLI convert these back to readable strings.
            </div>
        `
    },
    {
        id: "jil",
        title: "3. JIL & Dependencies",
        icon: "ph-code",
        content: `
            <h3>Job Information Language (JIL)</h3>
            <p>JIL is the scripting language used to define jobs. The <code>autosys/parser</code> module is responsible for tokenizing and parsing this language into database records.</p>
            <pre><code>
insert_job: nightly_etl   job_type: c
box_name: daily_batch
command: /scripts/run_etl.sh
machine: PROD_SERVER_01
condition: s(data_prep) & s(verify_disk, 12)
            </code></pre>
            <p>In the above example, <code>nightly_etl</code> is a command (c) job that belongs to a box job called <code>daily_batch</code>.</p>
            <br>
            <h3>How Conditions Work</h3>
            <p>The condition <code>s(data_prep) & s(verify_disk, 12)</code> means this job will only run if:</p>
            <ul>
                <li>The job <code>data_prep</code> has a status of <strong>SUCCESS</strong>.</li>
                <li>The job <code>verify_disk</code> has succeeded within the last <strong>12 hours</strong> (this is called look-back).</li>
            </ul>
            <p>The <code>autosys/scheduler/condition_evaluator.py</code> file parses these logical strings and checks the <code>ujo_job_status</code> table to see if they evaluate to True.</p>
        `
    },
    {
        id: "boxes",
        title: "4. Box Jobs & Rollups",
        icon: "ph-package",
        content: `
            <h3>Box Job Mechanics</h3>
            <p>A Box job is a container for other jobs. It doesn't have a command of its own; instead, its status is derived from its children.</p>
            <ul>
                <li>When a Box is forced to run, all of its nested children are put into the <code>ACTIVATED</code> state.</li>
                <li>A Box evaluates to <strong>SUCCESS</strong> when <em>all</em> of its child jobs evaluate to SUCCESS.</li>
                <li>A Box evaluates to <strong>FAILURE</strong> the moment <em>any</em> of its child jobs evaluate to FAILURE.</li>
            </ul>
            <p>This cascade logic is handled strictly by the <code>autosys/scheduler/box_manager.py</code>.</p>
            <br>
            <div class="mermaid">
            graph TD
                BOX[Box Job: daily_batch]
                BOX --- J1(Job A: SUCCESS)
                BOX --- J2(Job B: SUCCESS)
                BOX --- J3(Job C: RUNNING)
                style BOX fill:#2d3748,stroke:#4a5568,stroke-width:2px,color:#fff
                style J1 fill:#2f855a,stroke:#22543d,stroke-width:2px,color:#fff
                style J2 fill:#2f855a,stroke:#22543d,stroke-width:2px,color:#fff
                style J3 fill:#2b6cb0,stroke:#2c5282,stroke-width:2px,color:#fff
            </div>
            <p style="text-align: center; color: var(--text-muted); font-size: 13px;"><em>The box will remain RUNNING until Job C finishes.</em></p>
        `
    },
    {
        id: "cli",
        title: "5. Interacting (CLI)",
        icon: "ph-terminal",
        content: `
            <h3>The Command Line Tools</h3>
            <p>The <code>autosys/cli</code> module provides the tools users invoke to control the system. They are completely read-only or rely entirely on the App Server API to make changes.</p>
            <div class="tut-grid-2">
                <div class="tut-card">
                    <h4>autorep</h4>
                    <p>The reporting tool. Used to query job statuses, run history, and configuration. e.g. <code>autorep -J nightly_etl -d</code></p>
                </div>
                <div class="tut-card">
                    <h4>sendevent</h4>
                    <p>The mutation tool. Injects events into the Event Server. E.g. <code>sendevent -E FORCE_STARTJOB -J nightly_etl</code></p>
                </div>
                <div class="tut-card">
                    <h4>jil</h4>
                    <p>Takes JIL script input and parses it via the API to insert, update, or delete job definitions.</p>
                </div>
                <div class="tut-card">
                    <h4>chase</h4>
                    <p>An audit tool that verifies if jobs marked as RUNNING in the DB are actually alive on the target Agent machine.</p>
                </div>
            </div>
        `
    }
];
