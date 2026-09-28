#!/usr/bin/env python3
"""Independent verifier for the tolerant JIL importer (conservation contract).

    python scripts/jil_stress/verify.py --corpus DIR --db sqlite:///path.db [--sample 2000]

Reads DIR/manifest.json + DIR/manifest_files.jsonl (written by generate.py),
the source files in DIR, and the importer's SQLite DB.  Never imports autosys/.
Exit status 0 = every hard check passed, 1 = at least one violation.
Memory use is O(largest single file): the stanza table is streamed once
ordered by (file_id, seq) and ujo_job is probed in batches of 900 names.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import zlib
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jilcommon import decode, lenient, lost_lines, norm_newlines, LENIENT_ENCODINGS  # noqa: E402

DISPS = ('LOADED', 'LOADED_WITH_WARNINGS', 'ARCHIVE_ONLY', 'QUARANTINED', 'DUPLICATE')
CLASS_DISP = {
    'clean': {'LOADED'}, 'warn': {'LOADED_WITH_WARNINGS'}, 'archive_only': {'ARCHIVE_ONLY'},
    'quarantine': {'QUARANTINED'}, 'duplicate': {'DUPLICATE'}, 'any': set(DISPS),
}
ENC_ALIASES = {
    'utf-8': {'utf-8', 'utf8', 'ascii', 'us-ascii'}, 'utf-8-sig': {'utf-8-sig', 'utf8-sig', 'utf-8-bom', 'utf-8 bom'},
    'cp1252': {'cp1252', 'windows-1252'}, 'latin-1': {'latin-1', 'latin1', 'iso-8859-1', 'iso8859-1'},
    'utf-16-le-bom': {'utf-16', 'utf-16-le', 'utf-16le', 'utf-16-le-bom'},
    'utf-16-be-bom': {'utf-16', 'utf-16-be', 'utf-16be', 'utf-16-be-bom'},
}


class Report:
    def __init__(self, max_examples):
        self.hard = {}
        self.soft = {}
        self.maxex = max_examples

    def _add(self, d, name, example):
        e = d.setdefault(name, [0, []])
        e[0] += 1
        if len(e[1]) < self.maxex:
            e[1].append(example)

    def hard_fail(self, name, example):
        self._add(self.hard, name, example)

    def soft_fail(self, name, example):
        self._add(self.soft, name, example)


class _PgCursor:
    """sqlite3-style cursor over psycopg2 (execute() returns the cursor, '?' params)."""
    def __init__(self, conn):
        self._c = conn.cursor()

    def execute(self, sql, params=()):
        m = re.match(r"\s*PRAGMA table_info\((\w+)\)", sql)
        if m:
            sql = ("SELECT ordinal_position-1, column_name FROM information_schema.columns "
                   f"WHERE table_name = '{m.group(1)}' ORDER BY ordinal_position")
        self._c.execute(sql.replace('?', '%s'), list(params) if params else None)
        return self

    def __iter__(self):
        return iter(self._c)

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()


class _PgConn:
    def __init__(self, url):
        import psycopg2
        self._conn = psycopg2.connect(url)
        self._conn.set_session(readonly=True)

    def cursor(self):
        return _PgCursor(self._conn)


def open_db(url):
    if url.startswith(('postgresql://', 'postgres://')):
        return _PgConn(url)
    m = re.match(r'sqlite:///(.*)$', url)
    path = m.group(1) if m else url
    if not os.path.exists(path):
        raise SystemExit(f'DB not found: {path}')
    return sqlite3.connect(f'file:{os.path.abspath(path)}?mode=ro', uri=True)


def norm_cond(c):
    if c is None:
        return None
    s = re.sub(r'\s+', '', str(c)).lower()
    s = s.replace('&&', '&').replace('||', '|')
    s = re.sub(r'\band\b', '&', s)
    s = re.sub(r'\bor\b', '|', s)
    s = s.replace('and', '&') if False else s
    for full, short in (('success', 's'), ('failure', 'f'), ('done', 'd'), ('notrunning', 'n'),
                        ('terminated', 't'), ('exitcode', 'e'), ('exit_code', 'e')):
        s = re.sub(r'(?<![a-z_])' + full + r'\(', short + '(', s)
    s = s.replace('&', '&').replace('|', '|')
    s = re.sub(r'(?<=[)\d])and(?=[a-z(])', '&', s)
    s = re.sub(r'(?<=[)\d])or(?=[a-z(])', '|', s)
    return s


def norm_st(v):
    return None if v is None else re.sub(r'[\s"]', '', str(v))


def eq_val(exp, got):
    if isinstance(exp, dict):
        return got is not None and len(got) == exp['len'] and hashlib.sha256(got.encode()).hexdigest() == exp['sha256']
    return got == exp


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--corpus', required=True)
    ap.add_argument('--db', required=True)
    ap.add_argument('--sample', type=int, default=2000, help='clean jobs to attribute-check (0 = all)')
    ap.add_argument('--examples', type=int, default=3)
    ap.add_argument('--strict-soft', action='store_true', help='treat soft findings as failures')
    ap.add_argument('--strict-all', action='store_true', help='ignore per-stanza accept lists (strict class mapping)')
    ap.add_argument('--path-prefix', default='', help='prefix to strip from ujo_jil_file.path before matching')
    ap.add_argument('--json', default='', help='write machine-readable report here')
    a = ap.parse_args()
    t0 = time.time()
    corpus = os.path.abspath(a.corpus)
    summary = json.load(open(os.path.join(corpus, 'manifest.json')))
    mpath = os.path.join(corpus, summary['files_index'])
    R = Report(a.examples)
    db = open_db(a.db)
    cur = db.cursor()

    # ---- pass 0: index the manifest (path -> byte offset) --------------------------------
    offsets = {}
    with open(mpath, 'rb') as f:
        pos = 0
        for line in f:
            i = line.index(b'"path":"') + 8
            j = line.index(b'"', i)
            # paths never contain quotes/backslashes (generator uses [a-z0-9_./])
            offsets[line[i:j].decode()] = pos
            pos += len(line)
    n_manifest_files = len(offsets)
    mf = open(mpath, 'rb')

    def load_rec(path):
        mf.seek(offsets[path])
        return json.loads(mf.readline())

    # ---- file table ------------------------------------------------------------------
    cols = [r[1] for r in cur.execute('PRAGMA table_info(ujo_jil_file)')]
    for need in ('file_id', 'path', 'sha256', 'size_bytes', 'encoding', 'n_stanzas', 'n_issues', 'status'):
        if need not in cols:
            raise SystemExit(f'ujo_jil_file lacks column {need}')
    scols = [r[1] for r in cur.execute('PRAGMA table_info(ujo_jil_stanza)')]
    for need in ('stanza_id', 'file_id', 'seq', 'start_line', 'end_line', 'directive', 'object_name', 'disposition', 'raw_text'):
        if need not in scols:
            raise SystemExit(f'ujo_jil_stanza lacks column {need}')

    fid_by_path = {}
    frow = {}
    unmatched_db_paths = 0
    prefix = a.path_prefix
    for fid, path, sha, size, enc, nst, niss, status in cur.execute(
            'SELECT file_id, path, sha256, size_bytes, encoding, n_stanzas, n_issues, status FROM ujo_jil_file'):
        p = path or ''
        if prefix and p.startswith(prefix):
            p = p[len(prefix):]
        rel = None
        if p in offsets:
            rel = p
        else:
            q = os.path.normpath(p).replace(os.sep, '/')
            if q.startswith(corpus.rstrip('/') + '/'):
                q = q[len(corpus.rstrip('/')) + 1:]
            if q in offsets:
                rel = q
            else:
                parts = q.split('/')
                for i in range(1, len(parts)):
                    s = '/'.join(parts[i:])
                    if s in offsets:
                        rel = s
                        break
        if rel is None:
            unmatched_db_paths += 1
            R.soft_fail('db_file_row_not_in_manifest', path)
            continue
        if rel in fid_by_path:
            R.hard_fail('a_duplicate_file_rows_for_path', f'{rel} file_ids {fid_by_path[rel]},{fid}')
            continue
        fid_by_path[rel] = fid
        frow[fid] = (rel, sha, size, enc, nst, niss, status)
    path_by_fid = {v: k for k, v in fid_by_path.items()}

    # ---- counters ----------------------------------------------------------------------
    disp_actual = Counter()            # over stanza rows belonging to manifest files
    exp_vs_act = defaultdict(Counter)  # expected class -> actual disposition (exact files)
    st_rows_total = 0
    st_rows_per_file_sum_n = 0
    exact_expected_total = 0
    exact_files_seen = 0
    manifest_stanzas_total = 0
    kind_mismatch = defaultdict(lambda: [0, []])
    n_lenient_ok = 0
    lost_line_count = 0
    files_checked = 0

    keyed_total = max(1, summary['counts'].get('keyed_clean_jobs', 1))
    p_sample = 1.0 if a.sample == 0 else min(1.0, a.sample / keyed_total)
    thresh = int(p_sample * 1_000_000)
    jobq = []      # (name, mode, expected, ctx)
    tabq = defaultdict(list)
    stats = Counter()

    def flush_jobs(force=False):
        while len(jobq) >= 900 or (force and jobq):
            batch, jobq[:] = jobq[:900], jobq[900:]
            names = list({b[0] for b in batch})
            q = ('SELECT job_name, job_type, command, machine, owner, box_name, condition, start_times '
                 'FROM ujo_job WHERE job_name IN (%s)' % ','.join('?' * len(names)))
            got = {r[0]: r for r in cur.execute(q, names)}
            for name, mode, exp, ctx in batch:
                r = got.get(name)
                if mode == 'exist':
                    stats['job_exist_checked'] += 1
                    if r is None:
                        R.hard_fail('d_valid_insert_job_missing_from_ujo_job', f'{name} ({ctx})')
                    continue
                if r is None:
                    if mode in ('key',):
                        R.hard_fail('f_job_row_missing_for_key_check', f'{name} ({ctx})')
                    else:
                        R.hard_fail('d_duplicate_winner_missing', f'{name} ({ctx})')
                    continue
                _, jt, command, machine, owner, box, cond, stt = r
                stats['job_key_checked' if mode == 'key' else 'dup_winner_checked'] += 1
                gotmap = {'command': command, 'machine': machine, 'owner': owner, 'box_name': box,
                          'condition': cond, 'start_times': stt}
                soft = mode.startswith('soft')
                for k, ev in exp.items():
                    gv = gotmap.get(k)
                    if k == 'condition':
                        ok = norm_cond(ev) == norm_cond(gv)
                    elif k == 'start_times':
                        ok = norm_st(ev) == norm_st(gv)
                    elif k == 'job_type':
                        ok = (jt or '').upper() == ev
                    else:
                        ok = eq_val(ev, gv)
                    if not ok:
                        sh = lambda v: (v[:60] + '...') if isinstance(v, str) and len(v) > 60 else v
                        msg = f'{name} {k}: expected {sh(ev) if not isinstance(ev, dict) else ev} got {sh(gv)} ({ctx})'
                        if mode == 'key':
                            R.hard_fail(f'f_attr_mismatch_{k}', msg)
                        elif mode == 'dup':
                            R.hard_fail(f'd_first_occurrence_did_not_win_{k}', msg)
                        else:
                            R.soft_fail(f'f_soft_attr_mismatch_{k}', msg)

    def flush_tabs(force=False):
        for (tbl, col), lst in list(tabq.items()):
            while len(lst) >= 900 or (force and lst):
                batch, lst[:] = lst[:900], lst[900:]
                have = {r[0] for r in cur.execute(
                    f'SELECT {col} FROM {tbl} WHERE {col} IN (%s)' % ','.join('?' * len(batch)), [b[0] for b in batch])}
                for nm, ctx in batch:
                    stats['typed_row_checked'] += 1
                    if nm not in have:
                        R.hard_fail(f'typed_row_missing_{tbl}', f'{nm} ({ctx})')

    # ---- per-file verification ----------------------------------------------------------
    def verify_file(rec, rows):
        nonlocal st_rows_total, exact_expected_total, exact_files_seen, manifest_stanzas_total
        nonlocal lost_line_count, files_checked, n_lenient_ok
        path = rec['path']
        fid = fid_by_path[path]
        _, dsha, dsize, denc, dnst, dniss, dstatus = frow[fid]
        files_checked += 1
        try:
            data = open(os.path.join(corpus, path), 'rb').read()
        except OSError as e:
            R.hard_fail('corpus_file_unreadable', f'{path}: {e}')
            return
        if hashlib.sha256(data).hexdigest() != rec['sha256']:
            R.hard_fail('corpus_file_changed_since_manifest', path)
        if dsha != rec['sha256']:
            R.hard_fail('a_file_row_sha256_mismatch', f'{path}: db {dsha}')
        if dsize is not None and dsize != rec['size']:
            R.hard_fail('a_file_row_size_mismatch', f'{path}: db {dsize} vs {rec["size"]}')
        if not dstatus:
            R.hard_fail('a_file_row_status_empty', path)
        if denc is None or denc == '':
            R.hard_fail('a_file_row_encoding_empty', path)
        elif rec['encoding'] in ENC_ALIASES and str(denc).lower() not in ENC_ALIASES[rec['encoding']]:
            R.soft_fail('a_file_row_encoding_label_differs', f'{path}: db {denc!r} expected {rec["encoding"]}')
        n = len(rows)
        if dnst is not None and dnst != n:
            R.hard_fail('b_file_row_n_stanzas_disagrees_with_stanza_rows', f'{path}: n_stanzas={dnst} rows={n}')
        st_rows_total += n
        # seq contiguity / duplicates
        seqs = [r[0] for r in rows]
        if n:
            s0 = seqs[0]
            if s0 not in (0, 1) or seqs != list(range(s0, s0 + n)):
                R.hard_fail('b_seq_not_contiguous', f'{path}: {seqs[:12]}{"..." if n > 12 else ""}')
        # counts
        stz = rec['stanzas']
        manifest_stanzas_total += len(stz)
        if rec['exact']:
            exact_files_seen += 1
            exact_expected_total += len(stz)
            if n != len(stz):
                R.hard_fail('b_stanza_count_mismatch',
                            f'{path} kind={rec["kind"]}: expected {len(stz)} got {n}')
        else:
            if not (rec.get('count_min', 0) <= n <= rec.get('count_max', 10 ** 9)):
                R.hard_fail('b_stanza_count_out_of_range', f'{path}: {n} not in [{rec.get("count_min")},{rec.get("count_max")}]')
        # decode source
        text = norm_newlines(decode(data, rec['encoding']))
        lenient_mode = rec['encoding'] in LENIENT_ENCODINGS
        if lenient_mode:
            text = lenient(text)
        line_starts = None
        cursor = 0
        ranges = []
        all_found = True
        for seq, sl, el, directive, oname, disp, raw in rows:
            if disp not in DISPS:
                R.hard_fail('g_unknown_disposition', f'{path} seq {seq}: {disp!r}')
            disp_actual[disp] += 1
            if raw is None or raw == '':
                R.hard_fail('c_empty_raw_text', f'{path} seq {seq} disp={disp}')
                all_found = False
                continue
            r = norm_newlines(raw)
            if lenient_mode:
                r = lenient(r)
            idx = -1
            if sl and sl > 0:
                if line_starts is None:
                    line_starts = [0]
                    p = text.find('\n')
                    while p != -1:
                        line_starts.append(p + 1)
                        p = text.find('\n', p + 1)
                if sl <= len(line_starts):
                    off = line_starts[sl - 1]
                    lo = max(cursor, off)
                    idx = text.find(r, lo, off + len(r) + 4096)
            if idx < 0:
                idx = text.find(r, cursor)
            if idx < 0:
                all_found = False
                if text.find(r) >= 0:
                    R.hard_fail('c_raw_text_out_of_order_or_overlapping', f'{path} seq {seq}')
                else:
                    R.hard_fail('c_raw_text_not_verbatim_in_source',
                                f'{path} seq {seq} disp={disp} raw={r[:70]!r}')
                continue
            ranges.append((idx, idx + len(r)))
            cursor = idx + len(r)
        if all_found:
            prev = 0
            gaps = [(prev, ranges[0][0])] if ranges else [(0, len(text))]
            for (s1, e1), (s2, e2) in zip(ranges, ranges[1:]):
                gaps.append((e1, s2))
            if ranges:
                gaps.append((ranges[-1][1], len(text)))
            for g0, g1 in gaps:
                if g1 > g0:
                    ll = lost_lines(text[g0:g1])
                    if ll:
                        lost_line_count += len(ll)
                        R.hard_fail('c_input_line_lost_not_in_any_stanza',
                                    f'{path} (+{len(ll)} lines): {ll[0][:80]!r}')
        # ---- alignment + disposition (exact files only) ----
        if rec['exact'] and n == len(stz):
            for ent, row in zip(stz, rows):
                seq, sl, el, directive, oname, disp, raw = row
                exp_dir = ent['directive']
                got_dir = (directive or None)
                if (exp_dir or None) != (got_dir.lower() if got_dir else None):
                    R.hard_fail('b_directive_mismatch', f'{path} seq {seq}: expected {exp_dir!r} got {directive!r}')
                if ent.get('name_check', True) and (ent['object_name'] or None) != (oname or None):
                    R.hard_fail('b_object_name_mismatch', f'{path} seq {seq}: expected {ent["object_name"]!r} got {oname!r}')
                if ent['start_line'] != sl or ent['end_line'] != el:
                    R.soft_fail('b_line_span_differs', f'{path} seq {seq}: expected {ent["start_line"]}-{ent["end_line"]} got {sl}-{el}')
                cls = ent['expected_class']
                exp_vs_act[cls][disp] += 1
                allowed = set(CLASS_DISP[cls])
                if not a.strict_all and ent.get('accept'):
                    allowed |= set(ent['accept'])
                if disp not in allowed:
                    k = (ent['kind'], cls, disp)
                    e = kind_mismatch[k]
                    e[0] += 1
                    if len(e[1]) < a.examples:
                        e[1].append(f'{path} seq {seq} {ent["object_name"]}')
                elif cls in ('clean', 'warn') and ent.get('strict', True) is False:
                    n_lenient_ok += 1
        # ---- job / typed-table expectations (independent of segmentation) ----
        for ent in stz:
            job = ent.get('job')
            ctx = f'{path} seq {ent["seq"]} {ent["kind"]}'
            if ent['directive'] == 'insert_job' and ent['object_name']:
                nm = ent['object_name']
                if job is True:
                    jobq.append((nm, 'exist', None, ctx))
                sampled = (zlib.crc32(nm.encode()) % 1_000_000) < thresh
                if ent.get('key') and job is True and sampled:
                    jobq.append((nm, 'key', ent['key'], ctx))
                if ent.get('key_soft') and job is True and sampled:
                    jobq.append((nm, 'soft', ent['key_soft'], ctx))
                if ent.get('dup_first_key'):
                    jobq.append((nm, 'dup', ent['dup_first_key'], ctx))
            if ent.get('tab') and ent.get('strict', True) and ent['expected_class'] == 'clean':
                tabq[tuple(ent['tab'])].append((ent['object_name'], ctx))
        flush_jobs()
        flush_tabs()

    # ---- pass 1: stream stanza rows grouped by file ----------------------------------------
    visited = set()
    orphan_rows = 0
    cur2 = db.cursor()
    cur2.execute('SELECT file_id, seq, start_line, end_line, directive, object_name, disposition, raw_text, raw_escaped '
                 'FROM ujo_jil_stanza ORDER BY file_id, seq')
    group_fid = None
    group = []

    def process_group(fid, grp):
        nonlocal orphan_rows
        if fid not in path_by_fid:
            orphan_rows += len(grp)
            R.hard_fail('b_stanza_rows_for_unknown_file_id', f'file_id={fid} ({len(grp)} rows)')
            return
        path = path_by_fid[fid]
        visited.add(path)
        verify_file(load_rec(path), grp)

    for row in cur2:
        fid = row[0]
        if fid != group_fid:
            if group:
                process_group(group_fid, group)
            group_fid, group = fid, []
        row = list(row[1:])
        if row[-1]:          # NULs were escaped by the importer
            row[6] = re.sub(r'\\(.)', lambda m: '\x00' if m.group(1) == '0' else m.group(1), row[6])
        group.append(tuple(row[:-1]))
    if group:
        process_group(group_fid, group)

    # files without any stanza row
    for path in offsets:
        if path in visited:
            continue
        if path not in fid_by_path:
            R.hard_fail('a_no_ujo_jil_file_row', path)
            continue
        verify_file(load_rec(path), [])
    flush_jobs(True)
    flush_tabs(True)

    # ---- global checks ------------------------------------------------------------------------
    for name, cnt in cur.execute(
            "SELECT object_name, COUNT(*) FROM ujo_jil_stanza WHERE lower(directive)='insert_job' "
            "AND disposition IN ('LOADED','LOADED_WITH_WARNINGS') AND object_name IS NOT NULL "
            "GROUP BY object_name HAVING COUNT(*) > 1 LIMIT 1000"):
        R.hard_fail('d_insert_job_loaded_more_than_once', f'{name} x{cnt}')
    db_total = cur.execute('SELECT COUNT(*) FROM ujo_jil_stanza').fetchone()[0]
    db_by_disp = dict(cur.execute('SELECT disposition, COUNT(*) FROM ujo_jil_stanza GROUP BY disposition'))
    if sum(db_by_disp.values()) != db_total:
        R.hard_fail('g_disposition_sum_mismatch', f'{sum(db_by_disp.values())} vs {db_total}')
    for d in db_by_disp:
        if d not in DISPS:
            R.hard_fail('g_unknown_disposition_value', f'{d!r} x{db_by_disp[d]}')
    if st_rows_total + orphan_rows != db_total:
        R.hard_fail('g_stanza_rows_not_attributable_to_files', f'{db_total} rows vs {st_rows_total}+{orphan_rows}')
    if exact_expected_total != sum(len(r) for r in ()) and False:
        pass
    n_job = cur.execute('SELECT COUNT(*) FROM ujo_job').fetchone()[0]

    # mismatch groups -> hard failures
    for (kind, cls, disp), (cnt, ex) in sorted(kind_mismatch.items(), key=lambda kv: -kv[1][0]):
        R.hard_fail(f'e_disposition_mismatch[{kind} expected={cls} actual={disp}]', f'x{cnt}: ' + '; '.join(ex))
        R.hard[f'e_disposition_mismatch[{kind} expected={cls} actual={disp}]'][0] = cnt

    # ---- report --------------------------------------------------------------------------------------
    hard = R.hard
    print('=' * 78)
    print(f'JIL conservation verification   corpus={corpus}   db={a.db}')
    print('=' * 78)
    print(f'manifest files            : {n_manifest_files}   (exact files: {exact_files_seen})')
    print(f'db file rows / stanza rows: {len(frow)} / {db_total}   (unmatched db paths: {unmatched_db_paths})')
    print(f'manifest stanzas (all)    : {manifest_stanzas_total}   (exact-file stanzas: {exact_expected_total})')
    print(f'ujo_job rows              : {n_job}   expected distinct valid insert_job: {summary["counts"]["distinct_valid_insert_jobs"]}')
    print(f'dispositions in db        : {db_by_disp}')
    print(f'jobs existence checked    : {stats["job_exist_checked"]}   attr-checked: {stats["job_key_checked"]} '
          f'(sample={a.sample or "all"})   dup-winner checked: {stats["dup_winner_checked"]}   typed rows: {stats["typed_row_checked"]}')
    print(f'lenient-accepted stanzas  : {n_lenient_ok}   input lines lost: {lost_line_count}')
    print('\nexpected class -> actual disposition (exact files):')
    for cls in sorted(exp_vs_act):
        print(f'  {cls:13s} {dict(exp_vs_act[cls])}')
    groups = [
        ('a', '(a) every file has a ujo_jil_file row / sha / size / status'),
        ('b', '(b) stanza count, seq contiguity, alignment (directive/name)'),
        ('c', '(c) raw_text verbatim, ordered, no input line lost'),
        ('d', '(d) each valid insert_job in ujo_job exactly once; first occurrence wins'),
        ('e', '(e) disposition matches expected_class'),
        ('f', '(f) key attributes of clean jobs'),
        ('g', '(g) row-count reconciliation'),
    ]
    print()
    failed = False
    for letter, title in groups:
        items = {k: v for k, v in hard.items() if k.startswith(letter + '_') or k.startswith(letter + '[')}
        if letter == 'a':
            items.update({k: v for k, v in hard.items() if k.startswith('corpus_') or k.startswith('typed_')})
        if items:
            failed = True
            print(f'FAIL {title}')
            for k, (cnt, ex) in sorted(items.items()):
                print(f'     - {k}: {cnt}')
                for e in ex[:a.examples]:
                    print(f'         e.g. {e}')
        else:
            print(f'PASS {title}')
    known = {k for k in hard if k[:2] in tuple(l + '_' for l, _ in groups) or k[:2] in tuple(l + '[' for l, _ in groups)
             or k.startswith('corpus_') or k.startswith('typed_') or k.startswith('e_')}
    other = {k: v for k, v in hard.items() if k not in known and not any(k.startswith(l + '_') for l, _ in groups)}
    for k, (cnt, ex) in other.items():
        failed = True
        print(f'FAIL {k}: {cnt}')
        for e in ex:
            print(f'         e.g. {e}')
    if R.soft:
        print('\nSOFT findings (informational; --strict-soft makes them fail):')
        for k, (cnt, ex) in sorted(R.soft.items()):
            print(f'  ~ {k}: {cnt}')
            for e in ex[:a.examples]:
                print(f'       e.g. {e}')
        if a.strict_soft:
            failed = True
    print(f'\nelapsed {time.time() - t0:.1f}s')
    print('RESULT:', 'FAIL' if failed else 'PASS')
    if a.json:
        json.dump({'failed': failed, 'hard': {k: {'count': v[0], 'examples': v[1]} for k, v in hard.items()},
                   'soft': {k: {'count': v[0], 'examples': v[1]} for k, v in R.soft.items()},
                   'db_by_disposition': db_by_disp}, open(a.json, 'w'), indent=1)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
