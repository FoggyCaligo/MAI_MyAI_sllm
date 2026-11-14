from __future__ import annotations

import asyncio

import pytest

from mai.llm.models import NativeToolCall
from mai.tools.calculator import CalculatorError, calculator, register_calculator_tools
from mai.tools.registry import ToolRegistry


def test_calculator_handles_stock_return_examples_exactly() -> None:
    assert calculator("70000 - 65100")["result"] == "4900"
    assert calculator("(70000 - 65100) / 65100 * 100")["result"].startswith("7.52688172043010752688")
    assert calculator("67400 - 63200")["result"] == "4200"
    assert calculator("8500 - 7220")["result"] == "1280"


def test_calculator_uses_decimal_arithmetic() -> None:
    assert calculator("0.1 + 0.2")["result"] == "0.3"
    assert calculator("(13610 - 12580) / 12580 * 100")["result"].startswith("8.18759936406995230524")


def test_calculator_rejects_code_and_unsafe_expressions() -> None:
    with pytest.raises(CalculatorError):
        calculator("__import__('os').system('echo nope')")
    with pytest.raises(CalculatorError):
        calculator("[1, 2, 3]")
    with pytest.raises(CalculatorError):
        calculator("2 ** 1000")


def test_calculator_is_available_through_native_registry() -> None:
    registry = ToolRegistry()
    register_calculator_tools(registry)
    assert registry.names() == ("calculator",)

    result = asyncio.run(registry.invoke(NativeToolCall(
        name="calculator",
        arguments={"expression": "23569 + 40930"},
    )))
    assert result == {"expression": "23569 + 40930", "result": "64499"}
