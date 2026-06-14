from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = "preview"
        self.markdown = ""

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.edit_button = QPushButton("Редактировать")
        self.save_button = QPushButton("Сохранить")
        self.cancel_button = QPushButton("Отмена")
        for button in (self.edit_button, self.save_button, self.cancel_button):
            button.setObjectName("headerButton")
            button.setFixedHeight(34)
            header.addWidget(button)
        layout.addLayout(header)

        self.preview = QTextBrowser()
        self.preview.setObjectName("summaryPreview")
        self.preview.setOpenExternalLinks(False)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("summaryEditor")

        layout.addWidget(self.preview, 1)
        layout.addWidget(self.editor, 1)
        self.setLayout(layout)

        self.edit_button.clicked.connect(self.enter_edit_mode)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self._cancel)
        self._sync_mode()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_markdown(self, markdown: str) -> None:
        self.markdown = markdown
        self.editor.setPlainText(markdown)
        self.preview.setMarkdown(markdown)
        self.mode = "preview"
        self._sync_mode()

    def enter_edit_mode(self) -> None:
        self.mode = "edit"
        self.editor.setPlainText(self.markdown)
        self.edit_requested.emit()
        self._sync_mode()

    def _save(self) -> None:
        self.markdown = self.editor.toPlainText()
        self.preview.setMarkdown(self.markdown)
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
