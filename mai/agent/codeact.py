"""Constrained CodeAct execution over already-registered MAI tools.

This runtime intentionally does not provide arbitrary Python imports, attribute access,
filesystem access, networking, or process creation. The model may use normal Python
control flow and calculations plus the explicit async tool(name, arguments) bridge.
Actual capabilities remain defined by ToolRegistry and account-specific registration.
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import io
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..tools.registry import ToolArgumentsError, ToolRegistry


class CodeActError(RuntimeError):
    """Base class for CodeAct contract and execution failures."""


class CodeActSyntaxError(CodeActError):
    """Generated code violates the constrained execution contract."""


class CodeActTargetError(CodeActError):
    """A selected execution target is unsupported by the requested tool."""


class CodeActLimitError(CodeActError):
    """A CodeAct execution exceeded a deterministic runtime budget."""


class CodeActInput(BaseModel):
    """Model-selected CodeAct source and execution target."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=12000)


@dataclass(frozen=True, slots=True)
class CodeActToolEvidence:
    name: str
    arguments: dict[str, Any]
    target: str
    ok: bool
    result: Any | None = None
    error_type: str | None = None
    message: str | None = None
    handler_started: bool = True

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "arguments": self.arguments,
            "target": self.target,
            "ok": self.ok,
            "handler_started": self.handler_started,
        }
        if self.ok:
            payload["result"] = self.result
        else:
            payload["error_type"] = self.error_type
            payload["message"] = self.message
        return payload


@dataclass(frozen=True, slots=True)
class CodeActResult:
    stdout: str
    calls: tuple[CodeActToolEvidence, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "calls": [item.as_dict() for item in self.calls],
        }


_ALLOWED_NODES = {
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign,
    ast.If, ast.For, ast.Break, ast.Continue,
    ast.Await, ast.Call, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Subscript, ast.Slice,
    ast.JoinedStr, ast.FormattedValue,
    ast.keyword,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
}

_SAFE_CALLS = {
    "print", "len", "range", "min", "max", "sum", "sorted", "enumerate", "zip",
    "str", "int", "float", "bool", "list", "dict", "tuple", "set", "abs", "round",
    "all", "any",
}

_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in _SAFE_CALLS
    if name != "print"
}

_REMOTE_TARGET_METADATA_KEY = "codeact_execution_target"
_REMOTE_TARGET_METADATA_VALUE = "routed_pc"


def execute_code_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": (
                "Execute constrained Python using only the tools activated with tool_manual. "
                "Inside code, call tools as: await tool('exact_name', {arguments}, is_remote=False). "
                "Set is_remote=True for a connected remote worker on a per-call basis. "
                "The model selects targets from user intent, not text matching. "
                "tool() returns {'ok': true, 'result': ...} or {'ok': false, 'error_type': ..., 'message': ...}. "
                "Do not put is_remote in a tool's original arguments object."
            ),
            "parameters": CodeActInput.model_json_schema(),
        },
    }


def codeact_bindings(registry: ToolRegistry, names: Sequence[str]) -> tuple[dict[str, Any], ...]:
    """Return exact activated schemas adapted only for the CodeAct target contract."""

    bindings: list[dict[str, Any]] = []
    for name in names:
        definition = registry.get(name)
        parameters = definition.input_model.model_json_schema()
        if definition.metadata.get(_REMOTE_TARGET_METADATA_KEY) == _REMOTE_TARGET_METADATA_VALUE:
            properties = dict(parameters.get("properties") or {})
            properties.pop("is_remote", None)
            parameters = {**parameters, "properties": properties}
            required = [item for item in parameters.get("required", []) if item != "is_remote"]
            if required:
                parameters["required"] = required
            else:
                parameters.pop("required", None)
        bindings.append({
            "name": definition.name,
            "description": definition.description,
            "parameters": parameters,
            "remote_target_supported": (
                definition.metadata.get(_REMOTE_TARGET_METADATA_KEY)
                == _REMOTE_TARGET_METADATA_VALUE
            ),
        })
    return tuple(bindings)


class _Validator(ast.NodeVisitor):
    def visit(self, node: ast.AST) -> Any:
        if type(node) not in _ALLOWED_NODES:
            raise CodeActSyntaxError(
                f"CodeAct syntax '{type(node).__name__}' is not allowed"
            )
        return super().visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise CodeActSyntaxError("CodeAct calls must use direct function names")
        if node.func.id not in _SAFE_CALLS and node.func.id not in {"tool", "_codeact_tick"}:
            raise CodeActSyntaxError(
                f"CodeAct function '{node.func.id}' is not available"
            )
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> Any:
        if not isinstance(node.value, ast.Call) or not isinstance(node.value.func, ast.Name):
            raise CodeActSyntaxError("CodeAct await is only allowed for tool(...)")
        if node.value.func.id != "tool":
            raise CodeActSyntaxError("CodeAct await is only allowed for tool(...)")
        self.generic_visit(node)


class _LoopTickTransformer(ast.NodeTransformer):
    def visit_For(self, node: ast.For) -> ast.AST:
        self.generic_visit(node)
        node.body.insert(
            0,
            ast.Expr(
                value=ast.Call(
                    func=ast.Name(id="_codeact_tick", ctx=ast.Load()),
                    args=[],
                    keywords=[],
                )
            ),
        )
        return node


class CodeActExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        activated_tool_names: Sequence[str],
        max_tool_calls: int = 64,
        max_loop_iterations: int = 10000,
        max_stdout_chars: int = 16000,
        timeout_seconds: float = 120,
    ) -> None:
        self.registry = registry
        self.activated_tool_names = frozenset(activated_tool_names)
        self.max_tool_calls = max_tool_calls
        self.max_loop_iterations = max_loop_iterations
        self.max_stdout_chars = max_stdout_chars
        self.timeout_seconds = timeout_seconds

    async def execute(self, request: CodeActInput) -> CodeActResult:
        tree = ast.parse(request.code, mode="exec")
        _Validator().visit(tree)
        tree = _LoopTickTransformer().visit(tree)
        ast.fix_missing_locations(tree)

        calls: list[CodeActToolEvidence] = []
        loop_iterations = 0

        def tick() -> None:
            nonlocal loop_iterations
            loop_iterations += 1
            if loop_iterations > self.max_loop_iterations:
                raise CodeActLimitError(
                    f"CodeAct loop iteration limit exceeded ({self.max_loop_iterations})"
                )

        async def tool(name: str, arguments: Mapping[str, Any], *, is_remote: bool = False) -> dict[str, Any]:
            if not isinstance(name, str) or not name:
                raise CodeActSyntaxError("tool name must be a non-empty string")
            if not isinstance(arguments, Mapping):
                raise CodeActSyntaxError("tool arguments must be a mapping")
            if type(is_remote) is not bool:
                raise CodeActTargetError("is_remote must be a boolean")
            if name not in self.activated_tool_names:
                raise CodeActSyntaxError(
                    f"tool '{name}' is not activated for this CodeAct run"
                )
            if len(calls) >= self.max_tool_calls:
                raise CodeActLimitError(
                    f"CodeAct tool-call limit exceeded ({self.max_tool_calls})"
                )

            definition = self.registry.get(name)
            prepared = dict(arguments)
            routed = (
                definition.metadata.get(_REMOTE_TARGET_METADATA_KEY)
                == _REMOTE_TARGET_METADATA_VALUE
            )
            if "is_remote" in prepared:
                raise CodeActTargetError(
                    "CodeAct tool arguments must not contain is_remote; use execute_code.target"
                )
            if is_remote:
                if not routed:
                    raise CodeActTargetError(
                        f"tool '{name}' does not have a remote worker implementation"
                    )
                prepared["is_remote"] = True
            elif routed:
                prepared["is_remote"] = False

            try:
                value = await definition.invoke(prepared)
            except Exception as exc:
                evidence = CodeActToolEvidence(
                    name=name,
                    arguments=prepared,
                    target="remote" if is_remote else "local",
                    ok=False,
                    error_type=type(exc).__name__,
                    message=str(exc),
                    handler_started=not isinstance(exc, ToolArgumentsError),
                )
                calls.append(evidence)
                return {
                    "ok": False,
                    "error_type": evidence.error_type,
                    "message": evidence.message,
                }

            evidence = CodeActToolEvidence(
                name=name,
                arguments=prepared,
                target="remote" if is_remote else "local",
                ok=True,
                result=value,
                handler_started=True,
            )
            calls.append(evidence)
            return {"ok": True, "result": value}

        wrapped = ast.Module(
            body=[
                ast.AsyncFunctionDef(
                    name="__codeact_main__",
                    args=ast.arguments(
                        posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
                        kw_defaults=[], kwarg=None, defaults=[],
                    ),
                    body=tree.body or [ast.Pass()],
                    decorator_list=[],
                )
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(wrapped)
        namespace: dict[str, Any] = {
            "__builtins__": _SAFE_BUILTINS,
            "tool": tool,
            "print": print,
            "_codeact_tick": tick,
        }
        compiled = compile(wrapped, "<mai-codeact>", "exec")
        exec(compiled, namespace, namespace)
        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                await asyncio.wait_for(
                    namespace["__codeact_main__"](),
                    timeout=self.timeout_seconds,
                )
        except asyncio.TimeoutError as exc:
            raise CodeActLimitError(
                f"CodeAct execution timed out after {self.timeout_seconds:g} seconds"
            ) from exc

        output = stdout.getvalue()
        if len(output) > self.max_stdout_chars:
            raise CodeActLimitError(
                f"CodeAct stdout limit exceeded ({self.max_stdout_chars} characters)"
            )
        return CodeActResult(stdout=output, calls=tuple(calls))
