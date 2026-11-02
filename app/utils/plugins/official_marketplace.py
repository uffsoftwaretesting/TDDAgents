"""
Constants for the official plugins marketplace.

Ported from `reference/claude-code/src/utils/plugins/officialMarketplace.ts` -> `OFFICIAL_MARKETPLACE_SOURCE`,
`OFFICIAL_MARKETPLACE_NAME`.

Only the constants are ported (user decision 2026-11-01): there is no startup auto-install
(`officialMarketplaceStartupCheck.ts`) and no GCS/CDN download (`officialMarketplaceGcs.ts`). The
blocklist and policy code reads these constants; the official marketplace is added like any other.
"""

from __future__ import annotations

from typing import Final

from app.utils.plugins.schemas import GithubMarketplaceSource

#: `OFFICIAL_MARKETPLACE_SOURCE`.
OFFICIAL_MARKETPLACE_SOURCE: Final[GithubMarketplaceSource] = {
    "source": "github",
    "repo": "anthropics/claude-plugins-official",
}

#: `OFFICIAL_MARKETPLACE_NAME`: the key in known_marketplaces.json.
OFFICIAL_MARKETPLACE_NAME: Final = "claude-plugins-official"

__all__ = ["OFFICIAL_MARKETPLACE_NAME", "OFFICIAL_MARKETPLACE_SOURCE"]
