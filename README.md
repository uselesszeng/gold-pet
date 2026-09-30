# 🐥 黄金桌宠（Gold Pet）

一只住在桌面右下角的「金娘」，**实时盯着金价**：涨了笑、跌了哭，点一下切换 国内元/克 ↔ 国际美元/盎司。

- 当前版本：**v0.4.0**（版本号见 `gold_pet/__init__.py`）
- 运行环境：**Windows** + Python 3.11+（用到的语法与 `pyproject.toml` 的 `py-311` 目标为准）
- 数据来源：新浪财经公开行情接口，不需要密钥；行情仅供参考，**不构成投资建议**
- 许可：[MIT](LICENSE)

> 一期只做 **Windows**。Android / 鸿蒙 HAP 见 [docs/PRD.md](docs/PRD.md) 的二期、三期规划。

## 功能

| 功能 | 说明 |
|---|---|
| 🪙 桌宠窗口 | 无边框、透明背景、置顶、可拖动、位置记忆 |
| 🎞️ 状态动画 | 待机（呼吸+眨眼）/ 涨 / 跌 / 摸摸头 / 撒娇 / 加载中 / 离线 |
| 💬 价格气泡 | 价格 + 涨跌额与涨跌幅 + 今日高低 + 更新时间；可整块隐藏 |
| 🔄 自动刷新 | 默认 60 秒，可选 15/30/60/300/900 秒；双击立即刷新；失败指数退避重试 |
| 🔔 涨跌提醒 | 涨跌幅超过阈值 → 气泡高亮 + 系统通知（同方向默认 10 分钟不重复） |
| 🎯 到价提醒 | 设置「≥ 目标价」「≤ 目标价」（元/克，0 = 关闭）→ 金娘说话 + 系统通知 |
| 📈 创新高/新低 | 触及今日最高/最低时提醒（同方向 10 分钟不重复） |
| 🪙 多品种 | 黄金 / 白银 / 铂金 一键切换（右键菜单或托盘） |
| 📊 迷你走势图 | 气泡下方实时折线，记录最近 120 个价格点 |
| 🗣️ 互动台词 | 到价提醒、手动点击时金娘会说话（4 秒后恢复价格） |
| 🖱️ 交互 | 单击摸头 · 长按撒娇 · 双击刷新 · 拖动移动 · 右键菜单 |
| 📌 托盘常驻 | 关窗=藏到托盘；托盘菜单可刷新/切换/设置/退出 |
| ⚙️ 设置 | 品种、单位、刷新间隔、涨跌阈值、提醒冷却、到价提醒、不透明度、置顶、气泡、走势图、开机自启 |
| 🔌 离线兜底 | 断网显示「离线」+ 上次价格，不崩溃；非交易时段气泡标「休市」 |
| 🛡️ 单人单实例 | 重复启动会提示「已经在运行了」，托盘里能找到它 |

## 快速开始（Windows）

```bat
:: 1. 安装依赖（只需一次）
pip install -r requirements.txt

:: 2. 运行（双击 run.bat 或 run.py 都行）
python run.py
```

先看看行情、不弹界面：

```bat
python tools/check_price.py
python run.py --once
```

打包成单个 exe：

```bat
build.bat
:: 产物：dist\黄金桌宠.exe
```

## 操作说明

| 操作 | 效果 |
|---|---|
| 左键单击 | **摸摸头**：眯眼笑 + 台词 |
| 左键长按（约 0.6 秒） | **撒娇**：眼神飘开 + 台词 |
| 左键双击 | 立即刷新行情 |
| 左键拖动 | 移动位置（退出时记忆） |
| 右键 | 菜单：刷新 / 切换单位 / 切换品种 / 摸摸头 / 撒娇 / 说句话 / 置顶 / 气泡 / 走势图 / 设置 / 关于 / 隐藏 / 退出 |
| 托盘左键单击 | 显示 / 隐藏 |

右键菜单与托盘菜单是同一套（托盘多一项「显示/隐藏」）；「隐藏到托盘」只在托盘可用时出现。

## 数据来源

| 品种 | 国内（元/克） | 国际（美元/盎司） |
|---|---|---|
| 黄金 | `gds_AUTD`（上金所 Au T+D） | `hf_XAU`（伦敦金） |
| 白银 | `gds_AGTD`（白银 T+D，元/千克 ÷ 1000） | `hf_XAG`（伦敦银） |
| 铂金 | 由美元价换算 | `hf_XPT`（纽约铂金） |

- 汇率：新浪财经 `fx_susdcny`
- 换算：`元/克 = 美元/盎司 ÷ 31.1034768 × 汇率`；缺哪个方向就用另一个方向换算，高低价一起换算
- 均为免密钥公开接口；程序自带本地缓存，断网时显示上次价格
- 休市判断：日盘 09:00–11:30、13:30–15:30，夜盘 20:00–次日 02:30，周末休市；按本机本地时间判断，非东八区会有偏差

## 目录结构

```
gold-pet/
├── docs/PRD.md              产品需求文档（另有评审/产品/架构文档在同级子目录）
├── gold_pet/
│   ├── main.py              CLI 入口、日志、单实例锁、窗口装配
│   ├── ui.py                桌宠窗口、托盘、设置面板
│   ├── bubble.py            气泡 HTML 渲染（纯函数，无 Qt 依赖）
│   ├── price.py             行情抓取 / 解析 / 换算 / 缓存 / 历史
│   ├── config.py            配置读写与校验
│   ├── paths.py             用户数据目录与资源目录
│   └── autostart.py         开机自启（Windows 注册表，其他系统空操作）
├── assets/pet/*.png         动画帧（tools/gen_assets.py 生成）
├── assets/icon.ico|png      应用图标
├── tools/gen_assets.py      素材生成脚本（改配色/表情后重跑）
├── tools/check_price.py     命令行查行情（调试用）
├── tests/                   单元测试
├── mobile/flutter/          Android / 移动端骨架
├── requirements.txt         运行时依赖
├── requirements-dev.txt     开发与打包依赖
├── run.bat / run.py         开发运行
├── build.bat / gold-pet.spec 打包 exe
└── lint.bat                 一键质量检查
```

## 数据与配置位置

用户数据都在 `%APPDATA%\GoldPet\`（其他系统 `~/.goldpet/`），升级或重新打包不会丢：

| 文件 | 内容 |
|---|---|
| `settings.json` | 配置，可直接手改后重启 |
| `last_price.json` | 最后一次行情缓存（断网时回显） |
| `history.json` | 各品种+单位的走势点（每个最多 120 点，同一分钟不重复记） |
| `gold-pet.log` | 日志（滚动保留 2 份） |
| `.gold-pet.lock` | 单实例锁 |

## 命令行参数

```bat
python run.py --once                  :: 只拉一次行情并打印，不启动界面
python run.py --metal silver --once   :: 指定品种（gold / silver / platinum）
python run.py --unit usd_oz --once    :: 指定单位（cny_g / usd_oz）
python run.py --log-level DEBUG       :: 日志级别
python run.py --version               :: 版本号
```

打包后同样可用：`dist\黄金桌宠.exe --once`。

## 想改点东西？

| 想改什么 | 改哪里 |
|---|---|
| 金娘的样子/颜色/表情 | `tools/gen_assets.py`，改完 `python tools/gen_assets.py` |
| 动画帧数与播放速度 | `gold_pet/ui.py` 的 `STATES` |
| 台词 | `gold_pet/ui.py` 的 `LINES` |
| 气泡内容与配色 | `gold_pet/bubble.py`（纯函数，改完跑 `tests/test_bubble.py`） |
| 默认值、可选项 | `gold_pet/config.py` 的 `DEFAULTS` / `INTERVAL_CHOICES`，或设置面板 |
| 涨红跌绿换成涨绿跌红 | `gold_pet/bubble.py` 的 `UP_COLOR` / `DOWN_COLOR` |
| 换数据源、改换算 | `gold_pet/price.py` 的 `SINA_URL` / `METALS` / `parse_snapshot` |

## 移动端（Flutter）

`mobile/flutter/` 是 Android / 移动端的骨架，数据层与 Windows 版同一套接口与换算：

```bash
cd mobile/flutter
flutter create --org com.tjcoding --project-name gold_pet --platforms=android,windows .
flutter pub get && flutter run
```

详见 [mobile/flutter/README.md](mobile/flutter/README.md)。

## 开发与测试

```bash
pip install -r requirements-dev.txt

python -m ruff check .                 # 静态检查
python -m ruff format .                # 格式化
python -m mypy                         # 类型检查（price / config / paths / bubble）
python -m pytest tests -q              # 单元测试
python -m pytest tests --cov=gold_pet --cov-report=term-missing

:: Windows 一键：lint.bat
```

CI（`.cnb.yml`）在 push / PR 时自动跑：lint → 类型检查 → 测试 + 覆盖率。

测试覆盖：行情解析（三个品种、单位换算、缩放、高低价换算）、缓存与损坏兜底、
历史去重与上限、配置读写与坏值收敛、休市时段判断、气泡渲染、
UI 渲染 / 单位与品种切换 / 摸头撒娇 / 台词恢复 / 离线状态 / 到价提醒 / 失败退避。

## 打包

```bat
build.bat          :: 等价于 pyinstaller --noconfirm --clean gold-pet.spec
:: 产物：dist\黄金桌宠.exe（约 60-80MB，onefile、无控制台）
:: 精简：spec 里排除了 QtQml/QtWebEngine/Qt3D/QtMultimedia 等未使用模块
:: 资源：assets 整个目录随包打入，运行时从临时目录读取
```

## 常见问题

- **桌宠不见了**：换显示器/改分辨率后可能落在屏幕外，程序会自动回落到主屏右下角；
  若仍找不到，删掉 `%APPDATA%\GoldPet\settings.json` 里的 `pos` 后重启。
- **重复启动**：只会有一个金娘，第二次启动会提示「已经在运行了」，去托盘里找它。
- **没有托盘**：部分环境禁用托盘，此时关窗即退出，菜单里不会出现「隐藏到托盘」。
- **一直显示「离线」**：接口依赖外网，检查网络与代理；断网期间显示的是上次价格与时间。
- **提醒太吵 / 收不到**：设置里调「涨跌提醒阈值」（0 = 关闭）与「同类提醒冷却」；
  系统通知需要在 Windows 通知设置里允许。
- **气泡显示「休市」**：是非交易时段，价格不会动，属正常。
- **设置改坏了**：配置会被收敛到合法范围（坏值回落默认值）；也可直接删掉 `settings.json` 重新开始。

## 文档

- [PRD](docs/PRD.md)
- [代码评审 Hy4-gold-pet-v0.3.0](docs/review/Hy4-gold-pet-v0.3.0-代码评审文档.md)
- [产品优化方案 Hy4-gold-pet-v0.4.0](docs/product/Hy4-gold-pet-v0.4.0-产品优化方案文档.md)
- [架构优化方案 Hy4-gold-pet-v0.4.0](docs/architecture/Hy4-gold-pet-v0.4.0-架构优化文档.md)
- [移动端说明](mobile/flutter/README.md)

## 更新记录

- **v0.4**：到价提醒、休市标识、首次运行引导、关于窗口；右键菜单与托盘菜单统一；
  历史只在联网成功时落盘
- **v0.3**：摸头 / 撒娇互动、今日最高最低价与创新高/创新低提醒、Flutter 移动端骨架
- **v0.2**：多品种（黄金/白银/铂金）、迷你走势图、互动台词
- **v0.1**：桌宠窗口、帧动画、价格气泡、定时刷新、托盘、设置面板

## 仓库

- CNB：https://cnb.cool/TJ-coding/gold-pet
