# youtube-data-pipeline

Data pipeline YouTube: **YouTube Data API v3 → Python → BigQuery**, triển khai trên Google Cloud VM và chạy lịch 2 lần/ngày.

> `python main.py` fetch → MERGE BigQuery. Tạo dataset tay trên Console; code chỉ tạo bảng còn thiếu rồi ghi.

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
│   ├── fetch/                    # FETCH, tách theo API surface
│   │   ├── context.py            # FetchContext, start_run, cache uploads playlist
│   │   ├── channels.py           # channels.list, batch 50
│   │   ├── videos.py             # 1 playlist page/kênh + videos.list
│   │   ├── comments.py           # commentThreads.list + chọn video cần comment
│   │   └── results.py            # VideoFetchResult / CommentFetchResult
│   ├── quota.py          # units_spent theo ngày Pacific
│   ├── errors.py         # redact HttpError; quotaExceeded trước commentsDisabled
│   ├── youtube_api.py    # execute(): 1 unit, dịch HttpError → YoutubeApiError tại biên
│   ├── retry.py          # backoff 429/5xx; không retry quotaExceeded
│   ├── checkpoint.py     # set video_id pending/completed + watermark
│   ├── batch.py          # MERGE videos → comments → checkpoint
│   ├── config.py         # Tables (table IDs from env)
│   ├── table_schemas.py  # cột của mọi bảng — nguồn sự thật duy nhất
│   ├── schema.py         # create missing tables (not datasets)
│   ├── transform.py      # dtypes, extracted_at, ingestion_date
│   ├── load.py           # APPEND raw, TRUNCATE stg_*, MERGE curated
│   ├── artist_registry.py  # đọc/ghi data/processed/artists_registry.csv
│   └── utils.py          # paths, env, logging, JSON, chunked/dedupe, BQ client
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

`uv sync` cài luôn project (pyproject có `[build-system]`), nên `etl`/`scripts` import
được từ bất kỳ thư mục nào — entrypoint không cần vá `sys.path`.

Trong `.env`:

- `YOUTUBE_API_KEY` — API key YouTube Data API v3 (không commit)
- `GOOGLE_APPLICATION_CREDENTIALS` — đường dẫn file service account JSON
- `GCP_PROJECT_ID`, `BQ_DATASET_RAW`, `BQ_DATASET_CURATED`

## Freeze Artist 100 (một lần, không phải cron)

Nguồn: Billboard Artist 100 (US), tuần chart ghi trong CSV. “US-UK” = nghệ sĩ UK (và khác) **khi họ chart trên list US** — không union chart UK.

```bash
uv run yt-freeze-artist-100
# uv run yt-freeze-artist-100 --date 2026-08-29
uv run yt-resolve-channels     # cần YOUTUBE_API_KEY; không dùng search.list
```

Ghi `data/processed/artists_registry.csv` (gitignore `*.csv`, không push). Pipeline YouTube **chỉ đọc** file này; `etl/` / `main.py` không gọi Billboard.

Freeze **fail** nếu không đủ 100 `channel_id` dạng `UC…` unique. Bổ sung `@handle` công khai vào `scripts/channel_overrides.csv` rồi chạy lại resolver (script gọi `channels.list(forHandle=)` — chạy được trên VM, không scrape YouTube search).

Chạy lại Billboard freeze **giữ** `channel_id` đã resolve nếu `artist_name` không đổi.

## Quota một lần chạy

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

Dừng run khi remaining Pacific < 1 unit cho call tiếp theo. Raw JSON: `data/raw/{run_id}/`. `uploads_playlist_id` cache: `data/processed/uploads_playlists.json` (gitignore).

## Incremental load

Không TRUNCATE bảng curated. Mỗi batch:

1. **APPEND** `youtube_raw.raw_*` (giữ lịch sử extract)
2. **WRITE_TRUNCATE** chỉ `stg_*` (dedupe PK trước khi MERGE)
3. **SQL MERGE** vào `youtube_curated.videos` / `comments` / `artists` — không `WHEN NOT MATCHED BY SOURCE THEN DELETE`
4. Snapshot kênh MERGE trên `(channel_id, snapshot_date)` — chạy lại cùng ngày UTC cập nhật row, không xóa ngày khác
5. Checkpoint = **set** `video_id` (`comment_pending_video_ids` / `completed_video_ids`), không `playlist_page_token` / `last_video_id`

`load(df, table, *, write_disposition=...)` bắt buộc keyword, không default. `WRITE_TRUNCATE` trên bảng không phải `stg_*` sẽ `ValueError`.

`quotaExceeded` giữa comments: videos đã MERGE; comments đã lấy thì MERGE; ID còn lại nằm trong pending set để run sau resume (không bỏ quota thành `commentsDisabled`). 429/5xx retry tối đa 5 lần (`etl/retry.py`); không retry quota.

Watermark kênh chỉ tăng sau khi comments của batch đó durable hoặc video bị skip `commentsDisabled`. Burst (remainder playlist còn video mới hơn watermark cũ) thì không tăng watermark.

## Chạy local

Tạo sẵn dataset `youtube_raw` và `youtube_curated` trên Console. Code **không** tạo dataset; bảng còn thiếu sẽ được tạo lúc chạy.

```bash
uv run python test_connect.py             # in ra ok = 1 là đạt
uv run yt-pipeline --limit 2              # demo 2 kênh → ghi BigQuery
uv run yt-pipeline                        # đủ 100 nghệ sĩ freeze CSV
uv run yt-demo-fetch --limit 2            # fetch thử, không ghi BigQuery
```

## BigQuery

| Dataset | Bảng |
|---|---|
| `youtube_raw` | `raw_videos`, `raw_comments`, `raw_channels`, `stg_videos`, `stg_comments`, `stg_channels`, `stg_channel_snapshot` |
| `youtube_curated` | `artists`, `videos`, `comments`, `channel_daily_snapshot`, `pipeline_runs`, `fetch_checkpoint` |

Curated MERGE (không TRUNCATE). Raw APPEND. Staging TRUNCATE từng batch.

## Chạy trên Google Cloud VM

1. Tạo Compute Engine VM, clone repo.
2. Cài Python, tạo `.venv`, `pip install -r requirements.txt`.
3. Copy `.env` và file service account lên VM (không đưa lên GitHub).
4. `chmod +x run_pipeline.sh` rồi chạy thử `./run_pipeline.sh` (ghi `logs/cron.log`).

Script dùng `flock` trên Linux để lần 07:00 chưa xong thì 23:00 **bỏ qua** (không chạy chồng). Cron có `PATH` gần như rỗng — script tự set `PATH` và Python `.venv`.

## Schedule (crontab) — 02 lần/ngày (ICT)

Đặt timezone VM `Asia/Ho_Chi_Minh` (hoặc dùng `CRON_TZ`). Đường dẫn phải tuyệt đối:

```bash
crontab -e
```

```cron
0 7 * * *  /absolute/path/to/big-project-1/run_pipeline.sh
0 23 * * * /absolute/path/to/big-project-1/run_pipeline.sh
```

## Tài liệu tham khảo

- [YouTube Data API v3](https://developers.google.com/youtube/v3)
- [Getting Started](https://developers.google.com/youtube/v3/getting-started)
- [Videos](https://developers.google.com/youtube/v3/docs/videos)
- [CommentThreads: list](https://developers.google.com/youtube/v3/docs/commentThreads/list)
