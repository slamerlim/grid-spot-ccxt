from decimal import Decimal
from pathlib import Path

import pytest

from grid.config import MAX_CONFIG_BYTES, VENUE_IDS, ConfigError, load_config, parse_config


def test_example_is_disabled_and_explicitly_unconfigured() -> None:
    config = load_config(Path(__file__).parents[1] / "config.example.yaml")
    assert config.live is False
    assert tuple(name for name, _ in config.venues) == VENUE_IDS
    assert all(not venue.enabled for _, venue in config.venues)
    assert config.quote_currency is None
    missing = config.missing_prerequisites()
    assert "grid.spacing" in missing
    assert "grid.max_lots" in missing
    assert "risk.max_quote_spent_global" in missing
    assert "risk.residual_dust_quote_ceiling" in missing
    assert "exit.max_price_deviation" in missing
    assert "resources.event_queue_capacity" in missing
    assert "venues.enabled" in missing


def test_absent_config_uses_only_safe_defaults() -> None:
    config = load_config()
    assert config == parse_config({})
    assert not config.live
    assert config.grid.spacing is None


def test_decimal_string_is_lossless_and_zero_reserve_allowed(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        'grid:\n  spacing: "0.000000000000000000001"\n  quote_reserve: "0.00"\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.grid.spacing == Decimal("0.000000000000000000001")
    assert config.grid.quote_reserve == Decimal("0.00")


@pytest.mark.parametrize(
    "value",
    [1, 0.1, True, False, "NaN", "Infinity", "-1", "+1", "1e-3", " 0.1", "1_000", "0"],
)
def test_invalid_financial_values_are_rejected(value: object) -> None:
    with pytest.raises(ConfigError, match="grid.spacing"):
        parse_config({"grid": {"spacing": value}})


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.1, "1", 2**31])
def test_counts_are_exact_bounded_integers(value: object) -> None:
    with pytest.raises(ConfigError, match="pending_buy_levels"):
        parse_config({"grid": {"pending_buy_levels": value}})


@pytest.mark.parametrize(
    "text",
    [
        "live: false\nlive: true\n",
        "venues:\n  okx:\n    enabled: false\n    enabled: true\n",
        "live: &flag false\n",
        "a: &data [one, two]\nb: *data\n",
        "!!python/object/apply:os.system ['untrusted command']",
        "grid: [",
        "true: false",
        "[not-a-mapping]",
        "",
        "null",
        "---\nlive: false\n---\nlive: true",
    ],
)
def test_unsafe_or_malformed_yaml_is_rejected(text: str, tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize(
    "raw",
    [
        {"secret-value-in-key": "another-secret"},
        {"venues": {"unrecognized-secret": {"enabled": True}}},
        {"venues": {"okx": {"apiKey": "another-secret"}}},  # pragma: allowlist secret - fake input
        {"grid": {"secret-value-in-key": "another-secret"}},
        {"risk": {"secret-value-in-key": "another-secret"}},
        {"exit": {"secret-value-in-key": "another-secret"}},
        {"resources": {"secret-value-in-key": "another-secret"}},
        {"universe": {"secret-value-in-key": "another-secret"}},
    ],
)
def test_unknown_keys_rejected_without_echoing_input(raw: object) -> None:
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    message = str(caught.value)
    assert "secret" not in message
    assert "apiKey" not in message


@pytest.mark.parametrize("value", ["true", 1, None, [], {}])
def test_live_requires_a_boolean(value: object) -> None:
    with pytest.raises(ConfigError, match="config.live"):
        parse_config({"live": value})


def test_enabled_venue_has_explicit_funding_prerequisites() -> None:
    config = parse_config({"venues": {"okx": {"enabled": True}}})
    missing = config.missing_prerequisites()
    assert "venues.enabled" not in missing
    assert "venues.okx.account_id" in missing
    assert "venues.okx.allocated_quote" in missing
    assert "venues.okx.max_quote_spent" in missing


def test_disabled_venue_still_has_validated_fields() -> None:
    with pytest.raises(ConfigError, match="allocated_quote"):
        parse_config({"venues": {"okx": {"enabled": False, "allocated_quote": 10.0}}})


def test_manual_universe_needs_an_allowlist() -> None:
    config = parse_config({"universe": {"mode": "manual"}})
    assert "universe.allow" in config.missing_prerequisites()


@pytest.mark.parametrize("value", ["BTC/USDT", ["BTC/USDT", "BTC/USDT"], [False], ["x\ny"]])
def test_symbol_lists_reject_bad_shapes_and_duplicates(value: object) -> None:
    with pytest.raises(ConfigError, match="universe.allow"):
        parse_config({"universe": {"allow": value}})


@pytest.mark.parametrize(
    "raw",
    [
        {"grid": {"min_price": "10", "max_price": "10"}},
        {"grid": {"min_price": "11", "max_price": "10"}},
        {"exit": {"max_price_deviation": "1"}},
        {"exit": {"price_policy": "market"}},
        {"grid": {"seed_policy": "market"}},
    ],
)
def test_inconsistent_or_unsupported_policies_fail(raw: object) -> None:
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_file_errors_never_include_paths_or_content(tmp_path: Path) -> None:
    sentinel = "DO_NOT_DISCLOSE_THIS_VALUE"
    path = tmp_path / sentinel
    for payload in (None, b"\xff", f'grid: {{spacing: "{sentinel}"}}'.encode()):
        if payload is not None:
            path.write_bytes(payload)
        with pytest.raises(ConfigError) as caught:
            load_config(path)
        assert sentinel not in str(caught.value)


def test_oversized_config_is_rejected_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigError, match="size limit"):
        load_config(path)


def test_directory_is_not_a_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="regular file"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "value",
    ["!!int TOP_SECRET_MARKER", "!!timestamp TOP_SECRET_MARKER", "9999-99-99", "1" * 5000],
)
def test_constructor_errors_are_sanitized(value: str, tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(f"quote_currency: {value}", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "TOP_SECRET_MARKER" not in str(caught.value)
    assert len(str(caught.value)) < 200
