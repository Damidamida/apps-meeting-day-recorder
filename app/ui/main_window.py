from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Meeting Day Recorder")
        self.resize(480, 420)

        self.status_label = QLabel("Ready. Start a workday when needed.")
        layout = QVBoxLayout()
        layout.addWidget(self.status_label)

        actions = [
            ("Start workday", "Workday started (placeholder)."),
            ("Start meeting", "Meeting started (placeholder)."),
            ("End meeting", "Meeting ended (placeholder)."),
            ("End workday", "Workday ended (placeholder)."),
            ("Open review", "Review opened (placeholder)."),
            ("Save final summaries", "Final summaries saved (placeholder)."),
        ]
        for button_text, status_text in actions:
            button = QPushButton(button_text)
            button.clicked.connect(
                lambda checked=False, message=status_text: self.status_label.setText(message)
            )
            layout.addWidget(button)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

