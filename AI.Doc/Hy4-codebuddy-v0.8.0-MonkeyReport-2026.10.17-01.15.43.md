# 高强度 Monkey 测试报告 R5（gold-pet v0.8.0）

- **执行模型**：Hy4（CodeBuddy）｜**角色**：@codebuddy
- **调度 Issue**：#19（无人值守任务，第 1 轮 / R5）
- **基线提交**：`e660d9d`（`origin/main`，工作树 clean）
- **执行时间**：2026-10-17 01:15（沙箱时钟）
- **环境**：Python 3.11.2 / PySide6 6.12.0 / `QT_QPA_PLATFORM=offscreen` / `mcp==1.30.0`
- **种子**：20261103 / 20261104 / 20261105（区别于 R1 002~007、R2 011~016、R3 021~029）
- **新增套件**：`tests/test_monkey5_clock.py`（18）、`tests/test_monkey5_roundtrip.py`（39）、`tests/test_monkey5_soak.py`（9）＝ **66 条**
- **全量结果**：`2 failed, 465 passed, 7 skipped, 1 xfailed in 50.33s`（2 failed 为**前轮遗留测试资产缺陷**，见 M5-01）

---

## 一、本轮换的角度（为什么不是重复劳动）

前四轮已覆盖：脏数据灌入（R1/R2）、并发与原子性（R2）、故障注入（R3）、配置装载三态与写盘锁（R4）。
本轮挑的三个**没人碰过**的维度：

| 套件 | 角度 | 为什么危险 |
|---|---|---|
| `test_monkey5_clock.py` | **时间当作输入变量**：跨日、极端时间戳、时区、DST、勿扰开闭口径 | 桌宠是常驻程序，「跨日」每天必然发生一次；时间戳来自缓存/历史文件，可被手改 |
| `test_monkey5_roundtrip.py` | **配置往返收敛**：sanitize 幂等性、load(save(x)) 不动点、值域契约 | 前四轮只测「读侧容错」，没验证「规范化后再存再读是否漂移」——配置系统典型慢性病 |
| `test_monkey5_soak.py` | **长跑稳态**：RSS / FD / 线程 / 磁盘体积随时间是否有界 | 短时高频测试查不出线性泄漏；桌宠开机挂一整天 |

---

## 二、本轮发现（Monkey 段）

### 🔴 M5-01（P1，必现）前轮测试资产缺陷：`test_r3_fs_readonly_target` 的只读前提在 Linux 上不成立

- **现象**：`test_r3_fs_readonly_target[history]` 与 `[alerts]` 两条**全量必红**（`2 failed`）。
- **根因**（已实锤，非猜测）：
  - 用例把「只读」实现为 `os.chmod(target, stat.S_IREAD)`，然后断言「写入器必须吞掉失败、原内容不变」。
  - 但 `_atomic_write_text` 走的是 `tmp.write_text()` + `os.replace(tmp, target)`。
    **POSIX 的 `rename(2)` 只要求「所在目录」可写，不检查目标文件自身的权限位**，
    所以只读的 `history.json` 被原子替换成了新内容 —— 磁盘内容真的变了，断言必失败。
  - Windows 上该用例成立（`MoveFileEx` 对只读目标返回 `ERROR_ACCESS_DENIED`），
    但 CI 与本地开发都在 Linux → **这条断言在 Linux 上永远红**。
  - 另：`[alerts]` 那条约等于「同一个 Alert 写了两次，第二次不该落盘」——
    但 `Notifier.record()` 的语义就是**追加**，第二次追加后长度 2 是正确行为。
- **定性**：**测试资产缺陷 + 门禁可用性问题**，不是产品源码缺陷。但它让「一条命令跑整仓」永远拿不到绿证——
  这正是 Issue #10 里维护者仍在等的「MTX-01 绿证」的**另一半**（前一半是 `text=True` 编码问题）。
- **修法**（本轮已实施，见修复记录）：把「只读」改成在 Linux 上也真的成立的故障注入——
  monkeypatch `os.replace` 抛 `PermissionError`（等价于 Windows 的占用/只读表现），
  断言「写入器吞掉异常、原内容字节完全不变、无 `.tmp` 残留」；并给 `[alerts]` 明确「追加语义」的期望值。
- **复现概率**：100%（Linux 全量必红）｜**优先级**：P1（门禁长期红）

### 🔴 M5-02（P2，条件必现）`Notifier._roll_day` 在极端时间戳上抛 `OSError` 逃逸

- **现象**：`evaluate(..., now=1e18)` → `OSError: [Errno 75] Value too large for defined data type` 直接炸出 `evaluate`。
- **根因**：`gold_pet/notifier.py:224`
  ```python
  day = time.strftime("%Y-%m-%d", time.localtime(now))
  ```
  `time.localtime()` 对超出 `time_t` 可表示范围的浮点抛 `OSError`（不是 `ValueError`/`OverflowError`），
  而 `evaluate()` 只在最外层 `main` 有兜底 except —— 炸穿的是**刷新链路**。
- **触发场景**：`now` 参数是公开可注入的（单测、外部脚本、以及任何「时钟被系统调成极端值」的场合）；
  调用链上 `cli._fmt_time` 已经为同类问题捕获了 `OSError`，说明这一族问题在别处已被认知，这里是漏网。
- **修法**：`_roll_day` 内把 `time.localtime/strftime` 包进 `try/except (OSError, ValueError, OverflowError)`，
  失败时退回「不跨日」语义（保留当前 `_day`），并 debug 级记日志。
- **复现概率**：注入 `now=1e18` 时 100%（真实默认时钟不会；属**鲁棒性缺陷**而非日常必现）｜**优先级**：P2

### 🟡 M5-03（P3，记录）`Alert.from_dict` 对非 dict 输入抛 `AttributeError`（性质反转的隐患）

- **现象**：`Alert.from_dict(None / [] / "str" / 42)` → `AttributeError: 'NoneType' object has no attribute 'get'`。
- **为什么现在不致命**：`Notifier._ensure_loaded` 在调用前有 `isinstance(item, dict)` 过滤，
  所以产品路径炸不到；但**工厂函数的契约与同仓其他 `from_dict` 不一致**——
  `Snapshot.from_dict` / `Config` 路径都做类型防御。一旦有人在过滤前调用（或新增调用方），
  `AttributeError` 会绕过所有 `except (ValueError, TypeError)` 收敛口径。
- **建议**：`from_dict` 顶部加 `if not isinstance(data, dict): raise TypeError(...)` 或返回默认 Alert。
- **复现概率**：直接调用 100%；产品路径当前 0%｜**优先级**：P3

### ✅ 通过的主要攻击面（无缺陷，留证）

| 攻击 | 结果 |
|---|---|
| 全天 1440 分钟扫描 `in_quiet_hours("23:00","08:00")` | True 区间 = `[00:00,08:00) ∪ [23:00,24:00)`，恰好 540 分钟，无断裂 ✅ |
| 跨零点区间起点/终点开闭口径 | 含起点、不含终点，与「分钟数」定义一致 ✅ |
| 日界切换 | 200 分钟扫描只产生 1 个 day key（不重复重置水位）✅ |
| 随机游走 400 步波动水位 | 播报次数严格 ∈ (0, 400)，水位单调收敛 ✅ |
| 脏 `at` 时间戳（NaN/字符串/None/超大）加载 120 条 Alert | 全部收敛为有限 float，排序与格式化不炸 ✅ |
| 时区切换（TZ=Asia/Shanghai + `tzset`） | `_roll_day` 结果始终等于 `time.strftime(localtime(now))` ✅ |
| `sanitize` 幂等（300×3 轮随机脏配置） | 跑 1/2/3 遍结果逐键一致 ✅ |
| `load→save→load` 三遍不动点（60 轮脏文件） | 第二遍起逐键稳定，无漂移 ✅ |
| 值域契约（25 条边界矩阵：档位/夹取/枚举/大小写） | 全部按契约收敛 ✅ |
| 未知键（`__evil__` / 空键） | 不进入内存、不被写回磁盘，key 集合恒等于 DEFAULTS ✅ |
| `json` 任意精度整数（`10**400`）× 4 键 | 不炸、不残留 inf/nan，落盘仍合法 JSON ✅ |
| `Config.save` 目标是目录 | `OSError` 被吞（R3-M-03 口径），不抛 ✅ |
| 1000 轮 `HistoryStore.append` | 序列 ≤ max_points、文件 < 20KB、FD/线程零增长 ✅ |
| 500 轮 `save_cache`/`load_cache` 往返 | FD 零泄漏、内容稳定 ✅ |
| 300 轮 `Config.save/load` | FD 零泄漏 ✅ |
| 1000 条 Alert 写入 | 内存与磁盘都被 `max_history` 收敛到 100 条，文件长度恒定 ✅ |
| 1000 轮混合负载后 RSS 增幅 | < 32MB（实测远低于阈值）✅ |
| 600 轮 append 耗时劣化 | 末段均值 ≤ 首段 × 3（实测无劣化）✅ |
| 真实 HTTP 服务 300 次请求 + 50 次鉴权失败 | FD 与请求线程均不堆积（`ThreadingHTTPServer` 回收正常）✅ |

---

## 三、并发/多进程守恒性（承接前轮，本轮复现真实缺陷）

**R4 遗留的「跨进程写 `history.json`」问题本轮实测复现，且比预期严重得多：**

```
4 个进程 × 各 append 200 次 = 期望 800 点
实际落盘：1 点
```

不是「丢几个点」，而是**几乎全丢**：每次 `append` 都是全量「读-改-写」，
多进程无协调时最后落盘者win，其余全被覆盖；而同一分钟内相同价格还会触发
`series[-1] = {...}` 的「同分钟覆盖」分支，进一步把结果压成 1 点。

- **影响面**：桌宠本体 + MCP 桥/CLI 同时活跃时（v0.8 的典型用法）会命中。
- **性质**：**架构级 P2**，需要维护者决策（文件锁需新依赖 / 单写者仲裁 / 接受丢点并文档化）——
  与 Issue #10 第三节所列待决策项一致，本轮**不擅自引入新依赖**，仅补上复现证据。
- **本轮已落一条回归锚**：`test_r5_soak.py` 中的 FD/体积守恒断言（防修锁时引入新泄漏）。

---

## 四、与 R4 的结论差异（如实声明）

Issue #10 记录的 R4 已修项中，**`Config.save` 的原子写在当前 `main` 上并未生效**：

- Issue #10 称 R4-M-01「已把 `settings.json`/`control.json` 的裸 `write_text` 改为 tmp+`os.replace`」，
  并附提交号 `1d291af` / `6944bda`。
- 但这两笔提交**不在 `origin/main` 上**（远端 `main` 仍是 `e660d9d`，
  R4 的改动留在 `qoder/unattended-20261009` 分支）。
- 实测当前 `main`：`gold_pet/config.py:216` 仍是裸 `write_text`，
  300 轮并发读命中 **292 次坏读（空文件/半截 JSON）**。
- 因此本轮把它作为**独立缺陷重新登记**（见代码评审文档 CR-01），
  修法与 R4 一致（复用 `_atomic_write_text`），并额外处理「同源引入」问题。

---

## 五、留痕

- 新增测试：`tests/test_monkey5_clock.py`、`tests/test_monkey5_roundtrip.py`、`tests/test_monkey5_soak.py`
- 修复：见 `Hy4-codebuddy-v0.8.0-FixLog-*.md`
- Issue：见 `Hy4-codebuddy-v0.8.0-IssueLog-*.md`
- 复现脚本（不入库）：`/tmp/probe1.py`（时钟逃逸）、`/tmp/probe2.py`（并发坏读）、`/tmp/probe4.py`（多进程丢点）

---

_本报告由 @codebuddy 自动生成。所有结论均以当前 `main`（`e660d9d`）上的实测为准，未引用未入库分支的说法。_
