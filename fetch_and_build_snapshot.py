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

# 真正的全区间等距跳跃分层抽样配置 (步长 k ≈ 4~5，全周期覆盖老股至最新次新股)
SYSTEMATIC_PLAN = [
    ('sh_a', [1, 5, 10, 15, 20, 25, 30, 34], 50), # 沪市主板 (400只，覆盖 600, 601, 603, 605)
    ('sz_a', [1, 4, 8, 12, 16, 20, 24, 28], 50),  # 深市主板 (400只，覆盖 000, 001, 002, 003)
    ('cyb',  [1, 6, 11, 16, 21, 27], 50),         # 创业板   (300只，覆盖 300, 301)
    ('kcb',  [1, 4, 8, 11], 50),                  # 科创板   (200只，覆盖 688001 -> 6887xx)
    ('hs_bjs', [1, 5], 50),                       # 北交所   (100只，覆盖 920000 -> 9206xx)
]

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

    # 全局严格按股票代码升序排序
    stocks.sort(key=lambda x: x['code'])

    # 宽基与行业行情拉取
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
        'meta': {
            'sample_count': len(stocks),
            'sector_count': len(sectors),
            'prefix_breakdown': {
                '000_sz_main': sum(1 for s in stocks if s['code'].startswith(('000', '001', '002', '003'))),
                '300_chinext': sum(1 for s in stocks if s['code'].startswith(('300', '301'))),
                '600_sh_main': sum(1 for s in stocks if s['code'].startswith(('600', '601', '603', '605'))),
                '688_star': sum(1 for s in stocks if s['code'].startswith(('688', '689'))),
                '920_bse': sum(1 for s in stocks if s['code'].startswith(('920', '83', '87', '43')))
            },
            'span_check': {
                'sh_main_span': f"{[s['code'] for s in stocks if s['code'].startswith(('600','601','603','605'))][0]} -> {[s['code'] for s in stocks if s['code'].startswith(('600','601','603','605'))][-1]}",
                'sz_main_span': f"{[s['code'] for s in stocks if s['code'].startswith(('000','001','002','003'))][0]} -> {[s['code'] for s in stocks if s['code'].startswith(('000','001','002','003'))][-1]}",
                'chinext_span': f"{[s['code'] for s in stocks if s['code'].startswith(('300','301'))][0]} -> {[s['code'] for s in stocks if s['code'].startswith(('300','301'))][-1]}",
                'star_span': f"{[s['code'] for s in stocks if s['code'].startswith(('688','689'))][0]} -> {[s['code'] for s in stocks if s['code'].startswith(('688','689'))][-1]}",
                'bse_span': f"{[s['code'] for s in stocks if s['code'].startswith(('920','83','87','43'))][0]} -> {[s['code'] for s in stocks if s['code'].startswith(('920','83','87','43'))][-1]}",
            }
        }
    }
    
    os.makedirs('data', exist_ok=True)
    with open('data/live_snapshot.json', 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"Snapshot written: {len(stocks)} stocks, {len(sectors)} sectors")

if __name__ == '__main__':
    build_snapshot()
