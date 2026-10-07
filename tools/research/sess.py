# Сессии: ход за окно часов, отдельно в месяцы, когда золото за месяц росло и когда падало
import sys, statistics as st
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
b=T.load('data/gold_M30.csv'); px={t:o for t,o,h,l,c,s in b}; sp={t:s for t,o,h,l,c,s in b}
mon={}
for t,o,h,l,c,s in b:
    k=T.dtime(t).strftime('%Y-%m'); mon.setdefault(k,[o,c]); mon[k][1]=c
mtrend={k:(v[1]-v[0]) for k,v in mon.items()}
def window(h0,m0,h1,m1):
    out=[]
    for t in px:
        d=T.dtime(t)
        if d.hour==h0 and d.minute==m0 and d.weekday()<5:
            t1=t+((h1*60+m1)-(h0*60+m0))*60
            if t1 in px: out.append((d, px[t1]-px[t], px[t]))
    return out
W=[((1,30),(4,0),'Азия 01:30–04:00'),((1,30),(9,0),'Азия 01:30–09:00'),((4,0),(9,0),'утро 04:00–09:00'),((10,0),(15,0),'Лондон 10–15'),((15,0),(19,0),'США 15–19'),((19,0),(23,0),'вечер 19–23'),((15,0),(23,0),'США 15–23'),((10,0),(23,0),'день 10–23')]
for (a,bm),(c,dm),nm in W:
    v=window(a,bm,c,dm)
    up=[x[1]/x[2]*1e4 for x in v if mtrend[x[0].strftime('%Y-%m')]>0]
    dn=[x[1]/x[2]*1e4 for x in v if mtrend[x[0].strftime('%Y-%m')]<=0]
    yrs={}
    for x in v: yrs.setdefault(x[0].year,[]).append(x[1]/x[2]*1e4)
    s=f'{nm:18s} мес.роста {st.mean(up):+5.1f}бп  мес.падения {st.mean(dn):+5.1f}бп | по годам: '+' '.join(f'{y}:{st.mean(q):+.1f}' for y,q in sorted(yrs.items()))
    print(s)
print('месяцев роста', sum(1 for x in mtrend.values() if x>0), 'падения', sum(1 for x in mtrend.values() if x<=0))
