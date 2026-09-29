"""命令行入口。

用法示例：
  python main.py review examples/sample_code.py   # 审查单个文件
  python main.py review examples                  # 审查整个目录
  python main.py chat                             # 进入交互式模式

首次使用前请配置 .env（参考 .env.example），填入 DeepSeek API Key。
"""
from __future__ import annotations

import argparse
import os
import sys

from agent import CodeReviewAgent
from config import Config, ConfigError
from llm import DeepSeekClient, LLMError
from tools import ToolRegistry


def build_agent(config: Config, workspace_root: str, verbose: bool) -> CodeReviewAgent:
    """根据配置组装 Agent 的各个组件（依赖注入）。"""
    llm = DeepSeekClient(config)
    tools = ToolRegistry(workspace_root)
    return CodeReviewAgent(llm, tools, max_iterations=config.max_iterations, verbose=verbose)


def resolve_target(arg: str):
    """把用户传入的路径解析为 (工作目录, 相对目标)。

    目标为目录 → 工作目录即该目录，目标是相对路径 "."；
    目标为文件 → 工作目录为其父目录，目标是文件名。
    """
    full = os.path.abspath(arg)
    if os.path.isdir(full):
        return full, "."
    return os.path.dirname(full), os.path.basename(full)


def cmd_review(args: argparse.Namespace) -> int:
    """一次性审查模式。"""
    if not os.path.exists(args.path):
        print("错误：路径不存在：%s" % args.path, file=sys.stderr)
        return 1

    config = Config.from_env()
    workspace, target = resolve_target(args.path)
    agent = build_agent(config, workspace, verbose=not args.quiet)

    print("正在审查：%s" % args.path)
    print("工作目录：%s" % workspace)
    print("-" * 60)
    result = agent.run(target)
    print("-" * 60)
    print(result)
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """交互式模式：每行输入一个文件/目录路径或自由提问，输入 exit/quit 退出。"""
    config = Config.from_env()
    workspace = os.path.abspath(args.workspace or ".")
    agent = build_agent(config, workspace, verbose=not args.quiet)

    print("进入交互式代码审查模式（工作目录：%s）" % workspace)
    print("输入文件/目录路径进行审查，或输入自然语言问题；输入 exit 退出。")
    print("-" * 60)

    while True:
        try:
            line = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            break

        full = os.path.abspath(os.path.join(workspace, line))
        if os.path.exists(full):
            w, target = resolve_target(full)
        else:
            w, target = workspace, line

        agent.tools = ToolRegistry(w)  # 工具始终与当前工作目录保持一致

        print("-" * 60)
        print(agent.run(target))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="code-review-agent",
        description="基于 DeepSeek 的代码审查 Agent（原生 API + 自写循环）",
    )
    parser.add_argument("--model", help="覆盖 DEEPSEEK_MODEL")
    parser.add_argument("--quiet", action="store_true", help="不打印工具调用过程")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("review", help="审查指定文件或目录").add_argument("path", help="文件或目录路径")
    chat_parser = sub.add_parser("chat", help="交互式模式")
    chat_parser.add_argument("workspace", nargs="?", help="工作目录，默认当前目录")

    args = parser.parse_args()

    try:
        if args.model:
            os.environ["DEEPSEEK_MODEL"] = args.model
        if args.command == "chat":
            return cmd_chat(args)
        if args.command == "review":
            return cmd_review(args)
        # 无子命令时默认进入交互模式
        args.workspace = "."
        return cmd_chat(args)
    except ConfigError as e:
        print("配置错误：\n%s" % e, file=sys.stderr)
        return 2
    except LLMError as e:
        print("调用 LLM 出错：%s" % e, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
