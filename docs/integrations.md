# Project integration contract

The observability platform is intended to be shared by multiple repositories without copying Grafana/Tempo/Loki configuration into each application.

## Common contract

Every deployable process should set the following resource attributes:

```text
service.name=<process-or-worker>
service.namespace=magic-alt
service.version=<git-sha-or-release>
deployment.environment=<local|ci|dev|staging|prod>
project.name=<repository-or-product>
```

Applications send OTLP to the environment's collector/gateway. Local development normally uses:

```text
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

Inside Docker Compose, use `http://otel-collector:4318`. Cloud endpoints should be injected at deployment time.

## Span and metric conventions

Use span names that describe stable operations rather than implementation details:

```text
<domain>.<operation>
```

Examples:

- `rag.retrieve`
- `rag.rerank`
- `agent.tool_call`
- `qlib.feature_materialize`
- `qlib.train`
- `lean.backtest`
- `broker.order_submit`
- `mujoco.env_step`
- `robot.control_cycle`

Recommended metric naming:

```text
<project>_<subsystem>_<measurement>_<unit>
```

Examples: `ragbot_retrieval_duration_seconds`, `qlib_training_runs_total`, `lean_orders_rejected_total`, `mujoco_steps_total`.

Do not put unbounded request IDs, prompt text, stack traces, file paths, trace IDs, order IDs, or arbitrary URLs into metric labels. They belong in logs/traces where cardinality is controlled differently.

## Quant / QLib research

Suggested services:

- `qlib-research-api`
- `qlib-data-worker`
- `qlib-training-worker`
- `qlib-backtest-worker`

Useful telemetry:

- dataset/release freshness and materialization duration;
- feature-generation throughput and failures;
- training duration, queue wait, model family, fold count;
- IC/RankIC and backtest output as domain/evaluation records rather than high-cardinality infrastructure metrics;
- artifact promotion/rejection events;
- database/cache/object-store dependency latency;
- trace linkage from research request → data materialization → train → validate → backtest → artifact release.

Use `project.name=qlib-platform` so dashboards can aggregate the full research plane while preserving process-level `service.name`.

## LEAN / execution platform

Keep the existing LEAN lab as a reference integration, but production execution should not depend on the local Docker-socket control service.

Suggested services:

- `lean-runner`
- `lean-paper-worker`
- `execution-api`
- `broker-gateway`
- `risk-engine`

Operational signals should cover:

- backtest/paper/live run lifecycle;
- data-feed freshness and gaps;
- engine startup/runtime latency;
- order submit/ack/fill/reject latency;
- broker connectivity and reconnects;
- position/risk-limit violations;
- workflow/run IDs in traces and structured logs.

Execution telemetry must distinguish business outcomes from platform health: an intentionally rejected order is not necessarily a system error, while broker timeout or stale market data is.

## RAG / AI agents

Suggested services:

- `ragbot-api`
- `ragbot-ingestion-worker`
- `ragbot-retrieval-worker`
- `agent-orchestrator`

Trace the end-to-end request through routing, retrieval, reranking, model/tool calls, synthesis, verification, and persistence.

Useful attributes/log fields include provider/model, tool name, retrieval strategy, tenant-safe document identifiers, token counts, queue wait, retry count, cache hit, and evaluation outcome. Prompt/document contents should not be emitted by default; use explicit redaction/sampling policies for debugging data.

Key metrics include:

- request and stage latency histograms;
- model/tool error and retry rates;
- retrieval result count and no-hit rate;
- ingestion queue depth/age and DLQ count;
- token and cost counters;
- evaluation pass rates maintained in a low-cardinality form.

Use trace correlation to connect model latency with vector/database/network dependencies instead of building isolated dashboards per component.

## MuJoCo / robotics

Simulation and real-time control have different telemetry constraints.

### MuJoCo training

Suggested services:

- `mujoco-trainer`
- `mujoco-evaluator`
- `simulation-orchestrator`

Capture environment throughput, step/reset duration, GPU/CPU utilization, rollout/update timing, checkpoint lifecycle, reward/evaluation aggregates, and failure/restart events. Avoid a metric series per simulated environment when running hundreds or thousands of environments; aggregate by workload/configuration and sample traces when deep diagnosis is needed.

### Robot/servo systems

For real-time control, do not synchronously export network telemetry from the hard real-time loop. Record bounded local counters/ring-buffer events and export them asynchronously.

Useful signals include control-cycle jitter, missed deadlines, EtherCAT state and distributed-clock error, drive/device faults, bus error counters, command/feedback latency, current/velocity/position limit events, thermal/voltage state, and software/firmware versions.

High-rate waveform data should go to purpose-built capture/storage and be linked from an incident/run ID; it should not become millions of Prometheus time series.

## Dashboard ownership

Create dashboards around operational responsibilities rather than repositories alone:

- fleet/platform overview;
- application RED/USE dashboards;
- dependency dashboards;
- research/training workflow dashboards;
- execution/risk dashboards;
- agent quality/cost dashboards;
- robotics timing/fault dashboards.

Reusable dashboards should accept `service.namespace`, `project.name`, `service.name`, and environment variables so the same JSON can serve several projects.

## Integration acceptance criteria

A project is considered integrated when:

1. OTLP data reaches the collector without backend-specific code in the application.
2. All required resource attributes are present.
3. Health/error/latency/saturation signals exist for the critical user journey.
4. Logs can be correlated with trace IDs and traces can identify major dependencies.
5. Metric label cardinality has been reviewed.
6. Sensitive business/prompt/user data is excluded or explicitly redacted.
7. A CI or staging smoke test proves telemetry still arrives after a release.
