"""Пробой азиатского диапазона. Аккуратная версия: путь цены внутри свечи, стоп в свече входа, гэпы."""
import sys, itertools
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
SLIP=0.10; COMM=0.07; SPA=0.05
def load_days(path):
    b=T.load(path); days={}
    for i,x in enumerate(b): days.setdefault(T.dtime(x[0]).date(),[]).append(i)
    return b,days
def orb(b, days, asia=(1,10), last_entry=16, exit_h=23, stopk=1.0, fade=False, maxrng_atr=0, both=False):
    res=[]
    for dte,ix in days.items():
        if dte.weekday()>4: continue
        A=[b[i] for i in ix if asia[0]<=T.dtime(b[i][0]).hour<asia[1]]
        R=[i for i in ix if asia[1]<=T.dtime(b[i][0]).hour<exit_h]
        if len(A)<(asia[1]-asia[0])*2 or len(R)<4: continue
        hi=max(x[2] for x in A); lo=min(x[3] for x in A); rng=hi-lo
        if rng<=0: continue
        pos=None; done=False
        for i in R:
            t,o,h,l,c,sp=b[i]; sp+=SPA
            path=(o,l,h,c) if c>=o else (o,h,l,c)
            hr=T.dtime(t).hour
            k0=0
            if pos is None:
                if hr>=last_entry: break
                # первая точка пути, пробившая уровень
                hit=None
                for k in range(len(path)):
                    if path[k]>hi: hit=(k,1); break
                    if path[k]<lo: hit=(k,-1); break
                if hit is None: continue
                k0,d=hit
                lvl=hi if d>0 else lo
                px=path[k0] if k0==0 else lvl          # гэп на открытии свечи — по цене открытия
                if fade: d=-d
                ent=(px+sp+SLIP) if d>0 else (px-SLIP)
                stop=ent-d*stopk*rng
                pos=[d,ent,stop]
            d,ent,stop=pos
            # путь после входа (или с начала свечи): проверка стопа
            for k in range(k0, len(path)):
                p=path[k]
                if (d>0 and p<=stop) or (d<0 and p+sp>=stop):
                    fill=(min(stop,p) if k==0 else stop) if d>0 else (max(stop,p+sp) if k==0 else stop)
                    res.append((dte, d*(fill-ent)-COMM-SLIP)); pos=None; done=True; break
            if done: break
        if pos:
            d,ent,stop=pos; t,o,h,l,c,sp=b[R[-1]]
            ex=c if d>0 else c+sp+SPA
            res.append((dte, d*(ex-ent)-COMM))
    return res
def summary(res):
    y={}; m={}
    for d,p in res: y.setdefault(d.year,[]).append(p); m.setdefault(d.strftime('%Y-%m'),0); m[d.strftime('%Y-%m')]+=p
    return y,m
if __name__=='__main__':
    b,days=load_days('data/gold_M15.csv')
    print('M15 08.2022–10.2026, $ на 0.01 лота')
    for fade in (False,True):
        for sk in (0.5,1.0):
            y,m=summary(orb(b,days,stopk=sk,fade=fade))
            print(('ПРОТИВ ' if fade else 'ПО     ')+f'стоп {sk}×: '+'  '.join(f'{k}: {sum(v):+.0f}' for k,v in sorted(y.items()))+f' | мес. в плюсе {sum(x>0 for x in m.values())}/{len(m)}')
    print('\nУстойчивость (по пробою): конец Азии / последний вход / выход / стоп')
    rows=[]
    for ae,le,ex,sk in itertools.product((8,9,10,11),(14,16,18),(20,23),(0.5,0.75,1.0)):
        if le>=ex: continue
        y,m=summary(orb(b,days,asia=(1,ae),last_entry=le,exit_h=ex,stopk=sk))
        rows.append(((ae,le,ex,sk),{k:sum(v) for k,v in y.items()},sum(x>0 for x in m.values()),len(m)))
    for k,yy,mp,mn in rows:
        print(f'  Азия до {k[0]:02d}, вход до {k[1]}, выход {k[2]}, стоп {k[3]}: '+' '.join(f'{a}:{v:+5.0f}' for a,v in sorted(yy.items()))+f' | мес+ {mp}/{mn}')
    neg=[r for r in rows if min(v for a,v in r[1].items() if a>=2023)<0]
    print('вариантов', len(rows), '; с минусовым годом в 2023–2026:', len(neg))
