"""hwime 设置界面（PySide6）：可调参数的图形化配置窗口。

风格与悬浮面板一致的浅色磨砂；数值改动实时预览文本，「应用」提交到
appcfg.SETTINGS 并回调宿主（panel.apply_settings() 立即生效）。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout)

import appcfg

_DOCS_ICO = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "docs", "hwime.ico")

# 分组：键 → 所属栏目
_GROUPS = [
    ("书写手感", ["base_width", "speed_span", "taper_head", "taper_mid",
                  "taper_tail", "predict_ms"]),
    ("视觉", ["frost", "ambient_particles", "opacity", "live_blur"]),
]

_STYLE = """
QDialog { background: #F6F5F2; }
QLabel { color: #3A415A; font-size: 13px; }
QLabel#h1 { font-size: 19px; font-weight: 600; color: #22304E; }
QLabel#group { font-size: 13px; font-weight: 600; color: #5A6CA8; }
QLabel#val { color: #22304E; font-size: 12px; }
QLabel#hint { color: #8A8578; font-size: 11px; }
QFrame#card {
    background: #FFFFFF; border: 1px solid #E4E2DC; border-radius: 10px;
}
QSlider::groove:horizontal { height: 5px; background: #E2E6EF;
    border-radius: 2px; }
QSlider::sub-page:horizontal { background: #6E88C8; border-radius: 2px; }
QSlider::handle:horizontal { width: 15px; height: 15px; margin: -5px 0;
    border-radius: 7px; background: #FFFFFF; border: 2px solid #6E88C8; }
QPushButton { color: #39415A; font-size: 13px; border-radius: 8px;
    padding: 6px 18px; border: 1px solid #D9D7D0; background: #FFFFFF; }
QPushButton:hover { background: #F0F3FA; }
QPushButton#primary { color: #FFFFFF; border: none;
    background: #4A66AE; font-weight: 600; }
QPushButton#primary:hover { background: #5878C4; }
QCheckBox { color: #3A415A; font-size: 13px; }
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None, hotkey_desc: str = "", on_apply=None):
        super().__init__(parent)
        self.on_apply = on_apply
        self.hotkey_desc = hotkey_desc
        self.setWindowTitle(f"{appcfg.APP_NAME} 设置")
        if os.path.exists(_DOCS_ICO):
            self.setWindowIcon(QIcon(_DOCS_ICO))
        self.setFixedWidth(520)
        self.setStyleSheet(_STYLE)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        self._w: dict[str, QSlider | QCheckBox] = {}
        self._val_lbl: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        h1 = QLabel("设置")
        h1.setObjectName("h1")
        root.addWidget(h1)
        sub = QLabel("改动点击「应用」后立即生效并保存")
        sub.setObjectName("hint")
        root.addWidget(sub)
        root.addSpacing(4)

        for title, keys in _GROUPS:
            root.addWidget(self._build_card(title, keys))

        # ---- 系统卡片（自启 + 信息）----
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        g = QLabel("系统")
        g.setObjectName("group")
        lay.addWidget(g)

        self._auto = QCheckBox("开机自动运行（后台常驻，可在托盘退出）")
        self._auto.setChecked(appcfg.is_autostart())
        lay.addWidget(self._auto)

        info = QLabel(f"版本 {appcfg.APP_VERSION}　·　"
                      f"面板热键 {self.hotkey_desc or '—'}　·　"
                      f"配置文件 data/settings.json")
        info.setObjectName("hint")
        lay.addWidget(info)
        root.addWidget(card)

        # ---- 底部按钮 ----
        root.addSpacing(2)
        btns = QHBoxLayout()
        b_reset = QPushButton("恢复默认")
        b_reset.clicked.connect(self._reset)
        btns.addWidget(b_reset)
        btns.addStretch(1)
        self._status = QLabel("")
        self._status.setObjectName("hint")
        btns.addWidget(self._status)
        b_apply = QPushButton("应用")
        b_apply.setObjectName("primary")
        b_apply.clicked.connect(self._apply)
        btns.addWidget(b_apply)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.reject)
        btns.addWidget(b_close)
        root.addLayout(btns)

        self._sync_from_settings()

    # ------------------------------------------------------------- 构建

    def _build_card(self, title: str, keys: list[str]) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        g = QLabel(title)
        g.setObjectName("group")
        lay.addWidget(g)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for r, key in enumerate(keys):
            default, lo, hi, name, desc = appcfg.SCHEMA[key]
            lab = QLabel(name)
            lab.setToolTip(desc)
            grid.addWidget(lab, r, 0)
            if key in appcfg._BOOLINT:
                cb = QCheckBox("开启")
                cb.setToolTip(desc)
                grid.addWidget(cb, r, 1, 1, 2)
                self._w[key] = cb
            else:
                sl = QSlider(Qt.Horizontal)
                if isinstance(lo, float) or isinstance(default, float):
                    sl.setRange(int(lo * 100), int(hi * 100))
                else:
                    sl.setRange(int(lo), int(hi))
                sl.setToolTip(desc)
                sl.valueChanged.connect(
                    lambda _, k=key: self._sync_label(k))
                vl = QLabel("")
                vl.setObjectName("val")
                vl.setFixedWidth(52)
                vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                grid.addWidget(sl, r, 1)
                grid.addWidget(vl, r, 2)
                self._w[key] = sl
                self._val_lbl[key] = vl
        lay.addLayout(grid)
        return card

    # ------------------------------------------------------------- 数据同步

    def _slider_scale(self, key: str) -> float:
        default, lo, hi, _, _ = appcfg.SCHEMA[key]
        if isinstance(hi, float) or isinstance(default, float):
            return 100.0
        return 1.0

    def _sync_from_settings(self) -> None:
        for key, w in self._w.items():
            val = appcfg.SETTINGS[key]
            if isinstance(w, QCheckBox):
                w.setChecked(bool(val))
            else:
                w.setValue(int(round(float(val) * self._slider_scale(key))))
                self._sync_label(key)
        self._auto.setChecked(appcfg.is_autostart())

    def _sync_label(self, key: str) -> None:
        sl = self._w[key]
        v = sl.value() / self._slider_scale(key)
        if key in ("frost", "ambient_particles"):
            self._val_lbl[key].setText(str(int(v)))
        elif key == "predict_ms":
            self._val_lbl[key].setText("关" if v <= 0 else f"{v:.0f} ms")
        elif key == "opacity":
            self._val_lbl[key].setText(f"{v * 100:.0f}%")
        else:
            self._val_lbl[key].setText(f"{v:.2f}")

    # ------------------------------------------------------------- 动作

    def _collect(self) -> None:
        for key, w in self._w.items():
            if isinstance(w, QCheckBox):
                appcfg.SETTINGS[key] = 1 if w.isChecked() else 0
            else:
                appcfg.SETTINGS[key] = appcfg._coerce(
                    key, w.value() / self._slider_scale(key))

    def _apply(self) -> None:
        self._collect()
        appcfg.save()
        auto = self._auto.isChecked()
        if auto != appcfg.is_autostart():
            appcfg.set_autostart(auto)
        if self.on_apply:
            try:
                self.on_apply()
            except Exception as exc:            # noqa: BLE001
                print(f"[hwime] apply_settings failed: {exc}", flush=True)
        self._status.setText("已应用 ✓")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self._status.setText(""))

    def _reset(self) -> None:
        appcfg.reset_defaults()
        self._sync_from_settings()
        self._status.setText("已恢复默认，点「应用」生效")
