# Hy4-gold-pet-v0.4.0-架构优化文档

| 项 | 内容 |
|---|---|
| 评审模型 | Hy4 |
| 应用名称 | 黄金桌宠（gold-pet） |
| 版本 | v0.4.0 |
| 日期 | 2026-09-29 |
| 视角 | 高级开发工程师 |

---

## 一、现状架构

```
main.py            入口：CLI 解析、日志、单实例锁、装配
  ├── config.py    配置读写 + 白名单校验（JSON，用户目录）
  ├── price.py     行情：抓取 / 解析 / 换算 / 缓存 / 历史 / 休市判断（不依赖 Qt）
  ├── ui.py        PetWindow（窗口+动画+气泡+交互+提醒+台词）+ 设置面板 + 菜单 + 托盘
  └── autostart.py Windows 注册表自启（非 Windows 空实现）

tools/             素材生成、命令行查价
tests/             28 条 pytest（price / config / market / ui-offscreen）
mobile/flutter/    移动端骨架（Dart，数据层与桌面端同一套约定）
gold-pet.spec      PyInstaller 打包配置
```

### 做得对的

1. 数据层不依赖 GUI：price.py 纯 Python，可单测、可被移动端照搬
2. 边界兜底完整：网络异常、坏 JSON、坏配置、托盘缺失都有降级
3. 素材脚本化：不依赖二进制美术资源
4. 打包与 CI 已通：spec 验证过，CI 跑 pytest

---

## 二、架构问题清单（按影响面排序）

| # | 问题 | 位置 | 影响 | 建议 |
|---|---|---|---|---|
| A1 | PetWindow 成为上帝类（约 800 行）：窗口、动画、气泡、交互、提醒、台词、配置全在一起 | ui.py | 改一处牵一片 | 拆成 pet_window / bubble / animator / tray / menu / settings / notifier |
| A2 | 状态隐式：行情态（up/down/idle/offline）与交互态（pat/shy/loading）共用 self.state | ui.py | 交互期间来行情会互相打断 | 显式状态优先级或小型状态机 |
| A3 | UI 直接依赖具体 PriceService，无接口 | ui.py | 测试要构造真实对象 | 定义 PriceSource Protocol，构造注入，测试用 FakeSource |
| A4 | 配置是弱类型 dict，键名是散落的字符串 | config.py | 拼错键不报错 | dataclass + schema，键名常量化 |
| A5 | 气泡内容是手写 HTML 拼接 | ui.py::_render | 展示与逻辑耦合 | 抽 render_bubble() 模板函数 |
| A6 | 线程模型简单：每次刷新新建 QThread，无取消与并发上限 | ui.py::PriceWorker | 极端情况旧回调 | 统一 TaskRunner / QThreadPool + 可取消 |
| A7 | 路径散落：app_dir() 与 asset_dir() 都在 config | config.py | 职责混杂 | 独立 paths.py |
| A8 | 平台相关逻辑散落（注册表自启、时区假设、托盘能力） | 多文件 | 移植多端要重新找 | platform 适配层（autostart / tray / notify / paths） |
| A9 | 工程化不完整：无 lint、格式化、类型检查、覆盖率门禁 | 仓库 | 长期维护成本 | ruff + black + mypy，CI 加门禁，覆盖率先 60% |
| A10 | 发布链路缺失：无版本元数据、无签名、无自动更新、无安装包 | spec / CI | 分发体验 | 版本资源 + 可选签名 + Inno Setup |
| A11 | 可观测性不足：只有日志，无关键指标 | 全项目 | 问题定位慢 | 结构化日志 + 关键事件计数 |
| A12 | 桌面端与移动端重复实现解析逻辑 | price.py / price.dart | 两边漂移 | 字段下标与换算沉淀为共享契约 + 双端基线用例 |
| A13 | GBK 解码按字节转字符串（当前只取数字可行） | price.py | 取中文名会乱码 | 明确注释或引入 gbk 解码 |

---

## 三、建议的目标架构

```
app/
├── main.py              装配与生命周期
├── core/
│   ├── settings.py      dataclass 配置 + 校验
│   ├── paths.py         用户目录 / 资源目录（区分打包与开发）
│   ├── bus.py           轻量事件总线（行情更新 / 提醒 / 交互）
│   └── platform/        自启 / 托盘 / 通知 / 单实例（按 OS 分文件）
├── data/
│   ├── source.py        PriceSource Protocol
│   ├── sina.py          新浪实现（抓取 + 解析）
│   ├── model.py         Quote / Snapshot（纯数据）
│   ├── cache.py         上次行情 + 历史序列
│   └── market_hours.py  交易时段
└── ui/
    ├── pet_window.py    窗口与拖动
    ├── animator.py      帧动画与状态优先级
    ├── bubble.py        气泡渲染（模板）
    ├── tray.py          托盘与菜单
    ├── settings_dialog.py
    └── notifier.py      提醒规则与去重
```

---

## 四、重构路线（分阶段）

| 阶段 | 内容 | 收益 | 风险 | 时机 |
|---|---|---|---|---|
| 阶段 1 | A5 气泡模板化 + A7 抽 paths + A9 加 lint/format | 可读性 | 极低 | 现在 |
| 阶段 2 | A1 拆 ui.py + A2 状态优先级 + A3 注入 PriceSource | 可测性 | 中（需回归交互） | v0.5 前 |
| 阶段 3 | A4 配置 dataclass + A6 线程池 + A8 平台层 | 支撑多端复用 | 中 | 做 Android / 鸿蒙前 |
| 阶段 4 | A10 发布链路 + A11 指标 + A12 共享契约 | 交付体验 | 低 | v1.0 前后 |

每个阶段顺序：先补测试 → 再重构 → pytest + 打包验证 → 提交。

---

## 五、明确「不要做」（避免过度设计）

1. 不上插件系统、不上 DI 框架：单窗口桌面挂件，手工装配足够
2. 不引入 ORM / 数据库：JSON 缓存在这个数据量下够用
3. 不做云同步 / 账号：与本地低打扰的定位冲突
4. 不为“架构好看”一次性重写：按上表分阶段，每阶段都可发布

---

## 六、v0.4.0 已完成的架构改进

1. 菜单构建收敛为 build_menu()，右键与托盘共用
2. 交互态回退集中在 _end_interact()（状态机的过渡方案）
3. 历史写入只在联网成功时进行（I/O 与渲染解耦第一步）
4. 休市判断放进数据层 is_market_open()，UI 只负责展示
5. 测试 24 → 28 条（新增 market 与到价提醒）
