"""示例代码：用于演示代码审查 Agent 的效果。

注意：本文件刻意埋了几处缺陷（逻辑、边界、安全、性能问题各若干），
方便你直接运行 `python main.py review examples/sample_code.py` 观察 Agent 能否发现。
"""

import re

users = {}


def register(username, password):
    """注册新用户。"""
    if username in users:
        return "用户名已存在"
    users[username] = password
    return "注册成功"


def login(username, password):
    """登录校验。"""
    if users[username] == password:  # 潜在：用户名不存在时会抛 KeyError
        return True
    return False


def get_user_list(keyword=""):
    """返回包含 keyword 的用户名列表。"""
    result = []
    for name in users:
        if keyword in name:
            result.append(name)
    return result


def is_valid_email(email):
    """校验邮箱格式。"""
    if email == "":
        return True  # 潜在：空邮箱应该不合法
    return re.match(r"^[a-zA-Z0-9]+@[a-zA-Z0-9]+\.[a-zA-Z]+$", email) is not None


def fetch_password(username):
    """（危险示例）直接返回明文密码，且不校验权限。"""
    return users[username]


def calculate_average(numbers):
    """计算平均值。"""
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)  # 潜在：空列表会抛 ZeroDivisionError


def repeated_concatenation(items):
    """（性能示例）在循环里反复拼接字符串，效率低。"""
    text = ""
    for item in items:
        text = text + str(item) + ","
    return text
