"""
LLM service module.
Handles communication with the OpenAI-compatible LLM API.
"""

import logging
from typing import AsyncIterator

from config import SYSTEM_PROMPT
from services.model_router import ModelRouter

logger = logging.getLogger(__name__)


router = ModelRouter()
client = router.openai_client

# Keywords in model IDs that indicate vision support
_VISION_MODEL_KEYWORDS = ("gpt-4o", "gpt-4-vision", "gpt-4-turbo", "vision", "gemini", "claude-3")


async def list_models() -> list[str]:
    """Return a sorted list of available model IDs."""
    try:
        models = await client.models.list()
        return sorted(m.id for m in models.data)
    except Exception as e:
        logger.error("Failed to list models: %s", e)
        return []


def model_supports_vision(model_id: str) -> bool:
    """Heuristically check whether a model ID supports vision input."""
    lower = model_id.lower()
    return any(kw in lower for kw in _VISION_MODEL_KEYWORDS)


async def check_model_capabilities(model_id: str) -> dict:
    """
    Return a dict describing known capabilities of a model.

    Checks are heuristic (based on model ID) since the OpenAI models API
    does not expose a structured capabilities field.
    """
    available = await list_models()
    exists = model_id in available
    return {
        "model": model_id,
        "available": exists,
        "vision": model_supports_vision(model_id),
        "note": "Capability detection is heuristic based on model ID naming conventions.",
    }


async def get_llm_response(
    conversation_history: list[dict[str, str]],
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_executor=None,
) -> str:
    """
    Send conversation history to the LLM and return the assistant's response.

    When `tools` and `tool_executor` are provided the function runs an agentic
    loop: it calls the model, executes any requested tool calls, appends the
    results, and repeats until the model produces a plain text reply or the
    safety limit of 10 iterations is reached.

    Args:
        conversation_history: List of message dicts with 'role' and 'content'.
        model: Model ID to use. Defaults to chat model from ModelRouter.
        tools: Optional list of OpenAI tool schemas.
        tool_executor: Async callable ``(name, arguments_json) -> result_str``.

    Returns:
        The assistant's reply as a string.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(conversation_history)
    model_to_use = model or router.chat_model()
    routed = router.resolve(model_to_use)

    create_kwargs: dict = {"model": routed.model, "messages": messages}
    if tools:
        create_kwargs["tools"] = tools
        create_kwargs["tool_choice"] = "auto"

    # Disable Qwen3 / DeepSeek-R1 <think> chains for local Ollama models
    # (they add 30–60s of latency on T4 for simple chat).
    if model_to_use.startswith("ollama/"):
        create_kwargs["extra_body"] = {"reasoning_effort": "none"}

    max_iterations = 10
    try:
        for _ in range(max_iterations):
            response = await routed.client.chat.completions.create(**create_kwargs)
            msg = response.choices[0].message

            if not msg.tool_calls:
                # Plain text reply — we are done
                return msg.content or "I received an empty response. Please try again."

            # Append the assistant's tool-call message to the running history
            messages.append(msg.to_dict())

            # Execute each requested tool and collect results
            for tc in msg.tool_calls:
                result = await tool_executor(tc.function.name, tc.function.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            create_kwargs["messages"] = messages

        return "Sorry, I reached the tool call limit without a final answer."
    except Exception as e:
        logger.error("LLM API error: %s", e)
        return f"Sorry, I encountered an error while processing your request: {e}"


async def get_llm_response_stream(
    conversation_history: list[dict[str, str]],
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_executor=None,
    reasoning: bool = False,
) -> AsyncIterator[str]:
    """Stream LLM response tokens.

    Yields partial text chunks as they arrive.  When tool calls are needed
    the function handles them internally (non-streamed) and then streams
    the final text reply.

    Falls back to ``get_llm_response`` and yields the full reply in one
    chunk if anything goes wrong with streaming.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(conversation_history)
    model_to_use = model or router.chat_model()
    routed = router.resolve(model_to_use)

    create_kwargs: dict = {"model": routed.model, "messages": messages}
    if tools:
        create_kwargs["tools"] = tools
        create_kwargs["tool_choice"] = "auto"

    # Disable Qwen3 / DeepSeek-R1 <think> chains for local Ollama models.
    if model_to_use.startswith("ollama/") and not reasoning:
        create_kwargs["extra_body"] = {"reasoning_effort": "none"}

    max_iterations = 10
    try:
        # Fast path: no tools requested → skip the non-streamed tool-check
        # call and stream directly. Saves a full round-trip + generation
        # (~halves latency for raw chat on local Ollama).
        if not tools:
            stream_kwargs = dict(create_kwargs)
            stream_kwargs["stream"] = True
            stream = await routed.client.chat.completions.create(**stream_kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
            return

        final_content: str | None = None
        for _ in range(max_iterations):
            # First, do a non-streamed call to check for tool calls
            response = await routed.client.chat.completions.create(**create_kwargs)
            msg = response.choices[0].message

            if not msg.tool_calls:
                # No more tools — the assistant already produced the final
                # answer in this same response. Reuse it instead of paying
                # for another full LLM round-trip just to re-stream it.
                final_content = msg.content or ""
                break

            # Handle tool calls (non-streamed)
            messages.append(msg.to_dict())
            for tc in msg.tool_calls:
                result = await tool_executor(tc.function.name, tc.function.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            create_kwargs["messages"] = messages
        else:
            yield "Sorry, I reached the tool call limit without a final answer."
            return

        # Fast path: assistant gave us the final text in the same response
        # that had no tool calls — yield it directly (saves ~one full LLM
        # round-trip ≈ 10–15s with current model).
        if final_content:
            yield final_content
            return

        # Fallback (rare): assistant returned an empty content + no tool
        # calls. Stream a follow-up to coax a real answer.
        stream_kwargs = {k: v for k, v in create_kwargs.items() if k != "tools"}
        stream_kwargs.pop("tool_choice", None)
        stream_kwargs["stream"] = True
        stream = await routed.client.chat.completions.create(**stream_kwargs)

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    except Exception as e:
        logger.error("LLM streaming error, falling back: %s", e)
        # Fallback: get full response
        try:
            full = await get_llm_response(conversation_history, model, tools, tool_executor)
            yield full
        except Exception as e2:
            yield f"Sorry, I encountered an error: {e2}"
