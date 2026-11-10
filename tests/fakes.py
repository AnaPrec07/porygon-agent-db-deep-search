"""Shared test doubles. No network, no model endpoint."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


class FakeChat:
    """Stands in for a chat model. Structured results and text replies are served in order."""

    def __init__(self, structured_results: list[Any] | None = None, texts: list[str] | None = None):
        self._structured = list(structured_results or [])
        self._texts = list(texts or [])
        self.calls: list[tuple[Any, ...]] = []

    def with_structured_output(self, schema, method=None):
        chat = self

        class _Runnable:
            def invoke(self, messages):
                chat.calls.append(("structured", schema.__name__, messages))
                item = chat._structured.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        return _Runnable()

    def invoke(self, messages):
        self.calls.append(("text", messages))
        return AIMessage(content=self._texts.pop(0))
