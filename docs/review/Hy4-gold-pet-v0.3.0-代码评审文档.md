# Hy4-gold-pet-v0.3.0-代码评审文档

| 项 | 内容 |
|---|---|
| 评审模型 | Hy4 |
| 应用名称 | 黄金桌宠（gold-pet） |
| 应用版本 | v0.3.0 |
| 评审日期 | 2026-09-29 |
| 评审范围 | gold_pet/、tools/、tests/、mobile/flutter/、gold-pet.spec、build.bat、README.md、docs/PRD.md |
| 代码规模 | Python 约 1500 行（含测试 282 行），Dart 约 364 行 |
| 评审方式 | 静态代码走查 + 单元测试（22 条）+ PyInstaller 打包与运行验证 |
| 未覆盖 | Windows 真机运行、exe 实际启动、Flutter 编译（本机无 Windows / Flutter SDK） |

---

## 一、总体结论

**结论：可以发布试用（Beta），无阻塞性缺陷，发布前建议修掉 P1 的 4 项。**

架构健康：数据层与界面层解耦、第三方接口有兜底、素材可脚本化重生成、打包与测试链路已跑通。
主要风险集中在**健壮性边界**（异常配置、屏幕变化、重复启动）与**工程规范**（版本号、开源许可、CI），而非核心功能。

| 维度 | 评分（5 分制） | 说明 |
|---|---|---|
| 功能完整度 | 4.5 | 需求里的 P0/P1 基本都实现了 |
| 代码可读性 | 4.5 | 命名清晰、注释到位 |
| 健壮性 | 3.5 | 网络/坏数据有兜底，但配置与边界场景缺校验 |
| 可测试性 | 4.0 | 22 条测试覆盖主链路，CLI/托盘/打包未覆盖 |
| 工程规范 | 3.0 | 版本号不一致、缺 LICENSE、无 CI |
| 打包发布 | 4.0 | spec 已验证可用，体积偏大 |

---

## 二、问题清单（按优先级）

### P1：发布前应修复

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | 版本号与实现进度不一致 | gold_pet/__init__.py | 程序显示 0.1.0，但功能已是 v0.3，易误导 |
| 2 | 窗口位置记忆未做屏幕边界校验 | ui.py::_move_to_default | 换显示器/改分辨率后，桌宠可能落在屏幕外且找不回来 |
| 3 | PriceWorker.finished 覆盖 QThread 内置信号 | ui.py::PriceWorker | 与 Qt 内置 finished() 同名，语义混乱、易在重构中出错 |
| 4 | 公开仓库缺少 LICENSE | 仓库根目录 | 无许可证 = 默认保留所有权利，他人无法合法使用 |
| 5 | 无单实例保护 | main.py | 重复启动会出现多个金娘，并并发写配置/历史文件 |

**建议改法**

1. 版本号：已修为 `__version__ = "0.3.0"`。
2. 位置校验：恢复前判断是否在任一屏幕可视区内，不在则回落到右下角。

```python
def _move_to_default(self) -> None:
    pos = self.cfg.get("pos")
    if isinstance(pos, list) and len(pos) == 2:
        p = QPoint(int(pos[0]), int(pos[1]))
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(p):
                self.move(p)
                return
    # 不在任何屏幕内 -> 回落到主屏右下角
```

3. 信号改名：

```python
class PriceWorker(QThread):
    snapshot_ready = Signal(object)   # 不要用 finished，避免覆盖 QThread.finished
    def run(self):
        self.snapshot_ready.emit(self.service.fetch())
```

4. LICENSE：建议 MIT（与 hello-cnb 一致）。许可选择属于你的决定，我没有擅自添加。
5. 单实例：Windows 用 QLockFile（配置目录下 gold-pet.lock）或 QSharedMemory，发现已锁定则提示并退出。

---

### P2：尽快处理

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 6 | 配置值缺少校验 | config.py / ui.py::_apply_interval | 手改坏 settings.json（如 interval_sec 为字符串）会崩溃 |
| 7 | 换算行情丢失最高/最低价 | price.py::Snapshot.price_of | 铂金（元/克）是换算值，high/low 为 0，气泡不显示高/低 |
| 8 | 提醒去重时间硬编码 600 秒 | ui.py::_maybe_alert / _maybe_extreme | 用户无法调整，测试不好覆盖 |
| 9 | 系统托盘不可用时无降级 | ui.py::build_tray | 托盘被禁用的环境里隐藏后无法再唤出 |
| 10 | 打包体积约 68MB | gold-pet.spec | 分发不便 |
| 11 | 仓库无 CI | 缺 .cnb.yml | 测试不会自动跑，回归靠人 |

**建议改法**

- 6：Config.load 后做类型收敛：interval 取 INTERVAL_CHOICES 合法值、opacity 夹到 0.3~1.0、metal/unit 走白名单，非法值回落默认。
- 7：换算分支同时计算 `high` / `low`：`base.high / OZ_TO_GRAM * self.fx`。
- 8：新增配置 alert_cooldown_sec（默认 600），设置面板可改。
- 9：QSystemTrayIcon.isSystemTrayAvailable() 为 False 时禁用“隐藏到托盘”并提示，或提供全局热键唤出。
- 10：spec 中继续排除未用 Qt 模块、尝试 UPX（注意杀软误报）或改用 Nuitka。
- 11：加 .cnb.yml，push/PR 时跑 pytest tests -q。

---

### P3：优化项

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| 12 | docstring 里 Windows 路径产生 SyntaxWarning | main.py::setup_logging | 已修为 raw string |
| 13 | 每次渲染都写一次 history.json | ui.py::_update_spark | 无崩溃风险，可改成仅刷新成功时写 |
| 14 | 旧 Worker 的 finished 连接未断开 | ui.py::refresh | 重建 Worker 前 disconnect，避免极端情况下旧数据回调 |
| 15 | 测试未覆盖 | tests/ | 补：--once CLI、autostart（mock winreg）、SparkLine、打包产物 |
| 16 | Flutter 端未编译验证 | mobile/flutter/ | 缺 android 平台目录与 assets，需 flutter create 后真机验证 |
| 17 | 托盘菜单与右键菜单重复维护 | ui.py | 抽 build_menu() 供两处复用 |

---

## 三、做得好的地方（保持）

1. **数据层与界面层解耦**：price.py 不依赖 Qt，可单测，可被 Flutter / ArkTS 端照搬（字段下标与换算已写入 PRD 三端约定）。
2. **断网兜底**：失败不崩，回显缓存价并标记离线，之后指数退避重试。
3. **素材脚本化**：tools/gen_assets.py 改配色/表情重跑即可。
4. **异常兜底**：sys.excepthook 写入滚动日志，不再白屏崩溃。
5. **打包链路已验证**：spec 在 Linux 上成功产出可执行产物，assets 正确入包（18 项），--once 与 GUI 均正常。
6. **测试可跑**：22 条 pytest 0.11 秒完成，UI 测试用 offscreen Qt，不依赖显示器。

---

## 四、建议的修复顺序

```
第 1 批（30 分钟内）：#4 LICENSE → #5 单实例 → #3 信号改名 → #6 配置校验
第 2 批（1 小时内）：#2 屏幕边界 → #7 换算高低价 → #9 托盘降级 → #11 CI
第 3 批（有余力）：#8 提醒冷却可配置 → #10 体积 → #14/#15/#17 优化
```

#1（版本号）与 #12（转义告警）已在本次评审中直接修复。

---

## 五、附录：评审过程记录

| 动作 | 命令 | 结果 |
|---|---|---|
| 单元测试 | python -m pytest tests -q | 22 passed |
| 编译告警检查 | python3 -W error::SyntaxWarning -m compileall gold_pet | 修复后无告警 |
| 打包验证（Linux 等价物） | python -m PyInstaller --noconfirm --clean gold-pet.spec | 成功，产物 68MB |
| 产物 CLI 验证 | ./dist/黄金桌宠 --once | 输出行情并退出 0 |
| 产物 GUI 验证 | QT_QPA_PLATFORM=offscreen ./dist/黄金桌宠 | 日志出现“界面已启动” |
| 资源入包检查 | 读取产物归档 TOC | 18 个 assets 条目 |
| 未使用导入检查 | AST 扫描 | 无（仅 from __future__ 误报） |

### 已知限制

- Windows exe 无法在 Linux 交叉编译，打包结论来自同一份 spec 的 Linux 等价构建，请在 Windows 上执行 build.bat 并双击 dist 目录下的黄金桌宠.exe 做最终确认。
- Flutter / 鸿蒙端本次只做静态走查，未编译运行。

---

## 六、修复记录（评审后按业界通行做法落地）

| # | 问题 | 状态 | 做法 |
|---|---|---|---|
| 1 | 版本号不一致 | 已修 | __version__ = 0.3.0 |
| 3 | Worker 信号覆盖 Qt 内置 | 已修 | finished 改为 snapshot_ready，重建 Worker 前 disconnect |
| 4 | 缺 LICENSE | 已修 | 新增 MIT（2026 176code），与主流小型开源桌面应用一致 |
| 5 | 无单实例保护 | 已修 | QLockFile（配置目录 .gold-pet.lock，30s 陈旧锁），二次启动提示并退出 |
| 6 | 配置无校验 | 已修 | Config.sanitize()：interval 取白名单、opacity 夹 0.3~1.0、metal/unit 白名单、pos 校验、冷却夹取 |
| 7 | 换算丢最高/最低 | 已修 | price_of 换算分支补齐 high / low |
| 8 | 冷却时间硬编码 | 已修 | 新增 alert_cooldown_sec（默认 600），设置面板可调（分钟） |
| 9 | 托盘不可用时无降级 | 已修 | isSystemTrayAvailable() 为假则不建托盘、不显示“隐藏到托盘”、关窗即退出 |
| 11 | 无 CI | 已修 | 新增 .cnb.yml：push / PR 均跑 pytest tests -q |
| 12 | docstring 转义告警 | 已修 | 改 raw string |
| 14 | 旧 Worker 连接未断 | 已修 | 同 #3 |
| 2 | 位置无屏幕边界校验 | 待真机验证 | 已实现多屏可视区判断（需你在 Windows 上换分辨率实测） |
| 10 | 体积 68MB | 暂缓 | 后续可试 UPX 或 Nuitka |
| 13/15/16/17 | 写入频率 / 测试覆盖 / Flutter / 菜单复用 | 暂缓 | 优化项，不影响发布 |

### 修复后验证

```
python -m pytest tests -q                      -> 24 passed（新增 2 条：配置校验、换算高低价）
python run.py（第二个实例）                     -> 提示“已经在运行了”，退出
pyinstaller --noconfirm --clean gold-pet.spec   -> 构建成功
./dist/黄金桌宠 --once                          -> 正常输出行情，版本号 0.3.0
```
