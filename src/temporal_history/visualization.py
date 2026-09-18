from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .context_assembly import load_scope_summaries
from .core import load_events, read_json, read_jsonl, write_json
from .integrity import validate_integrity

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
TOKEN_RE = re.compile(r"\b(?:xox[baprs]-|sk-)[A-Za-z0-9-]{8,}\b")


def redact(text: str) -> str:
    return TOKEN_RE.sub("[redacted token]", EMAIL_RE.sub("[redacted email]", text))


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()}
    return value


def visualization_data(output_dir: Path) -> dict[str, Any]:
    events = load_events(output_dir)
    summaries = [
        summary
        for scope in ("channel", "thread")
        for summary in load_scope_summaries(output_dir, scope)
    ]
    pressure = [
        row
        for scope in ("channel", "thread")
        for row in read_jsonl(output_dir / "summaries" / scope / "pressure.jsonl")
    ]
    participants = Counter(
        event.actor_display_name or event.actor_id or "Unknown" for event in events
    )
    data = {
        "manifest": read_json(output_dir / "manifest.json"),
        "integrity": validate_integrity(output_dir),
        "coverage": read_jsonl(output_dir / "coverage.jsonl"),
        "summaries": [
            {
                "summary_id": summary.summary_id,
                "scope_type": summary.scope_type,
                "channel_id": summary.channel_id,
                "thread_id": summary.thread_id,
                "summary_reason": summary.summary_reason,
                "granularity": summary.granularity,
                "status": summary.status,
                "local_start": summary.local_start.isoformat(),
                "local_end": summary.local_end.isoformat(),
                "utc_start": summary.utc_start.isoformat(),
                "utc_end": summary.utc_end.isoformat(),
                "summary_text": redact(summary.summary_text),
                "topics": [redact(item) for item in summary.topics],
                "decisions": [redact(item) for item in summary.decisions],
                "actions": [redact(item) for item in summary.actions],
                "open_questions": [redact(item) for item in summary.open_questions],
                "participants": summary.participants,
                "source_event_ids": summary.source_event_ids,
                "child_summary_ids": summary.child_summary_ids,
                "embedded_thread_summaries": sanitize(
                    summary.embedded_thread_summaries
                ),
                "context_selections": sanitize(summary.context_selections),
                "coverage_gaps": [redact(item) for item in summary.coverage_gaps],
            }
            for summary in summaries
        ],
        "pressure_summaries": sanitize(pressure),
        "events": [
            {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "actor_id": event.actor_id,
                "actor_display_name": event.actor_display_name,
                "actor_type": event.actor_type,
                "role": event.source_metadata.get("role"),
                "thread_root_id": event.thread_root_id,
                "is_thread_reply": event.is_thread_reply,
                "display_content": redact(event.display_content[:2000]),
                "content_truncated": len(event.display_content) > 2000,
                "attachment_findings": event.source_metadata.get(
                    "attachment_findings", []
                ),
            }
            for event in events
        ],
        "participants": [
            {"name": redact(name), "event_count": count}
            for name, count in participants.most_common()
        ],
        "attachments": sanitize(read_jsonl(output_dir / "attachments.jsonl")),
    }
    write_json(output_dir / "visualization_data.json", data)
    return data


def build_visualization(output_dir: Path, html_path: Path) -> dict[str, Any]:
    data = visualization_data(output_dir)
    embedded = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Temporal History — Channel History Experiment</title>
<style>
:root{color-scheme:light dark;font-family:Inter,system-ui,sans-serif}
body{margin:0;background:#0b1020;color:#e8edf7}
header{position:sticky;top:0;background:#11182b;padding:16px 22px;border-bottom:1px solid #2a3552;z-index:2}
h1{font-size:20px;margin:0 0 5px}.warning{color:#fbbf24;font-size:12px}
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;padding:12px 22px;background:#11182b}
input,select,button{background:#17213a;color:#e8edf7;border:1px solid #34425f;border-radius:6px;padding:8px}
nav{display:flex;gap:8px;padding:12px 22px;flex-wrap:wrap}button.active{background:#2563eb}
main{padding:0 22px 30px}.view{display:none}.view.active{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.card{background:#131c31;border:1px solid #2a3552;border-radius:8px;padding:12px;overflow:auto}
.raw{border-left:4px solid #3b82f6}.pressure{border-left:4px solid #a855f7}.six_hour{border-left:4px solid #22c55e}
.day{border-left:4px solid #14b8a6}.week{border-left:4px solid #eab308}.month{border-left:4px solid #f97316}.year{border-left:4px solid #ef4444}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px;border-bottom:1px solid #2a3552;vertical-align:top}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px}.muted{color:#9aa8c2}.bad{color:#f87171}.good{color:#4ade80}
.pill{display:inline-block;padding:2px 6px;margin:2px;background:#253353;border-radius:10px;font-size:11px}
</style>
</head>
<body>
<header><h1>Temporal History — Channel History Experiment</h1><div class="warning">Contains internal channel history. No credentials, emails, or attachment bytes are embedded.</div></header>
<section class="filters">
<input id="search" placeholder="Search topics, decisions, actions…">
<select id="scope"><option value="">All scopes</option><option>channel</option><option>thread</option></select>
<select id="granularity"><option value="">All granularities</option><option>pressure</option><option>six_hour</option><option>day</option><option>week</option><option>month</option><option>year</option></select>
<input id="thread" placeholder="Thread ID">
<input id="participant" placeholder="Participant">
<select id="status"><option value="">All statuses</option><option>committed</option><option>failed</option><option>empty</option></select>
<select id="reason"><option value="">Scheduled + pressure</option><option>backfill</option><option>pressure</option><option>repair</option></select>
</section>
<nav><button data-view="calendar" class="active">Calendar Coverage</button><button data-view="hierarchy">Hierarchy & Lineage</button><button data-view="threads">Channel & Threads</button><button data-view="participants">Participants</button><button data-view="integrity">Integrity</button></nav>
<main>
<section id="calendar" class="view active"></section>
<section id="hierarchy" class="view"></section>
<section id="threads" class="view"></section>
<section id="participants" class="view"></section>
<section id="integrity" class="view"></section>
</main>
<script id="temporal-data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('temporal-data').textContent);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const groupBy=(items,key)=>items.reduce((groups,item)=>{const value=key(item);(groups[value]??=[]).push(item);return groups},{});
const values=()=>({q:search.value.toLowerCase(),scope:scope.value,g:granularity.value,thread:thread.value,p:participant.value.toLowerCase(),status:status.value,reason:reason.value});
function match(s){const f=values(),text=JSON.stringify(s).toLowerCase();return(!f.q||text.includes(f.q))&&(!f.scope||s.scope_type===f.scope)&&(!f.g||s.granularity===f.g)&&(!f.thread||s.thread_id===f.thread)&&(!f.p||text.includes(f.p))&&(!f.status||s.status===f.status)&&(!f.reason||s.summary_reason===f.reason)}
function render(){
 const summaries=[...D.summaries,...D.pressure_summaries].filter(match);
 const by=groupBy(summaries,s=>s.granularity);
 calendar.innerHTML='<div class="grid">'+['pressure','six_hour','day','week','month','year'].map(g=>`<div class="card ${g}"><h3>${g}</h3><b>${(by[g]||[]).length}</b> visible summaries</div>`).join('')+'</div><div class="card"><table><tr><th>Start</th><th>Scope</th><th>Level</th><th>Status</th><th>Events</th></tr>'+summaries.slice(0,500).map(s=>`<tr><td>${esc(s.utc_start)}</td><td>${esc(s.scope_type)} ${esc(s.thread_id||'')}</td><td>${esc(s.granularity)}</td><td>${esc(s.status||'committed')}</td><td>${(s.source_event_ids||[]).length}</td></tr>`).join('')+'</table></div>';
 hierarchy.innerHTML='<div class="grid">'+summaries.slice(0,300).map(s=>`<details class="card ${s.granularity}"><summary>${esc(s.granularity)} · ${esc(s.utc_start)} · ${esc(s.scope_type)}</summary><p>${esc(s.summary_text)}</p><p><b>Children:</b> ${(s.child_summary_ids||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</p><p><b>Raw evidence:</b> ${(s.source_event_ids||[]).length} event IDs</p><pre>${esc(JSON.stringify({decisions:s.decisions,actions:s.actions,gaps:s.coverage_gaps},null,2))}</pre></details>`).join('')+'</div>';
 const threads=groupBy(D.summaries.filter(s=>s.scope_type==='thread'&&match(s)),s=>s.thread_id||'missing');
 document.getElementById('threads').innerHTML='<div class="grid">'+Object.entries(threads).slice(0,300).map(([id,ss])=>`<details class="card"><summary>${esc(id)} · ${ss.length} summaries</summary><p>${esc(ss.at(-1).summary_text)}</p><h4>Then / Now selections</h4><pre>${esc(JSON.stringify(ss.at(-1).context_selections||{},null,2))}</pre></details>`).join('')+'</div>';
 document.getElementById('participants').innerHTML='<div class="card"><table><tr><th>Resolved participant</th><th>Events</th></tr>'+D.participants.filter(x=>!values().p||x.name.toLowerCase().includes(values().p)).map(x=>`<tr><td>${esc(x.name)}</td><td>${x.event_count}</td></tr>`).join('')+'</table></div>';
 const I=D.integrity;document.getElementById('integrity').innerHTML=`<div class="card"><h2 class="${I.passed?'good':'bad'}">Integrity ${I.passed?'passed':'failed'}</h2><pre>${esc(JSON.stringify(I,null,2))}</pre></div><div class="card"><h3>Attachments</h3><pre>${esc(JSON.stringify(D.attachments,null,2))}</pre></div>`;
}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button,.view').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.view).classList.add('active')});
document.querySelectorAll('.filters input,.filters select').forEach(x=>x.oninput=render);render();
</script>
</body></html>""".replace("__DATA__", embedded)
    graph_template = Path(__file__).with_name("graph_template.html")
    html = graph_template.read_text(encoding="utf-8").replace("__DATA__", embedded)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    return {
        "html": str(html_path),
        "data": str(output_dir / "visualization_data.json"),
        "summaries": len(data["summaries"]),
        "events": len(data["events"]),
        "integrity_passed": data["integrity"]["passed"],
    }
