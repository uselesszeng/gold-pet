# 修复记录（gold-pet v0.8.0 / R5）

- **执行模型**：Hy4（CodeBuddy）｜**调度 Issue**：#19
- **修复分支**：`auto/r5-fixes-a1ee`（从 `origin/main` @ `e660d9d` 新开）
- **修复提交**：`8297bd0`
- **PR**：[#26](https://cnb.cool/TJ-coding/gold-pet/-/pulls/26)
- **关闭的 Issue**：#20 #21 #22 #23 #24

---

## 修复前 / 修复后（门禁读数）

| 门禁 | 修复前 | 修复后 |
|---|---|---|
| `ruff check .` | 绿 | 绿 |
| `ruff format --check .` | 🔴 38 files would be reformatted | 🟢 80 files already formatted |
| `mypy` | 🔴 1 error（`price.py:241`） | 🟢 Success: no issues found |
| `pytest tests` | 🔴 2 failed, 463 passed | 🟢 **489 passed, 11 skipped, 1 xfailed** |
| 覆盖率 | 81.09% | **81.67%**（门禁 65%） |

---

## 逐条修复明细

### Issue #20（P1）配置 / 发现文件落盘原子化

**改动**
- `gold_pet/config.py:216` `Config.save()`：裸 `Path(path).write_text(...)`
  → `_atomic_write_text(Path(path), ...)`，并从 `.price` 导入。
- `gold_pet/control_server.py:323` `ControlServer._write_info()`：同源改造。

**为什么这么改**：本仓已有 `_atomic_write_text`（tmp 唯一名 + `os.replace`），
四个状态写入器里只剩这两个是裸写。补上即达成同源口径，零新依赖、零新机制。

**效果实测**
```
并发读 settings.json 300 轮：坏读 292 次 → 0 次
故障注入（os.replace 抛 EACCES）：原文件字节完全不变、异常被吞、无 .tmp 残留
```

### Issue #21（P2）极端时间戳容错

**改动**：`gold_pet/notifier.py:222` `_roll_day()` 把
`time.strftime("%Y-%m-%d", time.localtime(now))` 包进
`try/except (OSError, ValueError, OverflowError)`；失败时 `logger.debug` 留痕并
**退回「不跨日」语义**（保留当前 `_day`、不清水位）。

**降级方向的取舍**：宁可「少重置一次水位」也不要「崩穿刷新链路」。
水位每 24h 必然重置一次，漏一次只影响「跨日后的第一次新高可能不播报」；
而异常逃逸会直接打断 UI 刷新。

**效果实测**：`evaluate(now=1e18)` 从 `OSError` 逃逸 → 正常返回列表，且 `_day`/水位不被误改。

### Issue #22（P1）测试门禁：只读用例的跨平台前提

**改动**：`tests/test_monkey3_fault_fs.py`

- `test_r3_fs_readonly_target`：故障注入从 `os.chmod(S_IREAD)` 改为
  **`os.replace` 抛 `PermissionError`**（settings 走直写路径，则拦目标 `write_text`）。
  断言不变：写入被吞、原内容字节不变、无 `.tmp` 残留。
- 新增孪生用例 `test_r3_fs_readonly_target_windows_semantics`，
  保留原 `chmod` 语义，`os.name != "nt"` 时 `pytest.skip`。

**为什么不是简单加 skipif**：加 skip 等于在 Linux 上**删掉这一条覆盖面**；
换成 `os.replace` 注入后，两个平台都真实覆盖「目标不可写」分支，
且原 Windows 语义仍有专属用例守着。

**效果实测**：全量 `pytest` 从 `2 failed` → `0 failed`。

### Issue #23（P1）CI 门禁：mypy 必红 + format 漂移

**改动**

1. `gold_pet/price.py:238` `_num()`：函数签名按单行格式整理；
   函数体内引入 `raw: Any = value`，把 `float(value)` 改为 `float(raw)`。
   **运行期行为零变化** —— `tests/test_r5_fixes.py` 用 10 条参数化用例逐值锚定
   （含 `None`/`True`/`NaN`/`Inf`/`10**400`/字符串/列表/字典）。
2. `requirements-dev.txt`：`ruff>=0.6` → `ruff==0.17.1`（钉死版本，止住格式化漂移）。
3. 全仓 `ruff format .` 一次性收口（38 文件，**纯格式，无逻辑改动**）。

**为什么钉版本而不是调到最松**：Issue #15 已记录「本地绿、CI 红」正源于版本漂移。
浮动版本 = 每次装依赖都可能换格式化结果，门禁就永远是薛定谔的。

### Issue #24（P3）`Alert.from_dict` 类型守卫

**改动**：`gold_pet/notifier.py` 的 `Alert.from_dict()` 顶部加

```python
if not isinstance(data, dict):
    raise TypeError(f"Alert.from_dict 需要 dict，收到 {type(data).__name__}")
```

**口径依据**：`AttributeError` 不在本仓任何 `except` 白名单里
（常见口径是 `TypeError/ValueError/OSError/RecursionError`），而
`Snapshot.from_dict`、`Config` 装载路径都做了类型防御 —— 这里补上即口径统一。

**兼容性**：`Notifier._ensure_loaded` 的既有过滤（`isinstance(item, dict)` 跳过坏记录 + warning）
行为不变，已由 `test_r5_alert_from_dict_dict_still_tolerant` 锚定。

---

## 未修复项（如实声明）

### Issue #25（P2）跨进程写 `history.json` 丢更新 —— **未修，待决策**

```
4 进程 × 各 append 200 次  →  期望 800 点，实际 1 点
```

**为什么不修**：修法有 A（跨平台文件锁，需自写 `msvcrt`/`fcntl` 薄封装或引入新依赖）、
B（单写者仲裁）、C（文档化接受丢点）三条路线，属**架构决策**。
无人值守纪律下不擅自引入新依赖或改动架构，只补齐量化证据（已在 #25 评论留存）。

本轮已为该问题预置**资源守恒回归锚**（`test_monkey5_soak.py` 的体积/FD 断言），
将来加锁时若引入泄漏会立刻暴露。

---

## 新增测试资产

| 文件 | 条数 | 角度 |
|---|---|---|
| `tests/test_monkey5_clock.py` | 18 | 时间语义（跨日 / 极端时间戳 / 时区 / 勿扰开闭口径） |
| `tests/test_monkey5_roundtrip.py` | 39 | 配置往返收敛（sanitize 幂等 / 值域契约 / 未知键守恒） |
| `tests/test_monkey5_soak.py` | 9 | 长跑稳态（RSS / FD / 线程 / 磁盘体积有界性） |
| `tests/test_r5_fixes.py` | 32 | 本轮 5 条修复的回归锚 |
| **合计** | **98** | |

---

## 回退方式

```bash
git checkout main
git revert 8297bd0        # 单提交，可整笔回退
```

或丢弃分支：`git push origin --delete auto/r5-fixes-a1ee`（PR #26 一并关闭）。

---
_本文件由 @codebuddy 自动生成。所有读数均在本轮沙箱内实测，命令见正文。_
