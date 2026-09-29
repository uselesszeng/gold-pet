# 移动端（Flutter）架子

黄金桌宠的 Android / 移动端骨架，数据层与 Windows 版一致（同一套新浪接口与换算规则）。
鸿蒙 HAP（ArkTS）版本可直接复用这里的品种、字段下标与换算逻辑。

## 怎么用

```bash
# 1. 生成 android/ 等平台目录（需自备 Flutter SDK）
flutter create --org com.tjcoding --project-name gold_pet --platforms=android,windows .

# 2. 装依赖
flutter pub get

# 3. 运行
flutter run
```

本目录只提交 pubspec.yaml 与 lib/，android/、ios/ 等平台目录由 flutter create 生成。

## 已实现

- 实时抓取 黄金 / 白银 / 铂金（元/克、美元/盎司，缺项按汇率换算）
- 涨红跌绿、涨跌额与涨跌幅、最高 / 最低价
- 60 秒自动刷新 + 下拉刷新 + 右上角手动刷新
- 金娘头像占位（后续接入 assets 帧动画）

## 待补

- 帧动画（复用 assets/pet/*.png）
- 到价提醒（flutter_local_notifications）
- Android 桌面悬浮窗（SYSTEM_ALERT_WINDOW 权限）
