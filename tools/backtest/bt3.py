"""Прогон истории канала через настоящий main.py + FakeMT5 на минутных свечах FxPro.
Запуск — через run.sh (папка с копией кода, config.yaml, ch.json, gold_hybrid.csv):
  CHASE_SELL=1 CHASE_BUY= START=2026-5-27 END=2026-10-8 python bt3.py
Переменные: ELB/ELS — точка входа покупок/продаж (0..1), CHASE_BUY/CHASE_SELL — chase_usd_*, COMM — $ за лот за круг."""
import asyncio, csv, json, os, datetime as dt, collections, traceback
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo
E=os.environ
START=dt.datetime(*map(int,E.get('START','2026-5-27').split('-')),tzinfo=dt.timezone.utc).timestamp()
END=dt.datetime(*map(int,E.get('END','2026-10-8').split('-')),tzinfo=dt.timezone.utc).timestamp()
import logging
import main, trader as TR
logging.getLogger().setLevel(logging.WARNING)
from fake_mt5 import FakeMT5
MSK=ZoneInfo("Europe/Moscow")
CLOCK=[0.0]   # «МСК как UTC» (время сервера FxPro)
main.time=NS(time=lambda: CLOCK[0]-3*3600)
main.now_msk=lambda: dt.datetime.fromtimestamp(CLOCK[0], dt.timezone.utc).replace(tzinfo=MSK)
OUT=[]
async def notify(t, buttons=None, alt=""): OUT.append((CLOCK[0],t))
main.notify=notify
cfg=main.CFG
cfg['guard']={'enabled':False}
cfg['risk']['ask_risky_buy']=False
for side in ('buy','sell'):
    v=E.get(f'CHASE_{side.upper()}','')
    if v!='': cfg['risk'][f'chase_usd_{side}']=float(v)
f=FakeMT5(float(E.get('BAL','1000')))
COMM=float(E.get('COMM','8.0'))
_close=f._close
def close(p,px=None,why=''):
    _close(p,px,why); f.balance-=COMM*p.volume; f.deals[-1].commission=-COMM*p.volume
f._close=close
main.link=f; main.trader=TR.Trader(cfg,f); main.mt5_ok=True
for fn in ("state_bt.json","journal.jsonl"):
    if os.path.exists(fn): os.remove(fn)
main.state=main.State("state_bt.json")
main.state.d['entry_level_buy']=float(E.get('ELB','0.2')); main.state.d['entry_level_sell']=float(E.get('ELS','0.8'))
main.channel_entity=NS(title="Win Win")
bars=[tuple(float(x) for x in r) for r in csv.reader(open('gold_hybrid.csv'))]   # М5 до 25.06, дальше М1
def ts(d): return dt.datetime.strptime(d[:19],"%d.%m.%Y %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
msgs=[m for m in json.load(open('ch.json')) if START<=ts(m['date'])<END]
ERR=[]; HOLDS=[]; EQ=[]
async def checks():
    try:
        await main.daily_checks()
        for r in list(main.state.active()): await main.check_signal(r)
    except Exception: ERR.append(traceback.format_exc())
async def tick(px, spr):
    f.spread=spr; f.tick(px); await checks()
async def resolve():
    h=main.state.d.get("hold")
    if h:
        HOLDS.append((CLOCK[0],h['type'],h.get('reasons')))
        await main.resolve_hold({"trade":"ok","account":"resume","sl":"slok"}[h['type']])
async def handle(m):
    text=m['text'] or ''
    msg=NS(id=m['id'],message=text,date=dt.datetime.fromtimestamp(CLOCK[0]-3*3600,dt.timezone.utc),
           reply_to=NS(reply_to_msg_id=m['reply']) if m['reply'] else None)
    sig=main.parse_signal(text, **cfg.get('sanity',{}))
    if sig:
        if sig.symbol=='GOLD':
            await main.handle_signal(msg,text,sig); await resolve()
        return
    cmds=main.parse_command(text)
    if cmds:
        await main.handle_command(msg,text,cmds); await resolve()
async def run():
    mi=0; last_day=None
    for b in bars:
        t,o,h,l,c,sp,step=b
        if t<START-86400: continue
        if t>END+2*86400 and not f.pos: break
        spr=max(sp,1)*0.01
        CLOCK[0]=t; f.spread=spr; f.tick(o)
        while mi<len(msgs) and ts(msgs[mi]['date'])<t+step:
            CLOCK[0]=max(t,ts(msgs[mi]['date']))
            try: await handle(msgs[mi])
            except Exception: ERR.append(f"msg {msgs[mi]['id']}: "+traceback.format_exc())
            mi+=1
        CLOCK[0]=t+step*0.25; await checks()
        for px in ((l,h) if c>=o else (h,l)):
            CLOCK[0]+=step*0.25; await tick(px,spr)
        CLOCK[0]=t+step-1; await tick(c,spr)
        await resolve()
        d=dt.datetime.fromtimestamp(t,dt.timezone.utc).date()
        if d!=last_day:
            acc=f.account_info(); EQ.append((str(d),round(acc.balance,2),round(acc.equity,2))); last_day=d
asyncio.run(run())
acc=f.account_info()
J=[json.loads(l) for l in open('journal.jsonl')] if os.path.exists('journal.jsonl') else []
S=main.state.signals
peak=0; mdd=0
for d,bal,eq in EQ:
    peak=max(peak,bal); mdd=max(mdd,(peak-eq)/peak*100 if peak else 0)
w=[j['pnl'] for j in J if j['pnl']>0.5]; lo=[j['pnl'] for j in J if j['pnl']<-0.5]
res=dict(signals=len(S),status=dict(collections.Counter(r['status'] for r in S.values())),trades=len(J),wins=len(w),losses=len(lo),
         pnl=round(sum(j['pnl'] for j in J),2),balance=round(acc.balance,2),equity=round(acc.equity,2),open=len(f.pos),
         max_dd_pct=round(mdd,1),errors=len(ERR),holds=len(HOLDS))
print(json.dumps(res,ensure_ascii=False))
for e in ERR[:3]: print(e[-1500:])
json.dump({'res':res,'eq':EQ,'journal':J,'signals':S,'notes':OUT},open(f"bt_{E.get('TAG','x')}.json",'w'),ensure_ascii=False)
