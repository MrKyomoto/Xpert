#!/usr/bin/env python3
"""从 process.json 生成圆桌研讨过程可视化 HTML 报告。"""
import json, sys, os, webbrowser
from datetime import datetime
from pathlib import Path

DIM_COLORS = {"A": "#4A90D9", "B": "#50C878", "C": "#FF6B6B", "D": "#FFD700", "E": "#9B59B6", "F": "#FF8C00"}
DIM_NAMES = {"A": "结构完整性", "B": "内容丰富性", "C": "内容准确性", "D": "内容一致性", "E": "语言逻辑性", "F": "素养导向性"}

def gen(process_path: str, out_path: str = ""):
    with open(process_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data["meta"]
    roles = data["roles"]
    discussion = data.get("discussion", [])
    modifications = data.get("modifications", [])

    if not out_path:
        out_path = os.path.splitext(process_path)[0] + ".html"

    # 分数趋势
    rounds = sorted(set(d["round"] for d in discussion))
    score_rounds, scores_by_dim = [], {}
    # 从 discussion 提取评分
    for d in discussion:
        if "评分:" not in d["content"]:
            continue
        r = d["round"]
        content = d["content"]
        score_rounds.append(r)
        for line in content.split("\n"):
            if "评分:" in line:
                # 格式: A=2.0, B=14.0, C=20.0, D=14.0, E=9.0, F=29.0, 总分=88.0
                import re
                for k in "ABCDEF":
                    m = re.search(fr'{k}=([\d.]+)', line)
                    if m:
                        scores_by_dim.setdefault(k, {})[r] = float(m.group(1))
                m = re.search(r'总分=([\d.]+)', line)
                if m:
                    scores_by_dim.setdefault("总分", {})[r] = float(m.group(1))
                break
    score_rounds = sorted(set(score_rounds))
    score_series = ""
    if score_rounds:
        for label, key in [("总分", "总分")] + [(f"维度{k}", k) for k in "ABCDEF"]:
            vals = scores_by_dim.get(key, {})
            if not vals:
                continue
            pts = ",".join(str(vals.get(r, 0)) for r in score_rounds)
            color = DIM_COLORS.get(key, "#333")
            score_series += f"{{name:'{label}', type:'line', smooth:true, symbol:'circle', data:[{pts}], itemStyle:{{color:'{color}'}}}},"

    # 修改统计
    src_count = {}
    for m in modifications:
        r = m.get("source_role", "unknown")
        src_count[r] = src_count.get(r, 0) + 1

    # 讨论时间线
    timeline = []
    current_round = 0
    for d in discussion:
        r = d["round"]
        content = d["content"].replace("\n", "<br>").replace("'", "\\'")
        entry = f"{{name:'{d['role_id']}',round:{r},content:'{content[:500]}'}}"
        timeline.append(entry)

    # 冲突统计（从 discussion 的 Chair 发言中统计）
    conflict_count = 0
    for d in discussion:
        if d["role_id"] == "r_chair":
            for w in ["冲突", "裁决", "驳回"]:
                if w in d["content"]:
                    conflict_count += 1
                    break

    # 角色映射
    role_map = {r["role_id"]: r["name"] for r in roles}

    html = f"""<!DOCTYPE html>
<html lang=zh>
<head><meta charset=utf-8>
<title>圆桌磨课过程 - {meta.get('sample_id','')}</title>
<script src=https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',PingFang SC,Microsoft YaHei,sans-serif;background:#f5f6fa;color:#2d3436;padding:20px}}
.header{{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border-radius:16px;padding:30px;margin-bottom:24px}}
.header h1{{font-size:24px;margin-bottom:8px}}
.header .meta{{opacity:.85;font-size:14px}}
.grid{{display:grid;grid-template-columns:2fr 1fr;gap:20px;margin-bottom:20px}}
.card{{background:#fff;border-radius:12px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
.card h3{{font-size:15px;color:#636e72;margin-bottom:12px;border-bottom:1px solid #eee;padding-bottom:8px}}
.chart{{height:320px}}
.stats-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}
.stat-card{{background:#fff;border-radius:12px;padding:20px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
.stat-card .num{{font-size:32px;font-weight:700;color:#667eea}}
.stat-card .label{{font-size:13px;color:#636e72;margin-top:4px}}
.timeline{{margin-bottom:20px}}
.tl-item{{background:#fff;border-radius:10px;margin-bottom:10px;padding:16px;border-left:4px solid #667eea;box-shadow:0 1px 4px rgba(0,0,0,.04)}}
.tl-item .role-tag{{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;color:#fff;background:#667eea;margin-right:8px}}
.tl-item .round-tag{{background:#dfe6e9;color:#636e72;padding:2px 10px;border-radius:20px;font-size:12px}}
.tl-item .content{{margin-top:10px;font-size:14px;line-height:1.7;color:#2d3436}}
.tl-item .ref{{font-size:12px;color:#636e72;margin-top:6px}}
.rejection{{border-left-color:#ff6b6b!important}}
.accepted{{border-left-color:#50C878!important}}
.merged{{border-left-color:#FFD700!important}}
</style></head>
<body>
<div class=header>
<h1>📋 圆桌磨课过程报告</h1>
<div class=meta>
{meta.get('student_id','')} · {meta.get('sample_id','')} · {meta.get('timestamp','')[:10] if meta.get('timestamp') else ''}
</div></div>

<div class=stats-grid>
<div class=stat-card><div class=num>{len(rounds) if score_rounds else '—'}</div><div class=label>打磨轮次</div></div>
<div class=stat-card><div class=num>{len(discussion)}</div><div class=label>发言条数</div></div>
<div class=stat-card><div class=num>{len(modifications)}</div><div class=label>修改记录</div></div>
<div class=stat-card><div class=num>{conflict_count}</div><div class=label>冲突记载</div></div>
</div>

<div class=grid>
<div class=card><h3>📈 评分趋势</h3><div class=chart id=chartTrend></div></div>
<div class=card><h3>👥 修改来源分布</h3><div class=chart id=chartPie></div></div>
</div>

<div class=card><h3>📝 讨论时间线</h3>
<div class=timeline>
"""
    # 按轮次分组
    timed_entries = {}
    for d in discussion:
        r = d["round"]
        timed_entries.setdefault(r, []).append(d)
    for r in sorted(timed_entries):
        html += f"<div style='font-size:14px;font-weight:700;color:#667eea;margin:16px 0 8px'>第{r}轮</div>"
        for d in timed_entries[r]:
            rid = d["role_id"]
            name = role_map.get(rid, rid)
            content = d["content"].replace("\n", "<br>")
            ref = d.get("refers_to", "")
            ref_html = f"<div class=ref>🔗 回应: {ref}</div>" if ref else ""
            css = ""
            if "评分:" in content:
                css = "rejection"
            html += f"""<div class='tl-item {css}'>
<span class=role-tag>{name}</span><span class=round-tag>r{r}</span>
{ref_html}
<div class=content>{content[:800]}</div></div>"""

    html += """</div></div>

<div class=card><h3>📋 修改记录</h3>
<table style='width:100%;border-collapse:collapse;font-size:13px'>
<tr style='background:#f8f9fa'><th style='text-align:left;padding:8px;border-bottom:2px solid #eee'>ID</th><th style='text-align:left;padding:8px;border-bottom:2px solid #eee'>位置</th><th style='text-align:left;padding:8px;border-bottom:2px solid #eee'>来源</th><th style='text-align:left;padding:8px;border-bottom:2px solid #eee'>修改后摘要</th><th style='text-align:left;padding:8px;border-bottom:2px solid #eee'>理由</th></tr>
"""
    for m in modifications[:50]:
        html += f"<tr><td style='padding:6px 8px;border-bottom:1px solid #eee'>{m['mod_id']}</td><td style='padding:6px 8px;border-bottom:1px solid #eee'>{m.get('location','')}</td><td style='padding:6px 8px;border-bottom:1px solid #eee'>{m['source_role']}</td><td style='padding:6px 8px;border-bottom:1px solid #eee'>{m.get('after_summary','')[:60]}</td><td style='padding:6px 8px;border-bottom:1px solid #eee;max-width:300px'>{m.get('rationale','')[:100]}</td></tr>"

    html += "</table></div>"

    if score_series:
        html += f"""
<script>
var chart1 = echarts.init(document.getElementById('chartTrend'));
chart1.setOption({{
    tooltip:{{trigger:'axis'}},
    legend:{{bottom:0,textStyle:{{fontSize:12}}}},
    grid:{{left:40,right:20,bottom:40,top:20}},
    xAxis:{{type:'category',data:[{','.join(str(r) for r in score_rounds)}]}},
    yAxis:{{type:'value',min:0,max:100}},
    series:[{score_series}]
}});
var chart2 = echarts.init(document.getElementById('chartPie'));
chart2.setOption({{
    tooltip:{{formatter:'{{b}}: {{c}} ({{d}}%)'}},
    series:[{{
        type:'pie',radius:['40%','70%'],
        data:[{','.join(f"{{name:'{role_map.get(k,k)}',value:{v}}}" for k,v in src_count.items())}]
    }}]
}});
window.addEventListener('resize',()=>{{chart1.resize();chart2.resize()}});
</script>"""

    html += "\n</body></html>"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"→ {out_path}")
    return out_path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python report/viz.py <process.json路径> [输出html路径]")
        sys.exit(1)
    gen(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")