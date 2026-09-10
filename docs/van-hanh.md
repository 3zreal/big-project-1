# Vận hành

## Chuẩn bị

```bash
uv sync
cp .env.example .env
```

Hoặc `python -m venv` + `pip install -r requirements.txt`.

Trong `.env`:

- `YOUTUBE_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS` — đường dẫn tuyệt đối tới JSON service account
- `GCP_PROJECT_ID`, `BQ_DATASET_RAW`, `BQ_DATASET_CURATED`

## Freeze Artist 100 (một lần, không phải cron)

Nguồn: Billboard Artist 100 (US). “US-UK” = nghệ sĩ chart trên list US.

```bash
uv run yt-freeze-artist-100
# uv run yt-freeze-artist-100 --date 2026-08-29
uv run yt-resolve-channels
```

Ghi `data/processed/artists_registry.csv` (gitignore, không push). Pipeline chỉ đọc file này.

Cần 100 `channel_id` `UC…` unique. Thiếu thì thêm `@handle` vào `scripts/channel_overrides.csv` rồi chạy lại resolver.

## Quota một lần chạy

Mỗi `list` = **1 unit**. Trần 10.000/ngày, reset nửa đêm Pacific. Không dùng `search.list`.

| Slice | Units (100 kênh, typical) |
|---|---|
| `channels.list` 50+50 | ~2 |
| `playlistItems.list` 1 page/kênh | ~100 |
| `videos.list` batch 50 (≤20 video/kênh) | ~40 |
| **Trước comments** | **~142** |
| Comments steady-state (video mới hơn watermark, 1 page) | ~0–30 |
| Comments bootstrap (last 5/kênh) | **≤ ~500** |
| Một run steady-state | ~200–350 |
| Một run bootstrap | ~150 + ≤500 |

Raw: `data/raw/{run_id}/`. Cache playlist: `data/processed/uploads_playlists.json`.

## Incremental load

Không TRUNCATE bảng curated. Mỗi batch:

1. **APPEND** `youtube_raw.raw_*`
2. **WRITE_TRUNCATE** chỉ `stg_*`
3. **SQL MERGE** `videos` / `comments` / `artists`
4. Snapshot MERGE `(channel_id, snapshot_date)`
5. Checkpoint: set `video_id` pending/completed

## Chạy local

Tạo dataset `youtube_raw` và `youtube_curated` trên Console trước.

```bash
uv run python test_connect.py
uv run yt-pipeline --limit 2
uv run yt-pipeline
uv run yt-demo-fetch --limit 2
```

## BigQuery

| Dataset | Bảng |
|---|---|
| `youtube_raw` | `raw_videos`, `raw_comments`, `raw_channels`, `stg_videos`, `stg_comments`, `stg_channels`, `stg_channel_snapshot` |
| `youtube_curated` | `artists`, `videos`, `comments`, `channel_daily_snapshot`, `pipeline_runs`, `fetch_checkpoint` |

## Chạy trên Google Cloud VM

1. Tạo Compute Engine VM, clone repo.
2. `python3 -m venv .venv` rồi `pip install -r requirements.txt`.
3. Copy `.env`, JSON service account, `data/processed/artists_registry.csv` lên VM.
4. `chmod +x run_pipeline.sh` rồi `./run_pipeline.sh`.

Log: `logs/cron.log`. Timezone VM: `Asia/Ho_Chi_Minh`.

## Schedule (crontab) — 02 lần/ngày (ICT)

```bash
crontab -e
```

```cron
SHELL=/bin/bash
0 7 * * *  /absolute/path/to/big-project-1/run_pipeline.sh
0 23 * * * /absolute/path/to/big-project-1/run_pipeline.sh
```
