import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ROOT / "grafana" / "dashboards"
DATASOURCES = ROOT / "grafana" / "provisioning" / "datasources"


def test_dashboard_json_is_valid_and_uids_are_unique():
    uids = {}
    for path in sorted(DASHBOARDS.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        uid = payload.get("uid")
        title = payload.get("title")
        assert uid, f"{path} has no stable uid"
        assert title, f"{path} has no title"
        assert uid not in uids, f"duplicate dashboard uid {uid!r}: {uids[uid]} and {path}"
        uids[uid] = path

    assert {"local-stock-market", "stock-observability-lab", "lean-backtest-observability-lab"} <= set(uids)


def test_datasource_uids_are_unique_and_secrets_are_externalized():
    uids = {}
    for path in sorted(DATASOURCES.glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for datasource in payload.get("datasources", []):
            uid = datasource.get("uid")
            assert uid, f"{path} datasource has no uid"
            assert uid not in uids, f"duplicate datasource uid {uid!r}"
            uids[uid] = path

    postgres = yaml.safe_load((DATASOURCES / "postgres.yml").read_text(encoding="utf-8"))["datasources"][0]
    assert postgres["secureJsonData"]["password"] == "$POSTGRES_PASSWORD"
    assert postgres["database"] == "$POSTGRES_DB"
    assert postgres["user"] == "$POSTGRES_USER"

    assert {"stock-postgres", "prometheus", "loki", "tempo", "pyroscope"} <= set(uids)


def test_dashboards_are_managed_as_code():
    payload = yaml.safe_load(
        (ROOT / "grafana" / "provisioning" / "dashboards" / "dashboards.yml").read_text(encoding="utf-8")
    )
    provider = payload["providers"][0]
    assert provider["allowUiUpdates"] is False
    assert provider["disableDeletion"] is True
