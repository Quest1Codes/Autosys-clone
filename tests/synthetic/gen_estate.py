"""
Deterministic synthetic AutoSys JIL estate generator, for the conformance
suite (task13-assessment-report-research, prompt 09; re-verified against
this repo's real parser/importer while building T04).

Every archetype is a pure function of (archetype, seed, n_jobs):
  - identical inputs -> byte-identical JIL text, so a test asserting on one
    run's output stays true on the next
  - each file carries enough of the documented JIL universe (native ERP
    and mainframe types, cross-instance refs, dangling references, Windows
    syntax, deep nesting, schedule attributes, the non-job objects) to
    exercise more than plain CMD chains

No real or real-shaped customer data: every generated file opens with a
comment saying so, and every job/machine/calendar name is a synthetic
placeholder (CMD_00001, mach01, ...), never anything drawn from a real
estate. This is why these estates are committed and `jil_samples/` isn't.

generate() returns (jil_text, facts); facts is the ground truth a test
checks the real import/score/classify path against -- defined job names,
deleted jobs, feature counts -- not something read back out of the system
under test.
"""
from __future__ import annotations

import random
from typing import Any


def _hdr(lines: list[str], text: str) -> None:
    lines.append(f"/* {text} */")


def machine(lines: list[str], name: str, mtype: str = "a", opsys: str | None = None,
            extra: list[str] | None = None) -> None:
    _hdr(lines, f"machine {name}")
    lines.append(f"insert_machine: {name}")
    lines.append(f"  type: {mtype}")
    if opsys:
        lines.append(f"  opsys: {opsys}")
    for kv in (extra or []):
        lines.append(f"  {kv}")
    lines.append("")


def virtual_machine(lines: list[str], name: str, members: list[str]) -> None:
    lines.append(f"insert_machine: {name}")
    lines.append("  type: v")
    for m in members:
        lines.append(f"  machine: {m}")
    lines.append("")


def calendar(lines: list[str], name: str, dates: list[str]) -> None:
    lines.append(f"insert_calendar: {name}")
    lines.append(f'  dates: "{",".join(dates)}"')
    lines.append("")


def resource(lines: list[str], name: str, amount: int = 2, machine: str = "mach01") -> None:
    lines.append(f"insert_resource: {name}")
    lines.append("  res_type: R")
    lines.append(f"  amount: {amount}")
    lines.append(f"  machine: {machine}")
    lines.append("")


def xinst(lines: list[str], name: str) -> None:
    lines.append(f"insert_xinst: {name}")
    lines.append("  xtype: S")
    lines.append("  xmachine: remhost")
    lines.append("  xport: 7520")
    lines.append("")


def connprofile(lines: list[str], name: str) -> None:
    lines.append(f"insert_connectionprofile: {name}")
    lines.append("  connectionprofile_type: HADOOP")
    lines.append("  description: hadoop profile")
    lines.append("")


def monbro(lines: list[str], name: str, job: str) -> None:
    lines.append(f"insert_monbro: {name}")
    lines.append("  monbro_type: FILE_MONITOR")
    lines.append(f"  job_name: {job}")
    lines.append("")


def blob(lines: list[str], name: str, job: str) -> None:
    lines.append(f"insert_blob: {name}")
    lines.append(f"  job_name: {job}")
    lines.append("  blob_input:<auto_blobt>multi")
    lines.append("line blob payload")
    lines.append("</auto_blobt>")
    lines.append("")


def glob_(lines: list[str], name: str, value: str) -> None:
    lines.append(f"insert_glob: {name}")
    lines.append(f"  blob_input:<auto_blobt>{value}</auto_blobt>")
    lines.append("")


def jobtype(lines: list[str], name: str) -> None:
    lines.append(f"insert_job_type: {name}")
    lines.append(f"  command: /opt/site/bin/{name.lower()}")
    lines.append("  description: site-defined type")
    lines.append("")


CMD_TEMPLATES = [
    "/opt/app/scripts/daily_load_{i}.ksh -d $${{BATCH_DATE}}",
    "sh /etl/stage/extract_{i}.sh --env $${{ENV_NAME}}",
    "python3 /jobs/etl/transform_{i}.py --date %%DATE%%",
    "perl /legacy/tools/recon_{i}.pl",
    "/usr/bin/ksh /shared/batch/post_{i}.ksh $$RUNBOOK",
    "C:\\batch\\nightly_{i}.bat %BATCHDATE%",
    "cmd /c D:\\jobs\\load_{i}.cmd",
    "sendevent -E SET_GLOBAL -G \"JOB{i}_DONE=Y\"",
    "for %%f in (C:\\in\\*.csv) do call C:\\bin\\load.bat %%f",
    "/opt/infm/pmcmd startworkflow -sv IS01 -d Dom -u usr -p pwd wf_{i}",
]


def _job(lines: list[str], name: str, jtype: str = "c", machine: str | None = "mach01",
         command: str | None = None, attrs: list[str] | None = None,
         box: str | None = None, condition: str | None = None) -> None:
    lines.append(f"insert_job: {name}   job_type: {jtype}")
    if box:
        lines.append(f"  box_name: {box}")
    if machine is not None:
        lines.append(f"  machine: {machine}")
    if command is not None:
        lines.append(f"  command: {command}")
    if condition:
        lines.append(f"  condition: {condition}")
    for a in (attrs or []):
        lines.append(f"  {a}")
    lines.append("")


# ---------------------------------------------------------------------------
# Feature blocks -- each returns a writer(lines, names) closure
# ---------------------------------------------------------------------------

def feat_core(facts: dict, rnd: random.Random, n: int):
    """CMD jobs with plain conditions; no boxes."""
    def w(lines, names):
        for i in range(n):
            nm = f"CMD_{i:05d}"
            names.append(nm)
            cond = f"s(CMD_{i - 1:05d})" if i and i % 3 == 0 else None
            _job(lines, nm, command=CMD_TEMPLATES[i % len(CMD_TEMPLATES)].format(i=i), condition=cond)
    return w


def feat_boxes(facts: dict, rnd: random.Random, n: int):
    """BOX trees: root box -> mid boxes -> leaf CMDs."""
    def w(lines, names):
        roots = max(2, n // 60)
        per = n // roots
        idx = 0
        for r in range(roots):
            root = f"BOX_R{r:02d}"
            names.append(root)
            _job(lines, root, jtype="b", machine=None,
                 attrs=["start_times: \"06:00,18:00\"", "days_of_week: mo,tu,we,th,fr"])
            mids = max(1, min(4, per // 15))
            for m in range(mids):
                mid = f"BOX_R{r:02d}_M{m:02d}"
                names.append(mid)
                _job(lines, mid, jtype="b", machine=None, box=root,
                     condition=f"d(LEAF_{idx:05d})" if idx else None)
                kids = max(1, per // mids)
                for k in range(kids):
                    nm = f"LEAF_{idx:05d}"
                    idx += 1
                    names.append(nm)
                    cond = f"s(LEAF_{idx - 2:05d})" if k and idx % 2 == 0 else None
                    _job(lines, nm, command=f"/etl/run_{idx}.sh", box=mid, condition=cond)
                    if idx >= n:
                        break
    return w


def feat_native_erp(facts: dict, rnd: random.Random, n: int):
    """Native ERP job types the scorer has no branch for (SAP/PS/Oracle)."""
    sap_types = ["SAP", "SAPBDC", "SAPJC", "SAPBWIP", "SAPDA", "PS", "OASG", "OASET"]
    def w(lines, names):
        for i in range(n):
            jt = sap_types[i % len(sap_types)]
            nm = f"ERP_{i:04d}"
            names.append(nm)
            attrs = [f"description: {jt} job"]
            if jt.startswith("SAP"):
                attrs += ["sap_job_name: RDDIMPDP", "sap_client: 100",
                          "sap_target_sys: PRD", 'sap_lang: "EN"']
            elif jt == "PS":
                attrs += ["ps_process_name: APXXX", "ps_process_type: SQR Report",
                          "ps_operator_id: PSOFT1"]
            else:
                attrs += ["oracle_appl_name: FA", "oracle_program: FNDXXX"]
            if i % 5 == 0:
                attrs.append(f"condition: s(ERP_{i - 1:04d})" if i else "condition: s(ERP_0000)")
            _job(lines, nm, jtype=jt, attrs=attrs)
        facts["native_erp_jobs"] = n
    return w


def feat_mainframe(facts: dict, rnd: random.Random, n: int):
    """z/OS + i5 + database-native types crossing the mainframe boundary."""
    types = ["ZOS", "ZOSM", "ZOSDST", "I5", "DBMON", "DBPROC", "DBTRIG", "SQL", "SQLAGENT", "MICROFOCUS"]
    def w(lines, names):
        for i in range(n):
            jt = types[i % len(types)]
            nm = f"MF_{i:04d}"
            names.append(nm)
            if jt.startswith("ZOS"):
                attrs = ["zos_jobname: NIGHTLY1", "jcl_library: PROD.JCLLIB"]
            elif jt == "I5":
                attrs = ["i5_name: CYCLEEND", "i5_lib: QGPL", "i5_action: *RUN"]
            elif jt == "SQL":
                attrs = ["sql_command: select count(*) from t", "connect_string: db1"]
            elif jt == "SQLAGENT":
                attrs = ["sqlagent_jobname: nightly_etl", "sqlagent_target_db: msdb"]
            elif jt == "MICROFOCUS":
                attrs = ["mf_jcl_name: JOB1", "mf_server: mfsrv01"]
            else:
                attrs = ["dbtype: ORACLE", "tablename: STG_LOAD"]
            _job(lines, nm, jtype=jt, attrs=attrs)
        facts["mainframe_jobs"] = n
    return w


def feat_cross_instance(facts: dict, rnd: random.Random, n: int):
    """^ cross-instance refs, look-back forms, exitcode+lookback, NOT, v()."""
    def w(lines, names):
        for i in range(n):
            nm = f"XJ_{i:04d}"
            names.append(nm)
            c = i % 8
            if c == 0:
                cond = f"s(EXTJOB{i} ^PRD)"
            elif c == 1:
                cond = f"s(EXTJOB{i}^PRD)"
            elif c == 2:
                cond = f"s(XJ_{i - 1:04d}, 00.30)" if i else None
            elif c == 3:
                cond = f"e(XJ_{i - 1:04d}, 12) = 0" if i else None
            elif c == 4:
                cond = f'v(GLB_{i % 5}) = "RUN"'
            elif c == 5:
                cond = f"!s(XJ_{i - 1:04d})" if i else None
            elif c == 6:
                cond = f"e(XJ_{i - 1:04d}^PRD) != 4" if i else None
            else:
                cond = f"s(XJ_{i - 1:04d}) | f(XJ_{i - 2:04d}) & n(XJ_{i - 3:04d})" if i > 3 else None
            if c in (0, 1):
                facts["xinst_refs"] = facts.get("xinst_refs", 0) + 1
            _job(lines, nm, command=f"/xrun/xj_{i}.sh", condition=cond)
        facts["lookback_jobs"] = len([1 for i in range(n) if i % 8 == 2])
    return w


def feat_schedules(facts: dict, rnd: random.Random, n: int):
    """Calendars, time attrs, timezone, must_*, run_window, alarms."""
    def w(lines, names):
        calendar(lines, "RUN_CAL_A", ["2026-01-05", "2026-01-12", "2026-01-19"])
        calendar(lines, "HOLIDAY_CAL", ["2026-01-01", "2026-12-25"])
        for i in range(n):
            nm = f"SCH_{i:04d}"
            names.append(nm)
            a: list[str] = []
            c = i % 10
            if c == 0:
                a += ["run_calendar: RUN_CAL_A"]
            elif c == 1:
                a += ["exclude_calendar: HOLIDAY_CAL", "days_of_week: all"]
            elif c == 2:
                a += ["date_conditions: 1", 'start_times: "07:00,19:00"']
            elif c == 3:
                a += ["start_mins: 0,15,30,45"]
            elif c == 4:
                a += ['run_window: "08:00-17:00"', "days_of_week: mo,tu,we,th,fr"]
            elif c == 5:
                a += ['must_start_times: "06:30"', 'must_complete_times: "22:00"']
            elif c == 6:
                a += ["timezone: Australia/Sydney", 'start_times: "09:00"']
            elif c == 7:
                a += ["max_run_alarm: 120", "min_run_alarm: 5"]
            elif c == 8:
                a += ["alarm_if_fail: 1", "alarm_if_terminated: 1", "notification_emailaddress: ops@example.com"]
            else:
                a += ["term_run_time: 240", "n_retrys: 3", "max_exit_success: 4"]
            _job(lines, nm, command=f"/sched/job_{i}.sh", attrs=a)
    return w


def feat_objects(facts: dict, rnd: random.Random, n: int):
    """Non-job JIL objects + verbs (update/override/delete_job)."""
    def w(lines, names):
        virtual_machine(lines, "VM_POOL", ["mach01", "mach02"])
        xinst(lines, "PRD")
        resource(lines, "RES_A")
        connprofile(lines, "HDP_PROF")
        jobtype(lines, "SITEJOB")
        for i in range(n):
            nm = f"OBJ_{i:04d}"
            names.append(nm)
            c = i % 7
            if c == 0:
                _job(lines, nm, jtype="SITEJOB", attrs=["resources: (RES_A, quantity=1, free=Y)", "job_load: 10"])
            elif c == 1:
                _job(lines, nm, jtype="f", machine=None,
                     attrs=["watch_file: /in/feed.dat", "watch_file_min_size: 1"])
            elif c == 2:
                _job(lines, nm, command="/obj/ftp.sh", attrs=[
                    "profile: /etc/site.profile", "application: FIN_CLOSE", "group: LEDGER",
                    "owner: svc_fin", "permission: gx,wx,me,mx,ge,we"])
            elif c == 3:
                _job(lines, nm, command="ftp -n host", jtype="FTP", attrs=[
                    "ftp_server_name: sftp.example.com", "ftp_remote_name: /out/daily.csv",
                    "ftp_local_name: /tmp/daily.csv", "ftp_transfer_direction: DOWNLOAD"])
            elif c == 4:
                _job(lines, nm, command="/obj/ws.sh", attrs=[
                    "service_desk: 1", "svcdesk_desc: auto-ticket", "svcdesk_pri: 2",
                    "envvars: ORACLE_SID=PROD,PATH=/bin"])
            elif c == 5:
                _job(lines, nm, command="/obj/del.sh", attrs=["auto_delete: 24", "auto_hold: 1", "continuous: 1"])
            else:
                _job(lines, nm, command="/obj/plain.sh", attrs=[
                    "chk_files: /ready/go.flag", "avg_runtime: 45", "box_terminator: 0", "job_terminator: 1"])
        monbro(lines, "MON_FEED", "OBJ_0001")
        blob(lines, "BLOB_OBJ", "OBJ_0000")
        glob_(lines, "GLB_CONFIG", "prod-config-blob")
        if n >= 3:
            lines.append("update_job: OBJ_0002")
            lines.append("  description: patched description")
            lines.append("")
            lines.append("override_job: OBJ_0001")
            lines.append("  machine: mach02")
            lines.append("")
            lines.append("delete_job: OBJ_0005")
            facts["deleted_jobs"] = 1
        facts["objects_block"] = True
    return w


def feat_dangling(facts: dict, rnd: random.Random, n: int):
    """Partial export: conditions/boxes/calendars pointing at missing objects."""
    def w(lines, names):
        for i in range(n):
            nm = f"PRT_{i:04d}"
            names.append(nm)
            c = i % 6
            if c == 0:
                cond = f"s(GHOST_{i})"
                facts["dangling_cond"] = facts.get("dangling_cond", 0) + 1
            elif c == 1:
                cond = f's(PRT_{i - 1:04d}) & v(MISSING_G) = "Y"' if i else None
            elif c == 2:
                cond = None
            else:
                cond = f"s(PRT_{i - 1:04d})" if i else None
            box = "GHOST_BOX" if c == 3 else None
            if box:
                facts["dangling_box"] = facts.get("dangling_box", 0) + 1
            attrs = ["run_calendar: NEVER_DEFINED_CAL"] if c == 4 else None
            _job(lines, nm, command=f"/prt/j{i}.sh", machine="GHOST_MACH", box=box, condition=cond, attrs=attrs)
        lines.append("update_job: NEVER_DEFINED_JOB")
        lines.append("  description: update before insert")
        lines.append("")
        facts["dangling_update"] = 1
    return w


def feat_windows(facts: dict, rnd: random.Random, n: int):
    """Windows-only estate: .bat/.cmd, C:\\ paths, %%FOR vars, %ENV%."""
    def w(lines, names):
        for i in range(n):
            nm = f"WIN_{i:04d}"
            names.append(nm)
            c = i % 5
            if c == 0:
                cmd = f"for %%f in (D:\\in\\{i}\\*.csv) do call D:\\bin\\load.bat %%f"
            elif c == 1:
                cmd = f"cmd /c D:\\scripts\\night_{i}.cmd %BATCHDATE% %REGION%"
            elif c == 2:
                cmd = f"D:\\apps\\ftptool\\bkftp_{i}.exe -h host -u user -p pass"
            elif c == 3:
                cmd = f"powershell -File D:\\ps\\run_{i}.ps1"
            else:
                cmd = f"robocopy D:\\src{i} E:\\dst{i} /MIR"
            cond = f"s(WIN_{i - 1:04d})" if i % 4 == 0 and i else None
            _job(lines, nm, machine="winagent", command=cmd, condition=cond)
        facts["windows_jobs"] = n
    return w


def feat_deep_nesting(facts: dict, rnd: random.Random, n: int):
    """Deep box nesting (8 levels) + a 60-deep dependency chain."""
    def w(lines, names):
        parent = None
        depth = 8
        for d in range(depth):
            nm = f"NEST_L{d:02d}"
            names.append(nm)
            _job(lines, nm, jtype="b", machine=None, box=parent,
                 attrs=["box_success: s(CHAIN_00059)"] if d == 0 else None)
            parent = nm
        chain = min(n, 60)
        prev = None
        for i in range(chain):
            nm = f"CHAIN_{i:05d}"
            names.append(nm)
            _job(lines, nm, command=f"/deep/c{i}.sh", box=parent,
                 condition=f"s(CHAIN_{i - 1:05d})" if prev else None)
            prev = nm
        facts["chain_jobs"] = chain
        facts["nesting_depth"] = depth
    return w


def feat_scale(facts: dict, rnd: random.Random, n: int):
    """Flat estate at scale: everything XS-ish; measures runtime, not coverage."""
    def w(lines, names):
        for i in range(n):
            nm = f"S_{i:07d}"
            names.append(nm)
            _job(lines, nm, command=f"/bin/job_{i % 997}.sh", machine=f"mach{(i % 40):02d}",
                 condition=f"s(S_{i - 1:07d})" if i % 20 == 0 and i else None)
    return w


ARCHETYPES: dict[str, list] = {
    "flat-cmd":           [feat_core],
    "sap-erp":            [feat_native_erp, feat_schedules],
    "mainframe-boundary": [feat_mainframe, feat_core],
    "deep-box":           [feat_deep_nesting, feat_boxes],
    "cross-instance":     [feat_cross_instance, feat_objects],
    "partial-export":     [feat_dangling, feat_objects],
    "mixed-features":     [feat_objects, feat_schedules, feat_native_erp, feat_cross_instance],
    "windows-only":       [feat_windows],
    "tiny":               [feat_core],
    "big-flat":           [feat_scale],
}


def generate(archetype: str, n_jobs: int, seed: int) -> tuple[str, dict[str, Any]]:
    """Return (jil_text, facts) -- byte-identical for identical inputs."""
    rnd = random.Random(seed)
    facts: dict[str, Any] = {"archetype": archetype, "seed": seed, "n_requested": n_jobs}
    lines = [
        f"/* synthetic estate -- archetype={archetype} seed={seed} */",
        "# generated for conformance testing; not customer data",
        "",
    ]
    machine(lines, "mach01", opsys="linux")
    machine(lines, "mach02", opsys="linux")
    machine(lines, "winagent", opsys="windows")
    machine(lines, "mfsrv01", opsys="zos")
    machine(lines, "remhost")
    machine(lines, "db1")

    per = max(1, n_jobs // len(ARCHETYPES[archetype]))
    names: list[str] = []
    for feat in ARCHETYPES[archetype]:
        feat(facts, rnd, per)(lines, names)

    facts["n_jobs_defined"] = len(names)
    facts["job_names"] = names
    return "\n".join(lines) + "\n", facts
