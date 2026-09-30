# 🐥 黄金桌宠（Gold Pet）

一只住在桌面右下角的「金娘」，**实时盯着金价**：涨了笑、跌了哭，点一下切换 国内元/克 ↔ 国际美元/盎司。

> 一期只做 **Windows**。Android / 鸿蒙 HAP 见 [docs/PRD.md](docs/PRD.md) 的二期、三期规划。

## 功能

| 功能 | 说明 |
|---|---|
| 🪙 桌宠窗口 | 无边框、透明背景、置顶、可拖动、位置记忆 |
| 🎞️ 状态动画 | 待机（呼吸+眨眼）/ 涨（笑）/ 跌（哭）/ 加载中 / 离线 |
| 💬 价格气泡 | 国内金价 元/克 + 涨跌额与涨跌幅 + 更新时间 |
| 🔄 自动刷新 | 默认 60 秒，可选 15/30/60/300/900 秒；双击立即刷新 |
| 🔔 到价提醒 | 涨跌幅超过阈值 → 气泡高亮 + 系统通知（同方向 10 分钟不重复） |
| 🪙 多品种 | 黄金 / 白银 / 铂金 一键切换（右键菜单或托盘） |
| 📈 迷你走势图 | 气泡下方实时折线，记录最近 120 个价格点 |
| 🗣️ 互动台词 | 到价提醒、手动点击时金娘会说话（4 秒后恢复价格） |
| 🖱️ 交互 | 单击切换单位 · 双击刷新 · 拖动移动 · 右键菜单 |
| 📌 托盘常驻 | 关窗=藏到托盘；托盘菜单可刷新/切换/设置/退出 |
| ⚙️ 设置 | 单位、刷新间隔、提醒阈值、不透明度、置顶、开机自启 |
| 🔌 离线兜底 | 断网显示「离线」+ 上次价格，不崩溃 |

## 快速开始（Windows）

```bat
:: 1. 安装依赖（只需一次）
pip install -r requirements.txt

:: 2. 运行（双击 run.bat 或 run.py 都行）
python run.py
```

打包成单个 exe：

```bat
build.bat
:: 产物：dist\黄金桌宠.exe
```

## 操作说明

| 操作 | 效果 |
|---|---|
| 左键单击 | **摸摸头**：眯眼笑 + 飘爱心 + 台词 |
| 左键长按（约 0.6 秒） | **撒娇**：眼神飘开 + 脸红 + 台词 |
| 左键双击 | 立即刷新行情 |
| 左键拖动 | 移动位置（自动记忆） |
| 右键 | 菜单：刷新 / 切换单位 / 切换品种 / 摸摸头 / 撒娇 / 说句话 / 置顶 / 气泡 / 走势图 / 设置 / 隐藏 / 退出 |

## 数据来源

| 品种 | 国内（元/克） | 国际（美元/盎司） |
|---|---|---|
| 黄金 | `gds_AUTD`（上金所 Au T+D） | `hf_XAU`（伦敦金） |
| 白银 | `gds_AGTD`（白银 T+D，元/千克 ÷ 1000） | `hf_XAG`（伦敦银） |
| 铂金 | 由美元价换算 | `hf_XPT`（纽约铂金） |

- 汇率：新浪财经 `fx_susdcny`
- 换算：`元/克 = 美元/盎司 ÷ 31.1034768 × 汇率`

均为免密钥公开接口；程序自带本地缓存，断网时显示上次价格。**行情仅供参考，不构成投资建议。**

## 目录结构

```
gold-pet/
├── docs/PRD.md          产品需求文档
├── gold_pet/            源码（config / price / notifier / tray_icon / ui / autostart / main）
├── assets/pet/*.png     动画帧（tools/gen_assets.py 生成）
├── assets/icon.ico      应用图标
├── tools/gen_assets.py  素材生成脚本（改配色/表情后重跑）
├── requirements.txt
├── run.bat              开发运行
└── build.bat            打包 exe
```

## 想改点东西？

| 想改什么 | 改哪里 |
|---|---|
| 金娘的样子/颜色/表情 | `tools/gen_assets.py`，改完 `python tools/gen_assets.py` |
| 默认单位、刷新间隔 | `gold_pet/config.py` 的 `DEFAULTS` 或设置面板 |
| 涨红跌绿换成涨绿跌红 | `gold_pet/ui.py` 的 `UP_COLOR` / `DOWN_COLOR` |
| 换数据源 | `gold_pet/price.py` 的 `SINA_URL` 与 `parse_snapshot` |

配置文件在 `%APPDATA%\GoldPet\settings.json`，可以直接手改后重启。

## 移动端（Flutter）

`mobile/flutter/` 是 Android / 移动端的骨架，数据层与 Windows 版同一套接口与换算：

```bash
cd mobile/flutter
flutter create --org com.tjcoding --project-name gold_pet --platforms=android,windows .
flutter pub get && flutter run
```

详见 [mobile/flutter/README.md](mobile/flutter/README.md)。

## 仓库

- CNB：https://cnb.cool/TJ-coding/gold-pet

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

覆盖：行情解析（三个品种、单位换算、缩放）、缓存与损坏兜底、历史去重与上限、
配置读写与素材完整性、提醒规则与勿扰时段、托盘数字图标渲染、
UI 渲染 / 单位与品种切换 / 摸头撒娇 / 台词恢复 / 离线状态 / 失败退避。

质量门禁（与 CI 一致，Windows 直接跑 `lint.bat`）：

```bash
python -m ruff check .
python -m mypy                 # 注意：用裸 mypy，files 白名单在 pyproject.toml
python -m pytest tests --cov=gold_pet --cov-report=term-missing
```

## 调试与日志

```bash
python run.py --once                 # 只打印一次行情，不启动界面
python run.py --metal silver --once  # 指定品种
python run.py --log-level DEBUG      # 调试日志
打包后：dist\黄金桌宠.exe --once
```

日志：`%APPDATA%\GoldPet\gold-pet.log`（滚动保留 2 份）；未捕获异常也会写入日志而不是弹窗崩溃。

## 打包

```bat
build.bat          :: 等价于 pyinstaller --noconfirm --clean gold-pet.spec
:: 产物：dist\黄金桌宠.exe（约 60-80MB，onefile、无控制台）
:: 精简：spec 里排除了 QtQml/QtWebEngine/Qt3D/QtMultimedia 等未使用模块
:: 资源：assets 整个目录随包打入，运行时从临时目录读取
```

## 开源许可

[MIT](LICENSE) —— 与主流小型开源桌面应用一致，可自由使用、修改、二次分发，保留版权声明即可。

## 常见问题

- **桌宠不见了**：换显示器/改分辨率后可能落在屏幕外，程序会自动回落到主屏右下角；
  若仍找不到，删除 %APPDATA%\GoldPet\settings.json 里的 pos 后重启。
- **重复启动**：只会有一个金娘，第二次启动会提示“已经在运行了”。
- **没有托盘**：部分环境禁用托盘，此时关窗即退出，菜单里不会出现“隐藏到托盘”。

## v0.5.1 修复

- **设置面板撑满屏幕**：此前窗口尺寸实测 1920×1152，占满整屏且底部按钮被推到屏幕外。
  现限制宽度并改用滚动区，窗口 430×620 居中显示，按钮固定在底部
- **桌宠贴边**：右下角定位在布局定型前计算，边距算错（实测仅 17px）→ 现推迟到尺寸生效后定位
- 新增 `tools/diag_window.py`（窗口几何诊断）与 `tools/grab_screen.py`（零依赖截屏）
- 测试 138 → 142 条，覆盖率 77.29% → 78.18%

## v0.5 新增

- **提醒中心**：涨跌幅 / 到价 / 新高新低三类提醒各自独立开关，冷却去重后才会再次提醒
- **提醒历史**：每次触发都记录时间、类型与内容，菜单「提醒历史…」可查看，保留最近 200 条
- **勿扰模式**：设定一段静音时段（支持跨零点，如 23:00–08:00），时段内只静音系统通知，
  金娘的气泡与台词照常
- **走势图时间范围**：迷你走势图可切换「1 小时 / 今日 / 全部」；价格历史上限 120 → 1440 条
  （按 60 秒刷新约覆盖 24 小时）
- **托盘图标显示价格**：托盘图标直接画出当前价格整数部分（如 `901`）并按涨跌着色，
  完整精度仍在悬停提示中；可在设置里关闭
- 测试 85 → 138 条，覆盖率 72% → 77.29%

## v0.4.1 修复

- **首次启动崩溃**：`welcome()` 此前未实现，首次运行必崩 → 现已实现引导
- **「关于」菜单报错**：`open_about()` 此前未实现 → 现已实现关于窗口
- **菜单统一**：右键菜单现与托盘菜单完全一致（此前右键缺「关于」）
- **交易时段判断**：修正夜盘跨零点归属（周六上午、周一凌晨此前被误判为开市）
- **拖动误触发撒娇**：拖动超过 0.6 秒不再弹出撒娇动画
- **缓存时效提示**：离线时显示「缓存于 xx:xx」，超 1 小时提示已过期
- 测试 33 → 85 条，覆盖率 52% → 72%，覆盖率门禁 65% 已落到 CI

## v0.4 新增

- **到价提醒**：设置里填「≥ 目标价」「≤ 目标价」（元/克），触发时金娘说话 + 系统通知
- **休市标识**：非交易时段（含夜盘跨零点、周末）气泡显示「休市」，不再让人以为卡住
- **首次运行引导**：第一次启动金娘自我介绍 + 托盘气泡说明交互方式
- **关于窗口**：版本、数据来源、免责声明、许可（右键菜单 → 关于）
- 右键菜单与托盘菜单统一；历史只在联网成功时落盘

## 文档

- [PRD](docs/PRD.md)
- [更新日志](CHANGELOG.md)
- [程序修改点](程序修改点.md)（代码级变更，含每个文件的改动原因）
- [代码评审 Hy4-gold-pet-v0.3.0](docs/review/Hy4-gold-pet-v0.3.0-代码评审文档.md)
- [产品优化方案 Hy4-gold-pet-v0.4.0](docs/product/Hy4-gold-pet-v0.4.0-产品优化方案文档.md)
- [架构优化方案 Hy4-gold-pet-v0.4.0](docs/architecture/Hy4-gold-pet-v0.4.0-架构优化文档.md)

## 开发规范

```bash
pip install -r requirements-dev.txt

python -m ruff check .     # 静态检查
python -m ruff format .    # 格式化
python -m mypy             # 类型检查（纯逻辑模块）
python -m pytest tests --cov=gold_pet --cov-report=term-missing

# Windows 一键：lint.bat
```

CI（.cnb.yml）在 push / PR 时自动跑：lint → 类型检查 → 测试 + 覆盖率。
覆盖率门禁 65%，配置在 `pyproject.toml` 的 `[tool.coverage.report] fail_under`。
