# Resource Folders: Rule-Based Classification

**Status:** Draft for review — [COST-8529](https://redhat.atlassian.net/browse/COST-8529)

**Purpose:** This document proposes the backend design for user-defined resource folders ("folders" / "workspaces") with rule-based automatic classification, spanning OpenShift and cloud providers (AWS, Azure, GCP) within an organization.

**Last Updated:** October 29, 2026

---

## Table of Contents

1. [Overview](#overview)
2. [Rule Model (Canonical Format, v1)](#rule-model-canonical-format-v1)
3. [Attribute Catalog (v1)](#attribute-catalog-v1)
4. [Evaluation Semantics](#evaluation-semantics)
5. [Architecture](#architecture)
6. [Data Pipeline Impact](#data-pipeline-impact)
7. [API and UI (Sketch)](#api-and-ui-sketch)
8. [Rollout](#rollout)
9. [Open Questions](#open-questions)

---

## Overview

Each organization can define any number of **folders** and place resources inside them: OpenShift clusters, nodes, namespaces, virtualization VMs, and cloud resources from any number of cloud accounts. Resources that match nothing land in a built-in, undeletable **Unclassified** folder that serves as the triage bucket.

Key design decisions (see sections below for detail):

- **Automatic classification via ordered rules.** Each organization owns one active ruleset: an ordered list of rules mapping attribute predicates to folders. New and changed resources are classified automatically.
- **Manual override.** Administrators can explicitly assign or move individual resources to a folder, bypassing the rules.
- **Single-homing.** Each resource is in exactly one folder at any time, so folder spend always rolls up without double counting.
- **First-match-wins.** Rules evaluate top to bottom; the first matching rule assigns the folder. No match means Unclassified.
- **Explainable, previewable, audited.** Every assignment records which rule matched; rule edits support dry-run impact preview; rulesets are versioned with an audit trail.

## Rule Model (Canonical Format, v1)

The ruleset is stored and exchanged as versioned JSON. This is the **backend contract**: the admin UI builder, the API, and the SQL compiler all target this format. Administrators never write it by hand.

Example ruleset:

```json
{
  "ruleset_version": 1,
  "rules": [
    {
      "id": "rule-team-blue-ocp",
      "name": "Team Blue OCP",
      "folder_id": "11111111-1111-1111-1111-111111111111",
      "match": "all",
      "conditions": [
        {"field": "provider", "op": "eq", "value": "ocp"},
        {"field": "ocp.cluster_name", "op": "in", "value": ["cluster-x", "cluster-y"]}
      ]
    },
    {
      "id": "rule-prod-rds",
      "name": "Prod databases",
      "folder_id": "22222222-2222-2222-2222-222222222222",
      "match": "all",
      "conditions": [
        {"field": "provider", "op": "eq", "value": "aws"},
        {"field": "aws.resource_type", "op": "eq", "value": "RDS"},
        {"field": "aws.tag.env", "op": "eq", "value": "prod"}
      ]
    }
  ]
}
```

Notes on the model:

- Array order **is** the priority; there is no separate priority field and no stop/continue flag in v1 — matching always stops at the first match.
- `match: all` requires every condition to hold (AND); `match: any` requires at least one (OR). Nested groups are out of scope for v1.
- Folders are a separate entity referenced by `folder_id`; Unclassified is implicit (no rule may target it).
- Rule `id` values are unique within the ruleset and are recorded on every assignment for explainability.

### Validation schema

The JSON Schema below is published for API validation and documentation. Structural validation (shape, required fields, operator vocabulary) is enforced by this schema; semantic validation — field names against the attribute catalog, operator-vs-type compatibility, `folder_id` existence, and allowlisted tag/label/annotation keys — is enforced server-side against the catalog (section 3).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://cloud.redhat.com/schemas/cost-management/folder-ruleset/v1",
  "title": "Folder classification ruleset",
  "type": "object",
  "required": ["ruleset_version", "rules"],
  "additionalProperties": false,
  "properties": {
    "ruleset_version": {"const": 1},
    "rules": {"type": "array", "items": {"$ref": "#/$defs/rule"}}
  },
  "$defs": {
    "rule": {
      "type": "object",
      "required": ["id", "name", "folder_id", "match", "conditions"],
      "additionalProperties": false,
      "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "folder_id": {"type": "string", "format": "uuid"},
        "match": {"enum": ["all", "any"]},
        "conditions": {
          "type": "array",
          "minItems": 1,
          "items": {"$ref": "#/$defs/condition"}
        }
      }
    },
    "condition": {
      "type": "object",
      "required": ["field", "op"],
      "additionalProperties": false,
      "properties": {
        "field": {"type": "string", "minLength": 1},
        "op": {
          "enum": ["eq", "ne", "in", "not_in", "prefix", "regex", "exists", "not_exists"]
        },
        "value": true
      },
      "allOf": [
        {
          "if": {"properties": {"op": {"enum": ["exists", "not_exists"]}}},
          "then": {"not": {"required": ["value"]}},
          "else": {"required": ["value"]}
        },
        {
          "if": {"properties": {"op": {"enum": ["in", "not_in"]}}},
          "then": {"properties": {"value": {"type": "array", "minItems": 1}}}
        }
      ]
    }
  }
}
```

Additional server-side validation rules:

- Every `field` must exist in the organization's attribute catalog; parameterized families (`aws.tag.<key>`, `ocp.label.<key>`, `ocp.annotation.<key>`, …) require the key to be allowlisted for that organization.
- Every `op` must be allowed for the field's type (see catalog table); `regex` values must compile.
- Every `folder_id` must reference an existing, non-system folder in the same organization.
- Rule `id` values must be unique within the ruleset.

---

## Attribute Catalog (v1)

Rules match against a typed, namespaced **attribute catalog** — not just tags and labels. Field names are dotted and provider-prefixed. The same catalog drives the UI's field/operator dropdowns, value autocomplete, and the SQL compiler's field allowlist (user-supplied field names are never interpolated into SQL).

| Field | Type | Operators | Source |
|---|---|---|---|
| `provider` | enum (`ocp`, `aws`, `azure`, `gcp`) | `eq`, `in` | Ingestion pipeline (already available) |
| `cloud.account_id` | string | `eq`, `ne`, `in`, `not_in` | Cloud billing data (already available) |
| `cloud.resource_type` | string | `eq`, `ne`, `in`, `not_in` | Cloud billing data (already available) |
| `cloud.region` | string | `eq`, `ne`, `in`, `not_in`, `prefix` | Cloud billing data (already available) |
| `ocp.cluster_uuid` | id | `eq`, `ne`, `in`, `not_in` | Sources / masu (already available) |
| `ocp.cluster_name` | string | `eq`, `ne`, `in`, `not_in`, `prefix`, `regex` | Sources / masu (already available) |
| `ocp.namespace` | string | `eq`, `ne`, `in`, `not_in`, `prefix`, `regex` | Operator payload → summary tables (already available) |
| `ocp.node_name` | string | `eq`, `ne`, `in`, `not_in`, `prefix`, `regex` | Operator payload → summary tables (already available) |
| `ocp.node_role` | enum (`master`, `worker`, `infra`, …) | `eq`, `ne`, `in`, `not_in` | Derived from `node-role.kubernetes.io/*` labels (needs derivation) |
| `ocp.label.<key>` | string | `eq`, `ne`, `in`, `not_in`, `prefix`, `regex`, `exists`, `not_exists` | Operator payload (key allowlisted per org) |
| `ocp.annotation.<key>` | string | `eq`, `ne`, `in`, `not_in`, `prefix`, `regex`, `exists`, `not_exists` | Operator capture (new; key allowlisted per org) |
| `aws.tag.<key>` | string | same as `ocp.label.<key>` | CUR ingestion (key allowlisted per org) |
| `azure.tag.<key>` | string | same as `ocp.label.<key>` | Cloud ingestion (key allowlisted per org) |
| `gcp.label.<key>` | string | same as `ocp.label.<key>` | Cloud ingestion (key allowlisted per org) |

Notes:

- Parameterized families (`aws.tag.<key>`, `ocp.annotation.<key>`, …) are only matchable for keys the organization has allowlisted. This bounds cardinality at capture/ingestion time and keeps the UI's field list finite.
- Cross-provider tag normalization (one `tag.<key>` namespace) is explicitly **out of scope** for v1; rules use provider-prefixed families.
- The catalog is versioned alongside the ruleset format. Adding a field is an end-to-end change: capture → payload → ingestion → summary column → catalog entry → compiler mapping (see section 6).

---

## Evaluation Semantics

- **Ordered first-match.** Rules evaluate in array order against each classifiable resource. The first rule whose conditions hold assigns its `folder_id`; evaluation stops. No match assigns Unclassified.
- **Single-homing.** Each resource is in exactly one folder at any time, so folder-level spend always sums without double counting. A resource "belonging in two places" must be modeled as two orthogonal dimensions (out of scope for v1), not two folders.
- **Deterministic.** The same resource and active ruleset always produce the same assignment. Reordering rules is the only way to change conflict outcomes, and reordering is an audited ruleset change.
- **Explainable.** Every assignment records `matched_rule_id` (or `manual` / `unclassified`), so the UI can always answer "why is this resource in this folder?".
- **Empty ruleset.** A ruleset with zero rules is valid and classifies everything as Unclassified.
- **Manual override wins.** An explicit per-resource assignment bypasses rule evaluation until the override is removed. Exact override granularity follows each pipeline's classification key (TBD per pipeline during implementation).
- **Folder lifecycle.** A folder referenced by any rule cannot be deleted; its rules must be retargeted or removed first. Deleting a folder moves its rule-matched resources to whatever the ruleset then assigns (possibly Unclassified); historical assignments stay pinned to the ruleset version that produced them (see section 5).

---

## Architecture

### Storage (sketch)

- `Folder`: per-organization entity (`id` uuid, `name` unique per org, `description`). One system folder per org represents Unclassified: it cannot be renamed, deleted, or targeted by rules.
- `ClassificationRuleset`: versioned, per-organization document (`version`, `document` JSON per section 2, `created_by`, `created_at`). Exactly one version is active; edits create new versions, and every version is retained for audit and for pinning historical assignments.
- `ResourceAssignment`: materialized `(folder_id, matched_rule_id, ruleset_version)` alongside summarized data (exact placement — columns on summary tables vs. side table — TBD per pipeline during implementation).
- `ManualOverride`: explicit per-resource folder assignments that bypass rule evaluation, recording who assigned them and when.

All entities are strictly per-organization under the existing tenant isolation model.

### Evaluation: compile rules to SQL, evaluate set-based

Rules are **not** evaluated per-resource by an external engine. The active ruleset compiles to a `CASE WHEN` expression applied during summarization (masu), so Postgres/Trino classify whole partitions set-based. Conceptually, per pipeline:

```sql
CASE
  WHEN provider = 'ocp'
       AND cluster_name IN ('cluster-x', 'cluster-y')
    THEN '11111111-1111-1111-1111-111111111111'  -- Team Blue OCP
  WHEN provider = 'aws'
       AND resource_type = 'RDS'
       AND tags ->> 'env' = 'prod'
    THEN '22222222-2222-2222-2222-222222222222'  -- Prod databases
  ELSE NULL  -- Unclassified
END AS folder_id
```

A parallel expression yields `matched_rule_id` for explainability. The compiler maps each catalog field to its source column(s) **per pipeline** — OCP and cloud summary tables carry different columns, so there is one field-to-column mapping per pipeline, all driven by the shared catalog. Manual overrides are applied after rule evaluation (e.g., a left-joined override wins over the computed value).

### Preview (dry-run)

Rule edits are previewed with the **same compiler** before activation: compile the candidate ruleset, run it over recent summarized data, and report affected resources, associated spend, and sample matches per rule. What the admin previews is exactly what activation will compute — no divergent code paths.

### Retroactivity

Rule changes apply **forward-only** by default: already-summarized periods keep the assignments (and ruleset version) they were computed with, so activating a rule never silently rewrites reported history. Reclassifying past periods is an explicit backfill operation over selected periods.

---

## Data Pipeline Impact

Classification is only as expressive as the attributes present at summarization time. Per-field status for the v1 catalog:

| Field(s) | Status | Work needed |
|---|---|---|
| `provider`, `cloud.account_id`, `cloud.resource_type`, `cloud.region` | Already available | None (compiler mapping only) |
| `ocp.cluster_uuid`, `ocp.cluster_name` | Already available | None (compiler mapping only) |
| `ocp.namespace`, `ocp.node_name` | Already available in operator payload / summary tables | None (compiler mapping only) |
| `ocp.node_role` | Derivable | Derive from `node-role.kubernetes.io/*` labels during ingestion; add summary column |
| `ocp.label.<key>` | Available for collected labels | Per-org key allowlist; ensure allowlisted keys reach summary tables |
| `ocp.annotation.<key>` | **New** | Operator must join allowlisted annotation keys (via API server / kube-state-metrics — annotations are not Prometheus labels), add them to the payload, and masu must ingest them into summary tables |
| `aws.tag.<key>`, `azure.tag.<key>`, `gcp.label.<key>` | Available via existing tag handling | Per-org key allowlist; ensure allowlisted keys are matchable at summarization time |

Notes on annotation capture:

- Annotations are free-form and high-cardinality, so capture **must** be restricted to an explicit per-org key allowlist — same pattern as Prometheus label allowlists. Unbounded annotation ingestion is a non-goal.
- The operator change (capture + payload schema), masu ingestion change, and summary-table change must ship together for each newly captured key set; the catalog entry is enabled only once all three are in place.
- Ship v1 with already-available attributes first; annotation capture is the first catalog extension, not a launch blocker.

---

## API and UI (Sketch)

API (exact routes TBD):

- Folder CRUD: list/create/rename/delete folders (system folder excluded from rename/delete).
- Ruleset: get active ruleset, save candidate ruleset (validated), activate, list versions.
- Preview: submit a candidate ruleset, receive per-rule match counts, spend impact, and sample matches.
- Explain: for a resource, return its folder, `matched_rule_id` (or manual/unclassified), and the rule snapshot.
- Overrides: set/remove explicit per-resource assignments.

UI (detail lives in koku-ui):

- Administrators manage folders and rules through a visual rule builder (condition rows: field / operator / value, joined by AND/OR; drag-to-reorder for priority). The builder's output **is** the canonical JSON from section 2 — the backend never accepts any other rule encoding.
- Only the Cost Management Administrator role (or equivalent) can edit; other roles see read-only rules and the per-resource explanation.

---

## Rollout

- **Phase 1 — backend foundation:** folder + ruleset models and API, v1 catalog over already-available attributes, SQL compiler with per-pipeline mappings, materialized assignments, preview endpoint, explainability, versioning/audit.
- **Phase 2 — attribute extensions:** node-role derivation, per-org key allowlists, operator annotation capture with payload + ingestion + summary support.
- **Phase 3 — UI:** rule builder, dry-run preview integration, per-resource explanation views.
- **Explicitly later:** nested folders, folder-level access control, shared/split costs, cross-provider tag normalization, nested condition groups.

---

## Open Questions

1. Flat folders in v1, or nested hierarchy from the start? (Draft assumes flat.)
2. Do folders become access-control boundaries (who can view which folder's costs)? Classification (this design) and access policy should stay separate mechanisms regardless.
3. How are shared resources whose cost spans teams handled — single owner vs. split? (Draft assumes single owner via single-homing.)
4. Exact materialization placement per pipeline (columns on summary tables vs. side table) and override granularity per pipeline classification key.
5. Backfill UX and performance envelope for reclassifying past periods.
