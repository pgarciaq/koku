DELETE FROM {{schema | sqlsafe}}.reporting_ocp_vm_breakdown_p
WHERE usage_start >= {{start_date}}::date
    AND usage_start <= {{end_date}}::date
    AND source_uuid = {{source_uuid}}
;

INSERT INTO {{schema | sqlsafe}}.reporting_ocp_vm_breakdown_p (
    id,
    cluster_id,
    cluster_alias,
    namespace,
    node,
    vm_name,
    usage_start,
    usage_end,
    infrastructure_raw_cost,
    infrastructure_markup_cost,
    cost_model_cpu_cost,
    cost_model_memory_cost,
    cost_model_volume_cost,
    cost_model_gpu_cost,
    cost_model_rate_type,
    cost_model_rate_name,
    source_uuid,
    raw_currency,
    distributed_cost
)
SELECT uuid_generate_v4() as id,
    cluster_id,
    cluster_alias,
    namespace,
    node,
    all_labels->>'vm_kubevirt_io_name' as vm_name,
    usage_start as usage_start,
    usage_start as usage_end,
    sum(infrastructure_raw_cost) as infrastructure_raw_cost,
    sum(infrastructure_markup_cost) as infrastructure_markup_cost,
    sum(cost_model_cpu_cost) as cost_model_cpu_cost,
    sum(cost_model_memory_cost) as cost_model_memory_cost,
    sum(cost_model_volume_cost) as cost_model_volume_cost,
    sum(cost_model_gpu_cost) as cost_model_gpu_cost,
    cost_model_rate_type,
    cost_model_rate_name,
    {{source_uuid}}::uuid as source_uuid,
    max(raw_currency) as raw_currency,
    sum(distributed_cost) as distributed_cost
FROM {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary
WHERE usage_start >= {{start_date}}::date
    AND usage_start <= {{end_date}}::date
    AND source_uuid = {{source_uuid}}
    AND all_labels ? 'vm_kubevirt_io_name'
    AND namespace IS DISTINCT FROM 'Worker unallocated'
    AND namespace IS DISTINCT FROM 'Platform unallocated'
    AND namespace IS DISTINCT FROM 'Network unattributed'
    AND namespace IS DISTINCT FROM 'Storage unattributed'
    AND (
        COALESCE(cost_model_cpu_cost, 0)
        + COALESCE(cost_model_memory_cost, 0)
        + COALESCE(cost_model_volume_cost, 0)
        + COALESCE(cost_model_gpu_cost, 0)
        + COALESCE(distributed_cost, 0)
        + COALESCE(infrastructure_raw_cost, 0)
        + COALESCE(infrastructure_markup_cost, 0)) != 0
GROUP BY usage_start, cluster_id, cluster_alias, namespace, node, vm_name,
         cost_model_rate_type, cost_model_rate_name
;
