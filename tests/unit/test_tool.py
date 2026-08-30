"""Tests for the tool domain types, catalog, and example tools."""

import pytest

from osa.generic_agent import (
    CalculatorTool,
    Tool,
    ToolCatalog,
    ToolCategory,
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolTimeoutError,
)


class TestToolDefinition:
    def test_create(self) -> None:
        d = ToolDefinition(name="calculator", description="Arithmetic")
        assert d.name == "calculator"
        assert d.category == ToolCategory.NATIVE
        assert d.enabled is True
        assert d.timeout_seconds is None

    def test_with_capabilities(self) -> None:
        from osa.generic_agent.tool import ToolCapability

        d = ToolDefinition(
            name="search",
            capabilities=[ToolCapability(name="web_search", description="Search the web")],
        )
        assert len(d.capabilities) == 1
        assert d.capabilities[0].name == "web_search"


class TestToolResult:
    def test_success(self) -> None:
        r = ToolResult(success=True, output="42")
        assert r.success is True
        assert r.output == "42"
        assert r.error is None

    def test_failure(self) -> None:
        r = ToolResult(success=False, output="", error="bad input")
        assert r.success is False
        assert r.error == "bad input"


class TestToolError:
    def test_create(self) -> None:
        e = ToolError("calc", "division by zero")
        assert e.tool_name == "calc"
        assert "calc" in str(e)
        assert "division by zero" in str(e)

    def test_with_cause(self) -> None:
        cause = ValueError("oops")
        e = ToolError("calc", "failed", cause=cause)
        assert e.cause is cause


class TestToolTimeoutError:
    def test_create(self) -> None:
        e = ToolTimeoutError("slow-tool", 30.0)
        assert e.tool_name == "slow-tool"
        assert e.timeout_seconds == 30.0
        assert "timed out" in str(e)


class TestToolCatalog:
    def test_register_and_get_definition(self) -> None:
        catalog = ToolCatalog()
        d = ToolDefinition(name="calc")
        catalog.register_definition(d)
        assert catalog.get_definition("calc").name == "calc"

    def test_register_and_get_tool(self) -> None:
        catalog = ToolCatalog()
        tool = CalculatorTool()
        catalog.register_tool(tool)
        assert catalog.get_tool("calculator").name == "calculator"

    def test_get_missing_definition_raises(self) -> None:
        catalog = ToolCatalog()
        with pytest.raises(KeyError, match="Tool definition not found"):
            catalog.get_definition("nonexistent")

    def test_get_missing_tool_raises(self) -> None:
        catalog = ToolCatalog()
        with pytest.raises(KeyError, match="Tool not found"):
            catalog.get_tool("nonexistent")

    def test_list_definitions(self) -> None:
        catalog = ToolCatalog()
        catalog.register_definition(ToolDefinition(name="a"))
        catalog.register_definition(ToolDefinition(name="b"))
        assert len(catalog.list_definitions()) == 2

    def test_contains(self) -> None:
        catalog = ToolCatalog()
        catalog.register_definition(ToolDefinition(name="x"))
        assert "x" in catalog
        assert "y" not in catalog


class TestCalculatorTool:
    def test_add(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="add", a=2, b=3)
        assert result.success is True
        assert result.output == "5.0"

    def test_subtract(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="subtract", a=10, b=4)
        assert result.success is True
        assert result.output == "6.0"

    def test_multiply(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="multiply", a=3, b=7)
        assert result.success is True
        assert result.output == "21.0"

    def test_divide(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="divide", a=10, b=2)
        assert result.success is True
        assert result.output == "5.0"

    def test_divide_by_zero(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="divide", a=1, b=0)
        assert result.success is False
        assert "Division by zero" in result.error

    def test_unknown_operation(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="power", a=2, b=3)
        assert result.success is False
        assert "Unknown operation" in result.error

    def test_missing_parameters(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="add")
        assert result.success is False
        assert "Missing required parameters" in result.error

    def test_non_numeric_parameters(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(operation="add", a="x", b=3)
        assert result.success is False
        assert "numbers" in result.error

    def test_is_tool_instance(self) -> None:
        tool = CalculatorTool()
        assert isinstance(tool, Tool)
        assert tool.name == "calculator"
