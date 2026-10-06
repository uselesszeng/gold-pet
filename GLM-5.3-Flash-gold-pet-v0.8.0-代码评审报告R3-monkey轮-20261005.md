# gold-pet v0.8.0 · 代码评审报告 · R3 monkey 轮（20261005）

**评审对象**：R3 monkey 引入的全部未提交源码改动（git 84e6a52 全量 diff：5 个源文件 + 9 个测试文件）
**评审方式**：对抗式——假设修复代码本身有缺陷，逐项攻击修复路径（误拒合法输入 / 吞该抛异常 / 改下游契约 / 锁与原子写安全面 / 测试恒真）
**评审日期**：2026-10-05（评审执行 2026-10-06）
**回归验证**：`pytest tests/` 全量 exit=0（含 R1/R2 基线 316 例 + R3 新增）；ruff（E/F/W/I/UP/B）全绿；针对性 R3 7 套测试全绿

---

## 一、改动面

| 文件 | 改动 | 对应修复 |
|---|---|---|
| gold_pet/price.py | `_f` 捕获 OverflowError + 非有限值回落 default | R3-M-01 |
| gold_pet/control_server.py | `run()` 中 `_write_info()` 包 except OSError | R3-M-02 |
| gold_pet/config.py | `Config.save` 包 except OSError，降级为 warning | R3-M-03 |
| gold_pet/main.py | `setup_logging` 文件 handler 构造包 except OSError | R3-M-04 |
| gold_pet/ui.py | `_start_control` 对 `control.start()` 包 except（RuntimeError, OSError） | R3 附带 P3 |
| tests/test_ui.py / test_monkey_ui.py | `_window` 夹具统一 control_enabled=False | 测试基建 |
| tests/test_monkey3_*.py（7 个文件） | R3 新增测试 | — |

无未跟踪新增源码文件。

## 二、逐项对抗验证

### R3-M-01（price.py `_f`）— 通过
- `float("1e999")` → OverflowError 未捕获会被误吞（原本只捕 ValueError/IndexError）——R3 已补 `OverflowError`。
- 对抗输入验证：`float("nan")`、`float("inf")`、`float("1e999")` 全部产出非有限值，`math.isfinite` 过滤后回落 default。**合法输入不受影响**：正常数值 `897.15` 等通过 `math.isfinite` 原样返回。
- 测试 oracle：`test_r3_net_nonfinite_upstream_keeps_cache_strict` 断言缓存文件无 `Infinity`/`NaN` 字面量，且用 `parse_constant` 拒 NaN/Infinity 强断言——**非恒真**。

### R3-M-02（control_server.py `run()`）— 通过
- 根因核实：`_write_info` 抛 OSError 导致 `run()` 提前 return，`serve_forever` 从未运行，`stop()` 调 `server.shutdown()` 时 `__is_shut_down` 事件永不置位 → **确认永久挂死**。
- 修复后验证：`except OSError` 包住 `_write_info`，`serve_forever` 继续执行；`stop()` 正常返回。
- 测试 `test_r3_thread_info_write_failure_still_serves_and_stops`：看门狗 5s 内 `stop()` 必须返回，且真实 HTTP 200 可达——**非恒真**。
- 注释声称「stop() 里的 shutdown() 会永久阻塞」——与 Python 3.13 http.server 实现一致，因果链正确。

### R3-M-03（config.py `Config.save`）— 通过
- 根因核实：`Path.write_text` 抛 OSError 直接炸穿 UI 槽，`quit_app` 路径上 `QApplication.quit()` 永不执行——**确认主流程阻塞**。
- 修复后：`except OSError: logger.warning`，与 `save_cache`/`_save` 的 OSError 容错口径一致，不引入新契约（save 仍返回 None，调用方不变）。
- 测试 `test_r3_fs_atomic_write_faults_do_not_escape[settings]` + `readonly_target[settings]` + `recovery_after_fault[settings]` 均有内容断言，**非恒真**。

### R3-M-04（main.py `setup_logging`）— 通过
- 根因核实：`RotatingFileHandler` 构造即打开文件，目录被文件占位时 `NotADirectoryError` 在 `main()` 最前面抛出——**确认启动即崩**。
- 修复后：`except OSError` 降级为仅控制台，stderr 提示一次，`logging.basicConfig` 仍正常注册 StreamHandler——降级路径无副作用。
- 测试 `test_r3_degraded_logging_unwritable`：子进程验证 `degraded-ok` 输出 + returncode==0，**非恒真**。

### R3 附带（ui.py `_start_control`）— 通过
- `control.start()` 包 `except (RuntimeError, OSError)`，`self.control=None` 后 return——不炸 PetWindow 构造路径。
- 测试 `test_r3_thread_start_failure_degrades`：monkeypatch `ControlServer.start` 抛 RuntimeError，断言 `w.control is None`——**非恒真**。
- 注：catch 里 `logging.getLogger("gold_pet").warning` 在 UI 层直接取 logger，风格略不一致（其他文件在模块顶取），属 P3 可维护性问题，非缺陷。

## 三、安全面验证

| 项目 | 结论 |
|---|---|
| price.py `_f` 新增 `math.isfinite` 过滤 | 不引入新穿越面；NaN/Infinity 落盘是数据污染而非注入面，修复后正确 |
| control_server `_write_info` 容错 | 发现文件缺失只降级 MCP 桥自动发现，不影响安全模型（token 鉴权 + 127.0.0.1 绑定不变） |
| config.py `save` 容错 | 只记 warning 不改变数据流；`_data` 内存状态不变，下次写盘成功即恢复，无安全问题 |
| setup_logging 降级 | 无新路径穿越面 |

## 四、Mimosa 语料「未复现」结论质疑

| 假设 | 质疑 | 裁定 |
|---|---|---|
| H1/H2（random.choice 非安全随机） | 确实非安全上下文；test 有 400/500 次断言 | 经得起质疑 |
| H4（cli.py urlopen SSRF） | URL 唯一来源 control.json（本机），port 12 种脏值测试全 exit 4 不发请求；dirty port 测试含 `"8"`（字符串）和 `True`（bool），覆盖面完整 | 经得起质疑 |
| H5（price.py urlopen） | `SINA_URL` 是模块字面量常量，`fetch_raw` 签名无 URL 参数；test 直接断言 monkeypatch 后的 SINA_URL 值 | 经得起质疑 |
| H7（cnb put_file 跟随 API 响应） | 报告已记录为「设计内信任边界」，条件注明；本轮不单列缺陷 | 接受（已注明残余） |

## 五、发现问题

### P0（安全严重 / 数据破坏）
无。

### P1（主流程 / 高危）
无。R3-M-02 的 `stop()` 死锁修复因果链已核实正确。

### P2（边界 / 中等）
无。

### P3（质量）

1. **ui.py `_start_control` 的 logger 风格不一致**（ui.py:1017 附近）
   - 其他文件在模块顶层取 `logger = logging.getLogger("gold_pet")`，此处直接 `logging.getLogger("gold_pet").warning` 内联取。
   - 建议：统一为模块顶 logger，不影响功能。

2. **Config.save 的 docstring 声称「与 save_cache / Notifier._save / HistoryStore.append 的容错口径一致」，但 save_cache 有 RecursionError 捕获，Config.save 没有**（config.py:207）
   - 实际上 `json.dumps(self._data)` 中 `_data` 来自 `DEFAULTS` 加用户写入，不存在超深嵌套面，RecursionError 不可达，docstring 的「口径一致」描述在 RecursionError 维度有轻微不精确。
   - 建议：无需代码修改，在 docstring 加注释说明 Config._data 不可达 RecursionError 即可。

3. **control_server.py:293-294 的 R3-M-02 注释**
   - 注释写「serve_forever 从未运行，__is_shut_down 事件永不置位」——准确；但注释里「QThread 死亡后 self._server 仍挂着未开始服务的实例」的「死亡」措辞不够严谨（QThread 是结束而非崩溃死亡），建议改为「run() 提前返回」。不影响功能。

## 六、文档一致性（R3 报告 vs git diff）

| R3 报告声称 | git diff 核实 | 一致性 |
|---|---|---|
| R3-M-01：`_f` 补 OverflowError + math.isfinite | price.py 实际改动完全吻合 | 一致 |
| R3-M-02：`_write_info` 包 except OSError | control_server.py 实际改动吻合 | 一致 |
| R3-M-03：Config.save 包 except OSError | config.py 实际改动吻合 | 一致 |
| R3-M-04：setup_logging 文件 handler 构造包 except OSError | main.py 实际改动吻合 | 一致 |
| 测试基建：test_ui.py / test_monkey_ui.py 的 `_window` 加 `control_enabled=False` | 两个文件各 +3 行，内容吻合 | 一致 |
| 4 个 R3 修复全部落盘 | 5 个源文件 diff 全部验证 | 一致，无虚报 |

## 七、总体结论

- 4 个 P1/P2 修复（R3-M-01~04）因果链均核实正确，无正常路径副作用。
- Mimosa 8 条假设验证测试覆盖充分（含边界值），「未复现」结论经得起质疑。
- 测试资产无恒真断言，oracle 设计（看门狗 / byte 级 / parse_constant）合理。
- P3 记录 3 条（风格 / docstring 精确度 / 措辞），无需修改。
- **无 P0/P1，无需就地修复，回归测试全绿（exit=0）。**
