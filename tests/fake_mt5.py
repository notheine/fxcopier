from types import SimpleNamespace as NS
import itertools
class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO=0; ORDER_TYPE_BUY=0; ORDER_TYPE_SELL=1; ORDER_TYPE_BUY_LIMIT=2; ORDER_TYPE_SELL_LIMIT=3
    POSITION_TYPE_BUY=0; POSITION_TYPE_SELL=1; TRADE_ACTION_DEAL=1; TRADE_ACTION_PENDING=5; TRADE_ACTION_SLTP=6
    TRADE_ACTION_MODIFY=7; TRADE_ACTION_REMOVE=8; ORDER_FILLING_FOK=0; ORDER_FILLING_IOC=1; ORDER_FILLING_RETURN=2; ORDER_TIME_GTC=0
    def __init__(s, balance=1000.0):
        s.bid=4400.0; s.spread=0.15; s.pos={}; s.ord={}; s.ids=itertools.count(1000); s.balance=balance; s.deals=[]; s.reqs=0; s.fails=[]
    def initialize(s,**k): return True
    def reconnect_lib(s): pass
    def last_error(s): return (1,'ok')
    def account_info(s): return NS(login=1,server='demo',trade_mode=0,balance=s.balance,equity=s.balance+sum(s._pl(p) for p in s.pos.values()),currency='USD',margin_mode=2,margin_free=s.balance)
    def terminal_info(s): return NS(connected=True,trade_allowed=True)
    def symbol_info(s,n): return NS(digits=2,point=0.01,trade_stops_level=0,filling_mode=1,volume_min=0.01,volume_step=0.01,trade_tick_size=0.01,trade_tick_value=1.0,trade_contract_size=100.0) if n=='GOLD' else None
    def symbol_select(s,n,on=True): return True
    def order_calc_margin(s,t,n,v,px): return v*100*px/100.0   # плечо 1:100
    def symbol_info_tick(s,n): return NS(bid=s.bid,ask=round(s.bid+s.spread,2))
    def _pl(s,p,px=None):
        if px is None: px = s.bid if p.type==0 else s.bid+s.spread
        return ((px-p.price_open) if p.type==0 else (p.price_open-px))*p.volume*100
    def _close(s,p,px=None,why=''):
        pl=s._pl(p,px); s.balance+=pl; s.pos.pop(p.ticket); s.deals.append(NS(position_id=p.identifier,profit=pl,commission=0.0,swap=0.0,why=why))
    def positions_get(s,symbol=None): return [NS(**{**vars(p),'profit':s._pl(p)}) for p in s.pos.values()]
    def orders_get(s,symbol=None): return list(s.ord.values())
    def history_deals_get(s,a,b): return list(s.deals)
    def order_send(s,r):
        s.reqs+=1; a=r['action']; t=next(s.ids); ok=lambda **k: NS(retcode=10009,order=k.get('order',0),price=k.get('price',0),comment='')
        if a==1 and 'position' in r:
            p=s.pos[r['position']]
            if r.get('volume') and r['volume'] < p.volume - 1e-9:      # частичное закрытие
                part=NS(**{**vars(p),'volume':r['volume']}); pl=s._pl(part); s.balance+=pl; p.volume=round(p.volume-r['volume'],8)
                s.deals.append(NS(position_id=p.identifier,profit=pl,commission=0.0,swap=0.0,why='market'))
                return ok(order=t,price=r['price'])
            s._close(p,why='market'); return ok(order=t,price=r['price'])
        if a==1:
            # validate stops like a broker
            px=r['price']; buy=r['type']==0
            if (buy and not (r['sl']<px<r['tp'])) or (not buy and not (r['tp']<px<r['sl'])):
                s.fails.append(('invalid stops',r)); return NS(retcode=10016,order=0,price=0,comment='Invalid stops')
            s.pos[t]=NS(ticket=t,identifier=t,symbol=r['symbol'],type=r['type'],volume=r['volume'],price_open=px,sl=r['sl'],tp=r['tp'],magic=r['magic'],comment=r['comment'])
            return ok(order=t,price=px)
        if a==5:
            s.ord[t]=NS(ticket=t,symbol=r['symbol'],type=r['type'],volume_current=r['volume'],price_open=r['price'],sl=r['sl'],tp=r['tp'],magic=r['magic'],comment=r['comment']); return NS(retcode=10008,order=t,price=r['price'],comment='')
        if a==6:
            p=s.pos[r['position']]
            buy=p.type==0; cur=s.bid if buy else s.bid+s.spread
            if r['sl'] and ((buy and r['sl']>=cur) or (not buy and r['sl']<=cur)):
                s.fails.append(('invalid sl modify',r)); return NS(retcode=10016,order=0,price=0,comment='Invalid stops')
            if (p.sl,p.tp)==(r['sl'],r['tp']): return NS(retcode=10025,order=0,price=0,comment='No changes')
            p.sl=r['sl']; p.tp=r['tp']; return ok()
        if a==7:
            o=s.ord[r['order']]; o.sl=r['sl']; o.tp=r['tp']; o.price_open=r['price']; return ok()
        if a==8: s.ord.pop(r['order']); return ok()
    def tick(s,bid):
        s.bid=round(bid,2); ask=s.bid+s.spread
        for o in list(s.ord.values()):
            if (o.type==2 and ask<=o.price_open) or (o.type==3 and s.bid>=o.price_open):
                s.ord.pop(o.ticket); s.pos[o.ticket]=NS(ticket=o.ticket,identifier=o.ticket,symbol=o.symbol,type=o.type-2,volume=o.volume_current,price_open=o.price_open,sl=o.sl,tp=o.tp,magic=o.magic,comment=o.comment)
        for p in list(s.pos.values()):
            if p.type==0:
                if p.tp and s.bid>=p.tp: s._close(p,p.tp,'tp')
                elif p.sl and s.bid<=p.sl: s._close(p,p.sl,'sl')
            else:
                if p.tp and ask<=p.tp: s._close(p,p.tp,'tp')
                elif p.sl and ask>=p.sl: s._close(p,p.sl,'sl')
