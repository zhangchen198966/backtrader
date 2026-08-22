# -*- coding: utf-8 -*-
"""bt-lab: 在线数据源（拉取股票/指数日线 → 标准 CSV）

提供三个入口（均为免费公开数据）：
  - akshare-a     A股个股日线（新浪源，国内直连，前复权）
  - akshare-index A股指数日线（新浪源，如 sh000001 上证指数）
  - yfinance      美股/全球（Yahoo Finance，国内通常需要代理）

所有源统一清洗为：Date,Open,High,Low,Close,Volume,OpenInterest(=0)
按日期升序、剔除缺失行、日期格式 %Y-%m-%d —— 与 bt-lab 的 CSV 探测器约定一致，
落盘后无需再清洗即可回测。
"""
import csv
import json
import os
import re
import time
import traceback
from datetime import date as _date, datetime

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
STOCK_LIST_CACHE = os.path.join(CACHE_DIR, 'stock_list.json')
STOCK_LIST_TTL = 7 * 86400  # 名称清单缓存 7 天

# 常用指数（搜索用静态表）
INDEXES = [
    {'code': 'sh000001', 'name': '上证指数'},
    {'code': 'sz399001', 'name': '深证成指'},
    {'code': 'sz399006', 'name': '创业板指'},
    {'code': 'sh000300', 'name': '沪深300'},
    {'code': 'sh000016', 'name': '上证50'},
    {'code': 'sh000905', 'name': '中证500'},
    {'code': 'sh000688', 'name': '科创50'},
    {'code': 'sz399005', 'name': '中小100'},
]
from datetime import timedelta  # noqa: F401


class FetchError(Exception):
    """数据获取失败（网络/代码不存在等），消息直接展示给用户"""


PROVIDERS = {
    'akshare-a': {
        'label': 'A股个股日线(前复权) · AkShare(新浪)',
        'hint': '6位代码（拉取前复权日线），如 600519 贵州茅台、000001 平安银行、300750 宁德时代',
        'symbol_re': r'^\d{6}$',
        'symbol_hint': '请输入 6 位 A 股代码',
    },
    'akshare-index': {
        'label': 'A股指数日线 · AkShare(新浪)',
        'hint': '市场前缀+代码：sh000001 上证指数、sz399001 深证成指、sz399006 创业板指',
        'symbol_re': r'^(sh|sz|bj)\d{6}$',
        'symbol_hint': '格式：sh000001 / sz399001',
    },
    'yfinance': {
        'label': '美股/全球 · Yahoo Finance',
        'hint': '输入代码或中文名，如 AAPL / 苹果 / 腾讯 / 00700.HK；接口有频率限制且国内网络常需代理',
        'symbol_re': r'^[A-Za-z0-9.\-^=]{1,20}$',
        'symbol_hint': '请输入有效的 Yahoo 代码',
    },
}


# ---------------------------------------------------------------- 清洗（纯函数，可单测）

def _to_float(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_date_str(v):
    """各种日期形态 → 'YYYY-MM-DD'；无法解析返回 None"""
    if v is None or v == '':
        return None
    if isinstance(v, (datetime, _date)):
        return v.strftime('%Y-%m-%d')
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s.split('.')[0].split('+')[0].strip(), fmt) \
                .strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def normalize_ohlc_rows(raw_rows, keys):
    """原始记录列表 → 标准 OHLCV 行。

    raw_rows: [dict]（列名 → 值）
    keys: {'date':..., 'open':..., 'high':..., 'low':..., 'close':..., 'volume':...}
    返回 [ [date, open, high, low, close, volume, 0] ... ]，按日期升序、剔除缺失行。
    """
    out = []
    seen = set()
    for row in raw_rows:
        d = _to_date_str(row.get(keys['date']))
        o = _to_float(row.get(keys['open']))
        h = _to_float(row.get(keys['high']))
        l = _to_float(row.get(keys['low']))
        c = _to_float(row.get(keys['close']))
        v = _to_float(row.get(keys['volume']))
        if not d or o is None or h is None or l is None or c is None:
            continue
        if d in seen:
            continue
        seen.add(d)
        out.append([d, o, h, l, c, v if v is not None else 0.0, 0])
    out.sort(key=lambda r: r[0])
    return out


def rows_to_csv(rows):
    """标准行 → CSV 文本（含表头，unix 行尾）"""
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'OpenInterest'])
    w.writerows(rows)
    return buf.getvalue()


# Yahoo Finance 精选代码表（美股 / 中概 ADR / 港股 / 指数 / ETF / 大宗）
# Yahoo 无公开中文名接口，内置人工维护的常用对照，覆盖日常搜索需求
YAHOO_SYMBOLS = [
    # 美股科技/大盘
    {'code': 'AAPL', 'name': '苹果', 'en': 'Apple'},
    {'code': 'MSFT', 'name': '微软', 'en': 'Microsoft'},
    {'code': 'GOOGL', 'name': '谷歌', 'en': 'Alphabet Google'},
    {'code': 'AMZN', 'name': '亚马逊', 'en': 'Amazon'},
    {'code': 'META', 'name': 'Meta脸书', 'en': 'Meta Platforms Facebook'},
    {'code': 'NVDA', 'name': '英伟达', 'en': 'NVIDIA'},
    {'code': 'TSLA', 'name': '特斯拉', 'en': 'Tesla'},
    {'code': 'AMD', 'name': '超威半导体', 'en': 'AMD'},
    {'code': 'INTC', 'name': '英特尔', 'en': 'Intel'},
    {'code': 'NFLX', 'name': '奈飞', 'en': 'Netflix'},
    {'code': 'AVGO', 'name': '博通', 'en': 'Broadcom'},
    {'code': 'JPM', 'name': '摩根大通', 'en': 'JPMorgan Chase'},
    {'code': 'V', 'name': '维萨', 'en': 'Visa'},
    {'code': 'KO', 'name': '可口可乐', 'en': 'Coca-Cola'},
    {'code': 'WMT', 'name': '沃尔玛', 'en': 'Walmart'},
    {'code': 'XOM', 'name': '埃克森美孚', 'en': 'Exxon Mobil'},
    {'code': 'BRK-B', 'name': '伯克希尔', 'en': 'Berkshire Hathaway'},
    # 中概 ADR
    {'code': 'BABA', 'name': '阿里巴巴', 'en': 'Alibaba'},
    {'code': 'JD', 'name': '京东', 'en': 'JD.com'},
    {'code': 'PDD', 'name': '拼多多', 'en': 'PDD Holdings'},
    {'code': 'BIDU', 'name': '百度', 'en': 'Baidu'},
    {'code': 'NTES', 'name': '网易', 'en': 'NetEase'},
    {'code': 'NIO', 'name': '蔚来', 'en': 'NIO'},
    {'code': 'XPEV', 'name': '小鹏汽车', 'en': 'XPeng'},
    {'code': 'LI', 'name': '理想汽车', 'en': 'Li Auto'},
    {'code': 'BILI', 'name': '哔哩哔哩', 'en': 'Bilibili'},
    {'code': 'TME', 'name': '腾讯音乐', 'en': 'Tencent Music'},
    {'code': 'IQ', 'name': '爱奇艺', 'en': 'iQIYI'},
    {'code': 'TCEHY', 'name': '腾讯控股ADR', 'en': 'Tencent ADR'},
    # 港股
    {'code': '00700.HK', 'name': '腾讯控股', 'en': 'Tencent'},
    {'code': '09988.HK', 'name': '阿里巴巴-W', 'en': 'Alibaba HK'},
    {'code': '03690.HK', 'name': '美团-W', 'en': 'Meituan'},
    {'code': '01810.HK', 'name': '小米集团-W', 'en': 'Xiaomi'},
    {'code': '09618.HK', 'name': '京东集团-SW', 'en': 'JD HK'},
    {'code': '09888.HK', 'name': '百度集团-SW', 'en': 'Baidu HK'},
    {'code': '09999.HK', 'name': '网易-SW', 'en': 'NetEase HK'},
    {'code': '02318.HK', 'name': '中国平安', 'en': 'Ping An'},
    {'code': '01299.HK', 'name': '友邦保险', 'en': 'AIA'},
    {'code': '01398.HK', 'name': '工商银行H', 'en': 'ICBC HK'},
    {'code': '00388.HK', 'name': '香港交易所', 'en': 'HKEX'},
    {'code': '00005.HK', 'name': '汇丰控股', 'en': 'HSBC'},
    # 指数 / ETF / 大宗
    {'code': '^GSPC', 'name': '标普500指数', 'en': 'S&P 500'},
    {'code': '^IXIC', 'name': '纳斯达克指数', 'en': 'NASDAQ Composite'},
    {'code': '^DJI', 'name': '道琼斯指数', 'en': 'Dow Jones'},
    {'code': '^HSI', 'name': '恒生指数', 'en': 'Hang Seng'},
    {'code': 'SPY', 'name': '标普500ETF', 'en': 'SPDR S&P 500 ETF'},
    {'code': 'QQQ', 'name': '纳指100ETF', 'en': 'Invesco QQQ'},
    {'code': 'BTC-USD', 'name': '比特币', 'en': 'Bitcoin'},
    {'code': 'GC=F', 'name': '黄金期货', 'en': 'Gold Futures'},
    {'code': 'CL=F', 'name': '原油期货', 'en': 'Crude Oil WTI'},
]


def search_yahoo(q, limit=15):
    """Yahoo 代码搜索：代码前缀优先，其次中文名/英文名包含（大小写不敏感）"""
    q = (q or '').strip()
    if not q:
        return YAHOO_SYMBOLS[:limit]
    lq = q.lower()
    by_code, by_name = [], []
    for sym in YAHOO_SYMBOLS:
        if sym['code'].lower().startswith(lq):
            by_code.append(sym)
        elif q in sym['name'] or lq in sym['en'].lower():
            by_name.append(sym)
        if len(by_code) >= limit:
            break
    out = by_code[:limit]
    if len(out) < limit:
        out += by_name[:limit - len(out)]
    return out


# ---------------------------------------------------------------- 各源抓取

def _a_stock_prefixed(symbol):
    """6位 A股代码 → 带市场前缀（新浪源要求）"""
    if symbol[0] in '69':
        return 'sh' + symbol   # 60/68 主板科创板、9 B股
    if symbol[0] in '03':
        return 'sz' + symbol   # 00 主板、30 创业板
    raise FetchError('暂不支持该代码（当前支持 60/68/00/30 开头的沪深A股）')


def fetch_akshare_stock(symbol, start, end):
    """A股个股日线（新浪源，前复权 qfq）。

    注：东财接口对本机网络做了 TLS 指纹级拦截（python 直连被断），
    新浪源稳定可用，价格经过前复权处理。
    """
    import akshare as ak
    full = _a_stock_prefixed(symbol)
    df = ak.stock_zh_a_daily(symbol=full,
                             start_date=start.replace('-', ''),
                             end_date=end.replace('-', ''),
                             adjust='qfq')
    return normalize_ohlc_rows(df.to_dict('records'), {
        'date': 'date', 'open': 'open', 'high': 'high',
        'low': 'low', 'close': 'close', 'volume': 'volume',
    })


def fetch_akshare_index(symbol, start, end):
    """A股指数日线（新浪源，全量拉取后按区间过滤）。返回标准行。"""
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=symbol)
    rows = normalize_ohlc_rows(df.to_dict('records'), {
        'date': 'date', 'open': 'open', 'high': 'high',
        'low': 'low', 'close': 'close', 'volume': 'volume',
    })
    return [r for r in rows if start <= r[0] <= end]


def fetch_yfinance(symbol, start, end):
    """Yahoo Finance 日线。返回标准行。"""
    import yfinance as yf
    df = yf.download(symbol, start=start, end=end, progress=False,
                     auto_adjust=True)
    if df is None or df.empty:
        raise FetchError(f'Yahoo 未返回数据，请检查代码 {symbol} 是否正确')
    # 兼容 MultiIndex 列 (Price, Ticker) 与单级列
    if isinstance(df.columns, __import__('pandas').MultiIndex):
        df.columns = df.columns.get_level_values(0)
    records = []
    for idx, row in df.iterrows():
        d = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
        records.append({'date': d, 'open': row.get('Open'),
                        'high': row.get('High'), 'low': row.get('Low'),
                        'close': row.get('Close'), 'volume': row.get('Volume')})
    return normalize_ohlc_rows(records, {
        'date': 'date', 'open': 'open', 'high': 'high',
        'low': 'low', 'close': 'close', 'volume': 'volume',
    })


FETCHERS = {
    'akshare-a': fetch_akshare_stock,
    'akshare-index': fetch_akshare_index,
    'yfinance': fetch_yfinance,
}


# ---------------------------------------------------------------- 股票名称搜索

_stock_list = None


def get_stock_list(force=False):
    """A股 代码-名称 清单（内存 + 磁盘双层缓存，7 天过期）。

    全量拉取约需 30-40 秒（交易所分页接口），首次调用后落盘。
    """
    global _stock_list
    if _stock_list is not None and not force:
        return _stock_list
    if not force and os.path.isfile(STOCK_LIST_CACHE) and \
            time.time() - os.path.getmtime(STOCK_LIST_CACHE) < STOCK_LIST_TTL:
        with open(STOCK_LIST_CACHE, encoding='utf-8') as f:
            _stock_list = json.load(f)
        return _stock_list

    import akshare as ak
    df = ak.stock_info_a_code_name()
    _stock_list = [{'code': str(r['code']).zfill(6), 'name': str(r['name']).strip()}
                   for r in df.to_dict('records')]
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(STOCK_LIST_CACHE, 'w', encoding='utf-8') as f:
        json.dump(_stock_list, f, ensure_ascii=False)
    return _stock_list


def _norm_name(s):
    """去掉名称中的空白（含全角），便于 '万科A' 匹配 '万  科Ａ'"""
    return re.sub(r'[\s\u3000]+', '', str(s))


def search_stocks(q, limit=15):
    """按代码前缀或中文名包含搜索。代码前缀匹配优先于名称匹配。"""
    q = (q or '').strip()
    if not q:
        return []
    lst = get_stock_list()
    nq = _norm_name(q)
    by_code, by_name = [], []
    for s in lst:
        code, name = s['code'], s['name']
        if code.startswith(q):
            by_code.append(s)
        elif nq and nq in _norm_name(name):
            by_name.append(s)
        if len(by_code) >= limit + 5:
            break
    by_code.sort(key=lambda s: s['code'])  # 代码前缀结果按代码排序，稳定可预期
    out = by_code[:limit]
    if len(out) < limit:
        out += by_name[:limit - len(out)]
    return out


def search_indexes(q, limit=15):
    """指数搜索（静态表）"""
    q = (q or '').strip()
    if not q:
        return INDEXES[:limit]
    nq = _norm_name(q)
    out = [s for s in INDEXES
           if s['code'].startswith(q) or nq in _norm_name(s['name'])]
    return out[:limit]


def lookup_symbol_name(provider, symbol):
    """按代码查中文名（用于生成可读文件名）；查不到返回 None，不影响拉取"""
    try:
        if provider == 'akshare-index':
            return next((i['name'] for i in INDEXES if i['code'] == symbol), None)
        if provider == 'akshare-a':
            for s in get_stock_list():
                if s['code'] == symbol:
                    return s['name']
    except Exception:
        return None
    return None


def _safe_name(name):
    """名称 → 文件名安全片段（去空白与非法字符，限长）"""
    name = re.sub(r'[\s\u3000/\\:*?"<>|]+', '', str(name))
    return name[:20]


def fetch_to_csv(provider, symbol, start, end, uploads_dir):
    """拉取 + 清洗 + 落盘（文件名含股票中文名）。返回 {path, rows}。"""
    meta = PROVIDERS.get(provider)
    if not meta:
        raise FetchError(f'未知数据源: {provider}')
    symbol = symbol.strip()
    if not re.fullmatch(meta['symbol_re'], symbol):
        raise FetchError(f'{meta["symbol_hint"]}（收到: "{symbol}"）')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', start) or \
            not re.fullmatch(r'\d{4}-\d{2}-\d{2}', end):
        raise FetchError('日期格式应为 YYYY-MM-DD')
    if start >= end:
        raise FetchError('开始日期必须早于结束日期')

    try:
        rows = FETCHERS[provider](symbol, start, end)
    except FetchError:
        raise
    except Exception as e:
        raise FetchError(
            f'抓取失败（网络或接口异常）: {type(e).__name__}: {e}\n'
            f'yfinance 源在国内网络下通常需要开启代理')

    if not rows:
        raise FetchError('该区间无数据，请检查代码与日期范围')
    if len(rows) < 20:
        raise FetchError(f'仅获取到 {len(rows)} 行（不足 20），请扩大日期范围')

    os.makedirs(uploads_dir, exist_ok=True)
    name = lookup_symbol_name(provider, symbol)
    fname = f'{provider}-{symbol}'
    if name:
        fname += f'-{_safe_name(name)}'
    fname += f'-{start}_{end}.csv'
    path = os.path.join(uploads_dir, fname)
    with open(path, 'w', newline='') as f:
        f.write(rows_to_csv(rows))
    return {'path': path, 'rows': len(rows)}
