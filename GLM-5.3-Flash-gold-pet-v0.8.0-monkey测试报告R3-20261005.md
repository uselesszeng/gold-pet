# gold-pet v0.8.0 Monkey 测试报告 R3（2026-10-05，第三轮）

执行人：GLM-5.3-Flash（资深测试工程师角色）
项目：gold-pet v0.8.0（Python 3.13 + PySide6，路径 `C:\Users\tj-435\WorkBuddy\2026-09-30-09-31-13\gold-pet`）
入场基线（任务给定）：`pytest tests/` **316 passed / 1 skipped / 1 xfailed**
最终结果：**405 passed / 1 skipped / 1 xfailed，零回退**（基线 316 全保留 + 新增 89 个 R3 测试参数化用例，63 个测试函数），ruff（E/F/W/I/UP/B，gold_pet + tests + cli.py + tools）All checks passed。两轮全量运行结果一致（360s / 369s）。
零新增依赖：故障注入全部 monkeypatch/标准库；网络面复用项目既有 http.server/urllib；UI 走 offscreen 平台。

---

## 0. 与 R1/R2 的差异点

| 维度 | R1（报告 20261002） | R2 | **R3（本轮）** |
|------|------|------|------|
| 角度 | 单请求脏 payload 轰炸 | 修复合规绕过 + 并发 + 状态机 + MCP stdio | **故障注入（写盘/网络/线程那一刻注入错误）+ 中断自愈 + 时钟异常 + Mimosa 扫描语料定向复核 + 降级环境** |
| 种子 | 20261002~07 | 20261011~16 | **20261021 ~ 20261027**（7 个，全部不重复） |
| 注入面 | 输入数据 | 并发/状态机 | **输出/副作用时刻**：Path.write_text / os.replace / QThread.start / urlopen / time.time / 半截文件构造 |
| oracle | 不崩 + 结构化 | 同 + 资源守恒 | **故障下结构化不崩、半截文件不破坏原数据（读者要么旧要么新）、故障解除后一次自愈、stop() 不得死锁（看门狗 ≤5s）** |

R1/R2 语料与种子均未重复；R3 攻击面与其无重叠。

## 1. 目标清单（7 目标 / 63 测试函数 / 89 参数化用例）

| # | 目标 | 测试文件 | 种子 | 用例数 |
|---|------|----------|------|--------|
| T1 | 文件系统故障注入（ENOSPC/EACCES/EMFILE/只读/目录占位 × 5 状态文件） | tests/test_monkey3_fault_fs.py | 20261021 | 7（含 500 次随机混合） |
| T2 | 网络故障注入（真行情服务 10 种上游行为 + 退避链 + 断连变体） | tests/test_monkey3_fault_net.py | 20261022 | 9（含 500 次随机故障混合） |
| T3 | 线程/子进程故障（QThread 启动失败、端口占用、发现文件写失败、服务半途关闭、stop 生命周期矩阵） | tests/test_monkey3_fault_thread.py | 20261023 | 11（含 500 次空 stop） |
| T4 | 中断恢复（半截 JSON / 半截 tmp / rename 前后中间态 × 5 状态文件，自愈写盘） | tests/test_monkey3_interrupt.py | 20261024 | 7（含 500 轮随机破坏） |
| T5 | 时钟异常（±1 天 / ±1 年 / ±61s 跳变 × 去重/防抖/水位/DND/排期/时间轴） | tests/test_monkey3_clock.py | 20261025 | 10（含 3×500 跳变轰炸） |
| T6 | Mimosa 扫描语料定向复核（8 条 finding 逐条假设验证，不直接采信） | tests/test_monkey3_mimosa.py | 20261026 | 10 |
| T7 | 降级环境（assets 全缺/部分缺失、配置全缺、无网络、日志不可写、无托盘） | tests/test_monkey3_degraded.py | 20261027 | 9（含 500 次残缺配置） |

## 2. 方法

- **故障注入（真实覆盖错误路径，非灌脏数据）**：
  - 文件系统：monkeypatch `Path.write_text`（.tmp 阶段注入 ENOSPC/EACCES/EMFILE）、`os.replace`（rename 阶段注入 EACCES）、`chmod S_IREAD`（只读目标）、目录占位（`history.json` 位置是目录）；
  - 网络：本机 ThreadingHTTPServer 扮演新浪行情源，脚本化 10 种行为（慢响应 0.8s / 500 / 断连 / 半截报文 / GBK 乱码 / 非有限数值 / 1MB 大包 / 302 / 404 / 连接被拒 + accept 即关），PriceService.fetch 与 PriceWorker 全链路；
  - 线程：monkeypatch `ControlServer.start` / `build_server` / `_write_info`（QThread 创建失败、端口占用、发现文件写失败）；
  - 时钟：monkeypatch `time.time` 为可调 `_Clock`，14 档跳变量（±1 天 / ±1 年 / ±30 天 / ±6h / ±59s…）随机叠加 ±120s 抖动；
  - 中断：按随机切点截断合法状态文件（含 UTF-8 多字节中间切断、尾部垃圾、BOM、错类型、NaN 字面量、2 万层深嵌套），并在目标旁伪造半截 `.pid.tid.seq.tmp` 与「rename 未完成中间态」。
- **oracle**（按目标加严）：故障/坏数据下不得有异常逃逸；目标文件读者要么旧要么新（byte 级不变）；失败路径不残留 `.tmp`；故障解除后一次写盘严格合法 JSON（`parse_constant` 拒 NaN/Infinity）；`stop()` 看门狗 5s 内必须返回（死锁检测）；CLI 退出码 ∈ {0,2,3,4} 且 --json 末行合法；单操作 ≤2~3s。
- **Mimosa 语料**：读取 `C:\Users\tj-435\.mimosa\security-scans\project-19ddff75e31ce2260e9b86ea\scan-2026-10-04T07-17-16.204Z-95cc34778526\findings.json`（可读，8 条：2 low 不安全随机数 + 1 high path-traversal advisory + 5 high SSRF，verdictEffect=none）。每条作为**待验证假设**写定向测试实测（§3）；`test_r3_mimosa_scan_corpus_matches` 固化语料一致性（目录不可读时 skip 并注明）。
- **降级环境**：assets 整目录缺失 / 仅 idle 单帧、settings 键全缺与 500 次随机残缺、断网（4 类网络异常 × fetch/UI/CLI/run_once）、日志目录被文件占位（子进程验证）、托盘不可用。

## 3. Mimosa 假设验证结果表（8/8 实测，0 条复现为缺陷）

| 假设 | 位置 | 扫描器结论 | 实测结论 | 证据 |
|------|------|-----------|----------|------|
| H1 | ui.py:258 `random.choice(FATTY_LINES)` | insecure-randomness (CWE-330, low) | **未复现（非缺陷）**：台词池随机挑选，非安全上下文；token 生成走 `secrets.token_urlsafe`（源码断言） | `test_r3_mimosa_h1_*`：400 次池外内容零出现 |
| H2 | ui.py:768 `random.choice(LINES)` | insecure-randomness (low) | **未复现（非缺陷）**：同上，台词渲染；非法 mood 回落 idle | `test_r3_mimosa_h2_*`：500 次含 `nope/NaN/None` mood 全部回落 idle 池 |
| H3 | tools/import_dsh_assets.py:103 `frames_of` path-traversal (high, **advisory，需人工确认**) | 命令行参数流入 frames_of | **未复现（无越权写）**：真实 GIF 端到端跑 `main()`，src 含 `..` 只影响读入来源，写目标恒定 `PET_DIR`/ICON；真实工程 assets 目录 before/after 全量 diff 为零 | `test_r3_mimosa_h3_*` |
| H4 | cli.py:157 `urlopen(req)` SSRF (high) | 动态 URL 未校验 | **未复现**：URL 唯一来源是本机 `control.json` 的 port/token，host 恒为 127.0.0.1、path 白名单（/status /say /play /pat /tease /move）；parser 无 host/url 入口；脏 port（0/-1/65536/99999/True/"8"/3.14/None/list/dict）全部结构化 exit 4 且不发起请求 | `test_r3_mimosa_h4_*` ×2 |
| H5 | price.py:254 `urlopen(req)` SSRF (high) | 动态 URL | **未复现**：目标 URL 是模块字面量常量 `SINA_URL`（https://hq.sinajs.cn），`fetch_raw` 签名仅 `timeout` | `test_r3_mimosa_h5_*` |
| H6 | tools/cnb_release_upload.py:62 SSRF (high) | 动态 URL | **未复现**：slug 注入 12 种对抗串（evil.com@attacker.test、//evil.com、../../evil、换行注入…）+ 200 次随机 slug，`urlsplit` 断言 host 恒 `api.cnb.cool`、scheme 恒 https；控制字符输入 urllib 在发请求前拒绝（captured 为空） | `test_r3_mimosa_h6_*` |
| H7 | tools/cnb_release_upload.py:86 SSRF (high) | 动态 URL | **未复现（设计内信任边界）**：PUT URL 唯一来源是 CNB API 响应字段（预签名上传流程要求原样跟随），函数无 host 参数。记录条件：API 响应被控时文件会被 PUT 到任意外部地址——本次无证据，不单列缺陷 | `test_r3_mimosa_h7_*` |
| H8 | tools/pet_mcp_server.py:82 `httpx.request` SSRF (high) | 动态 URL | **未复现**：桥请求恒 `http://127.0.0.1:{port}`，port/token 来自本机 control.json（R2-M-06 已加固校验），path 白名单 | `test_r3_mimosa_h8_*` |

**复现数：0 / 8**；全部「未复现」或「记录条件」。Mimosa 扫描语料目录可读，无需跳过注明；扫描状态 inconclusive（verdictEffect=none），其 high 级 SSRF 均因 URL 锚定本机/常量/固定 API 而未构成可利用缺陷。

## 4. 发现清单（4 项，全部修复）

| 编号 | 级别 | 目标 | 根因 | 证据（故障注入表现） | 修复 | 验证 |
|------|------|------|------|------------------|------|------|
| R3-M-01 | **P2** | price.py `_f`（行情源文本字段转 float） | `float("1e999")` 成功但产出 `inf`；`float("nan")`/`float("inf")` 成功产出非有限值，被 `parse_snapshot` 当作有效报价，`json.dumps` 把 **NaN/Infinity 字面量写进 last_price.json**（严格 JSON 解析器全不认；一轮 M-04 只修了 `_valid_point`/`append`，`_f` 是残余同族面） | 真行情服务 `/nofinite`（价格=1e999）、`/nanmix`（价格=nan, prev_close=inf）：缓存文件出现 `Infinity`/`NaN` 字面量 | `_f` 捕获元组补 `OverflowError`，非有限值回落 default（与 `_num`/`_valid_point`/`_safe_float` 同口径） | `test_r3_net_fetch_matrix[/nofinite][/nanmix]`、`test_r3_net_nonfinite_upstream_keeps_cache_strict`、`test_r3_net_random_fault_mix`（500 次含非有限语料）：缓存恒严格 JSON、非有限报价被剔除 |
| R3-M-02 | **P1** | control_server `run()` 发现文件写失败 | `_write_info()` 抛 OSError（磁盘满/只读 AppData）发生在 `serve_forever` **之前**：QThread 死亡后 `self._server` 仍挂着未开始服务的实例，`stop()` 里的 `server.shutdown()` 因 `serve_forever` 从未运行（`__is_shut_down` 事件永不置位）**永久阻塞**——应用退出/关服务直接挂死 | `_write_info` 注入 ENOSPC 后 4s 看门狗 stop() 未返回（stashed 基线实测 `test_r3_thread_info_write_failure_still_serves_and_stops` FAILED） | `run()` 中 `_write_info()` 包 `except OSError`：发现文件缺失只影响 MCP 桥自动发现，本地服务照常提供，降级不挂死 | 回归转绿：服务真实 200 应答 + stop() 5s 内返回 |
| R3-M-03 | **P2** | config.py `Config.save` 直写不吞 OSError | 其余三个写入器（append/save_cache/_save）在 R2-M-05 已统一原子写+容错口径，`Config.save` 仍裸 `Path.write_text`：磁盘满/只读时 UI 槽（`quit_app`）炸穿，`QApplication.quit()` 永不执行（托盘退出/关窗退不掉） | settings.json 写路径注入 EACCES/ENOSPC/EMFILE：OSError 逃逸到调用方（stashed 基线 8 个 settings 用例 FAILED） | `save()` 包 `except OSError`：记 warning 不抛，与四个写入器口径一致 | `test_r3_fs_atomic_write_faults_do_not_escape[*-settings]`、`readonly_target[settings]`、`recovery_after_fault[settings]`、`random_fault_burst`、`dir_in_place_of_state_file` 全转绿 |
| R3-M-04 | **P2** | main.py `setup_logging` | `RotatingFileHandler` 构造即打开日志文件：日志目录不可写（目录被文件占位/磁盘满/只读 AppData）时 OSError 在 `main()` 最前面抛出，**降级环境下应用带裸 traceback 启动即崩** | 子进程实测：`app_dir()` 指向一个普通文件时 `setup_logging` 抛 NotADirectoryError | 文件 handler 构造包 `except OSError` → 降级仅控制台输出（stderr 提示一次） | `test_r3_degraded_logging_unwritable`（子进程，stashed 基线 FAILED → 修复后「degraded-ok」） |

**附带（测试基建，不立编号）**：`tests/test_ui.py` / `tests/test_monkey_ui.py` 的 `_window` 夹具统一 `control_enabled=False`——否则每个测试窗口各起一个 ControlServer QThread 并向真实 `%APPDATA%\GoldPet\control.json` 写发现文件，用例结束 QThread 仍 running 时解释器退出触发 Qt qFatal（0xC0000409），pytest 汇总与退出码一并丢失。
**附带（P3 健壮性，随 R3-M-02 同族）**：`ui.py _start_control` 对 `control.start()` 包 `except (RuntimeError, OSError)`：QThread 创建失败（线程资源耗尽）时控制服务降级不提供，不炸 PetWindow 构造路径。

**级别统计**：P0 = 0；P1 = 1（R3-M-02）；P2 = 3（R3-M-01/03/04）；P3 = 0。
**修复数**：4 / 4（全部最小化修复，无架构改动，零新增依赖）。
**遗留数**：0（Mimosa 8 条假设 0 复现；H7 记录的设计内信任边界条件随假设表注明）。

## 5. 统计与验证

- 发现 4 项（P1×1 / P2×3）；修复 4 项；Mimosa 假设复现 0/8；遗留 0 项。
- **反向验证（证明 4 处修复非冗余）**：`git stash` 暂存 5 处源码修复后跑全量，R3 套件 107 例中 **16 例失败**（settings 故障链 ×8、`/nofinite`/`/nanmix`/随机混合 ×4、线程注入 ×3、日志降级 ×1——分别精确命中 R3-M-01/02/03/04 的证据面）；`git stash pop` 恢复后全绿。
- 全量回归：`pytest tests/` **405 passed / 1 skipped / 1 xfailed**，连续两轮一致（360.15s / 369.45s，407 采集含 2 个非测试模块）。
- 针对性回归：修复区 4 文件（fault_fs / fault_thread / degraded / fault_net）重跑全绿。
- ruff（E/F/W/I/UP/B）：gold_pet 全包 + tests + cli.py + tools **All checks passed**。
- 测试文件（7 个，全部落盘、固定种子、可重复运行）：tests/test_monkey3_fault_fs.py（7）、test_monkey3_fault_net.py（9）、test_monkey3_fault_thread.py（11）、test_monkey3_interrupt.py（7）、test_monkey3_clock.py（10）、test_monkey3_mimosa.py（10）、test_monkey3_degraded.py（9）。
- 修改的源文件：
  - gold_pet/price.py（R3-M-01）
  - gold_pet/control_server.py（R3-M-02）
  - gold_pet/config.py（R3-M-03）
  - gold_pet/main.py（R3-M-04）
  - gold_pet/ui.py（_start_control QThread 失败兜底）
  - tests/test_ui.py、tests/test_monkey_ui.py（测试基建）
- 一次性探测脚本（_probe_r3_a/b/c.py）与临时产物（_r3_base.xml / _r3_full.xml 等）已按约定删除。

## 6. 遗留项与建议（非缺陷）

1. **H7 条件记录**（cnb_release_upload.py PUT 预签名 URL）：上游 API 响应被控时文件可被 PUT 到任意外部地址。发布工具仅开发者本机运行、需显式提供 CI token，风险面低；建议后续在 `put_file` 加 host 白名单（如 `*.cnb.cool` / OSS 域名）即可闭合。
2. **Notifier.record 的磁盘写失败仍不吞 OSError**（原子写失败时 `_save` 抛 OSError）：生产单写者语义下「记一条提醒」失败炸穿 UI 槽属可接受（提醒非关键路径，且写失败本身是磁盘级故障的显式信号）；如需完全静默口径，与 `Config.save`（R3-M-03）同治即可，本轮不改。
3. **`setup_logging` 降级仅控制台**（R3-M-04）：日志文件持续不可写时每次启动都会重新尝试构造 handler 并提示，无退避；磁盘级故障恢复前每启动一次刷一条 stderr，属期望行为。
4. **stop() 看门狗口径**：T3 的 5s 看门狗是测试侧防线；生产 `_stop_control` 目前无超时（R3-M-02 修复后死锁路径已消除）。若未来 ControlServer.stop 复杂度上升，建议加 2s 超时兜底。
5. 一轮遗留 #1（_MAX_DRAIN_BYTES 慢滴灌）、#3（pos 允许 bool）维持原状；R2-M-07（mcp 库 stdio 会话打死，xfail 固化）维持原状。
