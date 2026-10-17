# 代码评审报告 R5（gold-pet v0.8.0）

- **执行模型**：Hy4（CodeBuddy）｜**调度 Issue**：#19
- **评审对象**：`origin/main` @ `e660d9d`（v0.8.0）
- **评审时间**：2026-10-17（沙箱时钟）
- **覆盖范围**：`gold_pet/`（12 模块 2368 行）、`cli.py`、`tools/`、`tests/`、`.cnb.yml`、`requirements*.txt`
- **评审角度**：前四轮未碰的三个面 —— **落盘一致性台账**、**常驻程序稳态**、**门禁有效性**

> 全部结论以**当前 `main` 上的实测**为准；未采纳任何未入库分支的说法。
> 每条给出「现象 / 依据 / 影响 / 处置」，按优先级降序。

---

## P1（阻塞门禁或造成数据损失）

### CR-01 `Config.save` 与 `ControlServer._write_info` 用裸 `write_text`，破坏「四写入器原子写」台账

- **优先级**：P1 ｜ **状态**：✅ 已修（[#20](https://cnb.cool/TJ-coding/gold-pet/-/issues/20)，PR #26）
- **位置**：`gold_pet/config.py:216`、`gold_pet/control_server.py:323`
- **现象**：`write_text` = `open(w)` + `write`，**先 truncate 再写**，中间存在「0 字节或半截 JSON」窗口。
- **依据（实测）**：一个线程持续读 `settings.json`、主线程 `save()` 300 轮 → **坏读 292 次**。
- **影响**：并发的 CLI / 同步盘 / 杀软 / 编辑器读到会 `JSONDecodeError`；**窗口内进程被杀 → 配置永久留在截断态**，
  下次 `Config.load` 读不了 → 用户阈值/开关/窗口位置静默蒸发。`control.json` 同理会让人误判「桌宠没在运行」。
- **处置**：改用同源 `_atomic_write_text`（tmp + `os.replace`）。修复后坏读 0 次。
- **为什么值得记一笔**：`history.json` / `alert_history.json` / `last_price.json` 三个写入器**都**做了原子化并写了注释，
  只有这两个漏掉 —— 这是**台账不闭合**的典型形态（同一纪律在 4 处里落实了 2 处）。

### CR-04 CI 门禁实为「关着的门」：lint 阶段必红，mypy 与 pytest 从未执行

- **优先级**：P1 ｜ **状态**：✅ 已修（[#23](https://cnb.cool/TJ-coding/gold-pet/-/issues/23)，PR #26）
- **位置**：`.cnb.yml`（push / pull_request 两条流水线的 `quality` 任务）
- **现象**
  - `ruff format --check .` → `38 files would be reformatted`（exit 1）
  - `python -m mypy` → `gold_pet/price.py:241: error: float(object)`（exit 1）
- **依据（实测）**：CI 在 lint 阶段的第二个 script 退出，**后续 mypy 与 pytest 两个 stage 从未运行**。
- **影响**：质量门禁形同虚设 —— 覆盖率门禁、类型检查、Monkey 回归全部没有在 CI 上生效过。
- **两层根因**
  1. `_num(value: object, ...)` 的宽入参**是有意设计**（要吃被手改的脏 JSON），
     但 `float()` 存根只收 `str|Buffer|SupportsFloat|SupportsIndex`；函数体里的
     `except (TypeError, ValueError, OverflowError)` 已经表达了运行期意图，缺的只是**类型层面的同义表达**。
  2. `ruff>=0.6` 浮动版本 → 格式化结果随版本漂移（Issue #15 已在安卓侧记录同一现象）。
- **处置**：`_num` 内局部收窄为 `Any`（运行期行为零变化，10 条参数化用例锚定）；
  全仓 `ruff format` 收口；`ruff` 钉死 `==0.17.1`。
- **修复后**：`ruff check` 绿 / `format --check` 绿 / `mypy` 绿 / `pytest` 489 passed、覆盖率 81.67%。

### CR-03 测试门禁的「只读」断言依赖 Windows 语义，在 Linux 上永久失败

- **优先级**：P1 ｜ **状态**：✅ 已修（[#22](https://cnb.cool/TJ-coding/gold-pet/-/issues/22)，PR #26）
- **位置**：`tests/test_monkey3_fault_fs.py::test_r3_fs_readonly_target`
- **现象**：全量 `pytest` 恒为 `2 failed`。
- **依据**：用例用 `os.chmod(target, S_IREAD)` 造「只读」，但产品走 `tmp + os.replace`，
  而 **POSIX 的 `rename(2)` 只要求目录可写、不检查目标文件权限位** → 只读目标照样被原子替换。
  Windows 上 `MoveFileEx` 返回 `ACCESS_DENIED`，所以该用例只在 Windows 成立。
- **影响**：「一条命令跑整仓」永远拿不到绿证 —— 也就是 Issue #10 里维护者仍在等的
  「MTX-01 绿证」的另一半（前一半是 `text=True` 编码问题）。**门禁可信度被一条平台错配的断言拖住**。
- **处置**：故障注入换成 `os.replace` 抛 `PermissionError`（两平台都真实成立），
  原 Windows 语义保留为 `nt` 专属孪生用例。
- **评审意见**：这类「测试自己写错了假设、却表现成产品红」的缺陷最耗人 —— 它让真正的红（P1 产品缺陷）
  淹没在噪声里。**建议后续轮次把「先证明用例前提成立」写进 Monkey 纪律**。

---

## P2（功能性缺陷 / 需架构决策）

### CR-02 跨进程写 `history.json` 几乎全丢（4×200 → 1 点）

- **优先级**：P2 ｜ **状态**：⏸️ **保持 open**（[#25](https://cnb.cool/TJ-coding/gold-pet/-/issues/25)，待维护者决策）
- **位置**：`gold_pet/price.py` `HistoryStore.append`、`gold_pet/notifier.py` `Notifier.record`
- **现象**：每次 append 都是全量「读-改-写」，无文件锁、无版本比较 → 最后落盘者 win。
- **依据（实测）**：4 进程 × 各 200 次 → **实际落盘 1 点**。
  且「同一分钟内相同价格覆盖末点」的分支会把结果进一步压成 1 点。
- **影响面**：v0.8 的典型用法就是「桌宠本体 + MCP 桥/CLI 同时活跃」。
  **进程内多线程是安全的**（`R2-M-05` 全程持锁），跨进程无任何协调。
- **处置**：本轮**不修**。候选方案 A（薄封装跨平台锁，零新依赖）/ B（单写者仲裁）/ C（文档化接受丢点）
  属架构决策，无人值守纪律下不擅自选路。已补齐量化证据并预置资源守恒回归锚。
- **推荐**：A，其次 B。

### CR-05 `Notifier._roll_day` 对极端时间戳抛 `OSError` 并逃逸出 `evaluate`

- **优先级**：P2 ｜ **状态**：✅ 已修（[#21](https://cnb.cool/TJ-coding/gold-pet/-/issues/21)，PR #26）
- **位置**：`gold_pet/notifier.py:222`
- **现象**：`time.localtime(1e18)` → `OSError: [Errno 75] Value too large for defined data type`，
  从 `evaluate()` 一路逃逸（`evaluate` 是 UI 刷新链路直接调用点）。
- **依据（实测）**：`n.evaluate(quote, now=1e18)` 直接抛 `OSError`。
- **影响**：`now` 是公开参数（注入时钟、脚本、被系统调成极端值的时钟都能传）；
  **一次刷新崩穿**。同仓 `cli._fmt_time` 早已为同类问题捕获 `OSError` 并注释
  「时间戳可能被手改成任意值」—— 说明「时间戳不可信」是既定前提，这里是**同族漏网**。
- **处置**：捕获三类异常后**降级为「不跨日」**（保留 `_day`、不清水位）。
  降级方向的取舍：漏一次水位重置只影响「跨日后第一次新高可能不播报」，
  远轻于崩穿刷新链路。

---

## P3（防御性 / 口径统一）

### CR-06 `Alert.from_dict` 对非 dict 输入抛 `AttributeError`

- **优先级**：P3 ｜ **状态**：✅ 已修（[#24](https://cnb.cool/TJ-coding/gold-pet/-/issues/24)，PR #26）
- **现象**：`Alert.from_dict(None / [] / "str" / 42)` → `AttributeError: ... has no attribute 'get'`。
- **依据**：`Notifier._ensure_loaded` 有 `isinstance(item, dict)` 过滤，**当前产品路径不可达**。
- **为什么仍要修**：`AttributeError` **不在**本仓任何 `except` 白名单里
  （常见口径 `TypeError/ValueError/OSError/RecursionError`）；同仓 `Snapshot.from_dict`、
  `Config` 装载路径都做了类型防御 —— 这是**工厂函数契约不统一**。一旦有人在过滤前调用，
  异常会穿透整个收敛链路。
- **处置**：顶部加类型守卫抛 `TypeError`；既有宽容度由回归用例锚定不变。

### CR-07 `requirements.txt` 的 `"mcp<2"` 写法 + 无锁文件

- **优先级**：P3 ｜ **状态**：📝 仅记录（Issue #18 已单独登记，本轮**不抢同一行**）
- **现象**：`pip install -r requirements.txt` 对带引号的 requirement 解析行为依版本而异；
  仓库无锁文件，实际装到 2.1.1 时 `tools/pet_mcp_server.py` 的 `FastMCP`
  在 mcp 2.x 里已改名为 `MCPServer` → MCP 相关 **9 条测试直接 ERROR**。
- **澄清**：这**不是**代码缺陷 —— `requirements.txt` 明确写了 `mcp<2`，是**环境没按声明装**。
  但表现成「代码坏了」会误导后继轮次。
- **建议**：随 #18 一并处理（去掉引号 + 考虑加 `constraints.txt` 或锁文件）。

### CR-08 控制服务鉴权用 `==` 比较 token（非恒时比较）

- **优先级**：P3 ｜ **状态**：📝 仅记录（`tools/` 侧同类问题见于 Issue #10 第三节）
- **位置**：`gold_pet/control_server.py:111` `self.headers.get("X-GP-Token", "") == self.server.token`
- **分析**：服务仅绑 `127.0.0.1`、token 由 `secrets.token_urlsafe(24)` 生成且随进程轮换，
  **远程计时攻击面不存在**；本地攻击者已具备更大能力。
- **结论**：**可接受不修**。若将来控制服务支持非回环绑定，必须先换成 `hmac.compare_digest`。
  记录在案，避免后继轮次反复「发现」同一件事。

### CR-09 「静默吞掉」的日志可观测性

- **优先级**：P3 ｜ **状态**：📝 建议（不阻塞）
- **观察**：`Config.save`、`HistoryStore.append`、`Notifier._save`、`PriceService.save_cache`
  都是 `except OSError: pass` 或仅 warning。口径统一（最佳努力落盘）是**对的**，
  但用户侧完全无感知：磁盘满的时候配置改动**静默丢失**。
- **建议**：可考虑在连续 N 次落盘失败后，于气泡/托盘给一次性提示（成本低、可观测性收益高）。
  属产品决策，非缺陷。

---

## ✅ 评审通过面（本轮核查未发现 P0~P2 的模块）

| 面 | 结论 |
|---|---|
| **原子写实现** `price._atomic_write_text` | 临时名带 pid+线程 id+自增序号，并发不互相覆盖；失败分支 unlink 清理正确 ✅ |
| **线程模型** `control_server` | HTTP 线程绝不触碰 QWidget，动作经 Signal 跨线程投递；`daemon_threads=True` 防卡关机 ✅ |
| **/say 注入面** | 转义收口在渲染边界 `bubble.render_line_html`（实测 `<img onerror>` 被转义），服务端不重复转义 ✅ |
| **/move 数值边界** | `_to_coord` 拒 bool/NaN/Inf；`_clamp_to_screen` 先夹 int32 再构造 `QPoint`（R2-M-02）✅ |
| **请求体边界** | `_MAX_BODY_BYTES` 413、`_MAX_DRAIN_BYTES` 排空、超深 JSON 接 `RecursionError` ✅ |
| **鉴权短路** | 401/404 先 `_drain_body` 再回包，避免 Windows RST ✅ |
| **长跑稳态** | 1000 轮 append / 500 轮 cache / 300 轮 config / 300 次 HTTP：RSS、FD、线程、文件体积**全部有界** ✅ |
| **时间语义** | 全天 1440 分钟扫描勿扰区间连续且恰好 540 分钟；含起点不含终点；跨零点正确 ✅ |
| **配置往返** | `sanitize` 幂等；`load→save→load` 三遍不动点；未知键不落盘；值域契约 25 条边界全对 ✅ |
| **首启可重建性** | 空用户目录冷启动 0.039s 落齐 4 个文件，无缺失/多余 ✅ |

---

## 综合结论

| 维度 | 评价 |
|---|---|
| **架构** | 分层清晰（纯逻辑模块不依赖 Qt，UI 层薄）；故障域隔离到位（MCP 是可选的独立进程） |
| **容错** | 前四轮的投入看得见 —— 脏数据、并发、故障注入的收敛口径已成体系。**本轮暴露的都是「台账不闭合」**：`cli._fmt_time` 想到了、`_roll_day` 没想到；三个写入器原子化了、两个漏了 |
| **可测试性** | 注入式时钟、`screens_override`、`on_action` 注入点设计良好，Monkey 才打得进去 |
| **工程化** | 门禁脚本齐全但**长期未生效**（CR-04）—— 这是本轮最值得修的一项，也是「任务结束条件」的直接阻塞 |

**一句话**：v0.8.0 的防线是「有纪律的」，缺的是「让纪律自证的机制」——
本轮的修复（门禁转绿 + 台账闭合 + 98 条回归锚）正是补这一块。

---

## 与 Issue #19 任务结束条件的关系

| 条件 | 状态 |
|---|---|
| 1. 达到质量门禁 | ✅ **本轮达成**（ruff / format / mypy / pytest+覆盖率 全绿） |
| 2. 完成执行 SOP 全部动作 | ✅ Monkey → Monkey 文档 → refill → Issue 逐条 → 修复并关闭 → 本评审文档（优先级降序） |

仍有 1 条 Issue 保持 open（#25，架构决策），属「阻塞项写入文档、不阻塞调度」的口径；
若维护者认为 #25 也须闭环，任务应继续顺延，否则本轮即可判定结束条件满足。

---
_本报告由 @codebuddy 自动生成。_
