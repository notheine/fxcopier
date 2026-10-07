# «Азиатские часы»: покупка в 01:15–01:30, продажа в 04:00. Издержки: спред входа (из свечи, +0.05) + комиссия 0.07.
import sys, statistics as st
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
def run(path, h0, m0, h1, m1, stop_atr=0.0, days=(0,1,2,3,4)):
    b=T.load(path); idx={x[0]:i for i,x in enumerate(b)}
    m=T.to_m30(b); a=T.atr_series(m); ai={x[0]:a[k] for k,x in enumerate(m)}
    tr=[]
    for i,(t,o,h,l,c,sp) in enumerate(b):
        d=T.dtime(t)
        if not (d.hour==h0 and d.minute==m0 and d.weekday() in days): continue
        t1=t+((h1*60+m1)-(h0*60+m0))*60
        if t1 not in idx: continue
        ent=o+sp+0.05; atr=ai.get(t//1800*1800-1800, 5.0)
        stop=ent-stop_atr*atr if stop_atr else None
        out=None
        for j in range(i, idx[t1]):
            if stop is not None and b[j][3]<=stop: out=min(stop,b[j][1])-0.1; break
        if out is None: out=b[idx[t1]][1]
        tr.append((d, out-ent-0.07))
    return tr
def report(tr, label):
    yrs={}; mon={}
    for d,p in tr: yrs.setdefault(d.year,[]).append(p); mon.setdefault(d.strftime('%Y-%m'),[]).append(p)
    eq=pk=dd=0
    for d,p in tr: eq+=p; pk=max(pk,eq); dd=max(dd,pk-eq)
    ms=[sum(v) for v in mon.values()]
    print(f'{label}: сделок {len(tr)}, итог ${sum(p for _,p in tr):+.0f} на 0.01 лота, в плюс {100*sum(p>0 for _,p in tr)/len(tr):.0f}%, '
          f'месяцев в плюсе {sum(x>0 for x in ms)}/{len(ms)}, макс. просадка ${dd:.0f}')
    print('    по годам: '+'  '.join(f'{y}: ${sum(v):+.0f} ({len(v)} сд, ср ${st.mean(v):+.2f})' for y,v in sorted(yrs.items())))
    return mon
for days,dn in (((0,1,2,3,4),'все дни'),((1,2,3,4),'без понедельника')):
    report(run('data/gold_M15.csv',1,15,4,0,days=days), f'M15 01:15→04:00 {dn}')
report(run('data/gold_M15.csv',1,15,4,0,stop_atr=2.0), 'M15 01:15→04:00 стоп 2ATR')
report(run('data/gold_M15.csv',1,30,4,0), 'M15 01:30→04:00')
report(run('data/gold_M15.csv',1,30,3,0), 'M15 01:30→03:00')
mon=report(run('data/gold_M5.csv',1,15,4,0), 'M5  01:15→04:00')
print('    2026 по месяцам:', ' '.join(f"{k[5:]}:{sum(v):+.0f}" for k,v in sorted(mon.items()) if k.startswith('2026')))
print('    2025 по месяцам:', ' '.join(f"{k[5:]}:{sum(v):+.0f}" for k,v in sorted(mon.items()) if k.startswith('2025')))
mon=report(run('data/gold_M30.csv',1,30,4,0), 'M30 01:30→04:00 (с 09.2021)')
