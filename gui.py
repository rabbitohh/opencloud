from __future__ import annotations

import os
import platform
import queue
import re
import threading
import hashlib
from pathlib import Path

from openclaw_mini.agent import MiniOpenClawAgent
from openclaw_mini.config import Config
from openclaw_mini.history import ChatHistory
from openclaw_mini.latex_renderer import LatexMarkdownRenderer
from openclaw_mini.latex_sanitizer import sanitize_latex_content
from openclaw_mini.llm import DeepSeekClient
from openclaw_mini.memory import MemoryStore
from openclaw_mini.speech import BaiduSpeechRecognizer
from openclaw_mini.tools.local import build_local_tool_registry

try:
    from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
    from PySide6.QtGui import (
        QColor,
        QDesktopServices,
        QFont,
        QIcon,
        QPainter,
        QPixmap,
        QTextCursor,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QFrame,
        QGraphicsDropShadowEffect,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QTextBrowser,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:
    if exc.name == "PySide6":
        raise SystemExit("未安装 PySide6。请运行：python -m pip install PySide6") from exc
    raise

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
except (ImportError, ModuleNotFoundError):
    QAudioFormat = None  # type: ignore[assignment]
    QAudioSource = None  # type: ignore[assignment]
    QMediaDevices = None  # type: ignore[assignment]

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except (ImportError, ModuleNotFoundError):
    QTextToSpeech = None  # type: ignore[assignment]

try:
    from PySide6.QtSvg import QSvgRenderer
except (ImportError, ModuleNotFoundError):
    QSvgRenderer = None  # type: ignore[assignment]


FONT = "Microsoft YaHei"
APP_ROOT = Path(__file__).resolve().parent
SPEAKER_ICON_PATH = APP_ROOT / "assets" / "speaker.svg"
MICROPHONE_ICON_PATH = APP_ROOT / "assets" / "microphone.svg"
SPEAKER_ICON_SIZE = QSize(21, 21)
SPEAKER_PIXMAP_SIZE = QSize(64, 64)
MICROPHONE_ICON_SIZE = QSize(23, 23)
MICROPHONE_PIXMAP_SIZE = QSize(64, 64)


class ComposerTextEdit(QTextEdit):
    sendRequested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ClickableTitleLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TitleLineEdit(QLineEdit):
    canceled = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.canceled.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class OpenClawWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AppWindow")
        self.setWindowTitle("Opencloud")
        self.resize(1240, 820)
        self.setMinimumSize(980, 680)

        self.agent: MiniOpenClawAgent | None = None
        self.config: Config | None = None
        self.speech_recognizer: BaiduSpeechRecognizer | None = None
        self.events: queue.Queue[tuple[str, str, bool | None]] = queue.Queue()
        self.file_paths: dict[int, Path] = {}
        self.session_ids: dict[int, str] = {}
        self.is_running = False
        self.is_recording = False
        self.is_transcribing = False
        self.current_answer_has_delta = False
        self.max_rounds_waiting_for_approval = False
        self.continuation_rounds = 8
        self._refreshing_sessions = False
        self.transient_items: list[dict[str, str]] = []
        self.run_base_items: list[dict[str, str]] | None = None
        self.audio_source = None
        self.audio_device = None
        self.audio_buffer = bytearray()
        self.record_seconds = 0
        self.tts = QTextToSpeech(self) if QTextToSpeech is not None else None
        self.read_aloud_enabled = False
        self.current_speech_key: str | None = None
        self.speaker_icon = self._load_speaker_icon()
        self.microphone_icon = self._load_microphone_icon()
        self.latex_renderer = LatexMarkdownRenderer()
        self.latex_render_overrides: dict[str, bool] = {}
        self.message_font_presets: list[tuple[str, int]] = [
            ("小", 15),
            ("中", 17),
            ("大", 19),
        ]
        self.message_font_index = 1

        self._configure_theme()
        self._build_layout()
        self._update_read_aloud_button()
        self._update_message_font_button()
        self._load_agent()
        self._refresh_files()

        if self.tts is not None:
            self.tts.stateChanged.connect(self._on_tts_state_changed)

        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self._drain_events)
        self.event_timer.start(80)

        self.record_timer = QTimer(self)
        self.record_timer.timeout.connect(self._tick_recording)

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
            QLineEdit#TitleEditor {{
                color: {self.colors["text"]};
                background: #ffffff;
                border: 1px solid {self.colors["accent_tint"]};
                border-radius: 14px;
                padding: 8px 12px;
                font-size: 26px;
                font-weight: 700;
                selection-background-color: {self.colors["accent_tint"]};
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
            QPushButton#IconButton,
            QPushButton#ActiveIconButton {{
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
                border-radius: 17px;
                padding: 0;
                font-size: 15px;
                font-weight: 700;
                background: {self.colors["panel_inset"]};
                color: {self.colors["muted"]};
                border: 1px solid {self.colors["line"]};
            }}
            QPushButton#IconButton:hover {{
                background: #ffffff;
                color: {self.colors["accent_dark"]};
                border: 1px solid {self.colors["accent_tint"]};
            }}
            QPushButton#ActiveIconButton {{
                background: {self.colors["accent_soft"]};
                color: {self.colors["accent_dark"]};
                border: 1px solid {self.colors["accent"]};
            }}
            QPushButton#ActiveIconButton:hover {{
                background: {self.colors["accent_tint"]};
                border: 1px solid {self.colors["accent_dark"]};
            }}
            QPushButton#IconButton:disabled {{
                color: {self.colors["muted_soft"]};
                background: {self.colors["panel_inset"]};
                border: 1px solid {self.colors["line"]};
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
            QPushButton#GhostButton {{
                background: {self.colors["panel_inset"]};
                color: {self.colors["text"]};
                border: 1px solid {self.colors["line"]};
                font-weight: 600;
            }}
            QPushButton#GhostButton:hover {{
                background: #ffffff;
            }}
            QPushButton#LatexToggleButton,
            QPushButton#ActiveLatexToggleButton {{
                min-width: 46px;
                max-width: 46px;
                min-height: 34px;
                max-height: 34px;
                border-radius: 17px;
                padding: 0;
                font-size: 12px;
                font-weight: 700;
                background: {self.colors["panel_inset"]};
                color: {self.colors["muted"]};
                border: 1px solid {self.colors["line"]};
            }}
            QPushButton#LatexToggleButton:hover {{
                background: #ffffff;
                color: {self.colors["accent_dark"]};
                border: 1px solid {self.colors["accent_tint"]};
            }}
            QPushButton#ActiveLatexToggleButton {{
                background: {self.colors["accent_soft"]};
                color: {self.colors["accent_dark"]};
                border: 1px solid {self.colors["accent"]};
            }}
            QPushButton#ActiveLatexToggleButton:hover {{
                background: {self.colors["accent_tint"]};
                border: 1px solid {self.colors["accent_dark"]};
            }}
            QPushButton#VoiceIconButton {{
                min-width: 42px;
                max-width: 42px;
                min-height: 42px;
                max-height: 42px;
                border-radius: 21px;
                padding: 0;
                background: {self.colors["panel_inset"]};
                color: {self.colors["muted"]};
                border: 1px solid {self.colors["line"]};
            }}
            QPushButton#VoiceIconButton:hover {{
                background: #ffffff;
                border: 1px solid {self.colors["accent_tint"]};
            }}
            QPushButton#VoiceIconButton:pressed {{
                background: {self.colors["accent_soft"]};
                border: 1px solid {self.colors["accent"]};
            }}
            QPushButton#VoiceIconButton:disabled {{
                background: {self.colors["panel_inset"]};
                border: 1px solid {self.colors["line"]};
            }}
            QPushButton#DangerButton {{
                background: {self.colors["danger"]};
                color: #ffffff;
                font-weight: 700;
            }}
            QPushButton#DangerButton:hover {{
                background: #8f1d16;
            }}
            QFrame#SessionMenuPopup {{
                background: #ffffff;
                border: 1px solid {self.colors["line"]};
                border-radius: 14px;
            }}
            QPushButton#SessionMenuButton,
            QPushButton#SessionMenuDangerButton {{
                background: transparent;
                border: none;
                border-radius: 10px;
                color: {self.colors["text"]};
                font-size: 14px;
                font-weight: 500;
                padding: 9px 32px 9px 14px;
                text-align: left;
            }}
            QPushButton#SessionMenuButton:hover {{
                background: {self.colors["accent_soft"]};
                color: {self.colors["accent_dark"]};
            }}
            QPushButton#SessionMenuDangerButton:hover {{
                background: {self.colors["danger_soft"]};
                color: {self.colors["danger"]};
            }}
            QPushButton#SessionMenuButton:disabled,
            QPushButton#SessionMenuDangerButton:disabled {{
                color: {self.colors["muted_soft"]};
            }}
            QDialog#PopupDialog {{
                background: transparent;
            }}
            QDialog#ModalDialog {{
                background: {self.colors["panel"]};
                border-radius: 18px;
            }}
            QLabel#DialogTitle {{
                color: {self.colors["text"]};
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#DialogText {{
                color: {self.colors["muted"]};
                font-size: 13px;
                line-height: 1.45;
            }}
            QLabel#DialogDangerText {{
                color: {self.colors["danger"]};
                background: {self.colors["danger_soft"]};
                border: 1px solid #ffd5d0;
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 13px;
                line-height: 1.45;
            }}
            QLineEdit#DialogInput {{
                color: {self.colors["text"]};
                background: {self.colors["panel_inset"]};
                border: 1px solid {self.colors["line"]};
                border-radius: 14px;
                padding: 10px 12px;
                font-size: 14px;
                selection-background-color: {self.colors["accent_tint"]};
            }}
            QLineEdit#DialogInput:focus {{
                background: #ffffff;
                border: 1px solid {self.colors["accent"]};
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
            QTextBrowser#BubbleMarkdown,
            QTextBrowser#NoticeMarkdown {{
                background: transparent;
                border: none;
                color: {self.colors["text"]};
                selection-background-color: {self.colors["accent_tint"]};
            }}
            QLabel#BubbleContent {{
                color: {self.colors["text"]};
                font-size: 14px;
                line-height: 1.92;
            }}
            QLabel#EventContent {{
                color: {self.colors["muted"]};
                font-size: 13px;
                line-height: 1.84;
            }}
            QLabel#ErrorContent {{
                color: {self.colors["danger"]};
                font-size: 13px;
                line-height: 1.84;
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
            QScrollBar:horizontal {{
                background: transparent;
                height: 12px;
                margin: 0 4px 0 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {self.colors["line"]};
                border-radius: 6px;
                min-height: 32px;
            }}
            QScrollBar::handle:horizontal {{
                background: {self.colors["line"]};
                border-radius: 6px;
                min-width: 32px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {self.colors["muted"]};
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {self.colors["muted"]};
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: none;
                border: none;
                width: 0;
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
        brand_title = QLabel("Opencloud")
        brand_title.setObjectName("BrandTitle")
        brand_text.addWidget(brand_title)
        brand_row.addLayout(brand_text, 1)

        pill = QLabel("桌面版")
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
        self.new_session_button = QPushButton("新建对话")
        self.new_session_button.setObjectName("PrimaryButton")
        self.new_session_button.clicked.connect(self._new_session)
        session_top.addWidget(self.new_session_button, 0, Qt.AlignTop)
        sidebar_layout.addLayout(session_top)

        self.session_list = QListWidget()
        self.session_list.itemSelectionChanged.connect(self._switch_selected_session)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._open_session_context_menu)
        sidebar_layout.addWidget(self.session_list, 4)

        file_top = QHBoxLayout()
        file_top.setSpacing(8)
        file_labels = QVBoxLayout()
        file_labels.setSpacing(0)
        file_title = QLabel("工作区")
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
        self.chat_title_label = ClickableTitleLabel("对话")
        self.chat_title_label.setObjectName("TitleLabel")
        self.chat_title_label.setCursor(Qt.IBeamCursor)
        self.chat_title_label.setToolTip("点击重命名")
        self.chat_title_label.clicked.connect(self._start_title_edit)
        hero_text.addWidget(self.chat_title_label)
        self.chat_title_editor = TitleLineEdit()
        self.chat_title_editor.setObjectName("TitleEditor")
        self.chat_title_editor.setMaxLength(80)
        self.chat_title_editor.hide()
        self.chat_title_editor.editingFinished.connect(self._finish_title_edit)
        self.chat_title_editor.canceled.connect(self._cancel_title_edit)
        hero_text.addWidget(self.chat_title_editor)
        hero_layout.addLayout(hero_text, 1)

        self.state_chip = QLabel("未连接")
        self.state_chip.setObjectName("StateChip")
        self.read_aloud_button = self._make_icon_button()
        self.read_aloud_button.setToolTip("开启朗读")
        self.read_aloud_button.clicked.connect(self._toggle_read_aloud_mode)
        self.read_aloud_button.setEnabled(self.tts is not None)
        hero_layout.addWidget(self.read_aloud_button, 0, Qt.AlignTop)
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
        chat_top.addWidget(self._make_text_label("消息", "SectionTitle"))
        chat_top.addStretch(1)
        self.message_font_button = QPushButton()
        self.message_font_button.setObjectName("GhostButton")
        self.message_font_button.setToolTip("调整消息字号")
        self.message_font_button.clicked.connect(self._cycle_message_font_size)
        chat_top.addWidget(self.message_font_button)
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
        composer_layout.addWidget(self._make_text_label("输入", "SectionTitle"))

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
        self.voice_button = QPushButton()
        self.voice_button.setObjectName("VoiceIconButton")
        self.voice_button.setIcon(self.microphone_icon)
        self.voice_button.setIconSize(MICROPHONE_ICON_SIZE)
        self.voice_button.setToolTip("点击开始录音，再次点击停止并识别")
        self.voice_button.clicked.connect(self._toggle_voice_recording)
        composer_bottom.addWidget(self.voice_button)
        composer_bottom.addStretch(1)
        self.continue_button = QPushButton("继续 +8")
        self.continue_button.setObjectName("GhostButton")
        self.continue_button.setToolTip("授权更多推理轮次")
        self.continue_button.clicked.connect(self._continue_reasoning)
        self.continue_button.hide()
        composer_bottom.addWidget(self.continue_button)
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

    def _make_icon_button(self, active: bool = False) -> QPushButton:
        button = QPushButton()
        button.setObjectName("ActiveIconButton" if active else "IconButton")
        button.setIcon(self.speaker_icon)
        button.setIconSize(SPEAKER_ICON_SIZE)
        return button

    def _make_latex_toggle_button(self, active: bool) -> QPushButton:
        button = QPushButton("TeX")
        button.setObjectName("ActiveLatexToggleButton" if active else "LatexToggleButton")
        button.setToolTip("关闭这条消息的 LaTeX 渲染" if active else "开启这条消息的 LaTeX 渲染")
        return button

    def _message_font_size(self, *, notice: bool = False, code: bool = False) -> int:
        base_size = self.message_font_presets[self.message_font_index][1]
        if notice:
            return max(12, base_size - 1)
        if code:
            return max(12, base_size - 1)
        return base_size

    def _update_message_font_button(self) -> None:
        if not hasattr(self, "message_font_button"):
            return
        label, _size = self.message_font_presets[self.message_font_index]
        self.message_font_button.setText(f"字号 {label}")

    def _cycle_message_font_size(self) -> None:
        self.message_font_index = (self.message_font_index + 1) % len(self.message_font_presets)
        self._update_message_font_button()
        self.status_label.setText(f"消息流字号已切换为{self.message_font_presets[self.message_font_index][0]}")
        self._render_current_session(scroll_to_bottom=False)

    @staticmethod
    def _load_speaker_icon() -> QIcon:
        icon = QIcon()
        if QSvgRenderer is not None:
            renderer = QSvgRenderer(str(SPEAKER_ICON_PATH))
            if renderer.isValid():
                pixmap = QPixmap(SPEAKER_PIXMAP_SIZE)
                pixmap.fill(Qt.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
                icon.addPixmap(pixmap)
        if icon.isNull():
            icon = QIcon(str(SPEAKER_ICON_PATH))
        return icon

    @staticmethod
    def _load_microphone_icon() -> QIcon:
        icon = QIcon()
        if QSvgRenderer is not None:
            renderer = QSvgRenderer(str(MICROPHONE_ICON_PATH))
            if renderer.isValid():
                pixmap = QPixmap(MICROPHONE_PIXMAP_SIZE)
                pixmap.fill(Qt.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
                icon.addPixmap(pixmap)
        if icon.isNull():
            icon = QIcon(str(MICROPHONE_ICON_PATH))
        return icon

    def _load_agent(self) -> None:
        try:
            config = Config.from_env()
        except RuntimeError as exc:
            self.status_label.setText("缺少配置：请在 .env 中设置 DEEPSEEK_API_KEY")
            self.state_chip.setText("等待配置")
            self._set_transient_error(f"启动失败：{exc}")
            QMessageBox.critical(self, "Opencloud", f"启动失败：{exc}")
            self._render_current_session()
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
        self.agent = MiniOpenClawAgent(
            client=client,
            tools=tools,
            history=history,
            max_rounds=config.max_rounds,
        )
        self.speech_recognizer = BaiduSpeechRecognizer(
            api_key=config.baidu_speech_api_key,
            secret_key=config.baidu_speech_secret_key,
            cuid=config.baidu_speech_cuid,
            dev_pid=config.baidu_speech_dev_pid,
        )
        self.config = config
        self.continuation_rounds = config.max_rounds
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
        welcome = "你好，我是 Opencloud。可以读写 workspace 中的文件，也可以帮助执行安全的本地任务。"
        if history is None:
            return [{"role": "event", "content": welcome}]

        for message in history.messages:
            role = message.get("role")
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if role in {"user", "assistant"}:
                items.append({"role": str(role), "content": content})

        if not items:
            items.append({"role": "event", "content": welcome})
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
                title = session["title"] or "新建对话"
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

    def _open_session_context_menu(self, position) -> None:
        history = self._history()
        item = self.session_list.itemAt(position)
        if history is None or item is None:
            return

        row = self.session_list.row(item)
        session_id = self.session_ids.get(row)
        if not session_id:
            return

        action = self._show_session_context_menu(self.session_list.mapToGlobal(position))
        if action == "rename":
            self._rename_session_from_menu(session_id, item.text())
        elif action == "delete":
            self._delete_session_from_menu(session_id, item.text())

    def _show_session_context_menu(self, global_position) -> str | None:
        dialog = QDialog(self, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        dialog.setObjectName("PopupDialog")
        dialog.setAttribute(Qt.WA_TranslucentBackground, True)

        outer_layout = QVBoxLayout(dialog)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        menu_frame = QFrame(dialog)
        menu_frame.setObjectName("SessionMenuPopup")
        menu_frame.setFixedWidth(156)
        menu_layout = QVBoxLayout(menu_frame)
        menu_layout.setContentsMargins(6, 6, 6, 6)
        menu_layout.setSpacing(2)

        chosen_action: dict[str, str | None] = {"value": None}

        rename_button = QPushButton("重命名")
        rename_button.setObjectName("SessionMenuButton")
        delete_button = QPushButton("删除")
        delete_button.setObjectName("SessionMenuDangerButton")
        rename_button.setEnabled(not self.is_running)
        delete_button.setEnabled(not self.is_running)

        def choose(action: str) -> None:
            chosen_action["value"] = action
            dialog.accept()

        rename_button.clicked.connect(lambda: choose("rename"))
        delete_button.clicked.connect(lambda: choose("delete"))

        menu_layout.addWidget(rename_button)
        menu_layout.addWidget(delete_button)
        outer_layout.addWidget(menu_frame)

        dialog.adjustSize()
        dialog.move(global_position)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return chosen_action["value"]
        return None

    def _show_rename_session_dialog(self, current_title: str) -> str | None:
        dialog = QDialog(self)
        dialog.setObjectName("ModalDialog")
        dialog.setWindowTitle("重命名会话")
        dialog.setModal(True)
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(14)

        title_label = QLabel("重命名会话")
        title_label.setObjectName("DialogTitle")
        layout.addWidget(title_label)

        name_label = QLabel("会话名称")
        name_label.setObjectName("DialogText")
        layout.addWidget(name_label)

        editor = QLineEdit(current_title)
        editor.setObjectName("DialogInput")
        editor.setMaxLength(80)
        editor.selectAll()
        layout.addWidget(editor)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addStretch(1)

        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("GhostButton")
        save_button = QPushButton("保存")
        save_button.setObjectName("PrimaryButton")
        save_button.setEnabled(bool(current_title.strip()))
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

        cancel_button.clicked.connect(dialog.reject)
        save_button.clicked.connect(dialog.accept)
        editor.returnPressed.connect(save_button.click)
        editor.textChanged.connect(lambda text: save_button.setEnabled(bool(text.strip())))

        if dialog.exec() == QDialog.DialogCode.Accepted:
            return editor.text().strip()
        return None

    def _show_delete_session_dialog(self, title: str) -> bool:
        dialog = QDialog(self)
        dialog.setObjectName("ModalDialog")
        dialog.setWindowTitle("删除会话")
        dialog.setModal(True)
        dialog.setMinimumWidth(440)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(14)

        title_label = QLabel("删除会话")
        title_label.setObjectName("DialogTitle")
        layout.addWidget(title_label)

        message = QLabel(f"确定删除“{title}”？")
        message.setObjectName("DialogText")
        message.setWordWrap(True)
        layout.addWidget(message)

        warning = QLabel("此操作无法撤销。")
        warning.setObjectName("DialogDangerText")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addStretch(1)

        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("GhostButton")
        delete_button = QPushButton("删除")
        delete_button.setObjectName("DangerButton")
        button_row.addWidget(cancel_button)
        button_row.addWidget(delete_button)
        layout.addLayout(button_row)

        cancel_button.clicked.connect(dialog.reject)
        delete_button.clicked.connect(dialog.accept)

        return dialog.exec() == QDialog.DialogCode.Accepted

    def _rename_session_from_menu(self, session_id: str, current_title: str) -> None:
        if self.is_running:
            self.status_label.setText("当前对话仍在运行，请结束后再重命名。")
            return

        history = self._history()
        if history is None:
            return
        if self.chat_title_editor.isVisible():
            self._cancel_title_edit()

        clean_title = self._show_rename_session_dialog(current_title)
        if clean_title is None:
            return

        if not clean_title:
            return

        try:
            history.rename_session(session_id, clean_title)
        except ValueError as exc:
            self._set_transient_error(str(exc))
        self._refresh_sessions()
        self._render_current_session()

    def _delete_session_from_menu(self, session_id: str, title: str) -> None:
        if self.is_running:
            self.status_label.setText("当前对话仍在运行，请结束后再删除。")
            return

        history = self._history()
        if history is None:
            return
        if self.chat_title_editor.isVisible():
            self._cancel_title_edit()

        if not self._show_delete_session_dialog(title):
            return

        deleted_active_session = session_id == history.session_id
        try:
            history.delete_session(session_id)
        except ValueError as exc:
            self._set_transient_error(str(exc))
        else:
            if deleted_active_session:
                self.input_box.clear()
                self.max_rounds_waiting_for_approval = False
                self.transient_items.clear()
                self.run_base_items = None
            self.status_label.setText("会话已删除")

        self._refresh_sessions()
        self._render_current_session()
        self._update_send_button_state()

    def _new_session(self) -> None:
        if self.is_running:
            self.status_label.setText("当前对话仍在运行，请结束后再新建。")
            return

        history = self._history()
        if history is None:
            self._set_transient_error("Agent 尚未初始化，请先检查 .env。")
            self._render_current_session()
            return

        history.create_session()
        self.input_box.clear()
        self.max_rounds_waiting_for_approval = False
        self.transient_items.clear()
        self.run_base_items = None
        self._refresh_sessions()
        self._render_current_session()
        if self.config:
            self.status_label.setText(f"已新建对话\n{self.config.workspace}")
        else:
            self.status_label.setText("已新建对话")
        self._update_send_button_state()

    def _switch_selected_session(self) -> None:
        if self._refreshing_sessions:
            return
        if self.is_running:
            self._refresh_sessions()
            self.status_label.setText("当前对话仍在运行，请结束后再切换。")
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
        self.max_rounds_waiting_for_approval = False
        self.transient_items.clear()
        self.run_base_items = None
        self._render_current_session()
        self._refresh_sessions()
        if self.config:
            self.status_label.setText(f"已切换到：{history.current_title()}\n{self.config.workspace}")
        else:
            self.status_label.setText(f"已切换到：{history.current_title()}")
        self._update_send_button_state()

    def _start_title_edit(self) -> None:
        history = self._history()
        if history is None:
            return
        if self.is_running:
            self.status_label.setText("当前对话仍在运行，请结束后再重命名。")
            return

        title = history.current_title()
        self.chat_title_editor.setText(title)
        self.chat_title_editor.setPlaceholderText(title)
        self.chat_title_label.hide()
        self.chat_title_editor.show()
        self.chat_title_editor.setFocus(Qt.MouseFocusReason)
        self.chat_title_editor.selectAll()

    def _finish_title_edit(self) -> None:
        if not self.chat_title_editor.isVisible():
            return

        history = self._history()
        self.chat_title_editor.hide()
        self.chat_title_label.show()
        if history is None:
            self.chat_title_label.setText("对话")
            return

        new_title = self.chat_title_editor.text().strip()
        if new_title:
            history.rename_current_session(new_title)
        self.chat_title_label.setText(history.current_title())
        self._refresh_sessions()

    def _cancel_title_edit(self) -> None:
        history = self._history()
        self.chat_title_editor.hide()
        self.chat_title_label.show()
        self.chat_title_label.setText(history.current_title() if history else "对话")

    def _render_current_session(self, *, scroll_to_bottom: bool = True) -> None:
        history = self._history()
        title = "对话" if history is None else history.current_title()
        if self.chat_title_editor.isVisible():
            self.chat_title_editor.setPlaceholderText(title)
        else:
            self.chat_title_label.setText(title)

        base_items = self.run_base_items if self.run_base_items is not None else self._collect_history_items(history)
        self._render_chat(base_items + self.transient_items, scroll_to_bottom=scroll_to_bottom)

    def _render_chat(self, items: list[dict[str, str]], *, scroll_to_bottom: bool = True) -> None:
        previous_scroll_value = self.chat_scroll.verticalScrollBar().value()

        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index, item in enumerate(items):
            self._add_message_bubble(item["role"], item["content"], index)

        self.messages_layout.addStretch(1)
        if scroll_to_bottom:
            QTimer.singleShot(0, self._scroll_chat_to_bottom)
        else:
            QTimer.singleShot(0, lambda value=previous_scroll_value: self._restore_chat_scroll(value))

    def _add_message_bubble(self, role: str, content: str, index: int) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        if role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(
                self._build_bubble(
                    "UserBubble",
                    "你",
                    content,
                    user=True,
                    message_key=self._message_key(role, index, content),
                ),
                0,
                Qt.AlignRight,
            )
        elif role == "assistant":
            row_layout.addWidget(
                self._build_bubble(
                    "AssistantBubble",
                    "Opencloud",
                    content,
                    message_key=self._message_key(role, index, content),
                    speech_key=self._speech_key(index),
                ),
                0,
                Qt.AlignLeft,
            )
            row_layout.addStretch(1)
        elif role == "error":
            row_layout.addWidget(self._build_notice_bubble("ErrorBubble", content, error=True))
        else:
            row_layout.addWidget(self._build_notice_bubble("EventBubble", content))

        self.messages_layout.addWidget(row)

    def _build_bubble(
        self,
        object_name: str,
        sender: str,
        content: str,
        *,
        message_key: str,
        user: bool = False,
        speech_key: str | None = None,
    ) -> QFrame:
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

        bubble_bg = self.colors["accent_soft"] if user else self.colors["panel_inset_alt"]
        has_latex = self.latex_renderer.has_latex(content)
        render_latex = self._is_latex_rendering_enabled(message_key)
        content_view = self._make_markdown_view(content, background_color=bubble_bg, render_latex=render_latex)
        self._fit_markdown_view(content_view, self._message_max_width() - 36, content)
        bubble_layout.addWidget(content_view)

        if has_latex or speech_key is not None:
            action_row = QHBoxLayout()
            action_row.setContentsMargins(0, 0, 0, 0)
            action_row.setSpacing(8)
            action_row.addStretch(1)
            if has_latex:
                latex_button = self._make_latex_toggle_button(render_latex)
                latex_button.clicked.connect(
                    lambda _checked=False, key=message_key: self._toggle_message_latex_rendering(key)
                )
                action_row.addWidget(latex_button, 0, Qt.AlignRight)
            if speech_key is not None:
                speak_button = self._make_icon_button(active=self.current_speech_key == speech_key)
                speak_button.setToolTip("停止朗读" if self.current_speech_key == speech_key else "朗读这条消息")
                speak_button.setEnabled(self.tts is not None and bool(content.strip()))
                speak_button.clicked.connect(lambda _checked=False, key=speech_key, text=content: self._toggle_message_speech(key, text))
                action_row.addWidget(speak_button, 0, Qt.AlignRight)
            bubble_layout.addLayout(action_row)

        return bubble

    def _build_notice_bubble(self, object_name: str, content: str, *, error: bool = False) -> QFrame:
        bubble = QFrame()
        bubble.setObjectName(object_name)
        bubble.setMaximumWidth(max(500, int(self.chat_scroll.viewport().width() * 0.92)))
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 13, 16, 13)
        bubble_layout.setSpacing(0)

        bubble_bg = self.colors["danger_soft"] if error else self.colors["panel"]
        content_view = self._make_markdown_view(
            content,
            notice=True,
            error=error,
            background_color=bubble_bg,
        )
        self._fit_markdown_view(content_view, max(460, int(self.chat_scroll.viewport().width() * 0.94)) - 32, content)
        bubble_layout.addWidget(content_view)

        return bubble

    def _make_markdown_view(
        self,
        content: str,
        *,
        notice: bool = False,
        error: bool = False,
        background_color: str | None = None,
        render_latex: bool = True,
    ) -> QWidget:
        view = QTextBrowser()
        view.setObjectName("NoticeMarkdown" if notice else "BubbleMarkdown")
        view.setFrameShape(QFrame.NoFrame)
        view.setOpenExternalLinks(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setLineWrapMode(QTextEdit.WidgetWidth)
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        view.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard | Qt.LinksAccessibleByMouse)
        font = QFont(FONT)
        font.setPixelSize(self._message_font_size(notice=notice))
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setStyleHint(QFont.StyleHint.System)
        view.setFont(font)
        view.document().setDefaultFont(font)
        view.document().setDefaultStyleSheet(self._markdown_stylesheet(notice=notice, error=error))
        self._set_markdown_content(view, content or " ", notice=notice, error=error, render_latex=render_latex)
        return view

    def _fit_markdown_view(self, view: QWidget, content_width: int, content: str) -> None:
        content_width = max(260, content_width)
        markdown_markers = ("```", "|", "- ", "* ", "1. ", "#", ">")
        longest_line = max((len(line) for line in content.splitlines()), default=0)
        should_expand = (
            len(content) > 96
            or "\n" in content
            or any(marker in content for marker in markdown_markers)
        )
        char_width = max(11, int(self._message_font_size() * 0.95))
        desired_width = content_width if should_expand else min(content_width, max(170, longest_line * char_width + 28))
        view.setFixedWidth(desired_width)
        if isinstance(view, QTextBrowser):
            view.document().setTextWidth(desired_width)
            height = int(view.document().size().height()) + 8
            view.setFixedHeight(max(28, height))

    def _set_markdown_content(
        self,
        view: QTextBrowser,
        content: str,
        *,
        notice: bool = False,
        error: bool = False,
        render_latex: bool = True,
    ) -> None:
        text_color = self.colors["danger"] if error else self.colors["muted"] if notice else self.colors["text"]
        html_content = self.latex_renderer.to_html(
            content or " ",
            font_size=self._message_font_size(notice=notice),
            text_color=text_color,
            render_latex=render_latex,
        )
        view.setHtml(html_content)

    def _markdown_stylesheet(self, *, notice: bool = False, error: bool = False) -> str:
        text_color = self.colors["danger"] if error else self.colors["muted"] if notice else self.colors["text"]
        body_size = self._message_font_size(notice=notice)
        code_size = self._message_font_size(code=True)
        body_line_height = int(round(body_size * 2.0))
        return f"""
            body {{
                color: {text_color};
                font-family: '{FONT}';
                font-size: {body_size}px;
                line-height: {body_line_height}px;
                margin: 0;
            }}
            p {{
                margin: 0 0 16px 0;
            }}
            ul, ol {{
                margin-top: 8px;
                margin-bottom: 16px;
                padding-left: 22px;
            }}
            li {{
                margin-bottom: 8px;
            }}
            pre {{
                background: #eef2f7;
                border: 1px solid {self.colors["line"]};
                border-radius: 10px;
                margin: 12px 0 16px 0;
                padding: 14px 16px;
                white-space: pre-wrap;
            }}
            code {{
                background: #eef2f7;
                color: {self.colors["text"]};
                font-family: 'Cascadia Mono', Consolas, monospace;
                font-size: {code_size}px;
            }}
            blockquote {{
                color: {self.colors["muted"]};
                border-left: 3px solid {self.colors["line"]};
                margin: 12px 0 16px 0;
                padding-left: 12px;
            }}
        """

    @staticmethod
    def _message_key(role: str, index: int, content: str) -> str:
        digest = hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{role}:{index}:{digest}"

    def _is_latex_rendering_enabled(self, message_key: str) -> bool:
        return self.latex_render_overrides.get(message_key, True)

    def _toggle_message_latex_rendering(self, message_key: str) -> None:
        enabled = not self._is_latex_rendering_enabled(message_key)
        self.latex_render_overrides[message_key] = enabled
        self.status_label.setText("已开启这条消息的 LaTeX 渲染" if enabled else "已关闭这条消息的 LaTeX 渲染")
        self._render_current_session(scroll_to_bottom=False)

    @staticmethod
    def _speech_key(index: int) -> str:
        return f"assistant:{index}"

    def _toggle_read_aloud_mode(self) -> None:
        if self.tts is None:
            self.status_label.setText("QtTextToSpeech 不可用，朗读已禁用")
            return

        self.read_aloud_enabled = not self.read_aloud_enabled
        if not self.read_aloud_enabled:
            self._stop_speech()
        self._update_read_aloud_button()

    def _update_read_aloud_button(self) -> None:
        if not hasattr(self, "read_aloud_button"):
            return

        if self.tts is None:
            self.read_aloud_button.setObjectName("IconButton")
            self.read_aloud_button.setIcon(self.speaker_icon)
            self.read_aloud_button.setIconSize(SPEAKER_ICON_SIZE)
            self.read_aloud_button.setToolTip("QtTextToSpeech 不可用，朗读已禁用")
            self.read_aloud_button.setEnabled(False)
            self._refresh_widget_style(self.read_aloud_button)
            return

        self.read_aloud_button.setEnabled(True)
        self.read_aloud_button.setIcon(self.speaker_icon)
        self.read_aloud_button.setIconSize(SPEAKER_ICON_SIZE)
        if self.read_aloud_enabled:
            self.read_aloud_button.setObjectName("ActiveIconButton")
            self.read_aloud_button.setToolTip("关闭朗读")
        else:
            self.read_aloud_button.setObjectName("IconButton")
            self.read_aloud_button.setToolTip("开启朗读")
        self._refresh_widget_style(self.read_aloud_button)

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _toggle_message_speech(self, speech_key: str, content: str) -> None:
        if self.tts is None:
            self.status_label.setText("QtTextToSpeech 不可用，朗读已禁用")
            return

        if self.current_speech_key == speech_key and self.tts.state() in (
            QTextToSpeech.State.Speaking,
            QTextToSpeech.State.Synthesizing,
            QTextToSpeech.State.Paused,
        ):
            self._stop_speech()
            return

        self._speak_text(content, speech_key)

    def _speak_text(self, content: str, speech_key: str) -> None:
        if self.tts is None:
            return

        text = self._speech_plain_text(content)
        if not text:
            self.status_label.setText("这条消息没有可朗读的文本")
            return

        if self.tts.state() != QTextToSpeech.State.Ready:
            self.tts.stop()

        self.current_speech_key = speech_key
        self.tts.say(text)
        self.status_label.setText("正在朗读")
        self._render_current_session(scroll_to_bottom=False)

    def _stop_speech(self) -> None:
        if self.tts is not None and self.tts.state() != QTextToSpeech.State.Ready:
            self.tts.stop()
        self.current_speech_key = None
        self._update_read_aloud_button()
        self._render_current_session(scroll_to_bottom=False)

    def _on_tts_state_changed(self, state) -> None:
        if QTextToSpeech is None:
            return
        if state in (QTextToSpeech.State.Ready, QTextToSpeech.State.Error):
            if self.current_speech_key is not None:
                self.current_speech_key = None
                self._render_current_session(scroll_to_bottom=False)
            self._update_read_aloud_button()

    def _auto_read_latest_assistant(self, content: str) -> None:
        if not self.read_aloud_enabled or self.tts is None:
            return

        key = self._latest_assistant_speech_key()
        if key is None:
            key = "assistant:auto"
        self._speak_text(content, key)

    def _latest_assistant_speech_key(self) -> str | None:
        history = self._history()
        items = self.run_base_items if self.run_base_items is not None else self._collect_history_items(history)
        for index in range(len(items) - 1, -1, -1):
            if items[index].get("role") == "assistant":
                return self._speech_key(index)
        return None

    @staticmethod
    def _speech_plain_text(content: str) -> str:
        text = re.sub(r"```[\s\S]*?```", " code block ", content)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
        text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
        text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"[*_~|]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _message_max_width(self) -> int:
        viewport_width = max(520, self.chat_scroll.viewport().width())
        return max(520, int(viewport_width * 0.86))

    def _scroll_chat_to_bottom(self) -> None:
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _is_chat_near_bottom(self, threshold: int = 36) -> bool:
        scrollbar = self.chat_scroll.verticalScrollBar()
        return scrollbar.maximum() - scrollbar.value() <= threshold

    def _restore_chat_scroll(self, value: int, attempts: int = 8) -> None:
        scrollbar = self.chat_scroll.verticalScrollBar()
        if value > 0 and scrollbar.maximum() < value and attempts > 0:
            QTimer.singleShot(0, lambda: self._restore_chat_scroll(value, attempts - 1))
            return
        scrollbar.setValue(min(value, scrollbar.maximum()))

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
            prefix = "文件夹" if item_path.is_dir() else "文件"
            item = QListWidgetItem(f"{prefix}  {item_path.name}")
            item.setToolTip(str(item_path))
            self.file_list.addItem(item)
            self.file_paths[index] = item_path

    def _open_selected_file(self) -> None:
        row = self.file_list.currentRow()
        target = self.file_paths.get(row)
        if target is None:
            self._set_transient_error("请先在工作区选择文件或文件夹。")
            self._render_current_session()
            return

        if not target.exists():
            self._set_transient_error("所选项目不存在。请刷新文件列表后重试。")
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
            self.status_label.setText(f"已打开\n{target.name}")

    def _update_send_button_state(self) -> None:
        has_text = bool(self.input_box.toPlainText().strip())
        enabled = has_text and not self.is_running and not self.is_transcribing and self.agent is not None
        self.send_button.setEnabled(enabled)
        self._update_continue_button_state()
        if not hasattr(self, "voice_button"):
            return

        if self.is_recording:
            self.voice_button.setObjectName("GhostButton")
            self._refresh_widget_style(self.voice_button)
            self.voice_button.setIcon(QIcon())
            self.voice_button.setText(f"停止 {self.record_seconds}s")
            self.voice_button.setEnabled(True)
            return

        self.voice_button.setObjectName("VoiceIconButton")
        self._refresh_widget_style(self.voice_button)
        self.voice_button.setText("")
        self.voice_button.setIcon(self.microphone_icon)
        self.voice_button.setIconSize(MICROPHONE_ICON_SIZE)
        voice_enabled = (
            not self.is_running
            and not self.is_transcribing
            and not self.is_recording
            and QAudioFormat is not None
            and QAudioSource is not None
            and QMediaDevices is not None
            and self.speech_recognizer is not None
            and self.speech_recognizer.is_configured
        )
        self.voice_button.setEnabled(voice_enabled)
        if QAudioFormat is None or QAudioSource is None or QMediaDevices is None:
            self.voice_button.setToolTip("QtMultimedia 不可用，录音已禁用")
        elif self.speech_recognizer is None or not self.speech_recognizer.is_configured:
            self.voice_button.setToolTip("请先在 .env 中设置百度语音 API Key 和 Secret Key")
        elif self.is_transcribing:
            self.voice_button.setToolTip("正在识别语音")
        else:
            self.voice_button.setToolTip("点击开始录音，再次点击停止并识别")

    def _update_continue_button_state(self) -> None:
        if not hasattr(self, "continue_button"):
            return

        rounds = self._continuation_round_limit()
        self.continue_button.setText(f"继续 +{rounds}")
        self.continue_button.setToolTip(f"允许 Opencloud 继续运行最多 {rounds} 轮推理/工具调用")
        visible = self.max_rounds_waiting_for_approval and self.agent is not None
        self.continue_button.setVisible(visible)
        self.continue_button.setEnabled(
            visible and not self.is_running and not self.is_transcribing and not self.is_recording
        )

    def _continuation_round_limit(self) -> int:
        return max(1, self.config.max_rounds if self.config else self.continuation_rounds)

    def _toggle_voice_recording(self) -> None:
        if self.is_recording:
            self._stop_voice_recording()
        else:
            self._start_voice_recording()

    def _start_voice_recording(self) -> None:
        if self.is_running or self.is_transcribing:
            return
        if self.speech_recognizer is None or not self.speech_recognizer.is_configured:
            self.status_label.setText("语音未配置。请在 .env 中设置百度语音 API Key 和 Secret Key。")
            return
        if QAudioFormat is None or QAudioSource is None or QMediaDevices is None:
            self.status_label.setText("QtMultimedia 不可用，录音已禁用")
            return

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self.status_label.setText("未找到麦克风")
            return

        audio_format = QAudioFormat()
        audio_format.setSampleRate(16000)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(audio_format):
            self.status_label.setText("默认麦克风不支持百度语音识别所需的 16k/16bit/单声道 PCM。")
            return

        self.audio_buffer = bytearray()
        self.audio_source = QAudioSource(device, audio_format, self)
        self.audio_source.setBufferSize(32000)
        self.audio_device = self.audio_source.start()
        if self.audio_device is None:
            self.audio_source = None
            self.status_label.setText("启动麦克风失败")
            return

        self.audio_device.readyRead.connect(self._read_audio_chunk)
        self.record_seconds = 0
        self.is_recording = True
        self.status_label.setText("正在录音，点击停止后开始识别")
        self.state_chip.setText("录音中")
        self.record_timer.start(1000)
        self._update_send_button_state()

    def _read_audio_chunk(self) -> None:
        if self.audio_device is None:
            return
        chunk = self.audio_device.readAll()
        if chunk:
            self.audio_buffer.extend(bytes(chunk))

    def _tick_recording(self) -> None:
        if not self.is_recording:
            return
        self.record_seconds += 1
        if self.record_seconds >= 55:
            self._stop_voice_recording()
            return
        self._update_send_button_state()

    def _stop_voice_recording(self, *, transcribe: bool = True) -> None:
        if not self.is_recording:
            return

        self._read_audio_chunk()
        self.record_timer.stop()
        if self.audio_source is not None:
            self.audio_source.stop()
        self.audio_device = None
        self.audio_source = None
        self.is_recording = False
        audio = bytes(self.audio_buffer)
        self.audio_buffer.clear()
        self._update_send_button_state()

        if not transcribe:
            return
        if len(audio) < 3200:
            self.status_label.setText("录音太短，无法识别")
            self.state_chip.setText("已连接")
            return

        self.is_transcribing = True
        self.status_label.setText("正在识别语音...")
        self.state_chip.setText("识别中")
        self._update_send_button_state()

        thread = threading.Thread(target=self._recognize_voice, args=(audio,), daemon=True)
        thread.start()

    def _recognize_voice(self, audio: bytes) -> None:
        try:
            if self.speech_recognizer is None:
                raise RuntimeError("语音识别器尚未初始化")
            text = self.speech_recognizer.recognize_pcm(audio)
        except Exception as exc:
            self.events.put(("speech_error", f"语音识别失败：{exc}", None))
        else:
            self.events.put(("speech_done", text, None))

    def _send_message(self) -> None:
        if self.is_running:
            return
        if self.is_recording:
            self._stop_voice_recording()
            return
        if self.is_transcribing:
            return
        if self.agent is None:
            self._set_transient_error("Agent 尚未初始化，请先检查 .env。")
            self._render_current_session()
            return

        text = self.input_box.toPlainText().strip()
        if not text:
            return

        self.input_box.clear()
        self._start_agent_run(text)

    def _continue_reasoning(self) -> None:
        if self.is_running or self.is_recording or self.is_transcribing or self.agent is None:
            return
        if not self.max_rounds_waiting_for_approval:
            return

        rounds = self._continuation_round_limit()
        prompt = (
            f"继续推理，额外授权 {rounds} 轮。"
            "请基于上文继续，不要重复已经完成的步骤。"
        )
        self._start_agent_run(prompt, max_rounds=rounds)

    def _start_agent_run(self, text: str, *, max_rounds: int | None = None) -> None:
        self.max_rounds_waiting_for_approval = False
        self.run_base_items = self._collect_history_items()
        self.transient_items = [{"role": "user", "content": text}]
        self.is_running = True
        self.current_answer_has_delta = False
        self.status_label.setText("思考中...")
        self.state_chip.setText("处理中")
        self._render_current_session()
        self._update_send_button_state()

        thread = threading.Thread(target=self._run_agent, args=(text, max_rounds), daemon=True)
        thread.start()

    def _run_agent(self, text: str, max_rounds: int | None = None) -> None:
        had_delta = False

        def on_delta(delta: str) -> None:
            nonlocal had_delta
            had_delta = True
            self.events.put(("delta", delta, None))

        def on_event(message: str) -> None:
            self.events.put(("event", message, None))

        try:
            if self.agent is None:
                raise RuntimeError("Agent 尚未初始化")
            answer = self.agent.run_stream(
                text,
                on_delta=on_delta,
                on_event=on_event,
                max_rounds=max_rounds,
            )
        except Exception as exc:
            self.events.put(("error", f"运行失败：{exc}", None))
        else:
            self.events.put(("done", answer, had_delta))

    def _drain_events(self) -> None:
        dirty = False
        pin_to_bottom = self._is_chat_near_bottom()
        try:
            while True:
                kind, payload, had_delta = self.events.get_nowait()
                if kind == "delta":
                    self.current_answer_has_delta = True
                    if self.transient_items and self.transient_items[-1]["role"] == "assistant":
                        self.transient_items[-1]["content"] += payload
                        self.transient_items[-1]["content"] = sanitize_latex_content(
                            self.transient_items[-1]["content"]
                        )
                    else:
                        self.transient_items.append({
                            "role": "assistant",
                            "content": sanitize_latex_content(payload),
                        })
                    dirty = True
                elif kind == "event":
                    self.status_label.setText(payload)
                    self.transient_items.append({"role": "event", "content": payload})
                    dirty = True
                elif kind == "error":
                    self.run_base_items = None
                    self.max_rounds_waiting_for_approval = False
                    self.transient_items = [{"role": "error", "content": payload}]
                    self._finish_run("错误")
                    dirty = True
                elif kind == "done":
                    reached_limit = self.agent is not None and self.agent.max_rounds_reached
                    self.run_base_items = None
                    self.max_rounds_waiting_for_approval = reached_limit
                    self.transient_items.clear()
                    self._finish_run("等待授权" if reached_limit else "就绪")
                    if reached_limit:
                        self._set_limit_reached_status()
                    self._refresh_sessions()
                    self._refresh_files()
                    if payload.strip() and not reached_limit:
                        self._auto_read_latest_assistant(payload)
                    dirty = True
                elif kind == "speech_done":
                    self.is_transcribing = False
                    current = self.input_box.toPlainText().strip()
                    text = payload.strip()
                    self.input_box.setPlainText(f"{current}\n{text}" if current else text)
                    self.input_box.moveCursor(QTextCursor.MoveOperation.End)
                    self.input_box.setFocus()
                    self.state_chip.setText("已识别")
                    self.status_label.setText("语音识别完成，请确认后再发送。")
                    self._update_send_button_state()
                elif kind == "speech_error":
                    self.is_transcribing = False
                    self.state_chip.setText("识别失败")
                    self.status_label.setText(payload)
                    self._update_send_button_state()
        except queue.Empty:
            pass

        if dirty:
            self._render_current_session(scroll_to_bottom=pin_to_bottom)

    def _finish_run(self, status: str) -> None:
        self.is_running = False
        self.state_chip.setText(status)
        if self.config:
            self.status_label.setText(f"{status}\n{self.config.workspace}")
        else:
            self.status_label.setText(status)
        self._update_send_button_state()

    def _set_limit_reached_status(self) -> None:
        rounds = self._continuation_round_limit()
        status = f"已达到轮次上限。点击继续 +{rounds} 授权更多轮次。"
        if self.config:
            self.status_label.setText(f"{status}\n{self.config.workspace}")
        else:
            self.status_label.setText(status)
        self._update_send_button_state()

    def _set_transient_error(self, text: str) -> None:
        self.run_base_items = None
        self.max_rounds_waiting_for_approval = False
        self.transient_items = [{"role": "error", "content": text}]


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Opencloud")
    app.setStyle("Fusion")
    app.setFont(QFont(FONT, 10))

    window = OpenClawWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

