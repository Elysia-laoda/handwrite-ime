"""路径推导：项目内所有脚本共用的根目录 / 上游参考仓库定位。

硬编码绝对路径（D:\\ZCodeprojecttt\\...）是上一版最大的移植性缺陷：
换机、换盘、换目录后所有脚本和 panel 全部失效。这里统一为：

- PROJECT_ROOT：handwrite-ime 项目根（由本文件位置推导）
- 参考仓库根：优先环境变量 HWIME_REF；否则尝试已安装的 cnn_chinese_hw
  包内 data 目录；再否则取工作区 <workspace>/_ref/cnn_chinese_hw
"""
from __future__ import annotations

import os

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(ENGINE_DIR)
WORKSPACE = os.path.dirname(PROJECT_ROOT)


def _installed_data_dir() -> str | None:
    """已安装（含 editable）的 cnn_chinese_hw 包内 data 目录。"""
    try:
        import cnn_chinese_hw
    except ImportError:
        return None
    d = os.path.join(os.path.dirname(os.path.abspath(cnn_chinese_hw.__file__)),
                     "data")
    return d if os.path.isdir(d) else None


def ref_repo_root() -> str:
    """上游参考仓库（cnn_chinese_hw）根目录。"""
    env = os.environ.get("HWIME_REF")
    if env:
        return env
    data = _installed_data_dir()
    if data:
        # data 在 <repo>/cnn_chinese_hw/data，回溯两级即仓库根
        return os.path.dirname(os.path.dirname(data))
    return os.path.join(WORKSPACE, "_ref", "cnn_chinese_hw")


def corpus_path(name: str = "handwriting-zh_CN.xml") -> str:
    """Tomoe 手写语料 XML 路径（参考仓库 cnn_chinese_hw/data/ 内）。"""
    installed = _installed_data_dir()
    if installed:
        p = os.path.join(installed, name)
        if os.path.exists(p):
            return p
    return os.path.join(ref_repo_root(), "cnn_chinese_hw", "data", name)


def captures_path() -> str:
    """面板运行采集数据（真实笔迹回归集的来源）。"""
    return os.path.join(PROJECT_ROOT, "data", "captures.jsonl")
