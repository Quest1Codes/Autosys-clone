"""
AutoSys JIL (Job Information Language) Lexer.

What JIL looks like
--------------------
JIL is a flat attribute-value language.  A *stanza* begins with a directive
(``insert_job``, ``update_job``, etc.) and consists of attribute statements
until the next directive or end-of-file.

::

    /* block comment — may span multiple lines */
    # whole-line comment (pound sign as the first non-blank character)

    insert_job: extract_sales   job_type: CMD       ← stanza header
    box_name: demo_etl_box                          ← attribute line
    command: /scripts/extract.sh --date %%DATE%%    ← value contains spaces
    machine: etl-server-01  owner: svc_demo         ← several statements per line
    condition: success(check_source_ready)          ← complex value
    n_retrys: 2
    alarm_if_fail: 1

Rules implemented (vendor PDF, "JIL Syntax Rules")
---------------------------------------------------
* Rule 3  Several ``attribute: value`` statements may share a line, separated
  by whitespace.  A statement ends at the next whitespace-preceded
  ``<known attribute>:`` that is outside quotes / not backslash-escaped.
* Rule 5/6  A value containing a colon must be quoted or the colon escaped
  (``10\\:00``).  Quoted values keep embedded blanks.  ``\\:``, ``\\,`` and
  ``\\"`` are unescaped.
* Rule 7  ``#`` as the first non-blank character comments out the line.
  ``/* ... */`` comments are ignored inside quoted strings; the ``/*`` must be
  preceded by whitespace (or start the line) and ``*/`` followed by whitespace
  (or end the line) so globs such as ``/tmp/*/x`` survive.
* Rule 8  ``blob_input: <auto_blobt> ... </auto_blobt>`` is taken literally,
  across lines, with no comment / attribute processing inside.
* A value may continue on following lines (lines that are not themselves
  ``keyword:`` statements are appended), and a quoted value may span lines.
* Keys are case-insensitive; they are lower-cased except for the handful of
  attributes the PDF documents in mixed case (``URL``, ``ftp_use_SSL``, ...).

Token stream example for the stanza above
-------------------------------------------
DIRECTIVE  "insert_job"
JOB_NAME   "extract_sales"
ATTR_NAME  "job_type"         ← inline attribute on the header line
VALUE      "CMD"
ATTR_NAME  "box_name"
VALUE      "demo_etl_box"
...
EOF
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from autosys.parser.jil_attrs import canonical_key, is_known_attr


# ===========================================================================
# Token model
# ===========================================================================

class TokenKind(str, Enum):
    """All token kinds produced by the JIL lexer."""

    DIRECTIVE    = "DIRECTIVE"
    """insert_job | update_job | delete_job | override_job | ...
    The directive *name only* — the colon has already been consumed."""

    JOB_NAME     = "JOB_NAME"
    """The object name that follows the directive on the stanza header line."""

    ATTR_NAME    = "ATTR_NAME"
    """An attribute key name (before the colon): box_name, command, …"""

    VALUE        = "VALUE"
    """An attribute value (after the colon).  Quoted values have their
    surrounding double-quotes stripped; escapes are resolved."""

    RAW          = "RAW"
    """Tolerant mode only: text that could not be tokenised (an unknown
    sub-command with its body, stray lines before any stanza, a header with no
    name, ...).  Nothing is dropped — the parser turns it into a quarantined
    operation that keeps the raw source."""

    EOF          = "EOF"
    """Sentinel — signals the end of the token stream."""


@dataclass(frozen=True)
class Token:
    """
    A single JIL token with its source location.

    ``line`` is 1-based and refers to the *original* source line on which the
    statement started.
    """
    kind:  TokenKind
    value: str
    line:  int

    def __repr__(self) -> str:
        return f"Token({self.kind.value}, {self.value!r}, L{self.line})"


# ===========================================================================
# Lex error
# ===========================================================================

class LexError(Exception):
    """Raised when a line cannot be tokenised."""
    def __init__(self, message: str, line: int, raw: str = "") -> None:
        detail = f" — {raw!r}" if raw else ""
        super().__init__(f"JIL lex error at line {line}: {message}{detail}")
        self.source_line = line
        self.raw_line    = raw


# ===========================================================================
# Vocabulary
# ===========================================================================

# Sub-commands that begin a new stanza (every one the PDF documents).
_DIRECTIVES = frozenset({
    "insert_job", "update_job", "delete_job", "override_job", "rename_job",
    "delete_box",
    "insert_machine", "update_machine", "delete_machine",
    "insert_job_type", "update_job_type", "delete_job_type",
    "insert_monbro", "update_monbro", "delete_monbro",
    "insert_blob", "update_blob", "delete_blob",
    "insert_glob", "update_glob", "delete_glob",
    "insert_xinst", "update_xinst", "delete_xinst",
    "insert_resource", "update_resource", "delete_resource",
    "insert_connectionprofile", "update_connectionprofile",
    "delete_connectionprofile",
    "insert_calendar", "update_calendar", "delete_calendar",
    "insert_view", "modify_view", "delete_view",
    "insert_filter", "modify_filter", "delete_filter",
    "insert_alert_policy", "modify_alert_policy", "delete_alert_policy",
    "delete_user",
})

_DIRECTIVE_RE = re.compile(
    r'^\s*(' + '|'.join(sorted(_DIRECTIVES, key=len, reverse=True)) + r')'
    r'\s*:\s*(.*)$',
    re.IGNORECASE | re.DOTALL,
)

# Anything that *looks* like a sub-command but is not one we know: rejected
# instead of being silently swallowed as an attribute of the previous stanza.
_UNKNOWN_DIRECTIVE_RE = re.compile(
    r'^\s*((?:insert|update|delete|override|rename|modify)_[A-Za-z_0-9]+)\s*:',
    re.IGNORECASE,
)

_KEY = r'[A-Za-z][A-Za-z_0-9.\-]*'

# A statement start at the beginning of a line:  key :
_ATTR_START_RE = re.compile(r'^\s*(' + _KEY + r')\s*:')
# A statement start in the middle of a line (after whitespace was skipped).
_KEYCOLON_RE   = re.compile(r'(' + _KEY + r')\s*:')
_BARE_KEY_RE   = re.compile(r'^' + _KEY + r'$')

# Attributes whose value is free text and may have been wrapped onto the next
# line by whatever produced the file. Any other attribute takes one value, so
# a following non-attribute line is stray text, not a continuation.
_FREE_TEXT_ATTRS = frozenset({
    "command", "description", "condition", "box_success", "box_failure",
    "notification_msg", "resources",
})
_LOWER_KEY_RE  = re.compile(r'[a-z][a-z_0-9]*$')

# Common English words that are also attribute names.  Mid-line they are far
# more likely to be part of free text ("description: Server type: web") than a
# new statement, so they only start a statement at the beginning of a line or
# on a stanza header.
_AMBIGUOUS_KEYS = frozenset({
    "text", "type", "status", "mode", "server", "filter", "view", "force",
    "active", "running", "success", "failure", "starting", "terminated",
    "suspended", "restart", "alarm", "sound", "name",
})

# Header-line attributes whose value must be a single token.
_SINGLE_TOKEN_KEYS = frozenset({"job_type", "box_name", "machine", "n_retrys"})

# Attributes holding shell text: a bare ``\"`` there must stay as written.
_SHELL_KEYS = frozenset({"command", "sql_command"})

# Object names with blanks may be quoted; besides ``"..."`` the PDF's own
# examples use single and typographic quotes (e.g. alert policy names).
_NAME_QUOTES = {'"': '"', "'": "'", "\u2018": "\u2019", "\u201c": "\u201d"}

_BLOB_OPEN  = "<auto_blobt>"
_BLOB_CLOSE = "</auto_blobt>"


# ===========================================================================
# Comment stripper
# ===========================================================================

def _quote_opens(text: str, i: int, start: int = 0) -> bool:
    """True if the ``"`` at *i* opens a quoted string that closes on the line."""
    if i > start and text[i - 1] not in " \t=,(":
        return False
    return _find_close_quote(text, i + 1, stop_at_newline=True) != -1


def _find_close_quote(text: str, j: int, stop_at_newline: bool = False) -> int:
    """Index of the next unescaped ``"`` at or after *j*, or -1."""
    n = len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j
        if stop_at_newline and c == "\n":
            return -1
        j += 1
    return -1


def strip_comments(text: str) -> str:
    """
    Remove ``/* ... */`` block comments from JIL text.

    * Text inside double-quoted strings is never a comment.
    * ``/*`` opens a comment only at the start of a line or after whitespace
      (so ``/tmp/*/x`` is left alone); ``*/`` closes it only when followed by
      whitespace or the end of input.
    * ``<auto_blobt>...</auto_blobt>`` blocks are copied verbatim.
    * An unterminated ``/*`` is left as literal text (the lexer will then
      report it) — and is detected in linear time.

    Newlines inside comments are kept so line numbers stay accurate; all other
    comment characters become spaces.

    >>> strip_comments("a: 1  /* inline */ b: 2")
    'a: 1               b: 2'
    """
    out: list[str] = []
    n = len(text)
    i = 0
    in_quote = False
    no_close = False        # once no closer exists, none ever will later on
    while i < n:
        c = text[i]
        if c == "\n":
            in_quote = False
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n and text[i + 1] != "\n":
            out.append(text[i:i + 2])
            i += 2
            continue
        if c == '"':
            if in_quote:
                in_quote = False
            elif _quote_opens(text, i, text.rfind("\n", 0, i) + 1):
                in_quote = True
            out.append(c)
            i += 1
            continue
        if not in_quote and c == "<" and text.startswith(_BLOB_OPEN, i):
            k = i - 1
            while k >= 0 and text[k] in " \t":
                k -= 1
            if k >= 0 and text[k] == ":":
                end = text.find(_BLOB_CLOSE, i)
                end = n if end == -1 else end + len(_BLOB_CLOSE)
                out.append(text[i:end])
                i = end
                continue
        if (not in_quote and not no_close and c == "/"
                and text.startswith("/*", i)
                and (i == 0 or text[i - 1].isspace())):
            j = i + 2
            while True:
                k = text.find("*/", j)
                if k == -1:
                    no_close = True
                    break
                if k + 2 >= n or text[k + 2].isspace():
                    break
                j = k + 2
            if k != -1:
                seg = text[i:k + 2]
                out.append(re.sub(r"[^\n]", " ", seg))
                i = k + 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


# ===========================================================================
# Value normaliser
# ===========================================================================

def _unescape(v: str, key: str) -> str:
    """Resolve ``\\:`` and ``\\,`` (and ``\\"`` outside shell text)."""
    v = v.replace("\\:", ":").replace("\\,", ",")
    if key not in _SHELL_KEYS:
        v = v.replace('\\"', '"')
    return v


def _normalize_value(raw: str, key: str) -> str:
    """
    Turn a raw statement value into the attribute value.

    A value that is exactly one double-quoted token loses its quotes.  Any
    other value (unquoted, or several quoted pieces such as
    ``"10:00","11:00"`` / ``"a" && "b"``) is kept verbatim apart from escape
    resolution, so nothing between the quotes is destroyed.
    """
    v = raw.strip()
    if len(v) >= 2 and v[0] == '"':
        k = _find_close_quote(v, 1)
        if k == len(v) - 1:
            return v[1:-1].replace('\\"', '"')
    return _unescape(v, key)


def _strip_quotes(value: str) -> str:
    """Backwards-compatible helper: remove one surrounding pair of quotes."""
    return _normalize_value(value, "")


# ===========================================================================
# Statement splitter
# ===========================================================================

def _is_boundary(key: str, header: bool) -> bool:
    if header:
        return is_known_attr(key) or bool(_LOWER_KEY_RE.match(key))
    return is_known_attr(key) and key.lower() not in _AMBIGUOUS_KEYS


def _blob_span(text: str, i: int, lineno: int):
    """If a ``<auto_blobt>`` block starts at *i* (after blanks) return spans."""
    j = i
    n = len(text)
    while j < n and text[j] in " \t":
        j += 1
    if text.startswith(_BLOB_OPEN, j):
        close = text.find(_BLOB_CLOSE, j + len(_BLOB_OPEN))
        if close == -1:
            raise LexError("Unterminated <auto_blobt> block", lineno, text[j:j + 40])
        return j + len(_BLOB_OPEN), close, close + len(_BLOB_CLOSE)
    return None


def _scan_value(text: str, i: int, header: bool):
    """
    Scan a value starting at *i*; return ``(end, next_key_match_or_None)``.

    The value ends at the next whitespace-preceded, unescaped, unquoted
    ``<attribute>:``.
    """
    n = len(text)
    start = i
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == '"' and _quote_opens(text, i, start):
            k = _find_close_quote(text, i + 1)
            if k != -1:
                i = k + 1
                continue
        if c.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            m = _KEYCOLON_RE.match(text, j)
            if m and _is_boundary(m.group(1), header):
                return i, m
            i = j
            continue
        i += 1
    return n, None


def _split_statements(text: str, header: bool, lineno: int) -> list[tuple[str, str, bool]]:
    """Split *text* into ``(key, raw_value, is_blob)`` statements."""
    m = _ATTR_START_RE.match(text)
    if not m:
        raise LexError("Expected 'attribute: value'", lineno, text[:60])
    stmts: list[tuple[str, str, bool]] = []
    key = m.group(1)
    i = m.end()
    n = len(text)
    while True:
        blob = _blob_span(text, i, lineno)
        if blob:
            s, e, end = blob
            stmts.append((key, text[s:e], True))
            i = end
            while i < n and text[i].isspace():
                i += 1
            if i >= n:
                break
            m2 = _KEYCOLON_RE.match(text, i)
            if not m2:
                raise LexError("Unexpected text after </auto_blobt>", lineno, text[i:i + 40])
            key = m2.group(1)
            i = m2.end()
            continue
        end, m2 = _scan_value(text, i, header)
        stmts.append((key, text[i:end], False))
        if m2 is None:
            break
        key = m2.group(1)
        i = m2.end()
    return stmts


# ===========================================================================
# Lexer
# ===========================================================================

@dataclass
class _Entry:
    lineno:    int
    text:      str
    is_header: bool
    raw:       bool = False        # tolerant mode: quarantined block
    reason:    str  = ""


def _open_blob(text: str) -> bool:
    idx = text.rfind(_BLOB_OPEN)
    if idx == -1 or text.find(_BLOB_CLOSE, idx) != -1:
        return False
    k = idx - 1
    while k >= 0 and text[k] in " \t":
        k -= 1
    return k >= 0 and text[k] == ":"


def _open_value_quote(text: str) -> bool:
    """A body value that starts with ``"`` but has no closing quote yet."""
    m = _ATTR_START_RE.match(text)
    if not m:
        return False
    j = m.end()
    while j < len(text) and text[j] in " \t":
        j += 1
    return text[j:j + 1] == '"' and _find_close_quote(text, j + 1) == -1


def normalize_text(text: str) -> str:
    """The text the lexer actually sees: BOM removed, ``\\r\\n``/``\\r`` -> ``\\n``."""
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


class Lexer:
    """
    Tokenizes AutoSys JIL text into a flat list of :class:`Token` objects.

    ::

        tokens = Lexer().tokenize(jil_text)

    The returned list always ends with an ``EOF`` token.

    ``Lexer(tolerant=True)`` never raises :class:`LexError`.  Every problem is
    recorded in :attr:`diagnostics` (``{line, severity, code, message, raw}``)
    and the affected text is either kept in a best-effort form or emitted as a
    ``RAW`` token so no input is lost.  The default (strict) mode raises.
    """

    def __init__(self, tolerant: bool = False) -> None:
        self.tolerant = tolerant
        self.diagnostics: list[dict] = []

    def _diag(self, line: int, code: str, message: str, raw: str = "",
              severity: str = "warning") -> None:
        self.diagnostics.append({
            "line": line, "severity": severity, "code": code,
            "message": message, "raw": raw[:200],
        })

    def tokenize(self, text: str) -> list[Token]:
        """
        Tokenize the full JIL *text* and return all tokens.

        Raises
        ------
        LexError
            If a line cannot be tokenised (unknown sub-command, junk after a
            stanza header, unterminated quote / blob, ...).
        """
        text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
        clean = strip_comments(text)

        self.diagnostics = []
        if self.tolerant:
            clean = self._drop_unterminated_comment(clean)
        tokens: list[Token] = []
        for entry in self._assemble(clean.split("\n")):
            if not self.tolerant:
                tokens.extend(self._tokenize_entry(entry))
                continue
            try:
                tokens.extend(self._tokenize_entry(entry))
            except (LexError, Exception) as exc:          # never lose text
                code = "lex_error" if isinstance(exc, LexError) else "internal_error"
                self._diag(entry.lineno, code, str(exc) if code == "lex_error" else repr(exc),
                           entry.text, "error")
                # A header that cannot be read starts a quarantined block.  A
                # failing body statement stays inside its stanza (its text is
                # in the stanza's raw source) and is only a diagnostic.
                if entry.is_header:
                    tokens.append(Token(TokenKind.RAW, entry.text, entry.lineno))

        tokens.append(Token(TokenKind.EOF, "", text.count("\n") + 1))
        return tokens

    # ------------------------------------------------------------------
    # Pass 1: physical lines -> logical statements
    # ------------------------------------------------------------------

    @staticmethod
    def _inside_value(prefix: str) -> bool:
        """True when *prefix* ends inside an open <auto_blobt> or double-quoted value."""
        if prefix.count("<auto_blobt>") > prefix.count(_BLOB_CLOSE):
            return True
        start = 0
        for m in re.finditer(r"(?m)^(?:insert|update|override|delete)_\w+\s*:", prefix):
            start = m.start()
        body = re.sub(r"(?s)<auto_blobt>.*?</auto_blobt>", "", prefix[start:])
        return body.count('"') % 2 == 1

    def _drop_unterminated_comment(self, clean: str) -> str:
        """A ``/*`` that starts a line and is never closed comments out the rest of
        the file — but only when no sub-command follows (otherwise real stanzas
        would vanish from the typed tables; their text stays in the archive)."""
        lines = clean.split("\n")
        for i, l in enumerate(lines):
            if (l.lstrip().startswith("/*") and (i == 0 or not lines[i - 1].strip())
                    and "*/" not in "\n".join(lines[i:])
                    and not self._inside_value("\n".join(lines[:i]))):
                if any(_DIRECTIVE_RE.match(x.strip()) for x in lines[i + 1:]):
                    return clean
                self._diag(i + 1, "unterminated_comment",
                           "/* comment is never closed; the rest of the file is treated "
                           "as a comment", l.strip(), "info")
                return "\n".join(lines[:i])
        return clean

    def _assemble(self, lines: list[str]) -> list[_Entry]:
        tol = self.tolerant
        entries: list[_Entry] = []
        prev: _Entry | None = None
        raw_entry: _Entry | None = None     # open quarantined block (tolerant)
        n = len(lines)
        i = 0
        while i < n:
            lineno = i + 1
            s = lines[i].strip()
            if tol and s[:1] in ("\ufeff", "\xff", "\xfe"):
                s = s.lstrip("\ufeff\xff\xfe").strip()   # stray BOM bytes mid-file
            i += 1
            if not s or s.startswith("#") or s.lower() == "exit":
                continue

            # keyword on one line, ": value" on the next
            if _BARE_KEY_RE.match(s):
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and lines[j].strip().startswith(":"):
                    s = s + lines[j].strip()
                    i = j + 1

            unknown = _UNKNOWN_DIRECTIVE_RE.match(s)
            if _DIRECTIVE_RE.match(s):
                raw_entry = None
                entry = _Entry(lineno, s, True)
                entries.append(entry)
                prev = entry
            elif unknown and not is_known_attr(unknown.group(1)):
                if not tol:
                    raise LexError("Unknown JIL sub-command", lineno, s)
                self._diag(lineno, "unknown_subcommand",
                           f"Unknown JIL sub-command {unknown.group(1)!r}; "
                           "block quarantined", s, "error")
                entry = _Entry(lineno, s, False, raw=True, reason="unknown sub-command")
                entries.append(entry)
                prev = raw_entry = entry
                continue
            elif raw_entry is not None:
                # everything up to the next real sub-command belongs to the
                # quarantined block
                raw_entry.text += "\n" + s
                continue
            elif _ATTR_START_RE.match(s):
                entry = _Entry(lineno, s, False)
                entries.append(entry)
                prev = entry
            else:
                # continuation of the previous attribute's value ...
                if prev is None:
                    if not tol:
                        raise LexError("Cannot tokenise line", lineno, s)
                    self._diag(lineno, "stray_text",
                               "Text before any stanza; quarantined", s, "error")
                    entry = _Entry(lineno, s, False, raw=True, reason="stray text")
                    entries.append(entry)
                    prev = raw_entry = entry
                    continue
                if prev.is_header:
                    if not tol:
                        raise LexError("Cannot tokenise line", lineno, s)
                    self._diag(lineno, "unparsed_line",
                               "Line is not an attribute; kept in the stanza's "
                               "raw text only", s)
                    continue
                key, _, so_far = prev.text.partition(":")
                key = key.strip().lower()
                if so_far.strip() and key not in _FREE_TEXT_ATTRS:
                    # machine, owner, box_name, ... hold one token: a stray
                    # line (a pasted `sendevent ...`) glued on silently became
                    # machine "m1 sendevent ..." (audit PARSER-08). Real jil
                    # rejects the line; keep it in the raw text only.
                    if not tol:
                        raise LexError("Cannot tokenise line", lineno, s)
                    self._diag(lineno, "stray_line",
                               f"Line is not an attribute and {key!r} takes a single "
                               "value; not applied (kept in the stanza's raw text)", s)
                    continue
                if tol and so_far.strip():
                    self._diag(lineno, "suspect_continuation",
                               f"Line appended to the previous {key!r} value; verify it "
                               "is a wrapped value, not stray text", s)
                prev.text += " " + s
                entry = prev

            # Constructs that swallow following raw lines.
            if not entry.is_header:
                while _open_blob(entry.text):
                    if i >= n or (tol and not any(_BLOB_CLOSE in l for l in lines[i:])):
                        if not tol:
                            raise LexError("Unterminated <auto_blobt> block",
                                           entry.lineno, entry.text[:40])
                        self._diag(entry.lineno, "unterminated_blob",
                                   "<auto_blobt> is never closed; kept literally",
                                   entry.text, "error")
                        break
                    entry.text += "\n" + lines[i]
                    i += 1
                while _open_value_quote(entry.text):
                    if tol:
                        j = self._quote_close_line(lines, i, entry.text)
                        if j == -1:
                            self._diag(entry.lineno, "unterminated_quote",
                                       "Quoted value is never closed; kept literally",
                                       entry.text, "error")
                            break
                        while i <= j:
                            entry.text += "\n" + lines[i]
                            i += 1
                        continue
                    if i >= n:
                        raise LexError("Unterminated quoted value",
                                       entry.lineno, entry.text[:40])
                    entry.text += "\n" + lines[i]
                    i += 1
        return entries

    @staticmethod
    def _quote_close_line(lines: list[str], i: int, text: str) -> int:
        """Index of the line that closes an open value quote, or -1 (stops at the
        next sub-command so an unbalanced quote cannot swallow later stanzas)."""
        buf = text
        for j in range(i, len(lines)):
            if lines[j][:1] not in (" ", "\t") and _DIRECTIVE_RE.match(lines[j].strip()):
                return -1        # an unindented sub-command starts a new stanza
            buf += "\n" + lines[j]
            if not _open_value_quote(buf):
                return j
        return -1

    # ------------------------------------------------------------------
    # Pass 2: logical statement -> tokens
    # ------------------------------------------------------------------

    def _tokenize_entry(self, entry: _Entry) -> list[Token]:
        if entry.raw:
            return [Token(TokenKind.RAW, entry.text, entry.lineno)]
        if entry.is_header:
            return self._tokenize_header(entry)
        return self._emit(_split_statements(entry.text, False, entry.lineno), entry.lineno)

    @staticmethod
    def _emit(stmts: list[tuple[str, str, bool]], lineno: int) -> list[Token]:
        out: list[Token] = []
        for key, raw, is_blob in stmts:
            k = canonical_key(key)
            out.append(Token(TokenKind.ATTR_NAME, k, lineno))
            out.append(Token(TokenKind.VALUE, raw if is_blob else _normalize_value(raw, k), lineno))
        return out

    def _tokenize_header(self, entry: _Entry) -> list[Token]:
        """
        ``insert_job: extract_sales   job_type: CMD``
        →  [DIRECTIVE, JOB_NAME, ATTR_NAME job_type, VALUE CMD]
        """
        lineno = entry.lineno
        m = _DIRECTIVE_RE.match(entry.text)
        directive = m.group(1).lower()
        rest = m.group(2).strip()

        # --- object name: bare token, or "quoted", with \: escapes ---
        if not rest:
            raise LexError(f"{directive} needs a name", lineno, entry.text)
        if self.tolerant and _ATTR_START_RE.match(rest) and rest.split(None, 1)[0].endswith(":"):
            # "insert_job:   job_type: CMD" — the name is missing, not "job_type:"
            raise LexError(f"{directive} needs a name", lineno, entry.text)
        if rest[0] in _NAME_QUOTES:
            closer = _NAME_QUOTES[rest[0]]
            k = (_find_close_quote(rest, 1) if closer == '"'
                 else rest.find(closer, 1))
            if k == -1:
                raise LexError("Unterminated quoted name", lineno, rest)
            name, rest = rest[1:k], rest[k + 1:]
        else:
            nm = re.match(r'(?:\\.|[^\s\\])+', rest)
            if nm is None:
                raise LexError("Invalid object name", lineno, rest)
            name, rest = nm.group(0).replace("\\:", ":"), rest[nm.end():]
        tokens = [Token(TokenKind.DIRECTIVE, directive, lineno),
                  Token(TokenKind.JOB_NAME, name, lineno)]

        rest = rest.strip()
        if directive == "override_job" and re.match(r'delete(\s|$)', rest, re.IGNORECASE):
            tokens += [Token(TokenKind.ATTR_NAME, "delete", lineno),
                       Token(TokenKind.VALUE, "1", lineno)]
            rest = rest[6:].strip()

        if rest:
            if not _ATTR_START_RE.match(rest):
                if not self.tolerant:
                    raise LexError("Unexpected text after stanza header", lineno, rest)
                # keep whatever statements follow the junk
                junk, rest = self._skip_header_junk(rest)
                self._diag(lineno, "header_junk",
                           f"Unexpected text after stanza header ignored: {junk!r}",
                           junk)
            if rest:
                stmts = _split_statements(rest, True, lineno)
                fixed: list[tuple[str, str, bool]] = []
                for key, raw, blob in stmts:
                    v = raw.strip()
                    if (key.lower() in _SINGLE_TOKEN_KEYS and v[:1] != '"'
                            and re.search(r'\s', v)):
                        if not self.tolerant:
                            raise LexError(
                                f"Unexpected text after {key}: value", lineno, v)
                        first, _, extra = v.partition(" ")
                        extra = re.split(r'\s+', v, maxsplit=1)
                        self._diag(lineno, "value_junk",
                                   f"Unexpected text after {key}: {extra[0]!r} "
                                   f"ignored: {extra[1]!r}", v)
                        raw = extra[0]
                    fixed.append((key, raw, blob))
                tokens += self._emit(fixed, lineno)
        return tokens

    @staticmethod
    def _skip_header_junk(rest: str) -> tuple[str, str]:
        """Split header text that does not start with ``key:`` into
        ``(junk, remainder_starting_at_the_first_statement)``."""
        n = len(rest)
        i = 0
        while i < n:
            if rest[i].isspace():
                j = i
                while j < n and rest[j].isspace():
                    j += 1
                m = _KEYCOLON_RE.match(rest, j)
                if m and _is_boundary(m.group(1), True):
                    return rest[:i], rest[j:]
                i = j
                continue
            i += 1
        return rest, ""


# ===========================================================================
# Convenience top-level function
# ===========================================================================

def tokenize(text: str) -> list[Token]:
    """
    Tokenize JIL *text* — convenience wrapper around :class:`Lexer`.

    >>> tokens = tokenize('insert_job: my_job   job_type: CMD\\ncommand: echo hi')
    >>> [t.kind.value for t in tokens if t.kind != TokenKind.EOF]
    ['DIRECTIVE', 'JOB_NAME', 'ATTR_NAME', 'VALUE', 'ATTR_NAME', 'VALUE']
    """
    return Lexer().tokenize(text)
