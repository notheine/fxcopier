"""
Проверка подхода «смотрим на последние месяцы и берём то, что там лучше всего сработало» (07.10.2026).

1) Все 2 916 вариантов настроек «трендовой лесенки» (tools/trend_bt.py), включая вход «наоборот»,
   прогоняются по истории; итог каждого — по месяцам.
2) Лучшие задним числом на 2026 год.
3) Скользящий подбор: каждый месяц берём настройки, лучшие за последние 1/2/3/6 мес.,
   и торгуем ими следующий месяц (честная проверка, без заглядывания вперёд).

Запуск: python tools/trend_wf.py gold_M15.csv   (≈12 мин на 2 ядрах)
Итоги — docs/RESEARCH.md.
"""
import sys, itertools, json
sys.path.insert(0, __import__('os').path.dirname(__file__))
import trend_bt as T
from dataclasses import replace
from multiprocessing import Pool
F=sys.argv[1]
bars=T.load(F); m30=T.to_m30(bars); atr=T.atr_series(m30)
sigs=[]
for n,mv in itertools.product((2,3,4),(1.0,1.5,2.0,3.0)): sigs.append(dict(mode='bars',n=n,move=mv))
for n,er,mv in itertools.product((4,6,8),(0.5,0.7),(1.5,2.5)): sigs.append(dict(mode='er',n=n,er=er,move=mv))
for n in (10,20,40): sigs.append(dict(mode='donch',n=n))
exits=[dict(r=r,t=t,maxpos=mp,s=1.0) for r,t,mp in itertools.product((0.5,1.0,1.5),(1.5,2.5,4.0),(1,3,5))]
jobs=[{**a,**b,'ema':e,'fade':f} for a in sigs for b in exits for e in (0,100) for f in (False,True)]
def work(kw):
    s,_=T.run(bars,replace(T.P(),**kw),m30,atr)
    m={}
    for x in s:
        k=T.dtime(x[0]).strftime('%Y-%m'); a=m.setdefault(k,[0.0,0]); a[0]+=x[2]; a[1]+=1
    return kw, m
def analyse(res):
    import statistics as st
    months=sorted({m for _,d in res for m in d})
    C=len(res)
    R=[[d.get(m,[0,0])[0] for m in months] for _,d in res]
    N=[[d.get(m,[0,0])[1] for m in months] for _,d in res]
    def desc(kw):
        s=f"{kw['mode']} n{kw['n']}"
        if kw['mode']=='bars': s+=f" ход≥{kw['move']}"
        if kw['mode']=='er': s+=f" er{kw['er']} ход≥{kw['move']}"
        s+=f" стоп{kw['r']} трейл{kw['t']}R поз{kw['maxpos']}"+(' EMA' if kw['ema'] else '')+(' НАОБОРОТ' if kw['fade'] else '')
        return s
    print('месяцев', len(months), months[0], '…', months[-1], 'конфигураций', C)
    i26=[i for i,m in enumerate(months) if m.startswith('2026')]
    i25=[i for i,m in enumerate(months) if m.startswith('2025')]
    # 1) лучшие на 2026 (подгонка задним числом)
    tot26=sorted(range(C), key=lambda c:-sum(R[c][i] for i in i26))
    print('\nЛУЧШИЕ НА 2026 (задним числом):')
    for c in tot26[:5]:
        print(f'  {desc(res[c][0]):60s} 2026: {sum(R[c][i] for i in i26):+6.1f}R ({sum(N[c][i] for i in i26)} серий)  тот же в 2025: {sum(R[c][i] for i in i25):+6.1f}R')
        print('     по месяцам 2026:', ' '.join(f'{R[c][i]:+.0f}' for i in i26))
    allc=[sum(R[c][i] for i in i26) for c in range(C)]
    print(f'  все конфигурации в 2026: медиана {st.median(allc):+.1f}R, в плюсе {sum(x>0 for x in allc)/C*100:.0f}%')
    # 2) walk-forward
    def wf(L, idx, top=1, minn=8):
        out=[]; picks=[]
        for i in idx:
            if i<L: continue
            sc=[(sum(R[c][i-L:i]), c) for c in range(C) if sum(N[c][i-L:i])>=minn*L]
            sc.sort(reverse=True)
            best=[c for _,c in sc[:top]]
            out.append(st.mean(R[c][i] for c in best)); picks.append(best[0])
        return out, picks
    base=lambda idx: [st.mean(R[c][i] for c in range(C)) for i in idx]
    for label,idx in (('2026 (янв–окт)', i26), ('вся история', list(range(len(months))))):
        print(f'\nПОДБОР ПО ПОСЛЕДНИМ МЕСЯЦАМ → торгуем следующий месяц: {label}')
        b=base([i for i in idx if i>=6]); 
        print(f'  случайная конфигурация (среднее): {sum(b):+.1f}R за {len(b)} мес.')
        for L in (1,2,3,6):
            for top in (1,10):
                o,p=wf(L,[i for i in idx if i>=6],top)
                pos=sum(x>0 for x in o)
                print(f'  смотрим {L} мес., берём лучшие {top:2d}: итог {sum(o):+7.1f}R  месяцев в плюсе {pos}/{len(o)}  по месяцам: '+' '.join(f'{x:+.0f}' for x in o))


if __name__=='__main__':
    print(len(jobs),'jobs',flush=True)
    with Pool(2) as pool: res=pool.map(work,jobs,chunksize=16)
    analyse(json.loads(json.dumps(res)))

