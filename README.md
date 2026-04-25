# Opencloud

Opencloud 是一个用于课程作业和本地实验的 Python 迷你智能体框架。它接入 DeepSeek 的 OpenAI-compatible Chat Completions API，支持 Function Calling 工具调用、类 ReAct 的多轮执行循环、本地工具安全隔离、对话历史持久化、桌面 GUI、语音输入、朗读和 LaTeX 公式渲染。

## 功能概览

- DeepSeek 对话模型接入，支持普通响应和流式输出。
- Function Calling 工具系统，模型可以自动选择工具、生成参数并读取工具结果。
- 多轮工具调用循环：模型可连续观察结果、继续调用工具，直到给出最终答复或达到轮数上限。
- 本地安全工作区：文件读写和脚本执行默认限制在 `workspace` 目录。
- 会话历史持久化：多会话保存在 `chat_history.json`，GUI 可切换、重命名、删除会话。
- 内置工具：文件列表、文件读取、文件写入、安全命令执行、打开文件、网页搜索、网页正文读取、当前时间、天气预报。
- GUI 能力：聊天侧栏、工作区文件列表、流式消息、继续推理授权、Markdown/LaTeX 渲染、语音识别、文本朗读。

## 快速开始

1. 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

2. 配置环境变量，或在项目根目录创建 `.env`：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
$env:BAIDU_SPEECH_API_KEY="你的百度智能云 API Key"
$env:BAIDU_SPEECH_SECRET_KEY="你的百度智能云 Secret Key"
```

`.env` 示例：

```text
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=deepseek-chat
OPENCLAW_WORKSPACE=./workspace
OPENCLAW_HISTORY=./chat_history.json
OPENCLAW_MAX_ROUNDS=8
OPENCLAW_TEMPERATURE=0.2
```

3. 启动 GUI：

```powershell
python gui.py
```

也可以双击 `run.bat`，或启动命令行版本：

```powershell
python main.py
```

4. 可以尝试的指令：

```text
帮我列出 workspace 目录里的文件
读取 notes.txt 并总结
新建一个 hello.txt，写入一句中文问候
现在北京时间几点？
北京未来三天天气怎么样？
搜索 Python pathlib 的用法并总结
```

## 项目结构

```text
.
├─ main.py                         # CLI 入口
├─ gui.py                          # PySide6 桌面 GUI 入口
├─ run.bat                         # Windows 下启动 GUI 的批处理脚本
├─ requirements.txt                # 运行依赖
├─ README.md                       # 项目说明
├─ assets/
│  ├─ microphone.svg               # GUI 麦克风图标
│  └─ speaker.svg                  # GUI 朗读图标
└─ openclaw_mini/
   ├─ __init__.py
   ├─ agent.py                     # 智能体主循环，处理模型响应、工具调用和 Observation
   ├─ config.py                    # 环境变量与 .env 配置读取
   ├─ history.py                   # 对话历史、多会话存储和系统提示词
   ├─ latex_renderer.py            # Markdown 中 LaTeX 公式到 Qt HTML/PNG 的渲染
   ├─ llm.py                       # DeepSeek OpenAI-compatible API 客户端
   ├─ speech.py                    # 百度短语音识别 REST 客户端
   └─ tools/
      ├─ __init__.py
      ├─ base.py                   # Tool / ToolRegistry，负责注册与 schema 转换
      └─ local.py                  # 内置工具实现
```

运行时还会按配置生成：

```text
workspace/                         # 默认安全工作区，工具只允许访问这里
chat_history.json                  # 默认会话历史文件
.env                               # 可选，本地环境变量配置
```

## 核心实现方式

### 配置加载

`openclaw_mini/config.py` 会先读取项目根目录的 `.env`，再从环境变量构造 `Config`。必填项是 `DEEPSEEK_API_KEY`；其他配置都有默认值，例如默认工作区是 `./workspace`，默认历史文件是 `./chat_history.json`，默认最大推理轮数是 `8`。

### 模型客户端

`openclaw_mini/llm.py` 中的 `DeepSeekClient` 直接使用 `urllib.request` 调用：

- `POST /chat/completions`
- `Authorization: Bearer <DEEPSEEK_API_KEY>`
- 请求体中包含 `model`、`messages`、`temperature`
- 如果有工具，则附带 `tools` 和 `tool_choice: auto`

`chat()` 返回一次性响应；`chat_stream()` 解析 SSE 风格的 `data:` 流，逐段回调文本增量，并合并流式返回的 `tool_calls`。

### 智能体循环

`openclaw_mini/agent.py` 中的 `MiniOpenClawAgent` 是主执行器：

1. 把用户消息写入 `ChatHistory`。
2. 带上历史消息和工具 schema 请求模型。
3. 如果模型没有返回 `tool_calls`，直接把模型内容作为最终答复。
4. 如果模型返回工具调用，则解析 JSON 参数，调用本地工具。
5. 将工具返回值序列化为 JSON，作为 `role: tool` 的 Observation 写回消息列表和历史文件。
6. 进入下一轮，让模型基于 Observation 继续推理。
7. 达到 `OPENCLAW_MAX_ROUNDS` 后停止，并在 GUI 中显示“继续”授权按钮。

### 工具注册系统

`openclaw_mini/tools/base.py` 定义了两个核心对象：

- `Tool`：保存工具名、描述、JSON Schema 参数和真正的 Python handler。
- `ToolRegistry`：用装饰器注册工具，提供 `call(name, **kwargs)` 执行工具，并通过 `to_openai_tools()` 转成 OpenAI-compatible tool schema。

内置工具集中定义在 `openclaw_mini/tools/local.py` 的 `build_local_tool_registry(workspace)` 中。启动时 `main.py` 和 `gui.py` 都会调用它，把默认工作区传进去，得到同一套工具。

### 安全边界

本地文件工具通过 `safe_path()` 把用户传入路径拼到 `workspace` 后再 `resolve()`，并检查最终路径必须位于工作区内。这样可以阻止 `../` 之类的路径越界访问。

命令执行工具不会把字符串交给 shell，而是用 `shlex.split()` 拆分参数，并用 `subprocess.run(..., shell=False, cwd=workspace)` 运行。它还会拒绝管道、重定向和控制符，只开放一小组白名单命令。

## 内置工具

### `list_files`

列出 `workspace` 中的文件和目录。

参数：

- `path`：相对 `workspace` 的目录，默认 `.`。
- `recursive`：是否递归列出，默认 `false`。

实现方式：先用 `safe_path()` 校验路径，再根据 `recursive` 选择 `Path.iterdir()` 或 `Path.rglob("*")`，返回每个条目的相对路径、类型和文件大小。

### `read_file`

读取 `workspace` 中的文本文件。

参数：

- `path`：相对 `workspace` 的文件路径。
- `max_chars`：最多返回字符数，默认 `8000`，最大 `100000`。

实现方式：校验路径存在且是文件，然后用 UTF-8 读取，读取失败字符用替代符处理，并返回 `truncated` 标记说明内容是否被截断。

### `write_file`

向 `workspace` 中写入文本文件。

参数：

- `path`：相对 `workspace` 的文件路径。
- `content`：写入内容。
- `append`：是否追加写入，默认 `false`。

实现方式：限制单次写入最多 `200000` 字符，自动创建父目录，然后用 UTF-8 以覆盖或追加模式写入文件。

### `run_shell_command`

在 `workspace` 中执行少量安全命令。

支持：

- `dir` / `ls`：列出工作区顶层文件。
- `pwd`：返回工作区绝对路径。
- `type <file>` / `cat <file>`：读取工作区内文件末尾最多 `8000` 字符。
- `python --version`
- `python <workspace 内的 .py 脚本> [参数...]`

实现方式：解析命令后拒绝 `|`、`&&`、`;`、`>` 等 shell 控制符。简单命令直接用 Python 文件 API 返回结果；Python 脚本执行使用当前解释器 `sys.executable`、`shell=False`、工作目录固定为 `workspace`，超时限制为 `1-30` 秒，stdout/stderr 会截断返回。

### `open_file`

用系统默认应用打开 `workspace` 内的文件或目录。

参数：

- `path`：相对 `workspace` 的文件或目录路径。

实现方式：先通过 `safe_path()` 校验路径。Windows 下调用 `os.startfile()`，其他系统用 `webbrowser.open(path.as_uri())`。

### `web_search`

搜索网页并返回标题、摘要和链接。

参数：

- `query`：搜索关键词或问题。
- `max_results`：返回结果数，默认 `5`，范围 `1-10`。

实现方式：请求 DuckDuckGo HTML 搜索页 `https://duckduckgo.com/html/?q=...`，用自定义 `DuckDuckGoSearchParser` 解析结果标题、URL 和摘要。该工具需要网络环境可访问搜索服务。

### `fetch_url`

读取指定 HTTP/HTTPS URL 的正文内容。

参数：

- `url`：要读取的网页地址。
- `max_chars`：最多返回正文字符数，默认 `12000`，范围 `1000-50000`。

实现方式：只允许 `http` 和 `https`。请求时设置 User-Agent，最多读取约 `2MB`。如果是 HTML，会用 `HTMLParser` 去掉脚本、样式、SVG、canvas 等内容并提取可读文本；如果是 `text/*`、JSON 或 XML，则直接压缩空白后返回。

### `get_current_time`

返回当前日期和时间。

参数：

- `timezone`：可选。为空时使用本机时区；也支持 `UTC`、`UTC+8`、`+08:00`、`Asia/Shanghai`、`Beijing`、`Tokyo`、`New York` 等常见写法。

实现方式：优先匹配内置固定时区别名，再尝试 `zoneinfo.ZoneInfo` 解析 IANA 时区，最后解析 UTC 偏移量。返回 ISO 时间、日期、时间、星期、时区名和 UTC 偏移。

### `get_weather_forecast`

查询当前天气和未来 1-3 天天气预报。

参数：

- `location`：城市、地区或地址，例如 `北京`、`上海`、`Shenzhen`、`New York`。
- `language`：天气描述语言，`zh` 或 `en`，默认 `zh`。
- `days`：返回预报天数，默认 `3`，范围 `1-3`。

实现方式：调用免 API Key 的 `wttr.in` JSON 接口：`https://wttr.in/<location>?format=j1&lang=<language>`。返回解析后的地点信息、观测时间、当前天气，以及每日概览和小时级预报。该工具需要联网。

## GUI 能力

`gui.py` 基于 PySide6 构建桌面界面。启动后会加载配置、创建 `ChatHistory`、`DeepSeekClient`、工具注册器和 `MiniOpenClawAgent`。

- 左侧显示历史会话和工作区文件。
- 中间显示聊天消息、工具事件和错误信息。
- 输入框支持 `Ctrl+Enter` 发送。
- 达到最大工具调用轮数后，可点击“继续”授权额外轮数。
- 支持 Markdown 渲染；`latex_renderer.py` 会把 `$...$` 和 `$$...$$` 公式用 matplotlib mathtext 渲染成 PNG，再嵌入 Qt HTML。
- 配置百度语音后，可点击麦克风按钮录音并转文字。
- 如果系统支持 Qt TextToSpeech，可朗读助手回复。

## 语音识别

`openclaw_mini/speech.py` 实现了百度短语音识别客户端。

工作流程：

1. GUI 用 QtMultimedia 录制 `16kHz`、`16bit`、单声道 PCM。
2. `BaiduSpeechRecognizer` 使用 API Key 和 Secret Key 获取百度 `access_token`。
3. 将 PCM 音频 Base64 编码后发送到百度短语音识别接口。
4. 识别结果先填入输入框，用户确认后再发送给智能体。

相关配置：

```text
BAIDU_SPEECH_API_KEY      百度语音识别 API Key
BAIDU_SPEECH_SECRET_KEY   百度语音识别 Secret Key
BAIDU_SPEECH_DEV_PID      可选，默认 1537，普通话输入法模型
BAIDU_SPEECH_CUID         可选，调用端唯一标识
```

## 环境变量

```text
DEEPSEEK_API_KEY           必填，DeepSeek API Key
DEEPSEEK_MODEL             可选，默认 deepseek-chat
OPENCLAW_WORKSPACE         可选，默认 ./workspace
OPENCLAW_HISTORY           可选，默认 ./chat_history.json
OPENCLAW_MAX_ROUNDS        可选，默认 8
OPENCLAW_TEMPERATURE       可选，默认 0.2
BAIDU_SPEECH_API_KEY       可选，百度语音识别 API Key
BAIDU_SPEECH_SECRET_KEY    可选，百度语音识别 Secret Key
BAIDU_SPEECH_DEV_PID       可选，默认 1537
BAIDU_SPEECH_CUID          可选，默认 opencloud-<本机标识>
```

## 扩展内置工具

新增工具通常只需要修改 `openclaw_mini/tools/local.py`：

1. 在 `build_local_tool_registry(workspace)` 中用 `@registry.register(...)` 注册工具名、描述和 JSON Schema 参数。
2. 实现对应的 Python 函数，并返回可 JSON 序列化的数据。
3. 如果工具需要访问本地文件，优先使用已有的 `safe_path()`。
4. 如果工具会联网或执行命令，给出明确的超时、错误处理和安全限制。

启动时工具 schema 会自动提供给模型，不需要额外修改 Agent。

## 注意事项

- 所有文件工具都只应访问 `workspace`，不要把敏感文件放进该目录。
- `run_shell_command` 不是通用 shell，只适合查看目录、读取文件和运行工作区内的简单 Python 脚本。
- `web_search`、`fetch_url`、`get_weather_forecast` 和百度语音识别都需要网络。
- GUI 依赖 PySide6；LaTeX 公式渲染依赖 matplotlib；IANA 时区解析建议安装 `tzdata`。
