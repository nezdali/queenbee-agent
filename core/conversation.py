"""
Conversation history manager.
Stores per-user conversation history in memory.
"""

from collections import defaultdict
from config import MAX_HISTORY


class ConversationManager:
    """Manages conversation history for each user."""

    def __init__(self, max_history: int = MAX_HISTORY) -> None:
        self._history: dict[int, list[dict[str, str]]] = defaultdict(list)
        self._max_history = max_history
        self._user_models: dict[int, str] = {}
        self._user_vibes: dict[int, str] = {}

    def add_message(self, user_id: int, role: str, content: str) -> None:
        """
        Append a message to a user's conversation history.

        Args:
            user_id: Telegram user ID.
            role: Message role ('user' or 'assistant').
            content: Message text.
        """
        self._history[user_id].append({"role": role, "content": content})
        # Trim history to the configured limit
        if len(self._history[user_id]) > self._max_history:
            self._history[user_id] = self._history[user_id][-self._max_history :]

    def get_history(self, user_id: int) -> list[dict[str, str]]:
        """Return the conversation history for a user."""
        return list(self._history[user_id])

    def clear_history(self, user_id: int) -> None:
        """Clear conversation history for a user."""
        self._history[user_id].clear()

    def set_model(self, user_id: int, model: str) -> None:
        """Set the preferred model for a user."""
        self._user_models[user_id] = model

    def get_model(self, user_id: int) -> str | None:
        """Return the user's preferred model, or None if not set."""
        return self._user_models.get(user_id)

    def set_vibe(self, user_id: int, vibe: str) -> None:
        """Set the thinking-phrase vibe for a user."""
        self._user_vibes[user_id] = vibe

    def get_vibe(self, user_id: int) -> str | None:
        """Return the user's vibe, or None for default."""
        return self._user_vibes.get(user_id)
