# Все варианты (лесенка 2916 + правило «сильнейший ход» 432) → итог по неделям
import sys, itertools, json
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
from dataclasses import replace
from multiprocessing import Pool
bars=T.load('data/gold_M15.csv'); m30=T.to_m30(bars); atr=T.atr_series(m30)
sigs=[]
for n,mv in itertools.product((2,3,4),(1.0,1.5,2.0,3.0)): sigs.append(dict(mode='bars',n=n,move=mv))
for n,er,mv in itertools.product((4,6,8),(0.5,0.7),(1.5,2.5)): sigs.append(dict(mode='er',n=n,er=er,move=mv))
for n in (10,20,40): sigs.append(dict(mode='donch',n=n))
exits=[dict(r=r,t=t,maxpos=mp,s=1.0) for r,t,mp in itertools.product((0.5,1.0,1.5),(1.5,2.5,4.0),(1,3,5))]
jobs=[{**a,**b,'ema':e,'fade':f} for a in sigs for b in exits for e in (0,100) for f in (False,True)]
jobs+=[dict(mode='leg',W=W,bounce=b,mindur=md,spikecap=sc,bm=bm,r=1.0,t=t,maxpos=mp,s=1.0)
      for W,b,md,sc,bm,t,mp in itertools.product((48,96),(0.2,0.3,0.4),(6,12),(0.0,0.35),(1.0,1.5),(1.0,1.5,2.0),(1,3,5))]
def work(kw):
    s,_=T.run(bars,replace(T.P(),**kw),m30,atr)
    w={}
    for x in s:
        y,wk,_=T.dtime(x[0]).isocalendar(); k=f'{y}-{wk:02d}'; a=w.setdefault(k,[0.0,0]); a[0]+=x[2]; a[1]+=1
    return kw, w
if __name__=='__main__':
    with Pool(2) as pool: res=pool.map(work,jobs,chunksize=16)
    json.dump(res,open('wf_week.json','w')); print('done',len(res),flush=True)
