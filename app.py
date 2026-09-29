"""Web 界面入口：零第三方依赖的 HTTP 服务。

运行：
    python app.py             # 默认 http://127.0.0.1:8000
    python app.py --port 8080

只用 Python 标准库 http.server 实现，无需 pip 安装任何东西。
浏览器打开后在输入框填写文件/目录路径，即可在网页中查看审查报告
与工具调用轨迹。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agent import CodeReviewAgent
from config import Config, ConfigError
from llm import DeepSeekClient, LLMError
from tools import ToolRegistry

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PROJECT_DIR, "web")
# 网页中输入相对路径时的基准目录，默认是项目根目录（这样 examples/... 能直接命中）
DEFAULT_WORKSPACE = os.environ.get("WORKSPACE", PROJECT_DIR)


def _resolve(full: str):
    """把绝对路径拆成 (工作目录, 相对目标)。目录→目标是 "."，文件→目标是文件名。"""
    if os.path.isdir(full):
        return full, "."
    return os.path.dirname(full), os.path.basename(full)


class _ClientGone(Exception):
    """客户端在流式过程中断开连接时抛出，用于提前终止生成。"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeReviewAgent/1.0"

    # ---------------- 通用工具 ----------------

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, content_type: str) -> None:
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _emit_sse(self, obj: dict) -> None:
        """向 SSE 连接写入一条事件并立即刷新；客户端断开时抛出 _ClientGone。"""
        try:
            data = ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            raise _ClientGone()

    def log_message(self, format, *args):  # noqa: A002 —— 简化日志，不做 DNS 反查
        sys.stderr.write("[web] %s\n" % (format % args))

    # ---------------- 路由 ----------------

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        elif self.path.startswith("/api/review/stream"):
            self._handle_review_stream()
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/review":
            self._handle_review()
        else:
            self.send_error(404)

    # ---------------- 业务 ----------------

    def _handle_review(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return

        target = str(data.get("path", "")).strip()
        if not target:
            self._send_json(400, {"error": "请提供要审查的文件或目录路径 path"})
            return

        full = target if os.path.isabs(target) else os.path.join(DEFAULT_WORKSPACE, target)
        if not os.path.exists(full):
            self._send_json(404, {"error": "路径不存在: %s" % target})
            return

        try:
            # 每次请求独立构建 Agent，避免多线程共享可变状态
            config = Config.from_env()
            workspace, rel_target = _resolve(full)
            agent = CodeReviewAgent(
                DeepSeekClient(config),
                ToolRegistry(workspace),
                max_iterations=config.max_iterations,
                verbose=False,
            )
            report = agent.run(rel_target)
            self._send_json(200, {"report": report, "steps": agent.trace})
        except ConfigError as e:
            self._send_json(500, {"error": "配置错误: %s" % e})
        except LLMError as e:
            self._send_json(502, {"error": "调用 LLM 失败: %s" % e})
        except Exception as e:  # noqa: BLE001 —— 兜底，避免服务崩溃
            self._send_json(500, {"error": "服务器内部错误: %s" % e})

    def _handle_review_stream(self):
        """SSE 流式审查接口：GET /api/review/stream?path=...。

        事件类型：status / token / tool_call / tool_result / clear / error / done。
        """
        target = (parse_qs(urlparse(self.path).query).get("path") or [""])[0].strip()
        if not target:
            self._send_json(400, {"error": "请提供要审查的路径 path"})
            return
        full = target if os.path.isabs(target) else os.path.join(DEFAULT_WORKSPACE, target)
        if not os.path.exists(full):
            self._send_json(404, {"error": "路径不存在: %s" % target})
            return

        # SSE 响应头：流结束后主动关闭连接，让客户端据此判断流已结束
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        try:
            self._emit_sse({"type": "status", "message": "正在分析代码…"})
            config = Config.from_env()
            workspace, rel_target = _resolve(full)
            agent = CodeReviewAgent(
                DeepSeekClient(config),
                ToolRegistry(workspace),
                max_iterations=config.max_iterations,
                verbose=False,
            )
            agent.run_stream(rel_target, self._emit_sse)
            self._emit_sse({"type": "done"})
        except _ClientGone:
            pass  # 客户端主动断开，静默结束
        except ConfigError as e:
            self._emit_sse({"type": "error", "message": "配置错误: %s" % e})
        except LLMError as e:
            self._emit_sse({"type": "error", "message": "调用 LLM 失败: %s" % e})
        except Exception as e:  # noqa: BLE001
            self._emit_sse({"type": "error", "message": "服务器内部错误: %s" % e})


def main() -> None:
    parser = argparse.ArgumentParser(description="代码审查 Agent Web 界面")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print("代码审查 Agent Web 界面已启动：")
    print("  浏览器访问  http://127.0.0.1:%d" % args.port)
    print("  工作目录    %s" % DEFAULT_WORKSPACE)
    print("  按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
