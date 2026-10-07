@echo off
rem hwime 安装器启动脚本（优先标准 CPython——托管/精简 Python 往往缺 tkinter）
setlocal
set HERE=%~dp0

for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    "C:\Program Files\Python314\pythonw.exe"
    "C:\Program Files\Python313\pythonw.exe"
    "C:\Program Files\Python312\pythonw.exe"
) do (
    if exist %%P (
        start "" %%P "%HERE%installer.pyw"
        exit /b
    )
)

where pyw >nul 2>nul && ( start "" pyw "%HERE%installer.pyw" & exit /b )
where pythonw >nul 2>nul && ( start "" pythonw "%HERE%installer.pyw" & exit /b )

echo 未找到可用的 Python（3.8+，需含 tkinter）。请先安装 Python 后重试。
pause

