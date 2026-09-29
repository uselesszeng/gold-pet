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
├── gold_pet/            源码（config / price / ui / autostart / main）
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
配置读写与素材完整性、UI 渲染 / 单位与品种切换 / 摸头撒娇 / 台词恢复 / 离线状态 / 失败退避。

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
