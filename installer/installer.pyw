"""(pythonw-running) hwime 手写输入法 安装器。

零第三方依赖（仅标准库 + tkinter + robocopy + powershell），在任意
Windows + Python 3.8+ 上都能跑。做四件事：

1. 把完整运行时复制到安装目录（基础 Python + venv + 应用 + 上游 ref；
   自动排除 .git / data / cache，约 1.6GB）
2. 打补丁：重写 pyvenv.cfg 指向随装运行时、剔除开发机的 editable 安装
3. 生成启动器 hwime_launch.pyw 与卸载器 uninstall.pyw
4. 快捷方式（桌面/开始菜单）+ 卸载注册表项 + 可选开机自启

跑法：install.bat（或 pythonw installer/installer.pyw）
测试模式：installer.pyw --test <目录>   只做检查+复制，不碰快捷方式/注册表
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SELF_DIR = os.path.dirname(os.path.abspath(__file__))
APP_SRC = os.path.dirname(SELF_DIR)                          # 项目根
WORKSPACE = os.path.dirname(APP_SRC)

APP_NAME = "hwime 手写输入法"
APP_VERSION = "2.9.0"
ICO_SRC = os.path.join(APP_SRC, "docs", "hwime.ico")

DEFAULT_VENV = r"C:\Users\lab\.workbuddy\binaries\python\envs\hwime"

LOGF = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                    "hwime_install.log")


def filelog(msg: str):
    """落盘日志（GUI 卡住/无声退出时排查用）。"""
    try:
        with open(LOGF, "a", encoding="utf-8") as fh:
            fh.write(f"[{__import__('time').strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 环境探测

def find_ref_src() -> str | None:
    """上游参考仓库位置。"""
    env = os.environ.get("HWIME_REF")
    cands = [env] if env else []
    cands.append(os.path.join(WORKSPACE, "_ref", "cnn_chinese_hw"))
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "cnn_chinese_hw")):
            return os.path.abspath(c)
    return None


def find_venv_src() -> str | None:
    env = os.environ.get("HWIME_VENV")
    for c in ([env] if env else []) + [DEFAULT_VENV]:
        if c and os.path.isfile(os.path.join(c, "Scripts", "python.exe")):
            return os.path.abspath(c)
    return None


def read_pyvenv_cfg(venv: str) -> dict:
    cfg = {}
    try:
        with open(os.path.join(venv, "pyvenv.cfg"), encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
    except OSError:
        pass
    return cfg


def count_files(path: str, exclude_dirs=()) -> int:
    n = 0
    excl = {d.lower() for d in exclude_dirs}
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d.lower() not in excl]
        n += len(files)
    return n


# ---------------------------------------------------------------- 复制

def robocopy(src: str, dst: str, exclude_dirs=(), on_progress=None,
             total_hint: int = 0):
    """robocopy /MT:16 复制；解析逐文件输出行更新进度。

    on_progress(copied, total) 在复制线程里被频繁调用。
    """
    os.makedirs(dst, exist_ok=True)
    args = ["robocopy", src, dst, "/E", "/MT:16", "/NP", "/NDL", "/NJH",
            "/NJS", "/NC", "/NS"]
    if exclude_dirs:
        args += ["/XD"] + [os.path.join(src, d) for d in exclude_dirs]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW)
    copied = 0
    for line in proc.stdout or []:
        s = line.rstrip("\n")
        # 文件行形如 "\t  \t\t\t<源文件全路径>"（Tab 开头；表头/汇总已关）
        if s.startswith("\t"):
            copied += 1
            if on_progress and total_hint:
                on_progress(min(copied, total_hint), total_hint)
    proc.wait()
    return proc.returncode


# ---------------------------------------------------------------- 生成物

LAUNCH_PYW = '''"""hwime 启动入口（安装器生成）：装配路径后运行面板。"""
import os, runpy, sys

HERE = os.path.dirname(os.path.abspath(__file__))          # <install>\\app
INSTALL = os.path.dirname(HERE)
REF = os.path.join(INSTALL, "ref", "cnn_chinese_hw")

os.environ.setdefault("HWIME_REF", REF)
if os.path.isdir(os.path.join(REF, "cnn_chinese_hw")):
    sys.path.insert(0, REF)          # 免 editable 安装即可 import cnn_chinese_hw

_main = os.path.join(HERE, "panel", "main.py")
sys.argv = [_main] + sys.argv[1:]
runpy.run_path(_main, run_name="__main__")
'''

UNINSTALL_PYW = '''"""hwime 卸载程序（安装器生成）。点击「是」后自动退出并清理。"""
import ctypes
import os
import subprocess
import tkinter as tk
from tkinter import messagebox

INSTALL = r"{install}"
PYTHONW = r"{pythonw}"

def kill_app():
    """找标题含 hwime 的窗口 → 结束其进程。"""
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    pids = set()
    PS = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if "hwime" in buf.value.lower():
                pid = ctypes.c_ulong(0)
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pids.add(pid.value)
        return True
    u.EnumWindows(PS(cb), 0)
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       creationflags=subprocess.CREATE_NO_WINDOW,
                       capture_output=True)

def remove_shortcuts():
    home = os.path.expanduser("~")
    for lnk in (os.path.join(home, "Desktop", "hwime 手写输入法.lnk"),
                os.path.join(home, "Desktop", "hwime.lnk")):
        try: os.remove(lnk)
        except OSError: pass
    sm = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                      "Start Menu", "Programs", "hwime")
    import shutil as _sh
    _sh.rmtree(sm, ignore_errors=True)

def remove_registry():
    import winreg
    for root, sub in (
        (winreg.HKEY_CURRENT_USER,
         r"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\hwime"),
    ):
        try:
            k = winreg.OpenKey(root, sub, 0, winreg.KEY_ALL_ACCESS)
            # 先删子键再删自身（这里无子键）
            winreg.DeleteKey(root, sub)
            k.Close()
        except OSError:
            pass
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                           0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(k, "hwime")
    except OSError:
        pass

def main():
    root = tk.Tk()
    root.withdraw()
    ok = messagebox.askyesno(
        "卸载 hwime",
        "确定要卸载 hwime 手写输入法吗？\\n\\n"
        "将删除：程序文件、快捷方式、注册表项。\\n"
        "（data 目录中的个人笔迹数据一并删除）\\n\\n"
        "点击「是」后程序自动退出并完成清理。")
    if not ok:
        return
    kill_app()
    remove_shortcuts()
    remove_registry()
    # 等本进程退出后整目录自删（cmd 独立存活）
    # 注意：这里用 {{}} 转义——本模板会被外层 .format(install=…, pythonw=…)
    # 解析一次，双花括号在外层解析后还原成运行时 '{{}}'.format(INSTALL)。
    subprocess.Popen(
        'cmd /c ping -n 4 127.0.0.1 >nul & rd /s /q "{{}}"'.format(INSTALL),
        shell=True, creationflags=subprocess.DETACHED_PROCESS)
    root.destroy()

if __name__ == "__main__":
    main()
'''


def write_launcher(install: str):
    app_dir = os.path.join(install, "app")
    with open(os.path.join(app_dir, "hwime_launch.pyw"), "w",
              encoding="utf-8") as fh:
        fh.write(LAUNCH_PYW)


def write_uninstaller(install: str):
    pythonw = os.path.join(install, "env", "Scripts", "pythonw.exe")
    with open(os.path.join(install, "uninstall.pyw"), "w",
              encoding="utf-8") as fh:
        fh.write(UNINSTALL_PYW.format(install=install, pythonw=pythonw))


def patch_venv(install: str, cfg: dict):
    """重写 pyvenv.cfg 指向随装 runtime；剔除开发机 editable 残留。"""
    runtime = os.path.join(install, "runtime")
    with open(os.path.join(install, "env", "pyvenv.cfg"), "w",
              encoding="utf-8") as fh:
        fh.write(f"home = {runtime}\n"
                 f"include-system-site-packages = false\n"
                 f"version = {cfg.get('version', '3.13')}\n")
    sp = os.path.join(install, "env", "Lib", "site-packages")
    for name in os.listdir(sp):
        low = name.lower()
        if ("cnn_chinese_hw" in low
                and (low.startswith("__editable__") or "finder" in low)):
            p = os.path.join(sp, name)
            try:
                os.remove(p)
            except OSError:
                shutil.rmtree(p, ignore_errors=True)


def run_powershell(script: str) -> bool:
    """跑 PowerShell 脚本（列多个入口防 PATH 异常；全失败返回 False）。"""
    cands = [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell",
    ]
    for exe in cands:
        try:
            r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-Command", script],
                               capture_output=True, text=True, timeout=60,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


# ---------------------------------------------------------------- 快捷方式
# 直接走 COM（ctypes 调 IShellLinkW + IPersistFile.Save），不依赖任何外部
# 进程（powershell/wscript 在受限环境可能被安全策略拦截，实测 WinError
# 216）。失败再退回 PowerShell。

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    g = _GUID()
    if ctypes.windll.ole32.CLSIDFromString(s, ctypes.byref(g)) != 0:
        raise OSError("CLSIDFromString failed: " + s)
    return g


def _vt(ptr, index, *argtypes):
    """取 COM vtable 里第 index 个方法（0-2 是 IUnknown）。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                              *argtypes)(vtbl[index])


def create_shortcut_com(lnk: str, target: str, args: str,
                        workdir: str, icon: str) -> bool:
    """用 IShellLinkW COM 创建 .lnk（进程内，无外部依赖）。"""
    ole32 = ctypes.windll.ole32
    try:
        ole32.CoInitialize(None)
        clsid = _guid("{00021401-0000-0000-C000-000000000046}")   # ShellLink
        iid_link = _guid("{000214F9-0000-0000-C000-000000000046}")  # IShellLinkW
        iid_pf = _guid("{0000010b-0000-0000-C000-000000000046}")    # IPersistFile
        link = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                  ctypes.byref(iid_link),
                                  ctypes.byref(link)) != 0 or not link:
            return False
        # SetPath=20, SetArguments=11, SetWorkingDirectory=9,
        # SetIconLocation=17, SetDescription=7（W 系，LPWSTR）
        _vt(link, 20, ctypes.c_wchar_p)(link, target)
        _vt(link, 11, ctypes.c_wchar_p)(link, args)
        _vt(link, 9, ctypes.c_wchar_p)(link, workdir)
        _vt(link, 17, ctypes.c_wchar_p, ctypes.c_int)(link, icon, 0)
        _vt(link, 7, ctypes.c_wchar_p)(link, APP_NAME)
        # QI → IPersistFile → Save
        pf = ctypes.c_void_p()
        if _vt(link, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                link, ctypes.byref(iid_pf), ctypes.byref(pf)) != 0 or not pf:
            return False
        rc = _vt(pf, 6, ctypes.c_wchar_p, ctypes.c_int)(pf, lnk, 1)
        return rc == 0 and os.path.exists(lnk)
    except OSError:
        return False


def make_shortcut(lnk: str, target: str, args: str, workdir: str,
                  icon: str) -> bool:
    if create_shortcut_com(lnk, target, args, workdir, icon):
        filelog(f"shortcut(COM): {lnk}")
        return True
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{lnk}'); "
        f"$sc.TargetPath = '{target}'; "
        f"$sc.Arguments = '\"{args}\"'; "
        f"$sc.WorkingDirectory = '{workdir}'; "
        f"$sc.IconLocation = '{icon}'; "
        "$sc.Save()"
    )
    ok = run_powershell(ps)
    filelog(f"shortcut(powershell): {lnk} -> {ok}")
    return ok


def make_shortcuts(install: str, desktop: bool, startmenu: bool):
    pythonw = os.path.join(install, "env", "Scripts", "pythonw.exe")
    launcher = os.path.join(install, "app", "hwime_launch.pyw")
    workdir = os.path.join(install, "app")
    icon = os.path.join(install, "hwime.ico")
    targets = []
    if desktop:
        targets.append(os.path.join(os.path.expanduser("~"), "Desktop",
                                    "hwime 手写输入法.lnk"))
    if startmenu:
        smdir = os.path.join(os.environ.get("APPDATA", ""), "Microsoft",
                             "Windows", "Start Menu", "Programs", "hwime")
        os.makedirs(smdir, exist_ok=True)
        targets.append(os.path.join(smdir, "hwime 手写输入法.lnk"))
    ok = True
    for lnk in targets:
        ok = make_shortcut(lnk, pythonw, launcher, workdir, icon) and ok
    return ok


def write_registry(install: str, test_mode: bool):
    if test_mode:
        return
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\hwime"
    pythonw = os.path.join(install, "env", "Scripts", "pythonw.exe")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "Elysia-laoda")
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ,
                          os.path.join(install, "hwime.ico"))
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, install)
        winreg.SetValueEx(
            k, "UninstallString", 0, winreg.REG_SZ,
            f'"{pythonw}" "{os.path.join(install, "uninstall.pyw")}"')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def set_autostart(install: str, enable: bool):
    import winreg
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0,
                        winreg.KEY_SET_VALUE) as k:
        if enable:
            cmd = ('"{}" "{}" --boot'.format(
                os.path.join(install, "env", "Scripts", "pythonw.exe"),
                os.path.join(install, "app", "hwime_launch.pyw")))
            winreg.SetValueEx(k, "hwime", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "hwime")
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- 主安装流程

def do_install(install: str, ref_src: str, venv_src: str, base_src: str,
               opts: dict, log, progress, done_cb, test_mode=False):
    cfg = read_pyvenv_cfg(venv_src)

    # 预扫描总量
    log("正在统计文件数量…")
    total = (count_files(base_src) + count_files(venv_src)
             + count_files(APP_SRC, exclude_dirs=(
                 "data", ".git", "_session-temp", "installer", "__pycache__"))
             + count_files(ref_src, exclude_dirs=(".git",)))
    log(f"共 {total} 个文件，开始复制…")

    weights = {"runtime": 2, "env": 58, "app": 8, "ref": 26, "fin": 6}
    done_w = [0.0]

    def mk_progress(w_key, base_done):
        def _cb(copied, tot):
            frac = copied / max(1, tot)
            progress(min(99.0, base_done[0] + weights[w_key] * frac))
        return _cb

    # 1. runtime
    log("① 复制 Python 运行时…")
    robocopy(base_src, os.path.join(install, "runtime"),
             on_progress=mk_progress("runtime", done_w))
    done_w[0] += weights["runtime"]

    # 2. venv
    log("② 复制 Python 依赖环境（约 1.4GB，最耗时的步骤）…")
    robocopy(venv_src, os.path.join(install, "env"),
             on_progress=mk_progress("env", done_w))
    done_w[0] += weights["env"]

    # 3. app（排除个人数据与开发残留）
    log("③ 复制应用文件…")
    robocopy(APP_SRC, os.path.join(install, "app"),
             exclude_dirs=("data", ".git", "_session-temp", "installer",
                           "__pycache__"),
             on_progress=mk_progress("app", done_w))
    done_w[0] += weights["app"]

    # 4. ref（排除 .git 历史，-267MB）
    log("④ 复制上游模型与源码（已排除 .git 历史）…")
    robocopy(ref_src, os.path.join(install, "ref", "cnn_chinese_hw"),
             exclude_dirs=(".git",),
             on_progress=mk_progress("ref", done_w))
    done_w[0] += weights["ref"]

    # 5. 补丁与生成物
    log("⑤ 写入启动器 / 卸载器 / 环境补丁…")
    filelog("step5: patch_venv begin")
    patch_venv(install, cfg)
    filelog("step5: patch_venv done")
    write_launcher(install)
    filelog("step5: launcher done")
    write_uninstaller(install)
    filelog("step5: uninstaller done")
    if os.path.exists(ICO_SRC):
        shutil.copy2(ICO_SRC, os.path.join(install, "hwime.ico"))
        filelog("step5: icon done")
    progress(96.0)

    if not test_mode:
        log("⑥ 创建快捷方式 / 注册表…")
        make_shortcuts(install, opts["desktop"], opts["startmenu"])
        write_registry(install, test_mode)
        set_autostart(install, opts["autostart"])
    else:
        log("（测试模式：跳过快捷方式/注册表）")
    # 7. 冒烟自检：装好的 python 能起来吗
    log("⑦ 校验安装环境…")
    filelog("step7: self-check begin")
    env_ok = False
    try:
        r = subprocess.run(
            [os.path.join(install, "env", "Scripts", "python.exe"), "-c",
             "import sys, PySide6, torch, rapidocr; print('OK', sys.version)"],
            capture_output=True, text=True, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW)
        env_ok = "OK" in (r.stdout or "")
        filelog(f"step7: rc={r.returncode} ok={env_ok} "
                f"err={(r.stderr or '')[:200]}")
    except subprocess.TimeoutExpired:
        filelog("step7: 自检超时（180s）")
    except Exception as exc:                         # noqa: BLE001
        filelog(f"step7: 自检异常 {exc!r}")
    log("环境自检：" + ("通过 ✓" if env_ok else "异常（详见 %TEMP%\\hwime_install.log）"))
    progress(100.0)
    filelog("step7: done_cb")
    done_cb(env_ok)
    filelog("step7: done_cb returned")


# ---------------------------------------------------------------- GUI

class InstallerApp:
    def __init__(self, root: tk.Tk, test_dir: str | None = None):
        self.root = root
        self.test_mode = bool(test_dir)
        self.auto_yes = "--auto" in sys.argv         # 自动化：跳过覆盖确认
        root.title(f"{APP_NAME} 安装程序")
        root.geometry("580x480")
        root.resizable(False, False)
        try:
            if os.path.exists(ICO_SRC):
                root.iconbitmap(ICO_SRC)
        except tk.TclError:
            pass

        self.install_dir = tk.StringVar(
            value=test_dir or os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "Programs", "hwime"))
        self.ref_src = find_ref_src()
        self.venv_src = find_venv_src()
        self.base_src = read_pyvenv_cfg(self.venv_src).get("home", "") \
            if self.venv_src else ""

        self.desktop = tk.BooleanVar(value=True)
        self.startmenu = tk.BooleanVar(value=True)
        self.autostart = tk.BooleanVar(value=False)
        self.launch_after = tk.BooleanVar(value=True)

        self._build_ui()

    # --- UI ---
    def _build_ui(self):
        pad = {"padx": 22, "pady": 4}
        tk.Label(self.root, text=APP_NAME,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(
            anchor="w", padx=22, pady=(18, 0))
        tk.Label(self.root, text=f"版本 {APP_VERSION}　·　悬浮手写输入法"
                                 "（数位板连续书写中文，双路识别）",
                 font=("Microsoft YaHei UI", 9), fg="#666").pack(
            anchor="w", padx=22)

        box = tk.LabelFrame(self.root, text=" 安装位置 ",
                            font=("Microsoft YaHei UI", 9))
        box.pack(fill="x", **pad)
        row = tk.Frame(box)
        row.pack(fill="x", padx=8, pady=8)
        tk.Entry(row, textvariable=self.install_dir,
                 font=("Microsoft YaHei UI", 9)).pack(
            side="left", fill="x", expand=True)
        tk.Button(row, text="浏览…", command=self._browse).pack(
            side="left", padx=(6, 0))

        opt = tk.LabelFrame(self.root, text=" 选项 ", font=("Microsoft YaHei UI", 9))
        opt.pack(fill="x", **pad)
        for text, var in (("创建桌面快捷方式", self.desktop),
                          ("创建开始菜单快捷方式", self.startmenu),
                          ("开机自动运行（后台常驻，可在托盘退出）", self.autostart),
                          ("安装完成后启动", self.launch_after)):
            tk.Checkbutton(opt, text=text, variable=var,
                           font=("Microsoft YaHei UI", 9),
                           anchor="w").pack(fill="x", padx=8)

        info = "包含完整 Python 运行时（约 1.6GB，无需另外安装 Python）。"
        if not (self.ref_src and self.venv_src and self.base_src):
            info = "⚠ 未找到运行时来源，请检查开发环境（见 README 打包说明）"
        tk.Label(self.root, text=info, fg="#666",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", **pad)

        self.progress = ttk.Progressbar(self.root, maximum=100.0)
        self.progress.pack(fill="x", **pad)
        self.status = tk.Label(self.root, text="准备就绪",
                               font=("Microsoft YaHei UI", 9),
                               anchor="w", fg="#333")
        self.status.pack(fill="x", **pad)

        self.logbox = tk.Text(self.root, height=8, font=("Consolas", 9),
                              state="disabled", bg="#F7F7F5")
        self.logbox.pack(fill="both", expand=True, padx=22, pady=(4, 8))

        self.btn = tk.Button(self.root, text="开始安装",
                             font=("Microsoft YaHei UI", 11, "bold"),
                             command=self._start, height=1)
        self.btn.pack(pady=(0, 14))

        self.launch_btn = None

    def _browse(self):
        d = filedialog.askdirectory(title="选择安装位置")
        if d:
            self.install_dir.set(d.replace("/", "\\"))

    def log(self, msg: str):
        filelog(msg)                                 # 落盘（排查 GUI 静默问题）
        def _do():
            try:
                self.logbox.configure(state="normal")
                self.logbox.insert("end", msg + "\n")
                self.logbox.see("end")
                self.logbox.configure(state="disabled")
                self.status.configure(text=msg)
            except tk.TclError:
                pass
        try:
            self.root.after(0, _do)
        except RuntimeError:
            pass

    def set_progress(self, v: float):
        self.root.after(0, lambda: self.progress.configure(value=v))

    def _start(self):
        install = self.install_dir.get().strip().rstrip("\\")
        if not install:
            messagebox.showerror("hwime", "请选择安装位置")
            return
        if " " in install:
            pass  # 空格可以，后面都按引号处理
        if (os.path.exists(os.path.join(install, "app"))
                and not self.test_mode and not self.auto_yes):
            if not messagebox.askyesno(
                    "hwime", "目标目录已有旧的 hwime 安装，覆盖安装？"):
                return
        if not (self.ref_src and self.venv_src and self.base_src):
            messagebox.showerror(
                "hwime", "缺少运行时来源（venv/ref/base python），无法安装。\n"
                         "可用环境变量 HWIME_VENV / HWIME_REF 指定。")
            return
        self.btn.configure(state="disabled", text="安装中…")
        opts = {"desktop": self.desktop.get(),
                "startmenu": self.startmenu.get(),
                "autostart": self.autostart.get()}
        t = threading.Thread(target=self._worker,
                             args=(install, opts), daemon=True)
        t.start()

    def _worker(self, install: str, opts: dict):
        try:
            do_install(install, self.ref_src, self.venv_src, self.base_src,
                       opts, self.log, self.set_progress, self._done,
                       test_mode=self.test_mode)
        except Exception as exc:                     # noqa: BLE001
            import traceback
            filelog("WORKER EXCEPTION:\n" + traceback.format_exc())
            self.log(traceback.format_exc())
            def _err():
                try:
                    messagebox.showerror("hwime", f"安装失败：{exc}")
                except tk.TclError:
                    pass
            try:
                self.root.after(0, _err)
            except RuntimeError:
                pass

    def _done(self, env_ok: bool):
        def _ui():
            try:
                filelog(f"_ui: test_mode={self.test_mode} env_ok={env_ok}")
                self.btn.configure(state="normal", text="重新安装")
                self.log("✔ 安装完成！" if env_ok else "⚠ 安装完成（自检异常）")
                if self.test_mode:
                    self.log("（测试模式结束，窗口自动关闭）")
                    self.root.after(1200, self.root.destroy)
                    return
                if not env_ok:
                    messagebox.showwarning("hwime", "安装完成，但环境自检异常，"
                                                    "请把日志反馈给作者")
                if self.launch_after.get():
                    self._launch()
                if messagebox.askyesno("hwime", "安装完成！\n\n打开安装目录？"):
                    os.startfile(self.install_dir.get())
            except Exception:                        # noqa: BLE001
                import traceback
                filelog("_ui EXCEPTION:\n" + traceback.format_exc())
        try:
            self.root.after(0, _ui)
        except RuntimeError:
            pass

    def _launch(self):
        pythonw = os.path.join(self.install_dir.get(), "env", "Scripts",
                               "pythonw.exe")
        launcher = os.path.join(self.install_dir.get(), "app",
                                "hwime_launch.pyw")
        subprocess.Popen([pythonw, launcher],
                         cwd=os.path.join(self.install_dir.get(), "app"))


def main():
    test_dir = None
    if "--test" in sys.argv:
        i = sys.argv.index("--test")
        if i + 1 < len(sys.argv):
            test_dir = sys.argv[i + 1]
    root = tk.Tk()
    app = InstallerApp(root, test_dir)
    if test_dir or "--auto" in sys.argv:
        # 测试模式 / 自动化模式：跳过点击，自动开跑
        root.after(600, app._start)
    root.mainloop()


if __name__ == "__main__":
    main()
