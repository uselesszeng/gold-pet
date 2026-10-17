# gold-pet v0.8.1 Monkey 测试报告 R5（CodeBuddy，2026-10-13 第五轮）

执行人：CodeBuddy（资深测试工程师角色）
项目：gold-pet v0.8.1（本轮修复后）（Python 3.11 + PySide6，容器路径 `/workspace`）
入场基线：`QT_QPA_PLATFORM=offscreen pytest tests` → **396 passed / 2 failed / 10 skipped**
（2 例红为 `test_monkey3_fault_fs.py::test_r3_fs_readonly_target[history|alerts]`，
下文定性为**前轮测试资产的平台假设错误**，非产品缺陷）
最终结果：**449 passed / 0 failed / 10 skipped**，零回退，新增 53 个 R5 用例（14 个测试函数）。
`ruff check`（E/F/W/I/UP/B）全仓 All checks passed。

---

## 0. 与前四轮的差异点

| 维度 | R1 | R2 | R3 | R4 | **R5（本轮）** |
|------|----|----|----|----|----|
| 种子 | 20261002~07 | 20261011~16 | 20261021~27 | （Qoder 轮） | **20261101** |
| 攻击面 | 单请求脏 payload | 修复合规绕过 / 并发 / 状态机 / MCP stdio | 写盘·网络·线程**故障注入** / 中断 / 时钟 | 并发原子写 / 畸形类型域 / 多进程 | **敌对状态文件的「形态」** |
| 关键区别 | 灌**非法值** | 打**修复点** | 注入**副作用故障** | 打**并发时序** | 找**能通过既有校验、却在原生类型运算上致命**的合法形态 |
| oracle | 不崩 + 结构化 | + 资源守恒 | + 半截文件不破坏 | + 丢更新归零 | **刷新/渲染/提醒/历史窗口四条链路零异常逃逸** |

**为什么这是一个新面**：前四轮的所有加固都在「值是否合法」这一层——`_valid_point`
只校验 `float(point["t"])` 能不能成功。但**校验层放行 ≠ 使用层安全**：
`float("1700000000")` 成功，于是字符串被放行，而下游 `now - series[-1]["t"]`
按**原生类型**做减法就地炸掉。R5 专打这条「校验与使用之间的类型鸿沟」。

## 1. 目标清单（1 目标域 / 14 测试函数 / 53 参数化用例）

| # | 子目标 | 测试函数数 | 用例数 |
|---|--------|-----------|--------|
| 1-A | `HistoryStore.append` 的「数值字符串 t」形态 + UI 刷新槽端到端 | 4 | 16 |
| 1-B | `Alert.kind_name` 的不可哈希 kind + 提醒历史窗口端到端 | 3 | 17 |
| 1-C | 超平台 time_t 时间戳（气泡/提醒历史/spark_cutoff/evaluate/filter_series） | 5 | 17 |
| 1-D | 口径锚定（`_valid_point` 既有语义不变 / 安全时间戳上界可格式化） | 2 | 3 |

语料：`HOSTILE_HISTORY_SERIES`（6 种字符串时间戳形态，含带空白、`1e0` 科学计数、
混排序列）、`UNHASHABLE_KINDS`（6 种可 JSON 落盘的不可哈希形态）、
`BAD_ATS`（9 种超界/异型时间戳）。全部固定种子 `20261101`，可重复运行。

## 2. 方法

- **真实窗口而非 mock**：所有端到端用例都构造**真实 `PetWindow`**（offscreen 平台）
  与**真实 `AlertHistoryDialog`**，走 `_render → _update_spark → HistoryStore.append`、
  `dialog._reload → alert.kind_name` 的实际调用链，不用打桩。
- **形态语料而非值语料**：语料不是「更脏的值」，而是「**形态不同但校验等价**」的值
  ——每个用例先断言前置条件（`assert store.load()`，即该形态**必须**通过 `_valid_point`），
  再断言使用层不得炸。前置条件失败会让用例立刻红，保证语料没有退化成普通脏数据。
- **oracle**：四条链路（刷新 / 渲染 / 提醒 / 历史窗口）任何异常都算缺陷；
  落盘恒为严格 JSON（`parse_constant` 钩子拒 NaN/Infinity）；点数恒 ≤ 上限；
  展示层坏时间戳须显式回落占位符 `?`（**不做静默归零**，归零会把 1e308 显示成
  1970 年，比 `?` 更误导）。
- **500 次轰炸**：三个随机长跑（`append` 形态轰炸 / 500 条脏 kind / 500 条超界 at），
  用固定种子随机组合语料，恒不抛。

## 3. 发现清单（4 项，全部修复）

| 编号 | 级别 | 目标 | 根因 | 证据（真实调用链） | 修复 | 验证 |
|------|------|------|------|------------------|------|------|
| **R5-M-01** | **P1** | `price.HistoryStore.append`（`now - series[-1].get("t", 0)`） | `_valid_point` **有意放行**「数值字符串」时间戳（因为它只校验可转 float），`load()` 原样保留字符串；`append` 却拿**原生值**参与减法 → `TypeError: unsupported operand type(s) for -: 'float' and 'str'`，异常从 `ui._update_spark` 逃逸、**击穿整个刷新槽**（价格不更新、走势图不画、后续提醒与渲染全部中断） | 真实 `PetWindow._render()` 实测抛 TypeError：`price.py:477 → ui.py:751 → ui.py:694`；手改 `history.json` 一个 `"t": "1700000000"` 即稳定复现 | `price._num(series[-1].get("t"), 0.0)` 先把上一点时间戳收敛为有限 float 再比较（坏值按 0 = 走 append 分支，与既有容错口径一致）；**不收紧 `_valid_point`**，避免动到既有契约 | 4 例定向 + 端到端 + 500 次轰炸全绿 |
| **R5-M-02** | **P2** | `notifier.Alert.kind_name`（`KIND_NAMES.get(self.kind, self.kind)`） | `alert_history.json` 的 `kind` 若为 list/dict（手改/工具写出），`dict.get` 抛 `TypeError: unhashable type`；调用方是**提醒历史窗口**的列表填充 → 窗口**打开即崩** | 真实 `AlertHistoryDialog(notifier)` 实测崩：`ui.py:1528 → notifier.py:123` | `try: KIND_NAMES.get(kind, str(kind)) / except TypeError: return "提醒"`；纯展示层收敛，`to_dict` 仍存原值（不改数据语义） | 6 例定向 + 端到端 + 500 条轰炸全绿 |
| **R5-M-03** | **P2** | `ui.AlertHistoryDialog._reload`（`time.localtime(alert.at)`） | `Alert.at` 经 `_safe_float` 收敛但**只校验「是有限 float」**，1e308 这类超平台 `time_t` 范围的值被放行；`localtime()` 抛 `OverflowError`（不是 ValueError）→ 提醒历史窗口打开即崩 | 实测 `at=1e308/1e30/-1e30` 三档全部 `OverflowError: timestamp out of range for platform time_t` | 新增 `price.safe_strftime(fmt, ts, fallback="?")`：转 float + 夹取到 `[0, 公元3000年]` 后再格式化，任何异常回落 `?`（与 `cli._fmt_time` 早已采用的口径统一） | 9 例定向 + 端到端 + 500 条轰炸全绿 |
| **R5-M-04** | **P2** | `bubble.render_price_html` / `render_cache_html` 的 `localtime(updated_at)`；附带 `notifier.spark_cutoff("today")`、`Notifier._roll_day`、`evaluate` | 同 R5-M-03 的根因，但受害面更广：`updated_at` 来自 `last_price.json`（手改/损坏可控），气泡渲染是**每 60 秒刷新的主链路**，一崩就不显示价格；`spark_cutoff("today")` 崩则走势图空图；`_roll_day` 崩则水位不重置 | 实测 `render_price_html(updated_at=1e308)`、`render_cache_html(updated_at=-1e30)`、`spark_cutoff("today", 1e308)`、`evaluate(now=1e308)` 全部 `OverflowError` | 五处统一改走 `safe_strftime` / 包 `(OverflowError, OSError, ValueError)`；`spark_cutoff` 取不到当天零点时回落 `None`（= 不限制，走势图照常画） | 17 例定向全绿 |

**级别统计**：P0 = 0；P1 = 1（R5-M-01）；P2 = 3（R5-M-02/03/04）。
**修复数**：4 / 4（全部最小化修复，无架构改动，零新增依赖）。

## 4. 附带修正：入场基线的 2 例红（**前轮测试资产缺陷，非产品缺陷**）

| 项 | 内容 |
|----|------|
| 现象 | `test_r3_fs_readonly_target[history]` / `[alerts]` 稳定 FAILED（同一机器跑 3 次全红） |
| 表面原因 | 断言 `after == before` 做**字节级**比较，而两次写入各自调 `time.time()`，纳秒位必然不同 |
| **真实根因** | 该用例的前提**跨平台不成立**：`chmod S_IREAD` 后，Windows 上 `os.replace` 以 EACCES 失败（写入被吞，文件不变），**POSIX 上 rename(2) 只看目录写权限，目标文件 0444 不阻止覆盖**——实测 `chmod 0o400` 后 replace 照样成功、内容真的被更新、新 inode 权限位回落 0644 |
| 定性 | 前轮测试的**平台假设错误**，CI 跑在 Linux 上因此稳定红；产品行为在两种平台上都是正确的 |
| 修复 | 用例改为按**平台真实语义**断言：共同底线（不得抛异常、无 tmp 残留、文件严格 JSON）+ 分平台分支（Windows 断言原数据保留；POSIX 断言写入生效且文件合法），并去掉纳秒级字节比较 |

## 5. 反向验证（证明 4 处修复非冗余）

`git stash` 暂存 4 个源码文件的修复后重跑 R5 套件：
**53 例中 41 例失败**，且失败分布精确命中四组发现——
`history_append*` 4、`refresh_slot*` 4、`append_burst` 1（R5-M-01）；
`kind_name*` 6、`dialog_survives_unhashable` 6、`unhashable_burst` 1（R5-M-02）；
`dialog_survives_out_of_range_at` 6、`out_of_range_at_burst` 1（R5-M-03）；
`bubble_renders*` 8、`spark_cutoff/evaluate` 4（R5-M-04）。
`git stash pop` 恢复后全绿。

## 6. 统计与验证

- 发现 4 项（P1×1 / P2×3）；修复 4 项；附带修正前轮测试资产 1 项；遗留 0 项。
- 全量回归：**449 passed / 0 failed / 10 skipped**（基线 396 passed / 2 failed，
  零回退且清掉 2 例历史红）。
- 覆盖率：`--cov=gold_pet` **81.07%**（门禁 65%，余量充足）。
- `ruff check`（E/F/W/I/UP/B）全仓 All checks passed。
- 新增测试文件：`tests/test_monkey5_hostile_state.py`（53 例，固定种子可重复）。
- 修改的源文件：`gold_pet/price.py`（R5-M-01 + `safe_strftime`）、
  `gold_pet/notifier.py`（R5-M-02/03 附带）、`gold_pet/bubble.py`、`gold_pet/ui.py`（R5-M-03/04）、
  `tests/test_monkey3_fault_fs.py`（§4 附带修正）。

## 7. 遗留项与建议（非缺陷）

1. **`test_monkey2_concurrency.py::test_r2_control_server_threaded_endpoints` 在机器满载时
   偶发 `ConnectTimeout`**（单独跑必绿；仅在并发跑全量 + 编译等负载下复现）。
   属**测试资产对机器吞吐的敏感性**，非产品缺陷；建议后续给该用例的连接超时留更宽余量
   （与 R4 报告里 `test_monkey_control_server` 240s 预算不足是同族问题）。
2. **`test_monkey3_degraded.py:239` 的 `subprocess.run(..., text=True)` 未指定 `encoding`**
   （R4 报告 MTX-01 已定案、上一轮未授权修改）：CI 环境为 UTF-8 locale，本机未复现，
   但这是 R4 报告点名的「全量 pytest 一条命令不可用」隐患。**属测试资产改动，本轮按纪律
   不擅改**，建议采纳 R4 的 A 案（补 `encoding="utf-8", errors="replace"`）。
3. **`_valid_point` 放行数值字符串是「有意设计」**：本轮修复选择在使用层收敛而非收紧校验，
   因此 `history.json` 里仍可能存着字符串形态的点（读时被透明转换）。若希望状态文件
   规范化，可在 `load()` 时回写归一化，但那属**写放大**，不与本轮最小修复混做。
4. **`ruff format --check` 全仓有 38 个文件需重排**（price / notifier / ui 等在**改动前**
   即如此），`ruff check` 不查格式，故 CI 目前不拦。本轮新增代码本身已 format 干净
   （已逐文件核验：我的改动段落不在 format 建议里）；全仓重排建议单独开一笔纯格式提交，
   避免与逻辑修复混在同一 diff 里难以评审。
