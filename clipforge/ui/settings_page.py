"""Einstellungen: KI und API-Keys, Transkription, Trigger, Clips, Untertitel, Export, Updates."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from clipforge import __version__
from clipforge.core import secrets, updater
from clipforge.core.providers import LLM_PROVIDERS, STT_PROVIDERS, llm_price
from clipforge.core.settings import Settings, Trigger
from clipforge.pipeline.layouts import LAYOUTS
from clipforge.pipeline.llm import list_models
from clipforge.pipeline.subtitles import SubtitleStyle
from clipforge.ui.worker import Task


def _scroll(w: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(w)
    sa.setFrameShape(QScrollArea.NoFrame)
    return sa


class ColorButton(QPushButton):
    def __init__(self, color: str):
        super().__init__()
        self.setFixedWidth(90)
        self.set(color)
        self.clicked.connect(self._pick)

    def set(self, color: str):
        self.color = color
        self.setText(color.upper())
        self.setStyleSheet(f"background:{color}; color:{'#111' if QColor(color).lightness() > 128 else '#fff'};")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Farbe wählen")
        if c.isValid():
            self.set(c.name())


class TriggerTable(QTableWidget):
    """Begriff | aktiv | Ähnlichkeit % | Sekunden davor | Sekunden danach"""

    def __init__(self, triggers: list[Trigger]):
        super().__init__(0, 5)
        self.setHorizontalHeaderLabels(["Begriff", "Aktiv", "Ähnlichkeit %", "Sek. davor", "Sek. danach"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.verticalHeader().hide()
        for t in triggers:
            self.add(t)

    def add(self, t: Trigger | None = None):
        t = t or Trigger("neuer begriff")
        r = self.rowCount()
        self.insertRow(r)
        self.setItem(r, 0, QTableWidgetItem(t.phrase))
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if t.enabled else Qt.Unchecked)
        self.setItem(r, 1, chk)
        for col, v in ((2, t.threshold), (3, t.pre), (4, t.post)):
            self.setItem(r, col, QTableWidgetItem(f"{v:g}"))
        if t.phrase == "neuer begriff":
            self.editItem(self.item(r, 0))

    def remove_selected(self):
        for r in sorted({i.row() for i in self.selectedIndexes()}, reverse=True):
            self.removeRow(r)

    def values(self) -> list[Trigger]:
        out = []
        for r in range(self.rowCount()):
            phrase = (self.item(r, 0).text() if self.item(r, 0) else "").strip()
            if not phrase:
                continue

            def num(c, d):
                try:
                    return float(self.item(r, c).text().replace(",", "."))
                except (AttributeError, ValueError):
                    return d

            out.append(Trigger(phrase, self.item(r, 1).checkState() == Qt.Checked, int(num(2, 80)),
                               num(3, 30.0), num(4, 4.0)))
        return out


class SettingsPage(QWidget):
    saved = Signal()
    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = Settings.load()
        self.tasks: list[Task] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        h = QLabel("Einstellungen")
        h.setObjectName("h1")
        root.addWidget(h)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(_scroll(self._tab_ai()), "KI && API-Keys")
        self.tabs.addTab(_scroll(self._tab_stt()), "Transkription")
        self.tabs.addTab(_scroll(self._tab_triggers()), "Trigger")
        self.tabs.addTab(_scroll(self._tab_clips()), "Clips")
        self.tabs.addTab(_scroll(self._tab_subs()), "Untertitel")
        self.tabs.addTab(_scroll(self._tab_export()), "Export")
        self.tabs.addTab(_scroll(self._tab_update()), "Über && Update")
        # Zahlenfelder und Auswahllisten nicht über die ganze Breite ziehen
        from PySide6.QtWidgets import QAbstractSpinBox

        for sb in self.findChildren(QAbstractSpinBox):
            sb.setMaximumWidth(170)
        for cb in self.findChildren(QComboBox):
            if cb is not self.model:
                cb.setMaximumWidth(460)
        bar = QHBoxLayout()
        bar.addStretch(1)
        save = QPushButton("Speichern")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        bar.addWidget(save)
        root.addLayout(bar)

    # ------------------------------------------------------------------ KI
    def _tab_ai(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(18, 18, 18, 18)
        self.ai_enabled = QCheckBox("KI-Bewertung verwenden (Start/Ende, Score, Titel, Hashtags)")
        self.ai_enabled.setChecked(self.s.ai_enabled)
        f.addRow(self.ai_enabled)
        self.provider = QComboBox()
        for pid, p in LLM_PROVIDERS.items():
            self.provider.addItem(p.name, pid)
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.s.llm_provider)))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        f.addRow("Anbieter", self.provider)
        km = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("API-Key einfügen")
        self.key_state = QLabel("")
        self.key_state.setObjectName("muted")
        save_key = QPushButton("Key speichern")
        save_key.clicked.connect(self._save_key)
        del_key = QPushButton("Löschen")
        del_key.clicked.connect(self._del_key)
        self.key_link = QPushButton("Key erstellen ↗")
        self.key_link.clicked.connect(self._open_key_url)
        km.addWidget(self.key, 1)
        km.addWidget(save_key)
        km.addWidget(del_key)
        km.addWidget(self.key_link)
        f.addRow("API-Key", km)
        f.addRow("", self.key_state)
        self.base_url = QLineEdit(self.s.llm_base_url)
        self.base_url.setPlaceholderText("nur für eigenen Server oder anderen Port, z. B. http://localhost:11434/v1")
        f.addRow("Server-Adresse", self.base_url)
        mm = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(320)
        load = QPushButton("Modelle laden")
        load.clicked.connect(self._load_models)
        mm.addWidget(self.model, 1)
        mm.addWidget(load)
        f.addRow("Modell", mm)
        self.price_lbl = QLabel("")
        self.price_lbl.setObjectName("muted")
        f.addRow("Preis", self.price_lbl)
        pp = QHBoxLayout()
        self.price_in = QDoubleSpinBox(decimals=3, maximum=1000)
        self.price_out = QDoubleSpinBox(decimals=3, maximum=1000)
        pp.addWidget(QLabel("Eigener Preis $/1M Eingabe"))
        pp.addWidget(self.price_in)
        pp.addWidget(QLabel("Ausgabe"))
        pp.addWidget(self.price_out)
        pp.addStretch(1)
        f.addRow("", pp)
        self.effort = QComboBox()
        for v, label in (("low", "niedrig (günstig, schnell)"), ("medium", "mittel (empfohlen)"),
                         ("high", "hoch (gründlicher, teurer)")):
            self.effort.addItem(label, v)
        self.effort.setCurrentIndex(max(0, self.effort.findData(self.s.llm_effort)))
        f.addRow("Denktiefe (Claude)", self.effort)
        self.ctx_before = QSpinBox(minimum=20, maximum=600, value=int(self.s.context_before), suffix=" s")
        self.ctx_after = QSpinBox(minimum=5, maximum=180, value=int(self.s.context_after), suffix=" s")
        cx = QHBoxLayout()
        cx.addWidget(QLabel("davor"))
        cx.addWidget(self.ctx_before)
        cx.addWidget(QLabel("danach"))
        cx.addWidget(self.ctx_after)
        cx.addStretch(1)
        f.addRow("Kontext für die KI", cx)
        self.min_score = QSpinBox(minimum=0, maximum=10, value=self.s.min_score)
        self.min_score.setSpecialValueText("aus")
        f.addRow("Clips unter Score automatisch verwerfen", self.min_score)
        self.model.currentTextChanged.connect(self._update_price)
        self._provider_changed(init=True)
        return w

    def _pid(self) -> str:
        return self.provider.currentData()

    def _provider_changed(self, *_, init: bool = False):
        p = LLM_PROVIDERS[self._pid()]
        self.key.setEnabled(p.needs_key or p.id == "custom")
        self.key_link.setVisible(bool(p.key_url))
        self.key.clear()
        self._key_state()
        self.base_url.setPlaceholderText(p.base_url or "http://server:port/v1")
        self.model.clear()
        self.model.addItems(list(p.models))
        if init and self.s.llm_model:
            self.model.setCurrentText(self.s.llm_model)
        elif p.default_model:
            self.model.setCurrentText(p.default_model)
        self._update_price()

    def _key_state(self):
        p = LLM_PROVIDERS[self._pid()]
        if not p.needs_key and p.id != "custom":
            self.key_state.setText("Kein Key nötig – läuft lokal.")
            return
        try:
            k = secrets.get_key(p.id)
        except Exception as e:
            self.key_state.setText(str(e))
            return
        self.key_state.setText(f"Gespeichert: {secrets.mask(k)} (Windows-Anmeldeinformationsverwaltung)" if k
                               else "Noch kein Key gespeichert.")

    def _save_key(self):
        if self.key.text().strip():
            secrets.set_key(self._pid(), self.key.text())
            self.key.clear()
            self._key_state()
            self.status_message.emit("API-Key sicher gespeichert.")

    def _del_key(self):
        secrets.delete_key(self._pid())
        self._key_state()

    def _open_key_url(self):
        url = LLM_PROVIDERS[self._pid()].key_url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _update_price(self, *_):
        m = self.model.currentText().strip()
        ov = self.s.price_overrides.get(m)
        self.price_in.setValue(ov[0] if ov else 0)
        self.price_out.setValue(ov[1] if ov else 0)
        pr = llm_price(m, self.s.price_overrides, self._pid())
        self.price_lbl.setText(f"{pr.input_per_m:g} $ Eingabe / {pr.output_per_m:g} $ Ausgabe pro 1 Mio. Tokens"
                               if pr else "Unbekannt – trage unten den Preis ein, damit die Kosten stimmen.")

    def _load_models(self):
        s = Settings.load()
        s.llm_provider, s.llm_base_url = self._pid(), self.base_url.text().strip()
        pid = self._pid()

        def job(progress, cancel, confirm):
            return list_models(s, pid)

        t = Task(job)
        t.finished.connect(self._models_loaded)
        self.tasks.append(t)
        t.start()
        self.status_message.emit("Frage Modelle ab …")

    def _models_loaded(self, models: list[str]):
        cur = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setCurrentText(cur if cur in models or not models else models[0])
        self.status_message.emit(f"{len(models)} Modelle gefunden.")

    # ------------------------------------------------------------------ Transkription
    def _tab_stt(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(18, 18, 18, 18)
        self.stt = QComboBox()
        for pid, p in STT_PROVIDERS.items():
            self.stt.addItem(p.name, pid)
        self.stt.setCurrentIndex(max(0, self.stt.findData(self.s.transcription_backend)))
        f.addRow("Transkription", self.stt)
        hint = QLabel("Lokal braucht keinen Key und kostet nichts. Für OpenAI bzw. Groq trage den Key im Tab "
                      "„KI & API-Keys“ beim gleichnamigen Anbieter ein.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        f.addRow("", hint)
        self.whisper = QComboBox()
        self.whisper.addItems(list(STT_PROVIDERS["local"].models))
        self.whisper.setCurrentText(self.s.whisper_model)
        f.addRow("Lokales Modell", self.whisper)
        self.device = QComboBox()
        self.device.addItem("Grafikkarte (CUDA)", "cuda")
        self.device.addItem("Prozessor (langsam)", "cpu")
        self.device.setCurrentIndex(max(0, self.device.findData(self.s.whisper_device)))
        f.addRow("Rechnen auf", self.device)
        self.lang = QComboBox()
        for code, label in (("", "automatisch erkennen"), ("de", "Deutsch"), ("en", "Englisch"), ("es", "Spanisch"),
                            ("fr", "Französisch"), ("tr", "Türkisch")):
            self.lang.addItem(label, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(self.s.language)))
        f.addRow("Sprache", self.lang)
        self.chunk = QSpinBox(minimum=2, maximum=30, value=int(self.s.chunk_minutes), suffix=" min")
        f.addRow("Abschnittslänge", self.chunk)
        return w

    # ------------------------------------------------------------------ Trigger
    def _tab_triggers(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 18, 18, 18)
        lbl = QLabel("Sprachbefehle")
        lbl.setObjectName("h2")
        v.addWidget(lbl)
        self.voice = TriggerTable(self.s.voice_triggers)
        self.voice.setMinimumHeight(200)
        v.addWidget(self.voice)
        lbl2 = QLabel("Eigene Schlüsselbegriffe")
        lbl2.setObjectName("h2")
        v.addWidget(lbl2)
        info = QLabel("Unscharfe Erkennung: 80 % findet auch Versprecher und Dialekt. Kurze Begriffe brauchen "
                      "automatisch mehr Ähnlichkeit.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        v.addWidget(info)
        self.custom = TriggerTable(self.s.custom_keywords)
        self.custom.setMinimumHeight(160)
        v.addWidget(self.custom)
        bb = QHBoxLayout()
        for label, fn in (("+ Sprachbefehl", lambda: self.voice.add()), ("+ Begriff", lambda: self.custom.add()),
                          ("Markierte löschen", lambda: (self.voice.remove_selected(), self.custom.remove_selected()))):
            b = QPushButton(label)
            b.clicked.connect(fn)
            bb.addWidget(b)
        bb.addStretch(1)
        v.addLayout(bb)
        f = QFormLayout()
        self.use_chat = QCheckBox("Chat-Spitzen (Twitch und Kick)")
        self.use_chat.setChecked(self.s.use_chat)
        self.reactions = QLineEdit(" ".join(self.s.chat_reactions))
        self.chat_sens = QDoubleSpinBox(minimum=1.0, maximum=8.0, singleStep=0.5, value=self.s.chat_sensitivity)
        self.use_audio = QCheckBox("Lautstärkespitzen (Schreien, Lachen)")
        self.use_audio.setChecked(self.s.use_audio_peaks)
        self.audio_db = QDoubleSpinBox(minimum=4, maximum=25, value=self.s.audio_above_db, suffix=" dB über normal")
        f.addRow(self.use_chat)
        f.addRow("Reaktionen", self.reactions)
        f.addRow("Chat-Empfindlichkeit (kleiner = mehr)", self.chat_sens)
        f.addRow(self.use_audio)
        f.addRow("Schwelle", self.audio_db)
        v.addLayout(f)
        return w

    # ------------------------------------------------------------------ Clips
    def _tab_clips(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(18, 18, 18, 18)
        self.min_len = QSpinBox(minimum=5, maximum=120, value=int(self.s.min_clip_seconds), suffix=" s")
        self.max_len = QSpinBox(minimum=10, maximum=600, value=int(self.s.max_clip_seconds), suffix=" s")
        f.addRow("Mindestlänge", self.min_len)
        f.addRow("Höchstlänge", self.max_len)
        self.platform_caps = {}
        for name, cap in self.s.platforms.items():
            sb = QSpinBox(minimum=0, maximum=900, value=int(cap), suffix=" s")
            sb.setSpecialValueText("keine Grenze")
            self.platform_caps[name] = sb
            f.addRow(f"Höchstlänge {name}", sb)
        hint = QLabel("Der kleinste Wert begrenzt alle Clips, damit sie überall hochgeladen werden können.")
        hint.setObjectName("muted")
        f.addRow("", hint)
        return w

    # ------------------------------------------------------------------ Untertitel
    def _tab_subs(self) -> QWidget:
        st = SubtitleStyle.from_dict(self.s.subtitles)
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(18, 18, 18, 18)
        self.sub_on = QCheckBox("Untertitel einbrennen")
        self.sub_on.setChecked(st.enabled)
        f.addRow(self.sub_on)
        self.font = QFontComboBox()
        self.font.setEditable(True)
        self.font.setCurrentText(st.font)  # exakter Name, auch für Schnitte wie „Arial Black“
        f.addRow("Schrift", self.font)
        self.size = QSpinBox(minimum=30, maximum=160, value=st.size)
        f.addRow("Größe", self.size)
        self.c_text, self.c_hi, self.c_out = ColorButton(st.color), ColorButton(st.highlight), ColorButton(st.outline_color)
        cc = QHBoxLayout()
        for label, b in (("Text", self.c_text), ("Aktives Wort", self.c_hi), ("Umriss", self.c_out)):
            cc.addWidget(QLabel(label))
            cc.addWidget(b)
        cc.addStretch(1)
        f.addRow("Farben", cc)
        self.outline = QSpinBox(minimum=0, maximum=20, value=st.outline)
        f.addRow("Umriss-Stärke", self.outline)
        self.pos = QSlider(Qt.Horizontal)
        self.pos.setRange(10, 90)
        self.pos.setValue(int(st.position * 100))
        f.addRow("Position (oben ↔ unten)", self.pos)
        self.upper = QCheckBox("GROSSBUCHSTABEN")
        self.upper.setChecked(st.uppercase)
        self.pop = QCheckBox("Aktives Wort kurz vergrößern")
        self.pop.setChecked(st.pop)
        f.addRow(self.upper)
        f.addRow(self.pop)
        self.max_words = QSpinBox(minimum=1, maximum=8, value=st.max_words)
        f.addRow("Wörter pro Einblendung", self.max_words)
        return w

    # ------------------------------------------------------------------ Export
    def _tab_export(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(18, 18, 18, 18)
        ed = QHBoxLayout()
        self.export_dir = QLineEdit(self.s.export_dir)
        b = QPushButton("…")
        b.clicked.connect(lambda: self._pick_dir(self.export_dir))
        ed.addWidget(self.export_dir, 1)
        ed.addWidget(b)
        f.addRow("Exportordner", ed)
        dd = QHBoxLayout()
        self.download_dir = QLineEdit(self.s.download_dir)
        b2 = QPushButton("…")
        b2.clicked.connect(lambda: self._pick_dir(self.download_dir))
        dd.addWidget(self.download_dir, 1)
        dd.addWidget(b2)
        f.addRow("VOD-Downloads", dd)
        self.pattern = QLineEdit(self.s.filename_pattern)
        f.addRow("Dateiname", self.pattern)
        ph = QLabel("Platzhalter: {date} {streamer} {title} {index} {score}")
        ph.setObjectName("muted")
        f.addRow("", ph)
        self.layout_def = QComboBox()
        for k, v in LAYOUTS.items():
            self.layout_def.addItem(v, k)
        self.layout_def.setCurrentIndex(max(0, self.layout_def.findData(self.s.default_layout)))
        f.addRow("Standard-Layout", self.layout_def)
        self.codec = QComboBox()
        self.codec.addItem("H.264 (überall abspielbar)", "h264")
        self.codec.addItem("H.265 / HEVC (kleinere Dateien)", "hevc")
        self.codec.setCurrentIndex(max(0, self.codec.findData(self.s.codec)))
        f.addRow("Codec", self.codec)
        self.fps = QComboBox()
        self.fps.addItem("30 fps", 30)
        self.fps.addItem("60 fps", 60)
        self.fps.setCurrentIndex(max(0, self.fps.findData(self.s.fps)))
        f.addRow("Bildrate", self.fps)
        self.nvenc = QCheckBox("Grafikkarte zum Kodieren nutzen (NVENC)")
        self.nvenc.setChecked(self.s.use_nvenc)
        self.loud = QCheckBox("Lautstärke angleichen (-14 LUFS)")
        self.loud.setChecked(self.s.loudnorm)
        self.trim = QCheckBox("Stille am Anfang und Ende entfernen")
        self.trim.setChecked(self.s.trim_silence)
        self.smart = QCheckBox("Gameplay-Ausschnitt intelligent wählen")
        self.smart.setChecked(self.s.smart_crop)
        self.track = QCheckBox("Gesicht in der Facecam nachführen")
        self.track.setChecked(self.s.track_face)
        for c in (self.nvenc, self.loud, self.trim, self.smart, self.track):
            f.addRow(c)
        self.wm = self._file_row(f, "Wasserzeichen (PNG)", self.s.watermark, "Bilder (*.png)")
        self.wm_pos = QComboBox()
        for p in ("oben-rechts", "oben-links", "unten-rechts", "unten-links", "mitte"):
            self.wm_pos.addItem(p, p)
        self.wm_pos.setCurrentIndex(max(0, self.wm_pos.findData(self.s.watermark_pos)))
        f.addRow("Position", self.wm_pos)
        self.intro = self._file_row(f, "Intro-Video", self.s.intro, "Videos (*.mp4 *.mov *.mkv)")
        self.outro = self._file_row(f, "Outro-Video", self.s.outro, "Videos (*.mp4 *.mov *.mkv)")
        return w

    def _file_row(self, f: QFormLayout, label: str, value: str, flt: str) -> QLineEdit:
        row = QHBoxLayout()
        le = QLineEdit(value)
        le.setPlaceholderText("keins")
        b = QPushButton("…")
        b.clicked.connect(lambda: self._pick_file(le, flt))
        x = QPushButton("✕")
        x.setFixedWidth(34)
        x.clicked.connect(le.clear)
        row.addWidget(le, 1)
        row.addWidget(b)
        row.addWidget(x)
        f.addRow(label, row)
        return le

    def _pick_dir(self, le: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Ordner wählen", le.text())
        if d:
            le.setText(d)

    def _pick_file(self, le: QLineEdit, flt: str):
        p, _ = QFileDialog.getOpenFileName(self, "Datei wählen", "", flt)
        if p:
            le.setText(p)

    # ------------------------------------------------------------------ Update
    def _tab_update(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 18, 18, 18)
        t = QLabel(f"ClipForge {__version__}")
        t.setObjectName("h2")
        v.addWidget(t)
        self.upd_state = QLabel("Noch nicht geprüft.")
        self.upd_state.setObjectName("muted")
        self.upd_state.setWordWrap(True)
        v.addWidget(self.upd_state)
        row = QHBoxLayout()
        chk = QPushButton("Nach Updates suchen")
        chk.clicked.connect(self.check_update)
        self.upd_btn = QPushButton("Jetzt aktualisieren")
        self.upd_btn.setObjectName("primary")
        self.upd_btn.setEnabled(False)
        self.upd_btn.clicked.connect(self._apply_update)
        gh = QPushButton("Projekt auf GitHub ↗")
        gh.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/MoinMornhart/ClipForge")))
        logs = QPushButton("Logdatei öffnen")
        from clipforge.core.paths import logs_dir

        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir()))))
        for b in (chk, self.upd_btn, gh, logs):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        foot = QLabel("Verfasst mit Claude 🤖 · MIT-Lizenz")
        foot.setObjectName("muted")
        v.addWidget(foot)
        return w

    def check_update(self, silent: bool = False):
        def job(progress, cancel, confirm):
            return updater.check()

        t = Task(job)
        t.finished.connect(lambda info: self._update_info(info, silent))
        self.tasks.append(t)
        t.start()

    def _update_info(self, info, silent: bool):
        txt = info.message
        if info.changes:
            txt += "\n\nNeu:\n• " + "\n• ".join(info.changes)
        self.upd_state.setText(txt)
        self.upd_btn.setEnabled(info.available)
        if info.available:
            self.status_message.emit(f"Update verfügbar: {info.behind} Änderung(en) – Einstellungen → Über & Update")

    def _apply_update(self):
        self.upd_btn.setEnabled(False)
        self.upd_state.setText("Aktualisiere …")

        def job(progress, cancel, confirm):
            return updater.apply()

        t = Task(job)
        t.finished.connect(lambda msg: (self.upd_state.setText(msg), QMessageBox.information(self, "Update", msg)))
        t.failed.connect(lambda m, h: self.upd_state.setText(f"{m}\n{h}"))
        self.tasks.append(t)
        t.start()

    # ------------------------------------------------------------------ Speichern
    def save(self):
        s = Settings.load()
        s.ai_enabled = self.ai_enabled.isChecked()
        s.llm_provider = self._pid()
        s.llm_model = self.model.currentText().strip()
        s.llm_base_url = self.base_url.text().strip()
        s.llm_effort = self.effort.currentData()
        s.context_before, s.context_after = float(self.ctx_before.value()), float(self.ctx_after.value())
        s.min_score = self.min_score.value()
        if self.price_in.value() or self.price_out.value():
            s.price_overrides[s.llm_model] = [self.price_in.value(), self.price_out.value()]
        else:
            s.price_overrides.pop(s.llm_model, None)
        s.transcription_backend = self.stt.currentData()
        s.whisper_model = self.whisper.currentText()
        s.whisper_device = self.device.currentData()
        s.language = self.lang.currentData()
        s.chunk_minutes = float(self.chunk.value())
        s.voice_triggers = self.voice.values()
        s.custom_keywords = self.custom.values()
        s.use_chat = self.use_chat.isChecked()
        s.chat_reactions = self.reactions.text().split()
        s.chat_sensitivity = self.chat_sens.value()
        s.use_audio_peaks = self.use_audio.isChecked()
        s.audio_above_db = self.audio_db.value()
        s.min_clip_seconds, s.max_clip_seconds = float(self.min_len.value()), float(self.max_len.value())
        if s.max_clip_seconds < s.min_clip_seconds:
            s.max_clip_seconds = s.min_clip_seconds
        s.platforms = {k: sb.value() for k, sb in self.platform_caps.items()}
        s.subtitles = SubtitleStyle(
            enabled=self.sub_on.isChecked(), font=self.font.currentText().strip() or "Arial Black", size=self.size.value(),
            color=self.c_text.color, highlight=self.c_hi.color, outline_color=self.c_out.color,
            outline=self.outline.value(), position=self.pos.value() / 100, uppercase=self.upper.isChecked(),
            max_words=self.max_words.value(), pop=self.pop.isChecked()).to_dict()
        s.export_dir, s.download_dir = self.export_dir.text().strip(), self.download_dir.text().strip()
        s.filename_pattern = self.pattern.text().strip() or "{date}_{streamer}_{title}"
        s.default_layout = self.layout_def.currentData()
        s.codec, s.fps = self.codec.currentData(), self.fps.currentData()
        s.use_nvenc, s.loudnorm, s.trim_silence = self.nvenc.isChecked(), self.loud.isChecked(), self.trim.isChecked()
        s.smart_crop, s.track_face = self.smart.isChecked(), self.track.isChecked()
        s.watermark, s.watermark_pos = self.wm.text().strip(), self.wm_pos.currentData()
        s.intro, s.outro = self.intro.text().strip(), self.outro.text().strip()
        s.save()
        self.s = s
        self.saved.emit()
        self.status_message.emit("Einstellungen gespeichert.")
