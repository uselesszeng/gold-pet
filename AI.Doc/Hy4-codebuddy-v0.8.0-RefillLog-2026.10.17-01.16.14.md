# `code refill` 执行记录（gold-pet v0.8.0）

- **执行模型**：Hy4（CodeBuddy）｜**调度 Issue**：#19
- **执行时间**：2026-10-17 01:17（沙箱时钟）
- **基线**：`e660d9d`（`origin/main`）
- **结论**：**refill 通过（无残缺）** —— 工作树可从干净 clone 重建至全绿（除两处已登记缺陷）

---

## 1. 做了什么

`code refill` 在本项目语境下 = **代码库状态盘点与可重建性验证**：确认「一个新环境从 clone 到跑起来」
不缺任何东西，把缺失的依赖/文件/配置补齐，并记录实际安装的版本（便于后续轮次复现）。

## 2. 依赖清单核对（requirements*.txt vs 实际环境）

| 依赖 | 声明 | 沙箱实际 | 判定 |
|---|---|---|---|
| PySide6 | `>=6.6` | 6.12.0 | ✅ |
| Pillow | `>=10.0` | 12.3.0 | ✅ |
| mcp | `"mcp<2"` | 2.1.1 → **按声明装回 1.30.0** | ⚠️ 见下 |
| httpx | 未限版本 | 0.28.1 | ✅ |
| pytest | `>=8.0` | 9.1.1 | ✅ |
| pytest-cov | `>=5.0` | 7.1.0 | ✅ |
| ruff | `>=0.6` | **0.17.1** | ⚠️ 见下 |
| mypy | `>=1.10` | 2.4.0 | ✅ |
| pyinstaller | `>=6.3` | 未安装（打包用，CI 不需要） | ✅ |

### 2.1 `mcp` 版本口径（补记 Issue #15 / #18 的同类面）

`requirements.txt` 写的是 `"mcp<2"`，而沙箱预装 2.1.1。不按声明装回 1.30.0 的话，
`tools/pet_mcp_server.py` 与 `tests/test_monkey2_mcp_stdio.py` / `test_monkey2_json_spec.py` /
`test_monkey3_mimosa.py` 共 **9 条测试直接 ERROR**（`No module named 'mcp.server.fastmcp'`），
看起来像「代码坏了」，实际是**环境没按声明装**。

- **这不是代码缺陷**，是「声明了 pin 但缺可复现安装路径」的老问题（Issue #18 已就 `requirements.txt`
  的 `"mcp<2"` 引号写法单独登记）。
- 本轮**不改** requirements（避免与 #18 抢同一行），只在文档留证：**跑测试前必须先按声明装依赖**。

### 2.2 `ruff` 无上限版本（Issue #15 已登记的漂移）

沙箱 0.17.1 vs 仓库历史使用的版本，`ruff format` 结果差异直接体现为
「38 个文件需要重新格式化」——**这是工具版本漂移的假象，不是代码风格崩坏**。
本轮处理方式见代码评审文档 CR-04（先取证、再决定是否批量格式化）。

## 3. 冷启动可重建性验证（全新用户目录）

模拟「装完包第一次双击」：清空用户数据目录 → 只调库、不动 UI：

| 步骤 | 结果 |
|---|---|
| `Config.load()` 在目录不存在、文件不存在时 | 返回默认配置，`mkdir(parents=True)` 自建目录 ✅ |
| `HistoryStore.append()` 首写 | 落 `history.json` 55 B，合法 JSON ✅ |
| `PriceService.save_cache()` 首写 | 落 `last_price.json` 83 B ✅ |
| `Notifier.record()` 首写 | 落 `alert_history.json` 99 B ✅ |
| `Config.save()` | 落 `settings.json` 776 B ✅ |
| 全程耗时 | **0.039 s** |

产物清单（`~/.goldpet/`）：`settings.json` / `history.json` / `last_price.json` / `alert_history.json`
—— 与 `gold_pet/paths.py` 契约一致，**无缺失文件、无多余文件、无 `.tmp` 残留**。

## 4. 仓库结构核对

| 项 | 状态 |
|---|---|
| `gold_pet/` 12 个模块 | ✅ 全部可独立 import（GUI 模块需 offscreen） |
| `tests/` 36 个文件 | ✅ pytest 可完整收集，无 import 错 |
| `tools/` 15 个脚本 | ✅ 入口存在 |
| `assets/`（图标 + 动画帧） | ✅ `test_assets.py` 通过 |
| `.cnb.yml` 三段流水线（push / pull_request / tag_push） | ✅ 结构完整 |
| `.gitignore` | ✅ 覆盖 `release/`、`.workbuddy/`、`.pytest_cache/` |
| `AI.Doc/` | 🆕 本轮创建（本轮起文档统一收拢于此） |

## 5. 结论

- **refill 通过**：仓库自描述完整，冷启动零依赖缺失。
- 唯一「需人按声明装依赖」的坑：`mcp<2`（有 pin、无锁文件）——已在 §2.1 留证，不在本轮修。
- 未新增/删除任何依赖，未改 `requirements*.txt`（避免与 Issue #18 冲突）。

---
_本文件由 @codebuddy 自动生成。_
