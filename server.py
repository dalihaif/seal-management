"""
大理大学第一附属医院印章管理系统 — Flask + SQLite 后端
启动: python server.py
访问: http://localhost:5100
"""
import json, os, csv, io, sqlite3, hashlib, secrets
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g, session

app = Flask(__name__, static_folder='.')
app.secret_key = secrets.token_hex(32)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seal_archive.db')

# ==================== 密码工具 ====================
def hash_pwd(password):
    if not password: return ''
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

ROLE_NAMES = {'admin': '系统管理员', 'editor': '印章管理员', 'clerk': '经办人'}

# ==================== 鉴权装饰器 ====================
PUBLIC_ROUTES = {'/api/login', '/api/session', '/api/init-status'}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('username'):
            return jsonify({'error': '未登录'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('username'):
            return jsonify({'error': '未登录'}), 401
        if session.get('role') != 'admin':
            return jsonify({'error': '权限不足'}), 403
        return f(*args, **kwargs)
    return decorated

# ==================== 数据库工具 ====================
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS seals (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT DEFAULT '公章',
        dept TEXT DEFAULT '', keeper TEXT DEFAULT '', material TEXT DEFAULT '',
        shape TEXT DEFAULT '', carvingDate TEXT DEFAULT '', startDate TEXT DEFAULT '',
        endDate TEXT DEFAULT '', validUntil TEXT DEFAULT '', location TEXT DEFAULT '',
        status TEXT DEFAULT '在用', remark TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS carvings (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT DEFAULT '公章',
        dept TEXT DEFAULT '', material TEXT DEFAULT '橡胶', shape TEXT DEFAULT '圆形',
        applicant TEXT DEFAULT '', applyDate TEXT DEFAULT '', expectedDate TEXT DEFAULT '',
        vendor TEXT DEFAULT '', status TEXT DEFAULT '待审批',
        completeDate TEXT DEFAULT '', remark TEXT DEFAULT '',
        approvals TEXT DEFAULT '[]', reason TEXT DEFAULT '', quantity INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS usages (
        id TEXT PRIMARY KEY, sealId TEXT DEFAULT '', sealName TEXT DEFAULT '',
        user_name TEXT DEFAULT '', dept TEXT DEFAULT '', docType TEXT DEFAULT '',
        purpose TEXT DEFAULT '', approver TEXT DEFAULT '',
        checkOut TEXT DEFAULT '', checkIn TEXT DEFAULT '',
        status TEXT DEFAULT '使用中', copies TEXT DEFAULT '',
        usageType TEXT DEFAULT 'use', batchId TEXT DEFAULT '',
        contactPhone TEXT DEFAULT '', expectedReturn TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS loans (
        id TEXT PRIMARY KEY, sealId TEXT DEFAULT '', sealName TEXT DEFAULT '',
        borrower TEXT DEFAULT '', dept TEXT DEFAULT '', phone TEXT DEFAULT '',
        loanDate TEXT DEFAULT '', expectedDate TEXT DEFAULT '',
        destination TEXT DEFAULT '', approver TEXT DEFAULT '',
        purpose TEXT DEFAULT '', status TEXT DEFAULT '待审批',
        returnDate TEXT DEFAULT '', approvals TEXT DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS recoveries (
        id TEXT PRIMARY KEY, sealId TEXT DEFAULT '', sealName TEXT DEFAULT '',
        checker TEXT DEFAULT '', date TEXT DEFAULT '',
        condition TEXT DEFAULT '', result TEXT DEFAULT '', note TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS destroys (
        id TEXT PRIMARY KEY, sealId TEXT DEFAULT '', sealName TEXT DEFAULT '',
        applicant TEXT DEFAULT '', applyDate TEXT DEFAULT '', reason TEXT DEFAULT '',
        method TEXT DEFAULT '', approver TEXT DEFAULT '', destroyDate TEXT DEFAULT '',
        witnesses TEXT DEFAULT '', certNo TEXT DEFAULT '', status TEXT DEFAULT '待审批'
    );
    CREATE TABLE IF NOT EXISTS archives (
        id TEXT PRIMARY KEY, sealId TEXT DEFAULT '', sealName TEXT DEFAULT '',
        endDate TEXT DEFAULT '', archiveDate TEXT DEFAULT '',
        handler TEXT DEFAULT '', reason TEXT DEFAULT '', note TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT DEFAULT '', action TEXT DEFAULT '',
        detail TEXT DEFAULT '', user_name TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        must_change_pwd INTEGER DEFAULT 0
    );
    -- Default settings
    INSERT OR IGNORE INTO settings (key, value) VALUES ('departments', '["院办公室","党委办公室","财务科","人事科","医务科","护理部","科教科","教学科","总务科","基建科","保卫科","审计科","纪检监察室","工会","团委"]');
    INSERT OR IGNORE INTO settings (key, value) VALUES ('approvers', '[{"name":"院长","level":2},{"name":"分管副院长","level":2},{"name":"办公室主任","level":1},{"name":"综合档案室","level":1}]');
    INSERT OR IGNORE INTO settings (key, value) VALUES ('default_seals', '[]');
    INSERT OR IGNORE INTO settings (key, value) VALUES ('doc_types', '["证明","合同","报告","申请","函件","其他"]');
    -- Default users: admin/admin123, editor/edit123, clerk/(no password)
    INSERT OR IGNORE INTO users (username, password, role, display_name, must_change_pwd) VALUES ('admin', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', 'admin', '系统管理员', 1);
    INSERT OR IGNORE INTO users (username, password, role, display_name, must_change_pwd) VALUES ('editor', '84f3ee8f646c896e01ed7933bed50414ae8c8000e44880fa0e0d530e71f3b46e', 'editor', '印章管理员', 1);
    INSERT OR IGNORE INTO users (username, password, role, display_name, must_change_pwd) VALUES ('clerk', '', 'clerk', '经办人', 0);
    ''')
    # 兼容旧数据库：增量添加新列
    migrations = [
        "ALTER TABLE usages ADD COLUMN usageType TEXT DEFAULT 'use'",
        "ALTER TABLE usages ADD COLUMN batchId TEXT DEFAULT ''",
        "ALTER TABLE usages ADD COLUMN contactPhone TEXT DEFAULT ''",
        "ALTER TABLE usages ADD COLUMN expectedReturn TEXT DEFAULT ''",
    ]
    for m in migrations:
        try: db.execute(m)
        except sqlite3.OperationalError: pass  # 列已存在则跳过
    db.commit()
    db.close()

# ==================== 认证 API ====================
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    if not user:
        return jsonify({'error': '用户不存在'}), 400
    pwd_hash = hash_pwd(password)
    # clerk 无密码，其他需验证密码
    if user['role'] != 'clerk' and user['password'] != pwd_hash:
        return jsonify({'error': '密码错误'}), 400
    session['username'] = user['username']
    session['role'] = user['role']
    session['display_name'] = user['display_name'] or user['username']
    result = {
        'username': user['username'],
        'role': user['role'],
        'display_name': user['display_name'] or user['username'],
        'role_name': ROLE_NAMES.get(user['role'], user['role']),
        'must_change_pwd': bool(user['must_change_pwd'])
    }
    # 登录后清除 must_change_pwd 标记
    if user['must_change_pwd']:
        db.execute('UPDATE users SET must_change_pwd = 0 WHERE username = ?', (username,))
        db.commit()
    # 写入日志
    db.execute("INSERT INTO logs (time, action, detail, user_name) VALUES (?,?,?,?)",
               (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), '用户登录', f'{user["username"]}({ROLE_NAMES.get(user["role"],"")}) 登录系统', user['username']))
    db.commit()
    return jsonify(result)

@app.route('/api/logout', methods=['POST'])
def api_logout():
    username = session.pop('username', None)
    if username:
        db = get_db()
        db.execute("INSERT INTO logs (time, action, detail, user_name) VALUES (?,?,?,?)",
                   (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), '用户登出', f'{username} 登出系统', username))
        db.commit()
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/session', methods=['GET'])
def api_session():
    if not session.get('username'):
        return jsonify({'logged_in': False})
    return jsonify({
        'logged_in': True,
        'username': session['username'],
        'role': session['role'],
        'display_name': session.get('display_name', session['username']),
        'role_name': ROLE_NAMES.get(session['role'], '')
    })

@app.route('/api/change-password', methods=['POST'])
@login_required
def api_change_password():
    data = request.get_json(silent=True) or {}
    old_pwd = data.get('old_password', '')
    new_pwd = data.get('new_password', '')
    confirm_pwd = data.get('confirm_password', '')
    if not new_pwd or len(new_pwd) < 4:
        return jsonify({'error': '新密码长度不能少于4位'}), 400
    if new_pwd != confirm_pwd:
        return jsonify({'error': '两次输入的新密码不一致'}), 400
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username = ?', (session['username'],)).fetchone()
    if user['role'] != 'clerk' and user['password'] != hash_pwd(old_pwd):
        return jsonify({'error': '旧密码错误'}), 400
    db.execute('UPDATE users SET password = ? WHERE username = ?', (hash_pwd(new_pwd), session['username']))
    db.execute("INSERT INTO logs (time, action, detail, user_name) VALUES (?,?,?,?)",
               (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), '修改密码', f'{session["username"]} 修改了密码', session['username']))
    db.commit()
    return jsonify({'ok': True, 'message': '密码修改成功'})

@app.route('/api/users', methods=['GET'])
@admin_required
def api_get_users():
    db = get_db()
    rows = db.execute('SELECT username, role, display_name, must_change_pwd FROM users ORDER BY role, username').fetchall()
    users = []
    for r in rows:
        users.append({'username': r['username'], 'role': r['role'], 'role_name': ROLE_NAMES.get(r['role'], r['role']),
                       'display_name': r['display_name'] or r['username'], 'must_change_pwd': bool(r['must_change_pwd'])})
    return jsonify(users)

@app.route('/api/users', methods=['POST'])
@admin_required
def api_save_user():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    role = data.get('role', '')
    display_name = (data.get('display_name') or '').strip()
    password = data.get('password', '')
    if not username or not role:
        return jsonify({'error': '用户名和角色不能为空'}), 400
    if role not in ROLE_NAMES:
        return jsonify({'error': '无效的角色'}), 400
    db = get_db()
    existing = db.execute('SELECT username FROM users WHERE username = ?', (username,)).fetchone()
    if existing:
        # 编辑已有用户
        sets = ['role = ?', 'display_name = ?']
        params = [role, display_name]
        if password:
            sets.append('password = ?')
            params.append(hash_pwd(password))
        params.append(username)
        db.execute(f'UPDATE users SET {", ".join(sets)} WHERE username = ?', params)
        action = '编辑用户'
    else:
        # 新增用户
        if not password and role != 'clerk':
            return jsonify({'error': '新用户需要设置密码'}), 400
        db.execute('INSERT INTO users (username, password, role, display_name, must_change_pwd) VALUES (?,?,?,?,?)',
                   (username, hash_pwd(password) if password else '', role, display_name, 1 if password else 0))
        action = '新增用户'
    db.execute("INSERT INTO logs (time, action, detail, user_name) VALUES (?,?,?,?)",
               (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), action, f'{session["username"]} {action}: {username}({ROLE_NAMES.get(role,"")})', session['username']))
    db.commit()
    return jsonify({'ok': True, 'message': f'用户 {username} 已保存'})

@app.route('/api/users/<username>', methods=['DELETE'])
@admin_required
def api_delete_user(username):
    if username == session['username']:
        return jsonify({'error': '不能删除当前登录的用户'}), 400
    db = get_db()
    if not db.execute('SELECT username FROM users WHERE username = ?', (username,)).fetchone():
        return jsonify({'error': '用户不存在'}), 400
    db.execute('DELETE FROM users WHERE username = ?', (username,))
    db.execute("INSERT INTO logs (time, action, detail, user_name) VALUES (?,?,?,?)",
               (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), '删除用户', f'{session["username"]} 删除用户: {username}', session['username']))
    db.commit()
    return jsonify({'ok': True, 'message': f'用户 {username} 已删除'})

# ==================== 通用 CRUD API ====================
CATEGORIES = ['seals','carvings','usages','loans','recoveries','destroys','archives','logs']

TABLE_MAP = {
    'seals': 'seals', 'carvings': 'carvings', 'usages': 'usages',
    'loans': 'loans', 'recoveries': 'recoveries', 'destroys': 'destroys',
    'archives': 'archives', 'logs': 'logs'
}

@app.route('/api/data/<category>', methods=['GET', 'POST'])
@login_required
def handle_category(category):
    if category not in CATEGORIES:
        return jsonify({'error': '无效分类'}), 400
    table = TABLE_MAP[category]
    db = get_db()
    if request.method == 'GET':
        rows = db.execute(f'SELECT * FROM {table} ORDER BY rowid DESC').fetchall()
        result = [dict(r) for r in rows]
        # Fix column names: user → user_name mapping for frontend compatibility
        if category == 'usages':
            for r in result:
                if 'user_name' in r:
                    r['user'] = r.pop('user_name')
        if category == 'logs':
            for r in result:
                if 'user_name' in r:
                    r['user'] = r.pop('user_name')
        return jsonify(result)
    else:  # POST
        data = request.get_json()
        if isinstance(data, list):
            # Batch insert
            for item in data:
                insert_item(db, table, category, item)
        elif isinstance(data, dict):
            insert_item(db, table, category, data)
        else:
            return jsonify({'error': '无效数据格式'}), 400
        db.commit()
        return jsonify({'success': True, 'count': len(data) if isinstance(data, list) else 1})

@app.route('/api/data/<category>/<item_id>', methods=['PUT', 'DELETE'])
@login_required
def handle_item(category, item_id):
    if category not in CATEGORIES:
        return jsonify({'error': '无效分类'}), 400
    table = TABLE_MAP[category]
    db = get_db()
    if request.method == 'DELETE':
        id_col = 'id'
        # Parse ID as int if it's a number (for seals which use INTEGER PK)
        try:
            numeric_id = int(item_id)
            db.execute(f'DELETE FROM {table} WHERE {id_col}=?', (numeric_id,))
        except ValueError:
            db.execute(f'DELETE FROM {table} WHERE {id_col}=?', (item_id,))
        db.commit()
        return jsonify({'success': True})
    else:  # PUT
        data = request.get_json()
        id_col = 'id'
        # Normalize user_name ↔ user
        if category == 'usages' and 'user' in data:
            data['user_name'] = data.pop('user')
        if category == 'logs' and 'user' in data:
            data['user_name'] = data.pop('user')
        # Build SET clause
        sets = ', '.join(f'{k}=?' for k in data if k != 'id')
        vals = [v for k, v in data.items() if k != 'id']
        try:
            numeric_id = int(item_id)
            vals.append(numeric_id)
        except ValueError:
            vals.append(item_id)
        db.execute(f'UPDATE {table} SET {sets} WHERE {id_col}=?', vals)
        db.commit()
        return jsonify({'success': True})

def insert_item(db, table, category, item):
    """Insert a single item, mapping user→user_name for usages/logs"""
    item = dict(item)
    if category == 'usages' and 'user' in item:
        item['user_name'] = item.pop('user')
    if category == 'logs' and 'user' in item:
        item['user_name'] = item.pop('user')
    # Convert JSON fields
    if 'approvals' in item and isinstance(item['approvals'], list):
        item['approvals'] = json.dumps(item['approvals'], ensure_ascii=False)
    cols = ', '.join(item.keys())
    placeholders = ', '.join('?' * len(item))
    db.execute(f'INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})', list(item.values()))

# ==================== 批量操作 ====================
@app.route('/api/batch/<category>', methods=['POST'])
@login_required
def batch_operation(category):
    if category not in CATEGORIES:
        return jsonify({'error': '无效分类'}), 400
    table = TABLE_MAP[category]
    data = request.get_json()
    action = data.get('action', 'delete')
    ids = data.get('ids', [])
    db = get_db()
    id_col = 'id'
    if action == 'delete':
        for iid in ids:
            try:
                db.execute(f'DELETE FROM {table} WHERE {id_col}=?', (int(iid),))
            except ValueError:
                db.execute(f'DELETE FROM {table} WHERE {id_col}=?', (iid,))
        db.commit()
        return jsonify({'success': True, 'deleted': len(ids)})
    elif action == 'update':
        updates = data.get('updates', {})
        for iid in ids:
            sets = ', '.join(f'{k}=?' for k in updates)
            vals = list(updates.values())
            try:
                vals.append(int(iid))
            except ValueError:
                vals.append(iid)
            db.execute(f'UPDATE {table} SET {sets} WHERE {id_col}=?', vals)
        db.commit()
        return jsonify({'success': True, 'updated': len(ids)})
    return jsonify({'error': '未知操作'}), 400

# ==================== 设置管理 ====================
@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    db = get_db()
    def get_setting(key, default):
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row and row['value']:
            try:
                return json.loads(row['value'])
            except Exception:
                return default
        return default
    return jsonify({
        'departments': get_setting('departments', []),
        'approvers': get_setting('approvers', []),
        'default_seals': get_setting('default_seals', []),
        'doc_types': get_setting('doc_types', ['证明','合同','报告','申请','函件','其他'])
    })

@app.route('/api/settings', methods=['POST'])
@login_required
@admin_required
def save_settings():
    data = request.get_json()
    db = get_db()
    for key in ['departments', 'approvers', 'default_seals', 'doc_types']:
        if key in data:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                       (key, json.dumps(data[key], ensure_ascii=False)))
    db.commit()
    return jsonify({'success': True})

# ==================== 印章默认设置（管理员配置常用印章） ====================
@app.route('/api/settings/default-seals', methods=['GET'])
@login_required
def get_default_seals():
    """获取管理员设定的默认印章列表（供usage.html使用）"""
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key='default_seals'").fetchone()
    if row and row['value']:
        return jsonify(json.loads(row['value']))
    return jsonify([])

@app.route('/api/settings/default-seals', methods=['POST'])
@login_required
@admin_required
def save_default_seals():
    """管理员设定默认印章"""
    data = request.get_json()
    seals = data if isinstance(data, list) else []
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('default_seals', ?)",
               (json.dumps(seals, ensure_ascii=False),))
    db.commit()
    return jsonify({'success': True, 'count': len(seals)})

# ==================== Usage 专用 API（供 usage.html 扫码登记页使用） ====================
@app.route('/api/usage/search-seals', methods=['GET'])
@login_required
def search_seals():
    """搜索印章名称（usage.html下拉联想用）"""
    q = request.args.get('q', '').strip()
    db = get_db()
    if q:
        rows = db.execute(
            "SELECT id, name, dept, type, status FROM seals WHERE name LIKE ? AND status NOT IN ('已销毁','已废止') LIMIT 10",
            (f'%{q}%',)
        ).fetchall()
    else:
        # 无关键词时返回默认印章（管理员设定的常用印章）
        default_row = db.execute("SELECT value FROM settings WHERE key='default_seals'").fetchone()
        if default_row and default_row['value']:
            try:
                default_ids = json.loads(default_row['value'])
                if default_ids:
                    placeholders = ','.join('?' * len(default_ids))
                    rows = db.execute(
                        f"SELECT id, name, dept, type, status FROM seals WHERE id IN ({placeholders}) AND status NOT IN ('已销毁','已废止')",
                        default_ids
                    ).fetchall()
                    if rows:
                        return jsonify([dict(r) for r in rows])
            except Exception:
                pass
        # 回退：返回所有在用印章
        rows = db.execute(
            "SELECT id, name, dept, type, status FROM seals WHERE status NOT IN ('已销毁','已废止') LIMIT 50"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/usage/summary', methods=['GET'])
@login_required
def usage_summary():
    """获取使用登记摘要统计"""
    db = get_db()
    active = db.execute("SELECT COUNT(*) as c FROM usages WHERE status IN ('使用中','借出中')").fetchone()['c']
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_count = db.execute(
        "SELECT COUNT(*) as c FROM usages WHERE checkOut LIKE ?", (f'{today_str}%',)
    ).fetchone()['c']
    total = db.execute("SELECT COUNT(*) as c FROM usages").fetchone()['c']
    # 最近归还20条
    returned = db.execute(
        "SELECT * FROM usages WHERE status='已归还' ORDER BY checkIn DESC LIMIT 20"
    ).fetchall()
    # 使用中/借出中记录
    active_list = db.execute(
        "SELECT * FROM usages WHERE status IN ('使用中','借出中') ORDER BY checkOut DESC"
    ).fetchall()
    result = {
        'activeCount': active, 'todayCount': today_count, 'totalCount': total,
        'activeList': [dict_fix_user(r) for r in active_list],
        'returnedList': [dict_fix_user(r) for r in returned]
    }
    return jsonify(result)

@app.route('/api/usage/record', methods=['POST'])
@login_required
def record_usage():
    """登记印章使用（支持多印章批量登记 + 借出类型）"""
    data = request.get_json()
    seal_names_input = data.get('sealNames', data.get('sealName', ''))
    # 支持逗号/顿号分隔的多印章：公章,法人章
    if isinstance(seal_names_input, str):
        seal_names = [s.strip() for s in seal_names_input.replace('、', ',').split(',') if s.strip()]
    elif isinstance(seal_names_input, list):
        seal_names = [s.strip() for s in seal_names_input if s and s.strip()]
    else:
        seal_names = []

    user = data.get('user', '').strip()
    purpose = data.get('purpose', '').strip()
    usage_type = data.get('usageType', 'use')  # 'stamp'=盖章使用, 'use'=领用使用, 'loan'=借出外带

    if not seal_names or not user or not purpose:
        return jsonify({'error': '印章名称、使用人和用途为必填项'}), 400

    # 盖章使用类型直接标记为已完成，无需归还
    status = '借出中' if usage_type == 'loan' else ('已完成' if usage_type == 'stamp' else '使用中')
    batch_id = 'B' + datetime.now().strftime('%Y%m%d%H%M%S') + str(int(datetime.now().timestamp() * 1000) % 10000).zfill(4)
    records = []
    db = get_db()

    for seal_name in seal_names:
        uid = 'U' + datetime.now().strftime('%Y%m%d%H%M%S') + str(int(datetime.now().timestamp() * 1000) % 10000).zfill(4)
        # 略延迟以免毫秒级 ID 碰撞
        import time; time.sleep(0.002)
        record = {
            'id': uid, 'sealName': seal_name, 'user_name': user,
            'dept': data.get('dept', ''), 'docType': data.get('docType', '证明'),
            'purpose': purpose, 'approver': data.get('approver', ''),
            'checkOut': data.get('checkOut', datetime.now().strftime('%Y/%m/%d %H:%M:%S')),
            'checkIn': '', 'status': status, 'copies': data.get('copies', '1'), 'sealId': '',
            'usageType': usage_type, 'batchId': batch_id,
            'contactPhone': data.get('contactPhone', ''), 'expectedReturn': data.get('expectedReturn', '')
        }
        cols = ', '.join(record.keys())
        placeholders = ', '.join('?' * len(record))
        db.execute(f'INSERT INTO usages ({cols}) VALUES ({placeholders})', list(record.values()))
        record['user'] = record.pop('user_name')
        records.append(record)

    db.commit()
    return jsonify({'success': True, 'batchId': batch_id, 'count': len(records), 'records': records})

@app.route('/api/usage/return/<uid>', methods=['PUT'])
@login_required
def return_usage(uid):
    """归还单个印章"""
    db = get_db()
    now_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    db.execute("UPDATE usages SET status='已归还', checkIn=? WHERE id=?", (now_str, uid))
    db.commit()
    if db.total_changes == 0:
        return jsonify({'error': '未找到该使用记录'}), 404
    return jsonify({'success': True, 'checkIn': now_str})

@app.route('/api/usage/return-batch/<batch_id>', methods=['PUT'])
@login_required
def return_batch(batch_id):
    """批量归还同一批次的所有印章"""
    db = get_db()
    now_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    db.execute(
        "UPDATE usages SET status='已归还', checkIn=? WHERE batchId=? AND status IN ('使用中','借出中')",
        (now_str, batch_id)
    )
    db.commit()
    count = db.total_changes
    if count == 0:
        return jsonify({'error': '未找到该批次的在用记录'}), 404
    return jsonify({'success': True, 'checkIn': now_str, 'returnedCount': count})


def dict_fix_user(row_dict):
    """将 user_name 映射为 user 以兼容前端"""
    d = dict(row_dict)
    if 'user_name' in d:
        d['user'] = d.pop('user_name')
    return d

# ==================== 数据初始化（从data.json导入） ====================
@app.route('/api/init-status', methods=['GET'])
def init_status():
    db = get_db()
    count = db.execute('SELECT COUNT(*) as c FROM seals').fetchone()['c']
    return jsonify({'initialized': count > 0, 'sealCount': count})

@app.route('/api/init', methods=['POST'])
@login_required
def initialize_data():
    """从data.json导入初始印章数据，并生成演示数据"""
    data_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
    if not os.path.exists(data_file):
        return jsonify({'error': 'data.json 文件不存在'}), 404
    db = get_db()
    with open(data_file, 'r', encoding='utf-8') as f:
        seals = json.load(f)
    if not seals:
        return jsonify({'error': 'data.json 中没有数据'}), 400

    # Check if already initialized
    existing = db.execute('SELECT COUNT(*) as c FROM seals').fetchone()['c']
    if existing > 0:
        return jsonify({'success': True, 'count': existing, 'message': '已初始化'})

    # Get valid columns from the seals table
    cols_info = db.execute('PRAGMA table_info(seals)').fetchall()
    valid_cols = {row['name'] for row in cols_info}

    count = 0
    for s in seals:
        filtered = {k: v for k, v in s.items() if k in valid_cols}
        if not filtered.get('name'):
            continue
        filtered.setdefault('status', '在用')
        cols = ', '.join(filtered.keys())
        placeholders = ', '.join('?' * len(filtered))
        try:
            db.execute(f'INSERT OR IGNORE INTO seals ({cols}) VALUES ({placeholders})', list(filtered.values()))
            count += db.total_changes
        except Exception as e:
            print(f'  skip row: {e}')
    db.commit()
    total = db.execute('SELECT COUNT(*) as c FROM seals').fetchone()['c']

    # ===== 演示数据 =====
    now_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # 使用登记演示 (10条)
    demo_usages = [
        {'id': 'u001', 'sealId': '1', 'sealName': seals[0]['name'], 'user': '张明华', 'dept': '院办公室', 'docType': '证明', 'purpose': '开具在职证明', 'approver': '办公室主任', 'checkOut': '2026-05-20 09:30', 'checkIn': '2026-05-20 10:15', 'status': '已归还', 'copies': '3', 'usageType': 'stamp', 'batchId': 'B20260520001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u002', 'sealId': '5', 'sealName': seals[4]['name'], 'user': '李红梅', 'dept': '财务科', 'docType': '合同', 'purpose': '医疗器械采购合同盖章', 'approver': '分管副院长', 'checkOut': '2026-05-21 14:00', 'checkIn': '2026-05-21 15:30', 'status': '已归还', 'copies': '2', 'usageType': 'stamp', 'batchId': 'B20260521001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u003', 'sealId': '1', 'sealName': seals[0]['name'], 'user': '王建国', 'dept': '人事科', 'docType': '报告', 'purpose': '年度考核报告盖章', 'approver': '办公室主任', 'checkOut': '2026-05-22 10:00', 'checkIn': '2026-05-22 11:00', 'status': '已归还', 'copies': '5', 'usageType': 'stamp', 'batchId': 'B20260522001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u004', 'sealId': '9', 'sealName': seals[8]['name'], 'user': '赵丽萍', 'dept': '医务科', 'docType': '申请', 'purpose': '科研项目申请材料', 'approver': '院长', 'checkOut': '2026-05-23 08:45', 'checkIn': '2026-05-23 09:20', 'status': '已归还', 'copies': '1', 'usageType': 'stamp', 'batchId': 'B20260523001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u005', 'sealId': '14', 'sealName': seals[13]['name'] if len(seals) > 13 else '财务专用章', 'user': '陈志强', 'dept': '财务科', 'docType': '证明', 'purpose': '财务审计证明盖章', 'approver': '办公室主任', 'checkOut': '2026-05-24 09:00', 'checkIn': '', 'status': '使用中', 'copies': '2', 'usageType': 'stamp', 'batchId': 'B20260524001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u006', 'sealId': '1', 'sealName': seals[0]['name'], 'user': '刘芳', 'dept': '护理部', 'docType': '函件', 'purpose': '护理培训邀请函', 'approver': '分管副院长', 'checkOut': '2026-05-24 14:00', 'checkIn': '', 'status': '借出中', 'copies': '1', 'usageType': 'loan', 'batchId': 'B20260524002', 'contactPhone': '13988123456', 'expectedReturn': '2026-05-27'},
        {'id': 'u007', 'sealId': '3', 'sealName': seals[2]['name'], 'user': '杨建华', 'dept': '总务科', 'docType': '合同', 'purpose': '物业合同续签盖章', 'approver': '院长', 'checkOut': '2026-05-25 10:30', 'checkIn': '', 'status': '使用中', 'copies': '4', 'usageType': 'stamp', 'batchId': 'B20260525001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u008', 'sealId': '10', 'sealName': seals[9]['name'] if len(seals) > 9 else '党办公章', 'user': '周丽华', 'dept': '党委办公室', 'docType': '报告', 'purpose': '党建工作年度报告', 'approver': '院长', 'checkOut': '2026-05-18 09:00', 'checkIn': '2026-05-18 10:30', 'status': '已归还', 'copies': '2', 'usageType': 'stamp', 'batchId': 'B20260518001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u009', 'sealId': '4', 'sealName': seals[3]['name'], 'user': '吴国平', 'dept': '基建科', 'docType': '申请', 'purpose': '基建维修申请盖章', 'approver': '分管副院长', 'checkOut': '2026-05-19 15:00', 'checkIn': '2026-05-19 16:00', 'status': '已归还', 'copies': '1', 'usageType': 'stamp', 'batchId': 'B20260519001', 'contactPhone': '', 'expectedReturn': ''},
        {'id': 'u010', 'sealId': '7', 'sealName': seals[6]['name'] if len(seals) > 6 else '财务科公章', 'user': '孙秀英', 'dept': '审计科', 'docType': '其他', 'purpose': '内部审计文件盖章', 'approver': '办公室主任', 'checkOut': '2026-05-25 08:30', 'checkIn': '', 'status': '使用中', 'copies': '3', 'usageType': 'stamp', 'batchId': 'B20260525002', 'contactPhone': '', 'expectedReturn': ''},
    ]
    for u in demo_usages:
        insert_item(db, 'usages', 'usages', u)
    db.commit()

    # 借用管理演示 (10条)
    demo_loans = [
        {'id': 'l001', 'sealId': '1', 'sealName': seals[0]['name'], 'borrower': '张明华', 'dept': '院办公室', 'phone': '13988531234', 'loanDate': '2026-05-15', 'expectedDate': '2026-05-18', 'destination': '州卫健委', 'approver': '办公室主任', 'purpose': '办理医疗许可变更', 'status': '已归还', 'returnDate': '2026-05-17', 'approvals': '[{"name":"办公室主任","result":"approved","time":"2026-05-14","note":"同意"}]'},
        {'id': 'l002', 'sealId': '9', 'sealName': seals[8]['name'], 'borrower': '李红梅', 'dept': '医务科', 'phone': '13988234567', 'loanDate': '2026-05-18', 'expectedDate': '2026-05-20', 'destination': '州卫健委', 'approver': '分管副院长', 'purpose': '医师资格考试材料盖章', 'status': '已归还', 'returnDate': '2026-05-19', 'approvals': '[{"name":"分管副院长","result":"approved","time":"2026-05-17","note":"同意"}]'},
        {'id': 'l003', 'sealId': '3', 'sealName': seals[2]['name'], 'borrower': '王建国', 'dept': '人事科', 'phone': '13988345678', 'loanDate': '2026-05-20', 'expectedDate': '2026-05-25', 'destination': '州人社局', 'approver': '院长', 'purpose': '人才引进材料办理', 'status': '借出', 'returnDate': '', 'approvals': '[{"name":"院长","result":"approved","time":"2026-05-19","note":"同意"}]'},
        {'id': 'l004', 'sealId': '1', 'sealName': seals[0]['name'], 'borrower': '赵丽萍', 'dept': '科教科', 'phone': '13988456789', 'loanDate': '2026-05-22', 'expectedDate': '2026-05-24', 'destination': '大理大学', 'approver': '分管副院长', 'purpose': '科研合作协议签署', 'status': '借出', 'returnDate': '', 'approvals': '[{"name":"分管副院长","result":"approved","time":"2026-05-21","note":"同意"}]'},
        {'id': 'l005', 'sealId': '14', 'sealName': seals[13]['name'] if len(seals) > 13 else '财务专用章', 'borrower': '陈志强', 'dept': '财务科', 'phone': '13988567890', 'loanDate': '2026-05-23', 'expectedDate': '2026-05-28', 'destination': '州财政局', 'approver': '院长', 'purpose': '财政检查配合材料', 'status': '借出', 'returnDate': '', 'approvals': '[{"name":"院长","result":"approved","time":"2026-05-22","note":"同意，注意保密"}]'},
        {'id': 'l006', 'sealId': '5', 'sealName': seals[4]['name'], 'borrower': '刘芳', 'dept': '护理部', 'phone': '13988678901', 'loanDate': '2026-05-24', 'expectedDate': '2026-05-26', 'destination': '州卫健委', 'approver': '分管副院长', 'purpose': '护士执照注册材料', 'status': '待审批', 'returnDate': '', 'approvals': '[]'},
        {'id': 'l007', 'sealId': '10', 'sealName': seals[9]['name'] if len(seals) > 9 else '党办公章', 'borrower': '杨建华', 'dept': '党委办公室', 'phone': '13988789012', 'loanDate': '2026-05-10', 'expectedDate': '2026-05-13', 'destination': '州委组织部', 'approver': '院长', 'purpose': '干部考察材料', 'status': '已归还', 'returnDate': '2026-05-12', 'approvals': '[{"name":"院长","result":"approved","time":"2026-05-09","note":"同意"}]'},
        {'id': 'l008', 'sealId': '4', 'sealName': seals[3]['name'], 'borrower': '周丽华', 'dept': '基建科', 'phone': '13988890123', 'loanDate': '2026-05-12', 'expectedDate': '2026-05-15', 'destination': '州住建局', 'approver': '分管副院长', 'purpose': '工程验收资料盖章', 'status': '已归还', 'returnDate': '2026-05-14', 'approvals': '[{"name":"分管副院长","result":"approved","time":"2026-05-11","note":"同意"}]'},
        {'id': 'l009', 'sealId': '2', 'sealName': seals[1]['name'], 'borrower': '吴国平', 'dept': '保卫科', 'phone': '13988901234', 'loanDate': '2026-05-25', 'expectedDate': '2026-05-30', 'destination': '州公安局', 'approver': '办公室主任', 'purpose': '安保备案材料', 'status': '待审批', 'returnDate': '', 'approvals': '[]'},
        {'id': 'l010', 'sealId': '7', 'sealName': seals[6]['name'] if len(seals) > 6 else '财务科公章', 'borrower': '孙秀英', 'dept': '审计科', 'phone': '13988012345', 'loanDate': '2026-05-08', 'expectedDate': '2026-05-10', 'destination': '州审计局', 'approver': '分管副院长', 'purpose': '审计配合材料', 'status': '已归还', 'returnDate': '2026-05-10', 'approvals': '[{"name":"分管副院长","result":"approved","time":"2026-05-07","note":"同意"}]'},
    ]
    for l in demo_loans:
        insert_item(db, 'loans', 'loans', l)
    db.commit()

    # 刻制管理演示 (10条)
    demo_carvings = [
        {'id': 'c001', 'name': '大理大学第一附属医院工会委员会', 'type': '公章', 'dept': '工会', 'material': '橡胶', 'shape': '圆形', 'applicant': '刘芳', 'applyDate': '2026-05-01', 'expectedDate': '2026-05-15', 'vendor': '大理市刻章中心', 'status': '已完成', 'completeDate': '2026-05-12', 'remark': '工会换届重新刻制', 'approvals': '[{"name":"分管副院长","result":"approved","time":"2026-05-02"}]', 'reason': '', 'quantity': 1},
        {'id': 'c002', 'name': '大理大学第一附属医院营养科', 'type': '公章', 'dept': '总务科', 'material': '橡胶', 'shape': '圆形', 'applicant': '杨建华', 'applyDate': '2026-05-05', 'expectedDate': '2026-05-20', 'vendor': '大理市刻章中心', 'status': '已完成', 'completeDate': '2026-05-18', 'remark': '科室搬迁更换新章', 'approvals': '[{"name":"办公室主任","result":"approved","time":"2026-05-06"}]', 'reason': '', 'quantity': 1},
        {'id': 'c003', 'name': '大理大学第一附属医院体检专用章', 'type': '体检专用章', 'dept': '院办公室', 'material': '橡胶', 'shape': '圆形', 'applicant': '张明华', 'applyDate': '2026-05-10', 'expectedDate': '2026-05-25', 'vendor': '大理市刻章中心', 'status': '刻制中', 'completeDate': '', 'remark': '原章磨损严重', 'approvals': '[{"name":"办公室主任","result":"approved","time":"2026-05-11"}]', 'reason': '', 'quantity': 1},
        {'id': 'c004', 'name': '大理大学第一附属医院医疗证明章', 'type': '医疗证明章', 'dept': '医务科', 'material': '牛角', 'shape': '圆形', 'applicant': '李红梅', 'applyDate': '2026-05-18', 'expectedDate': '2026-06-01', 'vendor': '下关刻印社', 'status': '已批准', 'completeDate': '', 'remark': '', 'approvals': '[{"name":"分管副院长","result":"approved","time":"2026-05-19"}]', 'reason': '原章字迹模糊', 'quantity': 1},
        {'id': 'c005', 'name': '大理大学第一附属医院收费专用章', 'type': '收费专用章', 'dept': '财务科', 'material': '牛角', 'shape': '椭圆', 'applicant': '陈志强', 'applyDate': '2026-05-20', 'expectedDate': '', 'vendor': '', 'status': '待审批', 'completeDate': '', 'remark': '配合财务系统升级', 'approvals': '[]', 'reason': '旧章规格不符', 'quantity': 1},
        {'id': 'c006', 'name': '大理大学第一附属医院出生医学证明专用章', 'type': '出生医学证明专用章', 'dept': '院办公室', 'material': '橡胶', 'shape': '圆形', 'applicant': '赵丽萍', 'applyDate': '2026-05-15', 'expectedDate': '2026-05-30', 'vendor': '大理市刻章中心', 'status': '刻制中', 'completeDate': '', 'remark': '', 'approvals': '[{"name":"院长","result":"approved","time":"2026-05-16"}]', 'reason': '省卫健委要求更换新版本', 'quantity': 1},
        {'id': 'c007', 'name': '大理大学第一附属医院继续医学教育委员会', 'type': '公章', 'dept': '教学科', 'material': '橡胶', 'shape': '圆形', 'applicant': '周丽华', 'applyDate': '2026-05-22', 'expectedDate': '', 'vendor': '', 'status': '待审批', 'completeDate': '', 'remark': '', 'approvals': '[]', 'reason': '原章遗失需补刻', 'quantity': 1},
        {'id': 'c008', 'name': '大理大学第一附属医院医务科', 'type': '公章', 'dept': '医务科', 'material': '橡胶', 'shape': '圆形', 'applicant': '王建国', 'applyDate': '2026-04-25', 'expectedDate': '2026-05-10', 'vendor': '大理市刻章中心', 'status': '已完成', 'completeDate': '2026-05-08', 'remark': '机构更名重刻', 'approvals': '[{"name":"院长","result":"approved","time":"2026-04-26"}]', 'reason': '', 'quantity': 1},
        {'id': 'c009', 'name': '大理大学第一附属医院护理部', 'type': '公章', 'dept': '护理部', 'material': '橡胶', 'shape': '圆形', 'applicant': '吴国平', 'applyDate': '2026-05-08', 'expectedDate': '2026-05-22', 'vendor': '下关刻印社', 'status': '已完成', 'completeDate': '2026-05-20', 'remark': '', 'approvals': '[{"name":"分管副院长","result":"approved","time":"2026-05-09"}]', 'reason': '', 'quantity': 1},
        {'id': 'c010', 'name': '大理大学第一附属医院病历复印专用章', 'type': '病历复印专用章', 'dept': '院办公室', 'material': '橡胶', 'shape': '长方形', 'applicant': '孙秀英', 'applyDate': '2026-05-24', 'expectedDate': '', 'vendor': '', 'status': '待审批', 'completeDate': '', 'remark': '', 'approvals': '[]', 'reason': '病案管理要求更换', 'quantity': 1},
    ]
    for c in demo_carvings:
        insert_item(db, 'carvings', 'carvings', c)
    db.commit()

    # 回收核验演示 (10条)
    demo_recoveries = [
        {'id': 'r001', 'sealId': '86', 'sealName': '大理学院附属医院医疗管理办公室', 'checker': '张明华', 'date': '2026-05-10', 'condition': '磨损', 'result': '归档', 'note': '机构更名后旧章收回'},
        {'id': 'r002', 'sealId': '90', 'sealName': '大理学院附属医院医务科', 'checker': '李红梅', 'date': '2026-05-12', 'condition': '完好', 'result': '需更换', 'note': '机构更名需重刻'},
        {'id': 'r003', 'sealId': '91', 'sealName': '大理学院附属医院财务科', 'checker': '王建国', 'date': '2026-05-14', 'condition': '损坏', 'result': '直接报废', 'note': '印章裂纹无法继续使用'},
        {'id': 'r004', 'sealId': '92', 'sealName': '大理学院附属医院人力资源部', 'checker': '赵丽萍', 'date': '2026-05-15', 'condition': '完好', 'result': '归档', 'note': '机构调整旧章归档'},
        {'id': 'r005', 'sealId': '1', 'sealName': seals[0]['name'], 'checker': '陈志强', 'date': '2026-05-18', 'condition': '完好', 'result': '通过', 'note': '年度常规核验'},
        {'id': 'r006', 'sealId': '9', 'sealName': seals[8]['name'], 'checker': '刘芳', 'date': '2026-05-20', 'condition': '完好', 'result': '通过', 'note': '年度常规核验'},
        {'id': 'r007', 'sealId': '14', 'sealName': seals[13]['name'] if len(seals) > 13 else '财务专用章', 'checker': '杨建华', 'date': '2026-05-22', 'condition': '轻微磨损', 'result': '通过', 'note': '建议下次核验时考虑更换'},
        {'id': 'r008', 'sealId': '4', 'sealName': seals[3]['name'], 'checker': '周丽华', 'date': '2026-05-23', 'condition': '完好', 'result': '通过', 'note': '状态良好'},
        {'id': 'r009', 'sealId': '10', 'sealName': seals[9]['name'] if len(seals) > 9 else '党办公章', 'checker': '吴国平', 'date': '2026-05-24', 'condition': '完好', 'result': '通过', 'note': '年度常规核验'},
        {'id': 'r010', 'sealId': '3', 'sealName': seals[2]['name'], 'checker': '孙秀英', 'date': '2026-05-25', 'condition': '完好', 'result': '通过', 'note': '状态良好'},
    ]
    for r in demo_recoveries:
        insert_item(db, 'recoveries', 'recoveries', r)
    db.commit()

    # 归档记录演示 (10条)
    demo_archives = [
        {'id': 'a001', 'sealId': '86', 'sealName': '大理学院附属医院医疗管理办公室', 'endDate': '2013-02-27', 'archiveDate': '2026-05-10', 'handler': '张明华', 'reason': '机构更名废止', 'note': '医疗管理办公室更名为医务科'},
        {'id': 'a002', 'sealId': '90', 'sealName': '大理学院附属医院医务科（旧）', 'endDate': '2014-04-11', 'archiveDate': '2026-05-12', 'handler': '李红梅', 'reason': '机构更名废止', 'note': ''},
        {'id': 'a003', 'sealId': '91', 'sealName': '大理学院附属医院财务科（旧）', 'endDate': '2014-04-11', 'archiveDate': '2026-05-14', 'handler': '王建国', 'reason': '机构调整废止', 'note': ''},
        {'id': 'a004', 'sealId': '92', 'sealName': '大理学院附属医院人力资源部', 'endDate': '2014-04-11', 'archiveDate': '2026-05-15', 'handler': '赵丽萍', 'reason': '机构调整废止', 'note': ''},
        {'id': 'a005', 'sealId': '1', 'sealName': seals[0]['name'], 'endDate': '2020-01-01', 'archiveDate': '2026-03-01', 'handler': '陈志强', 'reason': '使用年限到期', 'note': '正常归档'},
        {'id': 'a006', 'sealId': '2', 'sealName': seals[1]['name'], 'endDate': '2019-12-31', 'archiveDate': '2026-03-05', 'handler': '刘芳', 'reason': '使用年限到期', 'note': ''},
        {'id': 'a007', 'sealId': '6', 'sealName': seals[5]['name'], 'endDate': '2021-06-30', 'archiveDate': '2026-03-10', 'handler': '杨建华', 'reason': '科室合并废止', 'note': ''},
        {'id': 'a008', 'sealId': '8', 'sealName': seals[7]['name'], 'endDate': '2022-03-15', 'archiveDate': '2026-04-01', 'handler': '周丽华', 'reason': '机构调整废止', 'note': ''},
        {'id': 'a009', 'sealId': '40', 'sealName': seals[9]['name'] if len(seals) > 9 else '筹备组基建科', 'endDate': '2015-12-31', 'archiveDate': '2026-04-15', 'handler': '吴国平', 'reason': '筹备组撤销', 'note': ''},
        {'id': 'a010', 'sealId': '16', 'sealName': seals[15]['name'] if len(seals) > 15 else '计生委公章', 'endDate': '2023-01-01', 'archiveDate': '2026-05-01', 'handler': '孙秀英', 'reason': '职能调整废止', 'note': ''},
    ]
    for a in demo_archives:
        insert_item(db, 'archives', 'archives', a)
    db.commit()

    # 操作日志
    db.execute("INSERT INTO logs (time, action, detail, user_name) VALUES (?,?,?,?)",
               (now_str, '系统初始化', f'从data.json导入{total}条印章数据及演示数据', '系统'))
    db.commit()
    return jsonify({'success': True, 'count': total})

# ==================== 导出 ====================
@app.route('/api/export/<category>', methods=['GET'])
@login_required
def export_csv(category):
    if category not in CATEGORIES + ['all']:
        return jsonify({'error': '无效分类'}), 400
    db = get_db()
    output = io.StringIO()
    writer = csv.writer(output)
    # BOM for Excel
    output.write('\ufeff')

    tables_to_export = {k: v for k, v in TABLE_MAP.items()} if category == 'all' else \
                       {category: TABLE_MAP[category]}

    for cat, table in tables_to_export.items():
        rows = db.execute(f'SELECT * FROM {table}').fetchall()
        if not rows: continue
        writer.writerow([f'=== {cat} ==='])
        writer.writerow([r.keys()[i] for i in range(len(r.keys()))] if rows else [])
        for r in rows:
            writer.writerow([r[i] for i in range(len(r.keys()))])
        writer.writerow([])

    csv_content = output.getvalue()
    output.close()
    return csv_content, 200, {
        'Content-Type': 'text/csv; charset=utf-8-sig',
        'Content-Disposition': f'attachment; filename=seal_export_{category}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    }

# ==================== 备份与恢复 ====================
@app.route('/api/backup', methods=['GET'])
@login_required
def backup_data():
    """导出全部数据为JSON"""
    db = get_db()
    backup = {'version': '3.2', 'timestamp': datetime.now().isoformat(), 'data': {}}
    for cat, table in TABLE_MAP.items():
        rows = db.execute(f'SELECT * FROM {table}').fetchall()
        backup['data'][cat] = [dict(r) for r in rows]
    # Fix user_name → user
    for cat in ['usages', 'logs']:
        if cat in backup['data']:
            for r in backup['data'][cat]:
                if 'user_name' in r:
                    r['user'] = r.pop('user_name')
    return jsonify(backup)

@app.route('/api/restore', methods=['POST'])
@login_required
@admin_required
def restore_data():
    """从JSON备份恢复数据"""
    data = request.get_json()
    if not data or 'data' not in data:
        return jsonify({'error': '无效的备份文件'}), 400
    db = get_db()
    # Clear all data first
    for table in TABLE_MAP.values():
        db.execute(f'DELETE FROM {table}')
    # Insert backup data
    for cat, items in data['data'].items():
        if cat not in TABLE_MAP or not items: continue
        table = TABLE_MAP[cat]
        for item in items:
            if cat == 'usages' and 'user' in item and 'user_name' not in item:
                item['user_name'] = item.pop('user')
            if cat == 'logs' and 'user' in item and 'user_name' not in item:
                item['user_name'] = item.pop('user')
            if 'approvals' in item and isinstance(item['approvals'], list):
                item['approvals'] = json.dumps(item['approvals'], ensure_ascii=False)
            cols = ', '.join(item.keys())
            placeholders = ', '.join('?' * len(item))
            db.execute(f'INSERT INTO {table} ({cols}) VALUES ({placeholders})', list(item.values()))
    db.commit()
    count = db.execute('SELECT COUNT(*) as c FROM seals').fetchone()['c']
    return jsonify({'success': True, 'sealCount': count})

# ==================== 清空数据 ====================
@app.route('/api/reset', methods=['POST'])
@login_required
@admin_required
def reset_data():
    db = get_db()
    for table in TABLE_MAP.values():
        db.execute(f'DELETE FROM {table}')
    db.commit()
    # Re-insert default settings
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('departments', '[\"院办公室\",\"党委办公室\",\"财务科\",\"人事科\",\"医务科\",\"护理部\",\"科教科\",\"教学科\",\"总务科\",\"基建科\",\"保卫科\",\"审计科\",\"纪检监察室\",\"工会\",\"团委\"]')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('approvers', '[{\"name\":\"院长\",\"level\":2},{\"name\":\"分管副院长\",\"level\":2},{\"name\":\"办公室主任\",\"level\":1},{\"name\":\"综合档案室\",\"level\":1}]')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('default_seals', '[]')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('doc_types', '[\"证明\",\"合同\",\"报告\",\"申请\",\"函件\",\"其他\"]')")
    db.commit()
    return jsonify({'success': True})

# ==================== 静态文件服务 ====================
@app.route('/')
@app.route('/index.html')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    if os.path.exists(os.path.join(os.path.dirname(__file__), filename)):
        return send_from_directory('.', filename)
    return jsonify({'error': 'Not found'}), 404

# ==================== 启动 ====================
if __name__ == '__main__':
    init_db()
    print('=' * 60)
    print('  大理大学第一附属医院印章管理系统 — 后端服务')
    print(f'  数据库: {DB_PATH}')
    print(f'  地址: http://localhost:5100')
    print('  按 Ctrl+C 停止服务')
    print('=' * 60)
    app.run(host='0.0.0.0', port=5100, debug=False)
