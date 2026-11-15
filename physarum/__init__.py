"""Physarum: a self-healing knowledge graph (slime-mould dynamics on HydraDB edges)."""

from .engine import Engine, Params  # noqa: F401


def __getattr__(name):
    if name == "Graph":
        from .sdk import Graph

        return Graph
    raise AttributeError(name)
