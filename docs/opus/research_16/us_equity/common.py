import json,datetime,math,csv,os
A=r"C:/Users/Xu/Documents/中性回路整合工程/.artifacts/research_us_equity"
def _nth_sunday(y,m,n):
    d=datetime.date(y,m,1); d+=datetime.timedelta((6-d.weekday())%7); return d+datetime.timedelta(7*(n-1))
def ny_dt(ts):
    u=datetime.datetime.utcfromtimestamp(ts)
    y=u.year; s=datetime.datetime.combine(_nth_sunday(y,3,2),datetime.time(7)); e=datetime.datetime.combine(_nth_sunday(y,11,1),datetime.time(6))
    if y<2007: s=datetime.datetime.combine(_nth_sunday(y,4,1),datetime.time(7)); e=datetime.datetime.combine(_nth_sunday(y,10,5) if _nth_sunday(y,10,5).month==10 else _nth_sunday(y,10,4),datetime.time(6))
    off=-4 if s<=u<e else -5
    return u+datetime.timedelta(hours=off)
def yahoo(fn):
    d=json.load(open(os.path.join(A,fn)))['chart']['result'][0]; q=d['indicators']['quote'][0]; out=[]
    for i,t in enumerate(d['timestamp']):
        o,h,l,c=q['open'][i],q['high'][i],q['low'][i],q['close'][i]
        if None in (o,h,l,c): continue
        out.append((ny_dt(t),o,h,l,c))
    return out
def cboe(name):
    out={}
    for r in csv.DictReader(open(os.path.join(A,f'cboe_{name}_History.csv'))):
        m,d,y=r['DATE'].split('/'); out[f'{y}-{m}-{d}']=(float(r['OPEN']),float(r['CLOSE']))
    return out
def stats(z):
    z=[x for x in z if x is not None]; n=len(z)
    if n==0: return {'n':0}
    a=sorted(abs(x) for x in z)
    return {'n':n,'rv_iv':round(math.sqrt(sum(x*x for x in z)/n),3),'gt1':round(sum(1 for x in a if x>1)/n,3),'med_abs_z':round(a[n//2] if n%2 else (a[n//2-1]+a[n//2])/2,3)}
def quantile(v,p):
    s=sorted(v); k=(len(s)-1)*p; f=int(k); return s[f]+(s[min(f+1,len(s)-1)]-s[f])*(k-f)
