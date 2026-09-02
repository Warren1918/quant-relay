import urllib.request
import json
import time
import os
import sys

HEADERS = {'Referer': 'http://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}

INDUSTRY_MAP = {
    'sz399435': '石油化工', 'sz399440': '煤炭能源', 'sh000932': '钢铁黑色',
    'sz399436': '有色金属', 'sz399441': '非银金融', 'sz399363': '芯片半导体',
    'sz399437': '计算机软件', 'sz399438': '互联网传媒', 'sz399433': '新能源光伏',
    'sz399997': '白酒消费', 'sz399986': '汽车制造'
}

SYSTEMATIC_PLAN = [
    ('sh_a', [1, 5, 10, 15, 20, 25, 30, 34], 50),
    ('sz_a', [1, 4, 8, 12, 16, 20, 24, 28], 50),
    ('cyb',  [1, 6, 11, 16, 21, 27], 50),
    ('kcb',  [1, 4, 8, 11], 50),
    ('hs_bjs', [1, 5], 50),
]

INDEX_SYMBOLS = {
    'sh000001': '上证指数',
    'sz399001': '深证成指',
    'sz399006': '创业板指',
    'sh000688': '科创50',
    'sh000852': '中证1000',
    'sh510300': '沪深300ETF',
    'sh588000': '科创50ETF'
}

def calculate_anomalies(current_stocks, previous_stocks):
    if not previous_stocks:
        return {"surges": [], "plunges": [], "co_exposure": "无历史缓存（首期快照）"}

    prev_map = {s['code']: s['pct'] for s in previous_stocks}
    deltas = []
    for s in current_stocks:
        if s['code'] in prev_map:
            d = s['pct'] - prev_map[s['code']]
            deltas.append({
                'code': s['code'],
                'name': s['name'],
                'pct_current': s['pct'],
                'pct_prev': prev_map[s['code']],
                'delta_pct': round(d, 2)
            })

    deltas_sorted = sorted(deltas, key=lambda x: x['delta_pct'], reverse=True)
    surges = [s for s in deltas_sorted if s['delta_pct'] >= 1.5][:3]
    plunges = [s for s in deltas_sorted if s['delta_pct'] <= -1.5][:3]

    co_exposure = "未形成单一因子共性暴露" if len(surges) <= 1 else "形成局部板块脉冲共性"

    return {
        "surges": surges,
        "plunges": plunges,
        "co_exposure": co_exposure
    }

def fetch_sector_money_flows():
    """从新浪官方 MoneyFlow 接口实时抓取行业主力大单资金净流向"""
    u_in = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk?page=1&num=3&sort=netamount&asc=0&fenlei=0'
    u_out = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk?page=1&num=3&sort=netamount&asc=1&fenlei=0'
    top_in, top_out = [], []
    try:
        req = urllib.request.Request(u_in, headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode('utf-8', errors='ignore'))
        for it in data:
            top_in.append({'name': it.get('name'), 'net_amt_yi': round(float(it.get('netamount', 0))/1e8, 2)})
    except Exception as e:
        print(f"Inflow fetch error: {e}")

    try:
        req = urllib.request.Request(u_out, headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode('utf-8', errors='ignore'))
        for it in data:
            top_out.append({'name': it.get('name'), 'net_amt_yi': round(float(it.get('netamount', 0))/1e8, 2)})
    except Exception as e:
        print(f"Outflow fetch error: {e}")

    return {'top_inflows': top_in, 'top_outflows': top_out}

def build_snapshot():
    stocks = []
    seen_codes = set()

    for node, pages, num in SYSTEMATIC_PLAN:
        for p in pages:
            url = f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={p}&num={num}&sort=symbol&asc=1&node={node}&symbol=&_s_r_a=page"
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=6) as r:
                    items = json.loads(r.read().decode('utf-8', errors='ignore'))
                    for it in items:
                        code = it.get('code', '')
                        if code and code not in seen_codes:
                            seen_codes.add(code)
                            stocks.append({
                                'code': code,
                                'name': it.get('name', ''),
                                'pct': float(it.get('changepercent', 0) or 0),
                                'trade': float(it.get('trade', 0) or 0),
                                'amt': float(it.get('amount', 0) or 0),
                                'turnover': float(it.get('turnoverratio', 0) or 0),
                                'mktcap': float(it.get('mktcap', 0) or 0)
                            })
            except Exception as e:
                print(f"Node {node} page {p} error: {e}")
            time.sleep(0.04)

    stocks.sort(key=lambda x: x['code'])

    # 差分历史
    cur_path = "data/live_snapshot.json"
    prev_path = "data/live_snapshot_prev.json"
    prev_stocks = []
    if os.path.exists(cur_path):
        try:
            with open(cur_path, "r", encoding="utf-8") as f:
                prev_data = json.load(f)
                prev_stocks = prev_data.get("stocks", [])
            with open(prev_path, "w", encoding="utf-8") as f:
                json.dump(prev_data, f, ensure_ascii=False, indent=2)
        except Exception: pass

    anomalies = calculate_anomalies(stocks, prev_stocks)

    # 1. 宽基与行业全景行情拉取 (现价、涨跌幅、成交额)
    all_syms = list(INDEX_SYMBOLS.keys()) + list(INDUSTRY_MAP.keys())
    url_idx = f"http://hq.sinajs.cn/list={','.join(all_syms)}"
    indices = {}
    sectors = []
    try:
        with urllib.request.urlopen(urllib.request.Request(url_idx, headers=HEADERS), timeout=6) as r:
            for line in r.read().decode('gbk', errors='ignore').strip().split('\n'):
                if '=' in line:
                    sym = line.split('=')[0].replace('var hq_str_', '').strip()
                    f = line.split('=')[1].strip('";\r\n').split(',')
                    if len(f) > 9:
                        name = f[0]
                        price = float(f[3] or 0)
                        prev_close = float(f[2] or 0)
                        pct = ((price - prev_close)/prev_close*100) if prev_close else 0.0
                        amt = float(f[9] or 0) / 1e8
                        if sym in INDEX_SYMBOLS:
                            indices[sym] = {'name': INDEX_SYMBOLS[sym], 'price': round(price, 2), 'pct': round(pct, 2), 'amt': round(amt, 1)}
                        elif sym in INDUSTRY_MAP:
                            sectors.append({'name': INDUSTRY_MAP[sym], 'price': round(price, 2), 'pct': round(pct, 2), 'amt': round(amt, 1)})
    except Exception as e:
        print(f"Index fetch error: {e}")

    # 2. 行业主力大单资金流
    sector_flows = fetch_sector_money_flows()

    snapshot = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'stocks': stocks,
        'indices': indices,
        'sectors': sectors,
        'sector_flows': sector_flows,
        'anomalies': anomalies,
        'meta': {
            'sample_count': len(stocks),
            'sector_count': len(sectors),
            'prefix_breakdown': {
                '000_sz_main': sum(1 for s in stocks if s['code'].startswith(('000', '001', '002', '003'))),
                '300_chinext': sum(1 for s in stocks if s['code'].startswith(('300', '301'))),
                '600_sh_main': sum(1 for s in stocks if s['code'].startswith(('600', '601', '603', '605'))),
                '688_star': sum(1 for s in stocks if s['code'].startswith(('688', '689'))),
                '920_bse': sum(1 for s in stocks if s['code'].startswith(('920', '83', '87', '43')))
            }
        }
    }
    
    os.makedirs('data', exist_ok=True)
    with open('data/live_snapshot.json', 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"Snapshot written: {len(stocks)} stocks, indices: {len(indices)}, flows: {len(sector_flows.get('top_inflows', []))}")

if __name__ == '__main__':
    build_snapshot()
