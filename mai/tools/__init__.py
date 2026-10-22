"""Native tool implementations and registry."""

from .calculator import CalculatorError, CalculatorInput, calculator, register_calculator_tools
from .code import code_read, code_search, code_symbols, register_code_tools
from .documents import document_read, register_document_tools
from .external import register_external_information_tools
from .filesystem import (
    register_filesystem_read_tools,
    register_filesystem_tools,
    register_upload_scoped_write_tools,
    file_list,
    file_search,
    file_read,
    file_write,
    file_create,
    file_delete,
    file_move,
    file_copy,
)
from .images import ImageAnalyzer, register_image_tools
from .local import register_local_pc_tools, register_readonly_local_tools
from .market import MarketDataError, MarketDataNotFoundError, MarketDataProtocolError, market_data, register_market_tools
from .registry import (
    DuplicateToolError,
    EmptyToolInput,
    ToolArgumentsError,
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
    UnknownToolError,
)
from .terminal import register_terminal_tools, terminal_run
from .time import current_time, register_time_tools
from .web import WebFetchError, WebSearchError, register_web_tools, web_fetch, web_search

__all__ = [
    "CalculatorError",
    "CalculatorInput",
    "DuplicateToolError",
    "EmptyToolInput",
    "ImageAnalyzer",
    "MarketDataError",
    "MarketDataNotFoundError",
    "MarketDataProtocolError",
    "ToolArgumentsError",
    "ToolDefinition",
    "ToolRegistry",
    "ToolRegistryError",
    "UnknownToolError",
    "WebFetchError",
    "WebSearchError",
    "calculator",
    "code_read",
    "code_search",
    "code_symbols",
    "current_time",
    "document_read",
    "file_list",
    "file_search",
    "file_read",
    "file_write",
    "file_create",
    "file_delete",
    "file_move",
    "file_copy",
    "market_data",
    "register_calculator_tools",
    "register_code_tools",
    "register_document_tools",
    "register_external_information_tools",
    "register_filesystem_read_tools",
    "register_filesystem_tools",
    "register_upload_scoped_write_tools",
    "register_image_tools",
    "register_local_pc_tools",
    "register_market_tools",
    "register_readonly_local_tools",
    "register_terminal_tools",
    "register_time_tools",
    "register_web_tools",
    "terminal_run",
    "web_fetch",
    "web_search",
]
