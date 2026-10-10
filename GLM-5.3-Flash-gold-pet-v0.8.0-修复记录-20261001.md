# gold-pet v0.8.0 修复记录（GLM-5.3-Flash，2026-10-01）

> 对应评审报告：`GLM-5.3-Flash-gold-pet-v0.8.0-代码评审报告.md`
> 本次仅修复 P1-01，最小改动，未做重构，未改版本号，未执行 git commit。

---

## 1. 修复项：P1-01 关怀提醒勿扰判断实参顺序错误

### 问题回顾

`PetWindow._care_tick`（关怀提醒：喝水/久坐/护眼）判断勿扰时段时，把配置
按 `(start, end)` 顺序传给了模块级函数 `notifier.in_quiet_hours`，而其实际
签名为 `in_quiet_hours(now_minutes: int | None = None, start: str = "", end: str = "")`
——「当前分钟数」在第一位。

原代码展开为 `in_quiet_hours(now_minutes="23:00", start="08:00", end="")`：
`parse_hhmm("")` 返回 None → 函数恒返回 False。结果：开启勿扰后关怀提醒
仍然照常出声，与 `care.py` 注释、CHANGELOG v0.7.0「遵守勿扰时段」、设置面板
提示「勿扰时段内不会出声」三处声明相悖。

### 修复方案

在调用处按签名补齐首个实参：now_minutes 传 `None`（函数内部自动取本地当前
时间），start/end 依次传配置值。

### 签名与语义核对

- `gold_pet/notifier.py:102-122`：签名 `(now_minutes=None, start="", end="")`；
  跨午夜区间（start > end，如 23:00-08:00）的分支 `now_minutes >= begin or now_minutes < finish`
  逻辑正确（同日区间 `begin <= now_minutes < finish` 亦正确）；起止相同或
  解析失败时返回 False（视为未启用），语义无问题，**无需改动函数本身**。
- 其余调用点逐一 grep 核对，无同类错误：
  - `gold_pet/ui.py:700`：`self.notifier.in_quiet_hours()`，走实例方法，正确；
  - `gold_pet/notifier.py:348-351`：`Notifier.in_quiet_hours` 内部调用，实参
    顺序 `(now_minutes, dnd_start, dnd_end)` 正确；
  - `tests/test_notifier.py`：既有用例均按正确契约调用。

## 2. 改动文件清单

| 文件 | 位置 | 改动 |
|---|---|---|
| `gold_pet/ui.py` | :760-762（`_care_tick`） | `in_quiet_hours(start, end)` → `in_quiet_hours(None, start, end)`，修正实参顺序（唯一逻辑改动，1 行） |
| `tests/test_notifier.py` | :110-135 附近（新增用例） | 新增 `test_in_quiet_hours_call_contract_cross_midnight`：锁定 `(now_minutes, start, end)` 调用契约，覆盖跨午夜 23:00-08:00 区间（23:30/07:00 在内、12:00 在外）、旧 bug 形态（`in_quiet_hours("23:00", "08:00")`）恒 False、以及 `Notifier.in_quiet_hours` 方法路径的跨午夜判断 |
| `CHANGELOG.md` | 0.8.0 的 `### 修复` 小节（:69-73） | 按项目既有格式补一条修复说明（项目无 Unreleased 小节，对应版本为 0.8.0） |

## 3. 验证方式与结果

环境：项目自带 `.venv`（Python 3.13，pytest 可用）。

1. `python -m py_compile gold_pet/ui.py` → 通过。
2. `pytest tests/test_notifier.py -q` → 57 passed，exit 0（含新增回归用例）。
3. `pytest tests/ -q`（全量）→ 全部用例通过（无 F/E，1 skipped，含新增用例
   共 +1），但进程退出码为 127，伴随「QThread: Destroyed while thread is
   still running」——已用 `git stash` 在未修改基线上复跑确认该现象与退出码
   **完全一致**，属既有测试收尾阶段问题（control_server 相关测试的 QThread
   清理），与本次修复无关。
4. 附注：系统 `python` 命令命中 Microsoft Store 占位 stub（无输出），
   故验证统一使用 `.venv\Scripts\python.exe`。

## 4. 残留风险

- 勿扰生效边界与 `Notifier` 行情提醒路径共享同一实现，修复后两条路径行为
  一致；但 `_care_tick` 的 dnd 值每 30 秒才重算一次，勿扰边界前后最多有
  30 秒的判断延迟（设计如此，注释已说明「计时保留，勿扰结束后到点的会
  立即补上」），非缺陷。
- 全量测试的 QThread teardown 退出码问题为既有现象（评审报告 P2-02 相关
  领域），本次未处理，不在 P1-01 范围内。
- 评审报告建议的「把签名改为 `(start, end, now_minutes=None)` 以便类型检查
  期暴露错误」属重构性方案，超出最小修改约束，未采用；已用回归单测锁定
  现有契约替代。

---

# P2 批次修复（同日追加）

> 修复评审报告 P2-01 / P2-02 / P2-03 全部 3 条。最小改动，未重构无关代码，
> 未改版本号，未执行 git commit。

## P2-01 `/say` 自由文本未转义未限长（QLabel 富文本注入）

### 方案

双端收口，且只转义一次（避免「服务端转义 + 渲染层再转义」双重转义）：

1. **渲染边界转义**（UI 接收链路收口）：`bubble.render_line_html` 内部统一
   `html.escape(text)` 后再插值。`/say` 文本经 `_handle_control` 进入
   `render_line_html` 时必经此口，无法再注入 `<img>`/`<table>` 等标签或加载
   本地图片；台词池、大肥鱼彩蛋等内部合法文本不含 HTML 标记，转义后显示不变。
2. **网络入口限长**（control_server 端点收口）：新增 `_MAX_SAY_CHARS = 500`，
   `/say` 端点对 `text.strip()` 截断后再分发，超长文本不再撑爆气泡。

`tools/pet_mcp_server.py` 仅透传 text，无需改动（限长与转义都在桌面端收口）。

### 改动文件

| 文件 | 位置 | 改动 |
|---|---|---|
| `gold_pet/bubble.py` | :9（`import html`）、:85-93（`render_line_html`） | 渲染前 `html.escape(text)`，补注释说明收口策略 |
| `gold_pet/control_server.py` | :47-49（`_MAX_SAY_CHARS`）、:124-128（`/say` 分发） | `text.strip()[:_MAX_SAY_CHARS]` 截断（500 字符） |
| `tests/test_bubble.py` | :79-86（新增） | `test_line_html_escapes_external_text`：`<img src="file:///...">` 被转义为 `&lt;img`、不再出现原始标签；普通文本不受影响 |
| `tests/test_control_server.py` | :108-118（新增） | `test_say_text_length_capped`：1000 字符 text 分发后截断为 500 |

## P2-02 每次刷新重建 PriceWorker 且不销毁（QThread 持续累积）

### 方案

`PetWindow.refresh()` 在确认旧 worker 线程已结束（`isRunning()` 为 False，
否则提前 return 不开新 worker——既有行为）并断开信号后，追加
`worker.deleteLater()`，把以 window 为 parent 的旧 QThread 对象交给事件循环
回收。线程退出竞态说明：能走到 deleteLater 说明线程已结束，且 deleteLater
延迟到主线程事件循环执行，不存在「销毁运行中线程」的竞态；`self._worker`
随每次刷新被替换，不会对同一 C++ 对象二次 deleteLater。60s 刷新档下每日
约 1440 个 QThread 对象的缓慢泄漏就此杜绝。

### 改动文件

| 文件 | 位置 | 改动 |
|---|---|---|
| `gold_pet/ui.py` | :607-614（`refresh()`） | 旧 worker disconnect 后追加 `worker.deleteLater()`（1 行逻辑 + 注释） |

## P2-03 history.json / alert_history.json 逐项数据无类型容错

### 方案

按「settings.json 有 sanitize、这两个文件也要同等对待」的口径，在读取与
渲染链路逐项容错：

1. `HistoryStore.load()`（price.py）：逐序列逐点清洗，非 dict 点、t/p 非数值
   点直接剔除并 `logger.warning` 记录（含条数）；非列表序列整条丢弃。坏数据
   不再进内存，`append` 的 `series[-1].get("t")` 与走势图渲染的 `p.get("p")`
   不再遇坏点抛 AttributeError/ValueError——`ui.py` 兜底分支（:741）的数据
   源即 `HistoryStore.load()`，随清洗自动安全，无需改动。
2. `notifier.filter_series()`：t/p 非数值的坏点跳过并记日志（缺字段仍按 0
   处理，保持既有「缺字段按 0」语义与既有用例契约）；无时间过滤（"all"）时
   不检查 t，t 坏但 p 好的点保留。
3. `Alert.from_dict()`：`at` 用新增的 `_safe_float()` 转换，非数值按 0.0
   兜底（评审报告建议口径），不再抛 ValueError。
4. `Notifier._ensure_loaded()`：逐条 try/except，坏记录跳过并记日志（作为
   from_dict 之上的第二道防线）。

### 改动文件

| 文件 | 位置 | 改动 |
|---|---|---|
| `gold_pet/price.py` | :15（`import logging`）、:286-301（新增 `_valid_point`）、:314-339（`load()` 重写） | 逐序列逐点清洗 + 剔除计数记日志 |
| `gold_pet/notifier.py` | :35-52（`import logging` + `_safe_float`）、:109（`from_dict.at`）、:396-406（`_ensure_loaded` 逐条容错）、:451-480（`filter_series` 容错） | 坏点/坏记录跳过或按 0 兜底，统一记日志 |
| `tests/test_price.py` | :9（`import json`）、:126-155（新增） | `test_history_load_skips_dirty_points`：非 dict/非数值 t、p/非列表序列逐项剔除，append 不抛异常 |
| `tests/test_notifier.py` | :520-535（新增）、:566-580（新增） | `test_history_skips_items_with_bad_at`（at 坏按 0 兜底不抛）；`test_filter_series_skips_non_numeric_points`（坏点跳过、好点保留） |

## 验证方式与结果

环境：项目自带 `.venv`（统一使用 `.venv\Scripts\python.exe`，系统 `python`
为 Store 占位 stub）。

1. `python -m py_compile`（5 个源文件 + 4 个测试文件，共 9 个改动文件）→ 通过。
2. `pytest tests/ -q`（全量）→ 无 F/E、1 skipped，与 P1 阶段基线一致（新增
   4 个 P2 回归用例）。
3. 进程退出码 127 + 「QThread: Destroyed while thread is still running」
   现象再现——已用 `git stash` 在改动前的基线上复跑对照，退出码与提示
   **完全一致**，确认为既有测试收尾阶段现象（control_server 相关测试的
   QThread 清理，评审报告 P2-02 相关领域），非本次修复引入。多轮复跑中另
   观察到 1 次 `test_unknown_path_404` 的 `httpx.ReadError`（WinError 10053）
   偶发失败，复跑即过、连续 3 轮全量 0 失败；属 control_server 测试收尾竞态
   （与 127 现象同域的既有脆弱点），本次 `/say` 改动仅涉及文本处理，与其
   无关。
4. P2-02 修复本身不影响测试路径（测试不走事件循环，deleteLater 仅入队），
   全量测试行为与基线一致。

## 残留风险

- `/say` 的 500 字符上限为截断而非 400 拒绝，AI 侧长发言会被静默截尾（可
  接受；评审报告两种建议取其一）。
- `render_line_html` 转义后，若未来有调用方想经此函数输出合法富文本（含
  HTML 标记），需要另走 `bubble_say()` 之类已知安全路径——注释已写明。
- P3 各项（含 P3-03 退出阻塞、P3-04 退避语义等）不在本批次范围，未处理。

---

# 无头 CLI（智能体调用面）

> 追加于同日：为项目新增不拉起 GUI 的本机 CLI（`cli.py`），包装 v0.8 AI 控制面，
> 供智能体/脚本调用。不改 GUI/服务行为，不改版本号，零新增依赖。

## 通信方式选择（以实际代码为准）

- 控制面实际端点（`gold_pet/control_server.py`）：`GET /status`；`POST /say /play /pat /tease /move`。
  **没有** price/history/alerts 的 HTTP 端点。
- **未走** `tools/pet_mcp_server.py` 的 MCP stdio 桥：该桥依赖第三方 `mcp`/`httpx`，
  且工具返回人类可读文案（丢失结构化信息，无法支撑 `--json` 输出与 2/3/4 退出码语义）。
- CLI 直接用标准库 `urllib` 调控制服务 HTTP；接入机制与 MCP 桥同源：读同一份发现文件
  `control.json`（%APPDATA%\GoldPet\，port/token/pid），并对 pid 做探活，残留文件不连。
- `price` / `history` / `alerts` 走本地数据文件只读（`last_price.json` / `history.json` /
  `alert_history.json`），复用 `gold_pet.price` / `gold_pet.config` 的同源容错逻辑
  （P2-03 坏点剔除），不写任何文件。`alerts` 未提供 --add/--del：服务无对应端点，
  且直改文件会与运行中的应用内存态竞态。

## 用法

```bash
.venv\Scripts\python.exe cli.py <command> [args] [--json]

# 示例
python cli.py --help                          # 退出 0
python cli.py status --json                   # 全结构化输出
python cli.py say "主人好呀"                   # 人类可读输出
python cli.py say --mood up                   # 台词组随机一句
python cli.py play shy
python cli.py move --dx 100 --dy -40          # 或 --x 10 --y 20
python cli.py price --metal gold --unit cny_g
python cli.py history --limit 30 --metal gold --unit cny_g
python cli.py alerts --limit 5 --kind pct
```

`--json` 放在子命令前后均可；JSON 模式 stdout 只输出一行
`{"ok": true/false, "command": "...", "data": {...} | "error": {"code","message","exit_code"}, "durationMs": 12.3}`，
提示与日志一律走 stderr；stdout/stderr 强制 UTF-8（避免管道下 cp936 乱码）。

## 命令清单

| 命令 | 通道 | 说明 |
|---|---|---|
| `status` | HTTP GET /status + 本地只读 | 运行状态/版本/活跃度/大肥鱼计数 + 最新价 + 配置摘要 |
| `say <text> [--mood M]` | HTTP POST /say | CLI 侧先做与服务端同源校验（去空白 + 500 字截断，P2-01 同源），text/mood 二选一 |
| `play <state>` | HTTP POST /play | 白名单 idle/up/down/pat/shy，CLI 侧先校验 |
| `pat` | HTTP POST /pat | 摸摸头，傲娇值清零 |
| `tease` | HTTP POST /tease | 叫她大肥鱼 |
| `move --dx --dy \| --x --y` | HTTP POST /move | 相对挪动或绝对定位 |
| `price [--metal --unit]` | 本地 last_price.json 只读 | 最新价/涨跌幅/高低/更新时间；控制服务无触发刷新端点 |
| `history [--limit N] [--metal --unit]` | 本地 history.json 只读 | P2-03 同源容错剔除坏点，附 dropped_bad_points 计数 |
| `alerts [--limit N] [--kind pct/target/extreme]` | 本地 alert_history.json 只读 | 只读，不做增删（无端点，避免与运行中应用竞态） |

## 退出码

| 码 | 含义 | 示例 |
|---|---|---|
| 0 | 成功 | 任意命令成功返回 |
| 2 | 用法错误 | 未知命令、缺参数、--limit 传了非整数 |
| 3 | 入参校验失败 | `say "  "`（空文本）、`play loading`（非表演态）、`move` 缺成对参数、--limit < 1 |
| 4 | 运行时失败 | 桌宠未运行/发现文件残留（pid 已死）、服务不可达、token 不匹配、数据文件缺失/损坏 |

## 安全边界

- **锁定 127.0.0.1**：URL 硬编码回环地址，不接受 `--host` 等任何指向其它地址的参数；
  端口与 token 只能来自本机用户目录的发现文件，且 pid 探活剔除残留文件。
- **say 校验同源**：CLI 侧与服务端同样先 `strip()` 再按 500 字截断（常量与
  `control_server._MAX_SAY_CHARS` 对齐并有 fixture 测试锚定）；HTML 转义统一在应用渲染边界
  `bubble.render_line_html` 完成，CLI 消息走应用同一分发链路，不另行转义。
- **无请求透传**：命令→端点映射为固定白名单（上表），不提供任意 HTTP 方法/路径透传。

## 验证方式与结果

1. `--help` / 子命令 help → 退出 0；未知命令 `cli.py bogus` → 退出 2；`say` 空文本 → 退出 3。
2. 服务未运行（残留 control.json、pid 已死）：`status` → 退出 4 + 明确提示
   「桌宠没有在运行……请先启动黄金桌宠：python run.py（或双击 run.bat）」。
3. **夹具真实 HTTP 冒烟**（仿 `tests/test_control_server.py` 的 `build_server` + daemon 线程 +
   发现文件指向随机端口）：真实子进程调用 CLI，逐条核对分发结果——say 普通文本、
   1000 字超长文本（CLI 截断为 500，服务端收到的分发文本长度=500）、`--mood up`、
   play shy、pat、tease、move 相对/绝对、空文本退出 3、`play loading` 退出 3、
   status 全链路、token 改错 → 退出 4。全部通过（临时夹具脚本验证后已删除）。
4. **真实 GUI 冒烟**：`.venv\Scripts\python.exe run.py` 后台拉起桌宠（Windows 本机有显示环境），
   control.json 刷新（新端口/token/pid）后依次真实调用 `--json status` / `say` / `history --limit 3` /
   `price` / 1000 字 `say`（返回 `{"mode":"text","length":500}`）——全部退出 0 且输出可
   `JSON.parse`；人类可读模式输出正常。验证后 `taskkill /F` 清理（优雅关闭对托盘程序无效，
   属既有行为；强制结束后残留的 control.json 因 pid 探活被 CLI 正确判为「未运行」）。
5. 回归：`pytest` 全量 247 passed / 1 skipped（与既有基线一致）；`ruff check cli.py` 通过。

## 改动文件清单

- 新增：`cli.py`（项目根，无头 CLI，唯一新增文件）
- 文档：本文件追加「无头 CLI（智能体调用面）」章节
- 未改动：GUI/服务代码、版本号、CHANGELOG、pyproject.toml（`python cli.py <command>` 直接可用，
  无需打包入口点）

