# OpenClaw Mini

一个用于课程作业的 Python 迷你智能体框架，包含 DeepSeek API 接入、Function Calling 工具调用、ReAct 风格执行循环、本地工具安全隔离和对话历史持久化。

## 快速开始

1. 创建环境变量：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

2. 运行命令行智能体：

```powershell
python main.py
```

3. 示例指令：

```text
帮我列出 workspace 目录里的文件
读取 notes.txt 并总结
新建一个 hello.txt，写入一句中文问候
```

## 项目结构

```text
openclaw_mini/
  agent.py          # ReAct / Function Calling 主循环
  config.py         # 配置读取
  history.py        # 对话历史持久化
  llm.py            # DeepSeek OpenAI-compatible API 客户端
  tools/
    base.py         # 工具注册与 JSON Schema 转换
    local.py        # 内置本地工具
main.py             # CLI 入口
workspace/          # 工具允许读写的安全工作目录
```

## 已实现能力

- 接入 DeepSeek Chat Completions API。
- 维护多轮对话历史，并保存到 `chat_history.json`。
- 支持 Function Calling，并允许模型自主选择工具和参数。
- 支持多轮工具调用，直到模型给出最终答复或达到最大轮数。
- 内置 4 个本地工具：`list_files`、`read_file`、`write_file`、`run_shell_command`。
- 工具执行被限制在 `workspace` 目录，并包含异常捕获和友好错误提示。

## 配置项

可以通过环境变量调整：

```text
DEEPSEEK_API_KEY      必填，DeepSeek API Key
DEEPSEEK_MODEL        可选，默认 deepseek-chat
OPENCLAW_WORKSPACE    可选，默认 ./workspace
OPENCLAW_HISTORY      可选，默认 ./chat_history.json
OPENCLAW_MAX_ROUNDS   可选，默认 8
```

## 注意

`run_shell_command` 只允许执行少量安全命令，例如 `dir`、`ls`、`pwd`、`type`、`cat`、`python --version`。如需扩展命令，请在 `openclaw_mini/tools/local.py` 中修改白名单。
