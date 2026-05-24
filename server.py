"""
大理大学第一附属医院印章管理系统 — Flask + SQLite 后端
启动: python server.py
访问: http://localhost:5100
"""
import json, os, csv, io, sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, g

app = Flask(__name__, static_folder='.')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seal_archive.db')

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
        status TEXT DEFAULT '使用中', copies TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS loans (
        id TEXT PRIMARY KEY, sealId TEXT DEFAULT '', sealName TEXT DEFAULT '',
        applicant TEXT DEFAULT '', dept TEXT DEFAULT '', purpose TEXT DEFAULT '',
        contactPhone TEXT DEFAULT '', loanDate TEXT DEFAULT '', dueDate TEXT DEFAULT '',
        returnDate TEXT DEFAULT '', status TEXT DEFAULT '待审批',
        approver TEXT DEFAULT '', approvalDate TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS recoveries (
        id TEXT PRIMARY KEY, sealId TEXT DEFAULT '', sealName TEXT DEFAULT '',
        checker TEXT DEFAULT '', checkDate TEXT DEFAULT '',
        condition TEXT DEFAULT '', result TEXT DEFAULT '', note TEXT DEFAULT '',
        status TEXT DEFAULT '已核验'
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
    -- Default settings
    INSERT OR IGNORE INTO settings (key, value) VALUES ('departments', '["院办公室","党委办公室","财务科","人事科","医务科","护理部","科研科","教学科","总务科","基建科","保卫科","审计科","纪检监察室","工会","团委"]');
    INSERT OR IGNORE INTO settings (key, value) VALUES ('approvers', '[{"name":"院长","level":2},{"name":"分管副院长","level":2},{"name":"办公室主任","level":1},{"name":"档案科科长","level":1}]');
    INSERT OR IGNORE INTO settings (key, value) VALUES ('default_seals', '[]');
    INSERT OR IGNORE INTO settings (key, value) VALUES ('doc_types', '["证明","合同","报告","申请","函件","其他"]');
    ''')
    db.commit()
    db.close()

# ==================== 通用 CRUD API ====================
CATEGORIES = ['seals','carvings','usages','loans','recoveries','destroys','archives','logs']

TABLE_MAP = {
    'seals': 'seals', 'carvings': 'carvings', 'usages': 'usages',
    'loans': 'loans', 'recoveries': 'recoveries', 'destroys': 'destroys',
    'archives': 'archives', 'logs': 'logs'
}

@app.route('/api/data/<category>', methods=['GET', 'POST'])
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
def get_default_seals():
    """获取管理员设定的默认印章列表（供usage.html使用）"""
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key='default_seals'").fetchone()
    if row and row['value']:
        return jsonify(json.loads(row['value']))
    return jsonify([])

@app.route('/api/settings/default-seals', methods=['POST'])
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
def usage_summary():
    """获取使用登记摘要统计"""
    db = get_db()
    active = db.execute("SELECT COUNT(*) as c FROM usages WHERE status='使用中'").fetchone()['c']
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_count = db.execute(
        "SELECT COUNT(*) as c FROM usages WHERE checkOut LIKE ?", (f'{today_str}%',)
    ).fetchone()['c']
    total = db.execute("SELECT COUNT(*) as c FROM usages").fetchone()['c']
    # 最近归还10条
    returned = db.execute(
        "SELECT * FROM usages WHERE status='已归还' ORDER BY checkIn DESC LIMIT 10"
    ).fetchall()
    # 使用中记录
    active_list = db.execute(
        "SELECT * FROM usages WHERE status='使用中' ORDER BY checkOut DESC"
    ).fetchall()
    result = {
        'activeCount': active, 'todayCount': today_count, 'totalCount': total,
        'activeList': [dict_fix_user(r) for r in active_list],
        'returnedList': [dict_fix_user(r) for r in returned]
    }
    return jsonify(result)

@app.route('/api/usage/record', methods=['POST'])
def record_usage():
    """登记一笔印章使用"""
    data = request.get_json()
    seal_name = data.get('sealName', '').strip()
    user = data.get('user', '').strip()
    purpose = data.get('purpose', '').strip()
    if not seal_name or not user or not purpose:
        return jsonify({'error': '印章名称、使用人和用途为必填项'}), 400
    uid = 'U' + datetime.now().strftime('%Y%m%d%H%M%S') + str(int(datetime.now().timestamp() * 1000) % 10000).zfill(4)
    record = {
        'id': uid, 'sealName': seal_name, 'user_name': user,
        'dept': data.get('dept', ''), 'docType': data.get('docType', '证明'),
        'purpose': purpose, 'approver': data.get('approver', ''),
        'checkOut': data.get('checkOut', datetime.now().strftime('%Y/%m/%d %H:%M:%S')),
        'checkIn': '', 'status': '使用中', 'copies': '', 'sealId': ''
    }
    db = get_db()
    cols = ', '.join(record.keys())
    placeholders = ', '.join('?' * len(record))
    db.execute(f'INSERT INTO usages ({cols}) VALUES ({placeholders})', list(record.values()))
    db.commit()
    record['user'] = record.pop('user_name')
    return jsonify({'success': True, 'record': record})

@app.route('/api/usage/return/<uid>', methods=['PUT'])
def return_usage(uid):
    """归还印章"""
    db = get_db()
    now_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    db.execute("UPDATE usages SET status='已归还', checkIn=? WHERE id=?", (now_str, uid))
    db.commit()
    if db.total_changes == 0:
        return jsonify({'error': '未找到该使用记录'}), 404
    return jsonify({'success': True, 'checkIn': now_str})


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
def initialize_data():
    """从data.json导入初始印章数据"""
    data_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
    if not os.path.exists(data_file):
        return jsonify({'error': 'data.json 文件不存在'}), 404
    db = get_db()
    with open(data_file, 'r', encoding='utf-8') as f:
        seals = json.load(f)
    if not seals:
        return jsonify({'error': 'data.json 中没有数据'}), 400
    
    # Get valid columns from the seals table
    cols_info = db.execute('PRAGMA table_info(seals)').fetchall()
    valid_cols = {row['name'] for row in cols_info}
    
    count = 0
    for s in seals:
        # Filter to only valid columns
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
    db.execute("INSERT INTO logs (time, action, detail, user_name) VALUES (?,?,?,?)",
               (datetime.now().strftime('%Y/%m/%d %H:%M:%S'), '系统初始化', f'从data.json导入{total}条印章数据', '系统'))
    db.commit()
    return jsonify({'success': True, 'count': total})

# ==================== 导出 ====================
@app.route('/api/export/<category>', methods=['GET'])
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
def reset_data():
    db = get_db()
    for table in TABLE_MAP.values():
        db.execute(f'DELETE FROM {table}')
    db.commit()
    # Re-insert default settings
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('departments', '[\"院办公室\",\"党委办公室\",\"财务科\",\"人事科\",\"医务科\",\"护理部\",\"科研科\",\"教学科\",\"总务科\",\"基建科\",\"保卫科\",\"审计科\",\"纪检监察室\",\"工会\",\"团委\"]')")
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('approvers', '[{\"name\":\"院长\",\"level\":2},{\"name\":\"分管副院长\",\"level\":2},{\"name\":\"办公室主任\",\"level\":1},{\"name\":\"档案科科长\",\"level\":1}]')")
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
