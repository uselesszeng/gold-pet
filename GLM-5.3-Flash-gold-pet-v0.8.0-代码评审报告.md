# gold-pet v0.8.0 代码评审报告（GLM-5.3-Flash，2026-10-01）

> 评审对象：`C:\Users\tj-435\WorkBuddy\2026-09-30-09-31-13\gold-pet`（git HEAD `c693c55`，tag v0.8.0）
> 评审方式：全程只读（读文件 / grep / git log / git ls-files），未修改任何项目代码、未安装依赖、未运行构建或启动命令。唯一写入为本报告文件。

---

## 1. 评审范围与方法

**范围**
- `gold_pet/` 全部 13 个源码模块（含 `.py.bak` 备份文件的甄别）：`main.py`、`ui.py`、`control_server.py`、`config.py`、`paths.py`、`price.py`、`notifier.py`、`care.py`、`bubble.py`、`tray_icon.py`、`shortcut.py`、`autostart.py`、`__init__.py`
- `tools/`：重点 `pet_mcp_server.py`（MCP 桥接），其余 12 个脚本（diag_*、gen_assets、cnb_release_upload、check_price、switch_icon、grab_screen、import_dsh_assets）略读
- 构建配置：`gold-pet.spec`、`pyproject.toml`、`version_info.txt`、`requirements*.txt`、`.cnb.yml`、`run.py`/`run.bat`/`run.sh`
- `tests/`（11 个文件，重点 `test_control_server.py` 逐条核对声明）；`mobile/flutter/`（判断归属后略读）
- 历史文档：`CHANGELOG.md`、`程序修改点.md`、`docs/review/Hy4-gold-pet-v0.3.0-代码评审文档.md`（用于核对历史修复声明）
- 跳过：`dist/`、`build/`、`release/`、`.venv/`、各 cache 目录

**方法**
- 安全维度逐条核对：监听绑定面、鉴权、CORS/CSRF 面、端点能力（能否触发文件/命令操作）、参数注入、DoS 面；全局 grep `eval/exec/subprocess/os.system/pickle/yaml.load/shell=True`
- 正确性：人工走读 Qt 线程模型（HTTP 线程 ↔ UI 线程信号转发）、定时器生命周期、配置读写与 sanitize 链路；对可疑调用逐一核对被调函数签名
- 历史声明核对：将 Hy4 v0.3.0 评审的 17 项问题、修复记录表与现行代码逐项比对；对 CHANGELOG v0.5~v0.8 的行为性声明抽查代码实现

**版本核实**：`pyproject.toml` 仅有工具链配置（ruff/mypy/pytest/coverage），**无 `[project]` 表、无版本字段**；实际版本来源为 `gold_pet/__init__.py` 的 `__version__ = "0.8.0"`、`version_info.txt`（filevers 0.8.0）、git 提交 `v0.8.0`，三处一致。**结论：实际版本即 0.8.0，与标题相符；但 pyproject.toml 本身不承载版本号**（见 P3-09）。

---

## 2. 项目概况

黄金桌宠（gold-pet）v0.8.0：Windows 桌面金价挂件，Python 3.11 + PySide6（非 Electron，无 Electron 配置面）。核心链路：`run.py → gold_pet/main.py → PetWindow`（透明置顶桌宠 + 托盘 + 提醒中心 + 走势图）；行情来自新浪财经免密钥公开接口（`hq.sinajs.cn`），60s 定时刷新、失败指数退避；配置/历史/日志全部落 `%APPDATA%\GoldPet\`。

v0.8.0 新增「AI 控制鲸娘」：
- `gold_pet/control_server.py`：QThread 内嵌 `ThreadingHTTPServer`，仅绑 `127.0.0.1` 随机端口，`secrets.token_urlsafe(24)` 随机 token（请求头 `X-GP-Token`），port/token/pid 写发现文件 `control.json`；6 个端点（/status /say /play /pat /tease /move）经 Qt Signal 跨线程转发 UI 线程执行。
- `tools/pet_mcp_server.py`：FastMCP stdio 桥接，6 个 MCP 工具，每次调用重读发现文件。
- 规模：`gold_pet/` 约 3,545 行 + `tools/pet_mcp_server.py` 165 行；测试 11 个文件、约 191 个用例。

`mobile/flutter/` 为独立的 Flutter 移动端骨架（仅 pubspec + lib，README 自述「待补」项众多），复用同一新浪接口，**不属于桌面主流程**，本次仅静态略读（未发现安全问题，仅拉取固定 URL）。

---

## 3. 分维度结论

### 3.1 安全（本项目重点）—— 总体评价：良好，安全模型设计认真且与实现一致

**a) 本地 HTTP 服务 `control_server.py`**
- **绑定面**：`build_server(("127.0.0.1", 0), ...)`（control_server.py:231），仅回环 + 随机端口，外部机器不可达。✔
- **鉴权**：所有 GET/POST 路由先 `_check_auth()`（:99、:107），token 每次启动随机生成；401 响应不泄露信息。发现文件写入用户目录（仅本机当前用户可读）。⚠ 两处弱点见 P2-01（文本注入）、P3-01（非常量时间比较）、P3-02（无并发上限）。
- **CORS/CSRF**：服务不返回任何 `Access-Control-Allow-Origin` 头，浏览器侧跨源读取被同源策略拦截；浏览器发起的跨站请求无法携带自定义头 `X-GP-Token`（非 simple header，预检即失败；即便 simple POST 也会 401），**CSRF/DNS-rebinding 面被 token 要求有效挡住**。✔
- **端点能力**：6 个端点全部只做「说话 / 播放白名单内动画 / 摸头 / 挪窗口」，**无任何文件读写、命令执行、配置修改能力**；`/play` 有状态白名单（loading/offline 拒绝）；`/move` 结果经 `_clamp_to_screen` 夹回屏幕可视区。✔
- **参数注入**：无路径拼接（路径走白名单 `==` 精确匹配）；JSON 解析强制要求 dict；请求体 64KB 上限（413）。⚠ `/say` 的自由文本未转义直接进 QLabel 富文本（P2-01）。
- **DoS 面**：`ThreadingHTTPServer` 每连接一线程且无上限，恶意本机进程可刷连接耗线程/内存——但攻击者需已在本机同用户会话内，此时收益极低。P3-02。
- **Windows 探活**：`_pid_alive` 正确避开 `os.kill(pid,0)` 在 Windows 等价 TerminateProcess 的坑，走 OpenProcess + GetExitCodeProcess。✔

**b) MCP 桥接 `tools/pet_mcp_server.py`**
- 暴露面仅 6 个桌宠控制工具，**不含任意文件读写、命令执行**；与桌面端 `/play` 白名单一致。
- `port` 经 `int(port)` 转换后拼 URL，无 URL/参数注入；请求固定发往 `127.0.0.1`。
- ⚠ 桥接侧 `_discover()` 与桌面端 `read_control_info()` 逻辑重复且已分叉：桥接不做 pid 探活，残留文件会导致多一次失败调用（仅体验问题，见 P3-05）。

**c) 桌宠主体**
- **无 exec/eval/subprocess/os.system**（grep 确认：命中项均为 Qt 对话框 `dlg.exec()`；`tools/diag_resources.py` 的 subprocess 是开发诊断脚本）；**无 pickle/yaml.load**；无自动更新/下载逻辑（发布走手工上传），不存在「下载不校验」面。
- **配置注入面**：`Config.sanitize()` 对 interval/opacity/metal/unit/pos/布尔/勿扰时间等全部收敛白名单或夹取，手改坏配置不崩。✔（但 `history.json`/`alert_history.json` 缺同等逐项校验，见 P2-03。）
- `gold-pet.spec` 里 `exec(_init.read_text(...))` 仅在构建期执行自身版本文件，可接受（已加 noqa 注释）。
- `tools/cnb_release_upload.py` token 走 `--token`/环境变量/`~/.cnb/token`，源码无硬编码凭据。✔

### 3.2 正确性 —— 良好，一处「文档声明与实现不符」的功能错误（P1-01）

- 线程模型正确：HTTP 线程只 `action_received.emit(dict)`，接收者 PetWindow 住主线程 → AutoConnection 走 Queued；`_control_state()` 只读纯 Python 原子属性。✔
- 动画/状态机、提醒去重（档位/水位/武装-触发）、跨零点勿扰判断（Notifier 侧）、多屏夹取、退出清理（停服删文件）实现与注释一致。✔
- **P1-01**：`PetWindow._care_tick` 调用 `in_quiet_hours` 时**实参顺序与函数签名不符**，导致关怀提醒的勿扰判断恒为 False——与 CHANGELOG v0.7.0「遵守勿扰时段」、设置面板提示「勿扰时段内不会出声」的声明不符（详见问题清单，含双重证据）。
- 次要：失败退避与固定间隔 `refresh_timer` 并存，退避并未「接管」刷新周期，只叠加了重试（P3-04）；`/move` 双击会先触发一次单击摸头（Qt 事件语义，无碍）。

### 3.3 健壮性 —— 良好

- 崩溃兜底：`sys.excepthook` 写滚动日志（main.py:37-42）✔；配置脏数据全部 sanitize ✔；资产缺失降级（icon 不存在回空 QIcon、帧缺失回 idle/空 pixmap）✔；托盘不可用降级为关窗即退出 ✔；端口用随机 0 端口绑定，无「端口被占」场景 ✔；`save_cache`/`HistoryStore` 写失败静默吞 OSError ✔。
- 缺口：`history.json`/`alert_history.json` 的**逐项数据**无类型容错，手改坏后刷新/提醒路径会抛异常（P2-03）；`setup_logging` 在日志文件不可写时启动即抛（罕见，未列条目）。

### 3.4 性能 —— 可接受，一处缓慢泄漏

- 帧动画预加载为 QPixmap、按状态切帧、安静档停表，托盘图标仅刷新时重绘，均无每帧重算。✔
- **P2-02**：每次 refresh 新建 `PriceWorker(self, parent=window)`，旧 worker 只 disconnect 不销毁，作为 window 子对象长期累积（60s 档约 1440 个/天），缓慢内存增长。
- `HistoryStore.append` 每次成功刷新全量重写 history.json（≤1440 点），量级可接受（P3）。
- `quit_app` 最多阻塞 UI 约 10s（worker.wait(10000)，worker 无事件循环 `quit()` 无效，只能等 fetch 8s 超时）+ 控制服务 5s（P3-03）。

### 3.5 可维护性 —— 中上

- 分层清晰（price/notifier/care/bubble 纯逻辑可单测）、注释解释「为什么」，测试覆盖有门禁（fail_under 65%）、CI 完整。✔
- 存在：发现文件读取逻辑双实现且分叉（P3-05）、居中逻辑双实现（P3-06）、`.py.bak`/备份目录/截屏残留（P3-07）、requirements.txt 混入桥接依赖且与注释矛盾（P3-08）、生产未用的 `read_control_info`（并入 P3-05）、调用私有函数 `shortcut._target_path()`（P3-10）。

---

## 4. 问题清单

排序：P0 → P1 → P2 → P3。（本次无 P0。）

### P1-01 关怀提醒的勿扰判断实参顺序错误，勿扰时段照常出声（文档声明与实现不符）

- **级别**：P1（文档/修复记录声称完成但代码未实现；行为性缺陷）
- **位置**：`gold_pet/ui.py:760-762`（根因调用点）；`gold_pet/notifier.py:102-122`（签名）；`gold_pet/care.py:5-6`（声明）；`CHANGELOG.md:170-174`（声明）；`gold_pet/ui.py:1306-1309`（设置面板用户可见声明）
- **根因**：模块级函数签名是 `in_quiet_hours(now_minutes=None, start="", end="")`——「当前分钟数」在第一位；`_care_tick` 却把配置当实参按 `(start, end)` 顺序传入。
- **证据（ui.py:760-762）**：

  ```python
  dnd = bool(self.cfg.get("dnd_enabled", False)) and in_quiet_hours(
      self.cfg.get("dnd_start", "23:00"), self.cfg.get("dnd_end", "08:00")
  )
  ```

  调用展开为 `in_quiet_hours(now_minutes="23:00", start="08:00", end="")`：`end=""` 使 `parse_hhmm("")` 返回 None → 函数恒返回 False。结果：开启勿扰后关怀提醒（喝水/久坐/护眼）**仍然照常出声**，与 care.py 注释「勿扰时段内不出声」、CHANGELOG v0.7.0「**遵守勿扰时段**」、设置面板提示「勿扰时段内不会出声」三处声明相悖。注意 `Notifier.in_quiet_hours()`（notifier.py:344-352）调用顺序正确，行情系统通知的勿扰不受影响——仅关怀提醒路径坏。
- **修复建议**：`in_quiet_hours(None, self.cfg.get("dnd_start", "23:00"), self.cfg.get("dnd_end", "08:00"))`；更彻底的做法是把 `notifier.in_quiet_hours` 签名改为 `(start, end, now_minutes=None)` 并借 mypy 的参数类型（now_minutes: int）让这类错误在类型检查期暴露；补一条「勿扰时段内 care.due 不产出」的单测。

### P2-01 `/say` 自由文本未转义未限长，可向气泡注入任意 HTML（QLabel 富文本注入）

- **级别**：P2（网络可达端点的输入校验缺失；本机回环 + token 缓解，故不到 P1）
- **位置**：`gold_pet/control_server.py:117-129`（端点接收）、`tools/pet_mcp_server.py:120-121`（透传）、`gold_pet/ui.py:1043-1044`（入 UI）、`gold_pet/bubble.py:84-86`（未转义插值）
- **根因**：AI（或本机任意读到 `control.json` 的进程）提供的 text 原样进入 `render_line_html` 的 f-string，`QLabel.setText` 自动按富文本渲染。
- **证据（bubble.py:84-86）**：

  ```python
  def render_line_html(text: str) -> str:
      """渲染一句话气泡（台词 / 引导）。"""
      return f'<div style="font-size:12px;color:#ffd978">{text}</div>'
  ```

  text 可携带 `<img src="file:///C:/..." width=800>`（QTextDocument 会加载本地文件资源）、任意 `<table>/<h1>` 或 64KB 长文本，把桌宠气泡撑爆/变形/显示任意本机图片；无脚本执行面（QLabel 不跑 JS），故危害限于 UI 破坏与信息显示。
- **修复建议**：在 `control_server.py` 收到 text 后先 `html.escape()` 再入分发（或 `render_line_html` 内部统一 escape，台词池走另一条已知安全路径）；同时给 text 设长度上限（如 200 字符，超出截断或 400）。

### P2-02 每次刷新重建 PriceWorker 且不销毁，QThread 子对象持续累积（缓慢内存泄漏）

- **级别**：P2（性能/内存）
- **位置**：`gold_pet/ui.py:600-615`（refresh）、`:265-276`（PriceWorker 定义）
- **根因**：`self._worker = PriceWorker(self.service, self)` 以 window 为 parent；旧 worker 仅 `snapshot_ready.disconnect(...)`，线程对象本身从不 `deleteLater`，父对象关系使其永生。60s 刷新档下每天新增约 1440 个 QThread Python/Qt 对象。
- **证据（ui.py:613-615）**：

  ```python
  self._worker = PriceWorker(self.service, self)
  self._worker.snapshot_ready.connect(self._on_snapshot)
  self._worker.start()
  ```

- **修复建议**：在 disconnect 后对旧 worker `worker.deleteLater()`（或复用单例 worker + `finished` 后置标志）；或在线程结束时 `self._worker.finished.connect(self._worker.deleteLater)`。

### P2-03 `history.json` / `alert_history.json` 逐项数据无容错，脏数据使刷新渲染与提醒链路抛异常

- **级别**：P2（边界条件/脏数据容忍；settings.json 有 sanitize 而这两个文件没有）
- **位置**：`gold_pet/price.py:295-300`（load 仅判 dict）、`gold_pet/notifier.py:429-437`（filter_series 的 `float()`）、`gold_pet/ui.py:740-741`（兜底分支同样 `float()`）、`gold_pet/notifier.py:383-390`（Alert.from_dict 的 `float(data.get("at", 0.0))` 未包 try）
- **根因**：文件手改坏（或磁盘损坏）后，`p`/`t`/`at` 为非数值字符串时 `float()` 直接 ValueError，异常发生在 `_render → _update_spark` 或 `Notifier._ensure_loaded`（每次 evaluate 都会走到），该次刷新/提醒链路中断。
- **证据（notifier.py:437）**：

  ```python
  return [float(p.get("p", 0.0)) for p in series if float(p.get("t", 0.0)) >= cutoff]
  ```

  与（notifier.py:388-390）：

  ```python
  self._history = [Alert.from_dict(item) for item in raw if isinstance(item, dict)][
      -self.max_history :
  ]
  ```

- **修复建议**：抽一个 `_safe_float(v, default=0.0)` 用于序列点与 `Alert.from_dict`；`_ensure_loaded` 的列表推导包 try/except 逐项跳过坏记录；可选：加载时发现坏文件即改名备份并重建。

### P3-01 token 比较非常量时间

- **级别**：P3
- **位置**：`gold_pet/control_server.py:70`
- **证据**：`if self.headers.get("X-GP-Token", "") == self.server.token:`
- **根因/影响**：`==` 短路比较理论上可测时；服务仅绑回环 + 24 字节随机 token，实际风险极低。
- **修复建议**：改 `hmac.compare_digest(self.headers.get("X-GP-Token", ""), self.server.token)`，一行成本。

### P3-02 ThreadingHTTPServer 无并发/速率上限（本机 DoS 面）

- **级别**：P3（仅回环可达，攻击者需已在本机同用户会话）
- **位置**：`gold_pet/control_server.py:168-172, 244`
- **证据**：`ThreadingHTTPServer` 默认每连接一线程、无队列上限；`serve_forever(poll_interval=0.2)`。
- **修复建议**：可给 `PetHTTPServer` 加请求计数/并发信号量（如同时 >32 个连接返回 503），或接受现状并在注释中写明威胁模型。

### P3-03 退出路径阻塞 UI 最长约 10 秒

- **级别**：P3
- **位置**：`gold_pet/ui.py:1093-1096`（quit_app）、`:1015-1022`（_stop_control）
- **证据**：`worker.quit(); worker.wait(10000)`——PriceWorker.run() 无事件循环，`quit()` 无效，只能等 `fetch` 的 8s 网络超时自然返回；叠加 `control.wait(5000)`。
- **修复建议**：`fetch` 已在独立线程，退出时不必等它：缩短 wait 或直接放弃等待（daemon 线程随进程结束）；控制服务 wait(5000) 可保留。

### P3-04 失败退避与固定间隔刷新定时器并存，退避语义名不符实

- **级别**：P3（行为与注释意图偏差，无功能损害）
- **位置**：`gold_pet/ui.py:617-633`（_on_snapshot 启动 `_retry_timer`）、`:536-537`（refresh_timer 仍按 interval_sec 固定触发）
- **根因**：失败后既保留 60s 固定刷新，又叠加 15s→300s 的重试定时器，「指数退避」实际是「额外重试」；成功恢复时才 `_apply_interval()`。
- **修复建议**：失败时 `refresh_timer.stop()`，把周期完全交给退避定时器，恢复成功后再重启固定周期。

### P3-05 发现文件读取逻辑双实现且已分叉（MCP 侧缺 pid 探活；桌面侧函数生产未用）

- **级别**：P3（可维护性 + 轻微行为不一致）
- **位置**：`tools/pet_mcp_server.py:47-61`（_discover）vs `gold_pet/control_server.py:305-318`（read_control_info，生产代码无调用方，仅测试使用）
- **根因**：为避免桥接依赖 gold_pet 包而复制了路径/解析逻辑，但两份已出现语义差：`read_control_info` 做 pid 探活过滤残留文件，`_discover` 不做——桌宠被强杀后，MCP 第一次调用会拿残留文件白连一次（靠「桌宠没有响应」兜底文案收场）。
- **修复建议**：把 `_pid_alive` + pid 校验逻辑并入 `_discover`（ctypes 探活可平移，不引入 gold_pet 依赖）；或在桥接侧注明差异并接受。

### P3-06 窗口居中逻辑双实现

- **级别**：P3
- **位置**：`gold_pet/ui.py:1361-1384`（SettingsDialog._center_on_parent 方法）与 `:1643-1665`（模块级 _center_on_screen）——两段近乎相同的代码，FirstRunDialog 用后者、SettingsDialog 用前者。
- **修复建议**：统一收敛到模块级 `_center_on_screen`。

### P3-07 仓库工作区残留备份与截屏文件

- **级别**：P3（未被 git 跟踪，仅污染工作区，但 `gold_pet/*.py.bak` 在包目录内易被误引用/误打包）
- **位置**：`gold_pet/config.py.bak`、`gold_pet/paths.py.bak`、`gold_pet/price.py.bak`、`gold_pet/ui.py.bak`、`tools/gen_assets.py.v054.bak`、`assets/_backup_v055/`、根目录 `pet_shot.bmp`、`pet_shot.png`（`git ls-files` 确认均未入库）
- **修复建议**：删除或移入 `.gitignore` 管理的归档目录；`gold_pet/` 包内不放任何非 `.py` 文件。

### P3-08 requirements.txt 混入 MCP 桥接依赖且注释自相矛盾

- **级别**：P3
- **位置**：`requirements.txt`
- **证据**：文件内注释写「MCP 桥接进程（tools/pet_mcp_server.py）依赖；桌宠本体不依赖」，但 `mcp<2` 与 `httpx` 就写在同一份「运行时依赖」里——桌面用户按 README 装依赖会多装两个用不到的包（反之桥接用户却因此装上 PySide6/Pillow）。
- **修复建议**：拆分为 `requirements.txt`（本体）/ `requirements-mcp.txt`（桥接），CI 与文档同步。

### P3-09 pyproject.toml 无 [project] 表与版本字段

- **级别**：P3
- **位置**：`pyproject.toml`（仅 ruff/mypy/coverage/pytest 配置）
- **影响**：版本唯一真源是 `gold_pet/__init__.py`，`pyproject.toml` 无法回答「这是什么包、什么版本」；对纯应用项目可接受，但与「以 pyproject.toml 为准」的惯例不符。
- **修复建议**：接受现状则无需改；若想规范化可加 `[project] name/version`（version 动态指向 `gold_pet.__init__.__version__`）。

### P3-10 open_install_folder 调用 shortcut 模块私有函数

- **级别**：P3
- **位置**：`gold_pet/ui.py:969-978`（`shortcut._target_path()`，下划线私有）
- **修复建议**：在 shortcut.py 提供公开别名（如 `target_path()`）或把「程序所在文件夹」计算逻辑公开化。

---

## 5. 上轮遗留项核查结果

**本次为首次系统评审（无上一轮 GLM/Hy4 全量报告作为「上轮遗留」基线）。** 但仓库内存在历史文档 `docs/review/Hy4-gold-pet-v0.3.0-代码评审文档.md`（17 项问题 + 修复记录表）及 `CHANGELOG.md` / `程序修改点.md`，已逐项核对历史修复声明，结果如下：

**Hy4 v0.3.0 的 17 项问题全部核实已落地，无「声称已修但代码未实现」**：
- #1 版本号 → `__init__.py __version__` 机制 ✔；#2 屏幕边界 → `_move_to_default` 多屏校验 + `_clamp_to_screen` ✔；#3 信号改名 → `snapshot_ready` 且 refresh 前 disconnect ✔；#4 LICENSE（MIT）✔；#5 单实例 → `QLockFile`（main.py:159-164）✔；#6 配置校验 → `Config.sanitize()` ✔；#7 换算行情补 high/low → price.py:109-133 ✔；#8 冷却可配置 → `alert_cooldown_sec` + 设置面板 ✔；#9 托盘降级 → `build_tray` 判 `isSystemTrayAvailable` + `closeEvent` 分支 ✔；#11 CI → `.cnb.yml`（push/PR/tag 三触发）✔；#12 raw string ✔；#14 旧 worker disconnect ✔；#17 菜单复用 → `build_menu()` 两处共用 ✔；#10 体积/#13 写入频率（v0.5 已改 append-only 落盘，ui.py:677）/ #15 测试/#16 Flutter 为声明的「暂缓/优化」项，与现状一致。
- CHANGELOG 抽查：v0.5.6「状态变化才提醒」✔（notifier 档位/水位/武装语义齐全）；v0.7.2「安静档定格 + 切档台词」✔；v0.7.3「弹窗图标显式化 + 🐥 清除」✔（grep 无残留）；v0.8.0「新增 20 条单测」✔（test_control_server.py 恰 20 个用例）。
- **唯一的声明与实现不符项即 P1-01**（v0.7.0 声明关怀提醒「遵守勿扰时段」，实现在 ui.py:760-762 因实参顺序错误恒不生效）。

---

## 6. 总体结论与修复优先级排序

**总体结论：可以发布/继续使用（无 P0 阻塞项）。** v0.8.0 的核心新增「AI 控制服务」安全模型设计正确且执行到位——仅回环绑定、随机 token、端点能力白名单化、请求体上限、跨线程转发规范、Windows 探活避坑，属于同类「本地 AI 控制面」里做得规范的一档。历史修复可信度高（17/17 项落地）。主要缺口集中在一处声明不符的勿扰判断（P1-01）、`/say` 文本未消毒（P2-01）和两处资源/数据容错（P2-02、P2-03）。

**修复优先级排序（建议按此顺序处理）：**

1. **P1-01** 关怀提醒勿扰实参顺序错误（一行修复 + 补单测，消除「声明不符」）
2. **P2-01** `/say` 文本 `html.escape` + 长度上限（堵住唯一的网络端点输入面）
3. **P2-03** history/alert_history 逐项容错（与 settings 的 sanitize 同等对待）
4. **P2-02** PriceWorker 生命周期（deleteLater，止住缓慢泄漏）
5. **P3 批次**（可一次性收尾）：P3-01 常量时间比较 → P3-05 发现文件逻辑并轨 → P3-03 退出阻塞 → P3-04 退避语义 → P3-06/07/08/09/10 代码卫生

**统计**：P0 × 0，P1 × 1，P2 × 3，P3 × 10。
