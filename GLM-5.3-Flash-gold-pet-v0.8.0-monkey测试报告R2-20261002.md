# gold-pet v0.8.0 Monkey 测试报告 R2（2026-10-02/03，第二轮）

执行人：GLM-5.3-Flash（资深测试工程师角色）
项目：gold-pet v0.8.0（Python 3.13 + PySide6，路径 `C:\Users\tj-435\WorkBuddy\2026-09-30-09-31-13\gold-pet`）
入场均值基线：`pytest tests/` **266 passed / 1 skipped**（一轮收尾值，已确认）
最终结果：**298 passed / 1 skipped / 1 xfailed，零回退**（基线 266 全保留 + 新增 32 个 R2 monkey 测试），ruff（gold_pet + tests + cli.py + tools/pet_mcp_server.py）All checks passed。连续两轮全量运行结果一致。
零新增依赖：语料/并发/fuzz 全部标准库 + 项目测试既有 httpx + venv 既有的 mcp 包（一轮已存在的运行依赖）。

---

## 0. 与一轮的差异点

| 维度 | 一轮 | 二轮（本轮） |
|------|------|--------------|
| 种子 | 20261002 ~ 20261007 | **20261011 ~ 20261016**（全部不重复） |
| 角度 | 单请求脏 payload 轰炸（HTTP/文件解析面） | **打一轮修复代码本身的绕过变体 + 并发 + 状态机序列 + 资源累积 + JSON 规范边角 + MCP stdio（一轮空白面）** |
| 攻击已知修复 | 无 | M-01/M-02/M-03/M-04/M-05/M-07/M-08 逐个构造绕过变体（全部得手至少一处，见 R2-M-01/02/03/04） |
| 并发 | 无 | HistoryStore 10 线程、control_server 8 线程混打端点、save_cache/load_cache 4写4读交叠 |
| 状态机 | 单次 evaluate/from_dict | 20×500 步随机交错 + 内存/文件逐步一致性 oracle |
| MCP | 只测 HTTP 面 | stdio 深度 fuzz（握手后灌垃圾/10MB 行/非法 UTF-8/畸形 id/并发 5 进程） |

## 1. 目标清单（6）

| # | 目标 | 测试文件 | 种子 | 迭代数（约） |
|---|------|----------|------|--------------|
| T1 | 打一轮修复点：safe 转换器变体（bytes/Decimal/大整数）/ say 截断边界（孤代理对）/ change_pct=inf / CLI --json 合法性 | tests/test_monkey2_fixed_points.py | 20261011（+1/+2/+3） | 2313 |
| T2 | 并发面：HistoryStore 多线程 append、并发写期间读方、save_cache/load_cache 交叠、control_server 8 线程混打 | tests/test_monkey2_concurrency.py | 20261012（+1） | 5610 |
| T3 | 状态机序列：Notifier 20×500 步随机交错（evaluate/record/clear/reset/配置突变/DND 翻转）+ DND 全分钟网格 | tests/test_monkey2_notifier_fsm.py | 20261013（+1） | 13440 |
| T4 | 资源累积：append 长跑上限语义、10000 次同分钟去重（探测全量）/3000（套件）、record 1000 条、/say 1000 + /status 250 HTTP 长跑 | tests/test_monkey2_resource.py | 20261014 | 5850 |
| T5 | JSON 规范边角：重复键/__proto__ 键/-0/1e999/BOM/\u0000/非法 \x 转义/嵌套 2000 与 20000 层/400 位大整数 × 全部加载器 + 发现文件校验矩阵 | tests/test_monkey2_json_spec.py | 20261015 | 1040 |
| T6 | MCP stdio 桥 fuzz：握手后垃圾行/非法 UTF-8/10MB 单行/2 万层 params/畸形 id/合法工具错参/5 并发桥进程 | tests/test_monkey2_mcp_stdio.py | 20261016 | 13 个进程级场景（含 8 个 xfail 固化项） |
| | **合计** | | | **≈3.8 万次迭代操作** |

## 2. 方法

- **固定种子**：各文件模块头声明（20261011 ~ 20261016），`random.Random(seed)` 实例化，可重复运行。
- **oracle**（与一轮同源，按目标加严）：
  1. 不崩溃、不挂起（HTTP 单请求 ≤2s；MCP 合法请求 ≤6s、握手 ≤8s）；
  2. 结构化响应/退出码约定（CLI 退出码 ∈ {0,2,3,4}）；
  3. 状态文件逐步一致：Notifier 内存 `_history` == alert_history.json（每步断言）；
  4. 落盘恒为合法 JSON（`parse_constant` 钩子检测 NaN/Infinity 字面量）；
  5. 并发：动作计数守恒、丢更新归零、读者永不看到撕裂文件；
  6. 资源：点数/条数恒 ≤ 上限、文件尺寸有界、tracemalloc 增量有界。
- **真服务实测**：control_server 用 `build_server` 真起 ThreadingHTTPServer；MCP 用真实子进程 stdio 泵（initialize 握手 → 灌垃圾 → ping 探活）；UI 用真实 PetWindow（offscreen）端到端验证 /move 槽。
- **隔离**：全部 monkeypatch 到 tmp_path，不触碰真实 %APPDATA%\GoldPet。一次性探测脚本（_probe_r2_*.py、_probe_r2_tmp/）已用后删除。

## 3. 发现清单（7 项：修复 6，遗留 1）

| 编号 | 级别 | 目标 | 根因 | 证据（轰炸表现） | 修复 | 验证 |
|------|------|------|------|------------------|------|------|
| R2-M-01 | **P1** | config/_as_float、price/_num、price/_valid_point、notifier/_safe_float（一轮 M-03/M-04/M-05/M-07 的修复点） | 一轮修复只接 `except (TypeError, ValueError)`，而 json.loads 对 **400 位以上整数字面量**产出任意精度 int，`float(10**400)` 抛的是 **OverflowError**（属 ArithmeticError，不是 ValueError） | `{"interval_sec": 10^400}` → Config.load 崩、**程序拒启动**；history.json 含 400 位 t → HistoryStore.load 炸穿**每次刷新**；`filter_series([{"t":1,"p":10^400}], "all")` 炸穿渲染链；脏缓存 → load_cache 炸 → CLI 裸 traceback | 四处 except 元组补 `OverflowError`（config.py / price.py ×2 / notifier.py） | 500 次随机大整数（400/1024/4000 位）× 全加载链全绿；定向 5 组复现用例转绿 |
| R2-M-02 | **P1** | ui._clamp_to_screen（一轮 M-01 的 _to_coord 修复不挡量级） | `_to_coord` 放行任意 int（含 10^400），`_control_move` 相加后传入 `_clamp_to_screen`，函数入口 `probe = QPoint(x, y)` —— QPoint 只吃 int32，`QPoint(2**31)` 即抛 OverflowError，UI 线程槽未捕获 | `POST /move {"dx": 10**12}`（公网不可达但本机 HTTP API 可达）→ UI 槽 OverflowError，窗口不挪、traceback 刷屏，稳定复现 | `_clamp_to_screen` 入口把 x/y 夹取到 [-2^31, 2^31-1]（覆盖 dx+window.x() 相加溢出；对正常像素级坐标无感） | 真实 PetWindow 端到端：`{"dx":10**12}` / `{"x":10**400}` / `{"dx":2**31}` 全部安全挪回屏内 |
| R2-M-03 | **P1** | Config.load / HistoryStore.load / PriceService.load_cache / Notifier._ensure_loaded / cli._load_json（一轮 M-02 只修了 HTTP 面） | 超深嵌套 JSON（2 万层）让 json.loads 抛 **RecursionError**（非 ValueError 子类），文件加载器的 except 元组没接 | 2 万层 settings.json → **程序拒启动**；2 万层 history.json / alert_history.json / last_price.json → 刷新链/加载链炸穿；CLI `history` 裸 traceback 退出码违约 | 五处 except 元组补 `RecursionError`（config.py / price.py ×2 / notifier.py / cli.py，另 control_server.read_control_info 与 cli._discover 一并加固） | 2 万层文件 × 5 个加载器全部安全回落（默认配置 / 空历史 / None / CliError exit 4） |
| R2-M-04 | **P2** | Quote.change / change_pct 属性（绕过一轮 M-07 的「CLI --json 恒为合法 JSON」） | 属性层无有限性保护：prev_close=1e-308 时 `change/prev_close*100` → **inf**；price/prev_close 异号巨值时差值溢出 | 缓存 `{"price":1.0,"prev_close":1e-308}` → `cli price --json` 输出 `"change_pct": Infinity`——**非法 JSON**（parse_constant 钩子实锤） | 两个属性对非有限结果收敛 0.0（转换/相减/相除全程 OverflowError 容错）；顺带修复 format_price(-0.0) 显示 "-0.000" 的 P3 cosmetic | 500 组 (price, prev_close) 随机组合（含 1e-320/1e308/±10^400）：json.dumps(allow_nan=False) 恒通过；CLI 端到端 --json 恒合法 |
| R2-M-05 | **P2** | HistoryStore.append（10 线程×100 实测）、PriceService.save_cache、Notifier._save | append 是「读-改-写」整个文件且无锁：并发下**丢更新**（探测 10 键只剩 1~3 键）；write_text 非原子，跨线程/进程读写交错可看到**半截 JSON** | 10 线程并发 append 后 10 个序列只剩 1~3 个；并发写期间读方循环 json.loads 有撕裂风险（Windows 上还叠加 replace 瞬间 delete-pending 的 PermissionError 窗口） | 新增 `_atomic_write_text()`（**唯一临时文件名**（pid+线程 id+序号）+ os.replace，写坏自动清理）；HistoryStore 加 `threading.Lock` 全程持锁；save_cache/_save 换原子写 | 修复后 10 线程×40 append：10 键 × 40 点**零丢失**；4写4读×250 轮 load_cache 恒完整；并发 record 文件逐步合法、join 后磁盘==内存 |
| R2-M-06 | **P3** | control_server.read_control_info、tools/pet_mcp_server._discover（**一轮遗留 #2 兑现**） | 一轮 M-08 只加固了 CLI `_discover`，同源的两处发现文件读取器仍不校验 port/token 类型与范围 | control.json `{"port": ["99999"]}` → read_control_info 原样透传；MCP 桥 `int([99999])` → 裸 **TypeError/ValueError** 而非友好 RuntimeError | read_control_info 对脏 port/token 返回 None（port 必须 int 非 bool 且 1~65535、token 非空 str）；桥 _discover 同口径校验，坏值抛友好 RuntimeError；两处 except 补 RecursionError | 9 组校验矩阵（list/str/越界/bool/空 token/2 万层深）× 两处读取器全部按约定拒绝/放行 |
| R2-M-07 | **P2（遗留，不修）** | tools/pet_mcp_server.py（mcp 1.30 库 stdio transport） | **第三方 mcp 库域**：畸形入站请求（未知方法 / tools/call params=null 或 name=123 / **畸形 id 类型**）让库内响应任务打死——进程存活但会话此后对一切合法请求**永不应答**（stderr 为 pydantic literal_error 噪声后挂起；单条有时可存活、组合灌入稳定复现，非确定性） | 握手后灌 `{"method":"no/such"}` 等 8 条，12s 后合法 ping 仍无响应，进程 poll() 为 None；垃圾行/非法 UTF-8/10MB 单行/2 万层 params/合法工具错参则**均存活**（5 并发桥进程全绿） | 项目侧无干净的桥内缓解（FastMCP 无入站错误钩子）；以 `xfail(strict=False)` 测试固化缺陷现状 | tests/test_monkey2_mcp_stdio.py::test_r2_mcp_malformed_requests_may_wedge_session（xfail 即当前真实行为）；建议：升级 mcp 库 / 宿主侧调用超时重启兜底 |

**附带修复（不单列编号）**：`format_price(-0.0)` 显示 "-0.000" → 归一为 "0.000"（P3 cosmetic，随 R2-M-04 同文件修复）。

**级别统计**：P0 = 0；P1 = 3（R2-M-01、R2-M-02、R2-M-03）；P2 = 3（R2-M-04、R2-M-05、R2-M-07）+ P3 = 1（R2-M-06）。
**修复数**：6 / 7（R2-M-07 为第三方库域，固化为 xfail + 升级建议，列遗留）。
**遗留数**：1（R2-M-07）+ 2 条接受的局限（见 §6）。

## 4. 断言调整说明（测试预期过严/平台语义项）

1. **Windows os.replace 的 delete-pending 窗口**：原子 replace 瞬间，并发读者可能拿到 PermissionError。属文件系统语义而非撕裂写坏（内容永远完整），测试读循环按 OSError 容忍跳过；生产调用方（load/load_cache/_ensure_loaded/CLI）既有 OSError 容错路径天然消化。
2. **httpx 的 json= 无法发送孤代理对**（ensure_ascii=False + utf-8 编码即炸，客户端侧限制）：/say 孤代理对语料改用手工 `json.dumps(ensure_ascii=True)` 编码发送——`\udXXX` 转义正是服务端必须消化的合法 JSON 输入。
3. **10000 次同分钟 append 全量版**在探测中运行通过两轮（106s/160s，恒 1 点、文件不增长）；套件内取 3000 次控制时长，语义断言不变（文件头注释说明）。
4. **_rnd_huge_int 的位宽口径**：64~256 位整数在 float 量程内属合法值（保留是正确行为）；「必溢出」断言用 ≥400 位语料。
5. **畸形 id 单发 vs 组合**：mcp 库对畸形 id 的会话打死是非确定性的（单发常存活、组合稳定复现）——xfail 场景按组合语料固化。

## 5. 统计与验证

- 发现：7 项（P0:0 / P1:3 / P2:3 / P3:1，其中 1 项 P2 列遗留）；修复：6 项；遗留：1 项。
- 全量回归：**298 passed / 1 skipped / 1 xfailed**（基线 266 passed / 1 skipped 零回退；连续两轮全量一致）。
- ruff（E/F/W/I/UP/B）：gold_pet 全包 + tests + cli.py + tools/pet_mcp_server.py **All checks passed**。
- 测试文件（全部落盘、固定种子、可重复运行）：
  - tests/test_monkey2_fixed_points.py（14 例）
  - tests/test_monkey2_concurrency.py（5 例）
  - tests/test_monkey2_notifier_fsm.py（3 例）
  - tests/test_monkey2_resource.py（4 例）
  - tests/test_monkey2_json_spec.py（6 例）
  - tests/test_monkey2_mcp_stdio.py（7 例，含 1 个 xfail）
- 本轮修改的源文件：
  - gold_pet/config.py（R2-M-01、R2-M-03）
  - gold_pet/price.py（R2-M-01、R2-M-03、R2-M-04、R2-M-05：Quote 属性收敛 / _atomic_write_text / append 锁）
  - gold_pet/notifier.py（R2-M-01、R2-M-03、R2-M-05）
  - gold_pet/control_server.py（R2-M-03、R2-M-06）
  - gold_pet/ui.py（R2-M-02：_clamp_to_screen int32 夹取）
  - cli.py（R2-M-03、R2-M-06 口径一致）
  - tools/pet_mcp_server.py（R2-M-06）

## 6. 遗留项与接受的局限（非本轮修复）

1. **R2-M-07（P2）**：mcp 1.30 库 stdio 会话可被畸形入站请求打死（详见 §3）。建议关注 mcp 库更新，升级后 xfail 用例会自动转 XPASS 提示摘牌；宿主侧建议对桥工具调用加超时重启。
2. `_safe_float` / `_num` / `_as_float` 对**自定义 `__float__` 抛任意异常**（如 RuntimeError）的 Python 对象仍会穿透——JSON/配置文件路径不可达（只产 str/int/float/bool/None/list/dict），接受的局限；如未来有程序内构造路径再收窄为 `except Exception`。
3. Notifier.record 仍是单写者语义（生产全部在 UI 线程调用），未加锁；本轮以「唯一临时文件 + 原子替换」保证文件恒完整，多线程 record 的内存计数语义未承诺（测试只断言文件合法与上限）。
4. 一轮遗留 #1（_MAX_DRAIN_BYTES 慢滴灌）、#3（pos 允许 bool）维持原状，风险评级不变。
