"""
autosys.parser — JIL parsing pipeline.

Import surface
--------------
    from autosys.parser import parse_jil, parse_jil_file, jobs_from_jil
    from autosys.parser import parse_condition, evaluate, condition_to_str
    from autosys.parser import substitute, build_builtins
    from autosys.parser import tokenize, Lexer, Token, TokenKind
"""

from autosys.parser.lexer import (
    Lexer,
    Token,
    TokenKind,
    LexError,
    tokenize,
    strip_comments,
)
from autosys.parser.jil_parser import (
    JILParser,
    JILOperation,
    JILParseError,
    parse_jil,
    parse_jil_file,
    jobs_from_jil,
)
from autosys.parser.condition_parser import (
    ConditionNode,
    JobCondNode,
    ValueCondNode,
    AndNode,
    OrNode,
    NotNode,
    ConditionSyntaxError,
    parse_condition,
    evaluate,
    condition_to_str,
    list_job_dependencies,
)
from autosys.parser.jil_writer import (
    job_to_jil,
    jobs_to_jil,
)
from autosys.parser.variable_sub import (
    UndefinedVariableError,
    build_builtins,
    substitute,
    substitute_job_attrs,
    list_variables,
)

__all__ = [
    # Lexer
    "Lexer", "Token", "TokenKind", "LexError", "tokenize", "strip_comments",
    # Parser
    "JILParser", "JILOperation", "JILParseError",
    "parse_jil", "parse_jil_file", "jobs_from_jil",
    # Condition
    "ConditionNode", "JobCondNode", "ValueCondNode",
    "AndNode", "OrNode", "NotNode", "ConditionSyntaxError",
    "parse_condition", "evaluate", "condition_to_str", "list_job_dependencies",
    # JIL writer
    "job_to_jil", "jobs_to_jil",
    # Variable substitution
    "UndefinedVariableError", "build_builtins",
    "substitute", "substitute_job_attrs", "list_variables",
]
