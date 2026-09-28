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
update_job   → JILOperation(op="update",   job=<Job>)   # partial allowed
delete_job   → JILOperation(op="delete",   job=<Job>)   # partial — name only
delete_box   → JILOperation(op="delete_box", job=<Job>) # box + all its jobs
override_job → JILOperation(op="override", job=<Job>)
override_job: name delete → JILOperation(op="override_delete")
(every other sub-command keeps its own name as ``op``)

``NULL`` as an update/override value means "clear this attribute": those
attribute names are listed in ``JILOperation.null_attrs`` (and left out of
the model).  The ordered ``(attribute, value)`` pairs of every stanza —
including repeated keys — are kept in ``JILOperation.attr_pairs``.

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

from autosys.models.enums import JobStatus
from autosys.models.job import Job, parse_job, CmdJob
from autosys.models.machine import MachineDef
from autosys.parser.lexer import Lexer, LexError, Token, TokenKind, normalize_text


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
    attr_pairs:
        Every ``(attribute, value)`` pair in source order (repeated keys kept).
    null_attrs:
        Attributes given the value ``NULL`` on an update/override — the caller
        should clear them (see :func:`autosys.parser.jil_apply.apply_null_attrs`).
    coerced:
        The type-coerced attribute dict (NULL attributes excluded) — what an
        update/override should merge into the existing definition.
    start_line, end_line, raw_text:
        (tolerant parsing) the exact source span of the stanza.  The spans of
        all operations of a file are contiguous, and
        ``"".join(op.raw_text) == normalize_text(source)`` — no text is lost.
    issues:
        (tolerant parsing) ``{severity, code, message, line}`` problems found
        in the stanza.  ``op == "raw"`` marks a quarantined block.
    """
    op:          str                   # "insert" | "update" | "delete" | "override" | "insert_machine"
    job:         Job | None
    machine:     MachineDef | None     = None
    raw_attrs:   dict[str, str]        = field(default_factory=dict)
    source_line: int                   = 0
    attr_pairs:  list[tuple[str, str]] = field(default_factory=list)
    null_attrs:  list[str]             = field(default_factory=list)
    coerced:     dict[str, Any]        = field(default_factory=dict)
    # --- filled in by tolerant parsing (see JILParser.parse_text(tolerant=True)) ---
    start_line:  int                   = 0
    end_line:    int                   = 0
    raw_text:    str                   = ""
    issues:      list[dict]            = field(default_factory=list)
    raw_directive: str | None          = None   # for quarantined ("raw") blocks
    raw_name:    str | None            = None


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
    "job_terminator",
    "send_report",
    "date_conditions",
    "auto_delete",
    "continuous",
})

# Attributes whose raw numeric strings should become Python ints.
_INT_ATTRS: frozenset[str] = frozenset({
    "n_retrys",
    "max_exit_success",
    "max_run_alarm",
    "min_run_alarm",
    "term_run_time",
    "job_load",
    "max_load",
    "watch_file_min_size",
    "watch_interval",
    "command_timeout",
    "connection_retry",
    "connection_timeout",
})

# Map JIL directive keyword → short op code stored in JILOperation.op
_DIRECTIVE_TO_OP: dict[str, str] = {
    "insert_job":      "insert",
    "update_job":      "update",
    "delete_job":      "delete",
    "override_job":    "override",
    "rename_job":      "rename",
    "insert_machine":  "insert_machine",
    "update_machine":  "update_machine",
    "delete_machine":  "delete_machine",
    "insert_job_type": "insert_job_type",
    "update_job_type": "update_job_type",
    "delete_job_type": "delete_job_type",
    "insert_monbro":   "insert_monbro",
    "update_monbro":   "update_monbro",
    "delete_monbro":   "delete_monbro",
    "insert_blob":     "insert_blob",
    "delete_blob":     "delete_blob",
    "insert_glob":     "insert_glob",
    "delete_glob":     "delete_glob",
    "insert_xinst":    "insert_xinst",
    "update_xinst":    "update_xinst",
    "delete_xinst":    "delete_xinst",
    "insert_resource": "insert_resource",
    "update_resource": "update_resource",
    "delete_resource": "delete_resource",
    "insert_connectionprofile": "insert_connectionprofile",
    "delete_connectionprofile": "delete_connectionprofile",
    "insert_calendar": "insert_calendar",
    "update_calendar": "update_calendar",
    "delete_calendar": "delete_calendar",
    "delete_box":      "delete_box",
    "update_blob":     "update_blob",
    "update_glob":     "update_glob",
    "update_connectionprofile": "update_connectionprofile",
    "insert_view":     "insert_view",
    "modify_view":     "modify_view",
    "delete_view":     "delete_view",
    "insert_filter":   "insert_filter",
    "modify_filter":   "modify_filter",
    "delete_filter":   "delete_filter",
    "insert_alert_policy": "insert_alert_policy",
    "modify_alert_policy": "modify_alert_policy",
    "delete_alert_policy": "delete_alert_policy",
    "delete_user":     "delete_user",
}

# Operations whose model is a (possibly partial) Job.
_JOB_OPS = frozenset({
    "insert", "update", "delete", "override", "override_delete",
    "rename", "delete_box",
})

# ``status`` sets the INITIAL status of a job at insert time (vendor PDF,
# "status Attribute -- Set an Initial Status for a Job During Insertion").
_INITIAL_STATUSES = frozenset({
    "FAILURE", "INACTIVE", "ON_HOLD", "ON_ICE", "ON_NOEXEC", "SUCCESS", "TERMINATED",
})

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

    def parse_text(self, text: str, tolerant: bool = False) -> list[JILOperation]:
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
        if not tolerant:
            tokens = Lexer().tokenize(text)
            return self.parse(tokens)

        # Tolerant mode: never raises.  Every problem becomes an issue on the
        # operation it belongs to, unparseable blocks become ``op == "raw"``
        # operations, and each operation carries its exact source text.
        lexer = Lexer(tolerant=True)
        try:
            tokens = lexer.tokenize(text)
            ops = self.parse(tokens, tolerant=True)
        except Exception as exc:                 # absolute last resort
            ops = [JILOperation(op="raw", job=None, source_line=1)]
            lexer.diagnostics.append({
                "line": 1, "severity": "error", "code": "internal_error",
                "message": repr(exc), "raw": ""})
        self._attach_source(text, ops, lexer.diagnostics)
        return ops

    @staticmethod
    def _attach_source(text: str, ops: list[JILOperation], diagnostics: list[dict]) -> None:
        import bisect
        norm = normalize_text(text)
        lines = norm.split("\n")
        pieces = [l + "\n" for l in lines[:-1]] + [lines[-1]]
        total = len(pieces)
        if not ops:
            if norm.strip():
                ops.append(JILOperation(op="raw", job=None, source_line=1, issues=[{
                    "severity": "info", "code": "comments_only", "line": 1,
                    "message": "no JIL statements (comments / blank lines only)"}]))
            else:
                return
        ops.sort(key=lambda o: o.source_line)
        # Operations sharing a source line would get empty spans; keep one per
        # line (preferring a real operation over orphan-token noise) and carry
        # the others' issues over so nothing is hidden.
        merged: list[JILOperation] = []
        for op in ops:
            if merged and max(op.source_line, 1) == max(merged[-1].source_line, 1) and len(merged) > 0 \
                    and (op.op == "raw" or merged[-1].op == "raw"):
                prev = merged[-1]
                if prev.op == "raw" and op.op != "raw":
                    op.issues = prev.issues + op.issues
                    merged[-1] = op
                else:
                    prev.issues.extend(op.issues)
                continue
            merged.append(op)
        ops[:] = merged
        starts = [1] + [max(o.source_line, 1) for o in ops[1:]]
        for k, op in enumerate(ops):
            op.start_line = starts[k]
            op.end_line   = (starts[k + 1] - 1) if k + 1 < len(ops) else total
            op.raw_text   = "".join(pieces[op.start_line - 1:op.end_line])
        for d in diagnostics:
            k = max(bisect.bisect_right(starts, d["line"]) - 1, 0)
            ops[k].issues.append({
                "severity": d["severity"], "code": d["code"],
                "message": d["message"], "line": d["line"]})

    def parse(self, tokens: list[Token], tolerant: bool = False) -> list[JILOperation]:
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
        self._tokens   = tokens
        self._pos      = 0
        self._tolerant = tolerant
        operations: list[JILOperation] = []

        while not self._at_end():
            if self._peek().kind == TokenKind.RAW:
                operations.append(self._raw_op(self._consume()))
                # the quarantined header's own attribute lines belong to it
                while (not self._at_end() and self._peek().kind
                       in (TokenKind.ATTR_NAME, TokenKind.VALUE)):
                    self._consume()
            elif self._peek().kind == TokenKind.DIRECTIVE:
                if tolerant:
                    start = self._pos
                    try:
                        op = self._parse_stanza()
                    except Exception as exc:
                        op = self._salvage(start, exc)
                else:
                    op = self._parse_stanza()
                operations.append(op)
            elif tolerant:
                # orphan attribute tokens (cannot normally happen) — quarantine
                tok = self._consume()
                operations.append(JILOperation(
                    op="raw", job=None, source_line=tok.line, issues=[{
                        "severity": "error", "code": "orphan_token", "line": tok.line,
                        "message": f"unexpected {tok.kind.value} {tok.value!r}"}]))
                while (not self._at_end() and self._peek().kind
                       in (TokenKind.ATTR_NAME, TokenKind.VALUE)):
                    self._consume()
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
        Parse one complete stanza from the current position.

        A stanza starts with a DIRECTIVE + JOB_NAME and is followed by
        zero or more ATTR_NAME + VALUE pairs until the next DIRECTIVE or EOF.
        """
        # --- Header ---
        directive_tok = self._consume(TokenKind.DIRECTIVE)
        name_tok      = self._consume(TokenKind.JOB_NAME)
        source_line   = directive_tok.line
        op_code       = _DIRECTIVE_TO_OP[directive_tok.value]

        pairs: list[tuple[str, str]] = []

        # --- Attributes: inline on the header line, then one per body line ---
        while not self._at_end() and self._peek().kind == TokenKind.ATTR_NAME:
            attr_tok = self._consume(TokenKind.ATTR_NAME)
            val_tok  = self._consume(TokenKind.VALUE)
            pairs.append((attr_tok.value, val_tok.value))

        name_key = self._name_key(op_code)

        # ``override_job: name delete`` cancels an override.
        if op_code == "override" and any(k == "delete" for k, _ in pairs):
            op_code = "override_delete"
            pairs = [(k, v) for k, v in pairs if k != "delete"]

        tol = getattr(self, "_tolerant", False)
        issues: list[dict] = []
        raw_attrs: dict[str, str] = {name_key: name_tok.value}
        coerced:   dict[str, Any] = {name_key: name_tok.value}
        null_attrs: list[str]     = []
        for attr, value in pairs:
            raw_attrs[attr] = value                 # last one wins here;
            #                                        attr_pairs keeps them all
            if op_code in ("insert", "update", "override") and value.strip().upper() == "NULL":
                # NULL = "no value": on update/override it clears the attribute.
                if op_code != "insert" and attr not in null_attrs:
                    null_attrs.append(attr)
                coerced.pop(attr, None)
                continue
            if attr == "status" and op_code in _JOB_OPS:
                try:
                    coerced[attr] = self._initial_status(op_code, value, source_line)
                except JILParseError as exc:
                    if not tol:
                        raise
                    issues.append({"severity": "warning", "code": "invalid_status",
                                   "message": str(exc), "line": source_line})
                continue
            coerced[attr] = _coerce(attr, value)
        if null_attrs:
            for attr in null_attrs:
                coerced.pop(attr, None)

        common = dict(
            raw_attrs   = raw_attrs,
            source_line = source_line,
            attr_pairs  = [(name_key, name_tok.value)] + pairs,
            null_attrs  = null_attrs,
            coerced     = dict(coerced),
            issues      = issues,
        )
        if tol and op_code in _JOB_OPS or (tol and "machine" not in op_code):
            from autosys.models.job import job_name_warnings
            for w in job_name_warnings(name_tok.value):
                issues.append({"severity": "warning", "code": "job_name",
                               "message": w, "line": source_line})

        # --- Machines (real, and virtual machines / pools with members) ---
        if "machine" in op_code:
            members, top = self._machine_members(pairs)
            mcoerced = {name_key: name_tok.value}
            for attr, value in top:
                mcoerced[attr] = _coerce(attr, value)
            for attr in _MACHINE_INT_ATTRS:
                if attr in mcoerced and isinstance(mcoerced[attr], str):
                    try:
                        mcoerced[attr] = int(mcoerced[attr])
                    except ValueError:
                        pass
            if members and "members" in MachineDef.model_fields:
                mcoerced["members"] = members
                mcoerced.pop("machine", None)
            machine = self._build_machine(mcoerced, issues, source_line, tol)
            return JILOperation(op=op_code, job=None, machine=machine, **common)

        # --- Job operations ---
        if op_code in _JOB_OPS:
            job = self._build_job(op_code, coerced, issues if tol else None, source_line)
            return JILOperation(op=op_code, job=job, **common)

        # --- Everything else (monbro, glob, blob, resource, view, ...) ---
        return JILOperation(op=op_code, job=None, **common)

    # ------------------------------------------------------------------
    # Stanza helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _initial_status(op_code: str, value: str, line: int) -> int:
        """Validate the ``status`` attribute and return its status code."""
        if op_code != "insert":
            raise JILParseError(
                "the status attribute can only be used with insert_job "
                "(not while updating or overriding a job)", line)
        name = value.strip().upper()
        if name not in _INITIAL_STATUSES:
            raise JILParseError(
                f"invalid initial status {value!r}; valid values are "
                f"{', '.join(sorted(_INITIAL_STATUSES))}", line)
        return JobStatus[name].value

    @staticmethod
    def _name_key(op_code: str) -> str:
        """The attribute name under which a stanza's object name is stored."""
        if "machine" in op_code:
            return "machine_name"
        for needle, key in (
            ("resource", "resource_name"),
            ("calendar", "calendar_name"),
            ("glob", "global_name"),
            ("blob", "blob_name"),
            ("xinst", "xinst_name"),
            ("monbro", "monbro_name"),
            ("job_type", "job_type_name"),
            ("connectionprofile", "profile_name"),
            ("alert_policy", "policy_name"),
            ("view", "view_name"),
            ("filter", "filter_name"),
            ("user", "user_name"),
        ):
            if needle in op_code:
                return key
        return "job_name"

    @staticmethod
    def _machine_members(pairs: list[tuple[str, str]]):
        """
        Split a machine stanza's ordered pairs into pool members and the rest.

        A virtual machine / pool repeats ``machine:``; ``max_load`` and
        ``factor`` that follow a ``machine:`` belong to that member (before the
        first ``machine:`` they belong to the definition itself).
        """
        members: list[dict] = []
        top: list[tuple[str, str]] = []
        for attr, value in pairs:
            if attr == "machine":
                members.append({"machine": value, "max_load": None, "factor": None})
            elif members and attr in ("max_load", "factor"):
                try:
                    members[-1][attr] = int(value) if attr == "max_load" else float(value)
                except ValueError:
                    members[-1][attr] = None
            else:
                top.append((attr, value))
        return members, top

    @staticmethod
    def _partial_job(coerced: dict) -> Job:
        """A Job holding only what the stanza said (no required-field checks)."""
        return CmdJob.model_construct(
            job_name=coerced.get("job_name", ""),
            job_type=coerced.get("job_type", "CMD"),
            **{k: v for k, v in coerced.items() if k not in ("job_name", "job_type")},
        )

    @staticmethod
    def _build_machine(mcoerced: dict, issues: list[dict], line: int, tol: bool):
        """MachineDef from the coerced attributes; tolerant mode keeps going
        with whatever is valid and reports the rest (raw text is archived)."""
        fields = {k: v for k, v in mcoerced.items() if k in MachineDef.model_fields}
        if not tol:
            return MachineDef(**fields)
        from pydantic import ValidationError
        for _ in range(16):
            try:
                return MachineDef(**fields)
            except ValidationError as exc:
                bad = {e["loc"][0] for e in exc.errors()
                       if e.get("loc") and e["loc"][0] != "machine_name"}
                if not bad:
                    break
                for f in bad:
                    issues.append({"severity": "warning", "code": "invalid_value", "line": line,
                                   "message": f"invalid machine attribute {f}={fields.get(f)!r}; "
                                              "value kept only in the raw source"})
                    fields.pop(f, None)
        return MachineDef.model_construct(machine_name=mcoerced.get("machine_name", ""))

    def _build_job(self, op_code: str, coerced: dict,
                   issues: list[dict] | None = None, line: int = 0) -> Job:
        """
        insert_job needs a fully valid definition.  Every other job operation
        merges into (or removes) an existing definition, so it only has to be
        valid *if* it can be — otherwise it is kept as a partial model and the
        caller applies just the attributes that were given.
        """
        def _full(data: dict) -> Job:
            if issues is None:
                return parse_job(data)
            from autosys.models.job import parse_job_lenient
            job, warnings = parse_job_lenient(data)
            for w in warnings:
                issues.append({"severity": "warning", "code": "job_validation",
                               "message": w, "line": line})
            from autosys.models.job import attribute_job_type_warnings
            for w in attribute_job_type_warnings(job):
                issues.append({"severity": "warning", "code": "attribute_scope",
                               "message": w, "line": line})
            if op_code == "insert" and "job_type" not in data:
                issues.append({"severity": "warning", "code": "missing_job_type", "line": line,
                               "message": "insert_job has no job_type; defaulted to CMD"})
            ujt = (job.extra_attrs or {}).get("user_job_type")
            if ujt:
                import difflib
                from autosys.models.job import _VALID_JOB_TYPES
                known = [str(x).lower() for x in _VALID_JOB_TYPES]
                s = str(ujt).lower()
                if " " in s or difflib.get_close_matches(s, known, n=1, cutoff=0.75):
                    issues.append({"severity": "warning", "code": "job_type_typo", "line": line,
                                   "message": f"job_type {ujt!r} looks like a misspelt built-in type"})
            if ujt:
                issues.append({"severity": "info", "code": "user_job_type", "line": line,
                               "message": f"job_type {job.extra_attrs['user_job_type']!r} is not a "
                                          "built-in type; kept as a user-defined type"})
            return job

        if op_code == "insert":
            return _full(coerced)
        if op_code in ("delete", "delete_box", "rename", "override_delete"):
            return self._partial_job(coerced)
        # update / override
        if op_code == "update" and "job_type" not in coerced:
            return self._partial_job(coerced)
        try:
            return parse_job(coerced)
        except Exception:
            return self._partial_job(coerced)

    # ------------------------------------------------------------------
    # Tolerant-mode helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_op(tok: Token) -> JILOperation:
        """A quarantined block: unknown sub-command, stray text, unreadable header."""
        import re as _re
        m = _re.match(r'\s*([A-Za-z_][\w.\-]*)\s*:\s*("[^"]*"|\S+)?', tok.value)
        return JILOperation(
            op="raw", job=None, source_line=tok.line,
            raw_directive=m.group(1).lower() if m else None,
            raw_name=(m.group(2) or "").strip('"') or None if m else None,
        )

    def _salvage(self, start: int, exc: Exception) -> JILOperation:
        """A stanza whose parse failed unexpectedly becomes a quarantined block."""
        self._pos = start
        d = self._consume()
        name = None
        if self._peek().kind == TokenKind.JOB_NAME:
            name = self._consume().value
        while not self._at_end() and self._peek().kind in (TokenKind.ATTR_NAME, TokenKind.VALUE):
            self._consume()
        return JILOperation(
            op="raw", job=None, source_line=d.line, raw_directive=d.value, raw_name=name,
            issues=[{"severity": "error", "code": "unparseable_stanza",
                     "message": f"{type(exc).__name__}: {exc}", "line": d.line}])

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
    with open(path, encoding="utf-8-sig") as fh:
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
