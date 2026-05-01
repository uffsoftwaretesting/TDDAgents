from langchain_core.callbacks import UsageMetadataCallbackHandler


class GlobalTokenTracker(UsageMetadataCallbackHandler):
    """
    Modern token tracker built on UsageMetadataCallbackHandler (langchain-core ≥ 0.3.x).
    Supports cached tokens, multi-modal counting, and per-model breakdown out of the box.
    """

    @property
    def total_tokens(self) -> int:
        return sum(m.get("total_tokens", 0) for m in self.usage_metadata.values())

    @property
    def prompt_tokens(self) -> int:
        return sum(m.get("input_tokens", 0) for m in self.usage_metadata.values())

    @property
    def completion_tokens(self) -> int:
        return sum(m.get("output_tokens", 0) for m in self.usage_metadata.values())

    @property
    def cached_tokens(self) -> int:
        return sum(
            m.get("input_token_details", {}).get("cache_read", 0)
            for m in self.usage_metadata.values()
        )

    def summary(self) -> dict:
        return {
            "by_model": dict(self.usage_metadata),
            "totals": {
                "total_tokens": self.total_tokens,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cached_tokens": self.cached_tokens,
            },
        }

    def reset(self) -> None:
        """Clear accumulated data between invocations."""
        self.usage_metadata.clear()