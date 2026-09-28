# JIL importer stress harness

Independent proof harness for the tolerant JIL importer. It does not import
anything from `autosys/`. Three pieces:

| file | purpose |
|---|---|
| `generate.py` | deterministic corpus generator + `manifest` describing exactly what was written |
| `verify.py` | reads the manifest, the corpus and the importer's SQLite DB, checks the conservation contract |
| `jilcommon.py` | shared helpers (decode, newline normalisation, comment-gap check) |
| `pdf_examples.json` | the 753 real PDF JIL examples, verbatim |

## Run

```bash
python scripts/jil_stress/generate.py --out /data/stress600k --jobs 600000 --seed 1   # ~30 s, ~300 MB, ~160k files
python scripts/jil_stress/generate.py --out /data/stress3k   --jobs 3000   --seed 2   # quick corpus
# ... run the importer over the corpus directory into sqlite:///imp.db ...
python scripts/jil_stress/verify.py --corpus /data/stress600k --db sqlite:///imp.db     # exit 1 on any violation
```

Output is a pure function of `(--jobs, --seed)` (byte-identical for any `--workers`).
`verify.py` options: `--sample N` (clean jobs attribute-checked, default 2000, 0 = all),
`--examples N`, `--strict-all` (ignore the per-stanza `accept` lists: strict
class->disposition mapping), `--strict-soft`, `--path-prefix P` (strip P from
`ujo_jil_file.path`), `--json FILE`.
The importer's `ujo_jil_file.path` may be absolute, relative, or any prefix of the corpus-relative
path: the verifier matches on the longest suffix present in the manifest.

## Corpus mix (default, N = 600 000)

`N` = number of distinct valid `insert_job` names (exactly; `counts.distinct_valid_insert_jobs`).
Everything else is "extras" on top: PDF examples, hostile stanzas, duplicate stanzas, non-job objects.

* **Realistic estates (~all N core jobs)**, files sized: 1 stanza (45 %), 2-3 (25 %), 4-8 (30 %), 2 % of
  files 5-60 stanzas, plus 4 "huge" files (~15 000 stanzas each = 10 % of N). Sorted-path order matters for
  duplicates. Job variants (weights per 10 000): plain 3900, packed (several `key: value` per line) 700,
  quoted values with blanks/colons 700, box+children 800, conditions (s/f/d/n/t, success(), &, |, AND/OR,
  lookback `s(a,12.00)`, `value(G)=5`, `exitcode(a)=0`) 800, schedules (start_times, days_of_week, calendars) 600,
  alarms/retries 400, indented 400, multi-line quoted descriptions (containing `insert_job:` lines) 250,
  `<auto_blobt>` blobs whose text contains a fake `insert_job: evil` 200, comments inside quotes / `/tmp/*/x` globs
  200+200, `key:value` no space, `status: on_ice`, `\:` `\,` `\"` escapes, unquoted `http://h:8080` values,
  file watchers (`f`, `fw`, `filewatch`), ~62 non-CMD job types incl. `URL`, `WSDL_URL`, `endpoint_URL`,
  `ftp_use_SSL` mixed-case keys (300), mid-stanza comments (300), job_type aliases `c b f cmd box fw FT FTP` in mixed case,
  extreme values (0.02 %: 400 KB-1 MB description, 64 KB command).
  **warn family (~6.2 %)**: CMD without command, CMD without machine, BOX with command, job_type typos,
  user-defined types (`7`, `MY_TYPE`), `n_retrys: abc`, unknown attr `cpu_usage: 80`, invalid `status:`, missing
  job_type, 1 500-4 000 attributes in one stanza, NUL/control characters, junk after the header.
* **Supporting objects (~2 % of N as extras)**: machines (real; virtual with repeated `machine:` members), globals,
  calendars, resources, monbro, xinst, blobs, connection profiles, job types (typed tables) and
  views/filters/alert policies/`delete_user` (archive_only), delete_* of never-defined objects.
* **PDF examples**: all 753 verbatim, 1-6 per file separated by `# pdf example` comment lines
  (kind `pdf_example`, expected_class `any`, file is *not* exact).
* **Hostile files** (24 files each at N=600k, `max(2, N/25000)`): empty, whitespace-only, comment-only, binary, prose,
  JSON, shell/CSV/XML/python text, stray lines before the first stanza, unknown sub-commands (`insert_bogus:`),
  missing job names, unterminated quote / `<auto_blobt>` / `/*` (at end of file, and a variant where real
  stanzas follow the unterminated `/*`), NUL bytes between stanzas, mixed/invalid byte sequences.
* **Encodings** (label per file): utf-8 84 %, utf-8 BOM 3 %, cp1252 4 % (smart quotes, euro), latin-1 3 %,
  UTF-16 LE/BE with BOM 3 % each, mixed_invalid, binary. Line endings LF 80 % / CRLF 15 % / CR 5 %; 10 % have
  no trailing newline. Extensions vary (`.jil .JIL .txt .jl .jil.bak .conf .sh`, none). Nested dirs 1-4 deep.
* **Duplicates / mutation**: same name twice in a file (identical and different), cross-file redefinition of
  an earlier (sorted path) job, insert then update_job / override_job / `override_job: x delete` / delete_job /
  delete_box / rename_job, and update/override/delete of jobs that never existed.

## Manifest

`manifest.json` = summary (`counts`: files, bytes, stanzas by expected_class / kind, encodings, ...;
`files_index`: name of the per-file index).  `manifest_files.jsonl` = one JSON object per file, in sorted-path order:

```
{path, encoding, eol, trailing_newline, sha256, size, kind, exact,
 [count_min, count_max, accept]            # only when exact == false
 stanzas: [ {seq, directive, object_name, kind, expected_class, start_line, end_line,
             job, strict, accept, key, key_soft, dup_first_key, dup_of, tab, name_check, ...} ]}
```

* `encoding`: `utf-8 | utf-8-sig | cp1252 | latin-1 | utf-16-le-bom | utf-16-be-bom | mixed_invalid | binary`.
  Line/`start_line`/`end_line` numbers are 1-based over the newline-normalised decoded text.
* `expected_class`: `clean | warn | archive_only | quarantine | duplicate`, plus `any` for PDF examples.
* `exact: true` means the file's stanza list is fully deterministic (the importer must produce exactly that many
  stanzas, in that order, same directive / object_name). `exact: false` (PDF, non-JIL, binary, mixed_invalid,
  unterminated-`/*`-then-stanzas, NUL-between-stanzas): only `count_min..count_max`, conservation and job
  presence are checked.
* `strict: false` + `accept: [...]`: the semantics are ambiguous (e.g. unterminated quote, `update_job` of a missing job,
  header junk, `rename_job`, unknown types); any disposition in `accept` (or the class default) is fine.
* `job`: `true` = distinct valid insert_job that must be in `ujo_job`; `"optional"` = may or may not survive (delete victims,
  override-delete target); `"maybe"` = unterminated hostile stanzas.
* `key`: exact expected `command/machine/owner/box_name/condition/start_times` (unquoted value; commands > 2000 chars stored as
  `{sha256, len}`). `key_soft`: same, for constructs whose semantics are debatable (unquoted `http://h:8080` value, `job_type`
  normalised to CMD/BOX/FILEWATCH): reported as SOFT.
* `dup_first_key`: on a DUPLICATE stanza, what the *first* definition's command/machine/owner were: `ujo_job` must still hold them.
* `tab`: `[table, column]` typed row a clean machine/global/calendar stanza must create.

## Contract checked by `verify.py`

**Stanza model (the generator's ground truth).** A stanza starts at a top-level directive line
(`insert_/update_/delete_/override_/rename_<x>:`), never inside a quoted value, `<auto_blobt>` block or comment, and runs to
the next one. A maximal run of stray text before the first directive is ONE orphan stanza (`directive NULL`).
Blank lines, `#` lines and complete `/* */` comments may sit outside every stanza.

| # | check |
|---|---|
| a | every corpus file has a `ujo_jil_file` row (empty/binary/non-JIL too); `sha256` and `size_bytes` equal the real file; `status`, `encoding` non-empty (encoding label mismatch is SOFT) |
| b | per file: number of stanza rows == manifest count (exact files) or within `[count_min, count_max]`; `seq` contiguous (0- or 1-based) and unique; `n_stanzas` equals row count; per position `directive`/`object_name` equal the manifest (case-insensitive directive) |
| c | every `raw_text` occurs verbatim (after decoding with the recorded encoding and normalising CRLF/CR to LF), in order, non-overlapping; the source text outside all stanzas contains only blank lines, `#` lines, complete `/* */` comments, or an unterminated `/*` tail that swallows no directive line. So no non-comment input line is lost. For `mixed_invalid`/`binary` files runs of non-ASCII characters are collapsed before comparing |
| d | every `job: true` name is in `ujo_job` (PK => once); no name has two LOADED/LOADED_WITH_WARNINGS `insert_job` stanzas; later definitions are `duplicate` and the winner still carries the first definition's command/machine/owner |
| e | disposition matches `expected_class` (`clean`->LOADED, `warn`->LOADED_WITH_WARNINGS, `archive_only`->ARCHIVE_ONLY, `quarantine`->QUARANTINED, `duplicate`->DUPLICATE) or the stanza's `accept` list; mismatches grouped by kind with examples |
| f | `key` attributes of clean jobs equal `ujo_job` (sample of `--sample`, deterministic by name hash; all of them for duplicate winners); `condition` compared modulo whitespace/case and `success/s`, `AND/&`, `OR/\|` aliases; `start_times` modulo quotes/blanks |
| g | dispositions are the five known values and sum to the row count; every stanza row belongs to a file row; row totals reconcile; clean machine/global/calendar stanzas exist in `ujo_machine`/`ujo_glob_var`/`ujo_calendar` |

Memory: O(largest file). The stanza table is streamed once `ORDER BY file_id, seq`; `ujo_job` is probed in batches of 900 names.
