"""代码审查 Agent：实现「模型 → 工具调用 → 结果」的循环。

这是整个项目的核心。一个 Agent 循环由三个动作反复交替构成：
  1. 把对话历史交给 LLM，拿到回复（可能是文字，也可能是工具调用请求）
  2. 若模型请求工具，就真正执行工具，把结果以 tool 消息追加回对话
  3. 如此往复，直到模型不再请求工具、给出最终文字答复为止

本模块没有使用任何 Agent 框架，循环逻辑一目了然。
"""
from __future__ import annotations

from llm import AssistantMessage, DeepSeekClient
from prompts import SYSTEM_PROMPT
from tools import ToolRegistry


class CodeReviewAgent:
    """代码审查 Agent。"""

    def __init__(
        self,
        llm: DeepSeekClient,
        tools: ToolRegistry,
        max_iterations: int = 15,
        verbose: bool = True,
    ):
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.trace = []  # 本次审查的工具调用轨迹（CLI 打印 / Web 界面返回）

    def run(self, target: str) -> str:
        """对 target（相对工作目录的文件或目录路径）执行一次代码审查。"""
        self.trace = []
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_prompt(target)},
        ]

        for step in range(1, self.max_iterations + 1):
            message = self.llm.chat(messages, self.tools.schemas())
            messages.append(message.to_dict())

            # 有工具调用 → 执行并回传结果，继续循环
            if message.tool_calls:
                for call in message.tool_calls:
                    result = self.tools.execute(call.name, call.arguments)
                    self.trace.append(
                        {
                            "step": step,
                            "name": call.name,
                            "arguments": call.arguments,
                            "result": result[:500],
                        }
                    )
                    self._log_tool_call(step, call)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
                continue

            # 无工具调用 → 模型给出最终答复，结束
            return message.content or ""

        return (
            "\n\n> ⚠️ 已达到最大工具调用轮数（%d），审查可能未完成，可调大 DEEPSEEK_MAX_ITERATIONS 重试。"
            % self.max_iterations
        )

    def run_stream(self, target: str, on_event) -> str:
        """流式审查：与 run() 逻辑一致，但通过 on_event(dict) 实时推送进度。

        供 Web SSE 使用。事件类型：
          token        —— 最终报告的文本片段（逐 token 推送）
          tool_call    —— 即将调用工具（含 id/step/name/arguments）
          tool_result  —— 工具执行结果（含 id/step/name/result）
          clear        —— 罕见：一轮先输出正文又调用工具，通知前端清掉多余正文
        """
        self.trace = []
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_prompt(target)},
        ]

        call_seq = 0  # 工具调用序号，供前端把 tool_call 与 tool_result 对应起来
        for step in range(1, self.max_iterations + 1):
            content_parts = []
            tool_calls = []
            for evt in self.llm.chat_stream(messages, self.tools.schemas()):
                if evt["type"] == "delta":
                    content_parts.append(evt["content"])
                    on_event({"type": "token", "content": evt["content"]})
                elif evt["type"] == "tool_calls":
                    tool_calls = evt["tool_calls"]

            if tool_calls:
                if content_parts:
                    on_event({"type": "clear"})
                messages.append(
                    AssistantMessage(
                        content="".join(content_parts) or None, tool_calls=tool_calls
                    ).to_dict()
                )
                for call in tool_calls:
                    call_seq += 1
                    on_event({"type": "tool_call", "id": call_seq, "step": step,
                              "name": call.name, "arguments": call.arguments})
                    result = self.tools.execute(call.name, call.arguments)
                    self.trace.append({
                        "step": step, "name": call.name,
                        "arguments": call.arguments, "result": result[:500],
                    })
                    on_event({"type": "tool_result", "id": call_seq, "step": step,
                              "name": call.name, "result": result[:500]})
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
                continue

            # 无工具调用：最终答复已通过 token 事件实时推送完毕
            return "".join(content_parts)

        return (
            "\n\n> ⚠️ 已达到最大工具调用轮数（%d），审查可能未完成，可调大 DEEPSEEK_MAX_ITERATIONS 重试。"
            % self.max_iterations
        )

    @staticmethod
    def _build_user_prompt(target: str) -> str:
        """根据目标是文件还是目录，给出对应的起始指令。"""
        if target in (".", ""):
            return (
                "请审查当前目录下的代码。\n"
                "请先用 list_directory 浏览目录结构，再挑选关键文件阅读并审查。"
            )
        return (
            "请审查代码文件 `%s`。\n"
            "请先用 read_file 读取该文件内容，再输出审查报告。" % target
        )

    def _log_tool_call(self, step: int, call) -> None:
        """可视化打印每一步工具调用，便于观察 Agent 循环过程。"""
        if not self.verbose:
            return
        args_preview = ", ".join("%s=%r" % (k, v) for k, v in call.arguments.items())
        print("\n  ┌─ 步骤 %d · 调用工具 %s" % (step, call.name))
        print("  │   参数: %s" % (args_preview or "（无）"))
