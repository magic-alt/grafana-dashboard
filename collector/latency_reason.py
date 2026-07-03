CRITICAL_STAGES = [
    "download_prices",
    "normalize_prices",
    "analysis",
    "store_prices",
    "store_indicators",
    "grafana_query",
    "browser_render",
]

PIPELINE_STAGES = [stage for stage in CRITICAL_STAGES if stage != "browser_render"]

STAGE_LABELS = {
    "download_prices": "Yahoo Finance download",
    "normalize_prices": "per-symbol normalization",
    "analysis": "technical indicator analysis",
    "store_prices": "price upsert",
    "store_indicators": "indicator upsert",
    "grafana_query": "Grafana datasource query",
    "browser_render": "Grafana browser render",
}

STAGE_REASON_CODES = {
    "download_prices": "market_data_source_latency",
    "normalize_prices": "symbol_normalization_latency",
    "analysis": "analysis_cpu_latency",
    "store_prices": "price_storage_latency",
    "store_indicators": "indicator_storage_latency",
    "grafana_query": "grafana_datasource_latency",
    "browser_render": "grafana_browser_render_latency",
}

STAGE_REASON_TEXT = {
    "download_prices": "真实 Yahoo Finance 行情下载阶段占比最高，主要受行情源响应和网络耗时影响。",
    "normalize_prices": "按股票代码拆分和标准化行情数据阶段占比最高，通常和 symbol 数量及返回数据形状有关。",
    "analysis": "技术指标分析阶段占比最高，主要来自 MA、波动率、回撤和可见 CPU 负载计算。",
    "store_prices": "价格数据写入 Postgres 阶段占比最高，通常和 upsert 行数、索引维护和数据库 I/O 有关。",
    "store_indicators": "指标数据写入 Postgres 阶段占比最高，通常和指标行数、索引维护和数据库 I/O 有关。",
    "grafana_query": "Grafana datasource 查询阶段占比最高，说明展示层 SQL 查询或 datasource 往返耗时是主因。",
    "browser_render": "Grafana 浏览器渲染阶段占比最高，说明前端加载、面板查询和页面绘制是主因。",
}


def _stage_value(stage_timings, stage):
    value = (stage_timings or {}).get(stage)
    return float(value or 0.0)


def pipeline_total_ms(stage_timings):
    return round(sum(_stage_value(stage_timings, stage) for stage in PIPELINE_STAGES), 3)


def critical_path_ms(stage_timings):
    return round(sum(_stage_value(stage_timings, stage) for stage in CRITICAL_STAGES), 3)


def explain_latency(stage_timings):
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
            "reason_summary": "没有可用于解释的阶段耗时数据。",
        }

    total = round(sum(duration for _, duration in values), 3)
    pipeline_total = pipeline_total_ms(stage_timings)
    ranked = sorted(values, key=lambda item: item[1], reverse=True)
    dominant_stage, dominant_ms = ranked[0]
    share = dominant_ms / total if total else 0.0

    if share >= 0.4:
        reason_code = STAGE_REASON_CODES[dominant_stage]
        reason_summary = STAGE_REASON_TEXT[dominant_stage]
    else:
        reason_code = "mixed_path_latency"
        top_labels = [STAGE_LABELS[stage] for stage, _ in ranked[:2]]
        reason_summary = "延迟不是由单一阶段主导，主要由 " + " 和 ".join(top_labels) + " 共同造成。"

    return {
        "critical_path_ms": total,
        "pipeline_total_ms": pipeline_total,
        "dominant_stage": dominant_stage,
        "dominant_stage_ms": round(dominant_ms, 3),
        "dominant_stage_share": round(share, 4),
        "reason_code": reason_code,
        "reason_summary": reason_summary,
    }
