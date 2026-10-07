# HANDOFF — hwime 项目交接

## 当前状态（2026-10-06，机器迁移到 C:/WBproject 后）

引擎 + 面板已在新机器上完整跑通并修复。**关键：上屏（SendInput）此前
是彻底坏的，已修复并真机验证**。测试矩阵全绿：路A/路B 冒烟、离屏全链路
smoke、SendInput 离线+真机注入、真实桌面启动+截图、双数据集评测。

## 新机器环境（重要）

- 项目根：`C:/WBproject/handwrite-ime`（旧机 D:\ZCodeprojecttt 已不在）
- **专用 venv**：`C:\Users\lab\.workbuddy\binaries\python\envs\hwime`
  （Python 3.13.12；已装 torch/rapidocr/onnxruntime/PySide6/jieba/comtypes
  /svg.path，以及 editable 安装的上游包）
  命令一律用它：`C:\Users\lab\.workbuddy\binaries\python\envs\hwime\Scripts\python.exe`
- 上游参考仓库：`C:/WBproject/_ref/cnn_chinese_hw`（clone 自 github
  mcyph/cnn_chinese_hw，**自带 data/hw_model.pt 52MB 权重 + Tomoe 语料**；
  旧文档说的 D 盘已作废。位置可用环境变量 `HWIME_REF` 覆盖）
- 全部脚本路径已移植化（engine/hwengine/paths.py 统一推导）

## 本次修复清单（2026-10-06）

1. **SendInput 上屏崩溃（P0，此前 100% 失败）**：panel/main.py 的
   `send_text_utf16` 有三个叠加缺陷——匿名联合构造 TypeError（日志实测
   每次上屏都崩）、联合体缺 MOUSEINPUT 导致 x64 下 sizeof(INPUT)=32≠40
   （即使不崩 SendInput 也返回 0）、按 utf-16-le **字节**迭代把每个汉字
   拆成两个错误码元。已重写 + `panel/test_send_input.py --live` 真机注入
   实测写入 `你好A1世界` 成功。
2. **数位板/合成鼠标双流重复（P0，毁掉路 A 的真凶）**：Windows 笔输入
   同时产生 tablet 事件流与合成鼠标流，两流同喂 → 每一笔被录成
   「1 点桩笔 + 真实笔」、点密度翻倍（真实数据 19:43 后所有会话均中招）。
   修复：tabletEvent 置 `_pen_active_until=+0.7s`，mouse Press/Move/
   Release 在板活跃期忽略（候选条双击 y<=40 放行以保留纠正菜单）。
3. **路径移植化（P0）**：panel/main.py、smoke_test、eval/test/exp 脚本
   里的 D:\ 硬编码全部移除；新增 engine/hwengine/paths.py。
4. 面板小修：候选菜单命中改用与绘制一致的字体度量（原来点第 n 个字
   常弹到别的字）；外点隐藏改 33ms 独立轮询（250ms 轮询漏检绝大多数
   短点击）；提交快照（识别期间新写的笔画不再被清掉、capture 记录
   不再错位）；删一笔后刷新预识别；菜单打开期间挂起外点隐藏；
   captures.jsonl 超 64MB 自动归档到 data/archive/。
5. 评测基建：新增 engine/eval_captures.py（真实笔迹离线回归 + 融合权重
   网格 + 路A重采样/路B渲染高度扫描）；eval_m0.py 修正统计口径（旧版把
   现行输出当"融合"和"纯路A"重复计数）。
6. **全局热键失灵（用户实测，v2.5 修复）**：RegisterHotKey(None,...) 的
   WM_HOTKEY 走线程消息队列，Qt 标注为 **windows_dispatcher_MSG** 而非
   windows_generic_MSG；旧过滤器只认后者 → 回调永不触发。已两者都收，
   `panel/test_hotkey.py` 真机闭环验证 PASS（合成 Ctrl+Alt+H → 回调）。
7. **唤起（显隐）缺陷（v2.5）**：面板隐藏时点击输入框**完全无处理**
   ——一旦隐藏且热键失灵就永远唤不回。已补隐藏态点击判定；焦点判定加
   原生插入符（GetGUIThreadInfo，快且覆盖 Chrome 文本框）+ UIA 双级、
   失败自动重试 5 次（Chromium 无障碍树按需构建，单次判定常来不及）。
8. 手写区改**单行大格**（N_ROWS=1，ROW_H=96→170），两行结构相关硬编码
   全部参数化。
9. **v2.6 交互重做：悬浮球开合**（用户实测反馈）。桌面常驻 52px 圆形
   悬浮球（可拖动，位置存 data/ui_state.json），点击开/关面板，热键
   等效；**删除**整套"点面板外隐藏 + 点输入框自动弹回"启发式（依赖
   原生插入符/UIA 焦点检测，Electron/浏览器场景天然不可靠——用户
   评价"太难了"）。Ball 类在 panel/main.py 末尾。
10. **v2.6 书写质感**：① 笔锋包络 `apply_stroke_taper()`——按弧长调制
   线宽（起笔轻入、行笔中段微鼓、收笔出锋）+ 沿弧长对称三点宽度平滑；
   仅影响显示，识别不受影响；手机手写键盘的无压感笔锋即同类算法。
   ② 笔尖预测：_chain 按最近两点瞬时速度外推画"未来线"，整行重绘（无
   残影），遮住笔尖滞后改善跟手感。③ _chain 改为整行 update。
11. **v2.7 UI 加料 + 微调**（用户要求"狠狠加料，增料优先"）：
   ① 毛玻璃 = **自采样背景模糊**（v2.7.4 定稿）：系统级方案在本机不可用
   （见下），改为面板**隐藏时**抓取屏幕区域 → 1/14 + 1/9 降采样/平滑
   升采样双级模糊 → 存为底图，叠白霜层（α≈124）+ 磨砂噪点 + 高光的
   自动显示模式（`prepare_backdrop()` / `refresh_backdrop_live()`，
   拖动面板后瞬隐一帧重采）。**⚠ 重大实测结论（2026-10-06，本机 Win11
   build 26200）：SetWindowCompositionAttribute 的亚克力/模糊染色
   （state 3/4）渲染为"发白的实心磨砂"，把背景完全盖住（黑底都透不
   出来）——三组对照实验实锤，是"透明了个寂寞"的根因。已弃用
   （USE_ACRYLIC_ACCENT=False）；想要系统级真模糊需改用
   DwmSetWindowAttribute 系统背景（要非分层窗口，属重做项）。**
   ② 粒子系统 `Particles`：常驻细尘漂浮粒子 26 颗（半径 0.5~1.3px）+
   特效爆发（收笔 9 / 上屏 24 / 纠正 8 / 清空 14）。
   ③ 悬浮球重做：SIZE 76（留光晕边距）、径向渐变球体 + 脉冲光晕 +
   两颗环绕微粒 + 悬停增亮；**球不挂任何亚克力染色**（染色会形成
   "黑方块"包裹——用户实测违和，v2.7.1 已移除）。
   ④ 笔锋增强（用户反馈两次"不够明显"）：基准线宽 4.2→5.0、收笔出锋
   收到 ~7%（0.93 系数）、出锋上限 48px、中段鼓肚 +14%、起笔 0.32；
   笔尖预测 18ms→10ms。
12. **v2.8 实时毛玻璃（用户要求"背景在动，静态快照穿帮"）**：
   方案 = **WGC 实时采样 + WDA_EXCLUDEFROMCAPTURE 排除**——
   `BackdropSampler`（panel/main.py，基于 pip 包 `windows-capture`，
   轮子 cp39-abi3 支持 3.13）：WGC（Windows Graphics Capture，DWM 通道）
   持续采主屏（≤11fps，`minimum_update_interval=90`），回调线程只做
   numpy 步长降采样存小图；面板打 `SetWindowDisplayAffinity(0x11)` 后
   **WGC 拍不到面板/悬浮球自身**（实测确认；GDI BitBlt 同样会被排除，
   所以面板从此对一切截屏/录屏隐形——这是实时取背景的必要代价，
   开关 `LIVE_BLUR` 在 panel/main.py 顶部，关掉即回退静态快照模式且
   恢复可截屏）。GUI 侧 90ms 取最新采样 → 小图平滑放大当玻璃底。
   实时性实测 PASS（背景红→蓝→黄，面板采样像素同步变化）。
   三级兜底：实时小图 → 静态快照（隐藏时抓屏）→ 纯半透明渐变。
   另：圆角收敛（面板 18→15、按钮 15→10、胶囊/判定区 10→6/7、内框→9）。
13. **v2.9 软件化封装**（用户要求：安装器/快捷方式/系统托盘/设置界面）：
   ① `panel/appcfg.py`——设置系统核心（SCHEMA 10 项参数、data/settings.json
   持久化、开机自启注册表读写），纯标准库。② `panel/settings.py`——浅色
   磨砂风格设置窗口（滑块实时数值、恢复默认、应用即生效回调）。
   ③ `panel/main.py`——所有可调参数改为读 appcfg.SETTINGS（线宽/速度调制/
   笔锋三项/预测ms/霜化/粒子数/不透明度）；`Panel.apply_settings()` 免重启
   生效；`_start_live_sampler()` 拆出（设置可开关实时模糊）；系统托盘
   `_build_tray()`（显示-隐藏/设置/开机自启/关于/退出，双击球同开关）；
   `--boot` 静默托盘自启模式；`setQuitOnLastWindowClosed(False)`。
   ④ `installer/installer.pyw`（tkinter 零依赖 GUI）+ `install.bat`：
   复制 基础Python(runtime)+venv(env)+app+ref 四件套（robocopy /MT:16，
   自动排除 .git -267MB / data / 开发残留；总计 ~29.5k 文件 1.6GB）→
   重写 pyvenv.cfg 指向随装 runtime、剔除 editable finder →
   生成 hwime_launch.pyw（注入 HWIME_REF + ref 入 sys.path，免 editable
   即可 import cnn_chinese_hw）与 uninstall.pyw → 快捷方式（PowerShell
   WScript.Shell，桌面+开始菜单）→ 卸载注册表项 HKCU\...\Uninstall\hwime
   → 环境自检（导入 PySide6+torch+rapidocr）。安装器坑：模板字符串里
   嵌 `'{}'.format` 与外层 `.format(install=)` 的匿名占位符冲突（IndexError，
   实测卡死一次）→ 转义 `{{}}`；pythonw 无控制台 + 管道缓冲 ⇒ 全程
   `filelog()` 落盘 %TEMP%\hwime_install.log 便于排查。
   ⑤ 图标 `docs/hwime.ico`（PIL 绘制多尺寸 16-256：深蓝球+手字）。
   ⑥ 安装布局：<install>/{runtime, env, app(+launch), ref/cnn_chinese_hw,
   uninstall.pyw, hwime.ico}；卸载器 kill 窗口→删快捷方式/注册表→
   detached cmd 延时 rd 自删。测试：沙盒 --test 全通过（29.5k 文件、
   自检 PASS、装好副本裸 import 失败/launcher 路径下 Pipeline OK ——
   证明真自包含）。

## 引擎策略结论（真实数据说话）

- captures.jsonl 共 808 条但**只有 16 条唯一笔迹**；其中 11 条是融合
  时代产物（text_fused==a_top1 拼接，垃圾）、5 条 B 主导时代。该文件
  里还混着旧机的双流桩笔（eval_captures 的 merge_stub_strokes 负责清理）。
- **上屏文本保持路 B 主导**（当前 pipeline.py 行为）：实写分布下路 B
  5/5 全对，路 A top1≈35-38%、top3≈45%；旧"逐位融合"任何权重都把正确
  的 B 拖坏（"你好"→"们牡"）。
- 路 A 弱 ≠ 流程错：切分干净（cells 渲染图已验证），是**模型风格失配**
  （top1 置信度普遍 ≤0.5，行书完全无解）。**最高价值待办 = 个人字库
  微调**（用 captures + 上游 train.py/HandwritingRegister 路线）。
- 合成评测（eval_m0）里路 A 85%、B 50% 是训练集内评测，虚高，别被误导。

## 待办（按优先级）

### M2 准确率（更新）
- [ ] **个人字库微调路 A**（采集→标注→微调 hw_model，上游 train.py 现成；
      面板已落盘干净笔迹，建议先攒 100+ 句人工确认的样本）
- [ ] 纠正菜单体验：B 错字时提供"路 A 候选 + 常用字"混合列表
- [ ] 增量词级识别（书写过程中逐词出候选）
- [ ] 路 A 导出 ONNX（为 M5 准备，上游自带 export_onnx.py）

### M3 Arch Linux
- [ ] PySide6 面板直接移植（engine 无平台依赖，panel 需换 SendInput 为
      xdotool/wtype；表笔事件流差异需重新验证双流问题是否也存在）
- [ ] fcitx5 engine 插件化（C++/Qt，参考 fcitx/fcitx-handwriting）
- [ ] 宿主→arch VM SSH 通道部署实测（ssh -i ~/.ssh/id_ed25519 syks@192.168.192.130）

### M4 macOS 26
- [ ] 面板移植 + CGEvent 注入；VM 软件渲染下验证

### M5 Android 10+
- [ ] Kotlin InputMethodService + SurfaceView 手写区
- [ ] ONNX Runtime Mobile 端内嵌路 A；路 B 视内存取舍
- [ ] vivo 实机（Android 11）+ Bliss OS VM（adb root）双通道测试

## 验证命令速查（venv python）

```
set PY=C:\Users\lab\.workbuddy\binaries\python\envs\hwime\Scripts\python.exe
%PY% engine\test_route_a.py          # 路 A 冒烟（我=0.976）
%PY% engine\test_route_b.py          # 路 B 冒烟
%PY% engine\eval_m0.py               # 合成评测（训练分布，A 虚高）
%PY% engine\eval_captures.py [--sweep-a] [--sweep-b] [--detail]
%PY% panel\test_send_input.py        # 离线；加 --live 真机注入验证
%PY% panel\test_hotkey.py            # 热键链路（合成按键真机验证）
%PY% panel\smoke_test.py             # 离屏全链路（PASS 含 wrap）
%PY% panel\main.py                   # 启动面板
```

## 关键事实（接手必读，继续保留）

1. 上游 cnn_chinese_hw 是 PyTorch 重写版；`route_a.py` 里的 iso_tools
   stub 仍然必要（上游 _LoadedModel 引用作者私有包）。
2. 路 B 跳过检测器（rec-only）是刻意为之：墨迹位置已知，det 会切碎结果。
3. 面板不夺焦（WA_ShowWithoutActivating + WindowDoesNotAcceptFocus），
   所有交互走鼠标/笔/全局热键，别加"按 Enter"类逻辑。
4. QAbstractNativeEventFilter 不是 QObject，挂不了 Signal，用回调。
5. worker 异常被塞进 res["error"]，调试看 _session-temp 或 stdout。
6. 压感实测不可用（驱动恒 ~0.02），线宽由速度模拟，别浪费时间接压感。
7. 真机注入测试会把文本打进"当前前台窗口"——跑 --live 前先清空焦点
   目标（比如点一下桌面），别让它打进正在编辑的文档。
