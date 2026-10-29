#!/usr/bin/env bash
# autosys_history_export.sh — TEMPLATE for extracting run history from a
# real AutoSys instance into the CSV our importer accepts.
#
# Run this ON THE CLIENT'S AutoSys server (or wherever their AutoSys CLI
# tools / DB client are available), not in this repo's own environment.
# It produces exactly the file `autosys history import` / POST
# /api/v1/history/import consume -- see autosys/parser/run_history_ingest.py's
# module docstring for the full column contract.
#
# ---------------------------------------------------------------------------
# WHY THIS IS A TEMPLATE, NOT A FINISHED TOOL
# ---------------------------------------------------------------------------
# We have not had a real AutoSys instance or a sample export to develop and
# test this against (see dev/task9-cost-calculator/05-cost-estimation-
# questions.md S21/S22). Three real extraction routes exist and which one
# applies depends entirely on what a given site already has:
#
#   1. A DBA-run SQL export directly against the Event Server DB
#      (ujo_proc_event / ujo_jobst). Most reliable and most complete, but
#      needs DB access and a DBA to write the query -- Section A below is a
#      STARTING POINT for that query, not a tested one.
#   2. AutoSys's own autobacklog / autorep CLI tools. Section B below
#      sketches this, but the exact columns autobacklog emits, and the
#      version-dependent shape of `autorep -J <job> -d`, were not verified
#      against a real product install -- treat Section B as a sketch to
#      adapt, not a working command.
#   3. Whatever AutoSys's existing NSM/SNMP/Remedy/ServiceNow integration
#      has already been accumulating (often longer-retained than the Event
#      Server DB itself -- see S22a). That export comes from a completely
#      different system this script has no visibility into; Section C just
#      documents the target shape to map onto.
#
# Pick whichever of the three actually exists at the site, adapt that
# section, delete the rest. Validate the result with:
#   autosys history import --dry-run <output.csv>     (not yet implemented;
#       until it is, a small sample through the real import is the check)
#
# ---------------------------------------------------------------------------
# OUTPUT CONTRACT (unchanged regardless of which section produced it)
# ---------------------------------------------------------------------------
# Header required. Comma, tab or pipe delimited -- auto-detected. Required
# columns: job_name, status, start_time. Optional: end_time, exit_code,
# machine, attempt, run_id. status is any of AutoSys's own terminal status
# spellings (SU/SUCCESS/4, FA/FAILURE/5, TE/TERMINATED/6, OI/ON_ICE/7,
# NE/ON_NOEXEC/16) -- anything else (RUNNING, STARTING, ...) is a run that
# had not finished at export time and is skipped, not an error.
#
# Timestamps: ISO 8601 with a UTC offset is safest ("2026-07-01T09:00:00Z"
# or "...+05:00"). A naive timestamp with no offset is accepted too, but the
# WHOLE FILE is then rejected unless --timezone is passed to the importer --
# silently assuming UTC can shift an estate's history by up to 14 hours.

set -euo pipefail

OUT="${1:-autosys_run_history.csv}"
START_DATE="${2:-$(date -d '90 days ago' +%Y-%m-%d 2>/dev/null || date -v-90d +%Y-%m-%d)}"

echo "job_name,status,start_time,end_time,exit_code,machine,attempt,run_id" > "$OUT"

# ===========================================================================
# Section A — direct SQL against the Event Server DB (needs DB access)
# ===========================================================================
# Adapt the table/column names to your AutoSys version and DB engine
# (Oracle / SQL Server / Sybase all differ in exact syntax). The real
# event-history table is commonly named something like ujo_proc_event or
# ujo_jobst_arch -- confirm the actual name with your AutoSys DBA before
# running this; it was not verified against a real schema.
#
#   sqlplus -s user/pass@db <<SQL > "$OUT.raw"
#     SET COLSEP ',' PAGESIZE 0 FEEDBACK OFF HEADING OFF TRIMSPOOL ON;
#     SELECT job_name || ',' || status || ',' ||
#            TO_CHAR(start_time, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') || ',' ||
#            TO_CHAR(end_time,   'YYYY-MM-DD"T"HH24:MI:SS"Z"') || ',' ||
#            exit_code || ',' || machine || ',' || ntry || ',' || run_id
#     FROM   ujo_proc_event
#     WHERE  start_time >= TO_DATE('${START_DATE}', 'YYYY-MM-DD');
#   SQL
#   cat "$OUT.raw" >> "$OUT"

# ===========================================================================
# Section B — autorep / autobacklog (sketch; verify against your version)
# ===========================================================================
#   autobacklog -J ALL -s "${START_DATE}" > "$OUT.raw"
#   # Then reshape autobacklog's actual columns into the header above --
#   # its exact output format depends on your AutoSys version and was not
#   # available to verify here.

# ===========================================================================
# Section C — an existing NSM/SNMP/Remedy/ServiceNow export
# ===========================================================================
# If AutoSys already forwards every job completion to a downstream system
# (see S22a), export from THAT system instead and map its fields onto the
# header above. The native webhook payload this simulator models for that
# integration is {alarm_id, job_name, status, ts} -- if your real
# integration looks similar, job_name -> job_name, status -> status,
# ts -> start_time is usually enough; end_time/exit_code/machine/attempt
# are optional and can be left blank.
#
# IMPORTANT: check whether that feed fires on every run or only on
# failures/alarms (S22a). A failures-only feed can give us a failure
# *count* but not a failure *rate* -- we would have no denominator.

echo "Wrote header to $OUT -- fill in Section A, B or C above for your site," >&2
echo "then send $OUT however you'd send any file (this needs no DB access on our end)." >&2
