"""One-off: fill channel_id on artists_registry.csv.

Resolution order (never search.list, never youtube.com/results scrape):
  1. scripts/channel_overrides.csv @handle → channels.list(forHandle=)
  2. Existing CSV value (re-runs)
  3. Wikidata property P2397 (YouTube channel ID)
  4. Guessed handles → channels.list(forHandle=)

Then batch-validate with channels.list(id=) and store channel_title.
Implementation lives in scripts/resolve_channels/.
"""
from __future__ import annotations

from scripts.resolve_channels.pipeline import main

if __name__ == "__main__":
    main()
