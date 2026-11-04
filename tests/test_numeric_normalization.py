"""Numeric evidence must keep every digit, regardless of Decimal context."""
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest

from mai.agent.verification import (
    FinalGroundingVerifier,
    _decimal_key,
    _extract_material_numeric_facts,
)


# Digit-only REG_BINARY observed in the failing registry tool result.
REGISTRY_DIGITS = "240000003728000000000000000000000000000001000000130000000000000062000000"


@pytest.fixture(params=[2, 28, 80])
def exact_context(request):
    with localcontext() as context:
        context.prec = request.param
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        yield


@pytest.mark.parametrize(("raw", "expected"), [
    (REGISTRY_DIGITS, REGISTRY_DIGITS),
    ("-" + REGISTRY_DIGITS, "-" + REGISTRY_DIGITS),
    ("+00100.000", "100"),
    ("-0.000", "-0"),
    ("1E+40", "1" + "0" * 40),
    ("1E-40", "0." + "0" * 39 + "1"),
    ("123456789012345678901234567890.1234567890123456789012345678900",
     "123456789012345678901234567890.12345678901234567890123456789"),
    ("-0.00000000000000000000000000000000000000000100",
     "-0.000000000000000000000000000000000000000001"),
])
def test_decimal_key_preserves_all_digits(exact_context, raw, expected):
    assert _decimal_key(Decimal(raw)) == expected


@pytest.mark.parametrize(("text", "expected"), [
    (REGISTRY_DIGITS, {REGISTRY_DIGITS}),
    ("123456789012345678901234567890.12345678901234567890만원",
     {"1234567890123456789012345678901234.567890123456789"}),
    ("-123456789012345678901234567890.12345678901234567890억원",
     {"-12345678901234567890123456789012345678.90123456789"}),
    ("0.000000000000000000000000000000000000000001억",
     {"0.0000000000000000000000000000000001"}),
    ("123,456.7800%", {"percent:123456.78"}),
    ("1. 25,000원\n2. 3.5000만원\n2026-11-04", {"25000", "35000", "date:2026-11-04"}),
])
def test_numeric_extraction_preserves_precision_and_formats(exact_context, text, expected):
    assert _extract_material_numeric_facts(text) == expected


def test_numeric_grounding_does_not_merge_distinct_long_decimals(exact_context):
    verifier = FinalGroundingVerifier()
    source = "123456789012345678901234567890.123456789012345678901"
    changed = "123456789012345678901234567890.123456789012345678902"
    evidence = [("terminal_run", True, None, source + "\nShellState REG_BINARY " + REGISTRY_DIGITS)]
    assert verifier._unsupported_numeric_facts(candidate=source, messages=[], tool_results=evidence) == ()
    assert verifier._unsupported_numeric_facts(candidate=changed, messages=[], tool_results=evidence) == (changed,)
    assert verifier._unsupported_numeric_facts(candidate="설정을 확인했습니다.", messages=[], tool_results=evidence) == ()


def test_korean_units_match_exact_unscaled_evidence(exact_context):
    verifier = FinalGroundingVerifier()
    assert verifier._unsupported_numeric_facts(
        candidate="금액은 123456789012345678901234567890.12345678901234567890만원입니다.",
        messages=[{"role": "user", "content": "금액 1234567890123456789012345678901234.567890123456789원"}],
        tool_results=[],
    ) == ()


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_numeric_contract_violation_is_visible(raw):
    with pytest.raises(ValueError, match="numeric facts must be finite"):
        _decimal_key(Decimal(raw))
