CRITICAL_STAGES = [
    "data_refresh",
    "config_write",
    "docker_backtest",
    "result_parse",
    "report_render",
    "grafana_query",
]

STAGE_LABELS = {
    "data_refresh": "market data refresh",
    "config_write": "LEAN config generation",
    "docker_backtest": "LEAN engine backtest",
    "result_parse": "result parsing",
    "report_render": "HTML report rendering",
    "grafana_query": "Grafana datasource query",
}

STAGE_REASON_CODES = {
    "data_refresh": "market_data_source_latency",
    "config_write": "lean_config_generation_latency",
    "docker_backtest": "lean_engine_execution_latency",
    "result_parse": "lean_result_parsing_latency",
    "report_render": "lean_report_render_latency",
    "grafana_query": "grafana_datasource_latency",
}

STAGE_REASON_TEXT = {
    "data_refresh": "真实行情数据准备阶段占比最高，主要受 provider 响应、网络、CSV 规范化和 LEAN daily zip 写入影响。",
    "config_write": "LEAN config 生成阶段占比最高，通常和本地文件系统写入或参数构造有关。",
    "docker_backtest": "LEAN 引擎 Docker 回测阶段占比最高，主要来自容器启动、数据读取、算法 warmup、交易撮合和结果生成。",
    "result_parse": "回测结果解析阶段占比最高，说明 JSON 结果、统计指标或图表数据解析是主要瓶颈。",
    "report_render": "HTML 报告渲染阶段占比最高，通常和回测图表点数或报告生成 I/O 有关。",
    "grafana_query": "Grafana datasource 查询阶段占比最高，说明展示层 SQL 查询或 Grafana 往返耗时是主因。",
}


def _stage_value(stage_timings, stage):
    return float((stage_timings or {}).get(stage) or 0.0)


def critical_path_ms(stage_timings):
    return round(sum(_stage_value(stage_timings, stage) for stage in CRITICAL_STAGES), 3)


def explain_lean_latency(stage_timings):
    values = [(stage, _stage_value(stage_timings, stage)) for stage in CRITICAL_STAGES]
    values = [(stage, duration) for stage, duration in values if duration > 0]
    if not values:
        return {
            "critical_path_ms": 0.0,
            "pipeline_total_ms": 0.0,
            "dominant_stage": None,
            "dominant_stage_ms": 0.0,
            "dominant_stage_share": 0.0,
            "reason_code": "no_latency_data",
            "reason_summary": "没有可用于解释的 LEAN 回测链路耗时数据。",
        }

    total = round(sum(duration for _, duration in values), 3)
    ranked = sorted(values, key=lambda item: item[1], reverse=True)
    dominant_stage, dominant_ms = ranked[0]
    share = dominant_ms / total if total else 0.0

    if share >= 0.4:
        reason_code = STAGE_REASON_CODES[dominant_stage]
        reason_summary = STAGE_REASON_TEXT[dominant_stage]
    else:
        reason_code = "mixed_lean_backtest_latency"
        top_labels = [STAGE_LABELS[stage] for stage, _ in ranked[:2]]
        reason_summary = "延迟不是由单一阶段主导，主要由 " + " 和 ".join(top_labels) + " 共同造成。"

    return {
        "critical_path_ms": total,
        "pipeline_total_ms": total,
        "dominant_stage": dominant_stage,
        "dominant_stage_ms": round(dominant_ms, 3),
        "dominant_stage_share": round(share, 4),
        "reason_code": reason_code,
        "reason_summary": reason_summary,
    }
