import urllib.request, json, sys, os
A=r"C:/Users/Xu/Documents/中性回路整合工程/.artifacts/research_us_equity"
UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36','Accept':'*/*'}
def get(url):
    r=urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=60); return r.read()
jobs=[]
for s in ['VIXCLS','SP500','VXVCLS']:
    jobs.append((f'fred_{s}.csv',f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}'))
for sym,fn,rng,iv in [('%5EVIX1D','VIX1D_1d','max','1d'),('%5EVIX9D','VIX9D_1d','10y','1d'),('%5EVIX','VIX_1d','10y','1d'),('%5EGSPC','GSPC_1d','10y','1d'),('SPY','SPY_1d','10y','1d'),('%5EGSPC','GSPC_1h','730d','1h'),('SPY','SPY_1h','730d','1h'),('%5EGSPC','GSPC_5m','60d','5m'),('SPY','SPY_5m','60d','5m'),('%5EVIX1D','VIX1D_1h','730d','1h'),('%5EVIX','VIX_1h','730d','1h')]:
    jobs.append((fn+'.json',f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}'))
for fn,url in jobs:
    try:
        b=get(url); open(os.path.join(A,fn),'wb').write(b); print(fn,len(b))
    except Exception as e: print(fn,'ERR',e)
