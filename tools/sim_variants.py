import json,csv,sys,datetime as dt
from parser import parse_signal
from trader import entry_price, decide_entry
H=json.load(open('hist.json'))
B=[]  # (t_server, o,h,l,c,sp)
for r in csv.reader(open('gold_m1_all.csv')):
    B.append((int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[5])*0.01))
import bisect
TS=[b[0] for b in B]
COMM=0.08; BEOFF=0.3; EXP=240*60
LV={'BUY':0.2,'SELL':0.8}
def run_positions(sig, pos, start_i, t_start, kind):
    """pos: list of dicts {k,entry,filled_t}. simulate from bar index start_i; returns pnl (excluding comm)."""
    return None

def simulate(sig, t0, variant):
    buy=sig.side=='BUY'; s=1 if buy else -1
    i0=bisect.bisect_right(TS,t0+3*3600)-1
    if i0<0: return None
    # bar containing signal (server time)
    b=B[i0]; bid=b[1]; sp=b[5]; ask=bid+sp
    lo,hi,e=entry_price(sig,2.0,LV[sig.side])
    tps=dict(sig.tps); ks=sorted(tps)[:3]
    if len(ks)<2: return None
    act,price,why=decide_entry(sig,bid,ask,2.0,1.0,level=LV[sig.side],below_market=True)
    cur=ask if buy else bid
    worst= hi if buy else lo
    tp1=tps[ks[0]]
    out={'act':act}
    if act=='skip': return {'act':'skip','pnl':0.0,'n':0}
    mode='limit' if act=='limit' else 'market'
    posk=ks
    entry_t=None
    # variant overrides
    keep_after_tp1 = False
    if act=='limit':
        beyond = s*(cur-worst)   # >0 = worse than range edge
        if variant=='hyb' and 0<beyond<=1.0:
            mode='market'
        elif variant=='hyb2' and beyond>0:   # любая цена за краем (в сторону тейков) -> TP2/TP3 по рынку
            mode='market2'
        elif variant=='keep': keep_after_tp1=True
        elif variant=='mkt3': mode='market'
        elif variant=='mkt2': mode='market2'
        elif variant=='nolimit_skip': return {'act':'skip','pnl':0.0,'n':0}
    # build positions
    positions=[]  # each: dict k, entry, open(bool)
    i=i0
    if mode in('market','market2'):
        ent=cur
        use=ks if mode=='market' else ks[1:]
        for k in use: positions.append({'k':k,'e':ent,'sl':sig.sl,'open':True,'pnl':None})
        start=i0
        pending=None
    else:
        pending={'p':e,'t':t0,'ks':ks}
        start=i0
    # walk bars
    be_done=False
    reached_tp1_t=None
    npos=len(positions)
    for j in range(start, min(len(B), start+ 60*24*3)):
        t,o,h,l,c,spr=B[j]
        bh,bl=h,l
        askh=h+spr; askl=l+spr
        # pending fill (limit at e): BUY fills when ask<=e ; SELL when bid>=e
        if pending:
            tnow=t - 3*3600
            # price touched TP1 without fill
            touched = (bh>=tp1) if buy else (bl<=tp1)
            fill = (askl<=pending['p']) if buy else (bh>=pending['p'])
            stopped = (bl<=sig.sl) if buy else (bh>=sig.sl)   # условный
            if j>start or True:
                if fill and not (touched and not keep_after_tp1 and (j>start or True) and not fill_first(o,h,l,buy,pending['p'],tp1)):
                    for k in pending['ks']:
                        if keep_after_tp1 and reached_tp1_t is not None and k==ks[0]: continue
                        positions.append({'k':k,'e':pending['p'],'sl':sig.sl,'open':True,'pnl':None})
                    npos=len(positions); pending=None
                    entry_idx=j
                    # на этой же свече дальше проверим тейки/стоп только со следующей свечи
                    continue
                if touched and reached_tp1_t is None: reached_tp1_t=t
                if touched and not keep_after_tp1:
                    return {'act':'cancelled_tp1','pnl':0.0,'n':0}
                if stopped and pending: return {'act':'cancel_sl','pnl':0.0,'n':0}
                if t-(t0+3*3600)>EXP: return {'act':'expired','pnl':0.0,'n':0}
                continue
        # manage open positions
        # SL first (pessimistic)
        for p in positions:
            if not p['open']: continue
            slhit = (bl<=p['sl']) if buy else (bh>=p['sl'])
            if slhit:
                p['open']=False; p['pnl']=s*(p['sl']-p['e'])
        for p in positions:
            if not p['open']: continue
            tp=tps[p['k']]
            if (bh>=tp) if buy else (bl<=tp):
                p['open']=False; p['pnl']=s*(tp-p['e'])
        # BE: when first not-reached TP is hit by any position of lower k (we use: position TP_k closed -> others BE) 
        closed_tp=[p for p in positions if (not p['open']) and p['pnl'] is not None and p['pnl']>0.0001 and abs(s*(tps[p['k']]-p['e'])-p['pnl'])<1e-6]
        if closed_tp:
            kmin=min(p['k'] for p in closed_tp)
            for p in positions:
                if p['open'] and p['k']>kmin:
                    be=p['e']+s*BEOFF
                    p['sl']=be
        if all(not p['open'] for p in positions) and positions and not pending: break
    if pending: return {'act':'never','pnl':0.0,'n':0}
    # close remaining at last bar close
    for p in positions:
        if p['open']:
            p['pnl']=s*(B[min(len(B)-1,start+60*24*3-1)][4]-p['e']); p['open']=False
    pnl=sum(p['pnl'] for p in positions)-COMM*len(positions)
    return {'act':act if mode=='limit' else 'market','pnl':pnl,'n':len(positions)}

def fill_first(o,h,l,buy,p,tp1):
    # в одной свече и вход, и TP1: считаем, что вход был (оптимистично для лимитки) только если цена открытия ближе к входу
    return abs(o-p)<abs(o-tp1)

sigs=[]
for m in H:
    t=m['text']
    if 'GOLD' not in t.upper() and 'XAU' not in t.upper(): continue
    s=parse_signal(t)
    if s and s.valid and not s.is_limit: sigs.append((m['id'],m['ts'],s))
print('signals',len(sigs))
variants=sys.argv[1:] or ['cur','keep','hyb','mkt3','mkt2']
res={}
for v in variants:
    tot=0; rows=[]
    for sid,ts,s in sigs:
        r=simulate(s,ts,v)
        if r is None: continue
        rows.append((sid,ts,s.side,r)); tot+=r['pnl']
    res[v]=rows
    print(v,'total',round(tot,2),'n',len(rows))
# compare on the subset where cur decided limit
cur={r[0]:r for r in res['cur']}
import collections
subset=[sid for sid,ts,sd,r in res['cur'] if r['act'] in('limit','cancelled_tp1','cancel_sl','expired','never')]
print('limit subset',len(subset))
for v in variants:
    d={r[0]:r for r in res[v]}
    print(v,'subset pnl',round(sum(d[s][3]['pnl'] for s in subset if s in d),2))
print(collections.Counter(r[3]['act'] for r in res['cur']))
json.dump({v:[(a,b,c,d) for a,b,c,d in rows] for v,rows in res.items()},open('res.json','w'))
