"""
AutoSys JIL Parser.

Converts the flat token stream from :mod:`autosys.parser.lexer` into a list
of :class:`JILOperation` objects, each of which wraps a validated Pydantic
:class:`~autosys.models.job.Job` model (or an Event / GlobalVariable).

Pipeline
--------
::

    JIL text
      │
      ▼  strip_comments + Lexer.tokenize()
    Token stream
      │
      ▼  JILParser.parse()
    list[JILOperation]
      │
      ▼  each op.job  is a validated CmdJob / BoxJob / … Pydantic model

Operations recognised
----------------------
insert_job   → JILOperation(op="insert",   job=<Job>)
update_job   → JILOperation(op="update",   job=<Job>)
delete_job   → JILOperation(op="delete",   job=<Job>)  # partial — name only
override_job → JILOperation(op="override", job=<Job>)

How attribute coercion works
-----------------------------
Raw JIL values are always strings.  Before handing them to Pydantic, the
parser coerces them to the right Python types based on the attribute name:

- Boolean flags (``alarm_if_fail``, ``box_terminator``, …)  →  bool
- Integer attributes (``n_retrys``, ``max_run_alarm``, …)   →  int
- Everything else                                            →  str

Pydantic handles ``start_times`` and ``days_of_week`` normalisation
(comma-split, quote-strip, "all" expansion) via its own validators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autosys.models.job import Job, parse_job, CmdJob
from autosys.models.machine import MachineDef
from autosys.parser.lexer import Lexer, Token, TokenKind


# ===========================================================================
# Public data structures
# ===========================================================================

@dataclass
class JILOperation:
    """
    A single parsed JIL operation — either a job or a machine definition.

    Attributes
    ----------
    op:
        For jobs: ``"insert"``, ``"update"``, ``"delete"``, ``"override"``.
        For machines: ``"insert_machine"``.
    job:
        The validated Pydantic Job model (CmdJob, BoxJob, etc.).
        ``None`` for machine operations — use ``machine`` instead.
    machine:
        The validated MachineDef model.  Set only when ``op == "insert_machine"``.
    raw_attrs:
        The un-coerced key:value dict exactly as the lexer produced it.
    source_line:
        The 1-based source line where this stanza started.
    """
    op:          str                   # "insert" | "update" | "delete" | "override" | "insert_machine"
    job:         Job | None
    machine:     MachineDef | None     = None
    raw_attrs:   dict[str, str]        = field(default_factory=dict)
    source_line: int                   = 0


class JILParseError(Exception):
    """Raised when the parser encounters unexpected tokens or invalid JIL."""
    def __init__(self, message: str, line: int = 0) -> None:
        loc = f" (line {line})" if line else ""
        super().__init__(f"JIL parse error{loc}: {message}")
        self.source_line = line


# ===========================================================================
# Attribute type coercion rules
# ===========================================================================

# Attributes whose raw "0"/"1" values should become Python bools.
# These map directly to JIL boolean flags.
_BOOL_ATTRS: frozenset[str] = frozenset({
    "alarm_if_fail",
    "alarm_if_terminated",
    "box_terminator",
    "send_report",
    "date_conditions",
})

# Attributes whose raw numeric strings should become Python ints.
_INT_ATTRS: frozenset[str] = frozenset({
    "n_retrys",
    "max_run_alarm",
    "min_run_alarm",
    "term_run_time",
    "job_load",
    "max_load",
    "watch_file_min_size",
    "watch_interval",
})

# Map JIL directive keyword → short op code stored in JILOperation.op
_DIRECTIVE_TO_OP: dict[str, str] = {
    "insert_job":      "insert",
    "update_job":      "update",
    "delete_job":      "delete",
    "override_job":    "override",
    "insert_machine":  "insert_machine",
}

# Machine integer attributes
_MACHINE_INT_ATTRS: frozenset[str] = frozenset({"port", "max_load"})


def _coerce(attr: str, raw: str) -> Any:
    """
    Convert a raw JIL attribute value string to the right Python type.

    Boolean conversion:  "1", "true", "yes" (case-insensitive) → True
                         anything else                          → False
    Integer conversion:  if int() succeeds use it, else keep the string
                         (guards against malformed data)
    Everything else:     return as-is (a plain str)

    The result is then handed to Pydantic's ``model_validate()``, so the
    validator in :class:`~autosys.models.job.Job` will further normalise
    values like ``start_times`` and ``days_of_week``.
    """
    if attr in _BOOL_ATTRS:
        return raw.strip().lower() in ("1", "true", "yes")

    if attr in _INT_ATTRS:
        try:
            return int(raw.strip())
        except ValueError:
            return raw   # let Pydantic surface the error with full context

    return raw


# ===========================================================================
# Parser
# ===========================================================================

class JILParser:
    """
    Recursive-descent parser that converts a JIL token stream into
    :class:`JILOperation` objects.

    Usage
    -----
    ::

        from autosys.parser.jil_parser import JILParser

        ops = JILParser().parse_text(open("jobs.jil").read())
        for op in ops:
            print(op.op, op.job.job_name, op.job.job_type)
    """

    # ------------------------------------------------------------------
    # Public parse entry points
    # ------------------------------------------------------------------

    def parse_text(self, text: str) -> list[JILOperation]:
        """
        Tokenize *text* and parse it into a list of JIL operations.

        This is the primary API.  Combines :func:`Lexer.tokenize` and
        :meth:`parse` in one call.

        Parameters
        ----------
        text:
            Raw JIL content (may include ``/* */`` block comments).

        Returns
        -------
        list[JILOperation]
            One operation per ``insert_job`` / ``update_job`` / … stanza.

        Raises
        ------
        LexError
            If the lexer cannot tokenise a line.
        JILParseError
            If the token stream violates the JIL grammar.
        pydantic.ValidationError
            If parsed attributes fail Pydantic validation (e.g. a CMD job
            with no ``command``).
        """
        tokens = Lexer().tokenize(text)
        return self.parse(tokens)

    def parse(self, tokens: list[Token]) -> list[JILOperation]:
        """
        Parse a pre-tokenised list of :class:`Token` objects.

        Parameters
        ----------
        tokens:
            Output of :func:`autosys.parser.lexer.Lexer.tokenize`.

        Returns
        -------
        list[JILOperation]
        """
        self._tokens = tokens
        self._pos    = 0
        operations: list[JILOperation] = []

        while not self._at_end():
            if self._peek().kind == TokenKind.DIRECTIVE:
                op = self._parse_stanza()
                operations.append(op)
            else:
                # Should not happen if the lexer is correct.
                tok = self._consume()
                raise JILParseError(
                    f"Unexpected token {tok.kind.value}={tok.value!r} "
                    f"outside of a stanza",
                    tok.line,
                )

        return operations

    # ------------------------------------------------------------------
    # Stanza parser
    # ------------------------------------------------------------------

    def _parse_stanza(self) -> JILOperation:
        """
        Parse one complete job stanza from the current position.

        A stanza starts with a DIRECTIVE + JOB_NAME and is followed by
        zero or more ATTR_NAME + VALUE pairs until the next DIRECTIVE or EOF.

        Returns a :class:`JILOperation` with a fully validated Job model.
        """
        # --- Header ---
        directive_tok = self._consume(TokenKind.DIRECTIVE)
        name_tok      = self._consume(TokenKind.JOB_NAME)
        source_line   = directive_tok.line
        op_code       = _DIRECTIVE_TO_OP[directive_tok.value]

        raw_attrs: dict[str, str]    = {}
        coerced:   dict[str, Any]    = {}

        # machine_name key differs from job_name (machine stanzas use "machine_name")
        name_key = "machine_name" if op_code == "insert_machine" else "job_name"
        raw_attrs[name_key] = name_tok.value
        coerced[name_key]   = name_tok.value

        # --- Inline attributes on the header line ---
        while (
            self._peek().kind == TokenKind.ATTR_NAME
            and self._peek().line == source_line
        ):
            attr_tok = self._consume(TokenKind.ATTR_NAME)
            val_tok  = self._consume(TokenKind.VALUE)
            raw_attrs[attr_tok.value] = val_tok.value
            coerced[attr_tok.value]   = _coerce(attr_tok.value, val_tok.value)

        # --- Body attributes (each on its own line) ---
        while (
            not self._at_end()
            and self._peek().kind == TokenKind.ATTR_NAME
        ):
            attr_tok = self._consume(TokenKind.ATTR_NAME)
            val_tok  = self._consume(TokenKind.VALUE)
            raw_attrs[attr_tok.value] = val_tok.value
            coerced[attr_tok.value]   = _coerce(attr_tok.value, val_tok.value)

        # --- Build the right Pydantic model ---

        if op_code == "insert_machine":
            # Coerce machine integer attrs separately (port, max_load)
            for attr in _MACHINE_INT_ATTRS:
                if attr in coerced and isinstance(coerced[attr], str):
                    try:
                        coerced[attr] = int(coerced[attr])
                    except ValueError:
                        pass
            machine = MachineDef(**{k: v for k, v in coerced.items()
                                    if k in MachineDef.model_fields})
            return JILOperation(
                op          = op_code,
                job         = None,
                machine     = machine,
                raw_attrs   = raw_attrs,
                source_line = source_line,
            )

        # insert_job / override_job: full validation required.
        # delete_job / update_job: may omit required fields.
        if op_code in ("delete", "update") and "job_type" not in coerced:
            job: Job = CmdJob.model_construct(
                job_name=coerced.get("job_name", ""),
                job_type=coerced.get("job_type", "CMD"),
                **{k: v for k, v in coerced.items()
                   if k not in ("job_name", "job_type")},
            )
        else:
            job = parse_job(coerced)

        return JILOperation(
            op          = op_code,
            job         = job,
            raw_attrs   = raw_attrs,
            source_line = source_line,
        )

    # ------------------------------------------------------------------
    # Token navigation helpers
    # ------------------------------------------------------------------

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _at_end(self) -> bool:
        return self._tokens[self._pos].kind == TokenKind.EOF

    def _consume(self, expected: TokenKind | None = None) -> Token:
        tok = self._tokens[self._pos]
        if expected is not None and tok.kind != expected:
            raise JILParseError(
                f"Expected {expected.value} but got "
                f"{tok.kind.value}={tok.value!r}",
                tok.line,
            )
        self._pos += 1
        return tok


# ===========================================================================
# Convenience top-level functions
# ===========================================================================

def parse_jil(text: str) -> list[JILOperation]:
    """
    Parse JIL *text* and return a list of :class:`JILOperation`.

    Convenience wrapper around ``JILParser().parse_text(text)``.

    Examples
    --------
    >>> ops = parse_jil('''
    ... insert_job: my_box   job_type: BOX
    ... owner: svc_demo
    ... start_times: "06:00"
    ... days_of_week: mo,tu,we,th,fr
    ... ''')
    >>> len(ops)
    1
    >>> ops[0].op
    'insert'
    >>> ops[0].job.job_name
    'my_box'
    >>> ops[0].job.start_times
    ['06:00']
    """
    return JILParser().parse_text(text)


def parse_jil_file(path: str) -> list[JILOperation]:
    """
    Read a JIL file from *path* and parse it.

    Parameters
    ----------
    path:
        File system path to a ``.jil`` file.

    Returns
    -------
    list[JILOperation]
    """
    with open(path, encoding="utf-8") as fh:
        return parse_jil(fh.read())


def jobs_from_jil(text: str) -> list[Job]:
    """
    Return just the :class:`~autosys.models.job.Job` models from a JIL text.

    Convenience helper for tests and the CLI.

    >>> jobs = jobs_from_jil('''
    ... insert_job: cleanup   job_type: CMD
    ... command: /scripts/cleanup.sh
    ... machine: localhost
    ... ''')
    >>> jobs[0].job_name
    'cleanup'
    >>> type(jobs[0]).__name__
    'CmdJob'
    """
    return [op.job for op in parse_jil(text)]
