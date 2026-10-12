"""autosys.agent — System Agent: subprocess dispatch, TCP server, remote routing.

The real-execution modules (runner, dispatch, server) are imported lazily:
a DRY_RUN_ONLY image build deletes them, and everything else here (protocol,
remote heartbeat/status) must still import.
"""

from autosys.agent.remote   import RemoteDispatch
from autosys.agent.protocol import send_message

_LAZY = {
    "LocalJobRunner": "autosys.agent.runner",
    "AgentDispatch":  "autosys.agent.dispatch",
    "AgentServer":    "autosys.agent.server",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LocalJobRunner", "AgentDispatch", "AgentServer",
    "RemoteDispatch", "send_message",
]
