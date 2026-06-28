# -*- coding: utf-8 -*-
"""
NeverDie Launcher
==================
Лаунчер для Minecraft 1.16.5 с поддержкой Vanilla / Forge / Fabric / OptiFine.

Запуск: python main.py
Все зависимости: см. requirements.txt / README.md
"""

import os
import sys
import json
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QLineEdit, QVBoxLayout,
    QHBoxLayout, QFrame, QProgressBar, QFileDialog, QSlider, QComboBox,
    QMessageBox, QGraphicsDropShadowEffect
)
from PyQt6.QtGui import QPixmap, QFont, QColor, QFontDatabase, QIcon
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect, pyqtSignal, QObject, QSize

import mc_downloader
import mc_launcher
import modloader_installer

# ------------------------------------------------------------------------------------
# КОНСТАНТЫ / ПУТИ
# ------------------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(APP_DIR, "assets")
BACKGROUND_PATH = os.path.join(ASSETS_DIR, "background.png")
LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")  # сюда пользователь кладёт свой logo.png

MC_ROOT = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), ".neverdie_minecraft")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
MC_VERSION = "1.16.5"

RED = "#E5262B"
RED_BRIGHT = "#FF3B41"
RED_DARK = "#7A0F12"
BLACK = "#0A0606"
BLACK_SOFT = "#140A0A"
WHITE = "#F5F0F0"

DEFAULT_CONFIG = {
    "username": "",
    "ram_mb": 4096,
    "loader": "Vanilla",  # Vanilla / Fabric / Forge / OptiFine
    "java_path": "java",
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = dict(DEFAULT_CONFIG)
            cfg.update(data)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------------------------
# СИГНАЛЫ ДЛЯ МЕЖПОТОЧНОГО ОБНОВЛЕНИЯ UI
# (загрузка/установка идёт в отдельном потоке, чтобы интерфейс не замораживался)
# ------------------------------------------------------------------------------------

class WorkerSignals(QObject):
    progress = pyqtSignal(str, int, int)   # текст, текущее, всего
    finished = pyqtSignal(bool, str)       # успех, сообщение/ошибка
    log_line = pyqtSignal(str)             # строка вывода консоли minecraft
    process_ended = pyqtSignal(int)        # код завершения процесса minecraft


# ------------------------------------------------------------------------------------
# КНОПКА "ЗАПУСТИТЬ" С АНИМАЦИЕЙ ПРИБЛИЖЕНИЯ ПРИ НАВЕДЕНИИ
# ------------------------------------------------------------------------------------

class LaunchButton(QPushButton):
    """
    Кастомная кнопка: при наведении мыши плавно увеличивается ("приближается"),
    при уходе мыши или после запуска — плавно возвращается в исходный размер.
    """

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.base_width = 220
        self.base_height = 56
        self.hover_scale = 1.12

        self.setFixedSize(self.base_width, self.base_height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {RED};
                color: {WHITE};
                border: 2px solid {BLACK};
                border-radius: 14px;
                font-size: 18px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QPushButton:disabled {{
                background-color: {RED_DARK};
                color: #cccccc;
            }}
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(229, 38, 43, 160))
        shadow.setOffset(0, 0)
        self.setGraphicsEffect(shadow)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._base_geometry = None  # запоминаем исходное geometry при первом showEvent

    def showEvent(self, event):
        super().showEvent(event)
        if self._base_geometry is None:
            self._base_geometry = QRect(self.x(), self.y(), self.base_width, self.base_height)

    def set_base_geometry(self, rect: QRect):
        self._base_geometry = rect
        self.setGeometry(rect)

    def _animate_to(self, scale: float):
        if self._base_geometry is None:
            return
        base = self._base_geometry
        new_w = int(base.width() * scale)
        new_h = int(base.height() * scale)
        dx = (new_w - base.width()) // 2
        dy = (new_h - base.height()) // 2
        target = QRect(base.x() - dx, base.y() - dy, new_w, new_h)

        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event):
        self._animate_to(self.hover_scale)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_to(1.0)
        super().leaveEvent(event)

    def reset_to_base(self):
        """Принудительно вернуть кнопку в исходное положение (вызывается после запуска игры)."""
        self._animate_to(1.0)


# ------------------------------------------------------------------------------------
# ВКЛАДКА 1: ЗАПУСК
# ------------------------------------------------------------------------------------

class LaunchTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.cfg = main_window.cfg

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("ЗАПУСК")
        title.setStyleSheet(f"color: {RED}; font-size: 16px; font-weight: 800; letter-spacing: 2px;")
        layout.addWidget(title)

        # Поле для никнейма
        nick_label = QLabel("Никнейм игрока:")
        nick_label.setStyleSheet(f"color: {WHITE}; font-size: 13px;")
        layout.addWidget(nick_label)

        self.nick_input = QLineEdit(self.cfg.get("username", ""))
        self.nick_input.setPlaceholderText("Введи свой ник...")
        self.nick_input.setMaxLength(16)
        self.nick_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BLACK_SOFT};
                color: {WHITE};
                border: 2px solid {RED_DARK};
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border: 2px solid {RED};
            }}
        """)
        self.nick_input.textChanged.connect(self._on_nick_changed)
        layout.addWidget(self.nick_input)

        # Выбор версии/загрузчика
        loader_label = QLabel("Версия клиента:")
        loader_label.setStyleSheet(f"color: {WHITE}; font-size: 13px;")
        layout.addWidget(loader_label)

        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["Vanilla", "Fabric", "Forge", "OptiFine"])
        self.loader_combo.setCurrentText(self.cfg.get("loader", "Vanilla"))
        self.loader_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {BLACK_SOFT};
                color: {WHITE};
                border: 2px solid {RED_DARK};
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 14px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {BLACK_SOFT};
                color: {WHITE};
                selection-background-color: {RED};
            }}
        """)
        self.loader_combo.currentTextChanged.connect(self._on_loader_changed)
        layout.addWidget(self.loader_combo)

        # Кнопка для ручной установки OptiFine-installer.jar (видна только когда выбран OptiFine)
        self.optifine_btn = QPushButton("Выбрать OptiFine_installer.jar...")
        self.optifine_btn.setStyleSheet(self._secondary_btn_style())
        self.optifine_btn.clicked.connect(self._select_optifine_installer)
        self.optifine_btn.setVisible(self.loader_combo.currentText() == "OptiFine")
        layout.addWidget(self.optifine_btn)

        # Прогресс-бар установки/загрузки
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {BLACK_SOFT};
                border: 2px solid {RED_DARK};
                border-radius: 8px;
                color: {WHITE};
                text-align: center;
                height: 22px;
            }}
            QProgressBar::chunk {{
                background-color: {RED};
                border-radius: 6px;
            }}
        """)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {WHITE}; font-size: 12px;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        self.optifine_installer_path = None

    def _secondary_btn_style(self):
        return f"""
            QPushButton {{
                background-color: {BLACK_SOFT};
                color: {WHITE};
                border: 2px solid {RED};
                border-radius: 8px;
                padding: 8px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {RED_DARK};
            }}
        """

    def _on_nick_changed(self, text):
        self.cfg["username"] = text
        save_config(self.cfg)

    def _on_loader_changed(self, text):
        self.cfg["loader"] = text
        save_config(self.cfg)
        self.optifine_btn.setVisible(text == "OptiFine")

    def _select_optifine_installer(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбери OptiFine_installer.jar", "", "Jar files (*.jar)"
        )
        if path:
            self.optifine_installer_path = path
            self.optifine_btn.setText(f"Выбрано: {os.path.basename(path)}")

    def update_progress(self, text, current, total):
        self.status_label.setText(text)
        if total > 0:
            self.progress_bar.setVisible(True)
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        else:
            self.progress_bar.setVisible(False)


# ------------------------------------------------------------------------------------
# ВКЛАДКА 2: НАСТРОЙКИ
# ------------------------------------------------------------------------------------

class SettingsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.cfg = main_window.cfg

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("НАСТРОЙКИ")
        title.setStyleSheet(f"color: {RED}; font-size: 16px; font-weight: 800; letter-spacing: 2px;")
        layout.addWidget(title)

        ram_label_title = QLabel("Оперативная память для Minecraft:")
        ram_label_title.setStyleSheet(f"color: {WHITE}; font-size: 13px;")
        layout.addWidget(ram_label_title)

        self.ram_value_label = QLabel(f"{self.cfg.get('ram_mb', 4096)} МБ")
        self.ram_value_label.setStyleSheet(f"color: {RED_BRIGHT}; font-size: 22px; font-weight: 700;")
        layout.addWidget(self.ram_value_label)

        self.ram_slider = QSlider(Qt.Orientation.Horizontal)
        self.ram_slider.setMinimum(1024)
        self.ram_slider.setMaximum(16384)
        self.ram_slider.setSingleStep(512)
        self.ram_slider.setPageStep(512)
        self.ram_slider.setValue(self.cfg.get("ram_mb", 4096))
        self.ram_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {BLACK_SOFT};
                border: 1px solid {RED_DARK};
                height: 8px;
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {RED};
                border: 2px solid {WHITE};
                width: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }}
            QSlider::sub-page:horizontal {{
                background: {RED};
                border-radius: 4px;
            }}
        """)
        self.ram_slider.valueChanged.connect(self._on_ram_changed)
        layout.addWidget(self.ram_slider)

        hint = QLabel("Рекомендуется: 2048-4096 МБ для Vanilla, 4096-6144 МБ для Forge/OptiFine с модами.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: #b08080; font-size: 11px;")
        layout.addWidget(hint)

        layout.addSpacing(10)

        folder_btn = QPushButton("📁 Открыть папку версии (.minecraft)")
        folder_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {RED};
                color: {WHITE};
                border: 2px solid {BLACK};
                border-radius: 10px;
                padding: 10px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {RED_BRIGHT};
            }}
        """)
        folder_btn.clicked.connect(self._open_mc_folder)
        layout.addWidget(folder_btn)

        layout.addStretch()

    def _on_ram_changed(self, value):
        # округляем до 512 МБ шага, чтобы значение было аккуратным
        value = round(value / 512) * 512
        self.ram_value_label.setText(f"{value} МБ")
        self.cfg["ram_mb"] = value
        save_config(self.cfg)

    def _open_mc_folder(self):
        os.makedirs(MC_ROOT, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(MC_ROOT)
        else:
            QMessageBox.information(self, "Папка версии", f"Папка находится здесь:\n{MC_ROOT}")


# ------------------------------------------------------------------------------------
# ГЛАВНОЕ ОКНО
# ------------------------------------------------------------------------------------

class NeverDieLauncher(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.signals = WorkerSignals()
        self.signals.progress.connect(self._on_progress)
        self.signals.finished.connect(self._on_finished)

        self.mc_process = None

        self.setWindowTitle("NeverDie Launcher")
        self.setFixedSize(925, 530)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))

        self._drag_pos = None

        self._build_ui()

    # ---------------------------------------------------------------- UI BUILD ----

    def _build_ui(self):
        # Фон
        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(0, 0, 925, 530)
        if os.path.exists(BACKGROUND_PATH):
            pixmap = QPixmap(BACKGROUND_PATH).scaled(
                925, 530, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.bg_label.setPixmap(pixmap)
        else:
            self.setStyleSheet(f"background-color: {BLACK};")

        # Затемняющая полупрозрачная подложка сверху фона для читаемости UI
        overlay = QLabel(self)
        overlay.setGeometry(0, 0, 925, 530)
        overlay.setStyleSheet("background-color: rgba(10, 6, 6, 110);")

        # Кнопка закрытия (т.к. окно frameless)
        close_btn = QPushButton("✕", self)
        close_btn.setGeometry(890, 12, 26, 26)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {WHITE};
                border: none;
                font-size: 16px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                color: {RED_BRIGHT};
            }}
        """)
        close_btn.clicked.connect(self.close)

        min_btn = QPushButton("—", self)
        min_btn.setGeometry(858, 12, 26, 26)
        min_btn.setStyleSheet(close_btn.styleSheet())
        min_btn.clicked.connect(self.showMinimized)

        # Логотип в левом верхнем углу
        self.logo_label = QLabel(self)
        self.logo_label.setGeometry(16, 14, 48, 48)
        self._load_logo()

        # Заголовок NeverDie, огороженный 2 чёрными линиями
        self._build_title_banner()

        # Боковая панель с вкладками (справа)
        self._build_side_tabs()

        # Контент вкладок
        self.launch_tab = LaunchTab(self)
        self.launch_tab.setParent(self)
        self.launch_tab.setGeometry(640, 70, 270, 380)

        self.settings_tab = SettingsTab(self)
        self.settings_tab.setParent(self)
        self.settings_tab.setGeometry(640, 70, 270, 380)
        self.settings_tab.setVisible(False)

        # Кнопка ЗАПУСТИТЬ снизу по центру
        self.launch_button = LaunchButton("ЗАПУСТИТЬ", self)
        btn_x = (925 - self.launch_button.base_width) // 2 - 60  # немного левее, т.к. справа панель вкладок
        btn_y = 530 - self.launch_button.base_height - 30
        self.launch_button.set_base_geometry(
            QRect(btn_x, btn_y, self.launch_button.base_width, self.launch_button.base_height)
        )
        self.launch_button.clicked.connect(self._on_launch_clicked)

        # Надпись "Клиент запущен!" под кнопкой
        self.launched_label = QLabel("Клиент запущен!", self)
        self.launched_label.setGeometry(btn_x - 50, btn_y + self.launch_button.base_height + 8, 320, 22)
        self.launched_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.launched_label.setStyleSheet(f"""
            color: {RED_BRIGHT};
            font-size: 13px;
            font-weight: 700;
        """)
        self.launched_label.setVisible(False)

    def _load_logo(self):
        if os.path.exists(LOGO_PATH):
            pix = QPixmap(LOGO_PATH).scaled(
                48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.logo_label.setPixmap(pix)
        else:
            # placeholder, если logo.png ещё не закинут
            self.logo_label.setText("LOGO")
            self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.logo_label.setStyleSheet(f"""
                color: {RED};
                border: 2px dashed {RED_DARK};
                border-radius: 6px;
                font-size: 9px;
                background-color: rgba(0,0,0,120);
            """)

    def _build_title_banner(self):
        """Надпись NeverDie сверху, огороженная двумя чёрными линиями."""
        container = QWidget(self)
        container.setGeometry(0, 16, 925, 56)

        line_top = QFrame(container)
        line_top.setGeometry(312, 4, 300, 3)
        line_top.setStyleSheet(f"background-color: {BLACK};")

        title = QLabel("NeverDie", container)
        title.setGeometry(312, 10, 300, 36)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont("Arial Black", 22, QFont.Weight.Black)
        title.setFont(font)
        title.setStyleSheet(f"""
            color: {RED};
            letter-spacing: 4px;
            background-color: transparent;
        """)

        line_bottom = QFrame(container)
        line_bottom.setGeometry(312, 48, 300, 3)
        line_bottom.setStyleSheet(f"background-color: {BLACK};")

    def _build_side_tabs(self):
        panel = QFrame(self)
        panel.setGeometry(625, 70, 15, 380)
        panel.setStyleSheet(f"background-color: {BLACK}; border-radius: 4px;")

        self.tab_launch_btn = QPushButton("ЗАПУСК", self)
        self.tab_launch_btn.setGeometry(640, 16, 130, 40)
        self.tab_settings_btn = QPushButton("НАСТРОЙКИ", self)
        self.tab_settings_btn.setGeometry(780, 16, 130, 40)

        self._style_tab_button(self.tab_launch_btn, active=True)
        self._style_tab_button(self.tab_settings_btn, active=False)

        self.tab_launch_btn.clicked.connect(lambda: self._switch_tab("launch"))
        self.tab_settings_btn.clicked.connect(lambda: self._switch_tab("settings"))

    def _style_tab_button(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {RED};
                    color: {WHITE};
                    border: 2px solid {BLACK};
                    border-radius: 8px;
                    font-size: 12px;
                    font-weight: 700;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {BLACK_SOFT};
                    color: {WHITE};
                    border: 2px solid {RED_DARK};
                    border-radius: 8px;
                    font-size: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background-color: {RED_DARK};
                }}
            """)

    def _switch_tab(self, name: str):
        if name == "launch":
            self.launch_tab.setVisible(True)
            self.settings_tab.setVisible(False)
            self._style_tab_button(self.tab_launch_btn, True)
            self._style_tab_button(self.tab_settings_btn, False)
        else:
            self.launch_tab.setVisible(False)
            self.settings_tab.setVisible(True)
            self._style_tab_button(self.tab_launch_btn, False)
            self._style_tab_button(self.tab_settings_btn, True)

    # ------------------------------------------------------------- DRAG WINDOW ----
    # Окно без рамки, поэтому реализуем перетаскивание мышью за фон

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ------------------------------------------------------------- LAUNCH LOGIC ----

    def _on_launch_clicked(self):
        username = self.launch_tab.nick_input.text().strip()
        if not username:
            QMessageBox.warning(self, "Никнейм не указан", "Введи никнейм перед запуском!")
            return
        if len(username) < 3:
            QMessageBox.warning(self, "Некорректный никнейм", "Никнейм должен быть от 3 до 16 символов.")
            return

        self.launch_button.setEnabled(False)
        self.launched_label.setVisible(False)
        loader = self.launch_tab.loader_combo.currentText()

        thread = threading.Thread(target=self._prepare_and_launch, args=(username, loader), daemon=True)
        thread.start()

    def _prepare_and_launch(self, username, loader):
        try:
            os.makedirs(MC_ROOT, exist_ok=True)

            def progress_cb(text, cur, tot):
                self.signals.progress.emit(text, cur, tot)

            # 1. Гарантируем наличие vanilla 1.16.5
            if not mc_downloader.is_vanilla_installed(MC_ROOT, MC_VERSION):
                mc_downloader.download_vanilla(MC_VERSION, MC_ROOT, progress_cb)

            version_to_launch = MC_VERSION

            # 2. Если выбран загрузчик — ставим его поверх (если ещё не установлен)
            if loader == "Fabric":
                existing = [v for v in mc_launcher.list_installed_versions(MC_ROOT) if "fabric" in v.lower()]
                if existing:
                    version_to_launch = existing[0]
                else:
                    version_to_launch = modloader_installer.install_fabric(
                        MC_ROOT, MC_VERSION, progress_cb=progress_cb
                    )

            elif loader == "Forge":
                existing = [v for v in mc_launcher.list_installed_versions(MC_ROOT) if "forge" in v.lower()]
                if existing:
                    version_to_launch = existing[0]
                else:
                    java_path = self.cfg.get("java_path", "java")
                    forge_id = modloader_installer.install_forge(
                        MC_ROOT, MC_VERSION, java_path=java_path, progress_cb=progress_cb
                    )
                    if not forge_id:
                        raise RuntimeError(
                            "Forge установился, но папка версии не найдена автоматически. "
                            "Проверь versions/ внутри .minecraft."
                        )
                    version_to_launch = forge_id

            elif loader == "OptiFine":
                existing = modloader_installer.find_optifine_version(MC_ROOT, MC_VERSION)
                if existing:
                    version_to_launch = existing
                else:
                    installer_path = self.launch_tab.optifine_installer_path
                    if not installer_path:
                        raise RuntimeError(
                            "Для OptiFine сначала выбери файл OptiFine_installer.jar "
                            "(скачай его с optifine.net) кнопкой выше."
                        )
                    java_path = self.cfg.get("java_path", "java")
                    progress_cb("Запуск установщика OptiFine...", 0, 1)
                    success, msg = modloader_installer.run_optifine_installer(
                        installer_path, MC_ROOT, java_path
                    )
                    if not success:
                        raise RuntimeError(msg)
                    raise RuntimeError(
                        "Открылось окно установки OptiFine — нажми там 'Install', "
                        "затем запусти клиент ещё раз."
                    )

            # 3. Запуск
            ram_mb = self.cfg.get("ram_mb", 4096)
            java_path = self.cfg.get("java_path", "java")
            self.mc_process = mc_launcher.launch_minecraft(
                MC_ROOT, version_to_launch, username, ram_mb=ram_mb, java_path=java_path
            )

            self.signals.finished.emit(True, "ok")

        except Exception as e:
            self.signals.finished.emit(False, str(e))

    def _on_progress(self, text, current, total):
        self.launch_tab.update_progress(text, current, total)

    def _on_finished(self, success, message):
        self.launch_button.setEnabled(True)
        self.launch_button.reset_to_base()
        self.launch_tab.update_progress("", 0, 0)

        if success:
            self.launched_label.setVisible(True)
        else:
            QMessageBox.critical(self, "Ошибка запуска", message)


def main():
    app = QApplication(sys.argv)
    window = NeverDieLauncher()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
