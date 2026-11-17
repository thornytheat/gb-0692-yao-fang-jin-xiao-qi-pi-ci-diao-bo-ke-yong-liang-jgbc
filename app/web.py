import json
from flask import Blueprint, render_template, request
from app.config import TITLE, MODULES, SHORT, SAMPLE
from app.pipeline import calculate
from app.store import latest, save, get_run, get_recall, list_recalls
bp=Blueprint('web',__name__)
@bp.route('/')
@bp.route('/work/<int:stage>')
def work(stage=1):
    if stage<1 or stage>7:
        from flask import abort
        abort(404)
    run=get_run(request.args['run_id']) if request.args.get('run_id') else latest()
    if run is None: run=save(calculate())
    current=run['stages'][str(stage)]
    recalls=[{'recall_id':r['id'],'sku':r['sku'],'lot':r['lot'],'status':r['status'],'done':r['done']} for r in list_recalls(run['manifest']['run_id'])] if stage==7 else []
    return render_template('m'+str(stage)+'.html', title=TITLE, modules=MODULES, short=SHORT, run=run, current=current, stage=stage, sample=SAMPLE.read_text(encoding='utf-8-sig'), recalls=recalls)
@bp.route('/recall/<rid>')
def recall_page(rid):
    row=get_recall(rid)
    payload=json.loads(row['payload'])
    run=get_run(payload['run_id'])
    t=payload['totals']
    rows=[{'需求单':p['需求单'],'门店':p['门店'],'原计划':p['原计划'],'已收':p['已收'],'待执行':p['待执行'],'补偿':'、'.join(str(c['批号'])+'×'+str(c['数量']) for c in p['补偿']) or '—','补偿缺口':p['补偿缺口'],'状态':p['state']} for p in payload['points']]
    current={'name':'召回处置','rows':rows,'metrics':[['原计划',t['原计划'],'件'],['已收',t['已收'],'件'],['待执行',t['待执行'],'件'],['补偿',t['补偿'],'件'],['补偿缺口',t['补偿缺口'],'件'],['源库退出',t['源库退出'],'件']]}
    return render_template('recall.html', title=TITLE, modules=MODULES, short=SHORT, run=run, current=current, stage=8, sample=SAMPLE.read_text(encoding='utf-8-sig'), recall=payload, done=row['done'], recalls=[])
