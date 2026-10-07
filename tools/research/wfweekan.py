import json, statistics as st
res=json.load(open('wf_week.json'))
weeks=sorted({w for _,d in res for w in d})
C=len(res)
R=[[d.get(w,[0,0])[0] for w in weeks] for _,d in res]
N=[[d.get(w,[0,0])[1] for w in weeks] for _,d in res]
print('недель', len(weeks), weeks[0], '…', weeks[-1], 'вариантов', C)
START=26   # первые полгода — только история для выбора
def yr(i): return weeks[i][:4]
base=[st.mean(R[c][i] for c in range(C)) for i in range(START,len(weeks))]
by={}
for i,v in zip(range(START,len(weeks)),base): by.setdefault(yr(i),0); by[yr(i)]+=v
print('случайный вариант (в среднем): итог %+.0fR | ' % sum(base) + ' '.join(f'{k}:{v:+.0f}' for k,v in sorted(by.items())))
for L in (1,2,4,8,13,26):
    for top in (1,10,50):
        out=[]
        for i in range(START,len(weeks)):
            sc=sorted(((sum(R[c][i-L:i]),c) for c in range(C) if sum(N[c][i-L:i])>=2*L), reverse=True)[:top]
            out.append(st.mean(R[c][i] for _,c in sc) if sc else 0)
        by={}
        for i,v in zip(range(START,len(weeks)),out): by.setdefault(yr(i),0); by[yr(i)]+=v
        wk=sum(x>0 for x in out)
        print(f'смотрим {L:2d} нед., берём лучшие {top:2d}: итог {sum(out):+6.0f}R, недель в плюсе {wk}/{len(out)} | '+' '.join(f'{k}:{v:+.0f}' for k,v in sorted(by.items())))
