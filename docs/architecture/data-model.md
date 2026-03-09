# 数据模型与存储布局

## 数据库定义（PostgreSQL）

说明：

- 使用 `uuid` 作为主键（或 `bigserial` 亦可）
- 时间统一使用 `timestamptz`
- JSON 使用 `jsonb`

### DDL（建议草案）

```sql
-- Providers: 'youtube' | 'bilibili'
create table media (
  id uuid primary key,
  provider text not null,
  provider_media_id text not null,
  url text not null,
  name text,
  avatar_url text,
  description text,
  subscriber_count bigint,
  video_count bigint,
  sync_cursor jsonb,
  last_profile_sync_at timestamptz,
  last_video_sync_at timestamptz,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  unique (provider, provider_media_id)
);
create index media_provider_idx on media(provider);

create table video (
  id uuid primary key,
  provider text not null,
  provider_video_id text not null,
  media_id uuid not null references media(id) on delete cascade,
  url text not null,
  title text,
  description text,
  thumbnail_url text,
  published_at timestamptz,
  duration_sec integer,
  view_count bigint,
  like_count bigint,
  comment_count bigint,
  tags text[],
  raw_info jsonb,
  status text not null,
  error_message text,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  unique (provider, provider_video_id)
);
create index video_media_published_idx on video(media_id, published_at desc);
create index video_status_idx on video(status);

create table asset (
  id uuid primary key,
  video_id uuid references video(id) on delete cascade,
  type text not null,
  format text not null,
  language text,
  source text not null,
  variant text,
  s3_bucket text not null,
  s3_key text not null,
  size_bytes bigint,
  checksum_sha256 text,
  metadata jsonb,
  created_at timestamptz not null,
  unique (video_id, type, format, coalesce(language, ''), source, coalesce(variant, ''))
);
create index asset_video_type_idx on asset(video_id, type);

create table playlist (
  id uuid primary key,
  name text not null,
  description text,
  created_at timestamptz not null,
  updated_at timestamptz not null
);
create unique index playlist_name_ux on playlist(lower(name));

create table playlist_media (
  playlist_id uuid not null references playlist(id) on delete cascade,
  media_id uuid not null references media(id) on delete cascade,
  added_at timestamptz not null,
  primary key (playlist_id, media_id)
);
create index playlist_media_media_idx on playlist_media(media_id);

create table daily_brief (
  id uuid primary key,
  playlist_id uuid not null references playlist(id) on delete cascade,
  brief_date date not null,
  status text not null,
  markdown_asset_id uuid references asset(id),
  error_message text,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  unique (playlist_id, brief_date)
);

create table job (
  id uuid primary key,
  type text not null,
  status text not null,
  priority integer not null default 0,
  params jsonb not null,
  result jsonb,
  progress_current integer,
  progress_total integer,
  error_message text,
  error_stack text,
  attempt integer not null default 0,
  max_attempts integer not null default 5,
  scheduled_for timestamptz not null default now(),
  created_at timestamptz not null,
  started_at timestamptz,
  finished_at timestamptz,
  lease_expires_at timestamptz,
  worker_id text,
  parent_job_id uuid references job(id) on delete set null
);
create index job_claim_idx on job(status, scheduled_for, priority desc);
create index job_lease_idx on job(status, lease_expires_at);
create index job_type_idx on job(type);

create table job_event (
  id bigserial primary key,
  job_id uuid not null references job(id) on delete cascade,
  ts timestamptz not null,
  level text not null,
  message text not null,
  data jsonb
);
create index job_event_job_ts_idx on job_event(job_id, ts desc);

create table app_config (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null
);
```

### 关键索引与约束说明

- `videos(provider, provider_video_id)` 唯一：保证发现视频幂等
- `assets(...)` 唯一：保证产物写入幂等，避免重复上传
- `daily_brief(playlist_id, brief_date)` 唯一：保证一天一份简报
- `job_claim_idx`：高频任务领取路径索引

## 对象存储（MinIO / S3）布局

### Bucket

建议统一一个 bucket（例如 `raelyn`），便于管理生命周期；也可按环境拆分。

### Key 命名规范（建议）

```text
{provider}/{provider_media_id}/{provider_video_id}/
  video/{variant}.{ext}
  audio/{variant}.{ext}
  subtitle/{lang}/{variant}.{ext}
  transcript/{lang}/{variant}.{ext}
  brief/{playlist_id}/{YYYY-MM-DD}.md
  meta/info.json
```

### 文件写入策略

- 统一先写临时 key 或本地临时文件，再校验并写入最终 key
- 建议记录 `checksum_sha256` 用于去重与校验
