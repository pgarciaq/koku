# Resource Optimization Service (ROS) Integration

This document describes how the ROS-OCP backend integrates with Koku for
resource optimization recommendations.

---

## Overview

ROS-OCP is a sibling service that provides CPU, memory, and GPU rightsizing
recommendations for containerized workloads on OpenShift. It consumes cost
data from Koku and the same CSV reports uploaded by the
[koku-metrics-operator](https://github.com/RedHatInsights/koku-metrics-operator).

**Repository:** [`ros-ocp-backend`](../../../../ros-ocp-backend/) (Go service)

---

## Architecture

```
┌─────────────────────┐
│ koku-metrics-operator│
│ (OpenShift cluster)  │
└──────────┬──────────┘
           │ tar.gz upload (manifest.json + CSV reports)
           ▼
┌─────────────────────┐
│ Ingress Service      │  ← insights-ingress-go (validates, stores in S3)
└──────────┬──────────┘
           │ Kafka: platform.upload.announce
           ▼
┌─────────────────────┐     ┌─────────────────────────┐
│ Koku Listener        │────▶│ Koku Worker (Celery)     │
│ (cost processing)    │     │ Process CSV → summaries  │
└──────────────────────┘     └─────────────────────────┘
           │ Kafka: platform.upload.announce
           ▼
┌─────────────────────┐     ┌─────────────────────────┐
│ ROS Processor        │────▶│ ROS PostgreSQL           │
│ (Go: ingestion +     │     │ daily_container_digests  │
│  recommendation      │     │ gpu_container_digests    │
│  engine)             │     │ recommendation_sets      │
└──────────────────────┘     └─────────────────────────┘
           │
           │ GET /effective_rates/ (cost data)
           ▼
┌─────────────────────┐
│ Koku Masu API        │  ← Returns configured cost rates per cluster/org
└──────────────────────┘
           │
           ▼
┌─────────────────────┐
│ ROS API (Go)         │  ← GET /recommendations/openshift/...
│ Enriches with GPU,   │
│ savings, boxplots    │
└──────────────────────┘
           │
           ▼
┌─────────────────────┐
│ koku-ui (React)      │  ← Optimizations tab
└──────────────────────┘
```

---

## Koku ↔ ROS Integration Points

### 1. Shared Ingress Pipeline

Both Koku and ROS consume the same upload from the operator. The tarball contains:

- **`manifest.json`** — metadata with `files` (Koku: pod/node usage) and
  `resource_optimization_files` (ROS: container-level metrics)
- **Pod/node CSVs** — processed by Koku for cost reporting
- **Container CSVs** — processed by ROS for recommendations

The Kafka topic `platform.upload.announce` notifies both services.

### 2. Effective Rates Endpoint (Koku → ROS)

**Endpoint:** `GET /api/cost-management/v1/effective_rates/`

**Source:** [`masu/api/effective_rates.py`](../../koku/masu/api/effective_rates.py)

ROS calls this endpoint to fetch cost rates for savings estimation. Parameters:

| Parameter | Description |
|-----------|-------------|
| `cluster_id` | OCP cluster UUID |
| `org_id` | Organization ID |

Returns aggregated per-namespace cost data including:

- CPU/memory usage hours and costs
- Infrastructure costs (raw + markup)
- Distributed overhead (platform, worker, storage, network, GPU)
- Configured cost model rates (`cpu_core_usage_per_hour`, `memory_gb_usage_per_hour`, `gpu_cost_per_month`, etc.)
- `currency` — ISO 4217 code from the cost model's `tiered_rates[0].unit` (default `"USD"`)

The SQL query aggregates from `reporting_ocpusagelineitem_daily_summary`
with `data_source IN ('Pod', 'GPU')` to include GPU distribution costs.

### 3. Cost Model Rates Used by ROS

ROS uses these cost model metrics from the `effective_rates` response:

| Rate | ROS Usage |
|------|-----------|
| `cpu_core_usage_per_hour` | CPU savings estimation |
| `memory_gb_usage_per_hour` | Memory savings estimation |
| `gpu_cost_per_month` | GPU savings (idle = full rate, MIG = fractional, time-slicing = shared) |
| Infrastructure costs | Per-namespace overhead apportionment |

### 4. Shared Source/Provider Registration

ROS uses the same `cluster_uuid` registered as a Koku Source/Provider.
The `clusters` table in the ROS database references the same cluster UUID.

---

## ROS Recommendation Engines

### Native Go Engine (default)

The native engine processes CSV data into daily digests, applies exponential
decay weighting, and computes percentile-based recommendations with
configurable terms and safety margins.

Key subsystems:

- **Ingestion pipeline** — CSV parsing → percentile computation → daily digest upsert
- **GPU pipeline** — Separate `gpu_container_digests` table for DCGM profiling metrics
- **Recommendation engine** — Multi-term (short/medium/long) with decay weighting
- **GPU recommender** — Classification, MIG profiling, time-slicing analysis
- **Savings estimator** — Integrates cost data from Koku `effective_rates`

### Kruize (legacy)

When `ROS_USE_NATIVE_ENGINE=false`, the ROS processor delegates to Kruize
Autotune via HTTP for recommendation computation. This path is maintained
for backward compatibility.

---

## Tag Sync (Koku → ROS)

When `ROS_TAGS_ENABLED=true`, Koku pushes enabled OCP namespace tags to ROS.

**Task:** [`masu/processor/ros_tag_sync.py`](../../koku/masu/processor/ros_tag_sync.py)

| Task | Trigger |
|------|---------|
| `sync_ros_ocp_tags` | Tag settings mutations, OCP summarization complete, periodic safety-net |
| `sync_ros_ocp_tags_periodic` | Celery beat every 6 hours (safety-net for missed events) |

**ROS endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/cost-management/v1/internal/tags/sync` | Full-replace sync for one org |
| `GET` | `/api/cost-management/v1/internal/tags/status?org_id=` | Per-org sync freshness |

### Sync Triggers

```
┌─────────────────────┐     immediate      ┌──────────────────┐
│ Tag Settings API    │ ─────────────────▶ │ sync_ros_ocp_tags│
│ (enable/disable/    │                    │ (per tenant)     │
│  mapping change)    │                    └────────┬─────────┘
└─────────────────────┘                             │
                                                    ▼
┌─────────────────────┐     after summary  ┌──────────────────┐
│ OCP summarization   │ ─────────────────▶ │ POST /internal/│
│ (daily new data)    │                    │ tags/sync        │
└─────────────────────┘                    └────────┬─────────┘
                                                    │
┌─────────────────────┐     every 6h       ┌────────▼─────────┐
│ Celery beat         │ ─────────────────▶ │ ros-ocp-backend  │
│ sync_ros_ocp_tags_  │   (all tenants)    │ org_container_   │
│ periodic            │                    │ keys.resolved_tags│
└─────────────────────┘                    └──────────────────┘
```

- **Settings mutations** — Immediate sync when tags are enabled, disabled, or mappings change.
- **Summarization** — After `update_summary_tables` completes for an OCP provider, sync runs for that tenant. New tag values only appear after new data is processed, so daily summarization is sufficient for value freshness.
- **Periodic safety-net** — Every 6 hours, all tenants are queued for sync to recover from network failures or missed events.

### Payload and Full-Replace Semantics

Koku sends all **enabled** OCP tag keys with their **current** values from the latest
billing period (`OCPUsageLineItemDailySummary.all_labels`). Disabled keys are omitted.

```json
{
  "org_id": "1234567",
  "synced_at": "2026-05-25T18:00:00Z",
  "tag_keys": [
    {"key": "environment", "values": ["production", "staging"]},
    {"key": "team", "values": []}
  ],
  "namespace_tags": [
    {"cluster_uuid": "...", "namespace": "payments", "tags": {"environment": "production"}}
  ]
}
```

ROS applies org-scoped **full-replace**:

1. Reset all `org_container_keys.resolved_tags` to `{}` for the org.
2. Apply each `namespace_tags` entry to matching rows.
3. Store `synced_at` and `tag_keys` in `org_tag_sync_metadata`.

Namespaces not in the payload end up with empty tags. The `synced_at` timestamp lets
operators detect staleness via the status endpoint.

### Tag Lifecycle Scenarios

| Scenario | Behavior |
|----------|----------|
| Tag key disappears from pods (still enabled in Settings) | Next summarization + sync sends key in `tag_keys` with fewer/empty `values`; namespace maps omit the key → ROS filters stop matching removed values |
| Tag disabled in Settings | Settings mutation triggers immediate sync; key omitted from payload → full-replace removes it from all containers |
| New tag value appears | Included after next summarization processes line items with the new value |
| Missed sync event | Periodic 6-hour task retries for all tenants |

### Authentication

**Current:** Kubernetes ServiceAccount token validation via TokenReview API.
Koku worker sends `Authorization: Bearer <service-account-token>`; ROS validates via
the in-cluster TokenReview API. Zero-config in-cluster; `ROS_TAGS_DEV_TOKEN` for local dev.

**Future: mTLS** — Planned upgrade for on-prem deployments. Mutual TLS between Koku and
ros-ocp-backend (cert-manager or service-mesh sidecar) will provide bidirectional
authentication and eliminate token rotation concerns. See ros-ocp-backend
[`docs/operations/tag-sync-auth.md`](../../../../ros-ocp-backend/docs/operations/tag-sync-auth.md).

| Koku setting | Default | Description |
|--------------|---------|-------------|
| `ROS_TAGS_ENABLED` | `false` | Feature gate for tag sync task |
| `ROS_OCP_BACKEND_URL` | `http://cost-onprem-ros-api:8000` | ROS API base URL |
| `ROS_TAGS_DEV_TOKEN` | (empty) | Dev bearer token when SA token is unavailable |

---

## Key Environment Variables (ROS)

| Variable | Default | Description |
|----------|---------|-------------|
| `ROS_USE_NATIVE_ENGINE` | `true` | Toggle native vs Kruize engine |
| `KOKU_MASU_URL` | — | Koku Masu service URL for `effective_rates` |
| `ROS_GPU_IDLE_THRESHOLD` | `0.05` | SM activity below = idle |
| `ROS_GPU_UNDERUTIL_THRESHOLD` | `0.30` | SM activity below = underutilized |
| `ROS_GPU_DRAM_MEM_BOUND_THRESHOLD` | `0.60` | DRAM activity above = memory-bound |

---

## Database Schema (ROS)

ROS maintains its own PostgreSQL database (separate from Koku). Key tables:

| Table | Purpose | Partitioned |
|-------|---------|-------------|
| `rh_accounts` | Organization/tenant registry | No |
| `clusters` | Cluster metadata | No |
| `workloads` | Workload (deployment/statefulset) registry | No |
| `daily_container_digests` | Daily CPU/memory percentiles per container | RANGE(bucket_date) |
| `gpu_container_digests` | Daily GPU profiling metrics per container | RANGE(interval_start) |
| `recommendation_sets` | Current active recommendations | No |
| `recommendation_history` | Historical recommendation snapshots | RANGE(recorded_at) |
| `recommendation_quality` | Stability/OOM/adoption metrics | RANGE(measured_at) |
| `container_usage_samples` | Raw samples for boxplots | RANGE(sample_time) |
| `namespace_usage_samples` | Namespace-level raw samples | RANGE(sample_time) |
| `org_recommendation_terms` | Per-org term window overrides | No |
| `recommendation_profiles` | Cost/performance profile definitions | No |

Full schema: [`ros-ocp-backend/docs/database/db-schema`](../../../../ros-ocp-backend/docs/database/db-schema)

---

## On-Prem Deployment

In on-prem mode, both Koku and ROS are deployed via the
[cost-onprem Helm chart](../../../../cost-onprem-chart/). The chart deploys:

- ROS API (`cost-onprem-ros-api`) — serves recommendation queries
- ROS Processor (`cost-onprem-ros-processor`) — ingests data, runs engine

The native engine is the default for on-prem deployments.

---

## Related Koku Files

| File | Role |
|------|------|
| [`masu/api/effective_rates.py`](../../koku/masu/api/effective_rates.py) | Cost rates endpoint consumed by ROS |
| [`csv-processing-ocp.md`](csv-processing-ocp.md) | OCP CSV pipeline (shared with ROS ingress) |
| [`cost-models.md`](cost-models.md) | Cost model system (provides rates to ROS) |
| [`mig-gpu-support.md`](mig-gpu-support.md) | GPU cost metering in Koku (complementary to ROS GPU recommendations) |
