"""
AutoSys condition expression parser.

What AutoSys conditions look like
-----------------------------------
The ``condition`` attribute of a JIL job holds a boolean expression that the
Scheduler ACE evaluates *every time* a relevant job changes state.  If the
expression evaluates to True AND the job's scheduling window is open, the
Scheduler places a STARTJOB event on the queue.

Grammar (EBNF)
--------------
condition   := or_expr
or_expr     := and_expr  ('|' and_expr)*
and_expr    := primary   ('&' primary)*
primary     := '(' condition ')'
             | job_func
             | value_cond
job_func    := func_name '(' job_ref ')'
func_name   := 'success' | 'failure' | 'done' | 'notrunning'
             | 'terminated' | 'activated'
value_cond  := 'value' '(' global_name ')' ('=' | '!=') '"' string '"'
job_ref     := identifier          # may contain letters, digits, _, ., -, :
global_name := identifier          # typically UPPERCASE by convention

Operator precedence (highest to lowest)
-----------------------------------------
1. Parentheses   (...)
2. AND           &
3. OR            |

So ``success(a) | success(b) & success(c)``
   = ``success(a) | (success(b) & success(c))``

Public API
----------
parse_condition(expr: str)  -> ConditionNode   — build AST
evaluate(node, statuses, globals) -> bool      — walk AST against live data
condition_to_str(node)      -> str             — pretty-print AST back to string

AST node types
--------------
ConditionNode   — abstract base
  JobCondNode   — success(job), failure(job), …
  ValueCondNode — value(GLOBAL) = "x"
  AndNode       — left & right
  OrNode        — left | right
  NotNode       — reserved for future !expr support
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ===========================================================================
# AST node types
# ===========================================================================

@dataclass
class ConditionNode:
    """Abstract base for all condition AST nodes."""


@dataclass
class JobCondNode(ConditionNode):
    """
    A function predicate on a specific job's current status.

    func      What this predicate tests
    --------  -----------------------------------------------------------------
    success   status == SUCCESS
    failure   status == FAILURE
    done      status in {SUCCESS, FAILURE}   (completed either way)
    notrunning status not in {STARTING, RUNNING, RESTART}
    terminated status == TERMINATED
    activated  status == ACTIVATED  (BOX jobs only)
    """
    func: str       # "success" | "failure" | "done" | "notrunning" | "terminated" | "activated"
    job_name: str   # the job whose status is tested
    instance: Optional[str] = None
    look_back: Optional[int] = None


@dataclass
class ExitCodeCondNode(ConditionNode):
    """
    Compares a job's last exit code to an integer.

    Real AutoSys example:
        condition: exitcode(extract_sales) = 0
    """
    job_name: str
    op: str            # "=" or "!="
    expected: int


@dataclass
class ValueCondNode(ConditionNode):
    """
    Compares a global variable's current value to a literal string.

    Real AutoSys example:
        condition: value(BATCH_DATE) = "20260625"
    """
    global_name: str   # UPPERCASE by convention
    op: str            # "=" or "!="
    expected: str      # the literal string (already stripped of quotes)


@dataclass
class AndNode(ConditionNode):
    """Left & Right — both sub-conditions must be True."""
    left:  ConditionNode
    right: ConditionNode


@dataclass
class OrNode(ConditionNode):
    """Left | Right — at least one sub-condition must be True."""
    left:  ConditionNode
    right: ConditionNode


@dataclass
class NotNode(ConditionNode):
    """Reserved for future !expr syntax (not in standard AutoSys but useful)."""
    operand: ConditionNode


# ---------------------------------------------------------------------------
# Status-check functions (used by evaluate())
# ---------------------------------------------------------------------------

from autosys.models.enums import JobStatus

_FUNC_CHECKS: dict[str, callable] = {
    # Full names
    "success":    lambda s: s == JobStatus.SUCCESS,
    "failure":    lambda s: s == JobStatus.FAILURE,
    "done":       lambda s: s in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.TERMINATED),
    "notrunning": lambda s: s not in (JobStatus.STARTING, JobStatus.RUNNING, JobStatus.RESTART),
    "terminated": lambda s: s == JobStatus.TERMINATED,
    "activated":  lambda s: s == JobStatus.ACTIVATED,
    # Real AutoSys single-letter shorthands (used in JIL condition: attributes)
    "s":  lambda s: s == JobStatus.SUCCESS,
    "f":  lambda s: s == JobStatus.FAILURE,
    "d":  lambda s: s in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.TERMINATED),
    "n":  lambda s: s not in (JobStatus.STARTING, JobStatus.RUNNING, JobStatus.RESTART),
    "t":  lambda s: s == JobStatus.TERMINATED,
}

_VALID_FUNCS = frozenset(_FUNC_CHECKS)


# ===========================================================================
# Condition tokenizer
# ===========================================================================

class _CondTokenKind:
    FUNC    = "FUNC"      # success, failure, done, …
    VALUE   = "VALUE"     # the keyword "value"
    EXITCODE = "EXITCODE" # the keyword "exitcode"
    LPAREN  = "LPAREN"    # (
    RPAREN  = "RPAREN"    # )
    AND     = "AND"       # &
    OR      = "OR"        # |
    NOT     = "NOT"       # ! (reserved)
    EQ      = "EQ"        # =
    NEQ     = "NEQ"       # !=
    QSTRING = "QSTRING"   # "literal" (quotes stripped in .value)
    NUMBER  = "NUMBER"    # integer literal
    IDENT   = "IDENT"     # job_name or global_name
    COMMA   = "COMMA"     # ,
    EOF     = "EOF"


@dataclass
class _CondToken:
    kind:  str
    value: str
    pos:   int    # character offset in the original expression


# Master regex — order matters (longer/more-specific patterns first)
_COND_RE = re.compile(
    r'(?P<WS>\s+)'
    r'|(?P<NEQ>!=)'
    r'|(?P<EQ>=)'
    r'|(?P<AND>&)'
    r'|(?P<OR>\|)'
    r'|(?P<LPAREN>\()'
    r'|(?P<RPAREN>\))'
    r'|(?P<NOT>!)'
    r'|(?P<QSTRING>"(?:[^"\\]|\\.)*")'
    r'|(?P<NUMBER>-?\d+)'
    r'|(?P<COMMA>,)'
    r'|(?P<IDENT>[A-Za-z0-9_][A-Za-z0-9_.:%^-]*)'
)


def _tokenize_condition(expr: str) -> list[_CondToken]:
    """
    Convert a condition expression string to a flat list of tokens.

    Whitespace is consumed and discarded.  IDENT tokens whose value matches
    a function name (success, failure, …) are reclassified as FUNC; the
    keyword "value" becomes VALUE.
    """
    tokens: list[_CondToken] = []
    pos = 0
    while pos < len(expr):
        m = _COND_RE.match(expr, pos)
        if m is None:
            raise ConditionSyntaxError(
                f"Unexpected character {expr[pos]!r} at position {pos}",
                expr, pos,
            )
        pos = m.end()
        kind = m.lastgroup
        raw  = m.group()

        if kind == "WS":
            continue   # skip whitespace
        if kind == "IDENT":
            val = raw
            if val in _VALID_FUNCS:
                kind = _CondTokenKind.FUNC
            elif val == "value":
                kind = _CondTokenKind.VALUE
            elif val == "exitcode":
                kind = _CondTokenKind.EXITCODE
        elif kind == "QSTRING":
            raw = raw[1:-1]   # strip surrounding double-quotes from the token value
        
        tokens.append(_CondToken(kind=kind, value=raw, pos=m.start()))

    tokens.append(_CondToken(kind=_CondTokenKind.EOF, value="", pos=len(expr)))
    return tokens


# ===========================================================================
# Parse errors
# ===========================================================================

class ConditionSyntaxError(Exception):
    """Raised when the condition expression cannot be parsed."""
    def __init__(self, message: str, expr: str, pos: int) -> None:
        pointer = " " * pos + "^"
        super().__init__(f"{message}\n  {expr}\n  {pointer}")
        self.expr = expr
        self.pos  = pos


# ===========================================================================
# Recursive-descent parser
# ===========================================================================

class _CondParser:
    """
    Recursive-descent parser for the AutoSys condition mini-language.

    Instantiate with the token list from _tokenize_condition(), then call
    .parse() to get the root ConditionNode.
    """

    def __init__(self, tokens: list[_CondToken], expr: str) -> None:
        self._tokens = tokens
        self._pos    = 0
        self._expr   = expr   # kept for error messages only

    # ------------------------------------------------------------------
    # Token navigation helpers
    # ------------------------------------------------------------------

    def _peek(self) -> _CondToken:
        return self._tokens[self._pos]

    def _consume(self, expected_kind: Optional[str] = None) -> _CondToken:
        tok = self._tokens[self._pos]
        if expected_kind and tok.kind != expected_kind:
            raise ConditionSyntaxError(
                f"Expected {expected_kind!r} but got {tok.kind!r} ({tok.value!r})",
                self._expr, tok.pos,
            )
        self._pos += 1
        return tok

    def _at_end(self) -> bool:
        return self._peek().kind == _CondTokenKind.EOF

    # ------------------------------------------------------------------
    # Grammar productions
    # ------------------------------------------------------------------

    def parse(self) -> ConditionNode:
        """Entry point — parse the full condition expression."""
        node = self._parse_or()
        if not self._at_end():
            tok = self._peek()
            raise ConditionSyntaxError(
                f"Unexpected token {tok.value!r} after condition end",
                self._expr, tok.pos,
            )
        return node

    def _parse_or(self) -> ConditionNode:
        """or_expr := and_expr ('|' and_expr)*"""
        left = self._parse_and()
        while self._peek().kind == _CondTokenKind.OR:
            self._consume(_CondTokenKind.OR)
            right = self._parse_and()
            left  = OrNode(left=left, right=right)
        return left

    def _parse_and(self) -> ConditionNode:
        """and_expr := primary ('&' primary)*"""
        left = self._parse_primary()
        while self._peek().kind == _CondTokenKind.AND:
            self._consume(_CondTokenKind.AND)
            right = self._parse_primary()
            left  = AndNode(left=left, right=right)
        return left

    def _parse_primary(self) -> ConditionNode:
        """primary := '(' condition ')' | job_func | value_comparison"""
        tok = self._peek()

        if tok.kind == _CondTokenKind.LPAREN:
            return self._parse_grouped()

        if tok.kind == _CondTokenKind.FUNC:
            return self._parse_job_func()

        if tok.kind == _CondTokenKind.VALUE:
            return self._parse_value_cond()

        if tok.kind == _CondTokenKind.EXITCODE:
            return self._parse_exitcode_cond()

        if tok.kind == _CondTokenKind.NOT:
            return self._parse_not()

        raise ConditionSyntaxError(
            f"Expected condition term (success/failure/… or '('), "
            f"got {tok.kind!r} ({tok.value!r})",
            self._expr, tok.pos,
        )

    def _parse_grouped(self) -> ConditionNode:
        """'(' condition ')'"""
        self._consume(_CondTokenKind.LPAREN)
        node = self._parse_or()
        self._consume(_CondTokenKind.RPAREN)
        return node

    def _parse_job_func(self) -> JobCondNode:
        """
        job_func := func_name '(' job_ref [',' NUMBER] ')'
        e.g.  success(extract_sales)
              success(job1^PRD, 12)
        """
        func_tok = self._consume(_CondTokenKind.FUNC)
        self._consume(_CondTokenKind.LPAREN)
        name_tok = self._consume(_CondTokenKind.IDENT)
        
        # parse instance if present in IDENT
        job_name = name_tok.value
        instance = None
        if "^" in job_name:
            job_name, instance = job_name.split("^", 1)

        look_back = None
        if self._peek().kind == _CondTokenKind.COMMA:
            self._consume(_CondTokenKind.COMMA)
            num_tok = self._consume(_CondTokenKind.NUMBER)
            look_back = int(num_tok.value)

        self._consume(_CondTokenKind.RPAREN)
        return JobCondNode(func=func_tok.value, job_name=job_name, instance=instance, look_back=look_back)

    def _parse_value_cond(self) -> ValueCondNode:
        """
        value_comparison := 'value' '(' global_name ')' ('=' | '!=') '"' string '"'
        e.g.  value(BATCH_DATE) = "20260625"
              value(ENV) != "PROD"
        """
        self._consume(_CondTokenKind.VALUE)
        self._consume(_CondTokenKind.LPAREN)
        name_tok = self._consume(_CondTokenKind.IDENT)
        self._consume(_CondTokenKind.RPAREN)

        op_tok = self._peek()
        if op_tok.kind == _CondTokenKind.EQ:
            self._consume(_CondTokenKind.EQ)
            op = "="
        elif op_tok.kind == _CondTokenKind.NEQ:
            self._consume(_CondTokenKind.NEQ)
            op = "!="
        else:
            raise ConditionSyntaxError(
                f"Expected '=' or '!=' after value(...), got {op_tok.value!r}",
                self._expr, op_tok.pos,
            )

        val_tok = self._consume(_CondTokenKind.QSTRING)
        return ValueCondNode(
            global_name=name_tok.value.upper(),
            op=op,
            expected=val_tok.value,  # already unquoted by tokenizer
        )

    def _parse_exitcode_cond(self) -> ExitCodeCondNode:
        """
        exitcode_comparison := 'exitcode' '(' job_name ')' ('=' | '!=') NUMBER
        e.g.  exitcode(extract_sales) = 0
              exitcode(job_a) != 1
        """
        self._consume(_CondTokenKind.EXITCODE)
        self._consume(_CondTokenKind.LPAREN)
        name_tok = self._consume(_CondTokenKind.IDENT)
        self._consume(_CondTokenKind.RPAREN)

        op_tok = self._peek()
        if op_tok.kind == _CondTokenKind.EQ:
            self._consume(_CondTokenKind.EQ)
            op = "="
        elif op_tok.kind == _CondTokenKind.NEQ:
            self._consume(_CondTokenKind.NEQ)
            op = "!="
        else:
            raise ConditionSyntaxError(
                f"Expected '=' or '!=' after exitcode(...), got {op_tok.value!r}",
                self._expr, op_tok.pos,
            )

        val_tok = self._consume(_CondTokenKind.NUMBER)
        return ExitCodeCondNode(
            job_name=name_tok.value,
            op=op,
            expected=int(val_tok.value),
        )

    def _parse_not(self) -> NotNode:
        """!expr  (reserved; real AutoSys doesn't support this)"""
        self._consume(_CondTokenKind.NOT)
        operand = self._parse_primary()
        return NotNode(operand=operand)


# ===========================================================================
# Public entry points
# ===========================================================================

def parse_condition(expr: str) -> ConditionNode:
    """
    Parse an AutoSys condition expression into an AST.

    Parameters
    ----------
    expr:
        The raw condition string from a JIL ``condition:`` attribute, e.g.
        ``"success(check_source_ready)"``
        ``"success(generate_report) & success(load_to_warehouse)"``
        ``"success(a) & (success(b) | failure(c))"``
        ``"value(BATCH_DATE) = \\"20260625\\""``

    Returns
    -------
    ConditionNode
        Root of the condition AST.

    Raises
    ------
    ConditionSyntaxError
        If the expression cannot be parsed.
    """
    tokens = _tokenize_condition(expr.strip())
    return _CondParser(tokens, expr).parse()


def evaluate(
    node: ConditionNode,
    job_statuses: dict[str, str],
    global_vars: Optional[dict[str, str]] = None,
    job_exitcodes: Optional[dict[str, int]] = None,
) -> bool:
    """
    Walk the condition AST and return True if all conditions are satisfied.

    Called by the Event Processor (Phase 4) whenever a relevant job changes
    state, to decide whether to enqueue a STARTJOB event.

    Parameters
    ----------
    node:
        Root AST node returned by ``parse_condition()``.
    job_statuses:
        Mapping of job_name → current status string (e.g. "SUCCESS").
        Jobs not in the dict are treated as INACTIVE.
    global_vars:
        Mapping of global variable name → current value.
        Names are normalised to UPPERCASE before lookup.
    job_exitcodes:
        Mapping of job_name → current exit code.
        If an exitcode condition references a job with no exitcode, it will fail to match.

    Examples
    --------
    >>> node = parse_condition("success(extract_sales)")
    >>> evaluate(node, {"extract_sales": "SUCCESS"})
    True
    >>> evaluate(node, {"extract_sales": "RUNNING"})
    False

    >>> and_node = parse_condition("success(a) & success(b)")
    >>> evaluate(and_node, {"a": "SUCCESS", "b": "SUCCESS"})
    True
    >>> evaluate(and_node, {"a": "SUCCESS", "b": "FAILURE"})
    False
    """
    _global_vars: dict[str, str] = {
        k.upper(): v for k, v in (global_vars or {}).items()
    }

    def _eval(n: ConditionNode) -> bool:
        if isinstance(n, JobCondNode):
            raw = job_statuses.get(n.job_name, "INACTIVE")
            # Normalise to a JobStatus enum so _FUNC_CHECKS lambdas work
            # regardless of whether the snapshot contains ints or strings.
            if isinstance(raw, int):
                try:
                    status: JobStatus = JobStatus(raw)
                except ValueError:
                    status = JobStatus.INACTIVE
            else:
                try:
                    status = JobStatus[str(raw).upper()]
                except KeyError:
                    status = JobStatus.INACTIVE
            checker = _FUNC_CHECKS.get(n.func)
            if checker is None:
                raise ValueError(f"Unknown condition function: {n.func!r}")
            return checker(status)

        if isinstance(n, ValueCondNode):
            actual = _global_vars.get(n.global_name, "")
            return (actual == n.expected) if n.op == "=" else (actual != n.expected)

        if isinstance(n, ExitCodeCondNode):
            actual_code = (job_exitcodes or {}).get(n.job_name)
            if actual_code is None:
                return False
            return (actual_code == n.expected) if n.op == "=" else (actual_code != n.expected)

        if isinstance(n, AndNode):
            # Short-circuit: if left is False, don't evaluate right
            return _eval(n.left) and _eval(n.right)

        if isinstance(n, OrNode):
            # Short-circuit: if left is True, don't evaluate right
            return _eval(n.left) or _eval(n.right)

        if isinstance(n, NotNode):
            return not _eval(n.operand)

        raise TypeError(f"Unknown ConditionNode type: {type(n)!r}")

    return _eval(node)


def condition_to_str(node: ConditionNode) -> str:
    """
    Render a condition AST back to a human-readable expression string.

    Useful for logging and the WCC dependency graph view (Phase 10).

    Examples
    --------
    >>> node = parse_condition("success(a) & (success(b) | failure(c))")
    >>> condition_to_str(node)
    'success(a) & (success(b) | failure(c))'
    """
    if isinstance(node, JobCondNode):
        return f"{node.func}({node.job_name})"

    if isinstance(node, ValueCondNode):
        return f'value({node.global_name}) {node.op} "{node.expected}"'

    if isinstance(node, ExitCodeCondNode):
        return f'exitcode({node.job_name}) {node.op} {node.expected}'

    if isinstance(node, AndNode):
        left  = condition_to_str(node.left)
        right = condition_to_str(node.right)
        # Wrap OrNode children in parens to preserve precedence visually
        if isinstance(node.left, OrNode):
            left = f"({left})"
        if isinstance(node.right, OrNode):
            right = f"({right})"
        return f"{left} & {right}"

    if isinstance(node, OrNode):
        left  = condition_to_str(node.left)
        right = condition_to_str(node.right)
        return f"{left} | {right}"

    if isinstance(node, NotNode):
        return f"!{condition_to_str(node.operand)}"

    raise TypeError(f"Unknown ConditionNode type: {type(node)!r}")


def list_job_dependencies(node: ConditionNode) -> list[str]:
    """
    Return a flat list of job names referenced in the condition.

    Duplicates are removed; order is depth-first left-to-right.
    Used by the WCC flow graph builder to draw dependency edges.

    Examples
    --------
    >>> node = parse_condition("success(a) & (success(b) | success(a))")
    >>> list_job_dependencies(node)
    ['a', 'b']
    """
    seen: dict[str, None] = {}

    def _collect(n: ConditionNode) -> None:
        if isinstance(n, JobCondNode):
            seen[n.job_name] = None
        elif isinstance(n, (AndNode, OrNode)):
            _collect(n.left)
            _collect(n.right)
        elif isinstance(n, NotNode):
            _collect(n.operand)
        elif isinstance(n, ExitCodeCondNode):
            seen[n.job_name] = None
        # ValueCondNode references globals, not jobs — skip

    _collect(node)
    return list(seen)
