# youtube-data-pipeline

Data pipeline YouTube: **YouTube Data API v3 → Python → BigQuery**, triển khai trên Google Cloud VM và chạy lịch 2 lần/ngày.

> Skeleton theo cấu trúc đề bài Big Project 1. Fetch slice + incremental MERGE đã có; `run()` nối đủ luồng ở phase 4.

## Cấu trúc

```
youtube-data-pipeline/
├── scripts/
│   ├── billboard.py              # Artist 100 only (one-off freeze)
│   ├── fetch_artist_100.py
│   ├── resolve_channel_ids.py    # CLI: Wikidata + forHandle (no search.list)
│   ├── resolve_channels/         # names.py, wikidata.py, youtube.py, pipeline.py
│   └── channel_overrides.csv     # @handles → channels.list(forHandle=)
├── etl/
│   ├── __init__.py
│   ├── fetch.py        # slice: N=20 videos/channel, 1 playlist page, comments 1 page
│   ├── quota.py        # units_spent theo ngày Pacific
│   ├── errors.py       # redact HttpError; quotaExceeded trước commentsDisabled
│   ├── youtube_api.py  # channels/playlist/videos/commentThreads list (1 unit)
│   ├── retry.py        # backoff 429/5xx; không retry quotaExceeded
│   ├── checkpoint.py   # set video_id pending/completed + watermark
│   ├── batch.py        # MERGE videos → comments → checkpoint
│   ├── transform.py    # làm sạch / chuẩn hóa → data/processed/
│   ├── load.py         # APPEND raw, TRUNCATE stg_*, MERGE curated
│   └── utils.py        # env, logging, BigQuery client, data/raw/{run_id}
├── data/
│   ├── raw/            # response thô (không commit)
│   └── processed/      # không commit (gồm artists_registry.csv)
├── logs/               # nhật ký vận hành (không commit)
├── main.py             # run(): fetch → transform → load
├── test_connect.py     # kiểm tra kết nối BigQuery
├── run_pipeline.sh     # script crontab trên VM
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Chuẩn bị

```bash
uv sync
cp .env.example .env               # điền API key + GCP project + đường dẫn service account
```

Hoặc `python -m venv` + `pip install -r requirements.txt`.

Trong `.env`:

- `YOUTUBE_API_KEY` — API key YouTube Data API v3 (không commit)
- `GOOGLE_APPLICATION_CREDENTIALS` — đường dẫn file service account JSON
- `GCP_PROJECT_ID`, `BQ_DATASET_RAW`, `BQ_DATASET_CURATED`

## Freeze Artist 100 (một lần, không phải cron)

Nguồn: Billboard Artist 100 (US), tuần chart ghi trong CSV. “US-UK” = nghệ sĩ UK (và khác) **khi họ chart trên list US** — không union chart UK.

```bash
uv run python scripts/fetch_artist_100.py
# uv run python scripts/fetch_artist_100.py --date 2026-08-29
uv run python scripts/resolve_channel_ids.py   # cần YOUTUBE_API_KEY; không dùng search.list
```

Ghi `data/processed/artists_registry.csv` (gitignore `*.csv`, không push). Pipeline YouTube **chỉ đọc** file này; `etl/` / `main.py` không gọi Billboard.

Freeze **fail** nếu không đủ 100 `channel_id` dạng `UC…` unique. Bổ sung `@handle` công khai vào `scripts/channel_overrides.csv` rồi chạy lại resolver (script gọi `channels.list(forHandle=)` — chạy được trên VM, không scrape YouTube search).

Chạy lại Billboard freeze **giữ** `channel_id` đã resolve nếu `artist_name` không đổi.

## Quota một lần chạy (Phase 2)

Mỗi method list ở trên = **1 unit**. Trần mặc định 10.000 unit/ngày, reset nửa đêm **Pacific**. Không dùng `search.list`.

| Slice | Units (100 kênh, typical) |
|---|---|
| `channels.list` 50+50 | ~2 |
| `playlistItems.list` 1 page/kênh | ~100 |
| `videos.list` batch 50 (≤20 video/kênh) | ~40 |
| **Trước comments** | **~142** |
| Comments steady-state (chỉ video mới hơn watermark, 1 page) | ~0–30 |
| Comments **bootstrap** (last 5 published/kênh, không 20×100) | **≤ ~500** |
| Một run steady-state | ~200–350 |
| Một run bootstrap (comments lần đầu) | ~150 + ≤500 |

Hard stop khi remaining Pacific < 1 unit cho call tiếp theo. Raw JSON: `data/raw/{run_id}/`. `uploads_playlist_id` cache: `data/processed/uploads_playlists.json` (gitignore).

`main.py` chưa nối fetch slice này (phase 4).

## Incremental load (Phase 3)

Không full-refresh như mức dễ (TRUNCATE cả bảng curated mỗi lần chạy). Luồng một batch:

1. **APPEND** `youtube_raw.raw_*` (giữ lịch sử extract)
2. **WRITE_TRUNCATE** chỉ `stg_videos` / `stg_comments` / `stg_channels` (dedupe PK trước khi MERGE)
3. **SQL MERGE** vào `youtube_curated.videos` / `comments` / `artists` — không `WHEN NOT MATCHED BY SOURCE THEN DELETE`
4. Snapshot kênh MERGE trên `(channel_id, snapshot_date)` — chạy lại cùng ngày UTC cập nhật row, không xóa ngày khác
5. Checkpoint = **set** `video_id` (`comment_pending_video_ids` / `completed_video_ids`), không `playlist_page_token` / `last_video_id`

`load(df, table, *, write_disposition=...)` bắt buộc keyword, không default. `WRITE_TRUNCATE` trên bảng không phải `stg_*` sẽ `ValueError`.

`quotaExceeded` giữa comments: videos đã MERGE; comments đã lấy thì MERGE; ID còn lại nằm trong pending set để run sau resume (không bỏ quota thành `commentsDisabled`). 429/5xx retry tối đa 5 lần (`etl/retry.py`); không retry quota.

Watermark kênh chỉ tăng sau khi comments của batch đó durable hoặc video bị skip `commentsDisabled`. Burst (remainder playlist còn video mới hơn watermark cũ) thì không tăng watermark.

## Chạy local

```bash
python test_connect.py             # in ra ok = 1 là đạt
python main.py                     # chạy pipeline (đang là skeleton)
```

## BigQuery (dự kiến)

Tự tạo dataset trên project GCP cá nhân:

| Dataset | Vai trò |
|---|---|
| `youtube_raw` | response gốc, đối soát / xử lý lại |
| `youtube_curated` | dữ liệu đã clean, dùng cho phân tích |

Bảng tối thiểu (theo đề): `videos`, `comments`. Schema chi tiết sẽ bổ sung khi triển khai fetch/transform.

## Chạy trên Google Cloud VM

1. Tạo Compute Engine VM, clone repo.
2. Cài Python, tạo `.venv`, `pip install -r requirements.txt`.
3. Copy `.env` và file service account lên VM (không đưa lên GitHub).
4. `chmod +x run_pipeline.sh` rồi chạy thử `./run_pipeline.sh`.

## Schedule (crontab) — 02 lần/ngày

```bash
crontab -e
```

```cron
0 7 * * *  /absolute/path/to/big-project-1/run_pipeline.sh
0 23 * * * /absolute/path/to/big-project-1/run_pipeline.sh
```

Log mỗi lần chạy: `logs/cron.log`.

## Tài liệu tham khảo

- [YouTube Data API v3](https://developers.google.com/youtube/v3)
- [Getting Started](https://developers.google.com/youtube/v3/getting-started)
- [Videos](https://developers.google.com/youtube/v3/docs/videos)
- [CommentThreads: list](https://developers.google.com/youtube/v3/docs/commentThreads/list)
