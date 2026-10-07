"""hwime 应用配置：可调参数定义、持久化、开机自启动。

被 panel/main.py（运行时读取）与 panel/settings.py（设置界面）共用。
不依赖 Qt，任何环境可导入。
"""
from __future__ import annotations

import json
import os
import sys

APP_NAME = "hwime 手写输入法"
APP_VERSION = "2.9.0"

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(_HERE)                     # 项目根 / <install>\app
SETTINGS_FILE = os.path.join(APP_ROOT, "data", "settings.json")

# 每个配置项：(默认值, 最小值, 最大值, 界面名称, 说明)
SCHEMA: dict[str, tuple] = {
    "base_width":       (5.0,  3.0,  8.0,  "基础线宽",       "笔画基准粗细（px）"),
    "speed_span":       (3.2,  1.0,  4.5,  "速度调制",       "快写变细的幅度"),
    "taper_head":       (0.32, 0.10, 0.70, "起笔轻入",       "起笔起始宽度比例（越小越锐）"),
    "taper_mid":        (0.14, 0.00, 0.30, "行笔鼓肚",       "笔画中段饱满度加成"),
    "taper_tail":       (0.93, 0.40, 0.97, "收笔出锋",       "收笔衰减比例（越大出锋越利）"),
    "predict_ms":       (10.0, 0.0,  30.0, "笔尖预测",       "外推毫秒数（0=关闭）"),
    "frost":            (124,  60,   200,  "毛玻璃霜化",     "玻璃底白色霜化浓度"),
    "ambient_particles": (26,  0,    60,   "环境粒子",       "漂浮微粒数量"),
    "opacity":          (1.0,  0.40, 1.00, "面板不透明度",   "整窗透明度（滚轮可临时调）"),
    "live_blur":        (1,    0,    1,    "实时毛玻璃",     "背景模糊实时跟随（关闭则显示时抓一张）"),
}

SETTINGS: dict = {k: v[0] for k, v in SCHEMA.items()}

_NUMERIC = {k for k, v in SCHEMA.items() if isinstance(v[0], float)}
_BOOLINT = {"live_blur"}


def _coerce(key: str, val):
    _, lo, hi, _, _ = SCHEMA[key]
    if key in _BOOLINT:
        return 1 if val else 0
    try:
        v = float(val)
    except (TypeError, ValueError):
        v = SETTINGS[key]
    if key in _NUMERIC:
        v = round(v, 3)
    else:
        v = int(round(v))
    return max(lo, min(hi, v))


def load() -> dict:
    """从 data/settings.json 读入（越界/损坏自动钳回默认）。"""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in SCHEMA:
                    SETTINGS[k] = _coerce(k, v)
    except (OSError, ValueError):
        pass
    return SETTINGS


def save() -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(SETTINGS, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def reset_defaults() -> None:
    for k, v in SCHEMA.items():
        SETTINGS[k] = v[0]


# ---------------------------------------------------------------- 开机自启

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "hwime"


def _launch_command(boot: bool = False) -> str:
    """当前部署形态下的启动命令。

    安装版：<install>\\app\\hwime_launch.pyw（存在则用）；
    开发版：项目 panel\\main.py。
    """
    exe = sys.executable or ""
    pythonw = exe.replace("python.exe", "pythonw.exe") if exe else "pythonw.exe"
    launch = os.path.join(APP_ROOT, "hwime_launch.pyw")
    if not os.path.exists(launch):
        launch = os.path.join(_HERE, "main.py")
    arg = " --boot" if boot else ""
    return f'"{pythonw}" "{launch}"{arg}'


def is_autostart() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            val, _ = winreg.QueryValueEx(key, _RUN_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enable: bool) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ,
                                  _launch_command(boot=True))
            else:
                try:
                    winreg.DeleteValue(key, _RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
