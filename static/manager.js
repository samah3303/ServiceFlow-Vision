const managerState = { project: null, results: null, runs: [], tasks: [], layoutImage: null };
const $ = selector => document.querySelector(selector);

function escapeHTML(value) { return String(value ?? "").replace(/[&<>'"]/g, character => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[character]); }
async function api(path, options = {}) { const response = await fetch(path, options); const value = await response.json().catch(() => ({})); if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`); return value; }
function toast(message) { const element = $("#managerToast"); element.textContent = message; element.className = "manager-toast show"; clearTimeout(element._timer); element._timer = setTimeout(() => element.className = "manager-toast", 2800); }
function formatDuration(seconds) { seconds = Number(seconds || 0); if (seconds >= 3600) return `${Math.floor(seconds/3600)}h ${Math.round((seconds%3600)/60)}m`; if (seconds >= 60) return `${Math.round(seconds/60)} min`; return `${Math.round(seconds)} sec`; }
function formatClock(seconds) { seconds = Math.max(0,Number(seconds)||0); return `${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(Math.floor(seconds%60)).padStart(2,"0")}`; }

function identityLabel(identity) {
  if (identity?.startsWith("staff:")) return managerState.project.staff.find(person => person.id === identity.split(":")[1])?.name || "Unmatched staff";
  return identity?.replace("customer:","Customer ") || "Unknown";
}

function taskOwner(task) {
  if (task.assignedTo) return managerState.project.staff.find(person => person.id === task.assignedTo)?.name || "Assigned";
  return task.targetRole ? `Any ${task.targetRole}` : "Unassigned";
}

function relevantTasks() {
  const runId = managerState.results?.runId;
  return managerState.tasks.filter(task => task.source === "manual" || !runId || task.sourceRunId === runId);
}

function taskStats() {
  const tasks = relevantTasks().filter(task => task.status !== "superseded");
  const open = tasks.filter(task => ["open", "accepted"].includes(task.status));
  const completed = tasks.filter(task => task.status === "completed");
  const dismissed = tasks.filter(task => task.status === "dismissed");
  const responded = tasks.filter(task => task.history?.some(item => item.action === "accept" || item.action === "complete"));
  return { tasks, open, completed, dismissed, acceptancePercent: tasks.length ? Math.round(responded.length / tasks.length * 100) : 0 };
}

function loadLayoutImage() {
  const source = managerState.project?.restaurant?.layoutImage;
  if (!source) { managerState.layoutImage = null; drawFloor(); return; }
  const image = new Image(); image.onload = () => { managerState.layoutImage = image; drawFloor(); }; image.src = source;
}

function averageFirstService(results) {
  const values = results.sessions.filter(session => session.firstServiceSeconds != null).map(session => session.firstServiceSeconds);
  return values.length ? values.reduce((sum,value)=>sum+value,0)/values.length : null;
}

function buildAlerts() {
  const settings = managerState.project.managerSettings;
  const alerts = [];
  managerState.results.sessions.forEach(session => {
    if (session.firstServiceSeconds == null) alerts.push({ severity:"critical", symbol:"!", title:`Table ${session.tableNumber} had no detected staff visit`, detail:`Session began at ${formatClock(session.start)} and lasted ${formatDuration(session.durationSeconds)}.`, time:session.start });
    else if (session.firstServiceSeconds > settings.greetingTargetSeconds) alerts.push({ severity:"critical", symbol:"!", title:`Table ${session.tableNumber} exceeded the first-service target`, detail:`${Math.round(session.firstServiceSeconds)} sec versus the ${settings.greetingTargetSeconds} sec target.`, time:session.start + session.firstServiceSeconds });
    if (session.durationSeconds > settings.longSessionMinutes * 60) alerts.push({ severity:"warn", symbol:"↗", title:`Table ${session.tableNumber} had a long session`, detail:`${formatDuration(session.durationSeconds)} versus the ${settings.longSessionMinutes} min review threshold.`, time:session.start });
    if (session.staffVisits < settings.minimumVisitsPerSession) alerts.push({ severity:"warn", symbol:"–", title:`Table ${session.tableNumber} had limited staff presence`, detail:`${session.staffVisits} visits detected; target is at least ${settings.minimumVisitsPerSession}.`, time:session.start });
  });
  managerState.project.cameras.forEach(camera => {
    const quality = camera.calibrationQuality;
    if (!quality?.ready || quality.score < 60) alerts.push({ severity:"warn", symbol:"⌖", title:`${camera.name} needs calibration review`, detail:quality?.ready ? `${quality.label} calibration quality (${quality.score}/100). Add points spread across the visible floor.` : `${quality?.points || 0} matched points; at least four are required.`, time:0 });
  });
  if ((managerState.results.summary.mappedObservationPercent || 0) < 90) alerts.push({ severity:"warn", symbol:"?", title:"Some detections were not mapped to the floor plan", detail:`${managerState.results.summary.mappedObservationPercent || 0}% of observations were mapped. Review camera calibration.`, time:0 });
  return alerts;
}

function render() {
  const results = managerState.results;
  $("#managerEmpty").hidden = !!results; $("#managerContent").hidden = !results; $("#managerExportButton").disabled = !results;
  const hour = new Date().getHours(); $("#managerGreeting").textContent = `${hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"}. Here’s the shift.`;
  if (!results) { $("#managerSubtitle").textContent = "Waiting for an analyzed recording."; return; }
  const generated = new Date(results.sourceGeneratedAt || results.generatedAt);
  $("#managerSubtitle").textContent = `${managerState.project.restaurant.name} · analyzed ${generated.toLocaleString()}`;
  const summary = results.summary;
  const greet = averageFirstService(results);
  const coordination = taskStats();
  const metrics = [
    ["Guests tracked",summary.customers,"Anonymous temporary identities"],
    ["Table turns",summary.tableSessions,`${summary.occupiedMinutes || 0} occupied table-minutes`],
    ["Average first service",greet == null ? "—" : `${Math.round(greet)}s`,greet == null ? "Not observed" : `${managerState.project.managerSettings.greetingTargetSeconds}s operating target`],
    ["Service coverage",`${summary.serviceCoveragePercent || 0}%`,"Sessions with staff presence"],
    ["Staff visits",summary.staffVisits || 0,`${summary.staffDetected || 0} staff profiles detected`],
    ["Open actions",coordination.open.length,`${coordination.completed.length} completed · ${coordination.dismissed.length} dismissed`],
  ];
  $("#managerMetrics").innerHTML = metrics.map((item,index)=>`<article class="pulse-card ${index===3?"accent":""}"><span>${item[0]}</span><strong>${item[1]}</strong><small>${item[2]}</small></article>`).join("");
  const quality = summary.mappedObservationPercent >= 90 && summary.averageDetectionConfidence >= .6;
  $("#qualityChip").className = `quality-chip ${quality ? "good" : "warn"}`; $("#qualityChip").innerHTML = `<span></span>${quality ? "Data quality looks healthy" : "Validation recommended"}`;
  renderTasks(); renderReplay(); renderAlerts(); renderStaff(); renderTables(); drawFloor(); drawOccupancy(); renderConfidence();
}

function renderTasks() {
  const { open } = taskStats();
  const priority = { critical: 0, high: 1, normal: 2 };
  const rows = [...open].sort((a,b)=>(priority[a.priority]??3)-(priority[b.priority]??3)||new Date(a.createdAt)-new Date(b.createdAt));
  $("#managerTasks").innerHTML = rows.length ? rows.slice(0,10).map(task=>`<div class="manager-task-row ${task.priority}"><i></i><div><strong>${escapeHTML(task.title)}</strong><span>${escapeHTML(task.requestedAction)}${task.tableNumber ? ` · Table ${escapeHTML(task.tableNumber)}` : ""}</span></div><span class="task-owner">${escapeHTML(taskOwner(task))}</span></div>`).join("") : `<div class="queue-empty">No unresolved operational actions.</div>`;
}

function renderReplay() {
  const tasks = relevantTasks().filter(task => task.status !== "superseded");
  const rank = task => ({ critical: 0, high: 1, normal: 2 }[task.priority] ?? 3) + (task.status === "completed" ? .4 : 0);
  const moments = [...tasks].sort((a,b)=>rank(a)-rank(b)||new Date(a.createdAt)-new Date(b.createdAt)).slice(0,5);
  $("#managerReplay").innerHTML = moments.length ? moments.map((task,index)=>`<div class="replay-row"><span class="replay-index">${index+1}</span><div class="replay-copy"><strong>${escapeHTML(task.title)}</strong><span>${escapeHTML(task.status)} · ${escapeHTML(taskOwner(task))} · ${escapeHTML(task.detail||task.requestedAction)}</span></div>${task.eventTimeSeconds == null ? "" : `<a href="/?view=review&time=${task.eventTimeSeconds}">Evidence</a>`}</div>`).join("") : `<div class="queue-empty">Action outcomes will form the shift replay.</div>`;
}

function renderAlerts() {
  const alerts = buildAlerts(); $("#alertCount").textContent = `${alerts.length} item${alerts.length===1?"":"s"}`;
  $("#managerAlerts").innerHTML = alerts.length ? alerts.slice(0,12).map(alert=>`<div class="alert-row ${alert.severity}"><div class="alert-symbol">${alert.symbol}</div><div class="alert-copy"><strong>${escapeHTML(alert.title)}</strong><span>${escapeHTML(alert.detail)}</span></div><a href="/?view=review&time=${alert.time}">Review</a></div>`).join("") : `<div class="all-clear"><strong>No target exceptions detected</strong>Review the validation confidence before treating this as a final shift record.</div>`;
}

function renderStaff() {
  const staff = managerState.results.staff; const maxVisits = Math.max(1,...staff.map(person=>person.tableVisits));
  $("#managerStaff").innerHTML = staff.length ? staff.map(person=>{const profile=managerState.project.staff.find(item=>`staff:${item.id}`===person.id);return `<div class="staff-row"><div><strong>${escapeHTML(person.name)}</strong><span>${escapeHTML(profile?.role || "waiter")} · ${person.tableVisits} table visits · ${person.firstContacts} first contacts · ${person.interactionMinutes} min near occupied tables</span></div><div class="staff-number">${person.tableVisits}</div><div class="staff-track"><div style="width:${person.tableVisits/maxVisits*100}%"></div></div></div>`;}).join("") : `<div class="staff-empty">No enrolled staff were matched near occupied tables in this analysis.</div>`;
}

function renderTables() {
  const target = managerState.project.managerSettings.greetingTargetSeconds; $("#tableCountManager").textContent = `${managerState.results.tables.length} mapped`;
  $("#managerTables").innerHTML = managerState.results.tables.length ? managerState.results.tables.map(table=>{const reset=[...relevantTasks()].reverse().find(task=>task.kind==="reset"&&task.tableId===table.tableId);const evidenceState=!table.turns?"Available":reset?.status==="completed"?"Ready":reset&&["open","accepted"].includes(reset.status)?"Reset check":"Session ended";return `<tr><td>${escapeHTML(table.tableNumber)}</td><td><span class="evidence-state">${evidenceState}</span></td><td>${table.turns}</td><td>${table.occupiedMinutes} min</td><td class="${table.averageFirstServiceSeconds > target || table.averageFirstServiceSeconds == null && table.turns ? "target-miss":""}">${table.averageFirstServiceSeconds == null ? "—" : `${table.averageFirstServiceSeconds}s`}</td><td>${table.staffVisits}</td></tr>`;}).join("") : `<tr><td colspan="6">No tables mapped.</td></tr>`;
}

function drawFloor() {
  const canvas = $("#managerFloorCanvas"); if (!canvas || !managerState.project) return; const ctx=canvas.getContext("2d"),w=canvas.width,h=canvas.height; ctx.clearRect(0,0,w,h); ctx.fillStyle="#eef1eb";ctx.fillRect(0,0,w,h);
  if (managerState.layoutImage) { ctx.globalAlpha=.76;ctx.drawImage(managerState.layoutImage,0,0,w,h);ctx.globalAlpha=1; } else { ctx.strokeStyle="#e0e5de"; for(let x=0;x<w;x+=50){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();} for(let y=0;y<h;y+=50){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();} }
  const zoneStyles={seat:["rgba(47,128,87,.08)","#4d936c"],handoff:["rgba(111,100,167,.13)","#6f64a7"],entrance:["rgba(64,141,169,.12)","#408da9"],waiting:["rgba(228,170,66,.12)","#b7832d"],staff:["rgba(197,87,73,.10)","#b95d51"]};
  (managerState.project.zones||[]).forEach(zone=>{const [fill,stroke]=zoneStyles[zone.type]||zoneStyles.handoff;const x=zone.x*w,y=zone.y*h,width=zone.w*w,height=zone.h*h;ctx.fillStyle=fill;ctx.strokeStyle=stroke;ctx.lineWidth=1.5;ctx.setLineDash([8,6]);ctx.fillRect(x,y,width,height);ctx.strokeRect(x,y,width,height);ctx.setLineDash([]);if(width>80&&height>28){ctx.fillStyle=stroke;ctx.textAlign="left";ctx.textBaseline="top";ctx.font="700 10px system-ui";ctx.fillText(zone.name||zone.type,x+8,y+7);}});
  const resultMap=new Map((managerState.results?.tables||[]).map(table=>[table.tableId,table])); const maxTurns=Math.max(1,...[...resultMap.values()].map(table=>table.turns));
  managerState.project.tables.forEach(table=>{ const result=resultMap.get(table.id)||{turns:0,averageFirstServiceSeconds:null}; const alpha=.2+.65*(result.turns/maxTurns); const x=table.x*w,y=table.y*h,width=table.w*w,height=table.h*h; ctx.fillStyle=`rgba(57,153,91,${alpha})`;ctx.strokeStyle="#153c2c";ctx.lineWidth=2;ctx.beginPath();ctx.roundRect?ctx.roundRect(x,y,width,height,Math.min(15,width/5,height/5)):ctx.rect(x,y,width,height);ctx.fill();ctx.stroke();ctx.fillStyle="#123025";ctx.textAlign="center";ctx.textBaseline="middle";ctx.font="800 16px system-ui";ctx.fillText(String(table.number),x+width/2,y+height/2-5);ctx.font="600 10px system-ui";ctx.fillText(`${result.turns} turn${result.turns===1?"":"s"}`,x+width/2,y+height/2+14); });
}

function drawOccupancy() {
  const canvas=$("#occupancyChart"),ctx=canvas.getContext("2d"),w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h); const sessions=managerState.results.sessions||[]; if(!sessions.length){ctx.fillStyle="#778078";ctx.font="13px system-ui";ctx.textAlign="center";ctx.fillText("No occupied table sessions detected",w/2,h/2);return;} const end=Math.max(...sessions.map(item=>item.end),1),bins=32,values=Array(bins).fill(0);sessions.forEach(session=>{for(let i=0;i<bins;i++){const t=i/bins*end;if(t>=session.start&&t<=session.end)values[i]++;}});const max=Math.max(1,...values),left=55,right=25,top=22,bottom=38,chartW=w-left-right,chartH=h-top-bottom;ctx.strokeStyle="#dce3dc";ctx.beginPath();ctx.moveTo(left,top+chartH);ctx.lineTo(w-right,top+chartH);ctx.stroke(); const barW=chartW/bins;values.forEach((value,index)=>{const barH=value/max*chartH;ctx.fillStyle=value===max?"#ff7a5e":"#2f8057";ctx.fillRect(left+index*barW+2,top+chartH-barH,Math.max(2,barW-4),barH);});ctx.fillStyle="#758078";ctx.font="10px system-ui";ctx.textAlign="left";ctx.fillText("00:00",left,top+chartH+22);ctx.textAlign="right";ctx.fillText(formatClock(end),w-right,top+chartH+22);ctx.textAlign="left";ctx.fillText(`${max} table${max===1?"":"s"} peak`,left,top+5);
}

function renderConfidence() {
  const summary=managerState.results.summary, mapped=summary.mappedObservationPercent||0, confidence=Math.round((summary.averageDetectionConfidence||0)*100), corrections=(managerState.results.corrections||[]).length;
  $("#confidenceTitle").textContent = mapped>=90&&confidence>=60?"Healthy baseline":"Review recommended";
  const coordination=taskStats();
  $("#confidenceCopy").textContent = `${mapped}% of observations mapped · ${confidence}% average person-detection confidence · ${summary.crossCameraHandoffs||0} camera handoffs · ${summary.handoffsNeedingReview||0} handoffs to review · ${corrections} human corrections · ${coordination.acceptancePercent}% action acceptance.`;
}

function renderRuns() {
  const select=$("#runSelect"); select.innerHTML=`<option value="">Latest analysis</option>`+managerState.runs.map(run=>`<option value="${run.id}">${new Date(run.generatedAt).toLocaleString()} · ${run.summary?.tableSessions||0} sessions</option>`).join("");
}

async function saveTargets(event) {
  event.preventDefault(); managerState.project.managerSettings={ greetingTargetSeconds:Number($("#greetingTarget").value), longSessionMinutes:Number($("#longSession").value), minimumVisitsPerSession:Number($("#minimumVisits").value) };
  managerState.project.managerSettings.anonymousRetentionDays=Number($("#retentionDays").value);
  managerState.project=await api("/api/project",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(managerState.project)}); $("#managerSettingsDialog").close();render();toast("Manager targets saved");
}

function bindEvents() {
  $("#runSelect").addEventListener("change",async event=>{const response=await api(event.target.value?`/api/results?runId=${encodeURIComponent(event.target.value)}`:"/api/results");managerState.results=response.available?response.results:null;render();});
  $("#managerSettingsButton").addEventListener("click",()=>{const settings=managerState.project.managerSettings;$("#greetingTarget").value=settings.greetingTargetSeconds;$("#longSession").value=settings.longSessionMinutes;$("#minimumVisits").value=settings.minimumVisitsPerSession;$("#retentionDays").value=settings.anonymousRetentionDays||7;$("#managerSettingsDialog").showModal();});
  $("#managerSettingsDialog .dialog-close").addEventListener("click",()=>$("#managerSettingsDialog").close()); $("#managerSettingsForm").addEventListener("submit",saveTargets);
  $("#pruneDataButton").addEventListener("click",async()=>{const days=Number($("#retentionDays").value)||7;if(!confirm(`Delete analyzed guest journeys older than ${days} days?`))return;try{const value=await api("/api/privacy/prune",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({days})});const [runs,response,tasks]=await Promise.all([api("/api/runs"),api("/api/results"),api("/api/tasks")]);managerState.runs=runs.runs;managerState.results=response.available?response.results:null;managerState.tasks=tasks.tasks;renderRuns();render();toast(`${value.removed.length} expired file${value.removed.length===1?"":"s"} removed`);}catch(error){toast(error.message);}});
  $("#managerExportButton").addEventListener("click",()=>{if(!managerState.results)return;const report={restaurant:managerState.project.restaurant.name,generatedAt:managerState.results.generatedAt,targets:managerState.project.managerSettings,summary:managerState.results.summary,attention:buildAlerts(),actions:relevantTasks(),tables:managerState.results.tables,staff:managerState.results.staff,dataConfidence:{mappedObservationPercent:managerState.results.summary.mappedObservationPercent,averageDetectionConfidence:managerState.results.summary.averageDetectionConfidence,corrections:(managerState.results.corrections||[]).length}};const blob=new Blob([JSON.stringify(report,null,2)],{type:"application/json"});const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=`serviceflow-shift-${new Date().toISOString().slice(0,10)}.json`;link.click();URL.revokeObjectURL(link.href);});
}

async function init() {
  bindEvents();
  try { const [project,runs,response,tasks]=await Promise.all([api("/api/project"),api("/api/runs"),api("/api/results"),api("/api/tasks")]);managerState.project=project;managerState.runs=runs.runs;managerState.results=response.available?response.results:null;managerState.tasks=tasks.tasks;renderRuns();loadLayoutImage();render();setInterval(async()=>{try{managerState.tasks=(await api("/api/tasks")).tasks;if(managerState.results)render();}catch(error){}},10000); }
  catch(error){toast(error.message);}
}
init();
