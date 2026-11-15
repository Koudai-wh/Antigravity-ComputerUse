"""Antigravity AI Studio — Unified PySide6 Native Desktop App (Chat + Computer Use + File Export).

Integrates:
- Dark-mode Chat UI inspired by llama-ui (OpenAI-compatible /v1/models & chat/completions)
- Autonomous Computer Use mode with resident virtual cursor overlay (assets/virtual_cursor.png)
- Top-right floating Mini Live Preview (PiP 320x200) showing live captures, cursor, ripples, & actions
- File Output / Artifact Cards with native macOS/OS Export (QFileDialog) + Drag & Drop attachments
- MCP server management, Mac Screen Sharing integration, and safety guardrails (Esc x2 kill switch)
"""

from __future__ import annotations

import base64
import json
import math
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeyEvent,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from artifact_parser import ExtractedArtifact, extract_artifacts
from computer_use_mcp import AppRuntime
from config_loader import DEFAULT_CONFIG_PATH, PROJECT_ROOT, AppConfig
from install_mcp_config import (
    build_mcp_config_document,
    install_into_json_file,
)
from safety import check_os_permissions
from vlm_client import (
    OpenAIComputerUseAgent,
    fetch_available_models,
    normalize_base_url,
    send_chat_completion,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
TEXT_CODE_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".csv",
    ".json",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sh",
    ".zsh",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
    ".swift",
    ".rs",
    ".go",
    ".xml",
    ".log",
}

DARK_STYLESHEET = """
QMainWindow, QWidget#RootContainer {
    background-color: #141416;
    color: #f2f2f5;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "Hiragino Sans", sans-serif;
    font-size: 13px;
}
QWidget#Sidebar {
    background-color: #1b1b1e;
    border-right: 1px solid #26262a;
}
QPushButton#SidebarActionBtn {
    background-color: transparent;
    color: #e4e4e7;
    border: none;
    border-radius: 8px;
    padding: 8px 10px;
    text-align: left;
    font-size: 13px;
}
QPushButton#SidebarActionBtn:hover {
    background-color: #27272c;
}
QPushButton#SidebarActionBtn[active="true"] {
    background-color: #2b2b31;
    color: #ffffff;
    font-weight: 600;
}
QLineEdit#SidebarSearch {
    background-color: #151518;
    color: #f2f2f5;
    border: 1px solid #2b2b30;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}
QLineEdit#SidebarSearch:focus {
    border-color: #38bdf8;
}
QListWidget#ConversationList {
    background-color: transparent;
    border: none;
    outline: none;
    color: #d4d4d8;
    font-size: 12px;
}
QListWidget#ConversationList::item {
    padding: 7px 10px;
    border-radius: 8px;
    margin-bottom: 2px;
}
QListWidget#ConversationList::item:selected {
    background-color: #27272c;
    color: #ffffff;
}
QListWidget#ConversationList::item:hover:!selected {
    background-color: #222226;
}
QFrame#SidebarBottomBox {
    background-color: #202024;
    border: 1px solid #2a2a30;
    border-radius: 12px;
}
QWidget#TopTabBar {
    background-color: #141416;
    border-bottom: 1px solid #222226;
}
QPushButton#TabPill {
    background-color: transparent;
    color: #9ca3af;
    border: none;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12px;
}
QPushButton#TabPill[active="true"] {
    background-color: #232328;
    color: #f4f4f5;
    font-weight: 500;
}
QPushButton#TabPill:hover:!checked {
    background-color: #1d1d21;
    color: #e4e4e7;
}
QPushButton#TopToolBtn {
    background-color: #202025;
    color: #d4d4d8;
    border: 1px solid #2e2e36;
    border-radius: 8px;
    padding: 5px 11px;
    font-size: 12px;
}
QPushButton#TopToolBtn:hover {
    background-color: #2b2b32;
    color: #ffffff;
}
QPushButton#TopToolBtn:checked {
    border-color: #38bdf8;
    color: #38bdf8;
}
QPushButton#EmergencyStopBtn {
    background-color: #7f1d1d;
    color: #fecaca;
    border: 1px solid #dc2626;
    border-radius: 8px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#EmergencyStopBtn:hover {
    background-color: #991b1b;
    color: #ffffff;
}
QPushButton#EmergencyStopBtn[paused="true"] {
    background-color: #14532d;
    color: #bbf7d0;
    border: 1px solid #22c55e;
}
QScrollArea#ChatScroll {
    background-color: #141416;
    border: none;
}
QWidget#ChatScrollContents {
    background-color: #141416;
}
QFrame#UserBubble {
    background-color: #232328;
    border: 1px solid #303037;
    border-radius: 16px;
}
QFrame#AssistantBubble {
    background-color: #19191d;
    border: 1px solid #27272d;
    border-radius: 16px;
}
QFrame#ArtifactCard {
    background-color: #1e1e23;
    border: 1px solid #32323c;
    border-radius: 12px;
}
QPlainTextEdit#ArtifactPreview {
    background-color: #101013;
    color: #e2e8f0;
    border: 1px solid #26262e;
    border-radius: 8px;
    font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
    font-size: 12px;
    padding: 8px;
}
QPushButton#ExportBtn {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#ExportBtn:hover {
    background-color: #3b82f6;
}
QPushButton#SmallActionBtn {
    background-color: #27272e;
    color: #e4e4e7;
    border: 1px solid #36363f;
    border-radius: 7px;
    padding: 5px 10px;
    font-size: 12px;
}
QPushButton#SmallActionBtn:hover {
    background-color: #32323b;
}
QFrame#ComposerPill {
    background-color: #212125;
    border: 1px solid #323239;
    border-radius: 22px;
}
QPlainTextEdit#ComposerInput {
    background-color: transparent;
    color: #f4f4f5;
    border: none;
    font-size: 14px;
    selection-background-color: #38bdf8;
}
QPushButton#AttachBtn {
    background-color: #2b2b31;
    color: #d4d4d8;
    border: 1px solid #3a3a42;
    border-radius: 15px;
    font-size: 16px;
    font-weight: 600;
}
QPushButton#AttachBtn:hover {
    background-color: #36363e;
    color: #ffffff;
}
QPushButton#ModeBtn {
    background-color: transparent;
    color: #a1a1aa;
    border: none;
    border-radius: 11px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#ModeBtn:checked {
    background-color: #32323b;
    color: #ffffff;
}
QPushButton#ModeBtnComputer:checked {
    background-color: #0c4a6e;
    color: #7dd3fc;
    border: 1px solid #0284c7;
}
QComboBox#ModelCombo {
    background-color: #2a2a30;
    color: #e4e4e7;
    border: 1px solid #393942;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 12px;
    min-width: 190px;
}
QComboBox#ModelCombo::drop-down {
    border: none;
    width: 20px;
}
QComboBox#ModelCombo QAbstractItemView {
    background-color: #1f1f24;
    color: #f2f2f5;
    border: 1px solid #363640;
    selection-background-color: #2563eb;
}
QPushButton#SendBtn {
    background-color: #e4e4e7;
    color: #111113;
    border: none;
    border-radius: 15px;
    font-size: 15px;
    font-weight: 700;
}
QPushButton#SendBtn:hover {
    background-color: #ffffff;
}
QPushButton#SendBtn[running="true"] {
    background-color: #ef4444;
    color: #ffffff;
}
QFrame#AttachmentChip {
    background-color: #2a2a32;
    border: 1px solid #3a3a45;
    border-radius: 10px;
}
QFrame#SettingsCard {
    background-color: #1b1b1f;
    border: 1px solid #2b2b33;
    border-radius: 14px;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #131316;
    color: #f4f4f5;
    border: 1px solid #303039;
    border-radius: 8px;
    padding: 6px 10px;
}
QListWidget#AllowList {
    background-color: #131316;
    color: #e4e4e7;
    border: 1px solid #303039;
    border-radius: 8px;
    padding: 4px;
}
QFrame#ToastBanner {
    background-color: #dcfce7;
    color: #14532d;
    border: 1px solid #86efac;
    border-radius: 10px;
}
QFrame#ToastBanner[error="true"] {
    background-color: #fee2e2;
    color: #7f1d1d;
    border: 1px solid #fca5a5;
}
"""


@dataclass
class AttachmentItem:
    path: str
    name: str
    kind: str  # "image" | "text"
    mime_type: str
    data_b64: str = ""
    text_content: str = ""


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    text: str
    attachments: list[AttachmentItem] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "chat"
    created_at: float = field(default_factory=time.time)


@dataclass
class ConversationSession:
    id: str
    title: str = "New chat"
    messages: list[ChatMessage] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


def load_attachment_from_path(file_path: str | Path) -> AttachmentItem | None:
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return None
    ext = p.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        raw = p.read_bytes()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }
        return AttachmentItem(
            path=str(p),
            name=p.name,
            kind="image",
            mime_type=mime_map.get(ext, "image/png"),
            data_b64=base64.b64encode(raw).decode("ascii"),
        )
    # Text / code attachment (limit 256 KB so prompt stays fast)
    try:
        raw_text = p.read_text(encoding="utf-8", errors="replace")[:262144]
        return AttachmentItem(
            path=str(p),
            name=p.name,
            kind="text",
            mime_type="text/plain",
            text_content=raw_text,
        )
    except Exception:
        return None


class GuiEventBridge(QObject):
    """Thread-safe signal bridge from background GUIController/Agent threads to Qt UI."""

    gui_event = Signal(str, dict)


class ModelFetchWorker(QThread):
    finished_models = Signal(bool, list, str)

    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key

    def run(self) -> None:
        ok, models, msg = fetch_available_models(self.base_url, self.api_key, timeout=4.5)
        self.finished_models.emit(ok, models, msg)


class ChatWorker(QThread):
    completed = Signal(str, str)  # reply_text, error

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self.max_tokens = max_tokens
        self.temperature = temperature

    def run(self) -> None:
        try:
            reply = send_chat_completion(
                base_url=self.base_url,
                model=self.model,
                messages=self.messages,
                api_key=self.api_key,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            self.completed.emit(reply, "")
        except Exception as exc:
            self.completed.emit("", str(exc))


class ComputerUseWorker(QThread):
    status_changed = Signal(str, str)
    step_logged = Signal(dict)
    completed = Signal(dict, str)

    def __init__(self, runtime: AppRuntime, instruction: str, max_steps: int = 12) -> None:
        super().__init__()
        self.runtime = runtime
        self.instruction = instruction
        self.max_steps = max_steps

    def run(self) -> None:
        try:
            agent = OpenAIComputerUseAgent(
                self.runtime,
                on_status=lambda st, detail="": self.status_changed.emit(st, detail),
                on_step=lambda entry: self.step_logged.emit(entry),
            )
            res = agent.run_task(self.instruction, max_steps=self.max_steps)
            self.completed.emit(res, "")
        except Exception as exc:
            self.completed.emit({"ok": False, "history": []}, str(exc))


@dataclass
class _PiPRipple:
    nx: float
    ny: float
    started_at: float
    color: QColor
    double_wave: bool = False


class _PiPCanvasWidget(QWidget):
    """Paints live screenshot + scaled virtual cursor + click ripples + thinking spinner."""

    def __init__(self, cursor_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self._frame_pixmap: QPixmap | None = None
        self._cursor_pixmap = QPixmap(str(cursor_path)) if cursor_path.exists() else QPixmap()
        self._hotspot_rx = 0.789
        self._hotspot_ry = 0.211
        self._cursor_nx = 500.0
        self._cursor_ny = 500.0
        self._status = "idle"
        self._action_label = "Waiting for Computer Use..."
        self._ripples: list[_PiPRipple] = []
        self._spin_angle = 0.0

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._on_tick)

    def set_frame_bytes(self, raw_bytes: bytes) -> None:
        pm = QPixmap()
        if raw_bytes and pm.loadFromData(raw_bytes):
            self._frame_pixmap = pm
            self.update()

    def set_cursor_normalized(self, nx: float, ny: float) -> None:
        self._cursor_nx = max(0.0, min(1000.0, float(nx)))
        self._cursor_ny = max(0.0, min(1000.0, float(ny)))
        self.update()

    def trigger_ripple(self, nx: float, ny: float, button: str = "left", clicks: int = 1) -> None:
        self.set_cursor_normalized(nx, ny)
        color = QColor("#00D4FF")
        if clicks >= 2:
            color = QColor("#7C4DFF")
        elif button == "right":
            color = QColor("#FF9500")
        self._ripples.append(
            _PiPRipple(
                nx=self._cursor_nx,
                ny=self._cursor_ny,
                started_at=time.monotonic(),
                color=color,
                double_wave=(clicks >= 2),
            )
        )
        if not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def set_status(self, status: str, label: str = "") -> None:
        self._status = status
        if label:
            self._action_label = label
        if status == "thinking" or self._ripples:
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        self.update()

    def _on_tick(self) -> None:
        now = time.monotonic()
        self._ripples = [r for r in self._ripples if (now - r.started_at) < 0.55]
        if self._status == "thinking":
            self._spin_angle = (self._spin_angle + 12.0) % 360.0
        elif not self._ripples:
            self._anim_timer.stop()
        self.update()

    def _target_image_rect(self) -> QRectF:
        w = max(1, self.width())
        h = max(1, self.height())
        if self._frame_pixmap is None or self._frame_pixmap.isNull():
            return QRectF(0, 0, w, h)
        pw = self._frame_pixmap.width()
        ph = self._frame_pixmap.height()
        scale = min(w / max(1, pw), h / max(1, ph))
        dw = pw * scale
        dh = ph * scale
        return QRectF((w - dw) / 2.0, (h - dh) / 2.0, dw, dh)

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        painter.fillRect(self.rect(), QColor("#0d0d10"))
        img_rect = self._target_image_rect()

        if self._frame_pixmap is not None and not self._frame_pixmap.isNull():
            painter.drawPixmap(img_rect.toRect(), self._frame_pixmap)
        else:
            painter.setPen(QColor("#71717a"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Screen preview ready")

        # Map normalized 0..1000 cursor into rendered screenshot rect
        cx = img_rect.left() + (self._cursor_nx / 1000.0) * img_rect.width()
        cy = img_rect.top() + (self._cursor_ny / 1000.0) * img_rect.height()

        # Thinking spinner arcs
        if self._status == "thinking":
            r = 14.0
            pen1 = QPen(QColor("#38BDF8"), 2.4)
            pen1.setCapStyle(Qt.RoundCap)
            painter.setPen(pen1)
            painter.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), int(-self._spin_angle * 16), 115 * 16)
            pen2 = QPen(QColor("#A855F7"), 2.0)
            pen2.setCapStyle(Qt.RoundCap)
            painter.setPen(pen2)
            painter.drawArc(
                QRectF(cx - r * 0.72, cy - r * 0.72, r * 1.44, r * 1.44),
                int((self._spin_angle * 1.4 + 180) * 16),
                95 * 16,
            )

        # Click ripples
        now = time.monotonic()
        for rip in self._ripples:
            prog = min(1.0, (now - rip.started_at) / 0.55)
            rx = img_rect.left() + (rip.nx / 1000.0) * img_rect.width()
            ry = img_rect.top() + (rip.ny / 1000.0) * img_rect.height()
            col = QColor(rip.color)
            col.setAlpha(int(230 * (1.0 - prog)))
            painter.setPen(QPen(col, 2.2))
            painter.setBrush(Qt.NoBrush)
            radius = 4.0 + prog * 22.0
            painter.drawEllipse(QPointF(rx, ry), radius, radius)
            if rip.double_wave and prog > 0.18:
                p2 = (prog - 0.18) / 0.82
                col2 = QColor(rip.color)
                col2.setAlpha(int(180 * (1.0 - p2)))
                painter.setPen(QPen(col2, 1.6))
                painter.drawEllipse(QPointF(rx, ry), 4.0 + p2 * 26.0, 4.0 + p2 * 26.0)

        # Virtual cursor image
        csize = 24.0
        if not self._cursor_pixmap.isNull():
            dx = cx - csize * self._hotspot_rx
            dy = cy - csize * self._hotspot_ry
            painter.drawPixmap(QRectF(dx, dy, csize, csize).toRect(), self._cursor_pixmap)
        else:
            painter.setBrush(QColor("#38BDF8"))
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.drawEllipse(QPointF(cx, cy), 5.0, 5.0)

        # Bottom action pill overlay
        pill_rect = QRectF(8, self.height() - 28, self.width() - 16, 22)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 15, 19, 210))
        painter.drawRoundedRect(pill_rect, 8, 8)
        painter.setPen(QColor("#e4e4e7"))
        font = QFont(painter.font())
        font.setPointSize(10)
        painter.setFont(font)
        elided = painter.fontMetrics().elidedText(self._action_label, Qt.ElideRight, int(pill_rect.width() - 16))
        painter.drawText(pill_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, elided)


class PiPLivePreviewWidget(QFrame):
    """Floating Top-Right 320x200 Mini Live Preview window for Computer Use mode."""

    closed_by_user = Signal()

    def __init__(self, cursor_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PiPLivePreview")
        self.setStyleSheet(
            """
            QFrame#PiPLivePreview {
                background-color: #16161b;
                border: 1px solid #38bdf8;
                border-radius: 12px;
            }
            QPushButton#PiPHdrBtn {
                background-color: transparent;
                color: #a1a1aa;
                border: none;
                border-radius: 6px;
                padding: 2px 6px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#PiPHdrBtn:hover {
                background-color: #272730;
                color: #ffffff;
            }
            """
        )
        self._collapsed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar (32px)
        hdr = QWidget()
        hdr.setFixedHeight(32)
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(10, 0, 6, 0)
        hdr_lay.setSpacing(6)

        self.dot_lbl = QLabel("●")
        self.dot_lbl.setStyleSheet("color: #22c55e; font-size: 10px;")
        hdr_lay.addWidget(self.dot_lbl)

        self.title_lbl = QLabel("Live Preview")
        self.title_lbl.setStyleSheet("color: #f4f4f5; font-size: 11px; font-weight: 600;")
        hdr_lay.addWidget(self.title_lbl)
        hdr_lay.addStretch(1)

        self.btn_collapse = QPushButton("—")
        self.btn_collapse.setObjectName("PiPHdrBtn")
        self.btn_collapse.setToolTip("Collapse / Expand")
        self.btn_collapse.clicked.connect(self.toggle_collapsed)
        hdr_lay.addWidget(self.btn_collapse)

        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("PiPHdrBtn")
        self.btn_close.setToolTip("Close Live Preview")
        self.btn_close.clicked.connect(self.close_preview)
        hdr_lay.addWidget(self.btn_close)

        root.addWidget(hdr)

        self.canvas = _PiPCanvasWidget(cursor_path)
        self.canvas.setFixedSize(320, 200)
        root.addWidget(self.canvas)

        self.setFixedSize(322, 234)
        self.hide()

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.canvas.setVisible(not self._collapsed)
        self.btn_collapse.setText("▢" if self._collapsed else "—")
        self.setFixedSize(322, 34 if self._collapsed else 234)

    def show_live(self, expand: bool = True) -> None:
        if expand and self._collapsed:
            self.toggle_collapsed()
        self.show()
        self.raise_()

    def close_preview(self) -> None:
        self.hide()
        self.closed_by_user.emit()

    def handle_gui_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "screenshot":
            raw = payload.get("bytes", b"")
            if isinstance(raw, (bytes, bytearray)):
                self.canvas.set_frame_bytes(bytes(raw))
            target = payload.get("target", "display")
            app = payload.get("active_app", "")
            self.canvas.set_status("capturing", f"Captured {target} ({app})")
        elif kind == "move":
            self.canvas.set_cursor_normalized(payload.get("nx", 500), payload.get("ny", 500))
            self.canvas.set_status("acting", str(payload.get("label", "Moving cursor...")))
        elif kind == "click":
            self.canvas.trigger_ripple(
                payload.get("nx", 500),
                payload.get("ny", 500),
                button=str(payload.get("button", "left")),
                clicks=int(payload.get("clicks", 1)),
            )
            self.canvas.set_status("acting", str(payload.get("label", "Clicking...")))
        elif kind == "drag":
            self.canvas.set_cursor_normalized(payload.get("nx", 500), payload.get("ny", 500))
            self.canvas.set_status("acting", str(payload.get("label", "Dragging...")))
        elif kind in ("scroll", "type_text", "press_hotkey"):
            self.canvas.set_status("acting", str(payload.get("label", kind)))

    def set_agent_status(self, state: str, detail: str = "") -> None:
        color_map = {
            "thinking": "#38bdf8",
            "acting": "#22c55e",
            "capturing": "#a855f7",
            "paused": "#ef4444",
            "idle": "#71717a",
        }
        self.dot_lbl.setStyleSheet(f"color: {color_map.get(state, '#22c55e')}; font-size: 10px;")
        self.canvas.set_status(state, detail or state.capitalize())


class ArtifactCardWidget(QFrame):
    """Downloadable File Output card with Copy & Export (QFileDialog) buttons."""

    exported = Signal(str)

    def __init__(self, artifact: ExtractedArtifact, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.artifact = artifact
        self.setObjectName("ArtifactCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        icon_lbl = QLabel("📄")
        hdr.addWidget(icon_lbl)

        name_lbl = QLabel(artifact.filename)
        name_lbl.setStyleSheet("font-weight: 600; color: #f4f4f5; font-size: 13px;")
        hdr.addWidget(name_lbl)

        meta_lbl = QLabel(f"{artifact.language.upper()} · {artifact.line_count} lines · {artifact.byte_size} B")
        meta_lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
        hdr.addWidget(meta_lbl)
        hdr.addStretch(1)

        btn_copy = QPushButton("Copy")
        btn_copy.setObjectName("SmallActionBtn")
        btn_copy.clicked.connect(self._on_copy)
        hdr.addWidget(btn_copy)

        self.btn_export = QPushButton("保存 (Export)")
        self.btn_export.setObjectName("ExportBtn")
        self.btn_export.clicked.connect(self._on_export)
        hdr.addWidget(self.btn_export)

        lay.addLayout(hdr)

        preview = QPlainTextEdit()
        preview.setObjectName("ArtifactPreview")
        preview.setReadOnly(True)
        preview.setPlainText(artifact.content)
        lines = min(12, max(3, artifact.line_count))
        preview.setFixedHeight(lines * 18 + 18)
        lay.addWidget(preview)

    def _on_copy(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.artifact.content)
            self.exported.emit(f"Copied {self.artifact.filename} to clipboard")

    def export_to_path(self, dest_path: str | Path) -> Path:
        p = Path(dest_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.artifact.content, encoding="utf-8")
        self.exported.emit(f"Saved: {p.name}")
        return p

    def _on_export(self) -> None:
        default_dir = str(Path.home() / "Desktop" / self.artifact.filename)
        if not (Path.home() / "Desktop").exists():
            default_dir = str(Path.home() / self.artifact.filename)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "ファイルを保存 (Export File)",
            default_dir,
            "All Supported (*.py *.md *.txt *.csv *.json *.js *.ts *.html *.sh *.yaml);;All Files (*)",
        )
        if path:
            self.export_to_path(path)


class ComposerTextEdit(QPlainTextEdit):
    """Multi-line composer box supporting Enter-to-send and file Drag & Drop."""

    submit_requested = Signal()
    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ComposerInput")
        self.setPlaceholderText("Type a message... (Drag & drop images or files here)")
        self.setAcceptDrops(True)
        self.setMaximumHeight(130)
        self.setMinimumHeight(34)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            e.accept()
            self.submit_requested.emit()
            return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dropEvent(self, e: QDropEvent) -> None:
        if e.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                e.acceptProposedAction()
                return
        super().dropEvent(e)


class AntigravityStudioWindow(QMainWindow):
    """Unified Native App integrating Chat, Computer Use (PiP Live), File Export, and MCP Control."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        dry_run: bool = False,
        auto_fetch_models: bool = True,
        enable_resident_overlay: bool = True,
        overlay_backend: str = "auto",
        runtime_factory: Callable[[GuiEventBridge], AppRuntime] | None = None,
    ) -> None:
        super().__init__()
        self.config_path = Path(config_path or DEFAULT_CONFIG_PATH)
        self.config = AppConfig.load(self.config_path)
        self.dry_run = dry_run
        self._enable_resident_overlay = enable_resident_overlay

        self._bridge = GuiEventBridge(self)
        self._bridge.gui_event.connect(self._on_runtime_gui_event)

        if runtime_factory is not None:
            self.runtime = runtime_factory(self._bridge)
        else:
            self.runtime = AppRuntime(
                config_path=self.config_path,
                enable_overlay=enable_resident_overlay and self.config.overlay.enabled,
                enable_listeners=not dry_run,
                dry_run=dry_run,
                overlay_backend=overlay_backend,
                on_gui_event=lambda kind, payload: self._bridge.gui_event.emit(kind, payload),
            )
            self.runtime.config = self.config

        self._mode: str = "chat"  # "chat" | "computer_use"
        self._attachments: list[AttachmentItem] = []
        self._conversations: list[ConversationSession] = []
        self._active_conv_id: str = ""
        self._esc_timestamps: list[float] = []
        self._mcp_proc: subprocess.Popen[str] | None = None
        self._active_worker: QThread | None = None
        self._model_worker: ModelFetchWorker | None = None
        self._current_steps: list[dict[str, Any]] = []

        self.setWindowTitle("Antigravity AI Studio")
        self.resize(1240, 820)
        self.setMinimumSize(920, 620)
        self.setAcceptDrops(True)

        cursor_path = self.config.overlay.resolve_cursor_path(PROJECT_ROOT)
        if cursor_path.exists():
            self.setWindowIcon(QIcon(str(cursor_path)))

        self.setStyleSheet(DARK_STYLESHEET)
        self._build_ui(cursor_path)
        self._new_conversation()

        qapp = QApplication.instance()
        if qapp is not None:
            qapp.installEventFilter(self)

        # Start resident virtual cursor overlay if enabled
        if enable_resident_overlay and self.config.overlay.enabled and self.config.overlay.always_visible:
            try:
                self.runtime.start()
            except Exception:
                pass

        if auto_fetch_models:
            QTimer.singleShot(150, self.refresh_models)

    def _build_ui(self, cursor_path: Path) -> None:
        root_widget = QWidget()
        root_widget.setObjectName("RootContainer")
        self.setCentralWidget(root_widget)

        main_lay = QHBoxLayout(root_widget)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # --- Left Sidebar ---
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(238)
        sb_lay = QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(12, 14, 12, 12)
        sb_lay.setSpacing(8)

        brand_row = QHBoxLayout()
        brand_icon = QLabel()
        if cursor_path.exists():
            brand_icon.setPixmap(QPixmap(str(cursor_path)).scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            brand_icon.setText("✦")
        brand_row.addWidget(brand_icon)
        brand_lbl = QLabel("Antigravity Studio")
        brand_lbl.setStyleSheet("font-weight: 700; font-size: 14px; color: #ffffff;")
        brand_row.addWidget(brand_lbl)
        brand_row.addStretch(1)
        sb_lay.addLayout(brand_row)

        self.btn_new_chat = QPushButton("✎  New chat")
        self.btn_new_chat.setObjectName("SidebarActionBtn")
        self.btn_new_chat.clicked.connect(self._new_conversation)
        sb_lay.addWidget(self.btn_new_chat)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("SidebarSearch")
        self.search_input.setPlaceholderText("🔍 Search conversations...")
        self.search_input.textChanged.connect(self._filter_conversations)
        sb_lay.addWidget(self.search_input)

        recent_hdr = QLabel("Recent conversations")
        recent_hdr.setStyleSheet("color: #71717a; font-size: 11px; font-weight: 600; padding: 6px 4px 2px 4px;")
        sb_lay.addWidget(recent_hdr)

        self.conv_list = QListWidget()
        self.conv_list.setObjectName("ConversationList")
        self.conv_list.itemClicked.connect(self._on_conversation_clicked)
        sb_lay.addWidget(self.conv_list, 1)

        # Bottom nav group (Models / MCP / Settings) matching screenshot
        nav_box = QFrame()
        nav_box.setObjectName("SidebarBottomBox")
        nav_lay = QVBoxLayout(nav_box)
        nav_lay.setContentsMargins(6, 6, 6, 6)
        nav_lay.setSpacing(2)

        self.btn_nav_models = QPushButton("⛁  Models")
        self.btn_nav_models.setObjectName("SidebarActionBtn")
        self.btn_nav_models.clicked.connect(lambda: self._switch_page("models"))
        nav_lay.addWidget(self.btn_nav_models)

        self.btn_nav_mcp = QPushButton("⚡  MCP")
        self.btn_nav_mcp.setObjectName("SidebarActionBtn")
        self.btn_nav_mcp.clicked.connect(lambda: self._switch_page("mcp"))
        nav_lay.addWidget(self.btn_nav_mcp)

        self.btn_nav_settings = QPushButton("⚙  Settings")
        self.btn_nav_settings.setObjectName("SidebarActionBtn")
        self.btn_nav_settings.clicked.connect(lambda: self._switch_page("settings"))
        nav_lay.addWidget(self.btn_nav_settings)

        sb_lay.addWidget(nav_box)
        main_lay.addWidget(sidebar)

        # --- Right Main Column ---
        right_col = QWidget()
        right_lay = QVBoxLayout(right_col)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        # Top tab bar
        top_bar = QWidget()
        top_bar.setObjectName("TopTabBar")
        top_bar.setFixedHeight(44)
        top_lay = QHBoxLayout(top_bar)
        top_lay.setContentsMargins(14, 6, 14, 6)
        top_lay.setSpacing(8)

        self.tabs_container = QHBoxLayout()
        self.tabs_container.setSpacing(4)
        top_lay.addLayout(self.tabs_container)
        top_lay.addStretch(1)

        self.btn_pip_toggle = QPushButton("▣ PiP Live")
        self.btn_pip_toggle.setObjectName("TopToolBtn")
        self.btn_pip_toggle.setCheckable(True)
        self.btn_pip_toggle.clicked.connect(self._toggle_pip_visibility)
        top_lay.addWidget(self.btn_pip_toggle)

        self.btn_allowed_apps = QPushButton("🛡 Allowed Apps")
        self.btn_allowed_apps.setObjectName("TopToolBtn")
        self.btn_allowed_apps.clicked.connect(lambda: self._switch_page("settings"))
        top_lay.addWidget(self.btn_allowed_apps)

        self.btn_emergency_stop = QPushButton("⏹ Emergency Stop (Esc×2)")
        self.btn_emergency_stop.setObjectName("EmergencyStopBtn")
        self.btn_emergency_stop.clicked.connect(self.toggle_emergency_stop)
        top_lay.addWidget(self.btn_emergency_stop)

        right_lay.addWidget(top_bar)

        # Stacked main pages (chat / models / mcp / settings)
        self.stack = QStackedWidget()
        self.chat_page = self._build_chat_page(cursor_path)
        self.models_page = self._build_models_page()
        self.mcp_page = self._build_mcp_page()
        self.settings_page = self._build_settings_page()

        self.stack.addWidget(self.chat_page)
        self.stack.addWidget(self.models_page)
        self.stack.addWidget(self.mcp_page)
        self.stack.addWidget(self.settings_page)
        right_lay.addWidget(self.stack, 1)

        main_lay.addWidget(right_col, 1)

        # Floating bottom-right toast banner (like llama-ui Model loaded card)
        self.toast_banner = QFrame(self.chat_page)
        self.toast_banner.setObjectName("ToastBanner")
        toast_lay = QHBoxLayout(self.toast_banner)
        toast_lay.setContentsMargins(12, 8, 12, 8)
        self.toast_lbl = QLabel("")
        self.toast_lbl.setStyleSheet("font-size: 12px; font-weight: 500;")
        toast_lay.addWidget(self.toast_lbl)
        self.toast_banner.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_banner.hide)

    def _build_chat_page(self, cursor_path: Path) -> QWidget:
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.setSpacing(0)

        # Chat Scroll Area
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("ChatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.chat_contents = QWidget()
        self.chat_contents.setObjectName("ChatScrollContents")
        self.chat_feed_lay = QVBoxLayout(self.chat_contents)
        self.chat_feed_lay.setContentsMargins(60, 28, 60, 18)
        self.chat_feed_lay.setSpacing(14)

        # Empty State ("Hello there")
        self.empty_state = QWidget()
        empty_lay = QVBoxLayout(self.empty_state)
        empty_lay.setAlignment(Qt.AlignCenter)
        empty_lay.setSpacing(6)
        hello_lbl = QLabel("Hello there")
        hello_lbl.setAlignment(Qt.AlignCenter)
        hello_lbl.setStyleSheet("font-size: 26px; font-weight: 700; color: #f4f4f5;")
        sub_lbl = QLabel("Type a message or upload files to get started")
        sub_lbl.setAlignment(Qt.AlignCenter)
        sub_lbl.setStyleSheet("font-size: 14px; color: #8e8ea0;")
        empty_lay.addWidget(hello_lbl)
        empty_lay.addWidget(sub_lbl)

        self.chat_feed_lay.addStretch(1)
        self.chat_feed_lay.addWidget(self.empty_state)
        self.chat_feed_lay.addStretch(1)

        self.chat_scroll.setWidget(self.chat_contents)
        page_lay.addWidget(self.chat_scroll, 1)

        # Composer Container
        composer_wrap = QWidget()
        composer_wrap_lay = QHBoxLayout(composer_wrap)
        composer_wrap_lay.setContentsMargins(60, 6, 60, 22)

        self.composer_pill = QFrame()
        self.composer_pill.setObjectName("ComposerPill")
        self.composer_pill.setMaximumWidth(860)
        pill_lay = QVBoxLayout(self.composer_pill)
        pill_lay.setContentsMargins(14, 10, 12, 10)
        pill_lay.setSpacing(6)

        # Attachment chip row (hidden when empty)
        self.attach_bar = QWidget()
        self.attach_bar_lay = QHBoxLayout(self.attach_bar)
        self.attach_bar_lay.setContentsMargins(2, 0, 2, 2)
        self.attach_bar_lay.setSpacing(6)
        self.attach_bar_lay.addStretch(1)
        self.attach_bar.hide()
        pill_lay.addWidget(self.attach_bar)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self.btn_attach = QPushButton("+")
        self.btn_attach.setObjectName("AttachBtn")
        self.btn_attach.setFixedSize(30, 30)
        self.btn_attach.setToolTip("Attach PNG/JPG image or code/text file")
        self.btn_attach.clicked.connect(self._on_attach_files)
        input_row.addWidget(self.btn_attach, 0, Qt.AlignVCenter)

        self.composer_input = ComposerTextEdit()
        self.composer_input.submit_requested.connect(self._on_send_clicked)
        self.composer_input.files_dropped.connect(self.add_attachments)
        input_row.addWidget(self.composer_input, 1)

        pill_lay.addLayout(input_row)

        # Bottom inline control row (Chat/Computer Use switch + Model Dropdown + Send)
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)

        self.btn_mode_chat = QPushButton("💬 Chat")
        self.btn_mode_chat.setObjectName("ModeBtn")
        self.btn_mode_chat.setCheckable(True)
        self.btn_mode_chat.setChecked(True)
        self.btn_mode_chat.clicked.connect(lambda: self.set_mode("chat"))
        ctrl_row.addWidget(self.btn_mode_chat)

        self.btn_mode_cu = QPushButton("🖥 Computer Use")
        self.btn_mode_cu.setObjectName("ModeBtnComputer")
        self.btn_mode_cu.setCheckable(True)
        self.btn_mode_cu.clicked.connect(lambda: self.set_mode("computer_use"))
        ctrl_row.addWidget(self.btn_mode_cu)

        self.target_badge = QComboBox()
        self.target_badge.addItems(["display", "screen_sharing", "active_window"])
        self.target_badge.setCurrentText(self.config.vlm.capture_target)
        self.target_badge.setToolTip("Capture Target")
        self.target_badge.setFixedWidth(130)
        self.target_badge.currentTextChanged.connect(self._on_target_changed)
        self.target_badge.hide()
        ctrl_row.addWidget(self.target_badge)

        ctrl_row.addStretch(1)

        self.model_combo = QComboBox()
        self.model_combo.setObjectName("ModelCombo")
        self.model_combo.setEditable(True)
        self.model_combo.setCurrentText(self.config.vlm.model)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        ctrl_row.addWidget(self.model_combo)

        self.btn_refresh_models = QPushButton("↻")
        self.btn_refresh_models.setObjectName("SmallActionBtn")
        self.btn_refresh_models.setFixedSize(28, 28)
        self.btn_refresh_models.setToolTip("Fetch models from /v1/models")
        self.btn_refresh_models.clicked.connect(self.refresh_models)
        ctrl_row.addWidget(self.btn_refresh_models)

        self.btn_send = QPushButton("↑")
        self.btn_send.setObjectName("SendBtn")
        self.btn_send.setFixedSize(32, 32)
        self.btn_send.clicked.connect(self._on_send_clicked)
        ctrl_row.addWidget(self.btn_send)

        pill_lay.addLayout(ctrl_row)
        composer_wrap_lay.addStretch(1)
        composer_wrap_lay.addWidget(self.composer_pill, 1)
        composer_wrap_lay.addStretch(1)
        page_lay.addWidget(composer_wrap)

        # Top-Right Floating Mini Live Preview (PiP)
        self.pip_preview = PiPLivePreviewWidget(cursor_path, parent=page)
        self.pip_preview.closed_by_user.connect(lambda: self.btn_pip_toggle.setChecked(False))
        return page

    def _build_models_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 28, 40, 28)
        outer.setSpacing(14)

        title = QLabel("Models & OpenAI-Compatible Endpoint")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(title)

        card = QFrame()
        card.setObjectName("SettingsCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(18, 18, 18, 18)
        c_lay.setSpacing(12)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("API Base URL:"))
        self.edit_base_url = QLineEdit(self.config.vlm.base_url)
        self.edit_base_url.setPlaceholderText("http://100.121.235.97:8000/v1")
        row1.addWidget(self.edit_base_url, 1)
        btn_test = QPushButton("Fetch /v1/models")
        btn_test.setObjectName("ExportBtn")
        btn_test.clicked.connect(self.refresh_models)
        row1.addWidget(btn_test)
        c_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("API Key:"))
        self.edit_api_key = QLineEdit(self.config.vlm.api_key)
        row2.addWidget(self.edit_api_key, 1)
        row2.addWidget(QLabel("Max Tokens:"))
        self.spin_max_tokens = QSpinBox()
        self.spin_max_tokens.setRange(128, 32768)
        self.spin_max_tokens.setValue(self.config.vlm.max_tokens)
        row2.addWidget(self.spin_max_tokens)
        row2.addWidget(QLabel("Temp:"))
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(0.0, 2.0)
        self.spin_temp.setSingleStep(0.05)
        self.spin_temp.setValue(self.config.vlm.temperature)
        row2.addWidget(self.spin_temp)
        c_lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.chk_video_mode = QCheckBox("Gemma 4 連続フレーム (Video Mode) を有効にする")
        self.chk_video_mode.setChecked(self.config.vlm.video_mode)
        row3.addWidget(self.chk_video_mode)
        row3.addStretch(1)
        row3.addWidget(QLabel("Frames:"))
        self.spin_video_frames = QSpinBox()
        self.spin_video_frames.setRange(1, 10)
        self.spin_video_frames.setValue(self.config.vlm.video_frames)
        row3.addWidget(self.spin_video_frames)
        c_lay.addLayout(row3)

        self.models_status_lbl = QLabel("Endpoint ready.")
        self.models_status_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        c_lay.addWidget(self.models_status_lbl)

        btn_save = QPushButton("Save & Apply Endpoint Settings")
        btn_save.setObjectName("ExportBtn")
        btn_save.clicked.connect(self._save_all_settings)
        c_lay.addWidget(btn_save, 0, Qt.AlignRight)

        outer.addWidget(card)
        outer.addStretch(1)
        return page

    def _build_mcp_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 28, 40, 28)
        outer.setSpacing(14)

        title = QLabel("MCP Server & Mac Screen Sharing")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(title)

        card1 = QFrame()
        card1.setObjectName("SettingsCard")
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(18, 18, 18, 18)
        c1.setSpacing(10)

        self.mcp_status_lbl = QLabel(f"MCP Streamable-HTTP: Stopped (Port {self.config.server.port})")
        c1.addWidget(self.mcp_status_lbl)
        r1 = QHBoxLayout()
        self.btn_mcp_http = QPushButton(f"Start MCP HTTP Server (:{self.config.server.port})")
        self.btn_mcp_http.setObjectName("ExportBtn")
        self.btn_mcp_http.clicked.connect(self._toggle_mcp_http)
        r1.addWidget(self.btn_mcp_http)

        btn_copy_json = QPushButton("Copy MCP JSON Config")
        btn_copy_json.setObjectName("SmallActionBtn")
        btn_copy_json.clicked.connect(self._copy_mcp_json)
        r1.addWidget(btn_copy_json)

        btn_claude = QPushButton("Install to Claude Desktop")
        btn_claude.setObjectName("SmallActionBtn")
        btn_claude.clicked.connect(self._install_claude_desktop)
        r1.addWidget(btn_claude)
        r1.addStretch(1)
        c1.addLayout(r1)
        outer.addWidget(card1)

        card2 = QFrame()
        card2.setObjectName("SettingsCard")
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(18, 18, 18, 18)
        c2.setSpacing(10)
        c2.addWidget(QLabel("Mac Screen Sharing / Virtual Cursor Overlay"))
        r2 = QHBoxLayout()
        self.edit_vnc_host = QLineEdit()
        self.edit_vnc_host.setPlaceholderText("Optional VNC host (e.g. 192.168.64.2 or macmini.local)")
        r2.addWidget(self.edit_vnc_host, 1)
        btn_vnc = QPushButton("Open Mac Screen Sharing")
        btn_vnc.setObjectName("SmallActionBtn")
        btn_vnc.clicked.connect(self._open_screen_sharing)
        r2.addWidget(btn_vnc)

        btn_demo = QPushButton("Play Cursor Demo (Spin + Ripple)")
        btn_demo.setObjectName("SmallActionBtn")
        btn_demo.clicked.connect(self._run_cursor_demo)
        r2.addWidget(btn_demo)
        c2.addLayout(r2)
        outer.addWidget(card2)

        outer.addStretch(1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 28, 40, 28)
        outer.setSpacing(14)

        title = QLabel("Safety Guardrails, Allowed Apps & OS Permissions")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(title)

        perm_card = QFrame()
        perm_card.setObjectName("SettingsCard")
        p_lay = QVBoxLayout(perm_card)
        p_lay.setContentsMargins(16, 14, 16, 14)
        self.perm_lbl = QLabel(json.dumps(check_os_permissions(), ensure_ascii=False, indent=2))
        self.perm_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #d4d4d8;")
        self.perm_lbl.setWordWrap(True)
        p_lay.addWidget(self.perm_lbl)

        if sys.platform == "darwin":
            mac_r = QHBoxLayout()
            b1 = QPushButton("Open Screen Recording Settings")
            b1.setObjectName("SmallActionBtn")
            b1.clicked.connect(
                lambda: subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])
            )
            b2 = QPushButton("Open Accessibility Settings")
            b2.setObjectName("SmallActionBtn")
            b2.clicked.connect(
                lambda: subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
            )
            mac_r.addWidget(b1)
            mac_r.addWidget(b2)
            mac_r.addStretch(1)
            p_lay.addLayout(mac_r)
        outer.addWidget(perm_card)

        allow_card = QFrame()
        allow_card.setObjectName("SettingsCard")
        a_lay = QVBoxLayout(allow_card)
        a_lay.setContentsMargins(16, 14, 16, 14)
        a_lay.setSpacing(8)
        a_lay.addWidget(QLabel("Allowed Applications (always_allowed_apps)"))

        self.allow_list_widget = QListWidget()
        self.allow_list_widget.setObjectName("AllowList")
        for app in self.config.safety.always_allowed_apps:
            self.allow_list_widget.addItem(app)
        a_lay.addWidget(self.allow_list_widget, 1)

        add_r = QHBoxLayout()
        self.edit_new_allowed_app = QLineEdit()
        self.edit_new_allowed_app.setPlaceholderText("App name (e.g. Safari, Finder, Screen Sharing)")
        add_r.addWidget(self.edit_new_allowed_app, 1)
        btn_add = QPushButton("Add")
        btn_add.setObjectName("ExportBtn")
        btn_add.clicked.connect(self._add_allowed_app)
        add_r.addWidget(btn_add)
        btn_rem = QPushButton("Remove Selected")
        btn_rem.setObjectName("SmallActionBtn")
        btn_rem.clicked.connect(self._remove_allowed_app)
        add_r.addWidget(btn_rem)
        a_lay.addLayout(add_r)

        ov_r = QHBoxLayout()
        self.chk_always_cursor = QCheckBox("Keep AI Virtual Cursor always visible on screen")
        self.chk_always_cursor.setChecked(self.config.overlay.always_visible)
        ov_r.addWidget(self.chk_always_cursor)
        ov_r.addStretch(1)
        btn_save_cfg = QPushButton("Save config.json")
        btn_save_cfg.setObjectName("ExportBtn")
        btn_save_cfg.clicked.connect(self._save_all_settings)
        ov_r.addWidget(btn_save_cfg)
        a_lay.addLayout(ov_r)

        outer.addWidget(allow_card, 1)
        return page

    # --- Layout / Resize / Keyboard ---

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._position_floating_widgets()

    def _position_floating_widgets(self) -> None:
        if hasattr(self, "pip_preview") and hasattr(self, "chat_page"):
            px = max(12, self.chat_page.width() - self.pip_preview.width() - 18)
            self.pip_preview.move(px, 14)
            self.pip_preview.raise_()
        if hasattr(self, "toast_banner") and hasattr(self, "chat_page"):
            self.toast_banner.adjustSize()
            tx = max(12, self.chat_page.width() - self.toast_banner.width() - 18)
            ty = max(12, self.chat_page.height() - self.toast_banner.height() - 18)
            self.toast_banner.move(tx, ty)
            self.toast_banner.raise_()

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if event is not None and event.type() == QEvent.KeyPress:
            if isinstance(event, QKeyEvent) and event.key() == Qt.Key_Escape:
                now = time.monotonic()
                self._esc_timestamps = [t for t in self._esc_timestamps if (now - t) <= 1.2]
                self._esc_timestamps.append(now)
                if len(self._esc_timestamps) >= 2:
                    self._esc_timestamps.clear()
                    self.trigger_emergency_stop("esc_double_tap_ui")
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            now = time.monotonic()
            self._esc_timestamps = [t for t in self._esc_timestamps if (now - t) <= 1.2]
            self._esc_timestamps.append(now)
            if len(self._esc_timestamps) >= 2:
                self._esc_timestamps.clear()
                self.trigger_emergency_stop("esc_double_tap_ui")
                event.accept()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.add_attachments(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    # --- Navigation & Conversations ---

    def _switch_page(self, page_name: str) -> None:
        idx_map = {"chat": 0, "models": 1, "mcp": 2, "settings": 3}
        self.stack.setCurrentIndex(idx_map.get(page_name, 0))
        self.btn_nav_models.setProperty("active", page_name == "models")
        self.btn_nav_mcp.setProperty("active", page_name == "mcp")
        self.btn_nav_settings.setProperty("active", page_name == "settings")
        for b in (self.btn_nav_models, self.btn_nav_mcp, self.btn_nav_settings):
            b.style().unpolish(b)
            b.style().polish(b)
        if page_name == "chat":
            self._position_floating_widgets()

    def _get_active_conversation(self) -> ConversationSession:
        for conv in self._conversations:
            if conv.id == self._active_conv_id:
                return conv
        if not self._conversations:
            return self._new_conversation()
        return self._conversations[0]

    def _new_conversation(self) -> ConversationSession:
        conv = ConversationSession(id=uuid.uuid4().hex[:8], title="New chat")
        self._conversations.insert(0, conv)
        self._active_conv_id = conv.id
        self._switch_page("chat")
        self._sync_conversation_views()
        self._render_messages()
        return conv

    def _select_conversation(self, conv_id: str) -> None:
        self._active_conv_id = conv_id
        self._switch_page("chat")
        self._sync_conversation_views()
        self._render_messages()

    def _close_conversation(self, conv_id: str) -> None:
        self._conversations = [c for c in self._conversations if c.id != conv_id]
        if not self._conversations:
            self._new_conversation()
            return
        if self._active_conv_id == conv_id:
            self._active_conv_id = self._conversations[0].id
        self._sync_conversation_views()
        self._render_messages()

    def _on_conversation_clicked(self, item: QListWidgetItem) -> None:
        cid = item.data(Qt.UserRole)
        if cid:
            self._select_conversation(str(cid))

    def _filter_conversations(self, _text: str) -> None:
        self._sync_conversation_views()

    def _sync_conversation_views(self) -> None:
        query = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        self.conv_list.clear()
        for conv in self._conversations:
            if query and query not in conv.title.lower():
                continue
            item = QListWidgetItem(conv.title)
            item.setData(Qt.UserRole, conv.id)
            self.conv_list.addItem(item)
            if conv.id == self._active_conv_id:
                item.setSelected(True)

        # Rebuild top tabs (up to 5 recent tabs)
        while self.tabs_container.count():
            child = self.tabs_container.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()

        for conv in self._conversations[:5]:
            tab_btn = QPushButton(f"{conv.title[:18]}  ×")
            tab_btn.setObjectName("TabPill")
            tab_btn.setProperty("active", conv.id == self._active_conv_id)
            tab_btn.clicked.connect(lambda _=False, cid=conv.id: self._select_conversation(cid))
            self.tabs_container.addWidget(tab_btn)

    # --- Toast & Status ---

    def show_toast(self, message: str, error: bool = False, duration_ms: int = 3600) -> None:
        self.toast_lbl.setText(message)
        self.toast_banner.setProperty("error", error)
        self.toast_banner.style().unpolish(self.toast_banner)
        self.toast_banner.style().polish(self.toast_banner)
        self.toast_banner.show()
        self._position_floating_widgets()
        self._toast_timer.start(duration_ms)

    def _toggle_pip_visibility(self) -> None:
        if self.pip_preview.isVisible():
            self.pip_preview.hide()
            self.btn_pip_toggle.setChecked(False)
        else:
            self.pip_preview.show_live(expand=True)
            self.btn_pip_toggle.setChecked(True)
            self._position_floating_widgets()

    # --- Mode & Model Handling ---

    def set_mode(self, mode: str) -> None:
        self._mode = "computer_use" if mode == "computer_use" else "chat"
        self.btn_mode_chat.setChecked(self._mode == "chat")
        self.btn_mode_cu.setChecked(self._mode == "computer_use")
        self.target_badge.setVisible(self._mode == "computer_use")
        if self._mode == "computer_use":
            self.composer_input.setPlaceholderText("Describe a desktop task for AI Computer Use... (Enter to run)")
        else:
            self.composer_input.setPlaceholderText("Type a message... (Drag & drop images or files here)")

    def _on_target_changed(self, target: str) -> None:
        if target in ("display", "screen_sharing", "active_window"):
            self.config.vlm.capture_target = target  # type: ignore[assignment]

    def _on_model_changed(self, model_name: str) -> None:
        if model_name.strip():
            self.config.vlm.model = model_name.strip()

    def refresh_models(self) -> None:
        base_url = self.edit_base_url.text().strip() if hasattr(self, "edit_base_url") else self.config.vlm.base_url
        api_key = self.edit_api_key.text().strip() if hasattr(self, "edit_api_key") else self.config.vlm.api_key
        if not base_url:
            return
        self.config.vlm.base_url = normalize_base_url(base_url)
        self.config.vlm.api_key = api_key or "EMPTY"
        self._model_worker = ModelFetchWorker(self.config.vlm.base_url, self.config.vlm.api_key)
        self._model_worker.finished_models.connect(self._on_models_fetched)
        self._model_worker.start()

    def _on_models_fetched(self, ok: bool, models: list[str], msg: str) -> None:
        if hasattr(self, "models_status_lbl"):
            self.models_status_lbl.setText(msg)
        if ok and models:
            cur = self.model_combo.currentText().strip()
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(models)
            if cur and cur in models:
                self.model_combo.setCurrentText(cur)
            elif self.config.vlm.model in models:
                self.model_combo.setCurrentText(self.config.vlm.model)
            else:
                self.model_combo.setCurrentText(models[0])
                self.config.vlm.model = models[0]
            self.model_combo.blockSignals(False)
            self.show_toast(f"Model loaded: {self.model_combo.currentText()}")
        elif not ok:
            self.show_toast(f"Endpoint offline: {msg[:60]}", error=True)

    # --- Attachments ---

    def _on_attach_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "ファイルを添付 (Attach Image / Code / Text)",
            str(Path.home()),
            "Images & Code (*.png *.jpg *.jpeg *.webp *.py *.md *.txt *.csv *.json *.js *.ts *.html *.sh);;All Files (*)",
        )
        if paths:
            self.add_attachments(paths)

    def add_attachments(self, paths: list[str]) -> int:
        added = 0
        for raw_path in paths:
            item = load_attachment_from_path(raw_path)
            if item is not None:
                self._attachments.append(item)
                added += 1
        self._rebuild_attachment_bar()
        return added

    def remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self._attachments):
            self._attachments.pop(index)
            self._rebuild_attachment_bar()

    def _rebuild_attachment_bar(self) -> None:
        while self.attach_bar_lay.count():
            child = self.attach_bar_lay.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()

        if not self._attachments:
            self.attach_bar.hide()
            return

        for idx, att in enumerate(self._attachments):
            chip = QFrame()
            chip.setObjectName("AttachmentChip")
            c_lay = QHBoxLayout(chip)
            c_lay.setContentsMargins(8, 4, 6, 4)
            c_lay.setSpacing(6)
            icon_txt = "🖼" if att.kind == "image" else "📄"
            lbl = QLabel(f"{icon_txt} {att.name}")
            lbl.setStyleSheet("font-size: 11px; color: #e4e4e7;")
            c_lay.addWidget(lbl)
            rm = QPushButton("×")
            rm.setStyleSheet("background: transparent; border: none; color: #9ca3af; font-weight: bold;")
            rm.clicked.connect(lambda _=False, i=idx: self.remove_attachment(i))
            c_lay.addWidget(rm)
            self.attach_bar_lay.addWidget(chip)

        self.attach_bar_lay.addStretch(1)
        self.attach_bar.show()

    # --- Chat & Computer Use Execution ---

    def _on_send_clicked(self) -> None:
        if self._active_worker is not None and self._active_worker.isRunning():
            self.trigger_emergency_stop("stop_button")
            return

        text = self.composer_input.toPlainText().strip()
        if not text and not self._attachments:
            return

        conv = self._get_active_conversation()
        if conv.title == "New chat" and text:
            conv.title = text[:26]
        conv.updated_at = time.time()

        sent_attachments = list(self._attachments)
        self._attachments.clear()
        self._rebuild_attachment_bar()
        self.composer_input.clear()

        user_msg = ChatMessage(role="user", text=text, attachments=sent_attachments, mode=self._mode)
        conv.messages.append(user_msg)
        self._sync_conversation_views()
        self._render_messages()

        self._set_running(True)
        if self._mode == "computer_use":
            self._start_computer_use_task(text, sent_attachments)
        else:
            self._start_chat_turn(conv)

    def _set_running(self, running: bool) -> None:
        self.btn_send.setProperty("running", running)
        self.btn_send.setText("■" if running else "↑")
        self.btn_send.style().unpolish(self.btn_send)
        self.btn_send.style().polish(self.btn_send)

    def _build_openai_chat_messages(self, conv: ConversationSession) -> list[dict[str, Any]]:
        api_msgs: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are Antigravity AI Studio, a helpful coding and multimodal assistant. "
                    "When generating files or code for export, include clear filenames in fenced code blocks "
                    "(e.g. ```python:script.py) so the user can export them with one click."
                ),
            }
        ]
        for msg in conv.messages:
            if msg.role == "assistant":
                api_msgs.append({"role": "assistant", "content": msg.text})
                continue

            has_images = any(a.kind == "image" for a in msg.attachments)
            text_parts = [msg.text] if msg.text else []
            for a in msg.attachments:
                if a.kind == "text":
                    text_parts.append(f"\n[Attached File: {a.name}]\n```\n{a.text_content}\n```")
            combined_text = "\n".join(text_parts).strip()

            if has_images:
                parts: list[dict[str, Any]] = []
                if combined_text:
                    parts.append({"type": "text", "text": combined_text})
                for a in msg.attachments:
                    if a.kind == "image":
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{a.mime_type};base64,{a.data_b64}"},
                            }
                        )
                api_msgs.append({"role": "user", "content": parts})
            else:
                api_msgs.append({"role": "user", "content": combined_text})
        return api_msgs

    def _start_chat_turn(self, conv: ConversationSession) -> None:
        worker = ChatWorker(
            base_url=self.config.vlm.base_url,
            api_key=self.config.vlm.api_key,
            model=self.model_combo.currentText().strip() or self.config.vlm.model,
            messages=self._build_openai_chat_messages(conv),
            max_tokens=self.config.vlm.max_tokens,
            temperature=max(0.2, self.config.vlm.temperature),
        )
        worker.completed.connect(self._on_chat_completed)
        self._active_worker = worker
        worker.start()

    def _on_chat_completed(self, reply: str, error: str) -> None:
        self._set_running(False)
        conv = self._get_active_conversation()
        if error:
            conv.messages.append(ChatMessage(role="assistant", text=f"Error communicating with server: `{error}`", mode="chat"))
        else:
            conv.messages.append(ChatMessage(role="assistant", text=reply or "(empty response)", mode="chat"))
        self._render_messages()

    def _start_computer_use_task(self, instruction: str, attachments: list[AttachmentItem]) -> None:
        self._current_steps = []
        if self.runtime.safety.state.paused:
            self.runtime.safety.resume()
            self._update_emergency_btn()

        # Sync latest UI model & target
        self.config.vlm.model = self.model_combo.currentText().strip() or self.config.vlm.model
        self.config.vlm.capture_target = self.target_badge.currentText()  # type: ignore[assignment]

        # Auto-show Top-Right PiP Live Preview
        self.pip_preview.show_live(expand=True)
        self.btn_pip_toggle.setChecked(True)
        self.pip_preview.set_agent_status("capturing", "Starting Computer Use...")
        self._position_floating_widgets()

        full_instruction = instruction
        for a in attachments:
            if a.kind == "text":
                full_instruction += f"\n\n[Attached File: {a.name}]\n{a.text_content[:4000]}"

        worker = ComputerUseWorker(self.runtime, full_instruction, max_steps=12)
        worker.status_changed.connect(self.pip_preview.set_agent_status)
        worker.step_logged.connect(lambda entry: self._current_steps.append(entry))
        worker.completed.connect(self._on_computer_use_completed)
        self._active_worker = worker
        worker.start()

    def _on_runtime_gui_event(self, kind: str, payload: dict[str, Any]) -> None:
        self.pip_preview.handle_gui_event(kind, payload)

    def _on_computer_use_completed(self, res: dict[str, Any], error: str) -> None:
        self._set_running(False)
        self._update_emergency_btn()
        conv = self._get_active_conversation()
        if error:
            text = f"Computer Use error: `{error}`"
            self.pip_preview.set_agent_status("paused", error[:48])
        elif not res.get("ok", True):
            reason = res.get("stopped_reason", "Stopped")
            text = f"Stopped: {reason}"
            self.pip_preview.set_agent_status("paused", str(reason)[:48])
        else:
            text = str(res.get("final_response") or "Computer Use task completed.")
            self.pip_preview.set_agent_status("idle", "Completed")

        conv.messages.append(
            ChatMessage(
                role="assistant",
                text=text,
                steps=list(res.get("history") or self._current_steps),
                mode="computer_use",
            )
        )
        self._render_messages()

    # --- Emergency Stop ---

    def trigger_emergency_stop(self, reason: str = "studio_button") -> None:
        self.runtime.safety.trigger_kill_switch(reason)
        if self.config.overlay.enabled:
            try:
                self.runtime.overlay.set_status("paused")
            except Exception:
                pass
        self.pip_preview.set_agent_status("paused", f"Emergency Stop ({reason})")
        self._update_emergency_btn()
        self.show_toast("Emergency Stop activated — AI paused", error=True)

    def toggle_emergency_stop(self) -> None:
        if self.runtime.safety.state.paused:
            self.runtime.safety.resume()
            if self.config.overlay.enabled:
                try:
                    self.runtime.overlay.set_status("idle")
                except Exception:
                    pass
            self.pip_preview.set_agent_status("idle", "Resumed")
            self._update_emergency_btn()
            self.show_toast("Automation resumed")
        else:
            self.trigger_emergency_stop("studio_button")

    def _update_emergency_btn(self) -> None:
        paused = self.runtime.safety.state.paused
        self.btn_emergency_stop.setProperty("paused", paused)
        self.btn_emergency_stop.setText("▶ Resume AI" if paused else "⏹ Emergency Stop (Esc×2)")
        self.btn_emergency_stop.style().unpolish(self.btn_emergency_stop)
        self.btn_emergency_stop.style().polish(self.btn_emergency_stop)

    # --- Message Feed Rendering ---

    def _render_messages(self) -> None:
        while self.chat_feed_lay.count():
            item = self.chat_feed_lay.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None and w is not self.empty_state:
                w.deleteLater()

        conv = self._get_active_conversation()
        if not conv.messages:
            self.empty_state.show()
            self.chat_feed_lay.addStretch(1)
            self.chat_feed_lay.addWidget(self.empty_state)
            self.chat_feed_lay.addStretch(1)
            self._position_floating_widgets()
            return

        self.empty_state.hide()
        for msg in conv.messages:
            bubble = self._create_message_widget(msg)
            self.chat_feed_lay.addWidget(bubble)
        self.chat_feed_lay.addStretch(1)

        QTimer.singleShot(
            20,
            lambda: self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum()),
        )
        self._position_floating_widgets()

    def _create_message_widget(self, msg: ChatMessage) -> QWidget:
        frame = QFrame()
        frame.setObjectName("UserBubble" if msg.role == "user" else "AssistantBubble")
        frame.setMaximumWidth(860)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        role_txt = "You" if msg.role == "user" else ("Computer Use Agent" if msg.mode == "computer_use" else "Assistant")
        hdr = QLabel(role_txt)
        hdr.setStyleSheet("font-size: 11px; font-weight: 700; color: #9ca3af;")
        lay.addWidget(hdr)

        if msg.attachments:
            att_row = QHBoxLayout()
            att_row.setSpacing(8)
            for a in msg.attachments:
                if a.kind == "image" and Path(a.path).exists():
                    thumb = QLabel()
                    pm = QPixmap(a.path).scaled(96, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    thumb.setPixmap(pm)
                    thumb.setStyleSheet("border: 1px solid #3a3a45; border-radius: 6px; padding: 2px;")
                    att_row.addWidget(thumb)
                else:
                    chip = QLabel(f"📄 {a.name}")
                    chip.setStyleSheet("background: #2b2b34; border-radius: 6px; padding: 4px 8px; font-size: 11px;")
                    att_row.addWidget(chip)
            att_row.addStretch(1)
            lay.addLayout(att_row)

        if msg.steps:
            step_lines = [
                f"• Step {s.get('step')}: {s.get('tool')}({json.dumps(s.get('args', {}), ensure_ascii=False)[:60]})"
                for s in msg.steps
            ]
            steps_lbl = QLabel("\n".join(step_lines))
            steps_lbl.setStyleSheet("color: #7dd3fc; font-family: monospace; font-size: 11px;")
            steps_lbl.setWordWrap(True)
            lay.addWidget(steps_lbl)

        if msg.text:
            body_lbl = QLabel(msg.text)
            body_lbl.setWordWrap(True)
            body_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body_lbl.setStyleSheet("color: #f4f4f5; font-size: 13.5px; line-height: 1.45;")
            lay.addWidget(body_lbl)

        if msg.role == "assistant" and msg.text:
            for art in extract_artifacts(msg.text):
                card = ArtifactCardWidget(art)
                card.exported.connect(lambda m: self.show_toast(m))
                lay.addWidget(card)

        wrapper = QWidget()
        w_lay = QHBoxLayout(wrapper)
        w_lay.setContentsMargins(0, 0, 0, 0)
        if msg.role == "user":
            w_lay.addStretch(1)
            w_lay.addWidget(frame, 4)
        else:
            w_lay.addWidget(frame, 5)
            w_lay.addStretch(1)
        return wrapper

    # --- Settings / MCP / Overlay Actions ---

    def _save_all_settings(self) -> None:
        self.config.vlm.base_url = normalize_base_url(self.edit_base_url.text().strip() or self.config.vlm.base_url)
        self.config.vlm.api_key = self.edit_api_key.text().strip() or "EMPTY"
        self.config.vlm.model = self.model_combo.currentText().strip() or self.config.vlm.model
        self.config.vlm.max_tokens = int(self.spin_max_tokens.value())
        self.config.vlm.temperature = float(self.spin_temp.value())
        self.config.vlm.video_mode = bool(self.chk_video_mode.isChecked())
        self.config.vlm.video_frames = int(self.spin_video_frames.value())
        self.config.vlm.capture_target = self.target_badge.currentText()  # type: ignore[assignment]
        self.config.overlay.always_visible = bool(self.chk_always_cursor.isChecked())
        self.config.safety.always_allowed_apps = [
            self.allow_list_widget.item(i).text() for i in range(self.allow_list_widget.count())
        ]
        saved_path = self.config.save(self.config_path)
        self.show_toast(f"Saved settings to {saved_path.name}")

    def _add_allowed_app(self) -> None:
        name = self.edit_new_allowed_app.text().strip()
        if not name:
            return
        existing = {self.allow_list_widget.item(i).text().lower() for i in range(self.allow_list_widget.count())}
        if name.lower() not in existing:
            self.allow_list_widget.addItem(name)
        self.edit_new_allowed_app.clear()
        self._save_all_settings()

    def _remove_allowed_app(self) -> None:
        row = self.allow_list_widget.currentRow()
        if row >= 0:
            self.allow_list_widget.takeItem(row)
            self._save_all_settings()

    def _toggle_mcp_http(self) -> None:
        if self._mcp_proc is not None and self._mcp_proc.poll() is None:
            self._mcp_proc.terminate()
            self._mcp_proc = None
            self.mcp_status_lbl.setText(f"MCP Streamable-HTTP: Stopped (Port {self.config.server.port})")
            self.btn_mcp_http.setText(f"Start MCP HTTP Server (:{self.config.server.port})")
            return

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--mcp-server", "--transport", "streamable-http", "--port", str(self.config.server.port)]
        else:
            cmd = [
                sys.executable,
                str(PROJECT_ROOT / "computer_use_mcp.py"),
                "--transport",
                "streamable-http",
                "--port",
                str(self.config.server.port),
            ]
        self._mcp_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.mcp_status_lbl.setText(f"MCP Streamable-HTTP: Running on :{self.config.server.port}")
        self.btn_mcp_http.setText("Stop MCP HTTP Server")
        self.show_toast(f"MCP HTTP Server started on :{self.config.server.port}")

    def _copy_mcp_json(self) -> None:
        doc = json.dumps(build_mcp_config_document(PROJECT_ROOT), ensure_ascii=False, indent=2)
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(doc)
        self.show_toast("Copied MCP JSON configuration")

    def _install_claude_desktop(self) -> None:
        try:
            target = install_into_json_file(project_root=PROJECT_ROOT)
            self.show_toast(f"Installed MCP server into {target.name}")
        except Exception as exc:
            self.show_toast(f"Failed: {exc}", error=True)

    def _open_screen_sharing(self) -> None:
        host = self.edit_vnc_host.text().strip() or None
        try:
            res = self.runtime.controller.connect_screen_sharing(host=host)
            self.target_badge.setCurrentText("screen_sharing")
            self.show_toast(res.get("detail", "Screen Sharing requested"))
        except Exception as exc:
            self.show_toast(str(exc), error=True)

    def _run_cursor_demo(self) -> None:
        try:
            self.runtime.start()
            self.runtime.overlay.set_status("thinking")
            QTimer.singleShot(700, lambda: self.runtime.overlay.move_to(620, 380, duration=0.4, wait=False))
            QTimer.singleShot(1150, lambda: self.runtime.overlay.trigger_click(620, 380, button="left", clicks=1))
            QTimer.singleShot(1700, lambda: self.runtime.overlay.set_status("idle"))
            self.show_toast("Playing virtual cursor demo")
        except Exception as exc:
            self.show_toast(str(exc), error=True)

    def closeEvent(self, event: Any) -> None:
        qapp = QApplication.instance()
        if qapp is not None:
            try:
                qapp.removeEventFilter(self)
            except Exception:
                pass
        if self._active_worker is not None and self._active_worker.isRunning():
            self._active_worker.quit()
            self._active_worker.wait(500)
        if self._model_worker is not None and self._model_worker.isRunning():
            self._model_worker.quit()
            self._model_worker.wait(300)
        if self._mcp_proc is not None and self._mcp_proc.poll() is None:
            self._mcp_proc.terminate()
        try:
            self.runtime.stop()
        except Exception:
            pass
        super().closeEvent(event)


def main() -> int:
    # Subprocess dispatch support inside PyInstaller .app bundle
    if "--cursor-overlay-serve" in sys.argv:
        sys.argv = [sys.argv[0], "--serve"] + [a for a in sys.argv[1:] if a != "--cursor-overlay-serve"]
        import cursor_overlay

        return cursor_overlay.main()

    if "--mcp-server" in sys.argv:
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--mcp-server"]
        import computer_use_mcp

        return computer_use_mcp.main()

    import argparse

    parser = argparse.ArgumentParser(description="Antigravity AI Studio")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--dry-run", action="store_true", help="Run without real OS input events")
    parser.add_argument("--no-auto-fetch", action="store_true", help="Do not query /v1/models on startup")
    parser.add_argument("--smoke-test", action="store_true", help="Verify startup and exit immediately")
    args, _ = parser.parse_known_args()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Antigravity AI Studio")
    win = AntigravityStudioWindow(
        config_path=args.config,
        dry_run=args.dry_run or args.smoke_test,
        auto_fetch_models=(not args.no_auto_fetch) and (not args.smoke_test),
        enable_resident_overlay=not args.smoke_test,
        overlay_backend="headless" if args.smoke_test else "auto",
    )
    win.show()
    if args.smoke_test:
        app.processEvents()
        win.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
