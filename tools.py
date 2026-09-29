"""工具注册表：给 Agent 提供「浏览代码」的能力。

提供三类只读工具，覆盖代码审查所需的全部信息获取途径：
  - list_directory : 列出目录内容（可递归）
  - read_file      : 按行号区间读取文件（自动加行号、截断大文件）
  - search_code    : 在代码中搜索指定文本（子串匹配）

所有路径均被限制在工作目录（workspace_root）内，防止路径越界读取任意文件。
"""
from __future__ import annotations

import json
import os
from typing import Callable

# 单文件读取上限（字节）与单次返回行数上限，避免一次性塞入过大内容
MAX_FILE_BYTES = 1024 * 1024          # 1 MB
MAX_READ_LINES = 400                  # 单次最多返回行数
MAX_LIST_ENTRIES = 200                # 单次列目录最多返回条目数
MAX_SEARCH_RESULTS = 50               # 搜索最多返回匹配数

# 递归遍历 / 搜索时跳过这些目录，减少无关噪音
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", ".idea", "dist", "build"}


class Tool:
    """单个工具的定义：名称、描述、JSON Schema 参数、执行函数。"""

    def __init__(self, name: str, description: str, parameters: dict, handler: Callable):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler

    def schema(self) -> dict:
        """返回 OpenAI function-calling 所需的工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """管理全部工具，并统一处理路径安全与结果序列化。"""

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self._tools = {
            "list_directory": Tool(
                name="list_directory",
                description="列出目录下的文件和子目录；若传入的是文件路径则返回该文件的基本信息。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对于工作目录的路径，默认 '.'"},
                        "recursive": {"type": "boolean", "description": "是否递归列出子目录，默认 false"},
                    },
                    "required": ["path"],
                },
                handler=self._list_directory,
            ),
            "read_file": Tool(
                name="read_file",
                description="读取文本文件指定行区间的内容，自动附带行号。用于查看代码。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对于工作目录的文件路径"},
                        "start_line": {"type": "integer", "description": "起始行号（从 1 开始），默认 1"},
                        "end_line": {"type": "integer", "description": "结束行号（含），0 表示读到文件末尾，默认 0"},
                    },
                    "required": ["path"],
                },
                handler=self._read_file,
            ),
            "search_code": Tool(
                name="search_code",
                description="在工作目录内搜索包含指定文本的代码行（子串匹配，忽略大小写）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "要搜索的文本"},
                        "path": {"type": "string", "description": "搜索起点：文件或目录，默认 '.'"},
                        "max_results": {"type": "integer", "description": "最多返回的匹配数，默认 50"},
                    },
                    "required": ["pattern"],
                },
                handler=self._search_code,
            ),
        }

    # ------------------------------------------------------------------ 对外接口

    def schemas(self) -> list:
        """所有工具的 JSON Schema 列表，直接作为 LLM 的 tools 参数。"""
        return [t.schema() for t in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> str:
        """执行一次工具调用，返回 JSON 字符串结果（供模型阅读）。"""
        tool = self._tools.get(name)
        if tool is None:
            return self._ok({"error": "未知工具: %s" % name})
        try:
            result = tool.handler(**arguments)
        except TypeError as e:
            return self._ok({"error": "工具参数错误: %s" % e})
        except Exception as e:  # noqa: BLE001 —— 任何工具异常都以结果形式回传，不中断循环
            return self._ok({"error": str(e)})
        return self._ok(result)

    # ------------------------------------------------------------------ 路径安全

    def _resolve(self, path: str) -> str:
        """把相对路径解析为工作目录内的绝对路径，越界则拒绝。"""
        path = path or "."
        full = os.path.abspath(os.path.join(self.workspace_root, path))
        if full != self.workspace_root and not full.startswith(self.workspace_root + os.sep):
            raise ValueError("路径越界，仅允许访问工作目录内的文件: %s" % path)
        return full

    @staticmethod
    def _ok(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _is_text_file(path: str) -> bool:
        """粗略判断是否为文本文件：读取前若干字节，无 NUL 字节即视为文本。"""
        try:
            with open(path, "rb") as f:
                head = f.read(4096)
        except OSError:
            return False
        return b"\x00" not in head

    # ------------------------------------------------------------------ 工具实现

    def _list_directory(self, path: str = ".", recursive: bool = False) -> dict:
        full = self._resolve(path)
        if os.path.isfile(full):
            return {
                "type": "file",
                "path": path,
                "size": os.path.getsize(full),
            }

        if not os.path.isdir(full):
            raise ValueError("路径不存在: %s" % path)

        entries = []
        if recursive:
            for root, dirs, files in os.walk(full):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for name in files:
                    rel = os.path.relpath(os.path.join(root, name), self.workspace_root)
                    entries.append(rel)
                    if len(entries) >= MAX_LIST_ENTRIES:
                        break
        else:
            for name in sorted(os.listdir(full)):
                p = os.path.join(full, name)
                marker = "/" if os.path.isdir(p) else ""
                entries.append(name + marker)

        return {
            "type": "directory",
            "path": path,
            "count": len(entries),
            "truncated": len(entries) >= MAX_LIST_ENTRIES,
            "entries": entries,
        }

    def _read_file(self, path: str, start_line: int = 1, end_line: int = 0) -> dict:
        full = self._resolve(path)
        if not os.path.isfile(full):
            raise ValueError("文件不存在: %s" % path)

        size = os.path.getsize(full)
        if size > MAX_FILE_BYTES:
            raise ValueError("文件过大（%d 字节），超过 %d 字节上限，请用 search_code 定位关键部分"
                             % (size, MAX_FILE_BYTES))

        with open(full, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        start = max(1, int(start_line or 1))
        end = total if int(end_line or 0) <= 0 else min(int(end_line), total)

        if start > total:
            return {"path": path, "total_lines": total, "start": start, "end": total,
                    "content": "(起始行号超出文件范围)"}

        window = end - start + 1
        truncated = window > MAX_READ_LINES
        end = min(end, start + MAX_READ_LINES - 1)

        numbered = []
        for i in range(start - 1, end):
            numbered.append("%4d | %s" % (i + 1, lines[i].rstrip("\n")))
        content = "\n".join(numbered)

        return {
            "path": path,
            "total_lines": total,
            "start": start,
            "end": end,
            "truncated": truncated,
            "content": content,
        }

    def _search_code(self, pattern: str, path: str = ".", max_results: int = MAX_SEARCH_RESULTS) -> dict:
        if not pattern:
            raise ValueError("搜索内容不能为空")
        full = self._resolve(path)
        needle = pattern.lower()
        limit = max(1, min(int(max_results or MAX_SEARCH_RESULTS), 500))

        matches = []

        def scan_file(filepath: str) -> None:
            if not self._is_text_file(filepath):
                return
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for idx, line in enumerate(f, 1):
                        if needle in line.lower():
                            matches.append({
                                "file": os.path.relpath(filepath, self.workspace_root),
                                "line": idx,
                                "text": line.rstrip("\n")[:200],
                            })
                            if len(matches) >= limit:
                                return
            except OSError:
                return

        if os.path.isfile(full):
            scan_file(full)
        elif os.path.isdir(full):
            for root, dirs, files in os.walk(full):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for name in files:
                    scan_file(os.path.join(root, name))
                    if len(matches) >= limit:
                        break
                if len(matches) >= limit:
                    break
        else:
            raise ValueError("路径不存在: %s" % path)

        return {
            "pattern": pattern,
            "count": len(matches),
            "truncated": len(matches) >= limit,
            "matches": matches,
        }
