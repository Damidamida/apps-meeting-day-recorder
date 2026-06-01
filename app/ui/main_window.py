from pathlib import Path

from PySide6.QtWidgets import (
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import load_config
from app.services.storage import StorageService


class MainWindow(QMainWindow):
    def __init__(self, storage: StorageService | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Meeting Day Recorder")
        self.resize(480, 420)
        config = load_config()
        self.storage = storage or StorageService(Path(config["storage"]["root"]))

        self.status_label = QLabel("Ready. Start a workday when needed.")
        layout = QVBoxLayout()
        layout.addWidget(self.status_label)

        self.start_workday_button = self._add_button(layout, "Start workday", self.start_workday)
        self.start_meeting_button = self._add_button(layout, "Start meeting", self.start_meeting)
        self.end_meeting_button = self._add_button(layout, "End meeting", self.end_meeting)
        self.end_workday_button = self._add_button(layout, "End workday", self.end_workday)
        self._add_button(layout, "Open review", self.open_review)
        self._add_button(layout, "Save final summaries", self.save_final_summaries)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.refresh_buttons()

    @staticmethod
    def _add_button(layout: QVBoxLayout, label: str, callback) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def start_workday(self) -> None:
        try:
            day_folder = self.storage.start_workday()
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self.status_label.setText(f"Workday started. Files: {day_folder}")
        self.refresh_buttons()

    def start_meeting(self) -> None:
        title, accepted = QInputDialog.getText(self, "Start meeting", "Meeting title:")
        if not accepted:
            self.status_label.setText("Meeting start cancelled.")
            return
        try:
            meeting_folder = self.storage.start_meeting(title)
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self.status_label.setText(f"Meeting started: {meeting_folder.name}")
        self.refresh_buttons()

    def end_meeting(self) -> None:
        try:
            meeting_folder = self.storage.end_meeting()
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self.status_label.setText(f"Meeting ended: {meeting_folder.name}")
        self.refresh_buttons()

    def end_workday(self) -> None:
        try:
            day_folder = self.storage.end_workday()
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self.status_label.setText(f"Workday ended. Drafts saved: {day_folder}")
        self.refresh_buttons()

    def open_review(self) -> None:
        self.status_label.setText("Review screen is not implemented yet. Local files are ready for review.")

    def save_final_summaries(self) -> None:
        self.status_label.setText("Final summary editing is not implemented yet. Draft files remain local.")

    def refresh_buttons(self) -> None:
        self.start_workday_button.setEnabled(not self.storage.workday_active)
        self.start_meeting_button.setEnabled(
            self.storage.workday_active and not self.storage.meeting_active
        )
        self.end_meeting_button.setEnabled(self.storage.meeting_active)
        self.end_workday_button.setEnabled(
            self.storage.workday_active and not self.storage.meeting_active
        )

