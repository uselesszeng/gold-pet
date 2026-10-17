# Issue 汇总与操作日志（gold-pet v0.8.0 / R5）

- **执行模型**：Hy4（CodeBuddy）｜**调度 Issue**：#19
- **时间**：2026-10-17 01:15 ~ 01:35（沙箱时钟）
- **基线**：`e660d9d` ｜**修复分支**：`auto/r5-fixes-a1ee` ｜**修复提交**：`8297bd0`

⚠️ **已知平台限制**：CNB 的 Issue 标题不支持 `+`（创建时被转义为 `%2B`）。
本条已按 SOP 规定格式提交；若需严格原样，维护者可在网页端手动改回。

---

## 一、本轮新建 Issue（6 条）

| # | 标题 | 优先级 | 状态 | 来源 |
|---|---|---|---|---|
| [#20](https://cnb.cool/TJ-coding/gold-pet/-/issues/20) | `v0.8.0+100%+P1+配置` | P1 | ✅ 已修已关 | Code Review（CR-01） |
| [#21](https://cnb.cool/TJ-coding/gold-pet/-/issues/21) | `v0.8.0+100%+P2+提醒引擎` | P2 | ✅ 已修已关 | Monkey（M5-02） |
| [#22](https://cnb.cool/TJ-coding/gold-pet/-/issues/22) | `v0.8.0+100%+P1+测试门禁` | P1 | ✅ 已修已关 | Monkey（M5-01） |
| [#23](https://cnb.cool/TJ-coding/gold-pet/-/issues/23) | `v0.8.0+100%+P1+CI门禁` | P1 | ✅ 已修已关 | Code Review（CR-04） |
| [#24](https://cnb.cool/TJ-coding/gold-pet/-/issues/24) | `v0.8.0+直接调用100%+P3+提醒引擎` | P3 | ✅ 已修已关 | Monkey（M5-03） |
| [#25](https://cnb.cool/TJ-coding/gold-pet/-/issues/25) | `v0.8.0+100%+P2+行情缓存` | P2 | ⏸️ **保持 open（待决策）** | Code Review（CR-02） |

### Issue 编号与发现的对应关系

| Issue | 发现编号 | 一句话 |
|---|---|---|
| #20 | CR-01 / M5-辅助 | `Config.save` / `_write_info` 裸 `write_text`，并发读实测 292/300 命中坏读 |
| #21 | M5-02 | `_roll_day` 的 `time.localtime` 对极端时间戳抛 `OSError` 逃逸出 `evaluate` |
| #22 | M5-01 | `test_r3_fs_readonly_target` 的「只读」前提只在 Windows 成立，Linux 必红 |
| #23 | CR-04 | `mypy` 必红 + `ruff format` 38 文件不符，CI 在 lint 阶段退出 |
| #24 | M5-03 | `Alert.from_dict` 对非 dict 抛 `AttributeError`（不在容错白名单） |
| #25 | CR-02 | 跨进程写 `history.json` 几乎全丢（4×200 → 1 点），需架构决策 |

## 二、操作日志（全程留痕）

| 时间 | 操作 | 对象 | 结果 |
|---|---|---|---|
| 01:17 | 创建 Issue（P1） | #20 | ✅ |
| 01:18 | 创建 Issue（P2） | #21 | ✅ |
| 01:19 | 创建 Issue（P1） | #22 | ✅ |
| 01:20 | 创建 Issue（P1） | #23 | ✅ |
| 01:21 | 创建 Issue（P3） | #24 | ✅ |
| 01:22 | 创建 Issue（P2） | #25 | ✅ |
| 01:23 | 修正 #25 标题（`%2B` 转义） | #25 | ✅ |
| 01:30 | 提交修复并推送 | `auto/r5-fixes-a1ee` | ✅ `8297bd0` |
| 01:32 | 创建 PR | [#26](https://cnb.cool/TJ-coding/gold-pet/-/pulls/26) | ✅ |
| 01:34 | 评论 + 关闭 | #20 #21 #22 #23 #24 | ✅ 5 条 |
| 01:35 | 评论（补量化证据，不关） | #25 | ✅ |

## 三、未关闭项与原因（不阻塞次日调度）

| Issue | 为什么保持 open |
|---|---|
| #25 | 修法涉及跨进程锁机制 / 单写者仲裁的**架构选择**，需维护者拍板；无人值守纪律下不擅自引入新依赖。已补齐量化证据 + 回归锚。 |

## 四、与既有 Issue 的关系

| 既有 Issue | 关系 |
|---|---|
| #10（第四轮报告） | 其称 R4-M-01「配置原子写已修」的提交 `1d291af`/`6944bda` **不在 `origin/main`** 上，当前主线仍为 `e660d9d`，故本轮把 #20 作为独立缺陷重新登记并实修。 |
| #15（CI 门禁失效） | 同类问题（format 未过 / ruff 漂移）。本轮**修了本仓面**（#23），#15 的安卓侧未动。 |
| #18（requirements 引号） | `"mcp<2"` 的独立问题，本轮未动。 |
| #3（v0.8.0 无人值守） | 同一长期任务的上一轮次记录。 |

---
_本文件由 @codebuddy 自动生成。_
