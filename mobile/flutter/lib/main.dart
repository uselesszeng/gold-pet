import 'dart:async';

import 'package:flutter/material.dart';

import 'src/price.dart';

void main() => runApp(const GoldPetApp());

class GoldPetApp extends StatelessWidget {
  const GoldPetApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '黄金桌宠',
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: const Color(0xFFFFD34D)),
      home: const PriceHomePage(),
    );
  }
}

class PriceHomePage extends StatefulWidget {
  const PriceHomePage({super.key});

  @override
  State<PriceHomePage> createState() => _PriceHomePageState();
}

class _PriceHomePageState extends State<PriceHomePage> {
  final SinaSource _source = SinaSource();
  Timer? _timer;

  String _metal = 'gold';
  String _unit = 'cny_g';
  Snapshot? _snapshot;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 60), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    if (_loading) return;
    setState(() => _loading = true);
    final snap = await _source.fetch();
    if (!mounted) return;
    setState(() {
      _snapshot = snap;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final quote = _snapshot?.priceOf(_metal, _unit);
    final metalName = (kMetals[_metal]?['name'] as String?) ?? '黄金';
    final Color color = quote == null
        ? Colors.grey
        : quote.change > 0
            ? const Color(0xFFFF4D4F) // 涨：红
            : quote.change < 0
                ? const Color(0xFF16C784) // 跌：绿
                : Colors.grey;

    return Scaffold(
      appBar: AppBar(
        title: const Text('黄金桌宠'),
        actions: [IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh))],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const SizedBox(height: 8),
            Center(
              child: _PetAvatar(
                state: quote == null
                    ? 'offline'
                    : quote.change > 0
                        ? 'up'
                        : quote.change < 0
                            ? 'down'
                            : 'idle',
              ),
            ),
            const SizedBox(height: 16),
            SegmentedButton<String>(
              segments: [
                for (final e in kMetals.entries)
                  ButtonSegment(value: e.key, label: Text(e.value['name'] as String)),
              ],
              selected: {_metal},
              onSelectionChanged: (s) => setState(() => _metal = s.first),
            ),
            const SizedBox(height: 12),
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'cny_g', label: Text('元/克')),
                ButtonSegment(value: 'usd_oz', label: Text('美元/盎司')),
              ],
              selected: {_unit},
              onSelectionChanged: (s) => setState(() => _unit = s.first),
            ),
            const SizedBox(height: 24),
            if (quote == null)
              const Center(child: Text('正在获取行情…'))
            else ...[
              Center(
                child: Text(
                  '$metalName ${quote.priceText} ${quote.unit}',
                  style: const TextStyle(fontSize: 26, fontWeight: FontWeight.bold),
                ),
              ),
              const SizedBox(height: 6),
              Center(
                child: Text(
                  '${quote.change > 0 ? '▲' : quote.change < 0 ? '▼' : '—'} '
                  '${quote.change >= 0 ? '+' : ''}${quote.change.toStringAsFixed(2)} '
                  '(${quote.change >= 0 ? '+' : ''}${quote.changePct.toStringAsFixed(2)}%)',
                  style: TextStyle(fontSize: 18, color: color),
                ),
              ),
              const SizedBox(height: 6),
              if (quote.high > 0 && quote.low > 0)
                Center(
                  child: Text(
                    '高 ${quote.high.toStringAsFixed(2)} · 低 ${quote.low.toStringAsFixed(2)}',
                    style: const TextStyle(color: Colors.grey),
                  ),
                ),
            ],
            const SizedBox(height: 24),
            Center(
              child: Text(
                _snapshot == null
                    ? ''
                    : '更新于 ${TimeOfDay.fromDateTime(_snapshot!.updatedAt).format(context)}'
                        '${_snapshot!.ok ? '' : ' · 离线'}',
                style: const TextStyle(color: Colors.grey),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 金娘头像占位：正式版换成 assets/pet 里的帧动画
class _PetAvatar extends StatelessWidget {
  final String state;
  const _PetAvatar({required this.state});

  @override
  Widget build(BuildContext context) {
    final emoji = switch (state) {
      'up' => '👑✨',
      'down' => '😢',
      'offline' => '😴',
      _ => '👑',
    };
    return Container(
      width: 140,
      height: 140,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: const LinearGradient(
          colors: [Color(0xFFFFF0B8), Color(0xFFF7D774), Color(0xFFD6A53C)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.12),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Center(child: Text(emoji, style: const TextStyle(fontSize: 54))),
    );
  }
}
