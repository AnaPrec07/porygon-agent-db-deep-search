"""Model helpers: structured output with bounded retries, and text extraction that drops reasoning blocks."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """Raised when a model does not return valid structured output after all retries."""


def structured(
    llm: Any,
    schema: type[T],
    system: str,
    human: str,
    *,
    retries: int = 2,
) -> T:
    """Invoke ``llm`` for an instance of ``schema``.

    On a parse or validation failure the model is asked again with the error message, up to ``retries``
    extra attempts. After that the function raises ``StructuredOutputError`` so the graph fails the run
    instead of continuing on malformed output.
    """
    runnable = llm.with_structured_output(schema, method="json_schema")
    messages: list[tuple[str, str]] = [("system", system), ("human", human)]
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            result = runnable.invoke(messages)
            return result if isinstance(result, schema) else schema.model_validate(result)
        except (ValidationError, ValueError) as error:
            last_error = error
            messages = [
                *messages,
                ("human", f"Your previous answer was invalid: {error}. Return a corrected answer."),
            ]
    raise StructuredOutputError(f"no valid {schema.__name__} after {retries + 1} attempts: {last_error}")


def text_of(message: Any) -> str:
    """Return the visible text of a model message.

    Content may be a string or a list of blocks; reasoning and other non-text blocks are dropped so private
    reasoning never reaches the report or the persisted state.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts).strip()
