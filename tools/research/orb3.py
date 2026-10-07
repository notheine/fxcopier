import sys, statistics as st, math
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
import trend_bt as T
sys.path.insert(0, __import__('os').path.dirname(__file__))
import orb2 as O
# пробой с результатом в R (R = расстояние до стопа) и направлением
def orb_r(b, days, asia=(1,10), last_entry=18, exit_h=23, stopk=0.75):
    res=[]
    for dte,ix in days.items():
        if dte.weekday()>4: continue
        A=[b[i] for i in ix if asia[0]<=T.dtime(b[i][0]).hour<asia[1]]
        R=[i for i in ix if asia[1]<=T.dtime(b[i][0]).hour<exit_h]
        need=(asia[1]-asia[0])*(2 if (b[1][0]-b[0][0])==1800 else 4)*0.75
        if len(A)<need or len(R)<4: continue
        hi=max(x[2] for x in A); lo=min(x[3] for x in A); rng=hi-lo
        pos=None; done=False
        for i in R:
            t,o,h,l,c,sp=b[i]; sp+=O.SPA
            path=(o,l,h,c) if c>=o else (o,h,l,c)
            k0=0
            if pos is None:
                if T.dtime(t).hour>=last_entry: break
                hit=None
                for k in range(4):
                    if path[k]>hi: hit=(k,1); break
                    if path[k]<lo: hit=(k,-1); break
                if hit is None: continue
                k0,d=hit; lvl=hi if d>0 else lo; px=path[k0] if k0==0 else lvl
                ent=(px+sp+O.SLIP) if d>0 else (px-O.SLIP); risk=stopk*rng; stop=ent-d*risk
                pos=[d,ent,stop,risk]
            d,ent,stop,risk=pos
            for k in range(k0,4):
                p=path[k]
                if (d>0 and p<=stop) or (d<0 and p+sp>=stop):
                    fill=(min(stop,p) if k==0 else stop) if d>0 else (max(stop,p+sp) if k==0 else stop)
                    res.append((dte,d,(d*(fill-ent)-O.COMM-O.SLIP)/risk)); pos=None; done=True; break
            if done: break
        if pos:
            d,ent,stop,risk=pos; t,o,h,l,c,sp=b[R[-1]]
            ex=c if d>0 else c+sp+O.SPA
            res.append((dte,d,(d*(ex-ent)-O.COMM)/risk))
    return res
def rep(res,label):
    y={}; m={}
    for d,dr,r in res: y.setdefault(d.year,[]).append(r); m[d.strftime('%Y-%m')]=m.get(d.strftime('%Y-%m'),0)+r
    eq=1000; pk=1000; dd=0
    for d,dr,r in sorted(res): eq*=1+0.01*r; pk=max(pk,eq); dd=max(dd,(pk-eq)/pk)
    rs=[r for _,_,r in res]; L=[r for _,dr,r in res if dr>0]; S=[r for _,dr,r in res if dr<0]
    print(f'{label}: сделок {len(rs)}, ср {st.mean(rs):+.3f}R (t={st.mean(rs)/(st.pstdev(rs)/math.sqrt(len(rs))):.1f}), в плюс {100*sum(r>0 for r in rs)/len(rs):.0f}%, '
          f'$1000→{eq:.0f} при 1%, просадка {dd*100:.0f}%, мес. в плюсе {sum(x>0 for x in m.values())}/{len(m)}')
    print('    по годам R: '+'  '.join(f'{k}: {sum(v):+.1f} ({len(v)})' for k,v in sorted(y.items()))+f' | покупки {sum(L):+.1f}R ({len(L)}), продажи {sum(S):+.1f}R ({len(S)})')
    return m
if __name__ == '__main__':
  for path,lab in (('data/gold_M30.csv','M30 09.2021–10.2026'),('data/gold_M15.csv','M15 08.2022–10.2026'),('data/gold_M5.csv','M5 05.2025–10.2026')):
    b,days=O.load_days(path)
    for asia in ((1,10),(1,11)):
        m=rep(orb_r(b,days,asia=asia), f'{lab} Азия до {asia[1]}')
        if path.endswith('M5.csv') and asia[1]==10:
            print('    2026 по месяцам R:', ' '.join(f'{k[5:]}:{v:+.1f}' for k,v in sorted(m.items()) if k.startswith('2026')))
