from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class SummaryMaterialView(QWidget):
    save_requested = Signal(str)
    cancel_requested = Signal()
    edit_requested = Signal()

    BASE_CONTENT_MIN_HEIGHT = 340
    BASE_CONTENT_MAX_HEIGHT = 380
    EXPANDED_CONTENT_MIN_HEIGHT = 560
    EXPANDED_CONTENT_MAX_HEIGHT = 720

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = "preview"
        self.height_mode = "base"
        self.markdown = ""

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        self.header_frame = QFrame()
        self.header_frame.setObjectName("summaryMaterialHeader")
        self.header_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header = QHBoxLayout()
        header.setContentsMargins(14, 10, 14, 10)
        header.setSpacing(8)
        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("sectionHint")
        self.meta_label.setWordWrap(True)
        title_block.addWidget(self.title_label)
        title_block.addWidget(self.meta_label)
        header.addLayout(title_block, 1)
        header.addStretch(1)
        self.extra_actions_layout = QHBoxLayout()
        self.extra_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.extra_actions_layout.setSpacing(8)
        header.addLayout(self.extra_actions_layout)
        self.extra_action_widgets: list[QWidget] = []

        self.height_toggle_button = QPushButton("Развернуть")
        self.edit_button = QPushButton("Редактировать")
        self.save_button = QPushButton("Сохранить")
        self.cancel_button = QPushButton("Отмена")
        self.height_toggle_button.setObjectName("headerButton")
        self.edit_button.setObjectName("headerButton")
        self.save_button.setObjectName("headerPrimaryButton")
        self.cancel_button.setObjectName("headerButton")
        for button in (
            self.height_toggle_button,
            self.edit_button,
            self.save_button,
            self.cancel_button,
        ):
            button.setFixedHeight(34)
            header.addWidget(button)
        self.header_frame.setLayout(header)
        layout.addWidget(self.header_frame)

        self.preview = QTextBrowser()
        self.preview.setObjectName("summaryPreview")
        self.preview.setOpenExternalLinks(False)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("summaryEditor")

        layout.addWidget(self.preview, 1)
        layout.addWidget(self.editor, 1)
        self.setLayout(layout)

        self.edit_button.clicked.connect(self.enter_edit_mode)
        self.height_toggle_button.clicked.connect(self.toggle_height_mode)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self._cancel)
        self._sync_height_mode()
        self._sync_mode()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_meta(self, meta: str) -> None:
        self.meta_label.setText(meta)
        self.meta_label.setVisible(bool(meta))

    def clear_extra_actions(self) -> None:
        while self.extra_actions_layout.count():
            item = self.extra_actions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self.extra_action_widgets.clear()

    def add_extra_action(self, button: QPushButton) -> None:
        self.extra_action_widgets.append(button)
        self.extra_actions_layout.addWidget(button)
        self._sync_mode()

    def set_markdown(self, markdown: str) -> None:
        self.markdown = markdown
        self.editor.setPlainText(markdown)
        self.preview.setProperty("summary_block_view", True)
        self.preview.setHtml(self._markdown_to_html(markdown))
        self.mode = "preview"
        self._sync_mode()

    def has_unsaved_changes(self) -> bool:
        return self.mode == "edit" and self.editor.toPlainText() != self.markdown

    def enter_edit_mode(self) -> None:
        self.mode = "edit"
        self.editor.setPlainText(self.markdown)
        self.edit_requested.emit()
        self._sync_mode()

    def toggle_height_mode(self) -> None:
        self.height_mode = "expanded" if self.height_mode == "base" else "base"
        self._sync_height_mode()

    def _save(self) -> None:
        self.markdown = self.editor.toPlainText()
        self.preview.setProperty("summary_block_view", True)
        self.preview.setHtml(self._markdown_to_html(self.markdown))
        self.mode = "preview"
        self._sync_mode()
        self.save_requested.emit(self.markdown)

    def _cancel(self) -> None:
        self.editor.setPlainText(self.markdown)
        self.mode = "preview"
        self._sync_mode()
        self.cancel_requested.emit()

    def _sync_mode(self) -> None:
        editing = self.mode == "edit"
        self.preview.setVisible(not editing)
        self.editor.setVisible(editing)
        self.edit_button.setVisible(not editing)
        self.save_button.setVisible(editing)
        self.cancel_button.setVisible(editing)
        for widget in self.extra_action_widgets:
            widget.setVisible(not editing)

    def _sync_height_mode(self) -> None:
        expanded = self.height_mode == "expanded"
        minimum = (
            self.EXPANDED_CONTENT_MIN_HEIGHT if expanded else self.BASE_CONTENT_MIN_HEIGHT
        )
        maximum = (
            self.EXPANDED_CONTENT_MAX_HEIGHT if expanded else self.BASE_CONTENT_MAX_HEIGHT
        )
        self.height_toggle_button.setText("Свернуть" if expanded else "Развернуть")
        self.setProperty("height_mode", self.height_mode)
        for widget in (self.preview, self.editor):
            widget.setMinimumHeight(minimum)
            widget.setMaximumHeight(maximum)

    @staticmethod
    def _markdown_to_html(markdown: str) -> str:
        lines = markdown.splitlines()
        sections: list[tuple[str, list[str]]] = []
        current_title = ""
        current_lines: list[str] = []

        def add_current_section() -> None:
            nonlocal current_title, current_lines
            if any(line.strip() for line in current_lines):
                sections.append((current_title, current_lines))
            current_title = ""
            current_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                add_current_section()
                continue
            if stripped.startswith("## "):
                add_current_section()
                current_title = stripped[3:].strip()
                current_lines = []
                continue
            if stripped.startswith("### "):
                add_current_section()
                current_title = stripped[4:].strip()
                current_lines = []
                continue
            current_lines.append(line)
        add_current_section()
        if not sections:
            sections = [("", ["Итог пока не заполнен."])]

        rendered_sections = []
        for title, section_lines in sections:
            body = SummaryMaterialView._render_markdown_lines(section_lines)
            title_html = f"<h2>{escape(title)}</h2>" if title else ""
            rendered_sections.append(
                '<table class="summary-section" width="100%" cellspacing="0" cellpadding="12" '
                'style="margin-bottom:12px; border:1px solid #334155; '
                'background-color:#111827; border-radius:8px;"><tr><td>'
                f"{title_html}{body}</td></tr></table>"
            )
        return (
            "<html><head><style>"
            "body { margin: 0; color: #e5e7eb; font-family: Segoe UI, Arial, sans-serif; }"
            ".summary-document { padding: 2px; }"
            ".summary-section h2 { margin: 0 0 10px 0; font-size: 18px; font-weight: 800; color: #f8fafc; }"
            ".summary-section p { margin: 8px 0; line-height: 1.45; color: #e5e7eb; }"
            ".summary-section ul { margin: 8px 0 4px 20px; padding: 0; }"
            ".summary-section li { margin: 5px 0; line-height: 1.4; }"
            ".summary-empty { color: #94a3b8; font-style: italic; }"
            "</style></head><body><div class=\"summary-document\">"
            + "".join(rendered_sections)
            + "</div></body></html>"
        )

    @staticmethod
    def _render_markdown_lines(lines: list[str]) -> str:
        html_parts: list[str] = []
        paragraph: list[str] = []
        in_list = False

        def flush_paragraph() -> None:
            if paragraph:
                html_parts.append(f"<p>{escape(' '.join(paragraph))}</p>")
                paragraph.clear()

        def close_list() -> None:
            nonlocal in_list
            if in_list:
                html_parts.append("</ul>")
                in_list = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                flush_paragraph()
                close_list()
                continue
            if stripped.startswith(("- ", "* ")):
                flush_paragraph()
                if not in_list:
                    html_parts.append("<ul>")
                    in_list = True
                html_parts.append(f"<li>{escape(stripped[2:].strip())}</li>")
                continue
            close_list()
            paragraph.append(stripped)
        flush_paragraph()
        close_list()
        if not html_parts:
            return '<p class="summary-empty">Итог пока не заполнен.</p>'
        return "".join(html_parts)
