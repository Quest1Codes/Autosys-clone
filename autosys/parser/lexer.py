"""
AutoSys JIL (Job Information Language) Lexer.

What JIL looks like
--------------------
JIL is a flat attribute-value language.  A *stanza* begins with a directive
(``insert_job``, ``update_job``, etc.) and consists of attribute lines until
the next directive or end-of-file.

::

    /* block comment — may span multiple lines */

    insert_job: extract_sales   job_type: CMD       ← stanza header
    box_name: demo_etl_box                          ← attribute line
    command: /scripts/extract.sh --date %%DATE%%    ← value contains spaces
    machine: etl-server-01
    condition: success(check_source_ready)          ← complex value
    n_retrys: 2
    alarm_if_fail: 1

Design decisions
----------------
Two kinds of lines require different tokenisation:

1. **Stanza header line** (starts with a directive keyword):
   ``insert_job: NAME   job_type: CMD``
   — Multiple key:value pairs on one line.  Values are single tokens
     (no spaces).  The lexer emits DIRECTIVE + JOB_NAME + (ATTR_NAME + VALUE)*.

2. **Attribute line** (starts with a regular key name):
   ``command: /scripts/extract.sh --date %%DATE%%``
   — One key per line; the value is everything after the colon (trimmed).
     It may contain spaces, operators, quotes — anything.
     The lexer emits ATTR_NAME + VALUE (a LINE_VALUE covering the whole rest
     of the line).

This distinction mirrors how real AutoSys parses JIL.

Token stream example for the stanza above
-------------------------------------------
DIRECTIVE  "insert_job"
JOB_NAME   "extract_sales"
ATTR_NAME  "job_type"         ← inline attribute on the header line
VALUE      "CMD"              ← INLINE_VALUE
ATTR_NAME  "box_name"
VALUE      "demo_etl_box"     ← LINE_VALUE
ATTR_NAME  "command"
VALUE      "/scripts/extract.sh --date %%DATE%%"
ATTR_NAME  "machine"
VALUE      "etl-server-01"
ATTR_NAME  "condition"
VALUE      "success(check_source_ready)"
ATTR_NAME  "n_retrys"
VALUE      "2"
ATTR_NAME  "alarm_if_fail"
VALUE      "1"
EOF
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterator


# ===========================================================================
# Token model
# ===========================================================================

class TokenKind(str, Enum):
    """All token kinds produced by the JIL lexer."""

    DIRECTIVE    = "DIRECTIVE"
    """insert_job | update_job | delete_job | override_job.
    The directive *name only* — the colon has already been consumed."""

    JOB_NAME     = "JOB_NAME"
    """The job name that follows the directive on the stanza header line."""

    ATTR_NAME    = "ATTR_NAME"
    """An attribute key name (before the colon): box_name, command, …"""

    VALUE        = "VALUE"
    """An attribute value (after the colon).  May be a single word
    (inline on the header line) or a full rest-of-line string (attribute
    lines).  Quoted values have their surrounding double-quotes stripped."""

    EOF          = "EOF"
    """Sentinel — signals the end of the token stream."""


@dataclass(frozen=True)
class Token:
    """
    A single JIL token with its source location.

    ``line`` is 1-based and refers to the *original* source line
    (before comment removal).
    """
    kind:  TokenKind
    value: str        # the text of the token (quotes stripped for quoted values)
    line:  int        # 1-based source line number

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
# Lexer regexes
# ===========================================================================

# Directives that begin a new job stanza.
_DIRECTIVES = frozenset({
    "insert_job",
    "update_job",
    "delete_job",
    "override_job",
    "insert_machine",   # Phase 7: machine definitions
})

# Stanza header line:  directive_name: job_name  [attr: val  attr: val  ...]
# Group 1 = directive, Group 2 = job_name, Group 3 = rest of line (may be empty)
_STANZA_HEADER_RE = re.compile(
    r'^\s*'
    r'(insert_job|update_job|delete_job|override_job|insert_machine)'  # directive
    r'\s*:\s*'
    r'(\S+)'                                              # job_name / machine_name
    r'(.*)?$',                                            # rest of line
)

# Inline attributes that appear after the job_name on the stanza header line.
# Matches pairs like  job_type: CMD   or   machine: etl-01
# Each value is a single non-whitespace token.
_INLINE_ATTR_RE = re.compile(
    r'([a-z][a-z_0-9]*)'   # attribute name
    r'\s*:\s*'
    r'(\S+)',               # single-word value
)

# Regular attribute line:  attr_name: value  (value extends to EOL)
# Group 1 = attribute name, Group 2 = raw value (may need quote-stripping)
_ATTR_LINE_RE = re.compile(
    r'^\s*'
    r'([a-z][a-z_0-9]*)'   # attribute name
    r'\s*:\s*'
    r'(.*?)'               # value (non-greedy, whitespace stripped by $)
    r'\s*$',
)

# Block comment: /* ... */  (may span multiple lines — handled separately)
_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)


# ===========================================================================
# Comment stripper
# ===========================================================================

def strip_comments(text: str) -> str:
    """
    Remove ``/* ... */`` block comments from JIL text.

    Newlines *inside* comments are replaced with actual newlines so that
    all subsequent line numbers remain accurate.  All other comment text
    is replaced with spaces (so multi-token lines don't accidentally merge).

    Examples
    --------
    >>> strip_comments("/* top comment */\\ninsert_job: a   job_type: BOX")
    '                 \\ninsert_job: a   job_type: BOX'

    >>> strip_comments("a: 1  /* inline */ b: 2")
    'a: 1            b: 2'
    """
    def _replace(m: re.Match) -> str:
        comment = m.group(0)
        # Preserve newlines so line numbers stay correct.
        return "\n".join(" " * len(part) for part in comment.split("\n"))

    return _BLOCK_COMMENT_RE.sub(_replace, text)


# ===========================================================================
# Value normaliser
# ===========================================================================

def _strip_quotes(value: str) -> str:
    """Remove surrounding double-quotes from a quoted value string."""
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    return value


# ===========================================================================
# Lexer
# ===========================================================================

class Lexer:
    """
    Tokenizes AutoSys JIL text into a flat list of :class:`Token` objects.

    Usage
    -----
    ::

        tokens = Lexer().tokenize(jil_text)
        for tok in tokens:
            print(tok)

    The returned list always ends with an ``EOF`` token.
    """

    def tokenize(self, text: str) -> list[Token]:
        """
        Tokenize the full JIL *text* and return all tokens.

        Parameters
        ----------
        text:
            Raw JIL content (may include ``/* */`` block comments).

        Returns
        -------
        list[Token]
            Flat token list ending with ``Token(EOF, "", last_line)``.

        Raises
        ------
        LexError
            If a non-empty line cannot be parsed as either a stanza header
            or an attribute line.
        """
        clean = strip_comments(text)
        tokens: list[Token] = []

        for lineno, raw_line in enumerate(clean.split("\n"), start=1):
            line = raw_line.strip()
            if not line:
                continue   # blank / comment-only line

            line_tokens = self._tokenize_line(line, lineno)
            tokens.extend(line_tokens)

        last_line = text.count("\n") + 1
        tokens.append(Token(TokenKind.EOF, "", last_line))
        return tokens

    # ------------------------------------------------------------------
    # Internal line dispatcher
    # ------------------------------------------------------------------

    def _tokenize_line(self, line: str, lineno: int) -> list[Token]:
        """Tokenize a single stripped, non-empty line."""

        # --- Stanza header: insert_job/update_job/… ---
        m = _STANZA_HEADER_RE.match(line)
        if m:
            return self._tokenize_stanza_header(m, lineno)

        # --- Regular attribute line ---
        m = _ATTR_LINE_RE.match(line)
        if m:
            return self._tokenize_attr_line(m, lineno)

        # --- Unrecognised — surface a helpful error ---
        raise LexError("Cannot tokenise line", lineno, line)

    # ------------------------------------------------------------------
    # Stanza header tokeniser
    # ------------------------------------------------------------------

    def _tokenize_stanza_header(self, m: re.Match, lineno: int) -> list[Token]:
        """
        Emit tokens for the stanza header line.

        ``insert_job: extract_sales   job_type: CMD``
        →  [DIRECTIVE "insert_job", JOB_NAME "extract_sales",
            ATTR_NAME "job_type", VALUE "CMD"]
        """
        tokens: list[Token] = []

        directive = m.group(1)
        job_name  = m.group(2)
        rest      = m.group(3) or ""

        tokens.append(Token(TokenKind.DIRECTIVE, directive, lineno))
        tokens.append(Token(TokenKind.JOB_NAME,  job_name,  lineno))

        # Parse any additional key:value pairs on the same line
        # (typically just job_type: TYPE, but can be more)
        for attr_m in _INLINE_ATTR_RE.finditer(rest):
            attr_name = attr_m.group(1)
            raw_value = attr_m.group(2)
            tokens.append(Token(TokenKind.ATTR_NAME, attr_name,              lineno))
            tokens.append(Token(TokenKind.VALUE,     _strip_quotes(raw_value), lineno))

        return tokens

    # ------------------------------------------------------------------
    # Attribute line tokeniser
    # ------------------------------------------------------------------

    def _tokenize_attr_line(self, m: re.Match, lineno: int) -> list[Token]:
        """
        Emit tokens for a regular attribute line.

        ``command: /scripts/extract.sh --date %%DATE%%``
        →  [ATTR_NAME "command", VALUE "/scripts/extract.sh --date %%DATE%%"]

        ``start_times: "06:00"``
        →  [ATTR_NAME "start_times", VALUE "06:00"]   ← quotes stripped
        """
        attr_name = m.group(1)
        raw_value = m.group(2)
        value     = _strip_quotes(raw_value)

        return [
            Token(TokenKind.ATTR_NAME, attr_name, lineno),
            Token(TokenKind.VALUE,     value,     lineno),
        ]


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
