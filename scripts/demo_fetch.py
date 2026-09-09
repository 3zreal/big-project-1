"""Fetch a few freeze-CSV channels without loading BigQuery.

    uv run python -m scripts.demo_fetch --limit 2
"""
from __future__ import annotations

import argparse

from etl.artist_registry import read_registry_rows
from etl.fetch import fetch_channels, fetch_comments, fetch_videos, select_comment_targets, start_run
from etl.utils import bootstrap


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a subset of Artist 100 (no BigQuery load)")
    parser.add_argument("--limit", type=int, default=2, help="How many freeze-CSV channels (default: 2)")
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")

    bootstrap("demo_fetch.log")

    artists = [
        (row.get("artist_name") or row["channel_id"].strip(), row["channel_id"].strip())
        for row in read_registry_rows(args.limit)
    ]
    channel_ids = [cid for _, cid in artists]
    print("channels:", ", ".join(f"{name} ({cid})" for name, cid in artists))

    ctx = start_run()
    channels = fetch_channels(channel_ids, ctx=ctx)
    videos = fetch_videos(channel_ids, ctx=ctx)
    comment_ids = select_comment_targets(videos.frame)
    comments = fetch_comments(comment_ids, ctx=ctx)

    print(f"run_id={ctx.run_id}")
    print(f"raw={ctx.run_dir}")
    print(
        f"channels_df={len(channels)} videos={len(videos.frame)} "
        f"comment_targets={len(comment_ids)} comments={len(comments.frame)}"
    )
    print(f"quota={ctx.quota.snapshot()}")


if __name__ == "__main__":
    main()
