# Document Parser sevice 

# URL: https://sp-doc-insight.qa.in.spdigital.sg

> **Orchestrator flow**: After `call_rest_api` returns, the response contains `predictions_key`,
> `row_count`, and `sample_rows` — **not** raw map HTML or CSV. Call
> `prepare_temp_table(predictions_key=<key>)` next to create the speedy_temp table, then invoke
> the mapper subagent with the `hex-risk-map` skill, and include `render_table(temp_table_name)`
> in your final answer. See the orchestrator prompt for the full step sequence.
---