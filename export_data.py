import openpyxl
import json
from datetime import datetime, date, timedelta

wb = openpyxl.load_workbook('E:/工作文档/档案室/08_印章档案/印章档案目录.xlsx')
ws = wb['Sheet1']
rows = list(ws.iter_rows(values_only=True))

def fmt_date(v):
    if v is None:
        return ''
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, (int, float)):
        epoch = date(1899, 12, 30)
        return (epoch + timedelta(days=int(v))).strftime('%Y-%m-%d')
    return str(v)

data = []
for row in rows[1:]:
    if row[0] is None:
        continue
    编号, 名称, 材质, 形状, 启用时间, 废止时间, 备注 = row
    data.append({
        'id': int(编号) if 编号 else 0,
        'name': str(名称) if 名称 else '',
        'material': str(材质) if 材质 else '',
        'shape': str(形状) if 形状 else '',
        'startDate': fmt_date(启用时间),
        'endDate': fmt_date(废止时间),
        'remark': str(备注) if 备注 else '',
        'status': '已废止' if (废止时间 is not None) else '在用'
    })

with open('E:/工作文档/档案室/08_印章档案/印章管理系统/data.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f'Exported {len(data)} records')
