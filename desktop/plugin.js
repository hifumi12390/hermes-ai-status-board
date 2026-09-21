import { ROUTES_AREA, SIDEBAR_NAV_AREA, useQuery } from '@hermes/plugin-sdk';
import { createElement as h, useState } from 'react';

const ID='ai-status-board';
let ctx;
export const COLORS={no_reported_incidents:'#44cf91',operational:'#44cf91',informational:'#65c9e6',maintenance:'#65c9e6',degraded:'#e7ca5b',partial_outage:'#ef9851',major_outage:'#ed6868',unknown:'#687180'};
const LABELS={no_reported_incidents:'No reported incidents',operational:'Operational',informational:'Informational',maintenance:'Maintenance',degraded:'Degraded',partial_outage:'Partial outage',major_outage:'Major outage',unknown:'Unknown'};
const RANGES=[[6,'6 hours'],[24,'24 hours'],[168,'7 days'],[720,'30 days'],[2160,'90 days'],[4320,'180 days']];
const STEPS=[[300,'5 min'],[900,'15 min'],[1800,'30 min'],[3600,'1 hour'],[21600,'6 hours'],[86400,'1 day']];
const control={background:'#151b24',color:'#dce4ec',border:'1px solid #354052',borderRadius:5,padding:'7px 10px',fontSize:12};
const muted={color:'#95a1b1',fontSize:12};
const date=value=>value?new Date(value).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
export const allowedSteps=hours=>STEPS.filter(([s])=>hours*3600/s<=600);

function Status({value,stale=false}) {
 return h('span',{style:{color:stale?COLORS.unknown:COLORS[value]||COLORS.unknown,fontSize:12,whiteSpace:'nowrap'}},'● ',LABELS[value]||'Unknown',stale?' · last known':'');
}
function Bars({timeline}) {
 const count=Math.max(1,timeline.buckets.length);
 return h('svg',{viewBox:`0 0 ${count*4} 19`,preserveAspectRatio:'none',style:{display:'block',height:19,width:'100%',minWidth:0,margin:'11px 0 6px'},role:'img','aria-label':`Locally observed history; coverage ${timeline.coverage_percent}%`},timeline.buckets.map((b,i)=>h('g',{key:i},
  h('title',null,`${date(b.from)} – ${date(b.to)} JST\n${LABELS[b.state]} · observed ${Math.round(b.coverage*100)}%`),
  h('rect',{x:i*4,y:0,width:3.3,height:19,rx:.3,fill:b.coverage===0?COLORS.unknown:COLORS[b.state],opacity:b.coverage===0?.25:1}),
  b.coverage>0&&b.coverage<.99&&h('path',{d:`M${i*4} 19 L${i*4+3.3} 0 L${i*4+3.3} 19 Z`,fill:'#0d111a',opacity:.65}))));
}
function OfficialBars({timeline}) {
 if(!timeline)return null;
 const count=Math.max(1,timeline.buckets.length);
 return h('div',null,h('div',{style:{...muted,marginTop:12}},`Official incidents · ${timeline.event_count} published record(s)`),
  h('svg',{viewBox:`0 0 ${count*4} 13`,preserveAspectRatio:'none',style:{display:'block',height:13,width:'100%',margin:'5px 0 9px'},role:'img','aria-label':`Official incident history: ${timeline.event_count} records; green means no reported incidents, gray means unavailable or unknown`},timeline.buckets.map((b,i)=>h('g',{key:i},
   h('title',null,`${date(b.from)} – ${date(b.to)} JST\n${b.event_count?`${b.event_count} published record(s) · ${LABELS[b.state]} · ${b.point_count} publication / unknown-duration marker(s) · ${b.unknown_impact_count} unknown severity`:b.state==='no_reported_incidents'?'No reported incidents in retrieved history (not measured uptime)':'History unavailable, stale, or outside retrieved publication window'}`),
   h('rect',{x:i*4,y:0,width:3.3,height:13,rx:.3,fill:COLORS[b.state],opacity:b.event_count||b.state==='no_reported_incidents'?1:.7}),
   b.point_count>0&&h('rect',{x:i*4+.8,y:4,width:1.7,height:5,fill:'#dbe4ef'})))));
}
function Metrics({timeline}) {
 const coverage=timeline.known_seconds>0&&timeline.coverage_percent===0?'<0.01':timeline.coverage_percent;
 return h('span',{style:muted},`Observed operational ${timeline.operational_percent===null?'—':timeline.operational_percent+'%'} · Coverage ${coverage}%`);
}
function Component({item,surface,hours,step,stale}) {
 const [open,setOpen]=useState(false);
 const q=useQuery({queryKey:[ID,'component',surface,item.id,hours,step],queryFn:()=>ctx.rest(`/component?surface=${encodeURIComponent(surface)}&component=${encodeURIComponent(item.id)}&hours=${hours}&step=${step}`),enabled:open,refetchInterval:open?30000:false});
 return h('div',{style:{padding:'8px 0',borderTop:'1px solid #202a37'}},
  h('button',{onClick:()=>setOpen(!open),'aria-expanded':open,style:{display:'flex',justifyContent:'space-between',width:'100%',gap:10,textAlign:'left',background:'none',border:0,color:'#c9d4e2',cursor:'pointer',fontSize:12}},h('span',null,open?'− ':'+ ',item.name,h('small',{style:{...muted,marginLeft:8}},item.id)),h(Status,{value:item.state,stale})),
  open&&h('div',null,q.data?h('div',null,h(OfficialBars,{timeline:q.data.official_timeline}),h('span',{style:muted},'Local observations'),h(Bars,{timeline:q.data}),h(Metrics,{timeline:q.data})):h('p',{style:muted},q.error?'Component history unavailable':'Loading component history…')));
}
function Event({event}) {
 return h('details',{style:{padding:'8px 0',borderTop:'1px solid #202a37'}},
  h('summary',{style:{cursor:'pointer',fontSize:12,color:'#c9d4e2'}},event.title,' · ',event.state||'feed update',' · ',date(event.started_at||event.scheduled_for||event.display_at)),
  h('div',{style:{padding:'8px 14px',...muted}},
   h('a',{href:event.url,target:'_blank',rel:'noopener noreferrer',style:{color:'#8ac6ef'}},'Official incident'),
   (event.updates||[{body:event.body}]).map((u,i)=>h('div',{key:u.id||i,style:{marginTop:10,whiteSpace:'pre-wrap',overflowWrap:'anywhere'}},h('strong',null,date(u.display_at),' ',u.state||''),h('div',null,u.body)))));
}
function Surface({row,hours,step}) {
 const [expanded,setExpanded]=useState(false);
 const s=row.source;
 const incidents=row.events.items.filter(e=>e.kind==='incident'&&!['resolved','postmortem'].includes(e.state));
 return h('section',{style:{borderTop:'1px solid #2a3544',padding:'18px 0'}},
  h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,flexWrap:'wrap'}},
   h('button',{onClick:()=>setExpanded(!expanded),'aria-expanded':expanded,style:{border:0,padding:0,background:'none',color:'#e7eef7',fontSize:15,fontWeight:600,cursor:'pointer'}},expanded?'− ':'+ ',row.display_name),
   h('div',{style:{display:'flex',gap:18,alignItems:'center'}},h(Status,{value:row.overall.state,stale:s.stale&&Boolean(s.fetched_at)}),h('a',{href:s.status_page_url,target:'_blank',rel:'noopener noreferrer',style:{fontSize:12,color:'#9eafc4'}},'Official source ↗'))),
  h(OfficialBars,{timeline:row.official_timeline}),
  h('span',{style:muted},'Local observations'),h(Bars,{timeline:row.timeline}),
  h('div',{style:{display:'flex',justifyContent:'space-between',gap:12,flexWrap:'wrap'}},h(Metrics,{timeline:row.timeline}),h('span',{style:muted},`Fetched ${date(s.fetched_at)} JST`)),
  s.error&&h('p',{role:'status',style:{...muted,margin:'8px 0 0',color:'#c1b8a6'}},`${s.error.kind}: ${s.error.message}`),
  s.stale&&!s.error&&h('p',{role:'status',style:muted},'Stale / no recent validated observation. Refresh is scheduled.'),
  s.history_error&&h('p',{role:'status',style:muted},`Official history sync failed: ${s.history_error.kind}. Stored records may be incomplete.`),
  incidents.length>0&&h('p',{style:{fontSize:12,color:'#e7ca5b'}},`${incidents.length} published active incident(s) · ${incidents[0].title}`),
  expanded&&h('div',{style:{marginTop:14,padding:'0 0 0 14px',borderLeft:'2px solid #293747'}},
   h('p',{style:muted},row.overall.summary),
   row.warnings.map((w,i)=>h('p',{key:i,style:muted},w)),
   h('p',{style:muted},`Provider updated: ${date(s.source_updated_at)} JST · Next poll: ${date(s.next_poll_at)} JST`),
   h('p',{style:muted},`History sync: ${Object.entries(s.history_sync||{}).map(([k,v])=>`${k} ${date(v.fetched_at)} JST`).join(' · ')||'Not completed / unsupported'}`),
   h('h3',{style:{fontSize:12,color:'#aebbd0',margin:'16px 0 8px'}},'Components · expand for official and local history'),
   row.components.length?row.components.filter(c=>c.showcase!==false).map(c=>h(Component,{key:c.id,item:c,surface:row.surface_id,hours,step,stale:s.stale})):h('p',{style:muted},'No supported component inventory.'),
   h('h3',{style:{fontSize:12,color:'#aebbd0',margin:'18px 0 8px'}},`Official incident history (${row.events.total})`),
   h('p',{style:muted},row.events.completeness),
   row.events.items.map(e=>h(Event,{key:e.kind+e.id,event:e})),
   row.events.truncated&&h('p',{style:muted},'Showing the latest 200 events in this range. Narrow the range to inspect more.'),
   s.history_error&&h('p',{style:muted},`History source error: ${s.history_error.kind}`)));
}
export function BoardView() {
 const [hours,setHours]=useState(24),[step,setStep]=useState(1800),[message,setMessage]=useState(''),[refreshing,setRefreshing]=useState(false);
 const query=useQuery({queryKey:[ID,'board',hours,step],queryFn:()=>ctx.rest(`/board?hours=${hours}&step=${step}`,{timeoutMs:15000}),refetchInterval:15000,staleTime:5000,retry:1});
 async function refresh(){setRefreshing(true);try{const r=await ctx.rest('/refresh',{method:'POST'});setMessage(r.scheduled.length?`Refresh requested for ${r.scheduled.length} sources.`:'Sources are current or waiting for their polling / retry interval.');await query.refetch();}catch{setMessage('Refresh failed. The backend may be unavailable.');}finally{setRefreshing(false);}}
 return h('main',{style:{height:'100%',overflowY:'auto',background:'#10151d',color:'#dbe4ef',padding:'24px 30px',fontFamily:'system-ui, sans-serif'}},
  h('div',{style:{maxWidth:1200,margin:'0 auto'}},
   h('header',null,h('div',{style:{fontSize:10,letterSpacing:2,color:'#8191a8'}},'OFFICIAL SERVICE HEALTH'),h('h1',{style:{fontSize:25,fontWeight:600,margin:'8px 0'}},'AI Status Board'),h('p',{style:muted},'6 providers · 8 surfaces · local observations kept for 180 days')),
   h('div',{style:{display:'flex',gap:12,alignItems:'center',flexWrap:'wrap',margin:'22px 0'}},
    h('label',{style:muted},'Range ',h('select',{'aria-label':'Time range',value:hours,style:control,onChange:e=>{const v=Number(e.target.value);setHours(v);if(v*3600/step>600)setStep(allowedSteps(v)[0][0]);}},RANGES.map(([v,l])=>h('option',{key:v,value:v},l)))),
    h('label',{style:muted},'Interval ',h('select',{'aria-label':'Time interval',value:step,style:control,onChange:e=>setStep(Number(e.target.value))},allowedSteps(hours).map(([v,l])=>h('option',{key:v,value:v},l)))),
    h('button',{onClick:refresh,disabled:refreshing,style:{...control,cursor:'pointer'}},refreshing?'Refreshing…':'Refresh'),h('span',{style:muted},'Auto refresh · JST')),
   h('p',{style:{...muted,lineHeight:1.6}},'Official incidents include published events from while Hermes was off, synchronized after startup or resume. Green means no reported incidents in retrieved history, not measured uptime. Gray means unavailable / outside the retrieved window or unknown severity; a white mark means a publication or unknown-duration event. Local observations and their operational % remain separate; gray / striped gaps are unobserved time.'),
   h('div',{style:{display:'flex',gap:14,flexWrap:'wrap',marginBottom:16}},Object.keys(COLORS).filter(k=>k!=='informational').map(k=>h('span',{key:k,style:{fontSize:10,color:COLORS[k]}},'● ',LABELS[k]))),
   query.data&&h('div',{style:{display:'flex',justifyContent:'space-between',...muted,marginBottom:12}},h('span',null,date(query.data.surfaces[0].timeline.buckets[0]?.from),' JST'),h('span',null,date(query.data.generated_at),' JST')),
   h('div',{'aria-live':'polite',style:muted},message,query.data?.polling.length?' · Polling in progress':''),
   query.error&&h('p',{role:'alert',style:{color:'#ed9868'}},'Board backend unavailable. Displayed data may be outdated.'),
   query.data?.backend_error&&h('p',{role:'alert',style:{color:'#ed9868'}},query.data.backend_error),
   !query.data?h('p',{style:muted},'Loading status history…'):query.data.surfaces.map(row=>h(Surface,{key:row.surface_id,row,hours,step})),
   h('footer',{style:{...muted,borderTop:'1px solid #2a3544',padding:'18px 0'}},'Official public sources · No credentials · Offline incidents are recovered within each source’s published history window. AI Studio history is unsupported. Collection runs while Hermes backend is running.')));
}
export default {id:ID,name:'AI Status Board',defaultEnabled:true,register(context){ctx=context;ctx.registerMany([{id:'page',area:ROUTES_AREA,data:{path:'/ai-status-board'},render:()=>h(BoardView)},{id:'nav',area:SIDEBAR_NAV_AREA,order:82,data:{path:'/ai-status-board',label:'AI Status Board',codicon:'pulse'}}]);}};
