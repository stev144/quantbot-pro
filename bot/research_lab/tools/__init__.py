# claude code changed: new — importing every tool module here is what
# actually populates base.py's _TOOL_REGISTRY (each module's
# @register_tool decorator only runs on import). Anything that calls
# bot.research_lab.tools.base.run_tool() must import this package first
# (not just base.py directly) or the registry will be empty.

from bot.research_lab.tools import dataset_tools, statistical_tools, research_tools, conditional_tools  # noqa: F401  # claude code changed: +conditional_tools, Conditional Hypothesis Integrity fix
from bot.research_lab.tools.base import ToolResult, run_tool  # noqa: F401
