import sqlite3, json, os
from pathlib import Path
from app.errors import DomainError
def connection():
    path=Path(os.environ.get('APP_DB','data/runs.sqlite3'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS recalls(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sku TEXT NOT NULL, lot TEXT NOT NULL, status TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL, created_at TEXT NOT NULL)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS recalls_lot ON recalls(run_id,sku,lot)')
    return db
def save(result):
    with connection() as db:
        ident=result['manifest']['run_id'];row=db.execute('SELECT payload,revision,decision FROM runs WHERE id=?',(ident,)).fetchone()
        if row: return {**json.loads(row['payload']), 'revision':row['revision'], 'decision':row['decision'], 'reused':True}
        db.execute('INSERT INTO runs(id,payload) VALUES (?,?)',(ident,json.dumps(result,ensure_ascii=False)))
    return {**result,'revision':1,'decision':'待复核','reused':False}
def get_run(ident):
    with connection() as db: row=db.execute('SELECT payload,revision,decision FROM runs WHERE id=?',(ident,)).fetchone()
    if row is None: raise DomainError('NOT_FOUND','批次不存在',404)
    return {**json.loads(row['payload']),'revision':row['revision'],'decision':row['decision']}
def latest():
    with connection() as db: row=db.execute('SELECT payload,revision,decision FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
    return {**json.loads(row['payload']),'revision':row['revision'],'decision':row['decision']} if row else None
def history():
    with connection() as db: return [dict(r) for r in db.execute('SELECT id,revision,decision FROM runs ORDER BY rowid DESC')]
def review(ident, revision, decision):
    if decision not in ('通过','退回'): raise DomainError('DECISION','复核动作无效')
    with connection() as db:
        changed=db.execute('UPDATE runs SET revision=revision+1,decision=? WHERE id=? AND revision=?',(decision,ident,revision)).rowcount
        if not changed: raise DomainError('VERSION','版本已变化，请重新载入',409)
    return {'revision':revision+1,'decision':decision}
def save_recall(ident, run_id, sku, lot, payload):
    with connection() as db:
        db.execute('INSERT INTO recalls(id,run_id,sku,lot,status,done,payload,created_at) VALUES (?,?,?,?,?,0,?,?)',(ident,run_id,sku,lot,'进行中',json.dumps(payload,ensure_ascii=False),payload['created_at']))
def get_recall(ident):
    with connection() as db: row=db.execute('SELECT * FROM recalls WHERE id=?',(ident,)).fetchone()
    if row is None: raise DomainError('NOT_FOUND','召回处置不存在',404)
    return row
def find_recall(run_id, sku, lot):
    with connection() as db: return db.execute('SELECT * FROM recalls WHERE run_id=? AND sku=? AND lot=?',(run_id,sku,lot)).fetchone()
def list_recalls(run_id=None):
    sql='SELECT * FROM recalls' + (' WHERE run_id=?' if run_id else '') + ' ORDER BY rowid'
    with connection() as db: return db.execute(sql,(run_id,) if run_id else ()).fetchall()
def update_recall(db, ident, done, status, payload):
    db.execute('UPDATE recalls SET done=?,status=?,payload=? WHERE id=?',(done,status,json.dumps(payload,ensure_ascii=False),ident))
