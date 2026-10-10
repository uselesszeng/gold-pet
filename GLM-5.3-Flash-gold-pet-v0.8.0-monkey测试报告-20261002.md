# gold-pet v0.8.0 Monkey 测试报告（2026-10-02）

执行人：GLM-5.3-Flash（资深测试工程师角色）
项目：gold-pet v0.8.0（Python 3.13 + PySide6，路径 `C:\Users\tj-435\WorkBuddy\2026-09-30-09-31-13\gold-pet`）
测试基线：`pytest tests/` 247 passed / 1 skipped（已确认）
最终结果：**266 passed / 1 skipped，零回退**（基线 247 全保留 + 新增 19 个 monkey 测试），ruff 全绿。
零新增依赖：语料生成仅用标准库 `random / json / string`；HTTP 客户端复用项目测试既有的 `httpx`。

---

## 1. 目标清单（6 + 1）

| # | 目标 | 测试文件 | 种子 | 迭代数 |
|---|------|----------|------|--------|
| T1 | control_server.py HTTP 端点（/status /say /play /pat /tease /move + 未知路径/非法 token） | tests/test_monkey_control_server.py | 20261002 | 512（500 随机 + 12 定向重型） |
| T2 | price.py HistoryStore.load / append | tests/test_monkey_price_history.py | 20261003（load）/ 20261004（append）/ +2（roundtrip） | 1060（500 + 500 + 60） |
| T3 | config.py 配置加载与 sanitize | tests/test_monkey_config_bubble.py | 20261004 | 505（500 + 5 定向回归） |
| T4 | bubble.render_line_html | tests/test_monkey_config_bubble.py | 20261004（+1） | 501（500 + 1 性能） |
| T5 | notifier.filter_series / Alert.from_dict / in_quiet_hours（+ evaluate 端到端） | tests/test_monkey_notifier.py | 20261005（+1/+2/+3） | 1560（500×3 + 60） |
| T6 | cli.py 参数解析与脏数据文件 | tests/test_monkey_cli.py | 20261006（+1/+2） | 691（500 + 120 + 60 + 3 + 8） |
| T7 | 补充：ui.py 迷你走势图脏序列（M-11 回归） | tests/test_monkey_ui.py | 20261007 | 200 |
| | **合计** | | | **5029** |

共享语料生成器：tests/_monkey_common.py（80% 非法 / 20% 合法边界；固定种子可重复运行）。

## 2. 方法

- **固定随机种子**：各文件模块头声明（20261002 / 20261003 / 20261004 / 20261005 / 20261006 / 20261007），`random.Random(seed)` 实例化，套件可无限次重复运行且语料序列一致。
- **语料构成（80% 非法 / 20% 合法边界）**：
  - 非法值池：None / bool / NaN / Infinity / ±10^308 / ±2^63 / 10^50 超大数、垃圾字符串（"0x10"、"1e400"、"nan"）、空串与纯空白、`\x00 \x01 \x07 \x1b` 控制字符、`<script>` / `<img onerror>` / javascript: 注入向量、HTML 实体、emoji 与全角混排、`\u202e` 双向控制符、孤代理对 `\ud800`、不可哈希结构（list/dict）、深层嵌套 JSON（最深 2 万层）、重复键、1MB 超大包、BOM / GBK / WTF-8 非法编码字节、截断 JSON。
  - 合法边界池：/say 限长 499/500/501 字、坐标 0/±2^31、interval 15/30/60/300/900、opacity 0.3/1.0、dnd "00:00"/"23:59"、10 万点超长序列（限频 5 次）。
- **oracle**：
  1. 不崩溃、不挂起：单次调用/单请求 ≤2s（httpx timeout=2 + 计时断言）；
  2. 每个请求都有 JSON 响应（`ok` 为 bool，状态码 ∈ {200,400,401,404,413}）；
  3. 无未处理异常逃逸到 pytest 之外（CLI 仅允许 argparse 的 SystemExit(2)）;
  4. 状态文件（history.json / settings.json / alert_history.json）写盘后必须能被 `json.loads` 解析且不含 NaN/Infinity 非法字面量（用 `json.loads(..., parse_constant=...)` 钩子精确检测，避免键名 "NaN" 子串误报）。
- **真服务实测**：T1 用 tests/test_control_server.py 同款 `build_server` 夹具真实起 ThreadingHTTPServer，httpx 逐发；1MB 包用原始 socket 验证 413 JSON 响应。
- **隔离**：CLI/UI 测试全部 monkeypatch 到 tmp_path，不触碰真实 %APPDATA%\GoldPet。
- **一次性探测脚本**（_probe_monkey.py 等）已按约定用后删除。

## 3. 发现清单（11 项，全部修复）

| 编号 | 级别 | 目标 | 根因 | 证据（轰炸表现） | 修复 | 验证 |
|------|------|------|------|------------------|------|------|
| M-01 | P1 | control_server /move | `int(float("nan"))` 抛 ValueError、`int(float("inf"))` 抛 OverflowError，handler 线程未捕获 | `{"dx": NaN, "dy": 1}` → 连接被断开，客户端收不到任何 JSON（RemoteProtocolError） | control_server.py 新增 `_to_coord()`：非数值/bool/非有限值一律返回 None → 400 | 定向用例 5 组 NaN/Inf/超大数 payload 全部 ≤2s 收到 400 JSON |
| M-02 | P1 | control_server 全部 POST | 约 3200 层以上的嵌套 JSON 使 json.loads 抛 RecursionError（非 ValueError 子类），逃逸出 `_read_json` | 2 万层 `[[[[…]]]]` → RecursionError、断连、无响应 | `_read_json` 捕获元组追加 `RecursionError` → 400 | 深度 20000 定向用例收到 400 JSON |
| M-03 | P1 | config.py sanitize/load | 配置文件手写 NaN/Infinity 被 json.loads 解析为 float 后，`int(nan)` ValueError / `int(inf)` OverflowError 炸穿 `_as_int`；`_clamp` 对 NaN 不生效（opacity 变 NaN） | `{"interval_sec": NaN}` → Config.load 抛 ValueError，程序拒绝启动 | `_as_float` 增加非有限值回落默认值（math.isfinite） | 5 组 NaN/Inf 配置定向回归 + 500 次随机脏配置全绿；sanitize 后值域断言全过 |
| M-04 | P2 | price.py HistoryStore | `_valid_point` 只查 float() 可转，NaN/Inf 点被放行；`append` 也直接写 NaN 价 → json.dumps 产出 `NaN`/`Infinity` 字面量，状态文件变成非法 JSON | 文件文本含 `NaN`；重载后 NaN 点继续存活并扩散 | `_valid_point` 要求 t/p 均为有限值；`append` 对非有限价格不落盘 | 500 次 append + 60 轮 roundtrip：文件始终合法 JSON（parse_constant 校验） |
| M-05 | P2 | notifier.py | `_safe_float` 放行 NaN/Inf → filter_series 给走势图返回 NaN 点；Alert.at=NaN 排序/落盘污染 | `filter_series([{"t":1,"p":NaN}], "all")` 返回 [nan] | `_safe_float` 对非有限值返回 default（None → 坏点跳过 / 0.0 兜底） | 500 次 filter_series：输出全部有限；500 次 Alert.from_dict：at 全部有限 |
| M-06 | P2 | cli.py human 输出路径 | 脏数据文件里 t=1e30/-1e30/NaN/字符串 → `time.localtime` 抛 OverflowError/ValueError/TypeError；脏提醒列表混入非 dict 项 → `a.get` AttributeError；全部炸穿退出码约定 | `history.json` 含 t=1e30 时 `cli.py history`（非 --json）以裸 traceback 退出 | 新增 `_fmt_time()`（失败显示 "?"）+ 非 dict 项跳过 | 120 次脏文件 × 双输出路径 + 500 次随机 argv：退出码全部 ∈ {0,2,3,4} |
| M-07 | P2 | price.py Snapshot/Quote.from_dict（波及 CLI/PriceService.load_cache） | "quotes" 非 dict（list/int）→ AttributeError（load_cache 只捕 OSError/ValueError/KeyError/TypeError）；quote 字段类型错 → 后续 change/format_price TypeError；price=NaN → CLI --json 输出非法 JSON（NaN 字面量） | `{"quotes": [1,2]}` 缓存 → cmd_price/cmd_status 崩溃 | from_dict 全面结构+类型收敛：quotes 必须为 dict[dict]、数值字段经 `_num` 收敛且非有限报价剔除、fx/updated_at/ok/error 逐一收敛 | 500 次 load + CLI 脏缓存 120 次：不再崩；--json 输出恒为合法 JSON |
| M-08 | P2 | cli.py `_discover` | 发现文件 port 为非数字字符串/越界值时 `int(port)` 的 ValueError/TypeError 直接抛出 main | control.json `{"port": "abc"}` → 裸 traceback | port 必须 int（非 bool）且 1~65535、token 必须非空 str，否则按 runtime 失败 exit 4 | 60 次脏 control.json 全部安全失败 exit 4 |
| M-09 | P2 | control_server 401/404/413 短路路径 | 短路响应前不消费请求体；socket 带未读数据关闭时 Windows 发 RST，客户端间歇性连响应都收不到（WinError 10053） | 纯 POST /status ×200 在第 72 发复现 ReadError（fresh server、无超大包） | 新增 `_drain_body()`（限量 8MB）在 401/404/413 前排空请求体 | 修复后 POST 404 ×500、过载后 ×100 全部 0 失败；500 次随机轰炸全程无 10053 |
| M-10 | P1 | config.py sanitize | `d.get("metal") not in METAL_NAMES` 对 list/dict 类型值做哈希成员判断 → TypeError: unhashable type，Config.load 崩溃 | `{"metal": []}` → Config.load 抛 TypeError | metal/unit/spark_range/activity_level 先验 `isinstance(str)` 再做成员判断 | 500 次随机脏配置（含不可哈希值）全绿 |
| M-11 | P1 | ui.py `_update_spark` 回退分支 | 调用了未导入的 `_safe_float`（NameError）——历史序列在当前时间范围内无点、且原始序列含数值字符串价格时必现（ruff F821 静态走查发现，M-05 修复同族） | `values = [_safe_float(...) for p in series]` 处 NameError 崩穿渲染链路 | ui.py 导入补齐 `from .notifier import _safe_float` | 新增 tests/test_monkey_ui.py：200 次随机脏序列 × 真实 PetWindow 实例不炸 |

**级别统计**：P0 = 0；P1 = 5（M-01、M-02、M-03、M-10、M-11）；P2 = 6（M-04 ~ M-09）；P3 = 0。
**修复数**：11 / 11（全部最小化修复，无架构改动）。
**遗留数**：0。

## 4. 断言调整说明（测试预期过严项）

1. **>64KB 请求体的客户端断连属标准 413 语义**：服务端回 413 即关闭，客户端仍可能在写剩余 body 时收到传输层错误（WinError 10053）。这不是服务端缺陷。测试调整为：循环内超大包允许 TransportError 但必须验证服务仍存活（`_assert_server_alive`）；1MB 语义用原始 socket 发完整 body 直读 413 JSON 响应断言。
2. **状态文件 "NaN" 检测改用 parse_constant 钩子**：子串匹配会把键名（如 `键🐳|NaN`）误判为非法字面量。
3. **append 非有限价格跳过落盘**：此时"该键必须存在于文件"的断言过严，调整为仅对有限价格断言落盘一致性。
4. **filter_series 的 now 参数**：生产中来自时钟，语料取 ±10^6 秒扰动；荒谬量级（如 1e18）属 stdlib `time.localtime` 行为域，不纳入 oracle。
5. **`_valid_point` 允许缺 t/p（按 0.0 兜底）**是既有设计，load 结构断言相应使用 `.get(key, 0.0)`。

## 5. 统计与验证

- 发现：11 项（P0:0 / P1:5 / P2:6 / P3:0）；修复：11 项；遗留：0 项。
- 全量回归：修复前后各跑 `pytest tests/`：
  - 修复后最终：**266 passed / 1 skipped**（连续运行 3 次结果一致，可重复；基线 247 passed / 1 skipped 零回退）。
  - ruff（E/F/W/I/UP/B 规则）对全部改动文件：All checks passed。
- 测试文件（全部落盘、可重复运行）：
  - tests/_monkey_common.py（共享语料生成器，非收集目标）
  - tests/test_monkey_control_server.py
  - tests/test_monkey_price_history.py
  - tests/test_monkey_config_bubble.py
  - tests/test_monkey_notifier.py
  - tests/test_monkey_cli.py
  - tests/test_monkey_ui.py
- 修改的源文件：
  - gold_pet/control_server.py（M-01、M-02、M-09）
  - gold_pet/config.py（M-03、M-10）
  - gold_pet/price.py（M-04、M-07）
  - gold_pet/notifier.py（M-05）
  - cli.py（M-06、M-08）
  - gold_pet/ui.py（M-11）

## 6. 遗留项与建议（非缺陷）

1. `_MAX_DRAIN_BYTES = 8MB`：恶意客户端声明超大 Content-Length 但缓慢滴灌时，单个 handler 线程最长阻塞在 8MB 排空上；本服务仅绑 127.0.0.1 + token 鉴权，风险可接受。若未来开放绑定，建议改用 chunked 读取 + 超时。
2. `read_control_info()`（MCP 桥接用）对 port 的类型约束弱于修复后的 CLI `_discover`，建议后续对齐（本次未改，属工具链文件且不在打面清单内）。
3. Config.sanitize 中 `pos` 允许 `[True, False]`（bool 是 int 子类）通过校验，语义无害但可在下个版本统一收紧。
4. bubble.render_line_html 与 render_price_html 的 1MB 文本渲染实测 <2s，未发现安全问题（转义收口有效）；建议保持"所有外部文本必须经 render_line_html"的约定不破。
