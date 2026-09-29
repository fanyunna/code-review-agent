# Code Review Agent · 代码审查 Agent

一个基于 **DeepSeek** 大模型的代码审查 Agent，用于分析代码质量、发现潜在 Bug，并给出改进建议。

本项目**不使用任何 Agent 框架**（LangChain / AutoGen 等），直接调用 LLM 原生 API，手写「模型 → 工具调用 → 结果」的 Agent 循环，从而把 Agent 的核心机制完整地展示出来。

## 功能特性

- ✅ **Agent 循环**：模型自主决定调用哪个工具、读哪些文件，循环执行直到给出最终审查报告
- ✅ **三种工具**：`list_directory`（列目录）、`read_file`（按行号读文件）、`search_code`（搜索代码）
- ✅ **结构化审查报告**：按 🔴严重 / 🟡中等 / 🟢轻微 三级输出问题，含位置、描述、修复建议
- ✅ **命令行 + 交互式 + Web 界面** 三种使用方式
- ✅ **零依赖 Web 界面**：标准库 `http.server` 实现，浏览器里输入路径即可审查，无需 pip 安装
- ✅ **健壮性**：路径越界防护、大文件截断、二进制文件过滤、接口失败重试、友好报错

## 快速开始

### 1. 环境要求

- Python 3.8+
- 依赖仅一个：`requests`（`pip install -r requirements.txt`）

### 2. 配置 API Key

```bash
cp .env.example .env      # Windows 下复制 .env.example 为 .env
```

编辑 `.env`，填入你的 DeepSeek API Key（在 <https://platform.deepseek.com/api_keys> 创建）：

```
DEEPSEEK_API_KEY=sk-你的key
```

### 3. 运行

```bash
# 审查单个文件
python main.py review examples/sample_code.py

# 审查整个目录
python main.py review examples

# 交互式模式（每行输入路径或问题）
python main.py chat
```

运行时会实时打印每一步工具调用过程，方便观察 Agent 的决策链路。

### 4. Web 界面（可选）

无需任何额外依赖，启动后浏览器打开 <http://127.0.0.1:8000>：

```bash
python app.py              # 默认端口 8000
python app.py --port 8080  # 换端口
```

在输入框里填路径（如 `examples/sample_code.py`），点「开始审查」，网页会**实时**展示：工具调用过程逐条刷新，审查报告像 ChatGPT 一样逐字生成（SSE 流式传输）。

## 使用示例

```
$ python main.py review examples/sample_code.py
正在审查：examples/sample_code.py
工作目录：E:\SE\code-review-agent\examples
------------------------------------------------------------

  ┌─ 步骤 1 · 调用工具 read_file
  │   参数: path='sample_code.py', start_line=1, end_line=0
  ┌─ 步骤 2 · 调用工具 search_code
  │   参数: pattern='users[', path='sample_code.py'
------------------------------------------------------------
## 总体评价
...

## 发现的问题
### 🔴 login 函数在用户名不存在时会抛出 KeyError
- **位置**：`sample_code.py:17`
- **描述**：...
- **建议**：...
```

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 接口地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名 |
| `DEEPSEEK_TEMPERATURE` | `0.2` | 采样温度，低值更稳定 |
| `DEEPSEEK_MAX_TOKENS` | `4096` | 单次回复最大 token 数 |
| `DEEPSEEK_MAX_ITERATIONS` | `15` | 工具调用最大轮数 |

也可用命令行参数 `--model`、`--quiet` 临时覆盖。

## 项目结构

```
code-review-agent/
├── main.py          # CLI 入口（review / chat 两个子命令）
├── app.py           # Web 入口（零依赖 http.server）
├── agent.py         # Agent 循环（模型 → 工具调用 → 结果）
├── llm.py           # DeepSeek 客户端（OpenAI 兼容接口 + 重试）
├── tools.py         # 工具注册表：列目录 / 读文件 / 搜索代码
├── prompts.py       # 系统提示词设计
├── config.py        # 配置加载（.env 解析）
├── web/index.html   # Web 前端（自包含 HTML/CSS/JS）
├── examples/        # 示例代码（含刻意缺陷，供演示）
├── Design.md        # 架构设计文档
└── README.md
```

更详细的设计说明见 [Design.md](Design.md)。
