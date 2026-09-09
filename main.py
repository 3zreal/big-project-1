"""Pipeline orchestrator: fetch -> transform -> load.

Run:
    uv run python main.py --limit 2
    uv run python main.py

Copy .env.example to .env and set GOOGLE_APPLICATION_CREDENTIALS before running.
Create datasets youtube_raw and youtube_curated in the console first.
Code creates missing tables, then APPEND raw / MERGE curated. No dataset create.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from etl.artist_registry import read_registry_rows
from etl.batch import ingest_channel_facts, ingest_video_comment_batch
from etl.checkpoint import load_checkpoint, resume_comment_targets
from etl.errors import QuotaExceeded, QuotaStop
from etl.fetch import fetch_channels, fetch_videos, select_comment_targets, start_run
from etl.load import append_raw
from etl.schema import ensure_tables
from etl.transform import (
    transform_channels,
    transform_comments,
    transform_snapshot,
    transform_videos,
)
from etl.utils import bootstrap


def run(limit: int | None = None) -> None:
    logger = bootstrap(require=("YOUTUBE_API_KEY", "GCP_PROJECT_ID"))

    tables = ensure_tables()
    registry = pd.DataFrame(read_registry_rows(limit))
    channel_ids = [str(c) for c in registry["channel_id"].tolist()]
    logger.info("Starting YouTube pipeline channels=%s", len(channel_ids))

    checkpoint = load_checkpoint()
    watermarks = dict(checkpoint.get("watermarks") or {})
    ctx = start_run()
    started = datetime.now(timezone.utc)
    status = "ok"
    error_message = ""
    channels_t = pd.DataFrame()
    videos_t = pd.DataFrame()
    comments = pd.DataFrame()

    try:
        channels = fetch_channels(channel_ids, ctx=ctx)
        video_result = fetch_videos(channel_ids, watermark=watermarks, ctx=ctx)
        videos = video_result.frame
        extracted_at = datetime.now(timezone.utc)

        channels_t = transform_channels(
            channels, artists=registry, run_id=ctx.run_id, extracted_at=extracted_at
        )
        videos_t = transform_videos(
            videos,
            artists=registry,
            run_id=ctx.run_id,
            extracted_at=extracted_at,
            channels=channels_t,
        )
        snapshot = transform_snapshot(channels_t, run_id=ctx.run_id, extracted_at=extracted_at)
        ingest_channel_facts(channels_t, tables=tables, snapshot=snapshot)

        known = set(videos["video_id"].astype(str)) if not videos.empty else set()
        comment_ids = resume_comment_targets(
            checkpoint, known, select_comment_targets(videos, watermark=watermarks)
        )

        def prepare_comments(frame: pd.DataFrame) -> pd.DataFrame:
            return transform_comments(
                frame,
                videos=videos_t,
                artists=registry,
                run_id=ctx.run_id,
                extracted_at=extracted_at,
            )

        comments = ingest_video_comment_batch(
            videos_t,
            comment_ids,
            ctx=ctx,
            tables=tables,
            checkpoint=checkpoint,
            remainder_blocked=video_result.blocked_channel_ids(),
            prepare_comments=prepare_comments,
        ).frame
    except (QuotaExceeded, QuotaStop) as err:
        status = "quota"
        error_message = str(err)
        logger.warning("run stopped: %s", err)
    except Exception as err:
        status = "error"
        error_message = str(err)
        logger.exception("pipeline failed")
        raise
    finally:
        run_row = pd.DataFrame(
            [
                {
                    "run_id": ctx.run_id,
                    "started_at": started,
                    "finished_at": datetime.now(timezone.utc),
                    "status": status,
                    "units_spent": ctx.quota.units_spent,
                    "pacific_date": ctx.quota.pacific_date,
                    "channel_count": len(channels_t),
                    "video_count": len(videos_t),
                    "comment_count": len(comments),
                    "error_message": error_message[:1000],
                }
            ]
        )
        try:
            append_raw(run_row, tables.pipeline_runs)
        except Exception:
            logger.exception("failed to append pipeline_runs")
        logger.info(
            "Pipeline finished status=%s run_id=%s videos=%s comments=%s quota=%s",
            status,
            ctx.run_id,
            len(videos_t),
            len(comments),
            ctx.quota.snapshot(),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube → BigQuery pipeline")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only the first N freeze-CSV channels (demo). Default: all 100.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    run(limit=args.limit)


if __name__ == "__main__":
    main()
