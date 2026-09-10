# youtube-data-pipeline

**YouTube Data API v3 → Python → Transform → BigQuery**, Google Cloud VM, cron 02 lần/ngày.

Cài đặt và vận hành: [`docs/van-hanh.md`](docs/van-hanh.md).

## Đề bài

Big Project 1 — JDE Data Engineering. Pipeline thu thập dữ liệu kênh/nghệ sĩ YouTube, source trên GitHub (không lộ key), Compute Engine VM, chạy tự động 02 lần/ngày.

100 nghệ sĩ Billboard Artist 100 (US). Freeze `data/processed/artists_registry.csv`. Channel ID: Wikidata P2397 và `channels.list(forHandle=)` — không `search.list`. Incremental load và retry.

| Bảng | Trường |
|---|---|
| video | `artist_name`, `channel_id`, `channel_title`, `video_id`, `video_title`, `description`, `published_at`, `duration`, `tags`, `category_id`, `view_count`, `like_count`, `comment_count`, `extracted_at`, `source`, `ingestion_date` |
| comment | `artist_name`, `channel_id`, `video_id`, `comment_id`, `parent_id`, `author_name`, `comment_text`, `published_at`, `updated_at`, `like_count`, `reply_count`, `extracted_at`, `ingestion_date` |

## Cấu trúc dự án

```
youtube-data-pipeline/
├── etl/
│   ├── fetch/
│   │   ├── context.py
│   │   ├── channels.py
│   │   ├── videos.py
│   │   ├── comments.py
│   │   └── results.py
│   ├── transform.py
│   ├── load.py
│   ├── utils.py
│   ├── config.py
│   ├── table_schemas.py
│   ├── schema.py
│   ├── quota.py
│   ├── retry.py
│   ├── errors.py
│   ├── youtube_api.py
│   ├── checkpoint.py
│   ├── batch.py
│   └── artist_registry.py
├── scripts/
│   ├── billboard.py
│   ├── fetch_artist_100.py
│   ├── resolve_channel_ids.py
│   ├── resolve_channels/
│   └── channel_overrides.csv
├── data/
│   ├── raw/
│   └── processed/
├── logs/
├── docs/van-hanh.md
├── main.py
├── test_connect.py
├── run_pipeline.sh
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Tài liệu tham khảo

**YouTube Data API v3**

- [Tổng quan](https://developers.google.com/youtube/v3)
- [Getting Started](https://developers.google.com/youtube/v3/getting-started)
- [API Reference](https://developers.google.com/youtube/v3/docs)
- [Video resource và statistics](https://developers.google.com/youtube/v3/docs/videos)
- [CommentThreads: list](https://developers.google.com/youtube/v3/docs/commentThreads/list)
- [Quota và cost từng method](https://developers.google.com/youtube/v3/determine_quota_cost)

**Nguồn xếp hạng và channel ID**

- [Billboard Artist 100](https://www.billboard.com/charts/artist-100) — nguồn "top 100 US-UK"
- [Wikidata P2397 — YouTube channel ID](https://www.wikidata.org/wiki/Property:P2397)

**BigQuery**

- [MERGE statement](https://cloud.google.com/bigquery/docs/reference/standard-sql/dml-syntax#merge_statement)
- [Load data from a DataFrame](https://cloud.google.com/python/docs/reference/bigquery/latest)
- [Partitioned tables](https://cloud.google.com/bigquery/docs/partitioned-tables)
