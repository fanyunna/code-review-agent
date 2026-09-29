"""配置加载模块。

从环境变量或项目根目录的 ``.env`` 文件中读取 DeepSeek API 配置。
不依赖第三方 dotenv 库，自行解析 ``KEY=VALUE`` 格式，减少安装负担。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# DeepSeek 默认服务地址与模型别名
# ``deepseek-chat`` 始终指向 DeepSeek 最新的对话模型，无需随版本升级修改。
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class ConfigError(Exception):
    """配置缺失或非法时抛出。"""


def _parse_dotenv(path: str) -> dict:
    """解析 .env 文件为 dict，忽略注释行与空行。"""
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # 去掉首尾成对引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    return values


def _load_dotenv_if_present(path: str) -> None:
    """若 .env 存在，将其键值写入环境变量（不覆盖已存在的环境变量）。"""
    for key, value in _parse_dotenv(path).items():
        os.environ.setdefault(key, value)


@dataclass
class Config:
    """一次 Agent 运行所需的全部配置。"""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 0.2
    max_tokens: int = 4096
    max_iterations: int = 15

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量 / .env 读取配置，缺少 API Key 时给出友好提示。"""
        # 依次加载：项目根目录 .env、当前工作目录 .env（后者优先）
        _load_dotenv_if_present(os.path.join(os.path.dirname(__file__), ".env"))
        _load_dotenv_if_present(".env")

        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ConfigError(
                "未找到 DEEPSEEK_API_KEY。\n"
                "  1. 复制 .env.example 为 .env\n"
                "  2. 在 https://platform.deepseek.com/api_keys 创建 Key\n"
                "  3. 填入 .env：DEEPSEEK_API_KEY=sk-xxxx"
            )

        try:
            temperature = float(os.environ.get("DEEPSEEK_TEMPERATURE", "0.2"))
            max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "4096"))
            max_iterations = int(os.environ.get("DEEPSEEK_MAX_ITERATIONS", "15"))
        except ValueError as e:
            raise ConfigError("配置中的数值格式不正确: %s" % e)

        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/"),
            model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip(),
            temperature=temperature,
            max_tokens=max_tokens,
            max_iterations=max_iterations,
        )
