# «Включать правило, только когда оно в последнее время работает»: помогает ли?
import sys
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
sys.path.insert(0, __import__('os').path.dirname(__file__))
import orb2 as O
from orb3 import orb_r  # noqa: E402
from dataclasses import replace
def periods(trades, key):
    p={}
    for d,r in trades: p[key(d)]=p.get(key(d),0)+r
    return p
def test(trades, label):
    for nm,key,Ls in (('неделя',lambda d:'%d-%02d'%d.isocalendar()[:2],(1,2,4,8)),('месяц',lambda d:d.strftime('%Y-%m'),(1,2,3,6))):
        p=periods(trades,key); ks=sorted(p); v=[p[k] for k in ks]
        line=f'  {label} по {nm}ям: всегда {sum(v[8:]):+.0f}R'
        for L in Ls:
            on=[v[i] for i in range(8,len(v)) if sum(v[i-L:i])>0]
            off=[v[i] for i in range(8,len(v)) if sum(v[i-L:i])<=0]
            line+=f' | {L}: вкл {sum(on):+.0f}R ({len(on)}), выкл-пропущено {sum(off):+.0f}R'
        print(line)
b,days=O.load_days('data/gold_M15.csv')
orbt=[(d,r) for d,dr,r in orb_r(b,days,asia=(1,10))]
test(orbt,'пробой Азии')
m=T.to_m30(b); a=T.atr_series(m)
s,_=T.run(b,replace(T.P(),mode='leg',W=48,bounce=0.3,mindur=6,bm=1.5,r=1.0,t=2.0,maxpos=3,s=1.0),m,a)
test([(T.dtime(x[0]),x[2]) for x in s],'сильнейший ход')
s,_=T.run(b,replace(T.P(),mode='bars',n=3,move=1.5,r=0.6,s=1.0,t=2.0,maxpos=4),m,a)
test([(T.dtime(x[0]),x[2]) for x in s],'лесенка')
