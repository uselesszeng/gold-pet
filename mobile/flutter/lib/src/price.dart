import 'dart:async';
import 'package:http/http.dart' as http;

/// 品种 -> 单位 -> (接口代码, 缩放系数)
const Map<String, Map<String, dynamic>> kMetals = {
  'gold': {
    'name': '黄金',
    'sources': {
      'cny_g': ['gds_AUTD', 1.0],
      'usd_oz': ['hf_XAU', 1.0],
    },
  },
  'silver': {
    'name': '白银',
    'sources': {
      'cny_g': ['gds_AGTD', 0.001],
      'usd_oz': ['hf_XAG', 1.0],
    },
  },
  'platinum': {
    'name': '铂金',
    'sources': {
      'usd_oz': ['hf_XPT', 1.0],
    },
  },
};

const double kOzToGram = 31.1034768;

String quoteKey(String metal, String unit) => '$metal|$unit';

class Quote {
  final String code;
  final String name;
  final double price;
  final double prevClose;
  final double high;
  final double low;
  final String unit;
  final bool converted;

  const Quote({
    required this.code,
    required this.name,
    required this.price,
    required this.prevClose,
    this.high = 0,
    this.low = 0,
    required this.unit,
    this.converted = false,
  });

  double get change => price - prevClose;
  double get changePct => prevClose == 0 ? 0 : change / prevClose * 100;

  String get priceText => price >= 10 ? price.toStringAsFixed(2) : price.toStringAsFixed(3);
}

class Snapshot {
  final Map<String, Quote> quotes;
  final double fx;
  final DateTime updatedAt;
  final bool ok;
  final String error;

  Snapshot({
    required this.quotes,
    this.fx = 0,
    DateTime? updatedAt,
    this.ok = false,
    this.error = '',
  }) : updatedAt = updatedAt ?? DateTime.now();

  /// 取指定品种与单位；缺则按汇率换算（元/克 <-> 美元/盎司）
  Quote? priceOf(String metal, String unit) {
    final direct = quotes[quoteKey(metal, unit)];
    if (direct != null) return direct;
    if (fx <= 0) return null;
    if (unit == 'cny_g') {
      final base = quotes[quoteKey(metal, 'usd_oz')];
      if (base == null) return null;
      return Quote(
        code: base.code,
        name: '${base.name}(换算)',
        price: base.price / kOzToGram * fx,
        prevClose: base.prevClose / kOzToGram * fx,
        high: base.high / kOzToGram * fx,
        low: base.low / kOzToGram * fx,
        unit: '元/克',
        converted: true,
      );
    }
    final base = quotes[quoteKey(metal, 'cny_g')];
    if (base == null) return null;
    return Quote(
      code: base.code,
      name: '${base.name}(换算)',
      price: base.price * kOzToGram / fx,
      prevClose: base.prevClose * kOzToGram / fx,
      high: base.high * kOzToGram / fx,
      low: base.low * kOzToGram / fx,
      unit: '美元/盎司',
      converted: true,
    );
  }
}

class SinaSource {
  static const String url =
      'https://hq.sinajs.cn/list=gds_AUTD,hf_XAU,gds_AGTD,hf_XAG,hf_XPT,fx_susdcny';

  static final RegExp _pattern = RegExp(r'var hq_str_(\w+)="([^"]*)";');

  Future<Snapshot> fetch() async {
    try {
      final resp = await http
          .get(Uri.parse(url), headers: {'Referer': 'https://finance.sina.com.cn'})
          .timeout(const Duration(seconds: 8));
      // 接口返回 GBK；这里只取数字与分隔符，按字节转字符串即可
      final text = String.fromCharCodes(resp.bodyBytes);
      final raw = <String, List<String>>{};
      for (final m in _pattern.allMatches(text)) {
        raw[m.group(1)!] = m.group(2)!.split(',');
      }
      return _parse(raw);
    } catch (e) {
      return Snapshot(quotes: const {}, ok: false, error: e.toString());
    }
  }

  Snapshot _parse(Map<String, List<String>> raw) {
    final quotes = <String, Quote>{};
    for (final entry in kMetals.entries) {
      final metal = entry.key;
      final name = entry.value['name'] as String;
      final sources = entry.value['sources'] as Map<String, dynamic>;
      for (final unit in sources.keys) {
        final code = (sources[unit] as List)[0] as String;
        final scale = (sources[unit] as List)[1] as double;
        final f = raw[code];
        if (f == null || f.length < 9) continue;
        final price = _f(f, 0) * scale;
        if (price <= 0) continue;
        quotes[quoteKey(metal, unit)] = Quote(
          code: code,
          name: name,
          price: price,
          prevClose: _f(f, 7) * scale,
          high: _f(f, 4) * scale,
          low: _f(f, 5) * scale,
          unit: unit == 'cny_g' ? '元/克' : '美元/盎司',
        );
      }
    }
    final fxRaw = raw['fx_susdcny'] ?? const <String>[];
    var fx = _f(fxRaw, 1);
    if (fx <= 3 || fx >= 12) fx = _f(fxRaw, 7);
    if (fx <= 3 || fx >= 12) fx = 0;
    return Snapshot(
      quotes: quotes,
      fx: fx,
      ok: quotes.isNotEmpty,
      error: quotes.isEmpty ? '行情为空' : '',
    );
  }

  double _f(List<String> v, int i) {
    if (i >= v.length) return 0;
    return double.tryParse(v[i]) ?? 0;
  }
}
