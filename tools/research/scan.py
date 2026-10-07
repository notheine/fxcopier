"""Широкий поиск простых правил. Каждое правило -> сделки (вход по open свечи, выход по времени или стопу).
Издержки: спред свечи + $0.05, комиссия $0.07 (на 1 унцию = 0.01 лота). Итог в $ на 0.01 лота и по годам."""
import sys, statistics as st, itertools
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
from collections import defaultdict
COST_ADD=0.05+0.07
def prep(path):
    b=T.load(path); m=T.to_m30(b) if path.endswith('M15.csv') else [[x[0],x[1],x[2],x[3],x[4],i,i] for i,x in enumerate(b)]
    a=T.atr_series(m)
    return b,m,a
def trade_time(m, j_in, d, hold, sp_in, sp_out):
    """вход open свечи j_in, выход open свечи j_in+hold (если без большого перерыва)"""
    j_out=j_in+hold
    if j_out>=len(m) or m[j_out][0]-m[j_in][0]>hold*1800+3600: return None
    pin=m[j_in][1]; pout=m[j_out][1]
    return d*(pout-pin) - sp_in - COST_ADD    # спред платим один раз (вход по ask/выход по bid или наоборот)
def evaluate(m,a,bars,rule,hold):
    out=defaultdict(list); last=-10
    for j in range(100,len(m)-hold-1):
        if j-last<hold: continue
        d=rule(m,a,j)
        if not d: continue
        if m[j+1][0]-m[j][0]>3600: continue
        sp=bars[m[j+1][5]][5]+0.05
        if sp>0.6: continue
        r=trade_time(m,j+1,d,hold,sp,sp)
        if r is None: continue
        out[T.dtime(m[j+1][0]).year].append(r/a[j])   # в долях ATR, чтобы годы были сравнимы
        last=j
    return out
# --- правила (сигнал на закрытии свечи j) ---
def fade_donch(N):
    def f(m,a,j):
        hi=max(x[2] for x in m[j-N:j]); lo=min(x[3] for x in m[j-N:j]); c=m[j][4]
        return -1 if c>hi else (1 if c<lo else 0)
    return f
def fade_bar(k):
    def f(m,a,j):
        mv=m[j][4]-m[j][1]
        return -1 if mv>k*a[j-1] else (1 if mv<-k*a[j-1] else 0)
    return f
def fade_run(n,k):
    def f(m,a,j):
        mv=m[j][4]-m[j-n][4]
        return -1 if mv>k*a[j-n] else (1 if mv<-k*a[j-n] else 0)
    return f
RULES={}
for N in (10,20,40,80): RULES[f'против пробоя {N} свечей']=fade_donch(N)
for k in (1.0,1.5,2.0,3.0): RULES[f'против свечи >{k}ATR']=fade_bar(k)
for n,k in ((4,2.0),(4,3.0),(8,3.0),(8,4.0)): RULES[f'против хода {n} свечей >{k}ATR']=fade_run(n,k)
if __name__=='__main__':
    res={}
    for src in ('data/gold_M15.csv','data/gold_M30.csv'):
        bars,m,a=prep(src)
        for (name,rule),hold in itertools.product(RULES.items(),(1,2,4,8)):
            o=evaluate(m,a,bars,rule,hold)
            res.setdefault(name+f' | держим {hold*0.5:g}ч',{}).update({(src[-7:-4],y):(st.mean(v),len(v),st.pstdev(v)) for y,v in o.items()})
    for k,v in res.items():
        ys=sorted(y for s,y in v if s=='M15'); 
        line=f'{k:40s}'
        allv=[]
        for y in ys:
            mu,n,sd=v[('M15',y)]; line+=f' {y}:{mu:+.3f}'; allv.append(mu)
        if ('M30',2021) in v: mu,n,sd=v[('M30',2021)]; line+=f' | 2021*:{mu:+.3f}'
        mu22=v.get(('M30',2022),(0,0,0))[0]; line+=f' 2022*:{mu22:+.3f}'
        pos=sum(x>0 for x in allv)
        line+=f' | лет в плюсе {pos}/{len(allv)} n/год~{v[("M15",2024)][1]}'
        print(line)
