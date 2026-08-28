"""Deterministic arithmetic tool for model-visible numeric verification."""
from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class CalculatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=500)


class CalculatorError(ValueError):
    """Raised when a calculator expression is unsupported or invalid."""


_BINARY_OPS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.Mod: lambda left, right: left % right,
}


def _decimal_literal(node: ast.Constant, source: str) -> Decimal:
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise CalculatorError("only numeric literals are allowed")
    literal = ast.get_source_segment(source, node)
    if literal is None:
        literal = str(node.value)
    try:
        return Decimal(literal.replace("_", ""))
    except InvalidOperation as exc:
        raise CalculatorError(f"invalid numeric literal: {literal}") from exc


def _evaluate(node: ast.AST, source: str) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, source)
    if isinstance(node, ast.Constant):
        return _decimal_literal(node, source)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate(node.operand, source)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, source)
        right = _evaluate(node.right, source)
        handler = _BINARY_OPS.get(type(node.op))
        if handler is not None:
            try:
                return handler(left, right)
            except (DivisionByZero, InvalidOperation, ZeroDivisionError) as exc:
                raise CalculatorError("invalid arithmetic operation") from exc
        if isinstance(node.op, ast.Pow):
            if right != right.to_integral_value():
                raise CalculatorError("exponent must be an integer")
            exponent = int(right)
            if abs(exponent) > 100:
                raise CalculatorError("absolute exponent must be <= 100")
            try:
                return left ** exponent
            except (InvalidOperation, DivisionByZero) as exc:
                raise CalculatorError("invalid exponentiation") from exc
    raise CalculatorError("unsupported expression; use only numbers, parentheses, +, -, *, /, %, and **")


def calculator(expression: str) -> dict[str, str]:
    """Evaluate a restricted arithmetic expression using decimal arithmetic."""

    source = expression.strip()
    if not source:
        raise CalculatorError("expression must be non-empty")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError("invalid arithmetic expression") from exc

    with localcontext() as context:
        context.prec = 40
        value = _evaluate(tree, source)

    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized == "-0":
        normalized = "0"
    return {"expression": source, "result": normalized}


def register_calculator_tools(
    registry: ToolRegistry,
    *,
    timeout_seconds: float | None = 10,
) -> None:
    registry.add(
        name="calculator",
        description=(
            "Evaluate arithmetic deterministically. Use this instead of mental arithmetic for sums, differences, "
            "products, divisions, percentages, returns, aggregates, and other numeric calculations. Supports only "
            "numbers, parentheses, +, -, *, /, %, and **."
        ),
        input_model=CalculatorInput,
        handler=calculator,
        timeout_seconds=timeout_seconds,
        category="calculation",
    )
