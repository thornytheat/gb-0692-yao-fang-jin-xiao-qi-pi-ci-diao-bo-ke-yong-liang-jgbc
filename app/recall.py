import hashlib, json, time
from flask import Blueprint, request, jsonify
from app.domain import allocate
from app.errors import DomainError
from app.numeric import serial
from app.store import connection, get_run, save_recall, get_recall, find_recall, list_recalls, update_recall

class RecallInterrupted(Exception):
    """测试钩子：模拟处置在处置点之间中断。已提交的处置点保持落库。"""
    def __init__(self, payload):
        self.payload = payload
        super().__init__('召回处置中断')

def _parse_receipts(receipts):
    if receipts is None: return []
    if not isinstance(receipts, list): raise DomainError('REQUEST','收货记录必须为数组',400)
    seen=set();out=[]
    for item in receipts:
        if not isinstance(item,dict): raise DomainError('REQUEST','收货记录必须为对象',400)
        record=item.get('需求单');qty=item.get('已收')
        if not isinstance(record,str) or not record.strip(): raise DomainError('REQUEST','收货记录缺少需求单',400)
        if isinstance(qty,bool) or not isinstance(qty,int) or qty<0: raise DomainError('REQUEST','已收必须为非负整数',400)
        record=record.strip()
        if record in seen: raise DomainError('RECEIPT','同一需求单收货记录重复')
        seen.add(record);out.append({'需求单':record,'已收':qty})
    return sorted(out,key=lambda r:r['需求单'])

def _identity(run_id, sku, lot, receipts):
    canonical=json.dumps({'run_id':run_id,'sku':sku,'lot':lot,'receipts':receipts},ensure_ascii=False,sort_keys=True)
    return 'R'+hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:15]

def _totals(points, frozen_remainder):
    pending=sum(p['待执行'] for p in points)
    return {'原计划':sum(p['原计划'] for p in points),'已收':sum(p['已收'] for p in points),'待执行':pending,
            '补偿':sum(c['数量'] for p in points for c in p['补偿']),'补偿缺口':sum(p['补偿缺口'] for p in points),
            '未分配余量退出':frozen_remainder,'源库退出':pending+frozen_remainder}

def _build(run, sku, lot, receipts, rid):
    key=sku+':'+lot
    involved=sorted((a for a in run['allocations'] if a['药品']==sku and a['批号']==lot),key=lambda r:r['需求单'])
    remaining={k:int(v) for k,v in run['remaining'].items()}
    if key not in remaining and not involved: raise DomainError('RECALL','该批号不在方案库存中')
    plan={a['需求单']:a['调拨数量'] for a in involved}
    received={}
    for r in receipts:
        if r['需求单'] not in plan: raise DomainError('RECEIPT','收货记录的需求单不在该批方案中')
        if r['已收']>plan[r['需求单']]: raise DomainError('RECEIPT','已收超过原计划量')
        received[r['需求单']]=r['已收']
    pool=dict(remaining)
    for other in list_recalls(run['manifest']['run_id']):
        prior=json.loads(other['payload'])
        pool.pop(prior['sku']+':'+prior['lot'],None)
        for pt in prior['points']:
            for c in pt['补偿']: pool[c['stock_key']]=pool.get(c['stock_key'],0)-c['数量']
    frozen_remainder=pool.pop(key,0)
    points=[]
    for a in involved:
        got=received.get(a['需求单'],0)
        points.append({'需求单':a['需求单'],'门店':a['门店'],'原计划':a['调拨数量'],'已收':got,'待执行':a['调拨数量']-got,'补偿':[],'补偿缺口':a['调拨数量']-got,'state':'待处置'})
    return {'recall_id':rid,'run_id':run['manifest']['run_id'],'sku':sku,'lot':lot,'status':'进行中','receipts':receipts,
            'original':involved,'points':points,'pool':pool,'frozen_remainder':frozen_remainder,
            'totals':_totals(points,frozen_remainder),'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}

def _dispose_point(pt, pool, usable, demands, sku):
    stocks=[{**usable[k],'available':qty} for k,qty in pool.items() if qty>0 and k in usable and usable[k]['sku']==sku]
    rows,after,_=allocate(stocks,[{**demands[pt['需求单']],'net_need':pt['待执行']}])
    pool.update(after)
    pt['补偿']=[{'批号':r['批号'],'stock_key':sku+':'+r['批号'],'到期日':r['到期日'],'数量':r['调拨数量'],'到店余期_天':r['到店余期_天']} for r in rows]
    pt['补偿缺口']=pt['待执行']-sum(c['数量'] for c in pt['补偿'])
    pt['state']='已处置'

def execute(rid, fail_after=None):
    row=get_recall(rid)
    payload=json.loads(row['payload'])
    run=get_run(payload['run_id'])
    usable={s['stock_key']:s for s in run['usable']}
    demands={d['record']:d for d in run['net_demands']}
    pool={k:int(v) for k,v in payload['pool'].items()}
    done=0
    with connection() as db:
        for pt in payload['points']:
            if pt['state']=='已处置': done+=1;continue
            if fail_after is not None and done>=fail_after: raise RecallInterrupted(payload)
            _dispose_point(pt,pool,usable,demands,payload['sku'])
            done+=1
            payload['pool']=pool
            payload['totals']=_totals(payload['points'],payload['frozen_remainder'])
            update_recall(db,rid,done,'进行中',payload);db.commit()
        payload['status']='已完成'
        payload['totals']=_totals(payload['points'],payload['frozen_remainder'])
        update_recall(db,rid,done,'已完成',payload)
    return payload

def submit(run_id, sku, lot, receipts=None, fail_after=None):
    if not isinstance(run_id,str) or not run_id: raise DomainError('REQUEST','缺少方案编号',400)
    if not isinstance(sku,str) or not sku.strip(): raise DomainError('REQUEST','缺少药品编码',400)
    if not isinstance(lot,str) or not lot.strip(): raise DomainError('REQUEST','缺少批号',400)
    sku=sku.strip();lot=lot.strip()
    run=get_run(run_id)
    if run['decision']!='通过': raise DomainError('STATE','方案未通过复核，不能执行召回处置',409)
    receipts=_parse_receipts(receipts)
    rid=_identity(run_id,sku,lot,receipts)
    existing=find_recall(run_id,sku,lot)
    if existing:
        if existing['id']!=rid: raise DomainError('RECALL','该批召回已登记，收货记录不一致',409)
        if existing['status']=='已完成': return {**json.loads(existing['payload']),'reused':True}
        return {**execute(rid,fail_after=fail_after),'reused':False}
    payload=_build(run,sku,lot,receipts,rid)
    save_recall(rid,run_id,sku,lot,payload)
    return {**execute(rid,fail_after=fail_after),'reused':False}

bp=Blueprint('recall',__name__)
@bp.route('/api/recall',methods=['POST'])
def create():
    body=request.get_json(silent=True) or {}
    return jsonify(serial(submit(body.get('run_id'),body.get('sku'),body.get('lot'),body.get('receipts'))))
@bp.route('/api/recall/<rid>')
def detail(rid):
    return jsonify(serial(json.loads(get_recall(rid)['payload'])))
@bp.route('/api/recalls')
def listing():
    rows=list_recalls(request.args.get('run_id') or None)
    return jsonify([{'recall_id':r['id'],'run_id':r['run_id'],'sku':r['sku'],'lot':r['lot'],'status':r['status'],'done':r['done'],'total':len(json.loads(r['payload'])['points']),'created_at':r['created_at']} for r in rows])
