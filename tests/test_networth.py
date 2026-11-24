from decimal import Decimal

from app.services.networth import AssetInput, LiabilityInput, compute_net_worth


def test_basic_net_worth_in_aed():
    nw = compute_net_worth(
        [
            AssetInput("cash", "AED", 5_000_00),
            AssetInput("gold", "AED", 12_000_00, cost_basis_minor=10_000_00),
            AssetInput("vehicle", "AED", None, cost_basis_minor=40_000_00),  # falls back to cost
        ],
        [LiabilityInput("auto_loan", "AED", 25_000_00), LiabilityInput("credit_card", "AED", 3_000_00)],
    )
    assert nw.total_assets_minor == 57_000_00
    assert nw.total_liabilities_minor == 28_000_00
    assert nw.net_worth_minor == 29_000_00
    assert nw.assets_by_kind["vehicle"] == 40_000_00
    assert nw.unconverted_currencies == []


def test_fx_conversion_and_missing_rate_reported():
    nw = compute_net_worth(
        [AssetInput("stock", "USD", 1_000_00), AssetInput("crypto", "BTC", 5)],
        [LiabilityInput("mortgage", "USD", 100_00)],
        fx_rates={"USD": Decimal("3.6725")},
    )
    assert nw.total_assets_minor == 367_250
    assert nw.total_liabilities_minor == 36_725
    assert nw.unconverted_currencies == ["BTC"]


def test_empty_and_negative_net_worth():
    assert compute_net_worth([], []).net_worth_minor == 0
    nw = compute_net_worth([], [LiabilityInput("other", "AED", 100)])
    assert nw.net_worth_minor == -100


def test_rounding_is_half_even():
    nw = compute_net_worth([AssetInput("stock", "USD", 1)], [], fx_rates={"USD": Decimal("2.5")})
    assert nw.total_assets_minor == 2  # 2.5 -> 2 (banker's rounding)
