import json
import pytest
from collections import Counter
from app.recall import submit, find_recall, RecallInterrupted
from app.store import connection

@pytest.fixture
def approved(client):
    run=client.post('/api/run',json={}).get_json()
    ident=run['manifest']['run_id']
    assert client.post('/api/review',json={'id':ident,'revision':1,'decision':'通过'}).status_code==200
    return client.get('/api/runs/'+ident).get_json()

def target(run, minimum=1):
    counts=Counter((a['药品'],a['批号']) for a in run['allocations'])
    return next(k for k,v in counts.items() if v>=minimum)

def post(client, run, sku, lot, receipts=None):
    body={'run_id':run['manifest']['run_id'],'sku':sku,'lot':lot}
    if receipts is not None: body['receipts']=receipts
    return client.post('/api/recall',json=body)

def test_unapproved_run_rejected(client):
    run=client.post('/api/run',json={}).get_json()
    res=client.post('/api/recall',json={'run_id':run['manifest']['run_id'],'sku':'0002','lot':'B0001'})
    assert res.status_code==409 and res.get_json()['code']=='STATE'

def test_returned_run_rejected(client):
    run=client.post('/api/run',json={}).get_json()
    client.post('/api/review',json={'id':run['manifest']['run_id'],'revision':1,'decision':'退回'})
    assert client.post('/api/recall',json={'run_id':run['manifest']['run_id'],'sku':'0002','lot':'B0001'}).status_code==409

def test_unknown_run_404(client):
    assert client.post('/api/recall',json={'run_id':'absent','sku':'0002','lot':'B0001'}).status_code==404

def test_unknown_lot_422(approved,client):
    res=post(client,approved,'0002','NOPE')
    assert res.status_code==422 and res.get_json()['code']=='RECALL'

def test_missing_fields_400(approved,client):
    assert client.post('/api/recall',json={}).status_code==400
    assert client.post('/api/recall',json={'run_id':approved['manifest']['run_id'],'sku':'0002'}).status_code==400

def test_non_object_json_400(client):
    assert client.post('/api/recall',json=[1]).status_code==400

def test_received_stays_pending_exits(approved,client):
    sku,lot=target(approved)
    a=next(x for x in approved['allocations'] if x['药品']==sku and x['批号']==lot)
    got=min(5,a['调拨数量'])
    d=post(client,approved,sku,lot,[{'需求单':a['需求单'],'已收':got}]).get_json()
    assert d['status']=='已完成'
    pt=next(p for p in d['points'] if p['需求单']==a['需求单'])
    assert pt['原计划']==a['调拨数量'] and pt['已收']==got and pt['待执行']==a['调拨数量']-got
    t=d['totals']
    assert t['原计划']==t['已收']+t['待执行']
    assert t['待执行']==t['补偿']+t['补偿缺口']
    assert t['源库退出']==t['待执行']+t['未分配余量退出']
    assert t['未分配余量退出']==approved['remaining'][sku+':'+lot]
    assert sum(c['数量'] for c in pt['补偿'])<=pt['待执行']

def test_compensation_follows_existing_caliber(approved,client):
    sku,lot=target(approved)
    d=post(client,approved,sku,lot).get_json()
    usable={s['stock_key']:s for s in approved['usable']}
    demands={x['record']:x for x in approved['net_demands']}
    used={}
    for p in d['points']:
        expiries=[c['到期日'] for c in p['补偿']]
        assert expiries==sorted(expiries)
        for c in p['补偿']:
            assert c['stock_key']!=sku+':'+lot
            assert c['数量']%usable[c['stock_key']]['pack_n']==0
            assert c['到店余期_天']>=demands[p['需求单']]['min_days_n']
            used[c['stock_key']]=used.get(c['stock_key'],0)+c['数量']
    for k,v in used.items(): assert v<=approved['remaining'][k]

def test_unallocated_lot_only_exits_source(approved,client):
    allocated={a['药品']+':'+a['批号'] for a in approved['allocations']}
    key=next(k for k,v in approved['remaining'].items() if k not in allocated and v>0)
    sku,lot=key.split(':')
    d=post(client,approved,sku,lot).get_json()
    assert d['points']==[] and d['status']=='已完成'
    assert d['totals']['待执行']==0 and d['totals']['源库退出']==approved['remaining'][key]

def test_original_plan_and_receipts_preserved(approved,client):
    sku,lot=target(approved)
    a=next(x for x in approved['allocations'] if x['药品']==sku and x['批号']==lot)
    receipts=[{'需求单':a['需求单'],'已收':min(3,a['调拨数量'])}]
    d=post(client,approved,sku,lot,receipts).get_json()
    assert d['original']==sorted([x for x in approved['allocations'] if x['药品']==sku and x['批号']==lot],key=lambda r:r['需求单'])
    assert d['receipts']==receipts
    after=client.get('/api/runs/'+approved['manifest']['run_id']).get_json()
    assert after['allocations']==approved['allocations'] and after['decision']=='通过'

def test_resume_continues_without_duplication(approved,client):
    sku,lot=target(approved,minimum=2)
    a=next(x for x in approved['allocations'] if x['药品']==sku and x['批号']==lot)
    receipts=[{'需求单':a['需求单'],'已收':min(5,a['调拨数量'])}]
    with pytest.raises(RecallInterrupted):
        submit(approved['manifest']['run_id'],sku,lot,receipts,fail_after=1)
    row=find_recall(approved['manifest']['run_id'],sku,lot)
    mid=json.loads(row['payload'])
    assert row['status']=='进行中' and row['done']==1
    done_before={p['需求单']:p['补偿'] for p in mid['points'] if p['state']=='已处置'}
    assert len(done_before)==1
    res=post(client,approved,sku,lot,receipts)
    assert res.status_code==200
    d=res.get_json()
    assert d['status']=='已完成' and all(p['state']=='已处置' for p in d['points'])
    for p in d['points']:
        if p['需求单'] in done_before:
            assert p['补偿']==done_before[p['需求单']]
    t=d['totals']
    assert t['源库退出']==t['待执行']+t['未分配余量退出']
    assert t['待执行']==t['补偿']+t['补偿缺口']
    used={}
    for p in d['points']:
        for c in p['补偿']: used[c['stock_key']]=used.get(c['stock_key'],0)+c['数量']
    for k,v in used.items(): assert v<=approved['remaining'][k]
    with connection() as db: assert db.execute('SELECT COUNT(*) c FROM recalls').fetchone()['c']==1

def test_resubmit_reuses_disposal(approved,client):
    sku,lot=target(approved)
    first=post(client,approved,sku,lot).get_json()
    again=post(client,approved,sku,lot).get_json()
    assert first['recall_id']==again['recall_id'] and again['reused'] is True
    assert first['totals']==again['totals']
    with connection() as db: assert db.execute('SELECT COUNT(*) c FROM recalls').fetchone()['c']==1

def test_conflicting_receipts_409(approved,client):
    sku,lot=target(approved)
    a=next(x for x in approved['allocations'] if x['药品']==sku and x['批号']==lot)
    assert post(client,approved,sku,lot).status_code==200
    res=post(client,approved,sku,lot,[{'需求单':a['需求单'],'已收':1}])
    assert res.status_code==409 and res.get_json()['code']=='RECALL'

def test_receipt_validation(approved,client):
    sku,lot=target(approved)
    a=next(x for x in approved['allocations'] if x['药品']==sku and x['批号']==lot)
    assert post(client,approved,sku,lot,[{'需求单':a['需求单'],'已收':a['调拨数量']+1}]).status_code==422
    assert post(client,approved,sku,lot,[{'需求单':'D9999','已收':1}]).status_code==422
    assert post(client,approved,sku,lot,[{'需求单':a['需求单'],'已收':1},{'需求单':a['需求单'],'已收':2}]).status_code==422
    assert post(client,approved,sku,lot,[{'需求单':a['需求单'],'已收':-1}]).status_code==400
    assert post(client,approved,sku,lot,[{'需求单':a['需求单'],'已收':'5'}]).status_code==400

def test_sequential_recalls_never_reuse_frozen_stock(approved,client):
    by_sku={}
    for a in approved['allocations']: by_sku.setdefault(a['药品'],[]).append(a['批号'])
    sku=next(s for s,lots in by_sku.items() if len(set(lots))>=2)
    lot1,lot2=list(dict.fromkeys(by_sku[sku]))[:2]
    d1=post(client,approved,sku,lot1).get_json()
    d2=post(client,approved,sku,lot2).get_json()
    assert d1['recall_id']!=d2['recall_id']
    for p in d2['points']:
        for c in p['补偿']: assert c['stock_key']!=sku+':'+lot1
    used={}
    for d in (d1,d2):
        for p in d['points']:
            for c in p['补偿']: used[c['stock_key']]=used.get(c['stock_key'],0)+c['数量']
    for k,v in used.items(): assert v<=approved['remaining'][k]

def test_review_page_shows_key_quantities(approved,client):
    sku,lot=target(approved)
    a=next(x for x in approved['allocations'] if x['药品']==sku and x['批号']==lot)
    d=post(client,approved,sku,lot,[{'需求单':a['需求单'],'已收':min(4,a['调拨数量'])}]).get_json()
    page=client.get('/recall/'+d['recall_id'])
    assert page.status_code==200
    html=page.data.decode()
    for word in ['原计划','已收','待执行','补偿','召回批','收货记录','原方案','补偿明细',str(d['totals']['待执行'])]:
        assert word in html
    assert client.get('/recall/absent').status_code==404

def test_m7_page_lists_recalls(approved,client):
    sku,lot=target(approved)
    d=post(client,approved,sku,lot).get_json()
    html=client.get('/work/7?run_id='+approved['manifest']['run_id']).data.decode()
    assert '召回处置' in html and '/recall/'+d['recall_id'] in html

def test_recall_api_detail_and_listing(approved,client):
    sku,lot=target(approved)
    d=post(client,approved,sku,lot).get_json()
    got=client.get('/api/recall/'+d['recall_id'])
    assert got.status_code==200 and got.get_json()['recall_id']==d['recall_id']
    assert client.get('/api/recall/absent').status_code==404
    listing=client.get('/api/recalls?run_id='+approved['manifest']['run_id']).get_json()
    assert len(listing)==1 and listing[0]['recall_id']==d['recall_id'] and listing[0]['status']=='已完成'
