create table {{schema | sqlsafe}}.cte_tag_value_{{uuid | sqlsafe}} as
    SELECT key,
        value,
        li.report_period_id,
        li.namespace,
        li.node
    FROM {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary AS li,
        jsonb_each_text(li.volume_labels) labels
    WHERE li.data_source = 'Storage'
    {% if report_period_ids %}
        AND li.report_period_id IN {{ report_period_ids | inclause }}
    {% endif %}
        AND li.usage_start >= {{start_date}}
        AND li.usage_start <= {{end_date}}
        AND value IS NOT NULL
        AND li.volume_labels ?| (SELECT array_agg(DISTINCT key) FROM {{schema | sqlsafe}}.reporting_enabledtagkeys WHERE enabled=true AND provider_type='OCP')
        AND COALESCE(li.infrastructure_project_raw_cost, 0) = 0
        AND COALESCE(li.infrastructure_raw_cost, 0) = 0
    GROUP BY key, value, li.report_period_id, li.namespace, li.node
;

create index ix_cte_tag_value_{{uuid | sqlsafe}}
    on {{schema | sqlsafe}}.cte_tag_value_{{uuid | sqlsafe}}
       (key, report_period_id, namespace, node)
;


create table {{schema | sqlsafe}}.cte_values_agg_{{uuid | sqlsafe}} as
    SELECT tv.key,
        array_agg(DISTINCT value)::text[] as "values",
        report_period_id,
        namespace,
        node
    FROM {{schema | sqlsafe}}.cte_tag_value_{{uuid | sqlsafe}} tv
    JOIN {{schema | sqlsafe}}.reporting_enabledtagkeys etk
        ON tv.key = etk.key
    WHERE etk.enabled = true
        AND etk.provider_type='OCP'
    GROUP BY tv.key, report_period_id, namespace, node
;

create unique index ix_cte_values_agg_{{uuid | sqlsafe}}
    on {{schema | sqlsafe}}.cte_values_agg_{{uuid | sqlsafe}}
       (key, report_period_id, namespace, node)
;


create table {{schema | sqlsafe}}.cte_distinct_values_agg_{{uuid | sqlsafe}} as
    SELECT v.key,
        array_agg(DISTINCT v."values")::text[] as "values",
        v.report_period_id,
        v.namespace,
        v.node
    FROM (
        SELECT va.key,
            unnest(va."values" || coalesce(ls."values", '{}'::text[]))::text as "values",
            va.report_period_id,
            va.namespace,
            va.node
        FROM {{schema | sqlsafe}}.cte_values_agg_{{uuid | sqlsafe}} AS va
        LEFT JOIN {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary AS ls
            ON va.key = ls.key
                AND va.report_period_id = ls.report_period_id
                AND va.namespace = ls.namespace
                AND va.node = ls.node
    ) as v
    GROUP BY key, report_period_id, namespace, node
;

create unique index ix_cte_distinct_values_agg_{{uuid | sqlsafe}}
    on {{schema | sqlsafe}}.cte_distinct_values_agg_{{uuid | sqlsafe}}
       (key, report_period_id, namespace, node)
;


create table {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}} as
SELECT uuid_generate_v4() as uuid,
    tv.key,
    tv.value,
    array_agg(DISTINCT rp.cluster_id)::text[] as cluster_ids,
    array_agg(DISTINCT rp.cluster_alias)::text[] as cluster_aliases,
    array_agg(DISTINCT tv.namespace)::text[] as namespaces,
    array_agg(DISTINCT tv.node)::text[] as nodes
FROM {{schema | sqlsafe}}.cte_tag_value_{{uuid | sqlsafe}} AS tv
JOIN {{schema | sqlsafe}}.reporting_ocpusagereportperiod AS rp
    ON tv.report_period_id = rp.id
GROUP BY tv.key, tv.value
;

create unique index ix_cte_kv_cluster_agg_{{uuid | sqlsafe}}
    on {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}}
       (key, value)
;


UPDATE {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary AS x
SET values = upd.values
FROM (
    SELECT x.uuid, y.values
    FROM {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary AS x, {{schema | sqlsafe}}.cte_distinct_values_agg_{{uuid | sqlsafe}} AS y
    WHERE y.key = x.key
        AND y.report_period_id = x.report_period_id
        AND y.namespace = x.namespace
        AND y.node = x.node
        AND y.values != x.values
    ORDER BY x.uuid
    FOR UPDATE OF x
) upd
WHERE x.uuid = upd.uuid
;


INSERT INTO {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary (uuid, key, report_period_id, namespace, node, values)
SELECT uuid_generate_v4() as uuid,
    key,
    report_period_id,
    namespace,
    node,
    "values"
FROM {{schema | sqlsafe}}.cte_distinct_values_agg_{{uuid | sqlsafe}} x
WHERE NOT EXISTS (
          SELECT 1
            FROM {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary y
           WHERE y.key = x.key
             AND y.report_period_id = x.report_period_id
             AND y.namespace = x.namespace
             AND y.node = x.node
      )
;


-- Rebuild the catalog columns for the key/value pairs this run touched, from
-- every label summary row whose report period is inside the reporting window
-- (ADR-0353: [last_month_start, this_month_end], selected on
-- report_period_start).
--
-- The previous form set the arrays from cte_kv_cluster_agg, which only holds
-- the single report period of this run. That made the arrays a per-run
-- snapshot: the last cluster to summarize owned any shared key/value, and the
-- pod and volume runs each replaced the other's namespaces/nodes. Reading both
-- label summaries here keeps the two sources additive and lets a cluster age
-- out of the arrays when its report period leaves the window.
create table {{schema | sqlsafe}}.cte_kv_catalog_{{uuid | sqlsafe}} as
SELECT pairs.key,
    pairs.value,
    array_agg(DISTINCT win.cluster_id ORDER BY win.cluster_id)::text[] AS cluster_ids,
    array_agg(DISTINCT win.cluster_alias ORDER BY win.cluster_alias)::text[] AS cluster_aliases,
    array_agg(DISTINCT win.namespace ORDER BY win.namespace)::text[] AS namespaces,
    array_agg(DISTINCT win.node ORDER BY win.node)::text[] AS nodes
FROM {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}} AS pairs
JOIN (
    SELECT pls.key, label_values.value, pls.namespace, pls.node, rp.cluster_id, rp.cluster_alias
    FROM {{schema | sqlsafe}}.reporting_ocpusagepodlabel_summary AS pls
    CROSS JOIN LATERAL unnest(pls."values") AS label_values(value)
    JOIN {{schema | sqlsafe}}.reporting_ocpusagereportperiod AS rp
        ON pls.report_period_id = rp.id
    WHERE pls.key IN (SELECT key FROM {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}})
        AND rp.report_period_start >= {{window_start}}
        AND rp.report_period_start <= {{window_end}}
    UNION ALL
    SELECT vls.key, label_values.value, vls.namespace, vls.node, rp.cluster_id, rp.cluster_alias
    FROM {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary AS vls
    CROSS JOIN LATERAL unnest(vls."values") AS label_values(value)
    JOIN {{schema | sqlsafe}}.reporting_ocpusagereportperiod AS rp
        ON vls.report_period_id = rp.id
    WHERE vls.key IN (SELECT key FROM {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}})
        AND rp.report_period_start >= {{window_start}}
        AND rp.report_period_start <= {{window_end}}
) AS win
    ON win.key = pairs.key
    AND win.value = pairs.value
GROUP BY pairs.key, pairs.value
;

create unique index ix_cte_kv_catalog_{{uuid | sqlsafe}}
    on {{schema | sqlsafe}}.cte_kv_catalog_{{uuid | sqlsafe}}
       (key, value)
;


UPDATE {{schema | sqlsafe}}.reporting_ocptags_values AS ov
   SET cluster_ids = upd.cluster_ids,
       cluster_aliases = upd.cluster_aliases,
       namespaces = upd.namespaces,
       nodes = upd.nodes
FROM (
    SELECT ov.uuid, cat.cluster_ids, cat.cluster_aliases, cat.namespaces, cat.nodes
    FROM {{schema | sqlsafe}}.reporting_ocptags_values AS ov
    JOIN {{schema | sqlsafe}}.cte_kv_catalog_{{uuid | sqlsafe}} AS cat
        ON cat.key = ov.key
        AND cat.value = ov.value
    WHERE (
        cat.cluster_ids IS DISTINCT FROM ov.cluster_ids OR
        cat.cluster_aliases IS DISTINCT FROM ov.cluster_aliases OR
        cat.namespaces IS DISTINCT FROM ov.namespaces OR
        cat.nodes IS DISTINCT FROM ov.nodes
    )
    ORDER BY ov.uuid
    FOR UPDATE OF ov
) upd
WHERE ov.uuid = upd.uuid
;


INSERT INTO {{schema | sqlsafe}}.reporting_ocptags_values (uuid, key, value, cluster_ids, cluster_aliases, namespaces, nodes)
SELECT uuid_generate_v4() as uuid,
    cat.key,
    cat.value,
    cat.cluster_ids,
    cat.cluster_aliases,
    cat.namespaces,
    cat.nodes
FROM {{schema | sqlsafe}}.cte_kv_catalog_{{uuid | sqlsafe}} AS cat
WHERE not exists (
    SELECT 1
    FROM {{schema | sqlsafe}}.reporting_ocptags_values AS rp
    WHERE rp.key = cat.key
        AND rp.value = cat.value
)
ON CONFLICT DO NOTHING
;


-- A touched pair with no cluster left inside the window is no longer part of
-- the catalog. Dropping it also keeps it out of every contained_by filter, which
-- an empty array would otherwise satisfy.
DELETE FROM {{schema | sqlsafe}}.reporting_ocptags_values AS ov
WHERE ov.uuid IN (
    SELECT ov.uuid
    FROM {{schema | sqlsafe}}.reporting_ocptags_values AS ov
    JOIN {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}} AS pairs
        ON pairs.key = ov.key
        AND pairs.value = ov.value
    WHERE NOT EXISTS (
        SELECT 1
        FROM {{schema | sqlsafe}}.cte_kv_catalog_{{uuid | sqlsafe}} AS cat
        WHERE cat.key = ov.key
            AND cat.value = ov.value
    )
    ORDER BY ov.uuid
    FOR UPDATE
)
;


-- We run this SQL in the volume label summary SQL as it is run after
-- the pod summary SQL and we want to make sure we consider both
-- source tables before deleting from the values table.
-- WITH cte_expired_tag_keys AS (
--     SELECT DISTINCT tv.key
--     FROM {{schema | sqlsafe}}.reporting_ocptags_values AS tv
--     LEFT JOIN {{schema | sqlsafe}}.reporting_ocpusagepodlabel_summary AS pls
--         ON tv.key = pls.key
--     LEFT JOIN {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary vls
--         ON tv.key = vls.key
--     WHERE pls.key IS NULL
--         AND vls.key IS NULL
-- )


DELETE FROM {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary AS ls
WHERE uuid IN (
    SELECT uuid FROM {{schema | sqlsafe}}.reporting_ocpstoragevolumelabel_summary AS ls
    WHERE ls.report_period_id IN {{ report_period_ids | inclause }}
    AND EXISTS (
        SELECT 1
        FROM {{schema | sqlsafe}}.reporting_enabledtagkeys AS etk
        WHERE etk.enabled = false
            AND ls.key = etk.key
            AND etk.provider_type='OCP'
    )
    ORDER BY ls.uuid
    FOR UPDATE
)
;


DELETE FROM {{schema | sqlsafe}}.reporting_ocptags_values AS tv
WHERE uuid IN (
    SELECT uuid FROM {{schema | sqlsafe}}.reporting_ocptags_values AS tv
    WHERE EXISTS (
        SELECT 1
        FROM {{schema | sqlsafe}}.reporting_enabledtagkeys AS etk
        WHERE etk.enabled = false
            AND tv.key = etk.key
            AND etk.provider_type='OCP'
    )
    ORDER BY tv.uuid
    FOR UPDATE
)
;


TRUNCATE TABLE {{schema | sqlsafe}}.cte_tag_value_{{uuid | sqlsafe}};
DROP TABLE {{schema | sqlsafe}}.cte_tag_value_{{uuid | sqlsafe}};
TRUNCATE TABLE {{schema | sqlsafe}}.cte_values_agg_{{uuid | sqlsafe}};
DROP TABLE {{schema | sqlsafe}}.cte_values_agg_{{uuid | sqlsafe}};
TRUNCATE TABLE {{schema | sqlsafe}}.cte_distinct_values_agg_{{uuid | sqlsafe}};
DROP TABLE {{schema | sqlsafe}}.cte_distinct_values_agg_{{uuid | sqlsafe}};
TRUNCATE TABLE {{schema | sqlsafe}}.cte_kv_catalog_{{uuid | sqlsafe}};
DROP TABLE {{schema | sqlsafe}}.cte_kv_catalog_{{uuid | sqlsafe}};
TRUNCATE TABLE {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}};
DROP TABLE {{schema | sqlsafe}}.cte_kv_cluster_agg_{{uuid | sqlsafe}};
