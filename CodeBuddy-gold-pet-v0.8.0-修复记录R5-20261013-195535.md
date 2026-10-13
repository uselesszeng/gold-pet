# gold-pet v0.8.0 修复记录 R5（CodeBuddy，2026-10-13）

分支 `codebuddy/monkey-r5-review-r5`，基线 `e660d9d`（main）。
本文档记录第 5 轮（Monkey 段 + 评审段）的**全部源码改动**，每项含「问题回顾 / 修复方案 /
改动文件 / 验证方式 / 残留风险」。

---

# 一、Monkey 段修复（4 项）

## 1.1 R5-M-01（**P1**）`HistoryStore.append` 被「数值字符串」时间戳击穿，刷新链整体死掉

### 问题回顾

`_valid_point` **有意放行**「数值字符串」形态的点（它只校验 `float(point["t"])` 能否成功，
用于兼容下游渲染），因此 `load()` 会把 `{"t": "1700000000", "p": 900.5}` 原样读进内存。
但 `append()` 里：

```python
if series and now - series[-1].get("t", 0) < 60:     # ← 原生值参与减法
```

`now` 是 float、`series[-1]["t"]` 是 str → `TypeError: unsupported operand type(s) for -`。
异常从 `ui._update_spark` **逃逸出刷新槽**，用户看到的是价格不更新、走势图不动、
后续提醒与渲染整条链中断（而不是一个可恢复的局部失败）。

真实调用链（实测栈）：
```
ui.py:694  _render
ui.py:751  _update_spark
price.py:477 HistoryStore.append  →  TypeError
```

### 修复方案

用既有的 `_num()`（任意脏值 → 有限 float）先把「上一点时间戳」收敛后再比较。
坏值回落 0.0，语义等价于「上一分钟之外」，走 `append` 分支——与 `_valid_point`
对坏点的既有容错口径一致。

**刻意不收紧 `_valid_point`**：它的宽松是 9 个既有用例锚定的契约（渲染层依赖
「字符串也能当点用」），本轮只修使用层，避免动到别处。

### 改动文件

`gold_pet/price.py` · `HistoryStore.append`

### 验证方式

- 6 种字符串时间戳形态（纯数字 / 小数 / 带空白 / 科学计数 / 混排序列 / 极端值）×
  「append 不抛 + 落盘严格 JSON + 点数有界」
- **端到端**：真实 `PetWindow._render()` 走完整刷新槽（修复前实测抛 TypeError）
- 500 次随机形态轰炸恒不抛
- 反向验证：回退修复后该组 9 例转红

### 残留风险

无。字符串点在读时被透明转换，文件里仍可能存在该形态（属既有设计，未改）。

---

## 1.2 R5-M-02（P2）`Alert.kind_name` 遇不可哈希 `kind` 抛错，提醒历史窗口打开即崩

### 问题回顾

```python
return KIND_NAMES.get(self.kind, self.kind)
```

`kind` 来自手改/损坏的 `alert_history.json`，可能是 list/dict → `dict.get` 抛
`TypeError: unhashable type`。调用方是「提醒历史」窗口的列表填充
（`ui.py:1528`），因此用户右键 → 提醒历史 → **窗口直接崩**。

### 修复方案

`try/except TypeError` 包住 `KIND_NAMES.get(...)`，不可哈希时回落固定占位文案「提醒」。
**纯展示层收敛**：`to_dict` 仍存原值，不改数据语义。

### 改动文件

`gold_pet/notifier.py` · `Alert.kind_name`

### 验证方式

6 种可 JSON 落盘的不可哈希形态（list/dict/嵌套/多元素）×
「`kind_name` 不抛 + 端到端 `AlertHistoryDialog` 构建成功」+ 500 条随机脏 kind 轰炸。
反向验证：回退后该组 13 例转红。

---

## 1.3 R5-M-03（P2）提醒历史窗口被超平台 `time_t` 的时间戳打崩

### 问题回顾

`Alert.at` 经 `_safe_float` 收敛，但 `_safe_float` **只校验「是有限 float」**——
`1e308` 这种远超平台 `time_t` 范围的值会被放行。而 `time.localtime()` 对它抛的是
`OverflowError`（**不是 ValueError**），既有 `except` 元组全部接不住。

实测 `at=1e308 / 1e30 / -1e30` 三档全部 `OverflowError: timestamp out of range for platform time_t`。

### 修复方案

新增 `price.safe_strftime(fmt, ts, fallback="?")`：

1. 转 float（接 `TypeError/ValueError/OverflowError`）；
2. 有限性 + 量级检查：不在 `[0, 公元3000年]`（`32503680000.0`，与 `_num` 对 `updated_at`
   的既有上界同口径）内 → fallback；
3. `strftime(localtime(...))` 再包一层异常兜底（32 位 `time_t` 平台夹取后仍可能越界）。

**刻意不做静默归零**：把 1e308 归零会显示成 1970 年，比显示 `?` 更误导用户。
口径与 `cli._fmt_time`（早已这么做）统一。

### 改动文件

`gold_pet/price.py`（新增 `safe_strftime`）、`gold_pet/ui.py` · `AlertHistoryDialog._reload`

### 验证方式

9 种异常 `at`（1e308/-1e308/1e30/-1e30/2^63/-2^63/字符串/None/容器）×
「窗口构建成功 + 条数正确」+ 500 条轰炸 + 上界可格式化锚定。
反向验证：回退后该组 7 例转红。

---

## 1.4 R5-M-04（P2）气泡渲染与走势范围计算被超范围时间戳打崩（同族，受害面更广）

### 问题回顾

同 R5-M-03 的根因，但影响的是**用户每次刷新都走的主链路**，且数据源是
`last_price.json`（手改/损坏可控）：

| 位置 | 后果 |
|------|------|
| `bubble.render_price_html` | 气泡不显示价格（60 秒一次的主渲染） |
| `bubble.render_cache_html` | 离线时的缓存提示崩 |
| `notifier.spark_cutoff("today")` | 走势图空图 |
| `Notifier._roll_day` | 跨日水位不重置 |
| `Notifier.evaluate` | 提醒判定整条链崩 |

实测 `render_price_html(updated_at=1e308)`、`render_cache_html(updated_at=-1e30)`、
`spark_cutoff("today", 1e308)`、`evaluate(now=1e308)` 全部 `OverflowError`。

### 修复方案

五处统一改走 `safe_strftime` / 包 `(OverflowError, OSError, ValueError)`。
`spark_cutoff("today")` 取不到当天零点时回落 `None`（= 不限制），走势图照常画，
不因时间源异常变空图。

### 改动文件

`gold_pet/bubble.py`（两处）、`gold_pet/notifier.py`（`spark_cutoff` / `_roll_day`）

### 验证方式

4 类超范围值 × 2 个渲染函数 + `spark_cutoff`/`evaluate` × 4 档 + `filter_series` 混合脏点，
共 17 例。反向验证：回退后该组 12 例转红。

---

## 1.5 附带（**前轮测试资产缺陷，非产品缺陷**）`test_r3_fs_readonly_target` 的平台假设错误

### 问题回顾

该用例稳定 FAILED（本仓入场基线的 2 例红）。表面的原因是「`after == before` 做字节级
比较，而两次写入各自 `time.time()`，纳秒位必然不同」。

但**真实根因是前提跨平台不成立**：

- **Windows**：目标文件带只读属性时 `os.replace` 以 EACCES 失败，写入被吞、文件不变
  —— 用例的 `assert after == before` 正是为这条路径写的；
- **POSIX**：`rename(2)` 只看**目录**写权限，目标文件 0444 **不阻止覆盖**。
  实测 `chmod 0o400` 后 `os.replace` 照样成功、内容真的被更新、新 inode 权限位回落 0644。

CI 跑在 Linux 上，所以这两例稳定红。

### 修复方案

用例改为按**平台真实语义**断言：
- 跨平台共同底线：写入不得抛异常、无 `.tmp` 残留、文件仍是严格合法 JSON；
- Windows 分支：断言原数据保留（写失败路径不得破坏文件）；
- POSIX 分支：断言写入正常生效（这是文件系统语义，不是缺陷）；
- 去掉纳秒级字节比较。

### 改动文件

`tests/test_monkey3_fault_fs.py`

### 验证方式

三种模式（`-q` 单跑 / 全量并发）连跑多次全绿；并使全量基线从
`396 passed / 2 failed` 变为 `398 passed / 0 failed`。

---

# 二、评审段修复（5 项）

## 2.1 REVIEW5-01（**P1**）受损 `settings.json` 被静默整份归零 → 后续 save 覆写 → 永久丢失

### 问题回顾

`Config.load` 把三种态挤进同一条 `except` 返回 `{}`：

```python
try:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
except (OSError, ValueError, RecursionError):
    data = {}
```

**无告警、无留档**。完整的数据丢失链：

1. 用户手改坏一个字符（实测 `{"metal":"silver",...}` 少一个花括号）；
2. `load` 静默返回全默认，用户自己的配置在内存里已经没了；
3. 程序退出（或用户改任意设置）触发 `save()` → **用默认值覆写原文件**；
4. 磁盘上再无任何现场 → **永久丢失且无法追回**。

### 修复方案

`load` 三态拆分：

| 态 | 处理 |
|----|------|
| 文件不存在 | 静默走默认（首次启动，正常） |
| 读取失败（OSError） | `logger.warning` + 走默认 |
| 解析失败 / 非 UTF-8 / 顶层非对象 | `logger.warning` + **原始字节**留档到 `<文件>.corrupt` + 走默认 |

留档细节：**已存在不覆盖**（保留最早现场，避免反复重启用不同坏内容轮换覆盖而看不清首因）；
按**字节**写（非 UTF-8 的坏文件也能原样保存）；写失败只记日志不扩散异常，
保住「坏配置也能起默认态」的底线。

同时把「先按字节读、再解码」拆开——`UnicodeDecodeError` 也是 `ValueError` 子类，
旧代码靠在 `except ValueError` 里顺带接住，拆开后必须显式同口径处理。

### 改动文件

`gold_pet/config.py` · `Config.load`（+ 新增 `_backup_corrupt`）

### 验证方式

- 6 种受损形态（空文件 / 非 UTF-8 / 顶层数组 / `null` / 语法错 / 2 万层嵌套）：
  一律不崩、一律留档、留档字节与原文件逐位相等；
- 「最早现场不被覆盖」；
- 「文件不存在不产生 `.corrupt` 噪音」；
- 完整用户旅程：坏文件 → 起得来 → 原配置可从留档追回（含「留档内容修一个字符即可复原」）；
- 反向验证：回退后该组 8 例转红。

### 残留风险

留档文件会占用一份配置大小的磁盘（正常几十字节~几 KB）；不自动清理，
需要用户人工处理，属有意设计（自动清理就失去了「找回」的意义）。

---

## 2.2 REVIEW5-02（P2）布尔键语义反转：字符串 `"false"` / `"0"` 被当成 `True`

### 问题回顾

```python
d[key] = bool(d.get(key, DEFAULTS[key]))
```

非空字符串恒为 True。实测 `{"control_enabled":"false","show_bubble":"false",
"dnd_enabled":"false","always_on_top":"0"}` → **四个键全部变成 `True`**。
用户勾选「关闭」后重启，功能自己又开了；`"dnd_enabled":"false"` 反而让勿扰生效。
JSON 里把布尔写成字符串是常见情形（手改、别的工具写出、前端序列化）。

### 修复方案

新增 `_as_bool(value, default)`：

| 输入 | 结果 |
|------|------|
| `bool` | 原样 |
| `int` | `0 → False`，非 0 → `True`（**锚定既有语义**，不破坏既有断言） |
| `float` | 非有限值 → 默认；否则按是否为 0 |
| `str` | 去空白 + 小写后查 `{"1","true","yes","on"}` → True；`{"0","false","no","off"}` → False；**其它未知字符串 → 默认**（不猜） |
| `None` / 容器 / 其它 | 默认 |

主 `bool_keys`（10 个键）与三个 `care_*_enabled` 统一口径。

### 改动文件

`gold_pet/config.py` · `sanitize`（+ 新增 `_as_bool`、`_TRUE_STRINGS` / `_FALSE_STRINGS`）

### 验证方式

7 种假写法 + 6 种真写法 + 既有语义锚定（int 0/1、bool、未知字符串 `"maybe"`、空串、
`None`、容器、NaN/Inf 回落默认）+ 关怀键同族。反向验证：回退后该组 9 例转红。

### 残留风险

无。未知字符串回落**默认**而非 `False`——这是有意的：猜错方向会把「本来是开的」
悄悄关掉，回落默认至少与用户没写过该键的行为一致。

---

## 2.3 REVIEW5-03（P2）`Config.save` / `_write_info` 直写，失败留半截文件并与 2.1 构成连锁

### 问题回顾

四个状态写入器里只有这两个是**直写**（truncate → write）。写入中途失败
（ENOSPC / 进程被杀 / 杀软）会在磁盘上留下**半截 JSON**：

- `Config.save` 的半截文件 → 下次启动 `load` 读不动 → **正好触发 REVIEW5-01 的整份归零**；
- `control_server._write_info` 的半截 `control.json` → CLI / MCP 桥会把**正在运行**的
  桌宠误报成「接入信息损坏」。

即「一次写入失败 = 用户配置永久丢失」的完整因果链。

实测注入 ENOSPC（只写前半 + 抛错）后文件停在 `"activity` 处，
`json.loads` 报 `Unterminated string starting at: line 17`。

### 修复方案

两者统一改走与 `save_cache` / `HistoryStore.append` / `Notifier._save` 同一套
`_atomic_write_text`（唯一 tmp 名 + `os.replace`）。读者要么看到旧内容、要么看到
完整新内容，**不存在半截中间态**；失败路径由该函数自己清理 tmp。

### 改动文件

`gold_pet/config.py` · `Config.save`、`gold_pet/control_server.py` · `_write_info`

### 验证方式

- 注入 `.tmp` 写失败（**只让临时文件写失败**，目标文件绝不被截断）→ 原文件字节逐位不变、
  仍是合法 JSON、无 tmp 残留、`save` 不抛；
- 静态口径断言：四个写入器全部引用 `_atomic_write_text`；
- 反向验证：回退后 2 例转红。

### 残留风险

无。**注意**：本项修复的是「写失败留下坏文件」的连锁，**不解决跨进程丢更新**
（那是另一个问题，见 §4）。

---

## 2.4 REVIEW5-04（P3）CLI 与服务端常量两处手抄

### 问题回顾

`cli.py:62-64` 自己写了一份 `MAX_SAY_CHARS` / `PLAYABLE_STATES` / `ALERT_KINDS`，
并附注释「服务端改动时必须同步这两处」——**注释约等于没有约束**。漏同步会静默
产生两端语义不一致（CLI 放行 600 字而服务端截断 500；CLI 放行某动作而服务端回 400），
集成方（AI 宿主 / MCP 桥 / 脚本）看到的两端行为会不一样。

### 修复方案

新增 `gold_pet/protocol.py` 作为**控制协议常量的唯一事实来源**，`cli.py` 与
`gold_pet/control_server.py` 都从它引用**同一对象**。

为什么不直接让 cli 从 `control_server` 导入：后者顶层 `from PySide6.QtCore import ...`，
而 CLI 的设计前提是**零 GUI 依赖**（能在纯命令行 / CI 跑）。把常量下沉到零依赖模块
是唯一同时满足「消除重复」和「保住 CLI 无 GUI 依赖」的做法。

`control_server` 保留 `_MAX_SAY_CHARS` / `_MAX_BODY_BYTES` 作为指向 protocol 的别名，
不破坏既有测试与内部引用。

### 改动文件

`gold_pet/protocol.py`（新增）、`cli.py`、`gold_pet/control_server.py`

### 验证方式

- 值相等 + **对象同一性**（`is`，抄一份做不到）+ 属性存在性；
- `cli.py --help` 在无 GUI 环境端到端正常，退出码约定（0/2/3/4）不变；
- 反向验证：回退后 1 例转红。

### 残留风险

无。

---

## 2.5 REVIEW5-05（P2，门禁有效性）CI 四条硬门禁里两条长期空转、一条结果漂移

### 问题回顾

| 事实 | 证据（改动前的基线 `e660d9d`） |
|------|-------------------------------|
| `ruff format --check .` 从未通过 | **38 个文件**不满足 formatter 风格；仓库历史只跑 `ruff check`（不查排版）→ 门禁纯空转 |
| `mypy` 必红 | `price.py:241`：`_num` 契约是「接受任意脏值 `object`」，而 `float()` 静态签名只接受 `SupportsFloat` 等 → 1 个真 error |
| 门禁结果随装包时刻漂移 | `ruff>=0.6` 未固定：**ruff 0.6.9** 下 `check` 报 `UP038`（`control_server.py:63`）；**ruff 0.17** 下该规则已被移除，但 `format --check` 又报 38 个文件 → 同一份代码，门禁结论取决于装包时间 |

### 修复方案

1. `_num` 显式标注 `# type: ignore[arg-type]`（`pyproject.toml` 的
   `warn_unused_ignores=false` 正是为这类必要 ignore 留的位置）。
   **不加宽签名**：`object` 是「接受任意脏值」契约的体现，改签名等于削弱契约。
2. `requirements-dev.txt` 固定 `ruff==0.6.9`（附原因注释），并修掉 0.6.9 报出的唯一 `UP038`
   （`isinstance(x, (int, float))` → `isinstance(x, int | float)`）。
3. 用固定版本执行一次**纯格式重排**（38 个文件），**单独一笔提交**，避免淹没逻辑修复的 diff。

### 改动文件

`gold_pet/price.py`、`requirements-dev.txt`、`pyproject.toml`、
`gold_pet/control_server.py`（UP038）、38 个 `.py` 的纯格式重排

### 验证方式

- 四条门禁**首次本地全绿**：`ruff check` PASS、`ruff format --check` PASS
  （61 files already formatted）、`mypy` PASS（no issues found）、
  `pytest --cov` PASS（81.38% ≥ 65%）。
- **纯格式提交的安全性证明**（避免「顺手改逻辑」的嫌疑）：逐文件比对 HEAD 版与工作区版的
  **AST**，被 format 改动的 `.py` **全部 AST 完全相同**，零逻辑变化。
- 全量回归：478 passed / 0 failed。

### 残留风险

`ruff==0.6.9` 是较旧版本（0.17 已移除若干规则）。固定版本换来了**可复现**，代价是
新规则红利暂时吃不到。升级 ruff 时应**同时**重跑 `format` 并复核 diff（本项的存在
本身就说明「不固定版本」的代价更大）。

---

# 三、改动文件清单（汇总）

| 文件 | 关联项 |
|------|--------|
| `gold_pet/price.py` | R5-M-01（`append`）、R5-M-03（`safe_strftime`）、REVIEW5-05（type ignore） |
| `gold_pet/notifier.py` | R5-M-02（`kind_name`）、R5-M-04（`spark_cutoff` / `_roll_day`） |
| `gold_pet/bubble.py` | R5-M-04（两处渲染） |
| `gold_pet/ui.py` | R5-M-03（提醒历史窗口） |
| `gold_pet/config.py` | REVIEW5-01 / 02 / 03 |
| `gold_pet/control_server.py` | REVIEW5-03、REVIEW5-04、REVIEW5-05（UP038） |
| `gold_pet/protocol.py`（新增） | REVIEW5-04 |
| `cli.py` | REVIEW5-04 |
| `requirements-dev.txt` / `pyproject.toml` | REVIEW5-05 |
| `tests/test_monkey5_hostile_state.py`（新增，53 例） | Monkey 段全部发现 |
| `tests/test_review5_goldpet.py`（新增，29 例） | 评审段全部发现 |
| `tests/test_monkey3_fault_fs.py` | §1.5 附带修正 |
| 38 个 `.py` | REVIEW5-05 纯格式重排（AST 等价） |

# 四、未修项（附理由）

1. **跨进程 `history.json` 丢更新**（P2）：实测 6 进程 × 25 点 → 期望 150 实际 44（丢 106）。
   原子写保证「不写坏」，不保证「不丢更新」。需产品决策：A. 独占文件锁（**新依赖，须授权**）；
   B. 单写者仲裁；C. 接受并文档化。本轮不改。
2. **`test_monkey3_degraded.py:239` 缺 `encoding`**（R4 定案的 MTX-01）：属**测试资产**改动，
   R4 的 A 案仍未获授权，按纪律不擅改。
3. **`test_monkey2_concurrency.py` 满载偶发 ConnectTimeout**：测试资产对机器吞吐敏感。

# 五、验证汇总

- 反向验证：Monkey 段回退源码后 **41/53 转红**；评审段回退源码后 **20/29 转红**。
- 全量回归：**478 passed / 0 failed / 10 skipped**（基线 396 passed / 2 failed，零回退）。
- 覆盖率：**81.38%**（门禁 65%）。
- CI 四条门禁：本地全绿（首次）。
- 回退方式：`git revert` 本文档所在分支的三笔提交即可（`test(R5)` / `fix(R5-R-01/R-02)` / `style(R5)` / 评审段提交）。
