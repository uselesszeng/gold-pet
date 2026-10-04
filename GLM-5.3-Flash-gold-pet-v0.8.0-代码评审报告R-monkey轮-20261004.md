# gold-pet v0.8.0 — 代码评审报告 R-monkey 轮（2026-10-04）

- 评审对象：HEAD（`7db70bd`）之上未提交源码改动，覆盖 R1+R2 monkey 测试引入的全部修复
- 评审方式：`git diff -U3` 锁定改动面，逐项目对抗式审查，就地最小修复后跑全量回归
- 全量回归：**298 passed / 1 skipped / 1 xfailed**（基线全保留，零回退）；ruff（E/F/W/I/UP/B）**All checks passed**

---

## 一、改动面清单

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `cli.py` | 源码 | `_discover` / `_load_json` 补 `RecursionError`（R2-M-03）；本轮新增 `main()` 非 `CliError` 异常兜底（P2-R-02） |
| `gold_pet/config.py` | 源码 | `_as_float` 补 `OverflowError`（R2-M-01）；`Config.load` 补 `RecursionError`（R2-M-03） |
| `gold_pet/control_server.py` | 源码 | `read_control_info` 补 `RecursionError` + port/token 类型/范围校验（R2-M-06） |
| `gold_pet/notifier.py` | 源码 | `_safe_float` 补 `OverflowError`（R2-M-01）；`_ensure_loaded` 补 `RecursionError`（R2-M-03）；`_save` 改用原子写（R2-M-05）；本轮新增 `_history_lock` 线程锁（P2-R-03） |
| `gold_pet/price.py` | 源码 | `Quote.change` / `change_pct` 有限性收敛（R2-M-04）；`format_price` 归一 `-0.0`（P3）；`_num` / `_valid_point` 补 `OverflowError`（R2-M-01）；`load_cache` 补 `RecursionError`（R2-M-03）；`save_cache` 改用原子写（R2-M-05）；`HistoryStore.append` 加锁 + 原子写（R2-M-05）；`_atomic_write_text` 新增 |
| `gold_pet/ui.py` | 源码 | `_clamp_to_screen` 入口 int32 夹取（R2-M-02）；本轮 `_control_move` 补 `int()` 异常兜底（P1-R-01） |
| `tools/pet_mcp_server.py` | 源码 | `_discover` 补 `RecursionError` + port/token 类型/范围校验（R2-M-06） |
| `tests/test_monkey2_*.py` | 新增测试 | 6 个 R2 monkey 测试套件（固定种子，零新增依赖） |

---

## 二、逐项目发现（编号 / 级别 / 文件:行号 / 根因 / 证据 / 修复建议）

### P1-R-01 `ui.py:_control_move` 的 `int()` 转换可被 HTTP 透传的非数值脏字段崩穿 UI 槽

- **级别**：P1（主流程错误，高危健壮性）
- **文件**：`gold_pet/ui.py:1079-1083`
- **根因**：R2-M-02 修复了 `_clamp_to_screen` 的 `QPoint` 溢出，但 `_control_move` 入口的 `int(action["dx"])` / `int(action["x"])` 未做异常兜底。`/move` 的 action dict 由 `control_server.dispatch()` 经 HTTP 边界注入，`_to_coord` 只保证值本身非 `None`，若某条指令的数值字段是字符串 `"abc"`（如手改 HTTP 请求体 `{"dx": "abc"}`）或对象 `{}`，`int("abc")` 抛 `ValueError`、`int({})` 抛 `TypeError`、`int(10**400)` 抛 `OverflowError`——均在 UI 线程槽内未捕获，traceback 刷屏且窗口不挪（与 R2-M-02 修复点同族残余面）。
- **证据**：直接对 `_to_coord` 喂字符串/对象验证，`_to_coord("abc")` 返回 `None`（安全），但 `_control_move` 收到的 dict 值可能是 `_to_coord` 之前注入的任意类型（action dict 经 `dispatch()` 透传，`_to_coord` 已在服务端做过第一次收敛，若服务端 `_to_coord` 被绕过则直接炸）。
- **修复**（已应用）：`_control_move` 入口的 `int()` 转换包入 `try/except (TypeError, ValueError, OverflowError, KeyError)`，异常时静默忽略本条指令，不崩穿 UI 槽。

### P2-R-02 `cli.py:main()` 非 `CliError` 异常裸 traceback 违约退出码约定

- **级别**：P2（边界 / 中等健壮性）
- **文件**：`cli.py:533-578`
- **根因**：`main()` 是外部智能体直接调用的 API（`python cli.py <cmd>` 或 `cli.main(["cmd"])`），仅捕获 `CliError`。`cmd_price` / `cmd_status` / `cmd_history` / `cmd_alerts` 读本地文件链路中，若 `Snapshot.from_dict` 内某未预期的脏数据形态（如 `quote` 的某字段 `TypeError` 从 `from_dict` 外的 `__annotations__` 遍历路径逃逸）触发非 `CliError` 异常，`main()` 裸 traceback，退出码非 0/2/3/4，违反 CLI 契约。
- **证据**：`cmd_price` 中 `snap.price_of()` 返回的 `Quote` 若某字段为 `None`（`from_dict` 的 `getattr` 默认值路径），`round(quote.change, 4)` 可触发 `TypeError`。
- **修复**（已应用）：`main()` 的 `try/except CliError` 后追加 `except Exception` 兜底，统一返回 `exit_code=4` + 结构化错误消息，不裸 traceback。

### P2-R-03 `notifier.py:_history` 多线程无锁——R2-M-05 只治落盘不治内存

- **级别**：P2（边界 / 中等健壮性）
- **文件**：`gold_pet/notifier.py:197`
- **根因**：R2-M-05 为 `HistoryStore.append` 加了 `threading.Lock`，为 `Notifier._save` 改用原子写，但 `Notifier.record` / `clear_history` / `history` 对 `_history` 的读-改-写（`extend` + 截断）全程无锁。多线程并发 `record` 时，两个线程的 `extend` 可互相覆盖（丢更新），且 join 后「磁盘 == 内存」的 oracle 只是概率成立（测试 `test_monkey2_concurrency.py` 里该断言依赖 GIL + 时序，非确定性保证）。
- **证据**：`Notifier.record` 无锁，并发 `record` + `clear_history` 交错时内存 `_history` 可被 `clear_history` 清空后由另一线程 `extend` 重建，磁盘写出的内容与内存不一致。
- **修复**（已应用）：`Notifier.__init__` 新增 `self._history_lock = threading.Lock()`；`record` / `history` / `clear_history` 的读-改-写段全程持锁，与 `HistoryStore.append`（R2-M-05）同治。生产路径（UI 线程单写）成本可忽略。

### P3-R-01 `cli.py:_load_json` 的 `missing_ok=True` 分支对 `RecursionError` 的 `CliError` 文案不精确

- **级别**：P3（质量）
- **文件**：`cli.py:197-206`
- **根因**：`_load_json` 的 `except (OSError, ValueError, RecursionError)` 分支对 `missing_ok=True` 和 `missing_ok=False` 两种情形都返回同一个 `CliError`（文案「数据文件损坏或不可读」），但 `RecursionError`（2 万层嵌套）与 `OSError`（文件权限）语义不同，下游调用方（`cmd_history`）无法区分「文件不存在」与「文件损坏」与「超深嵌套」。
- **修复建议**：`missing_ok=True` 时 `RecursionError` 与 `FileNotFoundError` 都静默返回 `None`（与现有 `OSError` 容忍路径一致）；`missing_ok=False` 时给 `RecursionError` 单独一条文案。本轮记录，不修（非 P0/P1）。

### P3-R-02 `price.py:_atomic_write_text` 的 `_write_seq` 全局变量在单进程多线程场景下存在轻微竞态

- **级别**：P3（质量）
- **文件**：`gold_pet/price.py:367-394`
- **根因**：`_write_seq` 是模块级全局 int，由 `_write_seq_lock` 保护，但 `threading.get_ident()` 在同一线程内是确定值，若两个线程同时进入 `_atomic_write_text` 且 `pid` 相同，`_write_seq` 的递增是原子的（有锁），不会碰撞。当前实现实际上**没有**竞态（锁保护了递增），此条为「记录已验证无缺陷」的 P3 观察项。
- **修复建议**：无需修复；当前实现正确。

---

## 三、文档一致性结论

| R2 报告声称 | git diff 实际 | 一致性 |
|---|---|---|
| R2-M-01：4 处 `except` 补 `OverflowError` | `config.py` / `price.py` ×2 / `notifier.py` 各 1 处已补 | 一致 |
| R2-M-02：`_clamp_to_screen` int32 夹取 | `ui.py:509` 已夹取 | 一致 |
| R2-M-03：5 处 `except` 补 `RecursionError` | `config.py` / `price.py` ×2 / `notifier.py` / `cli.py` / `control_server.py` 已补 | 一致 |
| R2-M-04：`Quote.change` / `change_pct` 有限性收敛 | `price.py:78-97` 已收敛 | 一致 |
| R2-M-05：`_atomic_write_text` + `HistoryStore` 锁 | `price.py:367-394` 已加；`HistoryStore.__init__` 已加锁 | 一致 |
| R2-M-06：`read_control_info` + MCP `_discover` 校验 | `control_server.py` / `tools/pet_mcp_server.py` 已对齐 | 一致 |
| R2-M-07（P2 遗留，mcp 库域）：xfail 固化 | `tests/test_monkey2_mcp_stdio.py` xfail 已落盘 | 一致 |

**无「文档声称完成但代码未实现」的缺口。**

---

## 四、总体结论

本轮 monkey R2 修复**整体质量高**：6 项修复中 5 项根因定位准确，边界夹取/异常兜底/原子写/线程锁均落在正确位置，零新增依赖，测试固定种子可重复。

**本轮就地修复 3 项**：
- P1-R-01（`_control_move` int 异常兜底）——与 R2-M-02 同族残余面，修复后 HTTP 透传脏字段不再崩穿 UI 槽
- P2-R-02（`cli.py:main` 非 CliError 异常兜底）——退出码约定违约面收窄
- P2-R-03（`Notifier._history` 加锁）——R2-M-05 内存侧缺锁补齐，多线程 `record` 不再丢更新

**遗留 2 项 P3**（记录不修）：`_load_json` 文案区分度（R3-R-01）、`_atomic_write_text` 全局变量观察项（R3-R-02）。

**全量回归 298 passed / 1 skipped / 1 xfailed，零回退。**
