import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "rules.json"


def test_config_parses_and_has_required_sections():
    cfg = json.loads(CONFIG_PATH.read_text())
    assert isinstance(cfg["lag_bytes_warn"], int)
    assert isinstance(cfg["tables_allowed_to_shrink"], list)
    assert isinstance(cfg["max_stale_days_by_table"], dict)
    assert isinstance(cfg["required_endpoints"], list)


def test_shipped_rules_raise_nothing_on_a_fresh_install():
    cfg = json.loads(CONFIG_PATH.read_text())
    assert "default_max_stale_days" not in cfg
    assert cfg["max_stale_days_by_table"] == {}
    assert cfg["required_endpoints"] == []
