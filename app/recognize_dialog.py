"""Окно «Распознать речь»: источник (композиция AE или файл), модель, разбивка, запуск."""
from __future__ import annotations

import multiprocessing as mp
import queue as queue_mod
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QProgressBar, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from . import transcriber
from .ae_bridge import AEJob, is_ae_running
from .jsx_builder import build_query_jsx
from .models import SplitSettings, Subtitle, Word, words_to_subtitles
from .style_panel import FormPage, _dspin
from .theme import Card, Segmented, ToggleSwitch, button, dark_title_bar, label
from .widgets import short_time


class RecognizeDialog(QDialog):
    def __init__(self, settings: dict, split: SplitSettings, words: list[Word], afterfx_path: str,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Распознать речь")
        self.resize(760, 900)
        self.words: list[Word] = list(words)
        self.result_subs: list[Subtitle] = []
        self.afterfx_path = afterfx_path
        self.proc: mp.Process | None = None
        self.queue = None
        self.ae_job: AEJob | None = None
        self.comp_info: dict | None = None
        self.video_source: dict | None = None  # видеослой композиции — для предпросмотра
        self.used_source = ""
        self.device_used = ""
        self._params: dict = {}
        self._start_after_query = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)
        root.addWidget(label("Распознать речь", "h1"))

        # ---- источник
        src_card = Card()
        head = QHBoxLayout()
        head.addWidget(label("Источник звука", "title"))
        head.addStretch()
        self.source_nav = Segmented(["Из After Effects", "Из файла"])
        head.addWidget(self.source_nav)
        src_card.lay.addLayout(head)
        self.source_stack = QStackedWidget()
        src_card.lay.addWidget(self.source_stack)

        ae_page = QWidget()
        al = QVBoxLayout(ae_page)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)
        row = QHBoxLayout()
        self.comp_label = label("Композиция ещё не прочитана", "muted")
        self.comp_label.setWordWrap(True)
        row.addWidget(self.comp_label, 1)
        self.query_btn = button("⟳  Прочитать из AE", slot=self._query_ae)
        row.addWidget(self.query_btn)
        al.addLayout(row)
        self.layers = QListWidget()
        self.layers.setFixedHeight(120)
        self.layers.setStyleSheet("QListWidget { background: #1C1F27; border: 1px solid #2A2E3A; border-radius: 8px;"
                                  " padding: 4px; } QListWidget::item { padding: 5px; }")
        al.addWidget(self.layers)
        wa = QHBoxLayout()
        self.work_area = ToggleSwitch("Только рабочая область")
        wa.addWidget(self.work_area)
        wa.addStretch()
        wa.addWidget(label("Снимите галочки со слоёв с музыкой — так распознавание точнее", "small"))
        al.addLayout(wa)
        self.source_stack.addWidget(ae_page)

        file_page = QWidget()
        fl = QHBoxLayout(file_page)
        fl.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit(settings.get("path", ""))
        self.path.setPlaceholderText("Видео или аудиофайл…")
        fl.addWidget(self.path, 1)
        fl.addWidget(button("Обзор…", slot=self._browse))
        self.source_stack.addWidget(file_page)
        self.source_nav.currentChanged.connect(self.source_stack.setCurrentIndex)
        if settings.get("source") == "file":
            self.source_nav.set_current(1)
            self.source_stack.setCurrentIndex(1)
        root.addWidget(src_card)

        # ---- распознавание и разбивка
        opts = Card(padding=10)
        page = FormPage()
        page.setMinimumHeight(330)
        self.language = QComboBox()
        for code, text in transcriber.LANGUAGES:
            self.language.addItem(text, code)
        self.language.setCurrentIndex(max(self.language.findData(settings.get("language", "ru")), 0))
        page.add_row("Язык речи", self.language)
        self.model = QComboBox()
        for name, text, _ in transcriber.MODELS:
            self.model.addItem(text, name)
        self.model.setCurrentIndex(max(self.model.findData(settings.get("model", "large-v3-turbo")), 0))
        self.model.currentIndexChanged.connect(self._update_model_label)
        page.add_row("Модель", self.model)
        self.model_label = label("", "small")
        page.add_row("", self.model_label)
        self.device = QComboBox()
        for value, text in [("auto", "Авто — видеокарта, если есть"), ("cuda", "Видеокарта NVIDIA"),
                            ("cpu", "Процессор")]:
            self.device.addItem(text, value)
        self.device.setCurrentIndex(max(self.device.findData(settings.get("device", "auto")), 0))
        page.add_row("Устройство", self.device)
        self.prompt = QLineEdit(settings.get("prompt", ""))
        self.prompt.setPlaceholderText("Имена, названия, термины — чтобы писались правильно")
        page.add_row("Подсказка", self.prompt)
        self.separate = ToggleSwitch()
        self.separate.setChecked(bool(settings.get("separate", False)))
        page.add_row("Убрать музыку", self.separate,
                     "Перед распознаванием отделить голос от музыки и шумов (модель Kim Vocal 2, ~66 МБ "
                     "скачается один раз). Точнее на роликах с громкой музыкой, но дольше: примерно 1 минута "
                     "обработки на 1 минуту звука.")

        page.add_section("Разбивка на субтитры")
        presets = QWidget()
        pl = QHBoxLayout(presets)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)
        for text, cfg in [("Обычные", SplitSettings()),
                          ("Шортс · 1 слово", SplitSettings(max_chars_line=20, max_lines=1, max_words=1,
                                                            pause_split=0.4, min_duration=0.3)),
                          ("Шортс · 2–3 слова", SplitSettings(max_chars_line=22, max_lines=1, max_words=3,
                                                              pause_split=0.4, min_duration=0.4))]:
            pl.addWidget(button(text, "chip", slot=lambda _=False, c=cfg: self._set_split(c)))
        pl.addStretch()
        page.add_row("Быстрый выбор", presets)
        self.max_chars = QSpinBox()
        self.max_chars.setRange(8, 200)
        page.add_row("Символов в строке", self.max_chars)
        self.max_lines = QSpinBox()
        self.max_lines.setRange(1, 5)
        page.add_row("Строк максимум", self.max_lines)
        self.max_words = QSpinBox()
        self.max_words.setRange(0, 50)
        self.max_words.setSpecialValueText("без ограничения")
        page.add_row("Слов в субтитре", self.max_words, "1–3 слова — стиль шортсов и рилсов")
        self.max_dur = _dspin(0.5, 30, 0.5, 1, " с")
        page.add_row("Макс. длительность", self.max_dur)
        self.pause = _dspin(0.1, 5, 0.1, 2, " с")
        page.add_row("Разрыв на паузе от", self.pause, "Пауза между словами дольше — новый субтитр")
        self.no_gaps = ToggleSwitch()
        page.add_row("Без пауз между", self.no_gaps, "Каждый субтитр держится до начала следующего — "
                     "на таймлайне они идут вплотную, без пустых моментов")
        self.replace = ToggleSwitch()
        self.replace.setChecked(True)
        page.add_row("Заменить текущие", self.replace, "Выкл — добавить в конец списка")
        page.finish()
        opts.lay.addWidget(page)
        self._set_split(split)
        root.addWidget(opts, 1)

        # ---- ход работы
        self.status = label("Выберите источник и нажмите «Распознать».", "muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFixedHeight(8)
        root.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(90)
        self.log.setPlaceholderText("Здесь появится распознанный текст…")
        root.addWidget(self.log)

        buttons = QHBoxLayout()
        self.resplit_btn = button("Пересобрать разбивку", "ghost", slot=self._resplit,
                                  tip="Разбить уже распознанный текст с новыми настройками — без повторного распознавания")
        self.resplit_btn.setEnabled(bool(self.words))
        buttons.addWidget(self.resplit_btn)
        buttons.addStretch()
        buttons.addWidget(button("Закрыть", "ghost", slot=self.reject))
        self.stop_btn = button("Остановить", slot=self._stop)
        self.stop_btn.setEnabled(False)
        buttons.addWidget(self.stop_btn)
        self.start_btn = button("✦  Распознать", "primary", slot=self._start)
        self.start_btn.setMinimumWidth(170)
        buttons.addWidget(self.start_btn)
        root.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        self._update_model_label()
        self._query_by_user = False
        if self.source_stack.currentIndex() == 0:
            if is_ae_running():
                QTimer.singleShot(150, lambda: self._query_ae(user=False))
            else:
                self.comp_label.setText("After Effects не запущен. Откройте проект с композицией и нажмите «Прочитать из AE».")

    def showEvent(self, event):
        super().showEvent(event)
        dark_title_bar(self)

    # ------------------------------------------------------------ настройки

    def settings(self) -> dict:
        return {"path": self.path.text().strip(), "language": self.language.currentData(),
                "model": self.model.currentData(), "device": self.device.currentData(),
                "prompt": self.prompt.text().strip(), "separate": self.separate.isChecked(),
                "source": "ae" if self.source_stack.currentIndex() == 0 else "file"}

    def split_settings(self) -> SplitSettings:
        base = getattr(self, "_split_base", SplitSettings())
        return SplitSettings(max_chars_line=self.max_chars.value(), max_lines=self.max_lines.value(),
                             max_duration=self.max_dur.value(), max_words=self.max_words.value(),
                             pause_split=self.pause.value(), min_duration=base.min_duration,
                             no_gaps=self.no_gaps.isChecked())

    def _set_split(self, s: SplitSettings):
        self._split_base = s
        self.max_chars.setValue(s.max_chars_line)
        self.max_lines.setValue(s.max_lines)
        self.max_words.setValue(s.max_words)
        self.max_dur.setValue(s.max_duration)
        self.pause.setValue(s.pause_split)
        if hasattr(self, "no_gaps"):
            self.no_gaps.setChecked(s.no_gaps)

    def _update_model_label(self):
        name = self.model.currentData()
        size = dict((m[0], m[2]) for m in transcriber.MODELS)[name]
        ready = transcriber.is_model_downloaded(name)
        self.model_label.setText("✓ Модель скачана" if ready else
                                 f"Скачается при первом распознавании (~{size} МБ, нужен интернет)")

    def _browse(self):
        start = str(Path(self.path.text()).parent) if self.path.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Видео или аудио", start, transcriber.MEDIA_FILTER)
        if path:
            self.path.setText(path.replace("/", "\\"))

    # ------------------------------------------------------------ After Effects

    def _query_ae(self, _checked=False, user=True):
        if self.ae_job:
            return
        self._query_by_user = user
        self.query_btn.setEnabled(False)
        self.comp_label.setText("Спрашиваю After Effects…")
        self.ae_job = AEJob(self.afterfx_path, build_query_jsx, timeout=25, parent=self)
        self.ae_job.finished.connect(self._on_query_done)
        self.ae_job.failed.connect(self._on_query_failed)
        self.ae_job.start()

    def _on_query_done(self, data: dict):
        self.ae_job = None
        self.query_btn.setEnabled(True)
        self.layers.clear()
        if not data.get("ok"):
            self.comp_info = None
            self._start_after_query = False
            msg = ("Откройте композицию в After Effects (кликните по ней на таймлайне) и нажмите «Прочитать из AE»."
                   if data.get("error") == "no_comp" else f"Ошибка AE: {data.get('error')}")
            self.comp_label.setText(msg)
            return
        self.comp_info = data["comp"]
        c = self.comp_info
        all_sources = data.get("sources", [])
        videos = [s for s in all_sources if s.get("has_video") and s.get("exists", True)]
        self.video_source = max(videos, key=lambda s: (s["depth"] == 0, s["b"] - s["a"])) if videos else None
        sources = [s for s in all_sources if s.get("has_audio", True)]
        self.comp_label.setText(f"<b>{c['name']}</b> · {c['width']}×{c['height']} · {c['fps']:.3g} к/с · "
                                f"{short_time(c['duration'])} · слоёв со звуком: {len(sources)}")
        for s in sources:
            text = f"{s['name']}   ·   {short_time(s['a'])} – {short_time(s['b'])}"
            if not s.get("exists", True):
                text += "   ·   ⚠ файл не найден"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, s)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if s.get("exists", True) else Qt.CheckState.Unchecked)
            item.setToolTip(s["path"])
            self.layers.addItem(item)
        for note in data.get("notes", []):
            self.log.appendPlainText("⚠ " + note)
        if not sources:
            self.comp_label.setText(self.comp_label.text() + "<br>В композиции нет слоёв со звуком.")
        if self._start_after_query:
            self._start_after_query = False
            self._start()

    def _on_query_failed(self, message: str):
        self.ae_job = None
        self._start_after_query = False
        self.query_btn.setEnabled(True)
        self.comp_label.setText("After Effects не ответил. Откройте AE с нужной композицией и нажмите «Прочитать из AE».")
        if self.isVisible() and self._query_by_user:
            QMessageBox.warning(self, "After Effects", message)

    def _ae_sources(self) -> list[dict]:
        out = []
        for i in range(self.layers.count()):
            item = self.layers.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    # ------------------------------------------------------------ процесс

    def _start(self, _checked=False, device_override: str | None = None):
        params = {**self.settings()}
        if self.source_stack.currentIndex() == 0:
            if self.comp_info is None:
                # композиция ещё не прочитана — читаем и сразу запускаем
                self._start_after_query = True
                self._query_ae(user=True)
                return
            sources = self._ae_sources()
            missing = [s["path"] for s in sources if not Path(s["path"]).is_file()]
            if not sources:
                QMessageBox.warning(self, "Распознавание", "Отметьте хотя бы один слой со звуком.")
                return
            if missing:
                QMessageBox.warning(self, "Распознавание", "Не найдены файлы:\n" + "\n".join(missing[:5]))
                return
            c = self.comp_info
            params.update(sources=sources, duration=c["duration"], path="")
            if self.work_area.isChecked():
                params["range"] = [c["work_start"], c["work_start"] + c["work_duration"]]
            self.used_source = "ae"
        else:
            path = self.path.text().strip()
            if not path or not Path(path).is_file():
                QMessageBox.warning(self, "Распознавание", "Выберите существующий видео- или аудиофайл.")
                return
            params["path"] = path
            self.used_source = "file"
        if device_override:
            params["device"] = device_override
        self._params = params
        self.log.clear()
        self.progress.setRange(0, 0)  # «бегущая» полоса, пока не знаем длительность
        self.status.setText("Запуск…")
        self.queue = mp.Queue()
        self.proc = mp.Process(target=transcriber.transcribe_worker, args=(params, self.queue), daemon=True)
        self.proc.start()
        self._set_running(True)
        self.timer.start()

    def _stop(self):
        if self.proc and self.proc.is_alive():
            self.proc.terminate()
        self._finish("Остановлено.")

    def _set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.resplit_btn.setEnabled(not running and bool(self.words))
        for w in (self.path, self.language, self.model, self.device, self.prompt, self.layers,
                  self.query_btn, self.source_nav, self.work_area, self.separate):
            w.setEnabled(not running)

    def _finish(self, message: str):
        self.timer.stop()
        self._set_running(False)
        self.progress.setRange(0, 1000)
        self.status.setText(message)
        self.proc = None
        self._update_model_label()

    def _poll(self):
        while True:
            try:
                msg = self.queue.get_nowait()
            except queue_mod.Empty:
                break
            kind = msg[0]
            if kind == "status":
                self.status.setText(msg[1])
            elif kind == "device":
                self.device_used = "видеокарте" if msg[1] == "cuda" else "процессоре"
            elif kind == "download":
                done, total = msg[1], msg[2]
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(min(done / max(total, 1), 1) * 1000))
                self.status.setText(f"Скачиваю модель: {done:.0f} из ~{total} МБ (только в первый раз)…")
            elif kind == "separate":
                self.progress.setRange(0, msg[2])
                self.progress.setValue(msg[1])
                self.status.setText(f"Отделяю голос от музыки: {msg[1]} из {msg[2]} фрагментов…")
            elif kind == "info":
                self.progress.setRange(0, 1000)
                self.progress.setValue(0)
                self.status.setText(f"Распознаю речь на {self.device_used}…")
            elif kind == "progress":
                pos, total, text = msg[1], msg[2], msg[3]
                self.progress.setValue(int(min(pos / max(total, 0.01), 1) * 1000))
                self.log.appendPlainText(f"[{short_time(pos)}]  {text}")
            elif kind == "done":
                self.words = [Word(*w) for w in msg[1]]
                self.progress.setValue(1000)
                self._finish(f"Готово за {msg[2]:.0f} с — распознано слов: {len(self.words)}.")
                self._accept_result()
                return
            elif kind == "error":
                self._finish("Ошибка.")
                self._on_error(msg[1])
                return
        if self.proc is not None and not self.proc.is_alive():
            # процесс упал без сообщения — обычно это сбой CUDA-библиотек
            code = self.proc.exitcode
            self._finish("Ошибка.")
            self._on_error(f"Процесс распознавания завершился аварийно (код {code}).")

    def _on_error(self, details: str):
        used_gpu = self._params.get("device") in ("auto", "cuda")
        last_line = details.strip().splitlines()[-1] if details.strip() else details
        if used_gpu:
            answer = QMessageBox.question(
                self, "Ошибка распознавания",
                f"Не получилось распознать на видеокарте:\n{last_line}\n\nПопробовать на процессоре?")
            if answer == QMessageBox.StandardButton.Yes:
                self.device.setCurrentIndex(self.device.findData("cpu"))
                self._start(device_override="cpu")
                return
        box = QMessageBox(QMessageBox.Icon.Warning, "Ошибка распознавания", last_line, parent=self)
        box.setDetailedText(details)
        box.exec()

    def _resplit(self):
        if self.words:
            self._accept_result()

    def _accept_result(self):
        self.result_subs = words_to_subtitles(self.words, self.split_settings())
        if not self.result_subs:
            QMessageBox.information(self, "Распознавание", "Речь не найдена.")
            return
        self.accept()

    def reject(self):
        if self.proc and self.proc.is_alive():
            if QMessageBox.question(self, "Распознавание", "Остановить распознавание?") \
                    != QMessageBox.StandardButton.Yes:
                return
            self.proc.terminate()
        if self.ae_job:
            self.ae_job.cancel()
        self.timer.stop()
        super().reject()
