'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = {user:null, health:null, conversation:null, busy:false, intent:'answer', stream:null, chapters:[], chapter:0, drafts:{}, editing:null};
function node(tag, className, text) { const element=document.createElement(tag); if(className)element.className=className; if(text!==undefined)element.textContent=text; return element; }
function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('#toast').hidden=true,4500); }
async function api(path, options={}) {
  const headers={...options.headers};
  if(options.body!==undefined) {headers['Content-Type']='application/json';options.body=JSON.stringify(options.body);}
  if(state.user?.csrf)headers['X-CSRF-Token']=state.user.csrf;
  const response=await fetch(path,{...options,headers,credentials:'same-origin'});
  let data; try {data=await response.json();}catch {data={detail:await response.text().catch(()=>response.statusText)};}
  if(!response.ok) {
    if(response.status===401 && path!=='/api/login')showLogin();
    const detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data);
    throw new Error(detail||`请求失败 (${response.status})`);
  }
  return data;
}
function showLogin(){state.stream?.close();state.user=null;$('#workspace').hidden=true;$('#login-screen').hidden=false;}
function setBusy(value){state.busy=value;$('#send').disabled=value;$('#question').disabled=value;$('#ticket-intent').disabled=value;$('#send').textContent=value?'…':'↑';}
async function enterWorkspace(user){
  state.user=user;state.conversation=null;state.intent='answer';
  $('#login-screen').hidden=true;$('#workspace').hidden=false;
  $('#user-name').textContent=user.username;$('#avatar').textContent=user.username[0].toUpperCase();
  $('#user-role').textContent=`${user.tenant_id} · ${user.role==='it_admin'?'管理员':'员工'}`;
  const live=state.health.mode==='live';
  $('#mode-label').textContent=live?'真实模型模式':'规则演示模式';$('#mode-badge').textContent=live?'LIVE':'DEMO';
  $('#banner').textContent=live?`真实模型：${state.health.model} · ${state.health.review_enabled?'独立审核已开启':'单 Agent 模式'} · 请核对引用与操作内容。`:'当前为规则演示，不调用大模型。你可以体验完整流程；配置 .env 后切换真实模型。';
  $('#add-document').hidden=user.role!=='it_admin';
  $('#ticket-intent').setAttribute('aria-pressed','false');
  await refreshConversations();await refreshDraftStatus();resetChat();showView('chat');
}
async function refreshConversations(){
  const list=await api('/api/conversations');$('#conversation-list').replaceChildren();
  for(const conversation of list){const button=node('button','conversation-item'+(conversation.id===state.conversation?' selected':''),conversation.title);button.addEventListener('click',()=>loadConversation(conversation.id).catch(error=>toast(error.message)));$('#conversation-list').append(button);}
}
function resetChat(){
  state.stream?.close();state.stream=null;state.conversation=null;setBusy(false);
  const welcome=$('#welcome');if(welcome)window.welcomeTemplate=welcome.cloneNode(true);
  $('#messages').replaceChildren(window.welcomeTemplate.cloneNode(true));bindPrompts();
  $('#trace').replaceChildren(node('div','trace-empty','发送问题后，这里会显示检索、引用校验与审核过程。'));$('#usage').replaceChildren();
}
function bindPrompts(){$$('[data-prompt]').forEach(button=>button.addEventListener('click',()=>{$('#question').value=button.dataset.prompt;submitQuestion();}));}
async function loadConversation(id){
  state.stream?.close();state.conversation=id;showView('chat');
  await refreshDraftStatus();const messages=await api(`/api/conversations/${id}/messages`);
  $('#messages').replaceChildren();for(const message of messages)renderMessage(message.role,message.content,message.payload);
  await refreshConversations();const runs=await api(`/api/conversations/${id}/runs`);
  $('#trace').replaceChildren();$('#usage').replaceChildren();
  if(runs.length){const run=await api(`/api/runs/${runs[0].id}`);watchRun(run.id,run.status==='queued'||run.status==='running');if(run.result)renderUsage(run.result);}
  else setBusy(false);
}
function showView(name){
  for(const view of $$('.view'))view.hidden=view.id!==`view-${name}`;
  for(const button of $$('[data-view]'))button.classList.toggle('active',button.dataset.view===name);
  $('#page-title').textContent={chat:'协作台',knowledge:'知识库',tickets:'工单中心',learn:'学习中心'}[name];
  if(name==='knowledge')refreshKnowledge().catch(error=>toast(error.message));
  if(name==='tickets')refreshTickets().catch(error=>toast(error.message));
  if(name==='learn')openLearning().catch(error=>toast(error.message));
}
const statusNames={answered:'已回答',evidence_found:'找到相关资料',insufficient_evidence:'依据不足',needs_clarification:'需要补充信息',awaiting_confirmation:'待你确认',failed:'运行失败'};
function renderMessage(role,text,payload){
  $('#welcome')?.remove();const article=node('article',`message ${role}`);const heading=node('div','message-role');
  heading.append(node('b','',role==='user'?'你':'AW'),node('span','',role==='user'?'':state.health.mode==='demo'?'规则演示助手':'知识协作助手'));
  article.append(heading,node('div','message-body',text));
  if(payload){
    for(const citation of payload.citations||[]){const details=node('details','citation');details.append(node('summary','',`${citation.title} · v${citation.version} ↗`),node('p','',citation.quote));article.append(details);}
    if(payload.draft)article.append(renderDraft(payload.draft));
    const labels=[statusNames[payload.status]||payload.status,payload.mode==='demo'?'规则演示':null,payload.review?`审核${payload.review.approved?'通过':'未通过'}`:null].filter(Boolean);
    article.append(node('div','result-meta',labels.join(' · ')));
    if(payload.run_id){const traceButton=node('button','text-button','查看此次运行过程 ↗');traceButton.addEventListener('click',()=>{watchRun(payload.run_id,false);renderUsage(payload);});article.append(traceButton);}
  }
  $('#messages').append(article);$('#messages').scrollTop=$('#messages').scrollHeight;
}
function renderDraft(draft){
  const card=node('div','draft-card');card.append(node('span','eyebrow','TICKET DRAFT'),node('h3','',draft.title),node('p','',draft.description));
  const status=state.drafts[draft.id]?.status||draft.status;
  if(status==='pending'){
    const actions=node('div','draft-actions');
    for(const [label,approve,className] of [['确认创建工单',true,'primary'],['暂不创建',false,'secondary']]){
      const button=node('button',className,label);button.addEventListener('click',async()=>{
        for(const other of actions.querySelectorAll('button'))other.disabled=true;
        try{const result=await api(`/api/drafts/${draft.id}/decision`,{method:'POST',body:{approve}});toast(approve?`工单已创建：${result.id}`:'草稿已拒绝');await refreshDraftStatus();card.replaceWith(renderDraft({...draft,status:approve?'approved':'rejected'}));}
        catch(error){toast(error.message);for(const other of actions.querySelectorAll('button'))other.disabled=false;}
      });actions.append(button);
    }card.append(actions);
  }else card.append(node('small','muted',`状态：${{approved:'已确认创建',rejected:'已拒绝',cancelled:'已取消'}[status]||status}`));
  return card;
}
async function submitQuestion(){
  const question=$('#question').value.trim();if(!question||state.busy)return;
  setBusy(true);try{
    if(!state.conversation){const conversation=await api('/api/conversations',{method:'POST',body:{title:question.slice(0,35)}});state.conversation=conversation.id;await refreshConversations();}
    renderMessage('user',question);$('#question').value='';$('#trace').replaceChildren();$('#usage').replaceChildren();
    const pending=node('div','pending-line','正在检索资料并核对依据…');pending.id='pending';$('#messages').append(pending);
    const response=await api(`/api/conversations/${state.conversation}/runs`,{method:'POST',body:{question,intent:state.intent}});
    state.intent='answer';$('#ticket-intent').setAttribute('aria-pressed','false');watchRun(response.run_id,true);
  }catch(error){$('#pending')?.remove();setBusy(false);toast(error.message);}
}
function addTrace(event){
  const names={queued:'任务入队',started:'开始协作',model_call:state.health.mode==='demo'?'规则决策':'模型调用',retrieval:'检索知识库',draft_prepared:'准备工单草稿',citation_check:'校验引用',review:'独立审核',tool_rejected:'拒绝无效调用',completed:'运行完成',failed:'运行失败'};
  const step=node('div','trace-step'+(event.stage==='failed'?' failed':''));step.append(node('strong','',names[event.stage]||event.stage));
  const details=[];if(event.payload.actor)details.push(event.payload.actor);if(event.payload.count!==undefined)details.push(`${event.payload.count} 条依据`);if(event.payload.elapsed_ms!==undefined)details.push(`${event.payload.elapsed_ms} ms`);if(event.payload.approved!==undefined)details.push(event.payload.approved?'通过':'未通过');
  step.append(node('small','',details.join(' · ')));$('#trace').append(step);
}
function renderUsage(result){
  const usage=result.usage;$('#usage').replaceChildren();if(!usage)return;
  const calls=state.health.mode==='demo'?`规则决策 ${usage.calls} 轮（无模型费用）`:`模型调用 ${usage.calls} 次\nTokens：${usage.input_tokens} 输入 / ${usage.output_tokens} 输出${usage.usage_known?'':'（估算）'}`;
  const cost=result.estimated_cost===null?'费用：未配置单价或非真实模型':`估算费用：${result.estimated_cost}（按配置单价）`;
  $('#usage').textContent=`${calls}\n${cost}\n总耗时：${result.duration_ms} ms`;
}
function watchRun(runId,active){
  state.stream?.close();$('#trace').replaceChildren();setBusy(active);
  const source=new EventSource(`/api/runs/${runId}/events`);state.stream=source;let errors=0;const seen=new Set();
  source.onmessage=event=>{errors=0;const item=JSON.parse(event.data);if(seen.has(item.id))return;seen.add(item.id);addTrace(item);};
  source.addEventListener('done',async()=>{
    source.close();if(state.stream!==source)return;setBusy(false);$('#pending')?.remove();
    try{const run=await api(`/api/runs/${runId}`);renderUsage(run.result||{});if(active){await refreshDraftStatus();renderMessage('assistant',run.result?.text||'运行未完成',run.result);}}
    catch(error){toast(error.message);}
  });
  source.onerror=()=>{errors++;if(errors>5){source.close();setBusy(false);$('#pending')?.remove();toast('运行过程连接中断，刷新页面可恢复查看。');}};
}
async function refreshDraftStatus(){const data=await api('/api/tickets');state.drafts=Object.fromEntries(data.drafts.map(draft=>[draft.id,draft]));return data;}
async function refreshTickets(){
  const data=await refreshDraftStatus();$('#draft-list').replaceChildren();$('#ticket-list').replaceChildren();
  const drafts=data.drafts.filter(draft=>draft.status==='pending');for(const draft of drafts)$('#draft-list').append(renderDraft(draft));
  if(!drafts.length)$('#draft-list').append(node('div','empty-state','暂无待确认草稿。在协作台选择“生成工单草稿”即可准备。'));
  for(const ticket of data.tickets){const card=node('article','ticket-card');card.append(node('span','badge light',ticket.id),node('h3','',ticket.title),node('p','',ticket.description));
    const labels={open:'待处理',in_progress:'处理中',resolved:'已解决'};
    if(state.user.role==='it_admin'){const select=node('select');select.setAttribute('aria-label',`工单状态：${ticket.title}`);for(const value of Object.keys(labels)){const option=node('option','',labels[value]);option.value=value;option.selected=value===ticket.status;select.append(option);}select.addEventListener('change',async()=>{try{await api(`/api/tickets/${ticket.id}`,{method:'PATCH',body:{status:select.value}});toast('工单状态已更新');}catch(error){toast(error.message);}});card.append(select);}
    else card.append(node('span','badge',labels[ticket.status]||ticket.status));$('#ticket-list').append(card);
  }
  if(!data.tickets.length)$('#ticket-list').append(node('div','empty-state','暂无正式工单。确认草稿后，工单会出现在这里。'));
}
async function refreshKnowledge(){
  const documents=await api('/api/documents');$('#knowledge-list').replaceChildren();
  for(const document of documents){const card=node('button','knowledge-card');card.append(node('div','doc-icon','▤'),node('h3','',document.title),node('p','',document.content.slice(0,110)+(document.content.length>110?'…':'')));const meta=node('div','card-meta');meta.append(node('span','',document.id),node('span','',`v${document.version} · ${document.roles.map(role=>role==='it_admin'?'管理员':'员工').join(' / ')}`));card.append(meta);card.addEventListener('click',()=>openDocument(document));$('#knowledge-list').append(card);}
  if(!documents.length)$('#knowledge-list').append(node('div','empty-state','当前身份没有可访问的资料。管理员可导入 Markdown 或文本。'));
}
function openDocument(document=null){
  state.editing=document?.id||null;const editable=state.user.role==='it_admin';
  $('#document-dialog-title').textContent=document?(editable?'编辑资料 · 保存为新版本':'查看资料'):'导入资料';
  $('#document-title').value=document?.title||'';$('#document-content').value=document?.content||'';$('#document-file').value='';
  $('#document-title').readOnly=!editable;$('#document-content').readOnly=!editable;$('#document-file').disabled=!editable;
  $('#role-employee').checked=document?document.roles.includes('employee'):true;$('#role-admin').checked=document?document.roles.includes('it_admin'):true;
  $('#role-employee').disabled=!editable;$('#role-admin').disabled=!editable;$('#save-document').hidden=!editable;$('#document-error').textContent='';$('#document-dialog').showModal();
}
function inline(parent,text){
  for(const fragment of text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)){if(fragment.startsWith('`')&&fragment.endsWith('`'))parent.append(node('code','',fragment.slice(1,-1)));else if(fragment.startsWith('**')&&fragment.endsWith('**'))parent.append(node('strong','',fragment.slice(2,-2)));else parent.append(document.createTextNode(fragment));}
}
function markdown(text){
  const container=node('div');const lines=text.split('\n');let code=null,list=null,table=null;
  for(const line of lines){
    if(line.startsWith('```')){if(code){container.append(code);code=null;}else code=node('pre');list=null;table=null;continue;}
    if(code){code.textContent+=line+'\n';continue;}
    if(!line.trim()){list=null;table=null;continue;}
    if(line.startsWith('|')){if(/^\|[\s:|\-]+\|$/.test(line))continue;if(!table){table=node('table');container.append(table);}const row=node('tr');for(const value of line.split('|').slice(1,-1)){const cell=node(table.children.length?'td':'th');inline(cell,value.trim());row.append(cell);}table.append(row);continue;}
    table=null;
    const heading=line.match(/^(#{1,4})\s+(.*)/);if(heading){const element=node('h'+heading[1].length);inline(element,heading[2]);container.append(element);list=null;continue;}
    const item=line.match(/^(?:[-*]|\d+\.)\s+(.*)/);if(item){if(!list){list=node('ul');container.append(list);}const li=node('li');inline(li,item[1]);list.append(li);continue;}
    list=null;const paragraph=node(line.startsWith('>')?'blockquote':'p');inline(paragraph,line.replace(/^>\s?/,''));container.append(paragraph);
  }return container;
}
async function openLearning(){if(!state.chapters.length)state.chapters=await api('/api/tutorials');await loadChapter(state.chapter);}
async function loadChapter(index){
  state.chapter=index;$('#chapter-list').replaceChildren();
  state.chapters.forEach((chapter,position)=>{const button=node('button','chapter-button'+(position===index?' active':''));button.append(node('span','chapter-number',String(position).padStart(2,'0')),node('span','',chapter.title));button.addEventListener('click',()=>loadChapter(position).catch(error=>toast(error.message)));$('#chapter-list').append(button);});
  const chapter=await api(`/api/tutorials/${state.chapters[index].slug}`);$('#chapter-content').replaceChildren(markdown(chapter.content));
  const pagination=node('div','chapter-pagination');for(const [label,next] of [['← 上一章',index-1],['下一章 →',index+1]]){const button=node('button','secondary',label);button.disabled=next<0||next>=state.chapters.length;button.addEventListener('click',()=>loadChapter(next).then(()=>$('#chapter-content').scrollIntoView({behavior:'smooth'})));pagination.append(button);}$('#chapter-content').append(pagination);
}
$('#login-form').addEventListener('submit',async event=>{event.preventDefault();$('#login-error').textContent='';$('#login-button').disabled=true;try{const user=await api('/api/login',{method:'POST',body:{username:$('#username').value,password:$('#password').value}});await enterWorkspace(user);}catch(error){$('#login-error').textContent=error.message;}finally{$('#login-button').disabled=false;}});
$$('[data-account]').forEach(button=>button.addEventListener('click',()=>{const name=button.dataset.account;$('#username').value=name;$('#password').value={employee:'AgentDemo!2026',admin:'AgentAdmin!2026',beta:'AgentBeta!2026'}[name];}));
$('#logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'});showLogin();}catch(error){toast(error.message);}});
$$('[data-view]').forEach(button=>button.addEventListener('click',()=>showView(button.dataset.view)));
$('#new-chat').addEventListener('click',()=>{resetChat();showView('chat');refreshConversations().catch(error=>toast(error.message));});
$('#composer').addEventListener('submit',event=>{event.preventDefault();submitQuestion();});
$('#question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();submitQuestion();}});
$('#ticket-intent').addEventListener('click',()=>{state.intent=state.intent==='ticket'?'answer':'ticket';$('#ticket-intent').setAttribute('aria-pressed',String(state.intent==='ticket'));if(state.intent==='ticket'&&!$('#question').value)$('#question').value='问题仍未解决，请协助准备工单';$('#question').focus();});
$('#learn-link').addEventListener('click',()=>showView('learn'));$('#refresh-tickets').addEventListener('click',()=>refreshTickets().catch(error=>toast(error.message)));
$('#add-document').addEventListener('click',()=>openDocument());$('#close-dialog').addEventListener('click',()=>$('#document-dialog').close());
$('#document-file').addEventListener('change',async()=>{const file=$('#document-file').files[0];if(!file)return;if(file.size>240000){toast('文件过大，请使用 8 万字以内的文本');return;}const content=await file.text();if(content.length>80000){toast('资料正文最多 8 万字');return;}$('#document-content').value=content;if(!$('#document-title').value)$('#document-title').value=file.name.replace(/\.(md|txt)$/i,'');});
$('#document-form').addEventListener('submit',async event=>{event.preventDefault();const roles=[];if($('#role-employee').checked)roles.push('employee');if($('#role-admin').checked)roles.push('it_admin');if(!roles.length){$('#document-error').textContent='至少选择一个可访问角色';return;}$('#save-document').disabled=true;try{await api(state.editing?`/api/documents/${state.editing}`:'/api/documents',{method:state.editing?'PUT':'POST',body:{title:$('#document-title').value,content:$('#document-content').value,roles}});$('#document-dialog').close();await refreshKnowledge();toast('资料已保存，新的检索将使用最新版本');}catch(error){$('#document-error').textContent=error.message;}finally{$('#save-document').disabled=false;}});
(async()=>{window.welcomeTemplate=$('#welcome').cloneNode(true);try{state.health=await api('/api/health');$('#demo-accounts').hidden=!state.health.demo_accounts;try{await enterWorkspace(await api('/api/me'));}catch{showLogin();}}catch(error){$('#login-error').textContent='无法连接服务：'+error.message;}})();
