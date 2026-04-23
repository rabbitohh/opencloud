from __future__ import annotations

import html
import os
import platform
import queue
import re
import threading
from pathlib import Path

from openclaw_mini.agent import MiniOpenClawAgent
from openclaw_mini.config import Config
from openclaw_mini.history import ChatHistory
from openclaw_mini.llm import DeepSeekClient
from openclaw_mini.tools.local import build_local_tool_registry

try:
    from PySide6.QtCore import Qt, QTimer, QUrl, Signal
    from PySide6.QtGui import QColor, QDesktopServices, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QGraphicsDropShadowEffect,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:
    if exc.name == "PySide6":
        raise SystemExit("PySide6 未安装，请先运行: python -m pip install PySide6") from exc
    raise


FONT = "Microsoft YaHei UI"


class ComposerTextEdit(QTextEdit):
    sendRequested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class OpenClawWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AppWindow")
        self.setWindowTitle("OpenClaw Mini")
        self.resize(1240, 820)
        self.setMinimumSize(980, 680)

        self.agent: MiniOpenClawAgent | None = None
        self.config: Config | None = None
        self.events: queue.Queue[tuple[str, str, bool | None]] = queue.Queue()
        self.file_paths: dict[int, Path] = {}
        self.session_ids: dict[int, str] = {}
        self.is_running = False
        self.current_answer_has_delta = False
        self._refreshing_sessions = False
        self.transient_items: list[dict[str, str]] = []
        self.run_base_items: list[dict[str, str]] | None = None

        self._configure_theme()
        self._build_layout()
        self._load_agent()
        self._refresh_files()

        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self._drain_events)
        self.event_timer.start(80)

    def _configure_theme(self) -> None:
        self.colors = {
            "bg": "#edf3fb",
            "panel": "#fbfdff",
            "panel_alt": "#f7f9fc",
            "panel_inset": "#f7f9fc",
            "panel_inset_alt": "#f7f9fc",
            "line": "#dce6f2",
            "line_soft": "#dce6f2",
            "text": "#0f172a",
            "muted": "#66768e",
            "muted_soft": "#66768e",
            "accent": "#2563eb",
            "accent_dark": "#1d4ed8",
            "accent_soft": "#eaf1ff",
            "accent_tint": "#d9e6ff",
            "danger": "#b42318",
            "danger_soft": "#fff5f4",
        }

        self.setStyleSheet(
            f"""
            QMainWindow#AppWindow {{
                background: {self.colors["bg"]};
            }}
            QWidget#CentralRoot {{
                background: {self.colors["bg"]};
            }}
            QFrame#SidebarCard,
            QFrame#HeroCard,
            QFrame#ChatCard,
            QFrame#ComposerCard,
            QFrame#StatusCard {{
                background: {self.colors["panel"]};
                border: 1px solid {self.colors["line_soft"]};
                border-radius: 28px;
            }}
            QFrame#StatusCard {{
                background: {self.colors["panel_alt"]};
                border: 1px solid {self.colors["line"]};
                border-radius: 22px;
            }}
            QFrame#ListShell,
            QFrame#InputShell {{
                background: {self.colors["panel_inset"]};
                border: 1px solid {self.colors["line"]};
                border-radius: 20px;
            }}
            QFrame#InputShell {{
                background: {self.colors["panel_inset_alt"]};
            }}
            QLabel#TitleLabel {{
                color: {self.colors["text"]};
                font-size: 26px;
                font-weight: 700;
            }}
            QLabel#BrandTitle {{
                color: {self.colors["text"]};
                font-size: 22px;
                font-weight: 700;
            }}
            QLabel#SectionTitle {{
                color: {self.colors["text"]};
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#StatusTitle {{
                color: {self.colors["muted"]};
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#StatusText {{
                color: {self.colors["text"]};
                font-size: 13px;
                line-height: 1.45;
            }}
            QLabel#PillLabel,
            QLabel#StateChip {{
                background: {self.colors["accent_soft"]};
                color: {self.colors["accent_dark"]};
                border: 1px solid {self.colors["accent_tint"]};
                border-radius: 14px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton {{
                border: none;
                border-radius: 18px;
                padding: 10px 16px;
                color: {self.colors["text"]};
                background: #ffffff;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {self.colors["panel_inset"]};
            }}
            QPushButton:pressed {{
                background: {self.colors["line_soft"]};
            }}
            QPushButton:disabled {{
                color: {self.colors["muted_soft"]};
                background: {self.colors["panel_inset"]};
            }}
            QPushButton#PrimaryButton {{
                background: {self.colors["accent"]};
                color: #ffffff;
                font-weight: 700;
            }}
            QPushButton#PrimaryButton:hover {{
                background: {self.colors["accent_dark"]};
            }}
            QPushButton#PrimaryButton:pressed {{
                background: {self.colors["accent_dark"]};
            }}
            QPushButton#PrimaryButton:disabled {{
                background: {self.colors["line"]};
                color: #ffffff;
            }}
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                color: {self.colors["text"]};
                font-size: 13px;
                padding: 2px 0;
            }}
            QListWidget::item {{
                border: none;
                border-radius: 14px;
                padding: 10px 12px;
                margin: 1px 0;
            }}
            QListWidget::item:selected {{
                background: {self.colors["accent_soft"]};
                color: {self.colors["accent_dark"]};
            }}
            QListWidget::item:hover {{
                background: {self.colors["panel_alt"]};
            }}
            QScrollArea#ChatScroll,
            QWidget#MessagesWidget {{
                background: transparent;
                border: none;
            }}
            QFrame#UserBubble {{
                background: {self.colors["accent_soft"]};
                border: 1px solid {self.colors["accent_tint"]};
                border-radius: 20px;
            }}
            QFrame#AssistantBubble {{
                background: {self.colors["panel_inset_alt"]};
                border: 1px solid {self.colors["line"]};
                border-radius: 20px;
            }}
            QFrame#EventBubble {{
                background: {self.colors["panel"]};
                border: 1px solid {self.colors["line_soft"]};
                border-radius: 18px;
            }}
            QFrame#ErrorBubble {{
                background: {self.colors["danger_soft"]};
                border: 1px solid {self.colors["line"]};
                border-radius: 18px;
            }}
            QLabel#BubbleName {{
                color: {self.colors["muted"]};
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#UserBubbleName {{
                color: {self.colors["accent_dark"]};
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#BubbleContent {{
                color: {self.colors["text"]};
                font-size: 14px;
                line-height: 1.6;
            }}
            QLabel#EventContent {{
                color: {self.colors["muted"]};
                font-size: 13px;
                line-height: 1.55;
            }}
            QLabel#ErrorContent {{
                color: {self.colors["danger"]};
                font-size: 13px;
                line-height: 1.55;
            }}
            QTextEdit#ComposerInput {{
                background: transparent;
                border: none;
                color: {self.colors["text"]};
                font-size: 14px;
                selection-background-color: {self.colors["accent_tint"]};
            }}
            QTextEdit#ComposerInput:focus {{
                border: none;
            }}
            QSplitter::handle {{
                background: transparent;
            }}
            QSplitter::handle:horizontal {{
                width: 12px;
            }}
            QSplitter::handle:vertical {{
                height: 16px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 12px;
                margin: 4px 0 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {self.colors["line"]};
                border-radius: 6px;
                min-height: 32px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {self.colors["muted"]};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: none;
                border: none;
                height: 0;
            }}
            """
        )

    def _build_layout(self) -> None:
        central = QWidget(self)
        central.setObjectName("CentralRoot")
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(22, 22, 22, 22)
        root_layout.setSpacing(0)

        main_splitter = QSplitter(Qt.Horizontal, central)
        main_splitter.setChildrenCollapsible(False)
        root_layout.addWidget(main_splitter)

        self.sidebar = self._make_card("SidebarCard", 30)
        self.sidebar.setMinimumWidth(310)
        self.sidebar.setMaximumWidth(390)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(22, 22, 22, 22)
        sidebar_layout.setSpacing(16)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_title = QLabel("OpenClaw Mini")
        brand_title.setObjectName("BrandTitle")
        brand_text.addWidget(brand_title)
        brand_row.addLayout(brand_text, 1)

        pill = QLabel("DESKTOP")
        pill.setObjectName("PillLabel")
        brand_row.addWidget(pill, 0, Qt.AlignTop)
        sidebar_layout.addLayout(brand_row)

        self.status_label = QLabel("正在初始化...")
        self.status_label.hide()

        session_top = QHBoxLayout()
        session_top.setSpacing(12)
        session_labels = QVBoxLayout()
        session_labels.setSpacing(0)
        session_title = QLabel("会话")
        session_title.setObjectName("SectionTitle")
        session_labels.addWidget(session_title)
        session_top.addLayout(session_labels, 1)
        self.new_session_button = QPushButton("新对话")
        self.new_session_button.setObjectName("PrimaryButton")
        self.new_session_button.clicked.connect(self._new_session)
        session_top.addWidget(self.new_session_button, 0, Qt.AlignTop)
        sidebar_layout.addLayout(session_top)

        self.session_list = QListWidget()
        self.session_list.itemSelectionChanged.connect(self._switch_selected_session)
        sidebar_layout.addWidget(self.session_list, 4)

        file_top = QHBoxLayout()
        file_top.setSpacing(8)
        file_labels = QVBoxLayout()
        file_labels.setSpacing(0)
        file_title = QLabel("Workspace")
        file_title.setObjectName("SectionTitle")
        file_labels.addWidget(file_title)
        file_top.addLayout(file_labels, 1)
        self.open_button = QPushButton("打开")
        self.open_button.clicked.connect(self._open_selected_file)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh_files)
        file_top.addWidget(self.open_button)
        file_top.addWidget(self.refresh_button)
        sidebar_layout.addLayout(file_top)

        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(lambda _item: self._open_selected_file())
        sidebar_layout.addWidget(self.file_list, 5)

        main_splitter.addWidget(self.sidebar)

        right_area = QWidget()
        right_layout = QVBoxLayout(right_area)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        hero_card = self._make_card("HeroCard", 30)
        hero_layout = QHBoxLayout(hero_card)
        hero_layout.setContentsMargins(20, 18, 20, 18)
        hero_layout.setSpacing(16)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(0)
        self.chat_title_label = QLabel("对话")
        self.chat_title_label.setObjectName("TitleLabel")
        hero_text.addWidget(self.chat_title_label)
        hero_layout.addLayout(hero_text, 1)

        self.state_chip = QLabel("未连接")
        self.state_chip.setObjectName("StateChip")
        hero_layout.addWidget(self.state_chip, 0, Qt.AlignTop)
        right_layout.addWidget(hero_card)

        content_splitter = QSplitter(Qt.Vertical, right_area)
        content_splitter.setChildrenCollapsible(False)
        right_layout.addWidget(content_splitter, 1)

        chat_card = self._make_card("ChatCard", 30)
        chat_layout = QVBoxLayout(chat_card)
        chat_layout.setContentsMargins(14, 14, 14, 14)
        chat_layout.setSpacing(12)

        chat_top = QHBoxLayout()
        chat_top.setSpacing(12)
        chat_top.addWidget(self._make_text_label("消息流", "SectionTitle"))
        chat_top.addStretch(1)
        chat_layout.addLayout(chat_top)

        chat_shell = self._make_shell("ListShell")
        chat_shell_layout = QVBoxLayout(chat_shell)
        chat_shell_layout.setContentsMargins(18, 18, 18, 18)
        chat_shell_layout.setSpacing(0)
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("ChatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.messages_widget = QWidget()
        self.messages_widget.setObjectName("MessagesWidget")
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.messages_layout.setContentsMargins(20, 18, 20, 26)
        self.messages_layout.setSpacing(18)
        self.chat_scroll.setWidget(self.messages_widget)
        chat_shell_layout.addWidget(self.chat_scroll)
        chat_layout.addWidget(chat_shell, 1)
        content_splitter.addWidget(chat_card)

        composer_card = self._make_card("ComposerCard", 28)
        composer_layout = QVBoxLayout(composer_card)
        composer_layout.setContentsMargins(16, 16, 16, 16)
        composer_layout.setSpacing(12)
        composer_layout.addWidget(self._make_text_label("输入消息", "SectionTitle"))

        input_shell = self._make_shell("InputShell")
        input_layout = QVBoxLayout(input_shell)
        input_layout.setContentsMargins(12, 10, 12, 10)
        input_layout.setSpacing(0)
        self.input_box = ComposerTextEdit()
        self.input_box.setObjectName("ComposerInput")
        self.input_box.setPlaceholderText("")
        self.input_box.setAcceptRichText(False)
        self.input_box.textChanged.connect(self._update_send_button_state)
        self.input_box.sendRequested.connect(self._send_message)
        input_layout.addWidget(self.input_box)
        composer_layout.addWidget(input_shell, 1)

        composer_bottom = QHBoxLayout()
        composer_bottom.setSpacing(12)
        composer_bottom.addStretch(1)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.clicked.connect(self._send_message)
        composer_bottom.addWidget(self.send_button)
        composer_layout.addLayout(composer_bottom)
        content_splitter.addWidget(composer_card)

        main_splitter.addWidget(right_area)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([334, 880])
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 0)
        content_splitter.setSizes([590, 220])

        self._render_current_session()
        self._update_send_button_state()

    def _make_card(self, object_name: str, radius: int) -> QFrame:
        card = QFrame()
        card.setObjectName(object_name)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(15, 23, 42, 24))
        card.setGraphicsEffect(shadow)
        card.setProperty("radius", radius)
        return card

    def _make_shell(self, object_name: str = "ListShell") -> QFrame:
        shell = QFrame()
        shell.setObjectName(object_name)
        shell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return shell

    @staticmethod
    def _make_text_label(text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        return label

    def _load_agent(self) -> None:
        try:
            config = Config.from_env()
        except RuntimeError as exc:
            self.status_label.setText("缺少配置：请在 .env 中填写 DEEPSEEK_API_KEY")
            self.state_chip.setText("等待配置")
            self._set_transient_error(f"启动失败：{exc}")
            QMessageBox.critical(self, "OpenClaw Mini", f"启动失败：{exc}")
            self._render_current_session()
            return

        config.workspace.mkdir(parents=True, exist_ok=True)
        history = ChatHistory(config.history_path)
        client = DeepSeekClient(
            api_key=config.deepseek_api_key,
            model=config.model,
            temperature=config.temperature,
        )
        tools = build_local_tool_registry(config.workspace)
        self.agent = MiniOpenClawAgent(
            client=client,
            tools=tools,
            history=history,
            max_rounds=config.max_rounds,
        )
        self.config = config
        self.status_label.setText(f"已连接\n{config.workspace}")
        self.state_chip.setText("已连接")
        self._refresh_sessions()
        self._render_current_session()
        self._update_send_button_state()

    def _history(self) -> ChatHistory | None:
        return self.agent.history if self.agent else None

    def _collect_history_items(self, history: ChatHistory | None = None) -> list[dict[str, str]]:
        history = history or self._history()
        items: list[dict[str, str]] = []
        if history is None:
            return [
                {
                    "role": "event",
                    "content": "你好，我是 OpenClaw Mini。你可以让我读写 workspace 里的文件，或帮你执行安全范围内的本地任务。",
                }
            ]

        for message in history.messages:
            role = message.get("role")
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if role in {"user", "assistant"}:
                items.append({"role": str(role), "content": content})

        if not items:
            items.append(
                {
                    "role": "event",
                    "content": "你好，我是 OpenClaw Mini。你可以让我读写 workspace 里的文件，或帮你执行安全范围内的本地任务。",
                }
            )
        return items

    def _refresh_sessions(self) -> None:
        history = self._history()
        self._refreshing_sessions = True
        try:
            self.session_list.clear()
            self.session_ids.clear()

            if history is None:
                item = QListWidgetItem("暂无会话")
                item.setFlags(Qt.NoItemFlags)
                self.session_list.addItem(item)
                return

            active_session_id = history.session_id
            active_index = 0
            for index, session in enumerate(history.list_sessions()):
                title = session["title"] or "新对话"
                item = QListWidgetItem(title)
                item.setToolTip(session.get("updated_at") or "")
                self.session_list.addItem(item)
                self.session_ids[index] = session["id"]
                if session["id"] == active_session_id:
                    active_index = index

            if self.session_ids:
                self.session_list.setCurrentRow(active_index)
        finally:
            self._refreshing_sessions = False

    def _new_session(self) -> None:
        if self.is_running:
            self.status_label.setText("当前对话仍在运行，请完成后再创建新对话。")
            return

        history = self._history()
        if history is None:
            self._set_transient_error("智能体尚未初始化，请先检查 .env 配置。")
            self._render_current_session()
            return

        history.create_session()
        self.input_box.clear()
        self.transient_items.clear()
        self.run_base_items = None
        self._refresh_sessions()
        self._render_current_session()
        if self.config:
            self.status_label.setText(f"已创建新对话\n{self.config.workspace}")
        else:
            self.status_label.setText("已创建新对话")
        self._update_send_button_state()

    def _switch_selected_session(self) -> None:
        if self._refreshing_sessions:
            return
        if self.is_running:
            self._refresh_sessions()
            self.status_label.setText("当前对话仍在运行，请完成后再切换会话。")
            return

        history = self._history()
        row = self.session_list.currentRow()
        if history is None or row < 0:
            return

        session_id = self.session_ids.get(row)
        if not session_id or session_id == history.session_id:
            return

        try:
            history.switch_session(session_id)
        except ValueError as exc:
            self._set_transient_error(str(exc))
            self._refresh_sessions()
            self._render_current_session()
            return

        self.input_box.clear()
        self.transient_items.clear()
        self.run_base_items = None
        self._render_current_session()
        self._refresh_sessions()
        if self.config:
            self.status_label.setText(f"已切换到：{history.current_title()}\n{self.config.workspace}")
        else:
            self.status_label.setText(f"已切换到：{history.current_title()}")
        self._update_send_button_state()

    def _render_current_session(self) -> None:
        history = self._history()
        if history is None:
            self.chat_title_label.setText("对话")
        else:
            self.chat_title_label.setText(history.current_title())

        base_items = self.run_base_items if self.run_base_items is not None else self._collect_history_items(history)
        self._render_chat(base_items + self.transient_items)

    def _render_chat(self, items: list[dict[str, str]]) -> None:
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for item in items:
            self._add_message_bubble(item["role"], item["content"])

        self.messages_layout.addStretch(1)
        QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _add_message_bubble(self, role: str, content: str) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        if role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(self._build_bubble("UserBubble", "你", content, user=True), 0, Qt.AlignRight)
        elif role == "assistant":
            row_layout.addWidget(self._build_bubble("AssistantBubble", "OpenClaw Mini", content), 0, Qt.AlignLeft)
            row_layout.addStretch(1)
        elif role == "error":
            row_layout.addWidget(self._build_notice_bubble("ErrorBubble", content, error=True))
        else:
            row_layout.addWidget(self._build_notice_bubble("EventBubble", content))

        self.messages_layout.addWidget(row)

    def _build_bubble(self, object_name: str, sender: str, content: str, *, user: bool = False) -> QFrame:
        bubble = QFrame()
        bubble.setObjectName(object_name)
        bubble.setMaximumWidth(self._message_max_width())
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(18, 14, 18, 16)
        bubble_layout.setSpacing(8)

        sender_label = QLabel(sender)
        sender_label.setObjectName("UserBubbleName" if user else "BubbleName")
        sender_label.setAlignment(Qt.AlignRight if user else Qt.AlignLeft)
        bubble_layout.addWidget(sender_label)

        content_label = QLabel(content)
        content_label.setObjectName("BubbleContent")
        content_label.setWordWrap(True)
        content_label.setTextFormat(Qt.PlainText)
        content_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        content_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        bubble_layout.addWidget(content_label)

        return bubble

    def _build_notice_bubble(self, object_name: str, content: str, *, error: bool = False) -> QFrame:
        bubble = QFrame()
        bubble.setObjectName(object_name)
        bubble.setMaximumWidth(max(420, int(self.chat_scroll.viewport().width() * 0.86)))
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 13, 16, 13)
        bubble_layout.setSpacing(0)

        content_label = QLabel(content)
        content_label.setObjectName("ErrorContent" if error else "EventContent")
        content_label.setWordWrap(True)
        content_label.setTextFormat(Qt.PlainText)
        content_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        bubble_layout.addWidget(content_label)

        return bubble

    def _message_max_width(self) -> int:
        viewport_width = max(520, self.chat_scroll.viewport().width())
        return max(360, int(viewport_width * 0.68))

    def _scroll_chat_to_bottom(self) -> None:
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _refresh_files(self) -> None:
        self.file_list.clear()
        self.file_paths.clear()
        workspace = self.config.workspace if self.config else Path.cwd() / "workspace"
        if not workspace.exists():
            item = QListWidgetItem("workspace 不存在")
            item.setFlags(Qt.NoItemFlags)
            self.file_list.addItem(item)
            return

        items = sorted(workspace.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        if not items:
            item = QListWidgetItem("暂无文件")
            item.setFlags(Qt.NoItemFlags)
            self.file_list.addItem(item)
            return

        for index, item_path in enumerate(items):
            prefix = "目录" if item_path.is_dir() else "文件"
            item = QListWidgetItem(f"{prefix}  {item_path.name}")
            item.setToolTip(str(item_path))
            self.file_list.addItem(item)
            self.file_paths[index] = item_path

    def _open_selected_file(self) -> None:
        row = self.file_list.currentRow()
        target = self.file_paths.get(row)
        if target is None:
            self._set_transient_error("请先在左侧 Workspace 中选择一个文件或文件夹。")
            self._render_current_session()
            return

        if not target.exists():
            self._set_transient_error("选中的项目不存在，请刷新文件列表后重试。")
            self._render_current_session()
            return

        try:
            if platform.system() == "Windows":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))
                if not opened:
                    raise OSError("系统没有可用的默认打开方式。")
        except OSError as exc:
            self._set_transient_error(f"打开失败：{exc}")
            self._render_current_session()
        else:
            self.status_label.setText(f"已打开\n{target.name}")

    def _update_send_button_state(self) -> None:
        has_text = bool(self.input_box.toPlainText().strip())
        enabled = has_text and not self.is_running and self.agent is not None
        self.send_button.setEnabled(enabled)

    def _send_message(self) -> None:
        if self.is_running:
            return
        if self.agent is None:
            self._set_transient_error("智能体尚未初始化，请先检查 .env 配置。")
            self._render_current_session()
            return

        text = self.input_box.toPlainText().strip()
        if not text:
            return

        self.run_base_items = self._collect_history_items()
        self.transient_items = [{"role": "user", "content": text}]
        self.input_box.clear()
        self.is_running = True
        self.current_answer_has_delta = False
        self.status_label.setText("正在思考...")
        self.state_chip.setText("处理中")
        self._render_current_session()
        self._update_send_button_state()

        thread = threading.Thread(target=self._run_agent, args=(text,), daemon=True)
        thread.start()

    def _run_agent(self, text: str) -> None:
        had_delta = False

        def on_delta(delta: str) -> None:
            nonlocal had_delta
            had_delta = True
            self.events.put(("delta", delta, None))

        def on_event(message: str) -> None:
            self.events.put(("event", message, None))

        try:
            if self.agent is None:
                raise RuntimeError("智能体未初始化")
            answer = self.agent.run_stream(text, on_delta=on_delta, on_event=on_event)
        except Exception as exc:
            self.events.put(("error", f"运行失败：{exc}", None))
        else:
            self.events.put(("done", answer, had_delta))

    def _drain_events(self) -> None:
        dirty = False
        try:
            while True:
                kind, payload, had_delta = self.events.get_nowait()
                if kind == "delta":
                    self.current_answer_has_delta = True
                    if self.transient_items and self.transient_items[-1]["role"] == "assistant":
                        self.transient_items[-1]["content"] += payload
                    else:
                        self.transient_items.append({"role": "assistant", "content": payload})
                    dirty = True
                elif kind == "event":
                    self.status_label.setText(payload)
                    self.transient_items.append({"role": "event", "content": payload})
                    dirty = True
                elif kind == "error":
                    self.run_base_items = None
                    self.transient_items = [{"role": "error", "content": payload}]
                    self._finish_run("出错了")
                    dirty = True
                elif kind == "done":
                    self.run_base_items = None
                    self.transient_items.clear()
                    self._finish_run("就绪")
                    self._refresh_sessions()
                    self._refresh_files()
                    dirty = True
        except queue.Empty:
            pass

        if dirty:
            self._render_current_session()

    def _finish_run(self, status: str) -> None:
        self.is_running = False
        self.state_chip.setText(status)
        if self.config:
            self.status_label.setText(f"{status}\n{self.config.workspace}")
        else:
            self.status_label.setText(status)
        self._update_send_button_state()

    def _set_transient_error(self, text: str) -> None:
        self.run_base_items = None
        self.transient_items = [{"role": "error", "content": text}]


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("OpenClaw Mini")
    app.setStyle("Fusion")
    app.setFont(QFont(FONT, 10))

    window = OpenClawWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
