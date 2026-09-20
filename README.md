<div align="center">

<img src="logo.png" width="112" alt="Raelyn" />

# RAELYN

### A private semantic observatory

**Raelyn watches the public video sources you choose, condenses what they say into real-world events, and grows them into a living starfield you can replay, follow, and trace back to the original evidence.**

[![License](https://img.shields.io/badge/license-Apache--2.0-2f6feb.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776ab.svg)](Dockerfile)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%C2%B7%20PostgreSQL%20%C2%B7%20S3-0b7285.svg)](docs/architecture/overview.md)
[![Self-hosted](https://img.shields.io/badge/deploy-self--hosted-16a34a.svg)](docs/reference/run-and-deploy.md)
[![MCP](https://img.shields.io/badge/agents-MCP%20ready-7c3aed.svg)](docs/architecture/mcp.md)

`Self-hosted` · `Evidence-first` · `Agent-ready` · `No CDN, no telemetry, no account`

**English** · [简体中文](README.zh-CN.md)

</div>

---

![Raelyn semantic starfield](docs/assets/readme/raelyn-semantic-starfield.gif)

<p align="center"><sub>Real WebGL capture, not a mockup: 6 seconds replaying a 12-month observation window, then a real event opening in the inspector. Each star is a conservatively merged canonical real-world event. The three axes express semantic proximity — never time, geography, causality, or impact.<br><a href="docs/assets/readme/raelyn-semantic-starfield.mp4">Watch the 1920×1200 MP4</a></sub></p>

---

## The idea

Feeds hand you items. Search hands you keywords. Neither lets you see the **shape** of a domain you have followed for two years, or how one story actually developed, or why you believe a conclusion you read last month.

Raelyn is built for the other mode: **long-term observation of a world you chose**.

You define an *observation domain* — a set of channels plus the questions you care about. Raelyn then runs continuously on your own hardware: it syncs sources, downloads and transcribes them, extracts structured events, merges duplicate reports of the same real-world event, projects them into a stable 3D semantic space, links evidence-backed continuations into stories, and writes periodic briefs that cite all of it.

Four questions, answered durably:

|  |  |
| --- | --- |
| **What happened?** | Canonical real-world events, merged from many reports |
| **What changed since I last looked?** | An observation cursor plus a change set that separates *when it happened* from *when the system learned it* |
| **How did it continue?** | Stories built only from evidence-backed `continuation` / `causes` / `response` / `corrects` relations |
| **Where did this conclusion come from?** | Every star, edge, and brief sentence drills down to a record, a transcript span, and the original video |

## A session in Raelyn

```
open a domain → the starfield is where you left it
  → play from your last observation to now, and watch events arrive
    → a trail lights up: a story gained a member, evidence, or a correction
      → open the canonical event, then its records and entities
        → jump to the exact transcript span and play the source video
          → read the period brief that explains this slice, every claim cited
            → adjust your sources and keep observing
```

The starfield is the default home screen. Jobs, storage, and model health stay out of the way until something actually needs you.

## Why it might interest you

- **You see change, not inventory.** The home screen is "what moved in this world", not "how many files were downloaded".
- **Space becomes memory.** Semantic coordinates stay continuous across snapshots, so over months you start remembering *where* a theme lives and *where* a story runs. When a rebase is unavoidable, the product says so.
- **Stories are earned, not inferred.** Semantic nearness only forms a theme. A story edge requires frozen evidence on both endpoints and explicit continuation, causal, response, or correction language.
- **Briefs are a lens on the starfield, not orphan Markdown.** Each brief pins the snapshot and generation basis it used and carries canonical / story / evidence / source references that navigate both ways.
- **Uncertainty is a visible product state.** Pending verification, thin evidence, imprecise event time, merges and splits are shown — not hidden behind nice visuals.
- **It stays yours.** Your capture, your database, your object storage, your model endpoints. Web, REST, WebSocket, and MCP all speak the same object semantics.

## What is implemented today

Nine first-class pages, all running against the same object model:

| Page | What it does |
| --- | --- |
| **Starfield** | 3D WebGL semantic field per domain: rotate/zoom/pan, month timeline, event-type and entity filters, theme and canonical inspectors, story trails, and a change layer split into *newly happened*, *cognition changed*, *story updated*, and *pending verification*. A typed list view is the non-WebGL twin of the same data. |
| **Stories** | A reading queue, not an audit table: worth reading / following / established / early leads, with unread substantive updates, "continue from new", real-time spans, maturity, and per-edge supporting records. |
| **Playlist** | Per-domain source replay: a day/week/month period rail, the playable records of that period, and a player + transcript column — with the period brief on the same rail. |
| **Library** | Sources and source records, scoped to the current domain or globally: import/export, sync, status, per-domain add/remove, deletion safety, and reverse lookup from a record to the canonicals, story edges, and briefs it supports. |
| **Domain settings** | Identity, cover, brief period, domain prompt, four independent coverage meters (usable transcripts / current-basis extraction / starfield admission / evidence verification), the continuous-observation switch, and guarded deletion with an impact report. |
| **Run center** | Jobs, workers, coverage, failure and skip reasons, live refresh, cancel and retry, plus domain data maintenance (backfill, re-extract, rebuild starfield). |
| **Resource usage** | 90-day trends for external calls, LLM tokens, downloads, and storage; per service/operation/provider/model profiles with success rate, latency, and missing-usage accounting. |
| **Agent access** | MCP endpoint, bearer auth, and ready-made connection snippets for MCP clients. |
| **Settings** | Inference endpoints (local OpenAI-compatible, Ollama, or Volcengine Ark/Speech), cookies, download and asset delivery policy, pause controls. |

Under those pages:

- **Sources**: YouTube and Bilibili, incremental sync with jitter, cookie recovery, PO-token provider support, and circuit breaking on download failures.
- **Ingest**: video, thumbnail, and subtitle download; audio extraction via ffmpeg; subtitle normalization; optional ASR when no usable subtitle exists; every artifact stored as a tracked asset.
- **Events**: structured extraction from `plain` transcripts, with event time, admission time, type, entities, relations, confidence, model basis, and evidence spans; embeddings for accepted events; conservative canonical merge that keeps singletons rather than risking a false merge, with merge/split lineage.
- **Snapshots**: immutable per-domain snapshots freezing two-level themes, story structure, entity indices, and fixed 3D coordinates.
- **Briefs**: daily / weekly / monthly aggregation into Markdown with structured references and safe rendering.
- **Runtime**: `FastAPI + Scheduler + 9 role-scoped workers`, one job system for every sync, download, process, and AI task — status, progress, retries, pause (system / provider / role), heartbeats, orphan requeue, and failure surfacing.

Gaps we have not closed yet are tracked honestly in [V2 capability gaps](docs/product/capability-gap-v2.md) — manual story curation, paragraph-level brief citations, transcript offset migration, hybrid semantic recall, and scale validation.

## From source to understanding

```text
Source (Media)
  └─ sync / download
      └─ Source record (Video + Asset)
          └─ subtitle / ASR / transcript
              ├─ periodic aggregation ─────────→ Markdown brief (+ structured references)
              └─ event extraction → review → embedding
                  └─ canonical / topic / story
                      └─ immutable 3D starfield snapshot
```

Briefs are currently generated from transcripts aggregated over the period, not reverse-generated from a snapshot.

## Product objects

| Concept | Implementation object | Responsibility |
| --- | --- | --- |
| Observation domain | `Playlist` | Sources, prompts, brief policy, and its own starfield |
| Source | `Media` | A channel or account under continuous observation |
| Source record | `Video` + `Asset` | Video, subtitle, audio, transcript, original link |
| Event record | `MarketEvent` | One structured statement from one analysis pass |
| Real-world event | `EventMapCanonical` | Conservative merge of multiple event records |
| Theme / story | `EventMapTopic` / `EventMapStoryIdentity` | Semantic grouping, and evidence-backed cross-snapshot relations |
| Observation brief | `Brief` / `BriefReference` | Period narrative with structured citations |
| Semantic starfield | `EventMapSnapshot` | The domain's fixed 3D semantic space |

> The API and data model still use the `Playlist` / `event map` names internally; the product language moved to *observation domain* and *semantic starfield*.

## Architecture

```text
Web / PWA ─┐
REST / WS ─┼─→ FastAPI ──→ PostgreSQL
MCP ───────┘      │       └→ S3 / MinIO
                  │
Scheduler ─→ Job queue ─→ Worker roles
                            ├─ download_youtube / download_bilibili
                            ├─ audio / process (ffmpeg, subtitles)
                            ├─ asr
                            ├─ sync
                            ├─ embedding
                            ├─ analysis (canonical / topic / story / 3D snapshot)
                            └─ ai (event extraction, transcript polish, briefs)
```

`FastAPI + Alpine.js SPA + TailwindCSS + PostgreSQL + S3/MinIO + workers/scheduler + MCP`. The built UI loads nothing from a CDN, and the runtime needs no Node.

**The starfield hot path does not ship JSONB.** Coordinates, time ranges, type codes, theme membership, review flags, and point indices live in typed relational columns; the API streams them as fixed-width 56-byte binary records that the browser parses straight into WebGL typed arrays. JSONB only carries low-frequency, evolving revision bodies and evidence context. Details in [V2 observation and storage](docs/architecture/v2-observation.md).

## Quickstart

Full configuration and troubleshooting: [run and deploy](docs/reference/run-and-deploy.md).

### Docker Compose

```bash
git clone https://github.com/Scisaga/raelyn.git raelyn
cd raelyn

# Copy the config, then set API token, database password, and MinIO credentials.
cp .env.example .env
${EDITOR:-nano} .env

# The Dockerfile copies these pre-downloaded external binaries.
./scripts/dev/download-ffmpeg.sh
./scripts/dev/download-node.sh

# Building the UI needs npm; on Ubuntu / WSL the project scripts can set it up.
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/build-ui.sh

docker compose up --build
```

Open `http://127.0.0.1:8000/`.

```bash
docker compose up -d --build    # background
docker compose logs -f app      # follow
docker compose down             # stop
```

### Local development

```bash
git clone https://github.com/Scisaga/raelyn.git raelyn
cd raelyn

cp .env.example .env
${EDITOR:-nano} .env

# Start local dependencies.
docker compose up -d postgres minio minio-init bgutil-pot

# Prepare Python, Node/npm, and ffmpeg.
./scripts/dev/bootstrap-python.sh
./scripts/dev/bootstrap-node-wsl.sh
./scripts/dev/download-ffmpeg.sh

./scripts/dev/build-ui.sh

# Start the API, role workers, and scheduler.
./scripts/dev/devctl.sh start
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
```

If `python3-venv`, `pip`, or `ensurepip` are missing, run `./scripts/dev/bootstrap-ubuntu.sh` first.

## Turning on AI and the starfield

Docker Compose ships PostgreSQL, MinIO, and the bgutil PO-token provider. It does **not** ship ASR, LLM, or embedding inference — point `.env` at endpoints you can actually reach (any OpenAI-compatible server, Ollama, or Volcengine). See [configuration](docs/reference/configuration.md).

A starfield does not appear from an empty database. It needs, at minimum:

1. usable transcripts in the domain;
2. accepted extracted events that carry an event time;
3. embeddings ready for those events;
4. the `ai`, `embedding`, and `analysis` workers running;
5. one successfully built `ready` snapshot for the current domain.

The main API mounts `/mcp` only when `API_BEARER_TOKEN` is set; without it the Web UI and REST still work, but the agent entry point stays off.

## Agents (MCP)

Raelyn exposes 32 MCP tools and 18 resources over the same semantics as REST — sources, source records, transcripts, domains, observation state, change sets, canonical history, stable stories, structured briefs, evidence context, jobs, and grouped semantic search — plus a small set of safe async actions (`sync_media`, `download_video`, `retranscribe_video`, `generate_brief`).

```jsonc
{
  "mcpServers": {
    "raelyn": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer YOUR_API_BEARER_TOKEN" }
    }
  }
}
```

Search results and web deep links share one object identity, so an agent's answer and your browser tab point at exactly the same star. Design notes: [MCP integration](docs/architecture/mcp.md).

## What Raelyn deliberately does not do

- No multi-tenancy or complex permissions — it is a single-user, self-hosted system.
- No general knowledge graph — extraction currently targets structured market and public events.
- No cross-domain canonical identity; each domain builds its own starfield rather than faking one global universe.
- No real-time guarantee; processing is continuous and asynchronous.
- No investment predictions, trading advice, return analysis, regime labels, or semantic-drift conclusions.
- No decorative stars, invented relations, or animations that could be mistaken for facts.
- AI output never replaces evidence review.

## Documentation

- [Product vision](docs/vision.md) · [Information architecture](docs/product/information-architecture-v2.md) · [Capability gaps](docs/product/capability-gap-v2.md)
- [Architecture overview](docs/architecture/overview.md) · [Observation and storage](docs/architecture/v2-observation.md) · [Data model](docs/architecture/data-model.md) · [Job system](docs/architecture/job-system.md)
- [Semantic starfield](docs/architecture/event-graph-analysis.md) · [UI overview](docs/ui/overview.md) · [Wireframes](docs/ui/wireframes-v2.md)
- [REST API](docs/api/rest.md) · [MCP integration](docs/architecture/mcp.md)
- [Run and deploy](docs/reference/run-and-deploy.md) · [Configuration](docs/reference/configuration.md) · [Docs index](docs/README.md)

## Contributing

Issues and pull requests are welcome. Before a larger change, read [docs/vision.md](docs/vision.md) and [AGENTS.md](AGENTS.md) — the product rules there (evidence first, no faked certainty, no regressing the starfield into a dashboard) are the ones that matter most in review. Backend tests live in `backend/raelyn/tests/`, UI tests in `ui/src/views/__tests__/`.

## Compliance

- This project assumes content you are entitled to access, download, archive, and process.
- Platform cookies, account capabilities, and access limits are the deployer's responsibility.
- You remain responsible for the target platform's terms, copyright, and your local law.

## License

[Apache License 2.0](LICENSE). Licenses for bundled third-party browser components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
