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

def calculate_anomalies(current_stocks, previous_stocks):
    """
    通过两期快照比对计算短窗(5-15分钟)价格差分异动:
    Delta = Pct_current - Pct_previous
    """
    if not previous_stocks:
        return {
            "surges": [],
            "plunges": [],
            "co_exposure": "历史快照不存在（首轮运行，无法计算差分）"
        }

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

    # 排序寻找价差异动
    deltas_sorted = sorted(deltas, key=lambda x: x['delta_pct'], reverse=True)
    surges = [s for s in deltas_sorted if s['delta_pct'] >= 1.5][:3]
    plunges = [s for s in deltas_sorted if s['delta_pct'] <= -1.5][:3]

    co_exposure = "未形成单一因子共性暴露" if len(surges) <= 1 else "形成局部板块脉冲共性"

    return {
        "surges": surges,
        "plunges": plunges,
        "co_exposure": co_exposure,
        "total_compared": len(deltas),
        "max_delta": deltas_sorted[0]['delta_pct'] if deltas_sorted else 0.0,
        "min_delta": deltas_sorted[-1]['delta_pct'] if deltas_sorted else 0.0
    }

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

    # 1. 备份现有快照为 live_snapshot_prev.json 用于双向核实
    cur_path = "data/live_snapshot.json"
    prev_path = "data/live_snapshot_prev.json"
    prev_stocks = []
    if os.path.exists(cur_path):
        try:
            with open(cur_path, "r", encoding="utf-8") as f:
                prev_data = json.load(f)
                prev_stocks = prev_data.get("stocks", [])
            # 复制为 prev 快照
            with open(prev_path, "w", encoding="utf-8") as f:
                json.dump(prev_data, f, ensure_ascii=False, indent=2)
        except Exception: pass

    # 2. 计算差分
    anomalies = calculate_anomalies(stocks, prev_stocks)

    # 3. 宽基与行业
    url_idx = "http://hq.sinajs.cn/list=sh000001,sz399001,sh510300,sh588000," + ",".join(INDUSTRY_MAP.keys())
    sh_amt, sz_amt, etf_300, etf_588 = 0.0, 0.0, 0.0, 0.0
    sectors = []
    try:
        with urllib.request.urlopen(urllib.request.Request(url_idx, headers=HEADERS), timeout=6) as r:
            for line in r.read().decode('gbk', errors='ignore').strip().split('\n'):
                if '=' in line:
                    sym = line.split('=')[0].replace('var hq_str_', '').strip()
                    f = line.split('=')[1].strip('";\r\n').split(',')
                    if sym == 'sh000001' and len(f) > 9: sh_amt = float(f[9] or 0) / 1e8
                    elif sym == 'sz399001' and len(f) > 9: sz_amt = float(f[9] or 0) / 1e8
                    elif sym == 'sh510300' and len(f) > 9: etf_300 = float(f[9] or 0) / 1e8
                    elif sym == 'sh588000' and len(f) > 9: etf_588 = float(f[9] or 0) / 1e8
                    elif sym in INDUSTRY_MAP and len(f) > 9:
                        pc = float(f[2] or 0)
                        p = float(f[3] or 0)
                        amt = float(f[9] or 0) / 1e8
                        pct = ((p - pc)/pc*100) if pc else 0.0
                        sectors.append({'name': INDUSTRY_MAP[sym], 'pct': pct, 'amt': amt})
    except Exception as e:
        print(f"Index fetch error: {e}")

    snapshot = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'stocks': stocks,
        'index_amt': {
            'sh_amt': sh_amt,
            'sz_amt': sz_amt,
            'etf_300_amt': etf_300,
            'etf_588_amt': etf_588
        },
        'sectors': sectors,
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
    print(f"Snapshot written: {len(stocks)} stocks, max_delta={anomalies['max_delta']}%, min_delta={anomalies['min_delta']}%")

if __name__ == '__main__':
    build_snapshot()
