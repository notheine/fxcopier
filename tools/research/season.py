# Сезонность по часам суток и дням недели: средний ход цены за час (по закрытиям M15 на границах часа)
import sys, statistics as st, math
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
b=T.load('data/gold_M15.csv')
# цены на начало каждого часа
hp={}
for t,o,h,l,c,sp in b:
    if t%3600==0: hp[t]=o
ks=sorted(hp)
rows=[]   # (год, час, день недели, ход за час в $ и в % )
for k in ks:
    if k+3600 in hp:
        d=T.dtime(k); r=hp[k+3600]-hp[k]
        rows.append((d.year + (0.5 if d.month>=7 else 0), d.hour, d.weekday(), r, r/hp[k]*1e4))
def period(y):
    # dev: 2022.5..2025.0 (до 06.2025), test: 2025.5, 2026.0
    return 'dev' if y<2025.5 else 'test'
print('часы: средний ход за час, б.п. (0.01%) — разработка 08.2022–06.2025 по полугодиям | проверка 07.2025–10.2026')
halves=sorted({r[0] for r in rows})
for hr in range(24):
    line=f'{hr:02d}:00 '
    devs=[]
    for hv in halves:
        v=[r[4] for r in rows if r[1]==hr and r[0]==hv]
        if len(v)>20:
            m=st.mean(v); line+=f'{m:+5.1f} '; 
            if hv<2025.5: devs.append(m)
        else: line+='   .  '
    dv=[r[4] for r in rows if r[1]==hr and period(r[0])=='dev']; tv=[r[4] for r in rows if r[1]==hr and period(r[0])=='test']
    if len(dv)>30:
        md,sd=st.mean(dv),st.pstdev(dv)/math.sqrt(len(dv)); mt,stt=(st.mean(tv),st.pstdev(tv)/math.sqrt(len(tv))) if len(tv)>30 else (0,1)
        same=sum(1 for x in devs if x*md>0)
        line+=f'| разр {md:+5.1f} (t={md/sd:+.1f}, знак совпал {same}/{len(devs)}) | пров {mt:+5.1f} (t={mt/stt:+.1f})'
    print(line)
print('\nдни недели (ход за сутки 01:00→23:00, б.п.):')
