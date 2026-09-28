#!/usr/bin/env python3
"""Deterministic JIL stress-corpus generator.

    python scripts/jil_stress/generate.py --out DIR --jobs 600000 --seed 1

See README.md for the corpus mix and the manifest schema.  The output is a
pure function of (--jobs, --seed): files, bytes and manifest are identical on
every run (worker count does not matter).  Nothing under autosys/ is imported.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import random
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jilcommon import DIRECTIVE_RE, norm_newlines  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

DOMS = ['trade', 'risk', 'mktdata', 'refdata', 'settle', 'pay', 'pnl', 'gl', 'reg', 'nav',
        'aml', 'dw', 'kyc', 'credit', 'liq', 'cpty', 'coll', 'margin', 'corpact', 'divi',
        'seclend', 'repo', 'cash', 'nostro', 'swift', 'conf', 'clientrpt', 'perf', 'compl',
        'sanct', 'fraud', 'audit', 'dq', 'bkp', 'infra', 'purge', 'esg', 'tax', 'fx', 'rates']
NOUNS = ['load', 'extract', 'validate', 'enrich', 'reconcile', 'publish', 'archive', 'purge',
         'transform', 'aggregate', 'report', 'feed', 'sync', 'calc', 'check', 'export',
         'import', 'snapshot', 'rollup', 'notify', 'stage', 'merge', 'cleanse', 'match']
JOB_TYPES = ['BOX', 'CMD', 'FTP', 'FILEWATCH', 'CONNECT', 'SAP', 'PEOPLESOFT', 'INFORMATICA',
             'MICROFOCUS', 'WEBSERVICE', 'REMOTECMD', 'WOL', 'USERDEFINED', 'DBMON', 'DBPROC',
             'DBTRIG', 'ENTYBEAN', 'FT', 'HTTP', 'HDFS', 'HIVE', 'I5', 'JAVARMI', 'JMSPUB',
             'JMSSUB', 'JMXMAG', 'JMXMAS', 'JMXMC', 'JMXMOP', 'JMXMREM', 'JMXSUB', 'OACOPY',
             'OASET', 'OASG', 'OMCPU', 'OMD', 'OMEL', 'OMIP', 'OMP', 'OMS', 'OMTF', 'OOZIE',
             'PAPROC', 'PAREQ', 'PIG', 'POJO', 'PROXY', 'SAPBDC', 'SAPBWIP', 'SAPBWPC', 'SAPDA',
             'SAPEVT', 'SAPJC', 'SAPPM', 'SCP', 'SESSBEAN', 'SNMPGET', 'SNMPSET', 'SQOOP', 'SQL',
             'WSDOC', 'WBSVC', 'SQLAGENT', 'ZOS', 'ZOSM', 'ZOSDST']
OTHER_TYPES = [t for t in JOB_TYPES if t not in ('BOX', 'CMD')]
TYPE_ATTRS = {
    'FTP': [('ftp_server_name', 'ftp{n}.bank.com'), ('ftp_local_name', '/data/out/f{n}.dat'),
            ('ftp_remote_name', '/in/f{n}.dat'), ('ftp_transfer_direction', 'UPLOAD'),
            ('ftp_use_SSL', 'y')],
    'WEBSERVICE': [('WSDL_URL', 'http\\://svc{n}.bank.com/ws?wsdl'),
                   ('endpoint_URL', 'http\\://svc{n}.bank.com\\:8080/ep')],
    'WSDOC': [('endpoint_url', 'http\\://svc{n}.bank.com\\:8080/Sample/services/S')],
    'HTTP': [('URL', 'http\\://www{n}.bank.com/status'), ('http_method', 'GET')],
    'SQL': [('sql_command', 'select count(*) from t{n}')],
    'FT': [('watch_file', '/data/in/f{n}.trg')],
    'FILEWATCH': [('watch_file', '/data/in/f{n}.csv'), ('watch_interval', '60')],
    'SCP': [('scp_local_name', '/data/f{n}'), ('scp_remote_name', '/r/f{n}')],
    'CONNECT': [('connect_host', 'db{n}.bank.com'), ('connect_port', '1521')],
}
JT_ALIAS_CMD = ['CMD', 'cmd', 'c', 'C', 'Cmd']
JT_ALIAS_BOX = ['BOX', 'box', 'b', 'B']
JT_ALIAS_FW = ['FILEWATCH', 'filewatch', 'f', 'FW', 'fw']

NONASCII = {
    'utf-8': ['Zürich ✓ 東京 desk', 'naïve café — “smart” quotes', 'Ünïcödé Straße €100'],
    'utf-8-sig': ['Zürich ✓ desk', 'café — “quotes”'],
    'cp1252': ['“Quarterly” – totals in € (net)', 'It’s the bank’s €50 fee…', 'Société ‘Générale’ • ok'],
    'latin-1': ['Société Générale café', 'Zürich Bänke ñandú', 'résumé à la carte'],
    'utf-16-le-bom': ['Zürich ✓ 東京', 'café — “quotes” €'],
    'utf-16-be-bom': ['Zürich ✓ 東京', 'café — “quotes” €'],
}

# (variant, weight per 10000 jobs)
VARIANTS = [
    ('plain', 3900), ('packed', 700), ('quoted', 700), ('multiline_desc', 250), ('blob', 200),
    ('glob_quoted', 200), ('comment_quote', 200), ('indented', 400), ('cond_rich', 800),
    ('sched', 600), ('alarms', 400), ('nospace', 100), ('onice', 100), ('escapes', 100),
    ('unquoted_colon', 150), ('long_line', 2), ('huge_cmd', 2), ('box', 800),
    ('filewatch', 200), ('other_type', 300), ('comment_mid', 300),
    # warn family
    ('w_nocmd', 100), ('w_nomach', 100), ('w_boxcmd', 50), ('w_badtype', 50),
    ('w_userdef', 50), ('w_badint', 70), ('w_badattr', 50), ('w_status', 30),
    ('w_nojobtype', 50), ('w_thousand', 2), ('w_ctrl', 30), ('w_junk_header', 40),
]
_TOT = sum(w for _, w in VARIANTS)
DUPABLE = {'plain', 'packed', 'quoted', 'indented', 'alarms', 'nospace', 'sched', 'cond_rich'}
WARN_VARIANTS = {v for v, _ in VARIANTS if v.startswith('w_')}

TOPS = ['prod', 'uat', 'dr', 'eod', 'intraday', 'batch', 'legacy', 'emea', 'apac', 'amer']
SUBS = ['trading', 'risk', 'ops', 'finance', 'compliance', 'infra', 'data', 'reporting',
        'treasury', 'payments', 'regional', 'core']


def name_for(jid):
    d = DOMS[jid % len(DOMS)]
    n = NOUNS[(jid // 7) % len(NOUNS)]
    st = jid % 5
    if st == 0:
        return f"{d}_{n}_{jid:07d}"
    if st == 1:
        return f"{d.upper()}_{n.upper()}_{jid:07d}"
    if st == 2:
        return f"{d}.{n}.{jid:07d}"
    if st == 3:
        return f"{d}-{n}-{jid:07d}"
    return f"{d}{n.capitalize()}{jid:07d}"


def jid_base(seed, jid):
    """Everything about a core job that must be a pure function of its id."""
    r = random.Random(seed * 1000003 + jid)
    x = r.random() * _TOT
    v = VARIANTS[-1][0]
    for name, w in VARIANTS:
        x -= w
        if x < 0:
            v = name
            break
    d = DOMS[jid % len(DOMS)]
    n = NOUNS[(jid // 7) % len(NOUNS)]
    machine = f"{d}-app-{r.randrange(1, 60):02d}"
    owner = f"svc_{d}"
    cmd = f"/opt/{d}/bin/{n}.sh --date %%DATE%% --mode {r.choice(['full', 'delta', 'eod', 'adhoc'])}"
    if v == 'quoted':
        cmd += " --note 'status: ok, retry: 3'"
    elif v == 'comment_quote':
        cmd += " --tag '# not a comment' --pat '/tmp/*/x' --x '/* nor this */'"
    elif v == 'glob_quoted':
        cmd = f"ls /data/{d}/*/in/*.csv /tmp/*/x"
    elif v == 'unquoted_colon':
        cmd += f" --url http://{d}.bank.com:8080/api?x=1"
    elif v == 'huge_cmd':
        cmd = cmd + ' ' + ' '.join(f"--opt{i}=value_{i:05d}" for i in range(3200))[:65536 - len(cmd) - 1]
    return dict(variant=v, dom=d, noun=n, machine=machine, owner=owner, cmd=cmd, r=r)


def expected_val(s):
    """Manifest value: store long strings as a digest."""
    if len(s) > 2000:
        return {'sha256': hashlib.sha256(s.encode()).hexdigest(), 'len': len(s)}
    return s


# ---------------------------------------------------------------------------
# stanza rendering
# ---------------------------------------------------------------------------

def _needs_quote_packed(v):
    return any(c in v for c in ' \t:#\n') or v == ''


def render_stanza(directive, name, jt, attrs, rng, style='lines', indent='', nospace=False,
                  mid_comment=False):
    """attrs: list of (key, value, quote).  jt: job_type token or None.
    Returns text (no trailing newline)."""
    sep = ':' if nospace else ': '

    def one(k, v, q, force=False):
        if q or (force and _needs_quote_packed(v)):
            return f'{k}{sep}"{v}"'
        return f'{k}{sep}{v}'

    if style == 'packed':
        stmts = ([f'job_type{sep}{jt}'] if jt is not None else []) + [one(k, v, q, True) for k, v, q in attrs]
        lines = []
        first = True
        i = 0
        while i < len(stmts) or first:
            take = rng.randint(2, 4)
            chunk = stmts[i:i + take]
            i += take
            line = ' '.join(chunk)
            if first:
                line = f'{directive}: {name}   ' + line
                first = False
            lines.append(indent + line.rstrip())
            if i >= len(stmts):
                break
        return '\n'.join(lines)
    lines = []
    if style == 'header_split':
        lines.append(f'{indent}{directive}: {name}')
        if jt is not None:
            lines.append(f'{indent}job_type{sep}{jt}')
    else:
        lines.append(f'{indent}{directive}: {name}' + (f'   job_type{sep}{jt}' if jt is not None else ''))
    for k, v, q in attrs:
        lines.append(indent + one(k, v, q))
    if mid_comment and len(lines) > 3:
        pos = rng.randint(2, len(lines) - 1)
        lines.insert(pos, rng.choice(['# tuned by ops 2019', '/* single-line block comment */',
                                      '# insert_job: commented_out_job',
                                      '/* multi-line\n   comment insert_job: x  */']))
    return '\n'.join(lines)


def desc_text(rng, ctx):
    base = rng.choice(['Nightly load for %s', 'Reconciles %s positions', 'Publishes %s snapshot',
                       'End of day %s processing', 'Validates %s inputs'])
    d = base % rng.choice(DOMS)
    pool = ctx.get('nonascii')
    if pool and rng.random() < 0.3:
        d += ' - ' + rng.choice(pool)
    return d


def make_condition(rng, ctx, jid):
    refs = ctx['recent'][-6:] or [name_for(max(1, jid - 1))]
    a = rng.choice(refs)
    b = rng.choice(refs)
    c = rng.choice(refs)
    t = rng.randrange(14)
    return [
        f's({a})', f'success({a}) & failure({b})', f'(s({a}) | s({b})) & n({c})',
        f's({a},12.00)', f'd({a}) AND t({b})', f'done({a}) and (success({b}) OR failure({c}))',
        f'value(GV_{DOMS[jid % len(DOMS)].upper()})=5 & s({a})', f'exitcode({a})=0',
        f's({a}) & exitcode({b}) > 2', f'n({a},00.30) | f({b},04.00)', f'notrunning({a})',
        f'terminated({a}) OR failure({b})', f's({a}) & s({b}) & s({c})',
        f'(s({a}) & (s({b}) | f({c},24.00))) | d({a})',
    ][t]


# ---------------------------------------------------------------------------
# core job stanza
# ---------------------------------------------------------------------------

def build_core(seed, jid, ctx, rng):
    b = jid_base(seed, jid)
    v = b['variant']
    name = name_for(jid)
    ent = dict(directive='insert_job', object_name=name, kind='core:' + v,
               expected_class='warn' if v in WARN_VARIANTS else 'clean', job=True)
    key, soft = {}, {}
    attrs = []
    style = 'lines'
    indent = ''
    nospace = False
    mid_comment = False
    strict = True
    jt = rng.choice(JT_ALIAS_CMD)
    has_cmd = True
    n = jid

    def add(k, val, q=False):
        attrs.append((k, val, q))

    if v == 'box':
        jt = rng.choice(JT_ALIAS_BOX)
        has_cmd = False
        soft['job_type'] = 'BOX'
    else:
        if jt.lower() in ('cmd', 'c'):
            soft['job_type'] = 'CMD'

    if ctx.get('box'):
        add('box_name', ctx['box'])
        key['box_name'] = ctx['box']
    # ---- command / machine / owner
    if v == 'other_type':
        t = OTHER_TYPES[jid % len(OTHER_TYPES)]
        jt = t if rng.random() < .7 else t.lower()
        soft.pop('job_type', None)
        add('machine', b['machine'])
        key['machine'] = b['machine']
        for k, val in TYPE_ATTRS.get(t, [('description', 'typed ' + t)]):
            add(k, val.format(n=n))
        ent['accept'] = ['LOADED', 'LOADED_WITH_WARNINGS']
        ent['strict'] = False
        has_cmd = False
        ent['variant_type'] = t
    elif v == 'filewatch':
        jt = rng.choice(JT_ALIAS_FW)
        soft.pop('job_type', None)
        if jt.lower() in ('f', 'filewatch'):
            soft['job_type'] = 'FILEWATCH'
        add('machine', b['machine'])
        key['machine'] = b['machine']
        add('watch_file', f"/data/{b['dom']}/in/f{n}.csv")
        add('watch_interval', '60')
        add('watch_file_min_size', '1024')
        has_cmd = False
    elif v == 'box':
        pass
    else:
        if v == 'w_nocmd':
            add('machine', b['machine'])
            key = {}
        elif v == 'w_nomach':
            add('command', b['cmd'])
        elif v == 'w_boxcmd':
            jt = rng.choice(JT_ALIAS_BOX)
            soft.pop('job_type', None)
            add('command', b['cmd'])
            add('machine', b['machine'])
        else:
            q = v in ('quoted', 'comment_quote', 'glob_quoted', 'huge_cmd')
            if v == 'packed':
                q = True
            add('command', b['cmd'], q)
            add('machine', b['machine'])
            if v == 'unquoted_colon':
                soft['command'] = b['cmd']
            elif v not in WARN_VARIANTS:
                key['command'] = expected_val(b['cmd'])
            if v not in WARN_VARIANTS:
                key['machine'] = b['machine']
    if v in ('w_nojobtype',):
        jt = None
        soft.pop('job_type', None)
    if v == 'w_badtype':
        jt = rng.choice(['cmdd', 'CMDS', 'boxx', 'fielwatch', 'CM D', 'c md'])
        soft.pop('job_type', None)
    if v == 'w_userdef':
        jt = rng.choice(['7', 'MY_TYPE', '42', 'CUSTOM_ADAPTER'])
        soft.pop('job_type', None)
    if v in WARN_VARIANTS:
        ent['strict'] = v in ('w_nocmd', 'w_nomach', 'w_boxcmd', 'w_badtype', 'w_badint',
                              'w_nojobtype', 'w_status')
        if not ent['strict']:
            ent['accept'] = ['LOADED_WITH_WARNINGS', 'LOADED']
    # owner
    if v != 'w_nocmd' or True:
        add('owner', b['owner'])
        if v not in WARN_VARIANTS:
            key['owner'] = b['owner']
    # description
    dsc = desc_text(rng, ctx)
    if v == 'multiline_desc':
        extra = ['second line: with a colon', '  # not a comment line']
        if rng.random() < .4:
            extra.append('  insert_job: not_a_job_inside_quotes')
            extra.append('  update_job: neither')
        dsc = dsc + '\n' + '\n'.join(extra)
        add('description', dsc, True)
    elif v == 'escapes':
        add('description', dsc + ' with \\: colon and \\, comma and \\" quote', True)
    elif v == 'comment_quote':
        add('description', dsc + ' # hash /* not a comment */ and /tmp/*/x path', True)
    elif v == 'glob_quoted':
        add('description', 'globs /data/*/*.dat and /tmp/*/x kept', True)
    elif v == 'long_line':
        add('description', dsc + ' ' + ('lorem ipsum dolor sit amet ' * 40000)[:rng.randint(400000, 1000000)], True)
    elif v == 'w_ctrl':
        add('description', dsc + ' ctl\x01\x02\x07\x1b[0m nul\x00byte tab\there \x0b vt\x0c ff', True)
    elif rng.random() < .8:
        add('description', dsc, True)
    # blob
    if v == 'blob':
        inner = ('line one of blob\ninsert_job: evil\n job_type: CMD\n command: rm -rf /\n'
                 'machine: nowhere\n# hash line\n/* open comment in blob\n"unbalanced quote\n')
        if rng.random() < .5:
            add('blob_input', '<auto_blobt>' + inner + '</auto_blobt>')
            attrs[-1] = ('blob_input', '<auto_blobt>' + inner + '</auto_blobt>', False)
        else:
            attrs.append(('blob_input', '<auto_blobt>' + inner + '</auto_blobt>compatibility: 12', False))
        add('std_in_file', '$$blobt')
    # schedule / condition / misc
    if v in ('sched', 'box') or (ctx.get('box') is None and rng.random() < .25):
        st = f"{rng.randint(0, 23):02d}:{rng.choice(['00', '15', '30', '45'])}"
        if rng.random() < .3:
            st += f",{rng.randint(0, 23):02d}:{rng.choice(['00', '30'])}"
        add('start_times', st, True)
        key['start_times'] = st
        add('days_of_week', rng.choice(['mo,tu,we,th,fr', 'all', 'sa,su', 'mo,we,fr']))
        add('date_conditions', '1')
        if rng.random() < .5:
            add(rng.choice(['run_calendar', 'exclude_calendar']), rng.choice(['us_market_holidays', 'month_end', 'quarter_end']))
    elif v == 'cond_rich' or rng.random() < .12:
        cond = make_condition(rng, ctx, jid)
        add('condition', cond)
        key['condition'] = cond
    if v == 'alarms' or rng.random() < .15:
        add('alarm_if_fail', '1')
        add('n_retrys', str(rng.randint(0, 5)))
        add('max_run_alarm', str(rng.randint(5, 240)))
        add('term_run_time', str(rng.randint(10, 480)))
    if v == 'w_badint':
        add('n_retrys', rng.choice(['abc', '3x', 'many', '-', '1.5.2']))
        add('max_run_alarm', 'soon')
    if v == 'w_badattr':
        add('cpu_usage', '80')
        add('n_retrys', 'abc')
    if v == 'w_status':
        add('status', rng.choice(['bogus', 'RUNNING_FOREVER', 'not_a_status']))
    if v == 'onice':
        add('status', 'on_ice')
    if v == 'w_thousand':
        for i in range(rng.randint(1500, 4000)):
            add(f'x_custom_attr_{i:05d}', f'v{i}')
        ent['accept'] = ['LOADED_WITH_WARNINGS', 'LOADED']
    if rng.random() < .3 and v not in WARN_VARIANTS:
        add('std_out_file', f"/logs/{b['dom']}/{n}.out")
        add('std_err_file', f"/logs/{b['dom']}/{n}.err")
    if rng.random() < .1:
        add('permission', 'gx,wx,me,mx,ge,we')
    if v == 'w_junk_header':
        pass
    # style
    if v == 'packed':
        style = 'packed'
    elif v == 'indented':
        indent = rng.choice(['  ', '    ', '\t', '     '])
    elif v == 'nospace':
        nospace = True
        # key:value form for single token values only -> quote nothing with spaces
        attrs[:] = [(k, val, q or (' ' in val or '\t' in val)) for k, val, q in attrs]
    elif v == 'comment_mid':
        mid_comment = True
    if v not in ('packed',) and rng.random() < .07:
        style = 'header_split'
    if v == 'multiline_desc' and style == 'packed':
        style = 'lines'
    text = render_stanza('insert_job', name, jt, attrs, rng, style, indent, nospace, mid_comment)
    if v == 'w_junk_header':
        text = text.replace(f'insert_job: {name}', f'insert_job: {name} junk words here ; !!', 1)
        ent['accept'] = ['LOADED_WITH_WARNINGS', 'LOADED']
        ent['strict'] = False
    if ent['expected_class'] == 'clean':
        if key:
            ent['key'] = key
        if soft:
            ent['key_soft'] = soft
    if v == 'w_thousand' or v == 'other_type' or v == 'w_ctrl' or v == 'w_junk_header':
        ent.pop('key', None)
    ent['strict'] = ent.get('strict', True)
    return text, ent, b


# ---------------------------------------------------------------------------
# supporting (non-job) stanzas
# ---------------------------------------------------------------------------

def infra_stanza(rng, tag, k, allow_nonascii):
    """Returns (text, entry).  tag makes names unique per file."""
    kind = rng.choice(['machine', 'machine', 'vmachine', 'glob', 'glob', 'calendar', 'resource',
                       'monbro', 'xinst', 'blob', 'connprofile', 'job_type', 'view', 'filter',
                       'alert_policy', 'delete_user', 'delete_machine', 'delete_glob',
                       'update_machine', 'delete_blob'])
    nm = f"{tag}_{k}"
    ent = dict(kind='infra:' + kind, expected_class='clean')
    if kind == 'machine':
        t = (f"insert_machine: mach_{nm}\ntype: a\nhost: mach{k}.{tag}.bank.com\nport: {rng.choice([7520, 7521, 9000])}\n"
             f"max_load: {rng.randint(5, 100)}\ndescription: \"Agent host {nm}\"")
        ent.update(directive='insert_machine', object_name=f'mach_{nm}', tab=['ujo_machine', 'machine_name'])
    elif kind == 'vmachine':
        t = f"insert_machine: vmach_{nm}\ntype: v\nmachine: mem_{nm}_a\nmachine: mem_{nm}_b  max_load: 10\nmachine: mem_{nm}_c\nfactor: 1.5"
        ent.update(directive='insert_machine', object_name=f'vmach_{nm}', tab=['ujo_machine', 'machine_name'],
                   accept=['LOADED', 'LOADED_WITH_WARNINGS'], strict=False)
    elif kind == 'glob':
        g = f"GV_{nm}".upper()
        t = f"insert_glob: {g}\nglobal_value: {rng.choice(['Q3', '20260705', 'PROD', '2026-07', 'on'])}"
        ent.update(directive='insert_glob', object_name=g, tab=['ujo_glob', 'glob_name'])
    elif kind == 'calendar':
        t = (f"insert_calendar: cal_{nm}\ndescription: \"Calendar {nm}\"\n"
             f"dates: \"2026-01-01,2026-04-03,2026-{rng.randint(5, 12):02d}-{rng.randint(10, 28)}\"")
        ent.update(directive='insert_calendar', object_name=f'cal_{nm}', tab=['ujo_calendar', 'calendar_name'])
    elif kind == 'resource':
        t = f"insert_resource: res_{nm}\nres_type: {rng.choice('RDT')}\nmachine: mach_{nm}\namount: {rng.randint(1, 50)}"
        ent.update(directive='insert_resource', object_name=f'res_{nm}')
    elif kind == 'monbro':
        t = f"insert_monbro: mb_{nm}\nmode: browser\nrestart: y\nall_status:n\nall_events:n\nafter_time: \"09/10/2012 00:00:00\""
        ent.update(directive='insert_monbro', object_name=f'mb_{nm}')
    elif kind == 'xinst':
        t = (f"  insert_xinst: xi_{nm}\n  xtype: e\n  xmanager: WAEEMGR\n  xmachine: WAEEHOST\n  xport: 12345\n"
             f"  xcrypt_type: NONE\n  xcomm_alias: ACE_SCH_ALIAS")
        ent.update(directive='insert_xinst', object_name=f'xi_{nm}')
    elif kind == 'blob':
        t = f"insert_blob: blob_{nm}\nblob_type: in\nblob_file: /tmp/blob_{nm}.txt"
        ent.update(directive='insert_blob', object_name=f'blob_{nm}')
    elif kind == 'connprofile':
        t = (f"insert_connectionprofile: cp_{nm}\nconnectionprofile_type: hadoop\ndescription: \"cp {nm}\"\n"
             f"hadoop_edgenode: edge{k}\nhadoop_edgenode_port: 22\nhadoop_binpath: /usr/bin")
        ent.update(directive='insert_connectionprofile', object_name=f'cp_{nm}')
    elif kind == 'job_type':
        t = f"insert_job_type: jt_{nm}\ncommand: /home/scripts/myftp{k}\ndescription: \"user type {nm}\""
        ent.update(directive='insert_job_type', object_name=f'jt_{nm}')
    elif kind in ('view', 'filter', 'alert_policy'):
        d = {'view': 'insert_view', 'filter': 'insert_filter', 'alert_policy': 'insert_alert_policy'}[kind]
        body = {'view': 'attributes: job_name,status', 'filter': 'view: MyView1\nfilter: job_name like Backup*\nactive: true',
                'alert_policy': 'attributes: FailedJobs, TerminatedJobs'}[kind]
        t = f"{d}: {kind}_{nm}\n{body}"
        ent.update(directive=d, object_name=f'{kind}_{nm}', expected_class='archive_only')
    elif kind == 'delete_user':
        t = f"delete_user: user_{nm}"
        ent.update(directive='delete_user', object_name=f'user_{nm}', expected_class='archive_only')
    else:  # deletes / updates of never-inserted objects: semantics undefined -> lenient
        d = {'delete_machine': 'delete_machine', 'delete_glob': 'delete_glob', 'update_machine': 'update_machine',
             'delete_blob': 'delete_blob'}[kind]
        t = f"{d}: ghost_{nm}" + ("\nagent_name: WA_MACH3\nnode_name: ghost" if kind == 'update_machine' else "")
        ent.update(directive=d, object_name=f'ghost_{nm}', expected_class='warn', strict=False,
                   accept=['LOADED', 'LOADED_WITH_WARNINGS', 'ARCHIVE_ONLY', 'QUARANTINED'])
    ent.setdefault('strict', True)
    return t, ent


# ---------------------------------------------------------------------------
# file assembly
# ---------------------------------------------------------------------------

def gap_chunk(rng):
    r = rng.random()
    if r < .35:
        return ['']
    if r < .45:
        return []
    if r < .52:
        return ['', '']
    if r < .68:
        return [rng.choice(['# ' + 'step ' + str(rng.randint(1, 99)), '#', '#--------------', '   # indented comment'])]
    if r < .84:
        return [rng.choice(['/* Step 1A: extract feed */',
                            '/* ==========================\n   FILE: block\n   DOMAIN: ops\n   ========================== */'])]
    if r < .90:
        return ['# insert_job: retired_job_do_not_load', '# command: echo old']
    if r < .94:
        return ['/* retired:\ninsert_job: retired_two   job_type: CMD\ncommand: echo gone\nmachine: nowhere\n*/']
    if r < .97:
        return ['', '/* trailing */ ', '']
    return ['\t', '   ', '']


class Assembler:
    def __init__(self):
        self.chunks = []
        self.line = 1
        self.entries = []

    def raw(self, text):
        self.chunks.append(text)
        self.line += text.count('\n') + 1

    def stanza(self, text, ent):
        start = self.line
        self.chunks.append(text)
        self.line += text.count('\n') + 1
        ent = dict(ent)
        ent['seq'] = len(self.entries) + 1
        ent['start_line'] = start
        ent['end_line'] = self.line - 1
        ent.setdefault('job', False)
        self.entries.append(ent)


def encode_text(text, enc):
    if enc == 'utf-8':
        return text.encode('utf-8')
    if enc == 'utf-8-sig':
        return b'\xef\xbb\xbf' + text.encode('utf-8')
    if enc == 'cp1252':
        return text.encode('cp1252')
    if enc == 'latin-1':
        return text.encode('latin-1')
    if enc == 'utf-16-le-bom':
        return b'\xff\xfe' + text.encode('utf-16-le')
    if enc == 'utf-16-be-bom':
        return b'\xfe\xff' + text.encode('utf-16-be')
    raise ValueError(enc)


EOL = {'lf': '\n', 'crlf': '\r\n', 'cr': '\r'}


def finish(asm, enc, eol, trail):
    text = '\n'.join(asm.chunks)
    if trail:
        text += '\n'
    return encode_text(text.replace('\n', EOL[eol]), enc)


# ---------------------------------------------------------------------------
# recipes.  Each returns dict(asm=Assembler | None, data=bytes | None, ...)
# ---------------------------------------------------------------------------

class FileCtx:
    def __init__(self, seed, plan, rng):
        self.seed = seed
        self.plan = plan
        self.rng = rng
        self.xk = 0

    def xname(self, prefix='hx'):
        self.xk += 1
        return f"{prefix}{self.plan['idx']}_{self.xk}"


def layout_jobs(fc, asm, jids, header=True, infra_prob=0.0):
    rng = fc.rng
    ctx = {'recent': [], 'box': None, 'nonascii': NONASCII.get(fc.plan['enc'])}
    box = None
    remaining = 0
    if header and rng.random() < .3:
        asm.raw('/* ============================================================\n'
                f"   FILE: {fc.plan['path'].rsplit('/', 1)[-1]}\n   DOMAIN: {rng.choice(DOMS)}\n"
                '   ============================================================ */')
        asm.raw('')
    firsts = {}
    for jid in jids:
        v = jid_base(fc.seed, jid)['variant']
        if v == 'box':
            ctx['box'] = box if remaining > 0 else None
            nb = name_for(jid)
            text, ent, _b = build_core(fc.seed, jid, ctx, rng)
            box, remaining = nb, rng.randint(3, 20)
        else:
            ctx['box'] = box if remaining > 0 else None
            if remaining > 0:
                remaining -= 1
            text, ent, _b = build_core(fc.seed, jid, ctx, rng)
        for g in gap_chunk(rng):
            asm.raw(g)
        asm.stanza(text, ent)
        firsts[jid] = (text, ent)
        ctx['recent'].append(name_for(jid))
        if len(ctx['recent']) > 12:
            del ctx['recent'][:6]
        if infra_prob and rng.random() < infra_prob:
            fc.xk += 1
            t, e = infra_stanza(rng, f"f{fc.plan['idx']}", fc.xk, False)
            asm.raw('')
            asm.stanza(t, e)
    return firsts


def R_normal(fc):
    p = fc.plan
    asm = Assembler()
    layout_jobs(fc, asm, range(p['base'], p['base'] + p['ncore']), infra_prob=0.02)
    return dict(asm=asm)


def R_infra(fc):
    rng = fc.rng
    asm = Assembler()
    for k in range(rng.randint(5, 40)):
        t, e = infra_stanza(rng, f"f{fc.plan['idx']}", k, False)
        for g in gap_chunk(rng):
            asm.raw(g)
        asm.stanza(t, e)
    return dict(asm=asm)


def R_empty(fc):
    return dict(data=b'', asm=None, exact=True, kind='empty', enc='utf-8')


def R_whitespace(fc):
    s = ''.join(fc.rng.choice([' ', '\t', '\n', '\n', '  \n']) for _ in range(fc.rng.randint(1, 200)))
    return dict(data=s.replace('\n', EOL[fc.plan['eol']]).encode(), asm=None, exact=True, kind='whitespace_only', enc='utf-8')


def R_comment_only(fc):
    s = ('# only a comment\n/* block\n   comment insert_job: not_real\n*/\n\n# another\n'
         '/* x */ # y\n')
    return dict(data=s.replace('\n', EOL[fc.plan['eol']]).encode(), asm=None, exact=False, kind='comment_only', enc='utf-8')


def R_binary(fc):
    rng = fc.rng
    n = rng.randint(200, 40000)
    b = bytes(rng.getrandbits(8) for _ in range(min(n, 4000)))
    b = (b * (n // len(b) + 1))[:n]
    if rng.random() < .3:
        b = b'\x7fELF\x02\x01\x01\x00' + b
    elif rng.random() < .3:
        b = b'PK\x03\x04' + b
    return dict(data=b, asm=None, exact=False, kind='non_jil:binary', enc='binary', cmin=0, cmax=10 ** 9,
                accept=['QUARANTINED', 'LOADED_WITH_WARNINGS'])


PROSE = ("The quarterly operations review covers batch scheduling across the trading estate.\n"
         "Jobs are grouped into boxes and triggered by calendars; failures raise alarms.\n\n"
         "Contact the batch team for details of the runbook.\nNothing in this file is JIL.\n")


def R_prose(fc):
    rng = fc.rng
    t = PROSE * rng.randint(1, 12)
    return dict(data=t.replace('\n', EOL[fc.plan['eol']]).encode(), asm=None, exact=False, kind='non_jil:prose',
                enc='utf-8', cmin=1, cmax=10 ** 9, accept=['QUARANTINED', 'LOADED_WITH_WARNINGS'])


def R_json(fc):
    rng = fc.rng
    obj = {'jobs': [{'insert_job': f'j{i}', 'job_type': 'CMD', 'command': 'echo hi'} for i in range(rng.randint(1, 30))]}
    t = json.dumps(obj, indent=2) + '\n'
    return dict(data=t.replace('\n', EOL[fc.plan['eol']]).encode(), asm=None, exact=False, kind='non_jil:json',
                enc='utf-8', cmin=1, cmax=10 ** 9, accept=['QUARANTINED', 'LOADED_WITH_WARNINGS'])


def R_other_text(fc):
    rng = fc.rng
    t = rng.choice([
        '#!/bin/bash\nset -e\necho "hello: world"\nexit 0\n',
        'a,b,c\n1,2,3\n4,5,6\n',
        '<?xml version="1.0"?>\n<jobs><job name="x"/></jobs>\n',
        'def main():\n    print("insert_job")\n\nmain()\n',
    ]) * rng.randint(1, 5)
    return dict(data=t.replace('\n', EOL[fc.plan['eol']]).encode(), asm=None, exact=False, kind='non_jil:text',
                enc='utf-8', cmin=0, cmax=10 ** 9, accept=['QUARANTINED', 'LOADED_WITH_WARNINGS', 'LOADED'])


def R_stray(fc):
    rng = fc.rng
    p = fc.plan
    asm = Assembler()
    stray = rng.choice([['this is not jil', 'random words: here', 'more stray text'],
                        ['command: echo orphan', 'machine: nowhere', 'owner: nobody'],
                        ['}', '{', ']]']])
    for l in stray:
        asm.raw(l)
    # the orphan run is one stanza
    text = '\n'.join(stray)
    asm.chunks = []
    asm.line = 1
    asm.stanza(text, dict(directive=None, object_name=None, kind='hostile:stray_lines', expected_class='quarantine'))
    asm.raw('')
    layout_jobs(fc, asm, range(p['base'], p['base'] + p['ncore']), header=False)
    return dict(asm=asm)


def _first_then(fc, tail_builder, kind_name):
    """Normal core jobs followed by one hostile trailing stanza (deterministic tail)."""
    p = fc.plan
    asm = Assembler()
    layout_jobs(fc, asm, range(p['base'], p['base'] + p['ncore']), header=False)
    asm.raw('')
    tail_builder(fc, asm)
    return dict(asm=asm, kind=kind_name, exact=(kind_name != 'hostile:unterminated_blob'))


def R_unknown_sub(fc):
    def tail(fc, asm):
        for k in range(fc.rng.randint(1, 3)):
            asm.stanza(f"insert_bogus: {fc.xname()}\ncommand: echo x\nmachine: m1",
                       dict(directive='insert_bogus', object_name=None, kind='hostile:unknown_subcommand',
                            expected_class='quarantine', name_check=False))
            asm.raw('')
            asm.stanza(f"update_frobnicate: {fc.xname()}\nowner: x",
                       dict(directive='update_frobnicate', object_name=None, kind='hostile:unknown_subcommand',
                            expected_class='quarantine', name_check=False))
            asm.raw('')
    return _first_then(fc, tail, 'hostile:unknown_subcommand')


def R_missing_name(fc):
    def tail(fc, asm):
        asm.stanza("insert_job:\njob_type: CMD\ncommand: echo noname\nmachine: m1",
                   dict(directive='insert_job', object_name=None, kind='hostile:missing_name', expected_class='quarantine'))
        asm.raw('')
        asm.stanza("insert_job:   job_type: CMD\ncommand: echo noname2\nmachine: m1",
                   dict(directive='insert_job', object_name=None, kind='hostile:missing_name', expected_class='quarantine',
                        name_check=False, strict=False, accept=['QUARANTINED', 'LOADED_WITH_WARNINGS']))
    return _first_then(fc, tail, 'hostile:missing_name')


def R_unterm_quote(fc):
    def tail(fc, asm):
        nm = fc.xname()
        asm.stanza(f'insert_job: {nm}   job_type: CMD\ncommand: "echo hello wall\nmachine: m1\nowner: x\ndescription: never closed',
                   dict(directive='insert_job', object_name=nm, kind='hostile:unterminated_quote', expected_class='warn',
                        strict=False, accept=['LOADED_WITH_WARNINGS', 'QUARANTINED'], job='maybe'))
    return _first_then(fc, tail, 'hostile:unterminated_quote')


def R_unterm_blob(fc):
    def tail(fc, asm):
        nm = fc.xname()
        asm.stanza(f'insert_job: {nm}   job_type: CMD\ncommand: cat\nmachine: m1\nblob_input: <auto_blobt>text that never ends\ninsert_job: evil_inner\ncommand: x',
                   dict(directive='insert_job', object_name=nm, kind='hostile:unterminated_blob', expected_class='warn',
                        strict=False, accept=['LOADED_WITH_WARNINGS', 'QUARANTINED'], job='maybe'))
    return _first_then(fc, tail, 'hostile:unterminated_blob')


def R_unterm_comment_tail(fc):
    def tail(fc, asm):
        asm.raw('/* forgotten close: prose only, no stanza below')
        asm.raw('just words here')
    return _first_then(fc, tail, 'hostile:unterminated_comment_tail')


def R_unterm_comment_swallow(fc):
    """Unterminated /* followed by real stanzas: they must NOT be silently lost."""
    p = fc.plan
    asm = Assembler()
    ids = list(range(p['base'], p['base'] + p['ncore']))
    layout_jobs(fc, asm, ids[:1], header=False)
    asm.raw('')
    asm.raw('/* this comment is never closed')
    asm.raw('')
    layout_jobs(fc, asm, ids[1:], header=False)
    for e in asm.entries[1:]:      # text after '/*' up to the next '*/' is a comment
        if e.get('job') is True:
            e['job'] = 'maybe'
    return dict(asm=asm, exact=False, kind='hostile:unterminated_comment_swallow', cmin=1, cmax=10 ** 9,
                accept=['LOADED', 'LOADED_WITH_WARNINGS', 'QUARANTINED'])


def R_nul_between(fc):
    p = fc.plan
    asm = Assembler()
    ids = list(range(p['base'], p['base'] + p['ncore']))
    layout_jobs(fc, asm, ids[:1], header=False)
    asm.raw('\x00\x00\x00')
    layout_jobs(fc, asm, ids[1:], header=False)
    return dict(asm=asm, exact=False, kind='hostile:nul_between_stanzas', cmin=1, cmax=10 ** 9,
                accept=['LOADED', 'LOADED_WITH_WARNINGS', 'QUARANTINED'])


def R_mixed_encoding(fc):
    p = fc.plan
    asm = Assembler()
    layout_jobs(fc, asm, range(p['base'], p['base'] + p['ncore']), header=False)
    text = '\n'.join(asm.chunks).replace('\n', EOL[p['eol']])
    b = text.encode('utf-8')
    # inject invalid / foreign byte sequences at safe-ish places (inside descriptions)
    bad = [b'\xff\xfe', b'\x80\x81', b'\xe9', b'\xc3\x28', b'\xed\xa0\x80', b'\xf0\x28\x8c\xbc']
    out = bytearray(b)
    for _ in range(fc.rng.randint(2, 6)):
        pos = fc.rng.randrange(len(out))
        out[pos:pos] = fc.rng.choice(bad)
    for e in asm.entries:     # bytes were injected at random offsets, even inside tokens
        e['job'] = 'maybe'
        for k_ in ('key', 'soft', 'dup_first_key'):
            e.pop(k_, None)
        e['expected_class'] = 'any'
    return dict(data=bytes(out), asm=asm, exact=False, kind='hostile:mixed_invalid_encoding', enc='mixed_invalid',
                cmin=1, cmax=10 ** 9, accept=['LOADED', 'LOADED_WITH_WARNINGS', 'QUARANTINED'])


def R_dup_same(identical):
    def f(fc):
        p = fc.plan
        asm = Assembler()
        firsts = layout_jobs(fc, asm, range(p['base'], p['base'] + p['ncore']), header=False)
        jid = p['base']
        text, ent = firsts[jid]
        asm.raw('')
        if identical:
            t2 = text
            e2 = dict(ent)
        else:
            t2 = (f"insert_job: {ent['object_name']}   job_type: CMD\ncommand: /changed/dup.sh\n"
                  f"machine: other-host\nowner: dupowner\ndescription: \"second definition\"")
            e2 = dict(ent)
        k1 = ent.get('key') or {}
        if 'command' in k1 and 'machine' in k1 and 'owner' in k1:
            e2['dup_first_key'] = {x: k1[x] for x in ('command', 'machine', 'owner')}
        e2.pop('key', None)
        e2.pop('key_soft', None)
        e2.update(kind='dup:same_file_' + ('identical' if identical else 'different'), expected_class='duplicate',
                  job=False, strict=True)
        e2.pop('accept', None)
        e2['dup_of'] = ent['object_name']
        asm.stanza(t2, e2)
        return dict(asm=asm, kind='dup:same_file')
    return f


def R_dup_cross(fc):
    """Redefines earlier (sorted-path) core jobs with different content."""
    p = fc.plan
    rng = fc.rng
    asm = Assembler()
    base = p['base']
    made = 0
    tries = 0
    while made < rng.randint(1, 3) and tries < 30 and base > 1:
        tries += 1
        jid = rng.randrange(1, base)
        b = jid_base(fc.seed, jid)
        if b['variant'] not in DUPABLE or jid in TOUCHED:
            continue
        nm = name_for(jid)
        text = (f"insert_job: {nm}   job_type: CMD\ncommand: /dup/shadow/{jid}.sh\nmachine: shadow-host\n"
                f"owner: shadow_user\ndescription: \"cross-file redefinition\"")
        ent = dict(directive='insert_job', object_name=nm, kind='dup:cross_file', expected_class='duplicate',
                   job=False, strict=True, dup_of=nm,
                   dup_first_key={'command': expected_val(b['cmd']), 'machine': b['machine'], 'owner': b['owner']})
        for g in gap_chunk(rng):
            asm.raw(g)
        asm.stanza(text, ent)
        made += 1
    if made == 0:   # nothing earlier available: plain comment file
        asm.raw('# nothing to duplicate yet')
    return dict(asm=asm, kind='dup:cross_file')


def R_update_delete(fc):
    """Insert two core jobs then update/override/delete around them, plus delete victims."""
    p = fc.plan
    rng = fc.rng
    asm = Assembler()
    ids = list(range(p['base'], p['base'] + p['ncore']))
    layout_jobs(fc, asm, ids, header=False)
    a, b_ = name_for(ids[0]), name_for(ids[-1])
    for e in asm.entries:
        if e['directive'] == 'insert_job' and e['object_name'] in (a, b_):
            e.pop('key', None)
            e.pop('key_soft', None)
            if e['object_name'] == b_:
                e['job'] = 'optional'
    asm.raw('')
    asm.stanza(f"update_job: {a}\nowner: newprod@unixagent\ndescription: \"updated\"",
               dict(directive='update_job', object_name=a, kind='mut:update_existing', expected_class='clean'))
    asm.raw('')
    asm.stanza(f"override_job: {b_}\ncommand: echo 'test'\nmachine: other-host",
               dict(directive='override_job', object_name=b_, kind='mut:override_existing', expected_class='clean',
                    strict=False, accept=['LOADED', 'LOADED_WITH_WARNINGS']))
    asm.raw('')
    asm.stanza(f"override_job: {b_} delete",
               dict(directive='override_job', object_name=b_, kind='mut:override_delete', expected_class='archive_only',
                    strict=False, accept=['ARCHIVE_ONLY', 'LOADED', 'LOADED_WITH_WARNINGS']))
    asm.raw('')
    v1, v2 = fc.xname('victim'), fc.xname('victimbox')
    asm.stanza(f"insert_job: {v1}   job_type: CMD\ncommand: echo v\nmachine: m1\nowner: x",
               dict(directive='insert_job', object_name=v1, kind='mut:victim_job', expected_class='clean', job='optional'))
    asm.raw('')
    asm.stanza(f"insert_job: {v2}   job_type: BOX\nowner: x",
               dict(directive='insert_job', object_name=v2, kind='mut:victim_box', expected_class='clean', job='optional'))
    asm.raw('')
    asm.stanza(f"delete_job: {v1}", dict(directive='delete_job', object_name=v1, kind='mut:delete_job',
                                         expected_class='clean', strict=False,
                                         accept=['LOADED', 'LOADED_WITH_WARNINGS', 'ARCHIVE_ONLY']))
    asm.raw('')
    asm.stanza(f"delete_box: {v2}", dict(directive='delete_box', object_name=v2, kind='mut:delete_box',
                                         expected_class='clean', strict=False,
                                         accept=['LOADED', 'LOADED_WITH_WARNINGS', 'ARCHIVE_ONLY']))
    asm.raw('')
    asm.stanza(f"rename_job: {fc.xname('ghost')}   new_name: {fc.xname('ghostnew')}",
               dict(directive='rename_job', object_name=None, kind='mut:rename_missing', expected_class='warn',
                    strict=False, name_check=False,
                    accept=['LOADED', 'LOADED_WITH_WARNINGS', 'ARCHIVE_ONLY', 'QUARANTINED']))
    return dict(asm=asm, kind='mut:update_delete')


def R_update_missing(fc):
    asm = Assembler()
    for k in range(fc.rng.randint(1, 3)):
        n1, n2 = fc.xname('nojob'), fc.xname('nojob')
        asm.stanza(f"update_job: {n1}\nowner: nobody",
                   dict(directive='update_job', object_name=n1, kind='mut:update_missing', expected_class='warn',
                        strict=False, accept=['LOADED_WITH_WARNINGS', 'QUARANTINED', 'ARCHIVE_ONLY']))
        asm.raw('')
        asm.stanza(f"override_job: {n2}\ncommand: echo x",
                   dict(directive='override_job', object_name=n2, kind='mut:override_missing', expected_class='warn',
                        strict=False, accept=['LOADED_WITH_WARNINGS', 'QUARANTINED', 'ARCHIVE_ONLY']))
        asm.raw('')
        asm.stanza(f"override_job: {fc.xname('nojob')} delete",
                   dict(directive='override_job', object_name=None, kind='mut:override_delete_missing',
                        expected_class='archive_only', strict=False, name_check=False,
                        accept=['ARCHIVE_ONLY', 'LOADED_WITH_WARNINGS', 'QUARANTINED', 'LOADED']))
        asm.raw('')
    return dict(asm=asm, kind='mut:update_missing')


def R_pdf(fc):
    ex = fc.plan['pdf']
    parts = []
    ents = []
    for i, e in enumerate(ex):
        parts.append(f"# pdf example (source line {e['line']})")
        parts.append(e['txt'])
        parts.append('')
        m = DIRECTIVE_RE.search(norm_newlines(e['txt']))
        nm = None
        if m:
            rest = norm_newlines(e['txt'])[m.end():].strip().split()
            nm = rest[0] if rest else None
        ents.append(dict(seq=i + 1, directive=m.group(1).lower() if m else None, object_name=nm,
                         kind='pdf_example', expected_class='any', job=False, approx=True,
                         pdf_line=e['line']))
    text = '\n'.join(parts)
    data = text.replace('\n', EOL[fc.plan['eol']]).encode('utf-8')
    return dict(data=data, asm=None, entries=ents, exact=False, kind='pdf_example', enc='utf-8', cmin=0, cmax=10 ** 9,
                accept=['LOADED', 'LOADED_WITH_WARNINGS', 'ARCHIVE_ONLY', 'QUARANTINED', 'DUPLICATE'])


RECIPES = {
    'normal': R_normal, 'huge': R_normal, 'infra': R_infra, 'empty': R_empty, 'whitespace': R_whitespace,
    'comment_only': R_comment_only, 'binary': R_binary, 'prose': R_prose, 'json': R_json,
    'other_text': R_other_text, 'stray': R_stray, 'unknown_sub': R_unknown_sub, 'missing_name': R_missing_name,
    'unterm_quote': R_unterm_quote, 'unterm_blob': R_unterm_blob, 'unterm_comment_tail': R_unterm_comment_tail,
    'unterm_comment_swallow': R_unterm_comment_swallow, 'nul_between': R_nul_between,
    'mixed_encoding': R_mixed_encoding, 'dup_same_identical': R_dup_same(True),
    'dup_same_different': R_dup_same(False), 'dup_cross': R_dup_cross, 'update_delete': R_update_delete,
    'update_missing': R_update_missing, 'pdf': R_pdf,
}
# recipe -> number of core jobs it consumes (fixed); None = variable
FIXED_NCORE = {'stray': 2, 'unknown_sub': 2, 'missing_name': 2, 'unterm_quote': 1, 'unterm_blob': 1,
               'unterm_comment_tail': 2, 'unterm_comment_swallow': 3, 'nul_between': 3, 'mixed_encoding': 3,
               'dup_same_identical': 1, 'dup_same_different': 1, 'update_delete': 3}
SPECIAL_ONE_PER = ['empty', 'whitespace', 'comment_only', 'binary', 'prose', 'json', 'other_text', 'stray',
                   'unknown_sub', 'missing_name', 'unterm_quote', 'unterm_blob', 'unterm_comment_tail',
                   'unterm_comment_swallow', 'nul_between', 'mixed_encoding', 'dup_same_identical',
                   'dup_same_different', 'dup_cross', 'update_delete', 'update_missing']


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def make_plan(N, seed, pdf_examples):
    rng = random.Random(seed)
    files = []

    def add(recipe, ncore=0, **kw):
        files.append(dict(recipe=recipe, ncore=ncore, **kw))

    per = max(2, N // 25000)
    # specials
    for rname in SPECIAL_ONE_PER:
        cnt = per
        if rname in ('empty', 'whitespace', 'comment_only'):
            cnt = per
        for _ in range(cnt):
            add(rname, FIXED_NCORE.get(rname, 0))
    special_core = sum(f['ncore'] for f in files)
    # pdf examples grouped 1-6 per file
    i = 0
    while i < len(pdf_examples):
        k = rng.randint(1, 6)
        add('pdf', 0, pdf=pdf_examples[i:i + k])
        i += k
    remaining = N - special_core
    if remaining < 0:
        raise SystemExit('--jobs too small for the fixed special recipes (need >= %d)' % special_core)
    # infra files: ~2% of N as supporting stanzas (avg 22 per file)
    n_infra = max(2, int(N * 0.02 / 22))
    for _ in range(n_infra):
        add('infra', 0)
    # huge files: 10% of N over H files (>= 5k stanzas each on the default run)
    H = max(1, round(N / 150000))
    huge_total = min(remaining, int(N * 0.10))
    if huge_total >= 100:
        for hh in range(H):
            share = huge_total // H + (1 if hh < huge_total % H else 0)
            add('huge', share)
        remaining -= huge_total
    # normal files
    while remaining > 0:
        u = rng.random()
        if u < .02:
            k = rng.randint(5, 60)
        elif u < .47:
            k = 1
        elif u < .72:
            k = rng.randint(2, 3)
        else:
            k = rng.randint(4, 8)
        k = min(k, remaining)
        add('normal', k)
        remaining -= k
    # encodings / eol / trailing newline / paths
    encs = ['utf-8'] * 84 + ['utf-8-sig'] * 3 + ['cp1252'] * 4 + ['latin-1'] * 3 + ['utf-16-le-bom'] * 3 + ['utf-16-be-bom'] * 3
    exts = ['.jil'] * 90 + ['.JIL', '.txt', '.jl', '.jil', '', '.jil.bak', '.conf', '.sh', '.jil', '.jil']
    for idx, f in enumerate(files):
        f['enc'] = rng.choice(encs)
        f['eol'] = rng.choices(['lf', 'crlf', 'cr'], [80, 15, 5])[0]
        f['trail'] = rng.random() > .1
        if f['recipe'] in ('binary',):
            f['ext'] = rng.choice(['.jil', '.bin', '.dat'])
        else:
            f['ext'] = rng.choice(exts)
        depth = rng.randint(0, 3)
        parts = [rng.choice(TOPS)] if depth else []
        for _ in range(max(0, depth - 1)):
            parts.append(rng.choice(SUBS))
        parts.append(f"b{idx // 400:04d}")
        f['path'] = '/'.join(parts + [f"f{idx:06d}_{rng.choice(DOMS)}{f['ext']}"])
        f['idx'] = idx
    files.sort(key=lambda f: f['path'])
    run = 1
    for f in files:
        f['base'] = run
        run += f['ncore']
        if f['recipe'] == 'update_delete':
            TOUCHED.update((f['base'], f['base'] + f['ncore'] - 1))
    return files


# ---------------------------------------------------------------------------
# per-file generation (worker)
# ---------------------------------------------------------------------------
_CFG = {}
TOUCHED = set()   # jids modified/deleted by update_job/override_job stanzas (never used as dup originals)


def gen_one(plan):
    seed = _CFG['seed']
    rng = random.Random(f"{seed}:{plan['idx']}")
    fc = FileCtx(seed, plan, rng)
    res = RECIPES[plan['recipe']](fc)
    enc = res.get('enc', plan['enc'])
    if plan['recipe'] == 'mixed_encoding':
        plan['enc'] = 'utf-8'
    asm = res.get('asm')
    if 'data' in res:
        data = res['data']
    else:
        data = finish(asm, enc, plan['eol'], plan['trail'])
    if 'entries' in res:
        ents = res['entries']
    else:
        ents = asm.entries if asm else []
    exact = res.get('exact', True)
    if plan['recipe'] in ('empty', 'whitespace', 'comment_only'):
        ents = []
    kind = res.get('kind', 'realistic' if plan['recipe'] == 'normal' else plan['recipe'])
    if plan['recipe'] == 'huge':
        kind = 'huge'
    rec = dict(path=plan['path'], encoding=enc, eol=plan['eol'], trailing_newline=plan['trail'],
               sha256=hashlib.sha256(data).hexdigest(), size=len(data), kind=kind, exact=exact, stanzas=ents)
    if not exact:
        rec['count_min'] = res.get('cmin', 0)
        rec['count_max'] = res.get('cmax', 10 ** 9)
        rec['accept'] = res.get('accept')
    full = os.path.join(_CFG['out'], plan['path'])
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'wb') as fh:
        fh.write(data)
    return json.dumps(rec, separators=(',', ':'), ensure_ascii=False)


def gen_chunk(plans):
    return [gen_one(p) for p in plans]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=600000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--pdf-examples', default=os.path.join(HERE, 'pdf_examples.json'))
    ap.add_argument('--no-pdf', action='store_true')
    a = ap.parse_args()
    t0 = time.time()
    pdf = [] if a.no_pdf else json.load(open(a.pdf_examples))
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    plan = make_plan(a.jobs, a.seed, pdf)
    _CFG.update(seed=a.seed, out=out)
    CH = 100
    chunks = [plan[i:i + CH] for i in range(0, len(plan), CH)]
    counts = Counter()
    kinds = Counter()
    cls = Counter()
    kc = Counter()
    encs = Counter()
    eols = Counter()
    nfiles = nbytes = nstanzas = 0
    core = 0
    max_st = 0
    dist = Counter()
    keyed = 0
    jf = os.path.join(out, 'manifest_files.jsonl')
    with open(jf, 'w', encoding='utf-8') as mf:
        pool = mp.get_context('fork').Pool(a.workers) if a.workers > 1 else None
        it = pool.imap(gen_chunk, chunks) if pool else map(gen_chunk, chunks)
        for lines in it:
            for ln in lines:
                mf.write(ln + '\n')
                rec = json.loads(ln)
                nfiles += 1
                nbytes += rec['size']
                encs[rec['encoding']] += 1
                eols[rec['eol']] += 1
                kinds['file:' + rec['kind']] += 1
                n = len(rec['stanzas'])
                nstanzas += n
                max_st = max(max_st, n)
                dist[0 if n == 0 else 1 if n == 1 else 2 if n <= 8 else 3 if n <= 100 else 4 if n < 5000 else 5] += 1
                for s in rec['stanzas']:
                    cls[s['expected_class']] += 1
                    kc[(s['kind'].split(':')[0] + ':' + s['kind'].split(':')[-1]) + '|' + s['expected_class']] += 1
                    if s.get('job') is True or (s.get('job') == 'optional' and not s['kind'].startswith('mut:victim')):
                        core += 1
                    if s.get('key'):
                        keyed += 1
            if nfiles % 20000 < CH:
                print(f'  {nfiles}/{len(plan)} files', file=sys.stderr, flush=True)
        if pool:
            pool.close()
            pool.join()
    summary = dict(
        generator='scripts/jil_stress/generate.py', seed=a.seed, requested_jobs=a.jobs,
        files_index='manifest_files.jsonl', path_order='sorted() of posix relative path; first occurrence wins',
        counts=dict(files=nfiles, bytes=nbytes, stanzas_listed=nstanzas, distinct_valid_insert_jobs=core,
                    keyed_clean_jobs=keyed, max_stanzas_in_a_file=max_st,
                    by_expected_class=dict(cls), files_by_encoding=dict(encs), files_by_eol=dict(eols),
                    files_by_kind=dict(sorted(kinds.items())),
                    stanzas_by_kind_and_class=dict(sorted(kc.items())),
                    files_by_stanza_count_bucket={'0': dist[0], '1': dist[1], '2-8': dist[2], '9-100': dist[3],
                                                  '101-4999': dist[4], '5000+': dist[5]}))
    with open(os.path.join(out, 'manifest.json'), 'w') as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary['counts'], indent=1))
    print('generation_seconds', round(time.time() - t0, 1))
    if core != a.jobs:
        print(f'WARNING: distinct valid insert_job stanzas {core} != requested {a.jobs}', file=sys.stderr)


if __name__ == '__main__':
    main()
