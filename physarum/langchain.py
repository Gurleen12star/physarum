"""LangChain integration (optional): Physarum as a retriever and as agent tools.

pip install "physarum-graph[langchain]"
from physarum import Graph
from physarum.langchain import PhysarumRetriever, physarum_tools

g = Graph.from_csv("facts.csv")
tools = physarum_tools(g)            # ask_graph, graph_feedback, graph_audit → bind to any LangChain agent
retriever = PhysarumRetriever(graph=g, relations=["reports_to", "leads"])
retriever.invoke("Bob")              # → Documents: the answer path as facts, with confidence in metadata
"""

from __future__ import annotations

import itertools
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import tool

from .sdk import Graph


class PhysarumRetriever(BaseRetriever):
    """Retrieve the trusted path of facts from `subject` along `relations` (the query is the subject)."""

    graph: Any
    relations: list[str]
    last_answer: Any = None

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        a = self.graph.ask(query.strip(), *self.relations)
        self.last_answer = a
        if a.answer is None:
            return []
        facts = [f"{s} {r} {o}" for s, r, o in zip(a.path, self.relations, a.path[1:])]
        return [
            Document(
                page_content="; ".join(facts),
                metadata=dict(answer=a.answer, confidence=a.confidence, unsure=a.unsure, source="physarum"),
            )
        ]

    def feedback(self, correct: bool):
        """Report whether the last retrieved answer was right."""
        if self.last_answer is not None:
            self.last_answer = self.graph.feedback(self.last_answer, correct)
        return self.last_answer


def physarum_tools(graph: Graph):
    answers: dict[int, Any] = {}
    ids = itertools.count(1)

    @tool
    def ask_graph(subject: str, relations: list[str]) -> dict:
        """Answer a multi-hop question from the knowledge graph by following `relations` from `subject`.
        Returns answer, path, confidence, unsure and an answer_id for graph_feedback."""
        a = graph.ask(subject, *relations)
        aid = next(ids)
        answers[aid] = a
        return dict(answer_id=aid, answer=a.answer, path=a.path, confidence=a.confidence, unsure=a.unsure)

    @tool
    def graph_feedback(answer_id: int, correct: bool) -> dict:
        """Tell the graph whether an earlier answer was right; it re-weights its facts immediately."""
        a = answers.get(answer_id)
        if a is None:
            return dict(error="unknown answer_id")
        b = graph.feedback(a, correct)
        return dict(answer=b.answer, confidence=b.confidence)

    @tool
    def graph_audit() -> list:
        """List facts the graph has stopped trusting (likely stale or wrong)."""
        return [f"{s} {r} {o}" for s, r, o, *_ in graph.audit()]

    return [ask_graph, graph_feedback, graph_audit]
