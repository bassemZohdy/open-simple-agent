"""Deterministic example tools for testing."""

from __future__ import annotations

from osa.generic_agent.tool import Tool, ToolResult


class CalculatorTool(Tool):
    """A deterministic calculator tool for testing.

    Supports basic arithmetic: add, subtract, multiply, divide.
    """

    name: str = "calculator"
    description: str = "Performs basic arithmetic operations"

    def execute(self, **kwargs: object) -> ToolResult:
        operation = kwargs.get("operation")
        a = kwargs.get("a")
        b = kwargs.get("b")

        if not all([operation, a is not None, b is not None]):
            return ToolResult(
                success=False,
                output="",
                error="Missing required parameters: operation, a, b",
            )

        try:
            a_val = float(a)  # type: ignore[arg-type]
            b_val = float(b)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ToolResult(success=False, output="", error="Parameters a and b must be numbers")

        operations = {
            "add": a_val + b_val,
            "subtract": a_val - b_val,
            "multiply": a_val * b_val,
            "divide": a_val / b_val if b_val != 0 else None,
        }

        if operation not in operations:
            return ToolResult(success=False, output="", error=f"Unknown operation: {operation}")

        result = operations.get(operation)
        if result is None:
            return ToolResult(success=False, output="", error="Division by zero")

        return ToolResult(success=True, output=str(result))
