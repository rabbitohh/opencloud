from __future__ import annotations

from openclaw_mini.agent import MiniOpenClawAgent
from openclaw_mini.config import Config
from openclaw_mini.history import ChatHistory
from openclaw_mini.llm import DeepSeekClient
from openclaw_mini.memory import MemoryStore
from openclaw_mini.tools.local import build_local_tool_registry


def main() -> None:
    try:
        config = Config.from_env()
    except RuntimeError as exc:
        print(f"启动失败: {exc}")
        print("你可以复制 .env.example 为 .env，然后填入 DEEPSEEK_API_KEY。")
        return

    config.workspace.mkdir(parents=True, exist_ok=True)

    memory_store = MemoryStore(config.memory_path)
    history = ChatHistory(config.history_path, memory_store=memory_store)
    client = DeepSeekClient(
        api_key=config.deepseek_api_key,
        model=config.model,
        temperature=config.temperature,
    )
    tools = build_local_tool_registry(config.workspace, memory_store=memory_store)
    agent = MiniOpenClawAgent(
        client=client,
        tools=tools,
        history=history,
        max_rounds=config.max_rounds,
    )

    print("Opencloud 已启动。输入 exit / quit 退出。")
    print(f"安全工作目录: {config.workspace}")

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            print("再见。")
            break

        answer = None
        try:
            agent.run_stream(user_input)
        except RuntimeError as exc:
            answer = f"运行失败: {exc}"
        except Exception as exc:
            answer = f"出现未预期错误: {exc}"

        if answer is not None:
            print(f"\nAI: {answer}")


if __name__ == "__main__":
    main()
