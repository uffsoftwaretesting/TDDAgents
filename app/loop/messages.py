"""
The loop's message currency, declared in exactly one place.

`Message` is an alias rather than a class because the loop does not own the message
format — the model seam does. Today every agent module, the chat-model factory and the
LangGraph reducers all speak `langchain_core.messages.BaseMessage`, so adopting a second
vocabulary here would buy nothing and cost an adapter at every boundary.

The alias exists so that decision stays revisable: if Part F1 moves the model call onto a
provider SDK, this line is the whole migration surface, and every signature that mentions
`Message` follows it. Import `Message` from here rather than importing `BaseMessage`
directly, or that property is lost the first time someone shortcuts it.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage

#: What a conversation is made of, as far as the loop is concerned.
Message = BaseMessage
