'use strict';
// 横向为实际按下减预期时间，纵向与谱面音符对齐。Miss 断线，整曲播放。
const $ = id => document.getElementById(id);
const state = {library:null,data:null,selected:null,loading:0,scrollSpeed:20,span:800,start:0,end:800,cursor:0,playing:false,lastFrame:null,hover:null,xMax:40,audioActive:false,playToken:0};
const upstreamHeight=960,upstreamFps=60;
state.fill=localStorage.getItem('judgement-fill')==='true';
$('judgement-fill').checked=state.fill;
$('judgement-fill').onchange=()=>{state.fill=$('judgement-fill').checked;localStorage.setItem('judgement-fill',String(state.fill));drawAll();};
function spanForScrollSpeed(speed){return upstreamHeight*1000/(speed*upstreamFps);}
const gradeColors = {MAX:'#ffffff','300':'#ffd237','200':'#46a815','100':'#448fe2','50':'#448fe2',Miss:'#ff6262'};
const colors = {early:'#68d5e3',late:'#f4b376',muted:'#8393ac'};
const fmtTime = ms => `${Math.floor(ms/60000)}:${((ms%60000)/1000).toFixed(2).padStart(5,'0')}`;
const preciseTime = ms => `${Math.floor(ms/60000)}:${((ms%60000)/1000).toFixed(3).padStart(6,'0')}`;
const fmtOffset = n => `${n>0?'+':''}${n} ms`;
function hitColor(offset){const neutral=[232,238,246],target=offset<0?[104,213,227]:[244,179,118],mix=Math.min(1,Math.abs(offset)/40),rgb=neutral.map((value,i)=>Math.round(value+(target[i]-value)*mix));return `rgb(${rgb.join(',')})`;}
// 所选量程占轨道可用宽度的 80%；两侧连续压缩，边界斜率连续。
// 固定比例不随单曲最大偏差改变，保留原始毫秒值供悬停读取。
function offsetPosition(offset){const value=Math.abs(offset),core=.8,focus=state.focusRange||40;return Math.sign(offset)*(value<=focus?core*value/focus:core+(1-core)*(1-Math.exp(-(value-focus)/(focus*(1-core)/core))));}
function edgeCurveColor(offset){const focus=state.focusRange||40,mix=1-Math.exp(-Math.max(0,Math.abs(offset)-focus)/(focus/4)),base=[173,197,231],target=offset<0?[70,160,255]:[255,149,65];return `rgb(${base.map((v,i)=>Math.round(v+(target[i]-v)*mix)).join(',')})`;}
state.focusRange=Math.max(40,Math.min(80,Number(localStorage.getItem('offset-range'))||40));
$('offset-range').value=state.focusRange;$('offset-range-value').value=`±${state.focusRange} ms`;
$('offset-range').oninput=event=>{state.focusRange=Number(event.target.value);localStorage.setItem('offset-range',String(state.focusRange));$('offset-range-value').value=`±${state.focusRange} ms`;if(state.data){state.curveSlopes=state.lanes.map(curveTangents);state.auxSlopes=state.auxLanes.map(curveTangents);drawAll();}};
// 单调三次 Hermite 插值：经过原始节点，转折处不向外过冲；Miss 切断邻域。
function curveTangents(lane){
  return lane.map((row,i)=>{
    if(row.offset===null)return 0;
    const previous=lane[i-1],next=lane[i+1],value=offsetPosition(row.offset);
    const before=previous?.offset!=null&&row.time>previous.time,after=next?.offset!=null&&next.time>row.time;
    const h0=before?row.time-previous.time:0,h1=after?next.time-row.time:0;
    const d0=before?(value-offsetPosition(previous.offset))/h0:0,d1=after?(offsetPosition(next.offset)-value)/h1:0;
    if(!before)return d1;if(!after)return d0;
    if(d0*d1<=0)return 0;
    const w0=2*h1+h0,w1=h1+2*h0;return (w0+w1)/(w0/d0+w1/d1);
  });
}
// 按同轨预期击间隔淡化，与预览流速、缩放无关；长间隔仍保留很淡的连接。
function connectionOpacity(gap){return .08+.8*Math.exp(-Math.max(0,gap-100)/220);}
function shortestLaneGap(lane){let shortest=Infinity;for(let i=1;i<lane.length;i++){const gap=lane[i].time-lane[i-1].time;if(gap>0)shortest=Math.min(shortest,gap);}return Number.isFinite(shortest)?shortest:100;}
function connectionWidth(gap,baseGap){return Math.max(.6,Math.min(8,8*baseGap/Math.max(gap,baseGap)));}
function accuracyBins(data,count=160){
  const scores={MAX:300,'300':300,'200':200,'100':100,'50':50,Miss:0},bins=Array.from({length:count},()=>({notes:0,score:0}));
  for(const row of data.rows){const index=Math.max(0,Math.min(count-1,Math.floor(row.time/data.duration*count))),bin=bins[index];bin.notes++;bin.score+=scores[row.head_judgement||row.judgement]??0;}
  return bins.map(bin=>bin.notes?bin.score/(bin.notes*300):null);
}
function accuracyColor(accuracy){
  if(accuracy===null)return '#465a80';
  const mix=Math.max(0,Math.min(1,(1-accuracy)/.1)),channel=Math.round(255-157*mix);
  return `rgb(255,${channel},${channel})`;
}
async function request(url,options){const response=await fetch(url,options),data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
function message(text){$('message').textContent=text;$('message').hidden=!text;}
function size(canvas){const rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.font='10px Segoe UI, Microsoft YaHei';return {ctx,w:rect.width,h:rect.height};}
function lowerBound(rows,time){let l=0,r=rows.length;while(l<r){const m=(l+r)>>1;if(rows[m].time<time)l=m+1;else r=m;}return l;}
function visible(rows,pad=0){return rows.slice(Math.max(0,lowerBound(rows,state.start)-pad),lowerBound(rows,state.end)+pad);}
function renderList(){
  if(!state.library)return;const query=$('search').value.toLowerCase();$('replay-list').replaceChildren();
  if(!state.library.configured){const p=document.createElement('p');p.className='muted';p.textContent='先选择 osu! 文件夹';$('replay-list').append(p);return;}
  const entries=state.library.replays.filter(r=>`${r.title} ${r.difficulty||''} ${r.filename}`.toLowerCase().includes(query));
  if(!entries.length){const p=document.createElement('p');p.className='muted';p.textContent='没有找到回放';$('replay-list').append(p);}
  for(const replay of entries){
    const button=document.createElement('button');button.className=`replay-card ${replay.id===state.selected?'active':''}`;
    for(const [className,text] of [['name',replay.title],['difficulty',replay.difficulty||replay.error||'找不到对应版本的谱面']]){const span=document.createElement('span');span.className=className;span.textContent=text;button.append(span);}
    const meta=document.createElement('span');meta.className='meta';const left=document.createElement('span');left.textContent=replay.matched?`${replay.keys}K · ${replay.mods}`:'MD5 未匹配';if(!replay.matched)left.className='unmatched';
    const right=document.createElement('span');right.textContent=(replay.date||'').replace('T',' ').slice(0,16);meta.append(left,right);button.append(meta);button.onclick=()=>loadReplay(replay);$('replay-list').append(button);
  }
}
async function loadLibrary(refresh=false){
  $('refresh').disabled=true;message('');
  try{
    state.library=await request(refresh?'/api/refresh':'/api/replays',refresh?{method:'POST'}:undefined);
    $('library-info').textContent=state.library.configured?`${state.library.replays.length} 个回放 · ${state.library.map_count.toLocaleString()} 个谱面`:'选择 osu! 文件夹开始扫描';$('directory').textContent=state.library.osu_dir;renderList();
    if(refresh&&state.selected){const replay=state.library.replays.find(r=>r.id===state.selected);if(replay)await loadReplay(replay);else{stopPreview();state.data=null;state.selected=null;$('analysis').hidden=true;$('empty').hidden=false;}}
  }catch(error){message(error.message);}finally{$('refresh').disabled=false;}
}
async function loadReplay(replay){
  stopPreview();const token=++state.loading;state.selected=replay.id;state.data=null;state.hover=null;$('tooltip').hidden=true;renderList();$('analysis').hidden=true;$('empty').hidden=true;
  if(!replay.matched){message(replay.error||'本地没有 MD5 完全一致的谱面。请恢复对应版本后刷新，不按歌曲名替代。');return;}
  if(!replay.supported){message('第一版仅支持原生 osu!mania 回放与谱面。');return;}message('正在解析回放并重建判定…');
  try{
    const data=await request(`/api/analyze?id=${encodeURIComponent(replay.id)}`);if(token!==state.loading)return;state.data=data;
    $('song-cover').hidden=!data.cover?.available;
    if(data.cover?.available)$('song-cover').src=data.cover.url;else $('song-cover').removeAttribute('src');
    state.lanes=Array.from({length:data.keys},(_,i)=>data.rows.filter(row=>row.column===i));
    state.curveSlopes=state.lanes.map(curveTangents);
    state.auxLanes=Array.from({length:data.keys},(_,i)=>(data.auxiliary_curve||[]).filter(row=>row.column===i));
    state.auxSlopes=state.auxLanes.map(curveTangents);
    state.laneBaseGaps=state.lanes.map(shortestLaneGap);
    state.bins=new Array(160).fill(0);let max=20;
    state.binAccuracy=accuracyBins(data,state.bins.length);
    for(const row of data.rows){state.bins[Math.min(159,Math.floor(row.time/data.duration*160))]++;if(row.offset!==null)max=Math.max(max,Math.abs(row.offset));}
    state.xMax=Math.ceil(max/20)*20;
    $('song-title').textContent=replay.title;$('song-detail').textContent=`${replay.artist} / ${replay.difficulty} · ${data.keys}K · ${replay.mods}`;
    $('warnings').replaceChildren();for(const text of data.warnings){const div=document.createElement('div');div.className='warning';div.textContent=text;$('warnings').append(div);}
    renderDiagnostics(data,replay);configureMusic(data);$('export-start').value=0;$('export-end').value=(data.duration/1000).toFixed(3);$('analysis').hidden=false;message('');seek(Math.max(0,(data.rows[0]?.time||0)-200));
  }catch(error){if(token===state.loading)message(error.message);}
}
function renderDiagnostics(data,replay){
  $('diagnostic-summary').textContent=`判定核对 · ${data.counts_match?'总数一致':'总数存在差异'}`;
  const table=document.createElement('table');table.innerHTML='<thead><tr><th>来源</th><th>MAX</th><th>300</th><th>200</th><th>100</th><th>50</th><th>Miss</th></tr></thead>';
  for(const [title,counts] of [['回放记录',data.official_counts],['重建结果',data.reconstructed_counts]]){const row=document.createElement('tr'),label=document.createElement('td');label.textContent=title;row.append(label);counts.forEach((value,i)=>{const td=document.createElement('td');td.textContent=value.toLocaleString();if(data.official_counts[i]!==data.reconstructed_counts[i])td.className='count-diff';row.append(td);});table.append(row);}
  $('counts-table').replaceChildren(table);$('diagnostic-detail').textContent=`${data.action_count.toLocaleString()} 个按键动作；${data.unmatched_actions.toLocaleString()} 个无归属动作仅显示灰线与叉号，不估算归属、不接入曲线。辅助虚线仅包含有判定归属的失败点击，相对主曲线偏离 40 ms 及以上为红色。MD5: ${replay.md5}。圆点横向偏差 = 实际按下 − 预期时间；短横线纵向表示实际按下时刻。色带使用上游整数判定窗；stable 的取整边界存在约 0.5 ms 差异。长条仅展示首次有效头部按下。`;
}
function updateWindow(){
  if(!state.data)return;
  const g=trackGeometry();
  state.start=Math.max(0,state.cursor-(g.bottom-g.hitY)/g.scale);
  state.end=Math.min(state.data.duration,state.cursor+(g.hitY-g.top)/g.scale);
}
function seek(time,pause=true){if(!state.data)return;if(pause)stopPreview();state.cursor=Math.max(0,Math.min(state.data.duration,time));syncMusicPosition();state.hover=null;$('tooltip').hidden=true;updateWindow();drawAll();}
function drawAll(){if(!state.data)return;drawOverview();drawPreview();}
function drawOverview(){
  const {ctx,w,h}=size($('overview')),left=2,right=w-2,pw=right-left,total=state.data.duration,bins=state.bins,max=Math.max(1,...bins);ctx.fillStyle='#465a80';
  bins.forEach((value,i)=>{const height=(h-6)*value/max;ctx.fillStyle=accuracyColor(state.binAccuracy[i]);ctx.fillRect(left+i*pw/bins.length,h-3-height,Math.max(1,pw/bins.length-1),height);});
  const a=left+state.start/total*pw,b=left+state.end/total*pw;ctx.fillStyle='#9cabff30';ctx.fillRect(a,1,Math.max(3,b-a),h-2);ctx.strokeStyle='#bac3ff';ctx.strokeRect(a,1,Math.max(3,b-a),h-2);ctx.fillStyle='#cbd4ff';ctx.fillRect(left+state.cursor/total*pw-1,1,2,h-2);
}
function trackGeometry(exportWidth,exportHeight,span=state.span,keys=state.data.keys){const rect=$('note-preview').getBoundingClientRect(),w=exportWidth||rect.width,h=exportHeight||rect.height,left=12,width=w-24,cell=width/keys,top=39,bottom=h-29,hitY=bottom-8;return {w,h,left,width,cell,top,bottom,hitY,scale:(bottom-top)/span};}
function drawPreview(canvas=null,view=state){
  if(!view.data)return;const ctx=canvas?canvas.getContext('2d'):size($('note-preview')).ctx,g=trackGeometry(canvas?.width,canvas?.height,view.span,view.data.keys),{w,h,left,width,cell,top,hitY,scale}=g,y=time=>hitY-(time-view.cursor)*scale;
  ctx.fillStyle='#000';ctx.fillRect(0,0,w,h);if(canvas)ctx.font='10px Segoe UI, Microsoft YaHei';
  for(let i=0;i<view.data.keys;i++){
    const center=left+(i+.5)*cell;ctx.fillStyle='#ffffff04';ctx.fillRect(center-2,top,4,h-top);ctx.strokeStyle='#5a6b8566';ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(center,top);ctx.lineTo(center,h-29);ctx.stroke();ctx.setLineDash([]);
    ctx.strokeStyle='#34415a';ctx.beginPath();ctx.moveTo(left+i*cell,top);ctx.lineTo(left+i*cell,h-28);ctx.stroke();ctx.textAlign='center';ctx.font='10px Segoe UI';ctx.fillStyle='#cbd5e9';ctx.fillText(`第 ${i+1} 列`,center,13);
    const focusExtent=(cell/2-9)*.8;
    ctx.strokeStyle='#5a6b8538';ctx.setLineDash([2,6]);
    for(const sign of [-1,1]){ctx.beginPath();ctx.moveTo(center+sign*focusExtent,top);ctx.lineTo(center+sign*focusExtent,h-29);ctx.stroke();}ctx.setLineDash([]);
    ctx.font='9px Segoe UI';ctx.fillStyle=colors.early;ctx.fillText(`−${state.focusRange}`,center-focusExtent,29);ctx.fillStyle='#9faec5';ctx.fillText('0',center,29);ctx.fillStyle=colors.late;ctx.fillText(`+${state.focusRange}`,center+focusExtent,29);ctx.fillStyle='#98a7bf';ctx.fillText(`${i+1}`,center,h-9);
  }
  ctx.strokeStyle='#34415a';ctx.beginPath();ctx.moveTo(left+width,top);ctx.lineTo(left+width,h-28);ctx.stroke();ctx.save();ctx.beginPath();ctx.rect(left,top,width,h-top-29);ctx.clip();
  // 填色保留预期判定时间窗；外轮廓表示该音符实际头部判定。
  // 判定窗为硬边界：先画所有 300 窗，再画 MAX 窗；不显示绿色 200。
  const [maxWindow,window300]=view.data.windows;
  for(const row of view.data.rows){
    if(row.ln&&row.time<=view.end&&row.time+row.duration>=view.start){
      ctx.fillStyle='#91a9ca25';ctx.fillRect(left+row.column*cell+5,y(row.time+row.duration),cell-10,y(row.time)-y(row.time+row.duration));
    }
  }
  for(const [bound,color] of [[window300,'#716027'],[maxWindow,'#b8bcc5']]){
    if(!view.fill)continue;
    ctx.fillStyle=color;
    for(const row of view.data.rows){
      if(row.time-bound>view.end||row.time+bound<view.start)continue;
      const upper=y(row.time+bound),lower=y(row.time-bound);
      ctx.fillRect(left+row.column*cell+5,upper,cell-10,lower-upper);
    }
  }
  // 音符预期时刻用细白线保留，密集判定窗重叠时仍能辨认每个音符。
  for(const row of view.data.rows){
    if(row.time>view.end||row.time<view.start)continue;
    ctx.fillStyle='#ffffffaa';ctx.fillRect(left+row.column*cell+5,y(row.time)-1,cell-10,2);
  }
  for(const row of view.data.rows){
    if(row.time-window300>view.end||row.time+window300<view.start)continue;
    const x=left+row.column*cell+5,upper=y(row.time+window300),lower=y(row.time-window300);
    const grade=row.head_judgement||row.judgement;
    ctx.strokeStyle=gradeColors[grade]||gradeColors.Miss;
    ctx.lineWidth=1.5;ctx.strokeRect(x,upper,cell-10,lower-upper);
    ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(x,upper);ctx.lineTo(x,lower);ctx.moveTo(x+cell-10,upper);ctx.lineTo(x+cell-10,lower);ctx.stroke();
  }
  // 折线节点横向左早右晚；实际打击短横线固定在中线，纵向采用实际按下时间。
  for(let i=0;i<view.data.keys;i++){
    const center=left+(i+.5)*cell,extent=cell/2-9,x=offset=>center+offsetPosition(offset)*extent,lane=view.lanes[i];
    const begin=Math.max(0,lowerBound(lane,view.start-view.xMax)-1),end=Math.min(lane.length,lowerBound(lane,view.end+view.xMax)+1);
    // 先画连接，再画所有标记，避免粗连线遮住上一节点和打击线。
    ctx.save();ctx.lineCap='round';
    for(let k=Math.max(1,begin);k<end;k++){
      const previous=lane[k-1],row=lane[k];if(previous.offset===null||row.offset===null)continue;
      const gap=row.time-previous.time,startX=x(previous.offset),startY=y(previous.time),px=x(row.offset),py=y(row.time),slopes=view.curveSlopes[i];
      const gradient=ctx.createLinearGradient(startX,startY,px,py);gradient.addColorStop(0,edgeCurveColor(previous.offset));gradient.addColorStop(1,edgeCurveColor(row.offset));
      ctx.strokeStyle=gradient;ctx.globalAlpha=connectionOpacity(gap);ctx.lineWidth=connectionWidth(gap,view.laneBaseGaps[i]);ctx.beginPath();ctx.moveTo(startX,startY);
      if(gap>0)ctx.bezierCurveTo(startX+extent*slopes[k-1]*gap/3,startY+(py-startY)/3,px-extent*slopes[k]*gap/3,py-(py-startY)/3,px,py);
      else ctx.lineTo(px,py);
      ctx.stroke();
    }
    ctx.restore();
    const aux=view.auxLanes[i],auxSlopes=view.auxSlopes[i];
    ctx.save();ctx.setLineDash([4,3]);ctx.lineWidth=1.5;
    for(let k=Math.max(1,lowerBound(aux,view.start)-1);k<Math.min(aux.length,lowerBound(aux,view.end)+1);k++){
      const a=aux[k-1],b=aux[k];if(!a.unmatched&&!b.unmatched)continue;
      const gap=b.time-a.time,ay=y(a.time),by=y(b.time),ax=x(a.offset),bx=x(b.offset);
      const color=row=>{const mix=Math.min(1,(row.discrepancy??40)/40);return `rgb(255,${Math.round(255-157*mix)},${Math.round(255-157*mix)})`;};
      const gradient=ctx.createLinearGradient(ax,ay,bx===ax&&by===ay?bx+1:bx,by);gradient.addColorStop(0,color(a));gradient.addColorStop(1,color(b));ctx.strokeStyle=gradient;ctx.globalAlpha=connectionOpacity(gap);
      ctx.beginPath();ctx.moveTo(ax,ay);if(gap>0)ctx.bezierCurveTo(ax+extent*auxSlopes[k-1]*gap/3,ay+(by-ay)/3,bx-extent*auxSlopes[k]*gap/3,by-(by-ay)/3,bx,by);else ctx.lineTo(bx,by);ctx.stroke();
    }
    ctx.restore();
    for(const row of aux){if(!row.unmatched||row.time<view.start||row.time>view.end)continue;const mix=Math.min(1,(row.discrepancy??40)/40);ctx.strokeStyle=`rgb(255,${Math.round(255-157*mix)},${Math.round(255-157*mix)})`;ctx.lineWidth=1.2;ctx.beginPath();ctx.arc(x(row.offset),y(row.time),2.5,0,Math.PI*2);ctx.stroke();}
    for(let k=begin;k<Math.min(end,lane.length);k++){
      const row=lane[k];
      const py=y(row.time);
      if(row.offset===null){ctx.strokeStyle=gradeColors.Miss;ctx.lineWidth=1.7;ctx.beginPath();ctx.moveTo(center-4,py-4);ctx.lineTo(center+4,py+4);ctx.moveTo(center+4,py-4);ctx.lineTo(center-4,py+4);ctx.stroke();continue;}
      const px=x(row.offset);
      // 小空心圈是偏差线节点；光晕只作用于圈，不影响短横线与连线。
      ctx.save();ctx.strokeStyle='rgba(255,255,255,.65)';ctx.lineWidth=1.1;ctx.shadowColor='rgba(255,255,255,.45)';ctx.shadowBlur=3;ctx.beginPath();ctx.arc(px,py,2.5,0,Math.PI*2);ctx.stroke();ctx.restore();
      // 实际按下位置用带细槽的小块标记；中心白色，偏差向两侧渐变。
      const actualY=y(row.actual);
      const markerWidth=Math.min(18,cell*.7),markerHeight=13,cut=3,x0=center-markerWidth/2,x1=center+markerWidth/2,y0=actualY-markerHeight/2,y1=actualY+markerHeight/2;
      ctx.beginPath();ctx.moveTo(x0+cut,y0);ctx.lineTo(x1-cut,y0);ctx.lineTo(x1,y0+cut);ctx.lineTo(x1,y1-cut);ctx.lineTo(x1-cut,y1);ctx.lineTo(x0+cut,y1);ctx.lineTo(x0,y1-cut);ctx.lineTo(x0,y0+cut);ctx.closePath();
      ctx.fillStyle=hitColor(row.offset);ctx.fill();ctx.strokeStyle='#070b10aa';ctx.lineWidth=.7;ctx.stroke();
      ctx.strokeStyle='#10151d';ctx.lineWidth=1.4;ctx.lineCap='round';ctx.beginPath();ctx.moveTo(center-markerWidth*.27,actualY);ctx.lineTo(center+markerWidth*.27,actualY);ctx.stroke();
      if(view.hover?.index===row.index){ctx.strokeStyle='rgba(255,255,255,.8)';ctx.lineWidth=1.1;ctx.beginPath();ctx.arc(px,py,5,0,Math.PI*2);ctx.stroke();}
    }
  }
  // 未匹配的按下独立标记，不参与偏差曲线。
  ctx.lineWidth=1;ctx.lineCap='butt';
  for(const click of [...(view.data.unmatched_clicks||[]),...(view.data.invalid_clicks||[])]){
    if(click.time<view.start||click.time>view.end)continue;
    const center=left+(click.column+.5)*cell,py=y(click.time),half=cell/2-7;
    ctx.strokeStyle=click.early_miss?'#ff424f':'#8b929f';
    ctx.beginPath();ctx.moveTo(center-half,py);ctx.lineTo(center-5,py);ctx.moveTo(center+5,py);ctx.lineTo(center+half,py);ctx.stroke();
    ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(center-2.5,py-2.5);ctx.lineTo(center+2.5,py+2.5);ctx.moveTo(center+2.5,py-2.5);ctx.lineTo(center-2.5,py+2.5);ctx.stroke();ctx.lineWidth=1;
    if(click.early_miss){
      ctx.save();ctx.strokeStyle='#ff424f';ctx.lineWidth=3;ctx.shadowColor='#ff424f';ctx.shadowBlur=6;
      ctx.beginPath();ctx.moveTo(center-half,py);ctx.lineTo(center-5,py);ctx.moveTo(center+5,py);ctx.lineTo(center+half,py);ctx.stroke();ctx.shadowBlur=0;
      ctx.font='bold 8px Segoe UI';ctx.textAlign='center';const label='EARLY MISS',labelWidth=ctx.measureText(label).width;
      ctx.fillStyle='#23070b';ctx.fillRect(center-labelWidth/2-3,py-16,labelWidth+6,11);ctx.fillStyle='#ff7880';ctx.fillText(label,center,py-7);ctx.restore();
    }
  }
  const selected=view.hover?.link||(view.hover&&(view.data.click_links||[]).find(link=>!link.estimated&&link.column===view.hover.column&&link.reference_time===view.hover.time&&(view.hover.actual===null||link.time===view.hover.actual)));
  if(selected&&!selected.estimated){
    const center=left+(selected.column+.5)*cell,upper=y(selected.reference_time+window300),lower=y(selected.reference_time-window300),actualY=y(selected.time);
    ctx.save();ctx.strokeStyle=selected.early_miss?'#ff7880':'#d5aaff';ctx.lineWidth=3;ctx.shadowColor=selected.early_miss?'#ff424f':'#c391ff';ctx.shadowBlur=9;
    ctx.strokeRect(left+selected.column*cell+5,upper,cell-10,lower-upper);
    const failed=selected.estimated||(view.data.invalid_clicks||[]).some(c=>c.column===selected.column&&c.time===selected.time);
    if(failed){ctx.beginPath();ctx.moveTo(center-cell/2+7,actualY);ctx.lineTo(center+cell/2-7,actualY);ctx.stroke();}
    else ctx.strokeRect(center-Math.min(18,cell*.7)/2-2,actualY-8.5,Math.min(18,cell*.7)+4,17);
    ctx.restore();
  }
  ctx.restore();
  ctx.strokeStyle='#d3dcef';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(left,hitY);ctx.lineTo(left+width,hitY);ctx.stroke();ctx.fillStyle='#bdc9df';ctx.textAlign='left';ctx.font='9px Segoe UI';ctx.fillText('NOW',left+3,hitY-9);if(!canvas){$('preview-time').textContent=fmtTime(view.cursor);}else{ctx.fillStyle='#aab5c7';ctx.fillText(preciseTime(view.cursor),left+3,h-9);}
}
function showHit(row,event){
  state.hover=row;drawPreview();const description=row.link?.early_miss?`EARLY MISS ${fmtOffset(row.offset)} · 可能连崩起点`:row.offset===null?'Miss / 无有效头部':`${row.offset<0?'EARLY':row.offset>0?'LATE':'准时'} ${fmtOffset(row.offset)}`;
  const detail=`第 ${row.column+1} 列 · ${row.ln?'长条头':'普通音符'} · ${row.judgement}`,timing=`预期 ${preciseTime(row.time)}${row.actual!==null?` / 实际 ${preciseTime(row.actual)}`:''}`;
  const value=document.createElement('strong');value.className=row.offset<=0?'early-text':'late-text';value.textContent=description;const info=document.createElement('div');info.textContent=detail;const time=document.createElement('small');time.textContent=timing;
  if(row.link?.early_miss)value.style.color='#ff7880';
  const tip=$('tooltip');tip.replaceChildren(value.cloneNode(true),info.cloneNode(true),time.cloneNode(true));tip.hidden=false;tip.style.left=`${Math.max(8,Math.min(window.innerWidth-tip.offsetWidth-12,event.clientX+15))}px`;tip.style.top=`${Math.max(8,Math.min(window.innerHeight-tip.offsetHeight-12,event.clientY+15))}px`;
}
function configureMusic(data){
  const music=$('music');music.pause();music.removeAttribute('src');state.audioActive=false;
  $('music-enabled').disabled=!data.audio?.available;
  $('music-status').textContent=data.audio?.available?`配乐：${data.audio.name}`:'未找到谱面音乐文件，仍可播放竖轨';
  if(data.audio?.available){music.src=data.audio.url;music.volume=.7;music.preservesPitch=false;}music.load();
}
function syncMusicPosition(){
  if(!state.data?.audio?.available)return;
  const music=$('music'),target=state.cursor/1000*state.data.rate;
  if(music.readyState>=1)music.currentTime=Math.min(target,Number.isFinite(music.duration)?music.duration:target);
  music.playbackRate=state.data.rate*Number($('preview-speed').value);
}
function stopPreview(){state.playing=false;state.audioActive=false;state.lastFrame=null;state.playToken++;$('music').pause();$('preview-play').textContent='▶ 播放整曲';}
function previewFrame(time){
  if(!state.playing||!state.data)return;
  if(state.audioActive)state.cursor=$('music').currentTime*1000/state.data.rate;
  else if(state.lastFrame!==null)state.cursor+=(time-state.lastFrame)*Number($('preview-speed').value);
  state.lastFrame=time;
  if(state.cursor>=state.data.duration){state.cursor=state.data.duration;stopPreview();}updateWindow();drawAll();if(state.playing)requestAnimationFrame(previewFrame);
}
function setScrollSpeed(speed){state.scrollSpeed=Number(speed);state.span=spanForScrollSpeed(state.scrollSpeed);$('scroll-speed-value').value=`${state.scrollSpeed} px/帧`;updateWindow();drawAll();}
async function playPreview(){
  if(!state.data)return;if(state.playing){stopPreview();return;}if(state.cursor>=state.data.duration)seek(0);
  state.playing=true;state.lastFrame=null;state.hover=null;state.audioActive=false;
  const token=++state.playToken;$('tooltip').hidden=true;$('preview-play').textContent='Ⅱ 暂停';
  if($('music-enabled').checked&&state.data.audio?.available){
    try{syncMusicPosition();await $('music').play();if(token!==state.playToken||!state.playing)return;state.audioActive=true;}
    catch(error){if(token!==state.playToken||!state.playing)return;$('music-status').textContent='音乐无法播放，竖轨继续；可关闭配乐后重试。';}
  }
  if(token===state.playToken&&state.playing)requestAnimationFrame(previewFrame);
}
$('preview-play').onclick=playPreview;
document.addEventListener('keydown',event=>{
  if((event.code!=='Space'&&event.key!==' ')||event.ctrlKey||event.altKey||event.metaKey||event.isComposing||!state.data||$('analysis').hidden)return;
  const target=event.target;
  if(target instanceof Element&&(target.closest('input,textarea,select')||target.isContentEditable))return;
  event.preventDefault();
  if(!event.repeat)playPreview();
});
$('music').addEventListener('loadedmetadata',()=>{if(!state.audioActive)syncMusicPosition();});
$('music').addEventListener('ended',()=>{state.audioActive=false;state.lastFrame=null;});
$('music').addEventListener('error',()=>{state.audioActive=false;state.lastFrame=null;$('music-status').textContent='浏览器无法读取配乐，竖轨仍可播放。';});
$('preview-speed').onchange=()=>{if(state.data)$('music').playbackRate=state.data.rate*Number($('preview-speed').value);};
$('scroll-speed').oninput=event=>setScrollSpeed(event.target.value);
$('music-enabled').onchange=()=>{const resume=state.playing;stopPreview();if(resume)playPreview();};
$('preview-restart').onclick=()=>seek(0);
let noteDrag=null;
$('note-preview').addEventListener('pointerdown',event=>{if(!state.data)return;noteDrag={y:event.clientY,time:state.cursor};stopPreview();event.currentTarget.setPointerCapture(event.pointerId);event.currentTarget.style.cursor='grabbing';});
$('note-preview').addEventListener('pointermove',event=>{
  if(!state.data)return;const g=trackGeometry();if(noteDrag){seek(noteDrag.time+(event.clientY-noteDrag.y)/g.scale);return;}
  const rect=event.currentTarget.getBoundingClientRect(),px=event.clientX-rect.left,py=event.clientY-rect.top,column=Math.floor((px-g.left)/g.cell),time=state.cursor+(g.hitY-py)/g.scale;
  if(column<0||column>=state.data.keys||py<g.top||py>g.bottom){state.hover=null;$('tooltip').hidden=true;drawPreview();return;}
  const lane=state.lanes[column],center=g.left+(column+.5)*g.cell,extent=g.cell/2-9;
  let row=null,best=14;
  const begin=Math.max(0,lowerBound(lane,time-state.xMax-30)-1),end=lowerBound(lane,time+state.xMax+30)+1;
  for(const candidate of lane.slice(begin,end)){
    const expectedY=g.hitY-(candidate.time-state.cursor)*g.scale;
    const nodeX=center+offsetPosition(candidate.offset||0)*extent;
    const nodeDistance=Math.hypot(px-nodeX,py-expectedY);
    const actualDistance=candidate.actual!==null?Math.hypot(Math.max(0,Math.abs(px-center)-Math.min(9,g.cell*.35)),Math.max(0,Math.abs(py-(g.hitY-(candidate.actual-state.cursor)*g.scale))-6.5)):Infinity;
    const distance=Math.min(nodeDistance,actualDistance);
    if(distance<best){row=candidate;best=distance;}
  }
  for(const link of state.data.click_links||[]){
    if(link.estimated||link.column!==column)continue;
    const actualY=g.hitY-(link.time-state.cursor)*g.scale,expectedY=g.hitY-(link.reference_time-state.cursor)*g.scale;
    const failed=link.estimated||(state.data.invalid_clicks||[]).some(c=>c.column===column&&c.time===link.time);
    const actualDistance=Math.hypot(Math.max(0,Math.abs(px-center)-(failed?g.cell/2-7:Math.min(9,g.cell*.35))),Math.max(0,Math.abs(py-actualY)-(failed?3:6.5)));
    const expectedDistance=Math.hypot(Math.max(0,Math.abs(px-center)-(g.cell/2-5)),py-expectedY);
    const distance=Math.min(actualDistance,expectedDistance);
    if(distance<=best){const target=lane.find(n=>n.time===link.reference_time);row={...target,column,time:link.reference_time,actual:link.time,offset:link.time-link.reference_time,judgement:link.early_miss?'early-Miss':link.estimated?'未匹配（估算归属）':target?.judgement,link};best=distance;}
  }
  if(row)showHit(row,event);else{state.hover=null;$('tooltip').hidden=true;drawPreview();}
});
for(const event of ['pointerup','pointercancel'])$('note-preview').addEventListener(event,e=>{noteDrag=null;e.currentTarget.style.cursor='grab';});
$('note-preview').addEventListener('pointerleave',()=>{if(!noteDrag){state.hover=null;$('tooltip').hidden=true;drawPreview();}});
$('note-preview').addEventListener('wheel',event=>{if(!state.data)return;event.preventDefault();seek(state.cursor+event.deltaY/trackGeometry().scale);},{passive:false});
$('note-preview').addEventListener('keydown',event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();seek(state.cursor+(event.key==='ArrowDown'?100:-100));}});
let overviewDrag=null;
function overviewTime(event){const rect=$('overview').getBoundingClientRect();return Math.max(0,Math.min(state.data.duration,(event.clientX-rect.left-2)/(rect.width-4)*state.data.duration));}
$('overview').addEventListener('pointerdown',event=>{if(!state.data)return;const t=overviewTime(event);seek(t);overviewDrag={t,time:state.cursor};event.currentTarget.setPointerCapture(event.pointerId);});
$('overview').addEventListener('pointermove',event=>{if(overviewDrag)seek(overviewDrag.time+overviewTime(event)-overviewDrag.t);});
for(const event of ['pointerup','pointercancel'])$('overview').addEventListener(event,()=>{overviewDrag=null;});
$('search').oninput=renderList;$('refresh').onclick=()=>loadLibrary(true);new ResizeObserver(()=>{updateWindow();drawAll();}).observe($('note-preview'));window.addEventListener('resize',()=>{updateWindow();drawAll();});loadLibrary();
document.querySelectorAll('#width-presets button').forEach(button=>button.onclick=()=>{document.documentElement.style.setProperty('--preview-width',`${button.dataset.width}px`);document.querySelectorAll('#width-presets button').forEach(other=>other.classList.toggle('active',other===button));drawAll();});
$('choose-osu').onclick=async()=>{const button=$('choose-osu');button.disabled=true;button.textContent='请选择文件夹…';try{const result=await request('/api/select-osu',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!result.cancelled){state.selected=null;state.data=null;$('analysis').hidden=true;$('empty').hidden=false;await loadLibrary();}}catch(error){message(error.message==='接口不存在'?'后台服务仍是旧版本。请重新双击“启动.cmd”；新版会自动打开新页面。':error.message);}finally{button.disabled=false;button.textContent='选择 osu! 文件夹';}};


let videoExport=null;
async function videoRequest(path,body){return request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
function exportRange(full){if(!state.data)return;$('export-start').value=((full?0:state.start)/1000).toFixed(3);$('export-end').value=((full?state.data.duration:state.end)/1000).toFixed(3);}
$('export-full').onclick=()=>exportRange(true);$('export-visible').onclick=()=>exportRange(false);
const exportControls=['export-start','export-end','export-full','export-visible','export-fps','export-music','export-encoder','export-resolution'];
function exportBusy(busy){$('export-video').disabled=busy;$('export-cancel').hidden=!busy;exportControls.forEach(id=>$(id).disabled=busy);}
$('export-cancel').onclick=async()=>{if(videoExport){videoExport.cancelled=true;$('export-status').textContent='正在取消…';if(videoExport.id){try{await videoRequest(`/api/video/cancel?id=${videoExport.id}`,{});}catch{}}}};
async function watchExport(task){
  try{
    while(true){
      if(task.cancelled)await videoRequest(`/api/video/cancel?id=${task.id}`,{});
      const result=await request(`/api/video/status?id=${task.id}`);
      if(result.state==='cancelled'){$('export-status').textContent='已取消导出';return;}
      if(result.state==='failed')throw Error(result.error||'后台编码失败');
      if(result.state==='complete'){
        const link=document.createElement('a');link.href=result.url;link.download=result.name;link.textContent='下载 MP4';$('export-result').replaceChildren(link);
        const detail=document.createElement('p');detail.className='export-note';detail.textContent=`已保存到 ${result.path}`;$('export-result').append(detail);
        $('export-result').dataset.profile=JSON.stringify(result);
        $('export-status').textContent=`后台 ${result.encoder} · 完成 · ${result.speed}× · 用时 ${result.elapsed.toFixed(1)} 秒 · ${result.throughput.toFixed(0)} 帧/秒`;
        link.click();return;
      }
      $('export-status').textContent=result.state==='encoding'?'后台正在完成视频与音乐合成…':`后台 ${result.encoder} · ${Math.round(result.progress*100)}% · ${result.throughput.toFixed(0)} 帧/秒 · 预计剩余 ${Math.ceil(result.remaining)} 秒`;
      await new Promise(resolve=>setTimeout(resolve,500));
    }
  }catch(error){$('export-status').textContent=`导出失败：${error.message}`;}
  finally{localStorage.removeItem('background-video-job');videoExport=null;exportBusy(false);}
}
$('export-video').onclick=async()=>{
  if(!state.data||videoExport)return;
  const start=Number($('export-start').value),end=Number($('export-end').value),fps=Number($('export-fps').value);
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>state.data.duration/1000+.001){$('export-status').textContent='请输入曲目范围内的起止时间，终点必须大于起点。';return;}
  const task={cancelled:false,id:null};videoExport=task;exportBusy(true);$('export-result').replaceChildren();
  try{
    $('export-status').textContent='正在提交后台任务…';
    const job=await videoRequest('/api/video/start',{id:state.selected,start,end,fps,music:$('export-music').checked,encoder:$('export-encoder').value,span:state.span,speed:Number($('preview-speed').value),fill:state.fill,focusRange:state.focusRange,resolution:Number($('export-resolution').value)});
    task.id=job.id;localStorage.setItem('background-video-job',task.id);await watchExport(task);
  }catch(error){$('export-status').textContent=`导出失败：${error.message}`;videoExport=null;exportBusy(false);}
};
const savedVideoJob=localStorage.getItem('background-video-job');
if(savedVideoJob){const task={id:savedVideoJob,cancelled:false};videoExport=task;exportBusy(true);watchExport(task);}
