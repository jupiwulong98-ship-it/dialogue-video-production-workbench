"""SQLite 日志先于外部付费请求落盘，查询错误不能抹掉已受理任务。"""

import json
import sqlite3
import uuid
from pathlib import Path


class DuplicateJob(RuntimeError):
    pass


class JobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
            state TEXT NOT NULL, request TEXT NOT NULL, submit_id TEXT,
            error TEXT, result TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def reserve(self, fingerprint: str, request: dict) -> dict:
        job_id = uuid.uuid4().hex
        try:
            with self.db:
                self.db.execute('INSERT INTO jobs (id,fingerprint,state,request) VALUES (?,?,?,?)',
                                (job_id, fingerprint, 'reserved', json.dumps(request, ensure_ascii=False)))
        except sqlite3.IntegrityError as exc:
            row = self.db.execute('SELECT id FROM jobs WHERE fingerprint=?', (fingerprint,)).fetchone()
            if row:
                raise DuplicateJob(f'相同输入已有任务 {row["id"]}；请查询该任务，勿重复提交。') from exc
            raise
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise ValueError(f'没有任务 {job_id}')
        result = dict(row)
        result['request'] = json.loads(result['request'])
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def list(self) -> list[dict]:
        return [self.get(row['id']) for row in self.db.execute('SELECT id FROM jobs ORDER BY created_at,id')]

    def set_state(self, job_id: str, state: str, *, result=None):
        with self.db:
            self.db.execute('UPDATE jobs SET state=?,result=COALESCE(?,result),updated_at=CURRENT_TIMESTAMP WHERE id=?',
                            (state, json.dumps(result, ensure_ascii=False) if result is not None else None, job_id))

    def accept(self, job_id: str, submit_id: str):
        if not submit_id.strip():
            raise ValueError('任务编号为空')
        with self.db:
            self.db.execute('UPDATE jobs SET submit_id=?,state="querying",error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                            (submit_id, job_id))

    def note_error(self, job_id: str, error: str):
        with self.db:
            self.db.execute('UPDATE jobs SET error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (error, job_id))

    def recover_interrupted(self):
        with self.db:
            self.db.execute('''UPDATE jobs SET state='submission_unknown',
                error='上次提交被中断，需核对即梦任务历史；不可自动重提',updated_at=CURRENT_TIMESTAMP
                WHERE state='submitting' AND submit_id IS NULL''')
