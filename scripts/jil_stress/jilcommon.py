"""Shared helpers for the JIL stress harness (independent of the importer)."""
import re

# A top-level "directive line": a sub-command name followed by a colon.
DIRECTIVE_RE = re.compile(
    r'^[ \t]*((?:insert|update|delete|override|rename|modify)_[A-Za-z_]+)[ \t]*:', re.I | re.M)
DIRECTIVE_LINE_RE = re.compile(
    r'^[ \t]*(?:insert|update|delete|override|rename|modify)_[A-Za-z_]+[ \t]*:', re.I)

_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.S)
_NONASCII_RUN = re.compile(r'[^\x00-\x7f]+')


def norm_newlines(s: str) -> str:
    return s.replace('\r\n', '\n').replace('\r', '\n')


def lenient(s: str) -> str:
    """Collapse every run of non-ASCII chars (used for undecodable files)."""
    return _NONASCII_RUN.sub('?', s)


def decode(data: bytes, enc: str) -> str:
    if enc == 'utf-8':
        return data.decode('utf-8', 'replace')
    if enc == 'utf-8-sig':
        return data.decode('utf-8-sig', 'replace')
    if enc in ('cp1252',):
        return data.decode('cp1252', 'replace')
    if enc == 'latin-1':
        return data.decode('latin-1')
    if enc in ('utf-16-le-bom', 'utf-16-be-bom'):
        return data.decode('utf-16', 'replace')     # BOM selects endianness
    # mixed_invalid / binary / unknown
    return data.decode('utf-8', 'replace')


LENIENT_ENCODINGS = ('mixed_invalid', 'binary')


def lost_lines(gap: str):
    """Return the list of non-blank, non-comment lines in a gap of source text.

    Allowed to be absent from every stanza: blank lines, '#' lines and
    complete /* ... */ comments.  An unterminated '/*' running to EOF is also
    tolerated unless it swallows something that looks like a directive line.
    """
    g = _BLOCK_COMMENT_RE.sub('\n', gap)
    i = g.find('/*')
    if i != -1:
        tail = g[i:]
        if any(DIRECTIVE_LINE_RE.match(l) for l in tail.split('\n')[1:]) or \
           DIRECTIVE_LINE_RE.match(tail[2:]):
            return [l for l in tail.split('\n') if l.strip()] or ['/*']
        g = g[:i]
    out = []
    for line in g.split('\n'):
        s = line.strip()
        if s and not s.startswith('#'):
            out.append(line)
    return out


def ref_directive_count(text: str) -> int:
    """Naive count of directive-looking lines (used only for approximate files)."""
    return len(DIRECTIVE_RE.findall(norm_newlines(text)))
