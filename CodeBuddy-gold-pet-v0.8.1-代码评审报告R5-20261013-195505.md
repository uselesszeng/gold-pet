# gold-pet v0.8.1 代码评审报告 R5（CodeBuddy，2026-10-13）

评审人：CodeBuddy（资深代码评审角色）
评审对象：分支 `codebuddy/monkey-r5-review-r5`，基线 `e660d9d`（main）
配套产物：`CodeBuddy-gold-pet-v0.8.1-monkey测试报告R5-*.md`、`tests/test_review5_goldpet.py`（29 例）
种子：20261102（与 monkey 段 20261101 及历史各轮不重复）

---

## 一、评审角度（与前四轮评审报告的差异）

| 轮次 | 评审角度 |
|------|----------|
| R1~R3（GLM-5.3-Flash） | 逐条对抗验证 monkey 轮改动、安全面、Mimosa 语料质疑 |
| R4（Qoder 无人值守） | 配置装载读侧、并发原子写、畸形类型域（**但修复未合入 main**） |
| **R5（本轮）** | **「配置装载的语义完整性」＋「CI 门禁的有效性」** |

本轮不再只看「单个值合不合法」，而问两个前四轮没问过的问题：

1. **一个损坏的文件会让用户损失到什么？** 崩溃是显性的、用户能看见；**数据丢失是隐性的**，
   用户只会觉得「我的设置怎么没了」。这类缺陷前四轮全部漏检。
2. **CI 的四条门禁真的有在拦东西吗？** 门禁号称在跑，但如果它从未通过（或结果随
   依赖版本漂移），它就是**装饰品**——这比没有门禁更危险，因为它给人虚假的安全感。

## 二、发现问题（5 项：P1×1 / P2×2 / P3×2）

### REVIEW5-01（**P1**，数据丢失）受损 `settings.json` 被静默整份归零，且后续 save 覆写导致永久丢失

| 项 | 内容 |
|----|------|
| 位置 | `gold_pet/config.py` · `Config.load`（旧第 136~145 行） |
| 根因 | 把「文件不存在 / 读失败 / 内容损坏」**三种态挤进同一条 `except`** 返回 `{}`：无告警、无留档 |
| 证据 | 实测 `{"metal":"silver","interval_sec":300,"alert_pct":2.5,"target_high":1200.0}` 末尾**少一个花括号** → `load` 静默返回全默认；随后 `save()`（程序退出必然调用）**用默认值覆写原文件** → 用户自定义项**永久丢失**，磁盘上无任何可追回的现场 |
| 定性 | 完整性缺陷。用户能看见的只是「设置自己变回去了」，实际是数据被程序主动销毁 |
| 修复 | 三态拆分：文件不存在静默走默认（首次启动属正常）；读取失败 `logger.warning`；解析失败/非 UTF-8/顶层非对象 → 告警 + **原始字节**留档到 `<文件>.corrupt`（已存在不覆盖，保留**最早**现场）。留档失败只记日志，不破「坏配置也能起默认态」的底线 |
| 验证 | 6 种受损形态 × 留档矩阵 + 最早现场保持 + 「缺失不产生噪音」+ 完整用户旅程（坏文件 → 起得来 → 原配置可追回）全绿；反向验证该组 8 例在修复前全红 |

### REVIEW5-02（**P2**，功能失效）布尔键语义反转：字符串 `"false"` / `"0"` 被当成 `True`

| 项 | 内容 |
|----|------|
| 位置 | `gold_pet/config.py` · `sanitize`（`bool(d.get(key, ...))`） |
| 根因 | 用 Python truthiness 判定：**非空字符串恒为 True**。而 JSON 里布尔值写成字符串是极常见的（手改、别的工具写出、前端序列化） |
| 证据 | `{"control_enabled":"false","show_bubble":"false","dnd_enabled":"false","always_on_top":"0"}` → 四个键**全部**收敛成 `True`。用户勾选「关闭」后重启，功能又自己开了；`"dnd_enabled":"false"` 反而让勿扰生效 |
| 修复 | 新增 `_as_bool(value, default)`：bool 原样；int 0=False 非0=True（**锚定既有语义**，不破坏既有用例断言）；float 非有限值回落默认、其余按是否为 0；字符串认 `{"1","true","yes","on"}` / `{"0","false","no","off"}`（先去空白、大小写不敏感），**未知字符串回落默认**（不猜）；None/容器回落默认。主 `bool_keys` 与三个 `care_*_enabled` 同口径 |
| 验证 | 7 种假写法 + 6 种真写法 + 既有语义锚定（int/bool/未知值回落）+ 关怀键同族，全绿；反向验证该组 9 例修复前全红 |

### REVIEW5-03（**P2**，连锁故障源）`Config.save` 与 `control_server._write_info` 直写，失败留半截文件

| 项 | 内容 |
|----|------|
| 位置 | `gold_pet/config.py` · `Config.save`；`gold_pet/control_server.py` · `_write_info` |
| 根因 | 四个状态写入器里**只有这两个是直写**（truncate → write）。写入中途失败（ENOSPC / 进程被杀 / 杀软）时磁盘上留下**半截 JSON**。它**正好触发 REVIEW5-01** → 下次启动整份归零 → 「一次写入失败 = 用户配置永久丢失」的连锁 |
| 证据 | 注入 `ENOSPC`（只写入前半 + 抛错）后文件内容停在 `..."activity` 处，`json.loads` 报 `Unterminated string starting at: line 17` |
| 修复 | 两者统一改走与 `save_cache` / `HistoryStore.append` / `Notifier._save` 同一套 `_atomic_write_text`（tmp + `os.replace`）：读者要么看到旧内容要么看到完整新内容，**不存在半截中间态** |
| 说明 | 直写导致的**并发撕裂读**本轮未复现（1500 次读 0 次坏读——写入窗口对几百字节而言极短）。因此本项按「**写入失败留下坏文件的连锁风险**」定级，不夸大为「必现的并发坏读」 |
| 验证 | 注入 `.tmp` 写失败（目标文件绝不被截断）→ 原文件字节逐位不变且仍是合法 JSON、无 tmp 残留；四写入器口径统一的静态断言；反向验证 2 例修复前红 |

### REVIEW5-04（**P3**，可维护性）CLI 与服务端常量两处手抄，靠注释约束同步

| 项 | 内容 |
|----|------|
| 位置 | `cli.py:62-64`（`MAX_SAY_CHARS` / `PLAYABLE_STATES` / `ALERT_KINDS`）与 `gold_pet/control_server.py` |
| 根因 | `cli.py` 自认「服务端改动时必须同步这两处」——**注释约等于没有约束**。漏同步就静默产生两端语义不一致（CLI 放行 600 字而服务端截断 500；CLI 放行某动作而服务端回 400） |
| 修复 | 新增 `gold_pet/protocol.py`（纯常量、零依赖）作为**唯一事实来源**，两侧引用同一对象。`cli` **不能**直接 import `control_server`（后者顶层拉 PySide6，而 CLI 的设计前提是无 GUI 依赖）——下沉模块正是为兼顾这两条约束 |
| 验证 | 值相等 + **对象同一性**（`is`，抄一份做不到）+ 属性存在性三条断言；`cli.py --help` 无 GUI 环境端到端正常，退出码约定不变 |

### REVIEW5-05（**P2**，门禁有效性）CI 的四条硬门禁里，**两条长期不通过**、结果随依赖版本漂移

| 项 | 内容 |
|----|------|
| 位置 | `.cnb.yml` 的 `lint` / `type check` 阶段；`requirements-dev.txt` |
| 事实 1 | `ruff format --check .` 在**改动前**就有 **38 个文件**不满足 formatter 风格 → 该门禁**从未通过过**，一直在空转（仓库历史上只跑 `ruff check`，不查排版） |
| 事实 2 | `mypy` 在**改动前**就报 1 个真 error（`price.py:241`：`_num` 接受 `object`，而 `float()` 静态签名只接受 `SupportsFloat` 等）→ type check 阶段必红 |
| 事实 3 | `requirements-dev.txt` 写的是 `ruff>=0.6` **未固定版本**：实测 ruff 0.6.9 下 `check` 报 `UP038`（`control_server.py:63`），ruff 0.17 下该规则**已被移除**而 `format --check` 又因默认排版风格不同报 38 个文件 → **同一份代码、同一个仓库，门禁结论取决于装包时刻** |
| 修复 | ① `_num` 显式标注 `# type: ignore[arg-type]`（`pyproject.toml` 的 `warn_unused_ignores=false` 正为此留位），**不加宽签名**以免削弱「接受任意脏值」的契约；② `ruff==0.6.9` 固定版本 + 修掉 0.6.9 报出的唯一 `UP038`；③ 用固定版本执行一次**纯格式重排**（38 个文件），单独一笔提交 |
| 验证 | 四条门禁**首次本地全绿**：`ruff check` PASS / `ruff format --check` PASS（61 files already formatted）/ `mypy` PASS / `pytest --cov` PASS（81.38% ≥ 65%）。纯排版提交逐文件做 **AST 等价**验证 —— 被 format 改动的 .py **全部 AST 完全相同**，零逻辑变化 |

## 三、核查通过项（无问题，已实测确认）

| 面 | 结论 |
|----|------|
| 控制服务鉴权 | token 走 `secrets.token_urlsafe`、比对前先短路失败路径、401 前限排空请求体（M-09 口径保留） |
| HTTP 面输入校验 | `/move` 的 `_to_coord` 拒 bool/非有限/非数值；`/play` 白名单；`/say` 限长 500——REVIEW5-04 修复后两端**同一常量对象**，不再有漂移空间 |
| 服务生命周期 | R3-M-02 的「发现文件写失败不得让 `stop()` 永久阻塞」修复在位（`run()` 里 `_write_info` 包 `except OSError`） |
| 写盘容错口径 | 四个写入器在 REVIEW5-03 后**全部**原子写 + 吞 OSError + 不残留 tmp |
| CLI 退出码约定 | 0/2/3/4 四档，`main` 有 `CliError` + 兜底 `except Exception` 双保险；实测无桌宠时 `status --json` 返回结构化 `exit_code: 4` |
| 发现文件校验 | `read_control_info` / `cli._discover` / MCP 桥三处对 port/token 同严（`0<port<65536`、token 非空 str） |
| 路径与资源 | `paths.py` 用户目录/资源目录分离正确；打包态走 `_MEIPASS` |

## 四、遗留项（不修，附理由与建议）

1. **跨进程「读-改-写」`history.json` 丢更新**（P2，沿用 R4 的待决策项）
   实测 6 进程 × 25 点：期望 150 点，实际 **44 点**（丢 106）。原因：各进程各自 `load → 改 → 原子写`，
   后写者覆盖前写者的新增点；原子写只保证**不写坏文件**，不保证**不丢更新**。
   桌宠本体 + MCP 桥同时跑即可能命中。三个可选方案：A. 独占文件锁（**需新增依赖，须授权**）；
   B. 单写者仲裁（CLI 只读，已基本如此）；C. 接受丢点并文档化。
   **本轮未改**：属于需产品决策的取舍（锁会引入新依赖 + Windows 行为差异），且不在「复测 + 发版」的任务边界内。
2. **`ruff format` 只覆盖 `.py`，`程序修改点.md` 也被 format 动过**
   markdown 被 diff 属正常（其内含代码块），已核对内容未被改写。
3. **`test_monkey3_degraded.py:239` 的 `subprocess.run(..., text=True)` 缺 `encoding`**
   （沿用 R4 报告 MTX-01 的定案）。CI 为 UTF-8 locale，本机未复现；属**测试资产改动**，
   R4 建议的 A 案（补 `encoding="utf-8", errors="replace"`）仍未获授权，本轮按纪律不擅改。
4. **`test_monkey2_concurrency.py::test_r2_control_server_threaded_endpoints` 满载时偶发 ConnectTimeout**
   单独跑必绿；属测试资产对机器吞吐的敏感性（与 R4 报告的 240s 预算不足同族）。

## 五、验证与统计

- 发现 5 项（P1×1 / P2×2 / P3×2，其中门禁项记 P2）；**修复 5 项**；遗留 4 项（含 1 项需产品决策）。
- **反向验证**：把本轮 4 个源码修复回退后跑评审套件 → **29 例中 20 例转红**
  （留档 8、布尔语义 9、原子写 2、常量同源 1），分布精确命中各组发现；恢复后全绿。
- 全量回归：**478 passed / 0 failed / 10 skipped**（入场基线 396 passed / 2 failed）。
- 覆盖率 **81.38%**（门禁 65%）。
- 四条 CI 门禁首次本地全绿（口径见 REVIEW5-05）。
- 新增：`tests/test_review5_goldpet.py`（29 例）、`gold_pet/protocol.py`。

## 六、改动文件清单

| 文件 | 关联项 |
|------|--------|
| `gold_pet/config.py` | REVIEW5-01 / 02 / 03（三态拆分 + 留档、`_as_bool`、原子写） |
| `gold_pet/control_server.py` | REVIEW5-03（发现文件原子写）、REVIEW5-04（常量引用 protocol） |
| `gold_pet/protocol.py`（新增） | REVIEW5-04（协议常量唯一事实来源） |
| `cli.py` | REVIEW5-04（改为引用 protocol，不再手抄） |
| `gold_pet/price.py` | REVIEW5-05（`_num` 的 type ignore） |
| `requirements-dev.txt` / `pyproject.toml` | REVIEW5-05（固定 ruff 版本 + 说明） |
| `tests/test_review5_goldpet.py`（新增） | 本轮全部发现的固化回归 |
| 38 个 `.py`（additionally 5 个） | REVIEW5-05 的纯格式重排（AST 等价已验证） |

## 七、总体结论

本轮找到并修复了 1 个 **P1 数据丢失链**（REVIEW5-01，且它有一个 P2 的上游触发点 REVIEW5-03，
两者构成完整因果），这是前四轮「只看崩溃、不看损失」的盲区里的真问题。
另外把 CI 的**两条空转门禁**（format / mypy）和**一条不可复现门禁**（ruff 版本漂移）修通了 ——
在此之前，无论代码多干净，`lint` 和 `type check` 阶段都不可能真的给出可信信号。

主线状态：全量 478 passed / 0 failed，覆盖率 81.38%，四条门禁全绿。
