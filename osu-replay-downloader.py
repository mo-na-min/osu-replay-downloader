import sys
import os
import string
import unicodedata
import requests

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QLabel,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog
)


# ==============================================================================
# 1. NEW INSTRUCTIONS / ABOUT WINDOW (QDialog)
# ==============================================================================
class AboutDialog(QDialog):
    """A secondary window providing app documentation and instructions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("How to Use - osu! Beatmap Downloader")
        self.setFixedSize(450, 380)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Content Area with Rich Text / HTML support
        text_area = QTextEdit()
        text_area.setReadOnly(True)
        text_area.setHtml("""
            <h2>How to Use This Downloader</h2>
            <hr>
            <ol>
                <li><b>Find User ID:</b> Go to any user's profile on <code>osu.ppy.sh</code> (e.g., <code>osu.ppy.sh/users/2</code>). The number at the end of the URL is the <b>User ID</b> (e.g., <code>2</code>).</li>
                <br>
                <li><b>Set Map Count:</b> Enter how many top "most played" beatmaps you want to retrieve.</li>
                <br>
                <li><b>Get Session Cookie:</b>
                    <ul>
                        <li>Log in to <a href="https://osu.ppy.sh">osu.ppy.sh</a> in your web browser.</li>
                        <li>Press <code>F12</code> to open Developer Tools.</li>
                        <li>Go to the <b>Application</b> tab (Chrome/Edge) or <b>Storage</b> tab (Firefox).</li>
                        <li>Find <b>Cookies</b> &rarr; <code>https://osu.ppy.sh</code>.</li>
                        <li>Copy the value of the <code>osu_session</code> cookie and paste it into the field.</li>
                    </ul>
                </li>
                <br>
                <li><b>References:</b> https://github.com/tomhepz/Osu-Most-Played-Downloader</li>
            </ol>
        """)
        layout.addWidget(text_area)

        # Close Button
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.accept)
        layout.addWidget(button_box)


# ==============================================================================
# 2. SANITIZER CLASS
# ==============================================================================
class BeatmapSanitizer:
    VALID_CHARS = f"-_.() {string.ascii_letters}{string.digits}"

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        cleaned = unicodedata.normalize('NFKD', filename).encode('ASCII', 'ignore')
        return ''.join(chr(c) for c in cleaned if chr(c) in cls.VALID_CHARS)


# ==============================================================================
# 3. WORKER THREAD
# ==============================================================================
class OsuDownloaderWorker(QThread):
    progress_updated = pyqtSignal(int, int, str)
    log_emitted = pyqtSignal(str)
    download_finished = pyqtSignal(bool, str)
    
    BASE_USER_URL = "https://osu.ppy.sh/users/{user_id}/beatmapsets/most_played"
    BASE_DOWNLOAD_URL = "https://osu.ppy.sh/beatmapsets/{beatmap_id}/download?noVideo=1"

    def __init__(self, user_id: int, limit: int, session_cookie: str, save_directory: str = "./songs"):
        super().__init__()
        self.user_id = user_id
        self.limit = limit
        self.session_cookie = session_cookie
        self.save_directory = save_directory

    def run(self):
        try:
            os.makedirs(self.save_directory, exist_ok=True)
            self.log_emitted.emit(f"Fetching top {self.limit} most played maps for User ID {self.user_id}...")

            # Set up session with browser-like headers
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://osu.ppy.sh/"
            })
            session.cookies.set('osu_session', self.session_cookie, domain='osu.ppy.sh')

            url = self.BASE_USER_URL.format(user_id=self.user_id)
            response = session.get(url, params={'offset': 0, 'limit': self.limit})
            response.raise_for_status()
            beatmaps = response.json()

            if not beatmaps:
                self.download_finished.emit(False, "No beatmaps found for this user ID.")
                return

            total = len(beatmaps)

            for index, item in enumerate(beatmaps, start=1):
                beatmapset = item.get('beatmapset', {})
                beatmap_id = beatmapset.get('id')
                raw_title = beatmapset.get('title', 'Unknown')
                clean_title = BeatmapSanitizer.sanitize_filename(raw_title)

                status_msg = f"Downloading ({index}/{total}): {clean_title}"
                self.progress_updated.emit(index, total, status_msg)
                self.log_emitted.emit(f"[{index}/{total}] Downloading: {clean_title}")

                download_url = self.BASE_DOWNLOAD_URL.format(beatmap_id=beatmap_id)
                dl_response = session.get(download_url, allow_redirects=True)
                
                # Verify we received binary data rather than an HTML login error page
                content_type = dl_response.headers.get('Content-Type', '')
                if dl_response.status_code == 200 and 'text/html' not in content_type:
                    file_path = os.path.join(self.save_directory, f"{clean_title}.osz")
                    with open(file_path, 'wb') as f:
                        f.write(dl_response.content)
                    self.log_emitted.emit(f"Saved: {file_path}")
                else:
                    self.log_emitted.emit(f"Failed map ID {beatmap_id}: Session cookie expired or blocked by Cloudflare (HTTP {dl_response.status_code})")

            self.download_finished.emit(True, f"Successfully downloaded maps to '{self.save_directory}'")

        except Exception as e:
            self.log_emitted.emit(f"\nError encountered: {str(e)}")
            self.download_finished.emit(False, f"An error occurred:\n{str(e)}")

# ==============================================================================
# 4. MAIN GUI CLASS
# ==============================================================================
class OsuDownloaderGUI(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("osu! Most Played Beatmap Downloader")
        self.setFixedSize(520, 560)
        
        # Initialize persistent settings configuration
        self.settings = QSettings("OsuDownloaderApp", "SaveSettings")
        
        self.worker = None
        self._init_ui()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Top Bar (Title + Instructions Button)
        top_bar_layout = QHBoxLayout()
        header_label = QLabel("<b>osu! Beatmap Batch Downloader</b>")
        
        # Button to open the About / Instructions Window
        self.help_btn = QPushButton("Instructions / Help")
        self.help_btn.setFixedWidth(130)
        self.help_btn.clicked.connect(self._open_about_dialog)

        top_bar_layout.addWidget(header_label)
        top_bar_layout.addStretch()
        top_bar_layout.addWidget(self.help_btn)
        main_layout.addLayout(top_bar_layout)

        # Form Layout
        form_layout = QFormLayout()
        
        self.user_id_input = QLineEdit()
        self.user_id_input.setPlaceholderText("copy paste user ID from https://osu.ppy.sh/users/#userID")
        form_layout.addRow("User ID:", self.user_id_input)

        self.maps_count_input = QLineEdit()
        self.maps_count_input.setText("10")
        form_layout.addRow("Number of Maps:", self.maps_count_input)

        self.cookie_input = QLineEdit()
        self.cookie_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookie_input.setPlaceholderText("read the instructions above for more info.")
        form_layout.addRow("osu_session Cookie:", self.cookie_input)

        # Save Directory Input with Browse Button
        path_layout = QHBoxLayout()
        self.save_dir_input = QLineEdit()
        
        # Load saved directory path or fallback to default ./songs
        saved_path = self.settings.value("save_directory", os.path.abspath("./songs"))
        self.save_dir_input.setText(saved_path)
        
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._on_browse_directory)
        path_layout.addWidget(self.save_dir_input)
        path_layout.addWidget(self.browse_btn)
        form_layout.addRow("Save Location:", path_layout)

        main_layout.addLayout(form_layout)

        # Action Button
        self.download_btn = QPushButton("Start Download")
        self.download_btn.setFixedHeight(35)
        self.download_btn.clicked.connect(self._on_start_download)
        main_layout.addWidget(self.download_btn)

        # Progress Status & Bar
        self.status_label = QLabel("Status: Idle")
        self.status_label.setStyleSheet("font-style: italic; color: #555;")
        main_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        # Console Output Log
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("background-color: #2b2b2b; color: #a9b7c6; font-family: Consolas;")
        main_layout.addWidget(self.log_box)

    def _open_about_dialog(self):
        """Instantiates and launches the Instructions modal dialog."""
        dialog = AboutDialog(self)
        dialog.exec()  # Opens window modally

    def _on_browse_directory(self):
        """Opens a file manager dialog to pick a save folder."""
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Save Directory",
            self.save_dir_input.text() or os.getcwd()
        )
        if selected_dir:
            self.save_dir_input.setText(selected_dir)
            self.settings.setValue("save_directory", selected_dir)

    def append_log(self, text: str):
        self.log_box.append(text)

    def _validate_inputs(self) -> tuple[int, int, str, str] | None:
        user_id_str = self.user_id_input.text().strip()
        maps_count_str = self.maps_count_input.text().strip()
        cookie = self.cookie_input.text().strip()
        save_dir = self.save_dir_input.text().strip()

        if not user_id_str.isdigit():
            QMessageBox.critical(self, "Input Error", "Please enter a valid numeric User ID.")
            return None

        if not maps_count_str.isdigit():
            QMessageBox.critical(self, "Input Error", "Please enter a valid number of maps.")
            return None

        if not cookie:
            QMessageBox.critical(self, "Input Error", "Please enter your osu_session cookie.")
            return None

        if not save_dir:
            QMessageBox.critical(self, "Input Error", "Please select a valid save directory.")
            return None

        return int(user_id_str), int(maps_count_str), cookie, save_dir

    def _on_start_download(self):
        data = self._validate_inputs()
        if not data:
            return

        user_id, maps_count, cookie, save_dir = data

        # Save selected path to settings persistently
        self.settings.setValue("save_directory", save_dir)

        self.download_btn.setEnabled(False)
        self.log_box.clear()
        self.progress_bar.setValue(0)

        self.worker = OsuDownloaderWorker(user_id, maps_count, cookie, save_dir)
        self.worker.progress_updated.connect(self._update_progress)
        self.worker.log_emitted.connect(self.append_log)
        self.worker.download_finished.connect(self._on_download_complete)
        self.worker.start()

    def _update_progress(self, current: int, total: int, status_msg: str):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(status_msg)

    def _on_download_complete(self, success: bool, message: str):
        self.download_btn.setEnabled(True)
        if success:
            self.status_label.setText("Status: Completed!")
            QMessageBox.information(self, "Success", message)
        else:
            self.status_label.setText("Status: Stopped or Failed")
            QMessageBox.warning(self, "Notice", message)


# ==============================================================================
# 5. ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OsuDownloaderGUI()
    window.show()
    sys.exit(app.exec())
