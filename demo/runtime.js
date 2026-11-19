/* Static demo adapter. All /api requests terminate in this browser. */
(() => {
  'use strict';
  const nativeFetch = globalThis.fetch.bind(globalThis);
  const rawStorage = globalThis.localStorage;
  const STATE_KEY = 'morrow-demo:state:v1', PREFIX = 'morrow-demo:pref:';
  const clone = value => JSON.parse(JSON.stringify(value));
  let connection = null;
  const storage = {
    getItem(key) {
      if(key==='activeModel')return JSON.stringify({pid:1,id:connection?.model||'demo-local',name:connection?.model||'演示回复'});
      try { return rawStorage.getItem(PREFIX + key); } catch { return null; }
    },
    setItem(key, value) { if(key!=='activeModel')rawStorage.setItem(PREFIX + key, String(value)); },
    removeItem(key) { rawStorage.removeItem(PREFIX + key); },
    clear() { for (const key of Object.keys(rawStorage)) if (key.startsWith(PREFIX)) rawStorage.removeItem(key); },
  };
  let state, storageUnavailable = false;
  try {
    const saved = JSON.parse(rawStorage.getItem(STATE_KEY) || 'null');
    state = saved?.version === 1 && Array.isArray(saved.sessions) && Array.isArray(saved.messages) && saved.settings ? saved : MorrowDemoSeed();
  } catch { state = MorrowDemoSeed(); storageUnavailable = true; }
  const persist = () => {
    const encoded = JSON.stringify(state);
    if (encoded.length > 2_000_000) throw new Error('演示数据已满，请先导出或重置。');
    try { rawStorage.setItem(STATE_KEY, encoded); }
    catch { storageUnavailable = true; throw new Error('浏览器未能保存数据，请导出备份或检查站点存储权限。'); }
  };
  for (const [key, value] of Object.entries({prefChatFont:'sans',prefDailyChat:'1',prefDailyAvatars:'1',dailyUserAvatar:'url-user',dailyAssistantAvatar:'url-assistant'})) {
    try {
      if (storage.getItem(key) === null) {
        const avatar = value.startsWith('url-') ? '/demo/assets/' + value.slice(4) + '.jpg' : value;
        storage.setItem(key, avatar);
      }
    } catch { storageUnavailable = true; }
  }
  const stamp = () => {
    const d = new Date(), z = n => String(n).padStart(2,'0');
    return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`;
  };
  const next = () => state.nextId++;
  const response = (data, status = 200) => new Response(JSON.stringify(data), {status, headers:{'Content-Type':'application/json'}});
  const unavailable = () => response({error:'静态演示不连接这项服务。可浏览示例记录；真实调度、推送和外部工具需运行完整版。'}, 501);
  const notice = message => {
    const el = document.querySelector('#demoNotice');
    if (el) { el.textContent = message; el.hidden = false; }
  };
  const sessions = () => state.sessions.filter(s => !s.archived);
  const model = () => connection?.model || 'demo-local';
  const providers = () => [{id:1,name:connection?'自带 Key · 本次页面':'演示回复',format:'openai',api_url:'',api_key_set:false,models:[{id:model(),name:connection?connection.model:'演示回复',enabled:true}]}];
  const newMessage = (sid, role, content) => {
    const msg = {id:next(),session_id:sid,role,content,created_at:stamp(),attachments:[],artifacts:[],reasoning:'',versions:null,active:0,proactive:0};
    state.messages.push(msg); return msg;
  };
  function localReply(text) {
    if (/薄荷|浇水|花盆/.test(text)) return '先摸摸土，还是湿的就不用加水。窗边别闷着，给它一点通风。你那盆薄荷今天叶子还耷拉着吗？';
    if (/面|番茄|做饭|晚饭|鸡蛋/.test(text)) return '要是还有番茄和鸡蛋，就煮一碗面吧。番茄先炒出汁，面快熟了再卧个蛋，少洗一个碗也挺好。';
    if (/雨|湿|袜子/.test(text)) return '鞋袜湿了就先换掉，湿鞋里塞点纸，放通风的地方。今天晚饭在家凑一顿，别又冒雨出去。';
    if (/谢谢|晚安/.test(text)) return '嗯，去忙你的吧。那杯薄荷水还在旁边的话，记得喝。';
    return '这页先用几段日常示例陪你试试聊天。可以聊聊窗台那盆薄荷，或者今晚的番茄鸡蛋面；想自由聊，也可以在“演示空间”里接上自己的模型。';
  }
  function endpoint(value) {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) throw new Error('请填写无账号、查询参数的 HTTPS 接口地址。');
    url.pathname = url.pathname.replace(/\/$/,'');
    if (!url.pathname.endsWith('/chat/completions')) url.pathname += '/chat/completions';
    return url.href;
  }
  async function realReply(sid, signal) {
    const config = connection;
    if (!config) throw new Error('连接已清除，请重新配置。');
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, {once:true});
    if (signal?.aborted) abort();
    const timeout = setTimeout(abort, 45000);
    try {
      const messages = state.messages.filter(m => m.session_id === sid).slice(-20).map(({role,content}) => ({role,content}));
      const result = await nativeFetch(config.endpoint, {
        method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+config.key},
        body:JSON.stringify({model:config.model,messages:[{role:'system',content:state.settings.persona.content},...messages],stream:false}),
        credentials:'omit',redirect:'error',referrerPolicy:'no-referrer',signal:controller.signal,
      });
      if (!result.ok) throw new Error(`模型接口返回 ${result.status}。请检查额度、模型名和接口配置。`);
      const data = await result.json();
      const content = data.choices?.[0]?.message?.content;
      if (typeof content !== 'string' || !content.trim()) throw new Error('接口未返回兼容的文本回复。');
      return content;
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      // Never display request bodies, keys or upstream error payloads.
      throw new Error(/^模型接口返回 |^接口未返回/.test(error.message) ? error.message : '无法直连接口。服务需允许浏览器跨域访问（CORS），也请检查网络。');
    } finally { clearTimeout(timeout); signal?.removeEventListener('abort',abort); }
  }
  async function chat(path, body, signal) {
    let sid = Number(body.session_id), session = state.sessions.find(s => s.id === sid);
    if (!session) {
      session = {id:next(),title:String(body.message || '新的聊天').slice(0,24),archived:0,pinned:0,created_at:stamp(),updated_at:stamp()};
      state.sessions.unshift(session); sid = session.id;
    }
    const isSend = path === '/api/chat/send';
    const user = isSend ? newMessage(sid,'user',String(body.message || '')) : state.messages.filter(m => m.session_id===sid && m.role==='user').at(-1);
    const target = !isSend && state.messages.find(m => m.id === Number(body.message_id) && m.role==='assistant');
    session.updated_at = stamp(); persist();
    let content;
    try { content = connection ? await realReply(sid,signal) : localReply(user?.content || ''); }
    catch (error) {
      if (error.name === 'AbortError') throw error;
      const event = {type:'error',detail:error.message};
      return new Response('data: '+JSON.stringify(event)+'\n\n',{headers:{'Content-Type':'text/event-stream'}});
    }
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        const emit = obj => controller.enqueue(encoder.encode('data: '+JSON.stringify(obj)+'\n\n'));
        try {
          emit({type:'session',session_id:sid,user_message_id:user?.id,title_pending:false});
          for (let i=0;i<content.length;i+=5) {
            if (signal?.aborted) throw new DOMException('Stopped','AbortError');
            emit({type:'delta',content:content.slice(i,i+5)});
            await new Promise(resolve=>setTimeout(resolve,12));
          }
          let ai;
          if (target) { target.versions = [...(target.versions || [target.content]),content]; target.active=target.versions.length-1; target.content=content; ai=target; }
          else ai = newMessage(sid,'assistant',content);
          persist(); emit({type:'done',message_id:ai.id,at:ai.created_at,model:model(),attachments:[],artifacts:[]});controller.close();
        } catch (error) { controller.error(error); }
      }
    });
    return new Response(stream,{headers:{'Content-Type':'text/event-stream'}});
  }
  async function route(path, method, body, query, signal) {
    let match;
    if (['/api/auth/check','/api/login'].includes(path)) return response({authed:true,ok:true});
    if (path === '/api/logout') { connection=null; refreshLabel(); return response({ok:true}); }
    if (path === '/api/ui-preview') return response({empty:false});
    if (path === '/api/visit') return response({last_ms:null,now_ms:Date.now()});
    if (['/api/activity','/api/push/read','/api/notes/seen'].includes(path)) return response({ok:true});
    if (path === '/api/version') return response({version:'static-demo',commit:''});
    if (['/api/chat/send','/api/chat/regenerate'].includes(path)) return chat(path,body,signal);
    if (path === '/api/chat/sessions') {
      if (method==='POST') { const item={id:next(),title:body.title||'新的聊天',archived:0,pinned:0,created_at:stamp(),updated_at:stamp()};state.sessions.unshift(item);persist();return response(item); }
      return response(state.sessions.filter(s => !!s.archived === (query.get('archived')==='1')));
    }
    if ((match=path.match(/^\/api\/chat\/messages\/(\d+)$/))) return response(state.messages.filter(m=>m.session_id===+match[1]));
    if ((match=path.match(/^\/api\/chat\/sessions\/(\d+)$/))) {
      const s=state.sessions.find(s=>s.id===+match[1]);if(!s)return response({error:'找不到这段对话。'},404);
      if(method==='GET')return response(s);
      if(method==='DELETE'){state.sessions=state.sessions.filter(x=>x!==s);state.messages=state.messages.filter(m=>m.session_id!==s.id);}
      if(method==='PUT') for(const key of ['title','archived','pinned']) if(body[key]!==undefined)s[key]=body[key];
      persist();return response(s);
    }
    if (path.endsWith('/generate-title')) return response({applied:false});
    if (path.startsWith('/api/chat/compression/')) return method==='GET'?response({has_summary:false}):unavailable();
    if ((match=path.match(/^\/api\/chat\/window\/(\d+)$/))) return method==='GET'?response({recent_count:20,continued_from:null}):unavailable();
    if ((match=path.match(/^\/api\/chat\/message\/(\d+)(?:\/(version))?$/))) {
      const m=state.messages.find(m=>m.id===+match[1]);if(!m)return response({error:'找不到消息。'},404);
      if(method==='DELETE'&&!match[2])state.messages=state.messages.filter(x=>x!==m);
      else if(match[2]&&method==='PUT'){m.active=Number(body.index)||0;m.content=(m.versions||[m.content])[m.active]||m.content;}
      else if(match[2]&&method==='POST'){m.versions=[...(m.versions||[m.content]),String(body.content||'')];m.active=m.versions.length-1;m.content=String(body.content||'');}
      else return unavailable();
      persist();return response(m);
    }
    if (path==='/api/providers') return method==='GET'?response(providers()):unavailable();
    if (path.startsWith('/api/providers/')) return unavailable();
    if (path==='/api/chat/tool-visibility') return response({servers:[],sticker:false,voice_letter:false});
    if (path==='/api/study/runs') return method==='GET'?response(state.runs):unavailable();
    if (path.startsWith('/api/study/runs/')) return unavailable();
    if (['/api/study/settings','/api/proactive/settings','/api/alarms/settings'].includes(path)) {
      return method==='GET'?response({enabled:false,sessions:sessions(),target_session_id:1,user_note:'',wake_prompt:'',handbooks:[]}):unavailable();
    }
    if (path==='/api/alarms') return response(state.alarms);
    if ((match=path.match(/^\/api\/alarms\/(\d+)(?:\/cancel)?$/))) { const item=state.alarms.find(x=>x.id===+match[1]);if(!item)return response({error:'纸条不存在。'},404);if(method==='GET')return response(item);item.status='cancelled';persist();return response(item); }
    if (path==='/api/proactive/status') return response({unread_count:0,unread_session_ids:[]});
    if (path==='/api/notes/latest') return response({...state.notes.at(-1),is_new:false,total:state.notes.length});
    if (path==='/api/notes/all') return response(state.notes.filter(n=>!query.get('date')||n.created_at.startsWith(query.get('date'))));
    if (path==='/api/notes/calendar') return response({days:state.notes.map(n=>n.created_at.slice(0,10))});
    if (path==='/api/notes') return method==='GET'?response(state.notes.at(-1)||{}):unavailable();
    if ((match=path.match(/^\/api\/notes\/(\d+)$/))&&method==='DELETE'){state.notes=state.notes.filter(n=>n.id!==+match[1]);persist();return response({ok:true});}
    if (path==='/api/memory/persona') {if(method==='PUT'){state.settings.persona.content=String(body.content||'');persist();}return response(state.settings.persona);}
    if (path==='/api/memory/recent') return response(state.memories.filter(m=>m.kind==='recent'));
    if (path==='/api/memory/entries') {
      if(method==='POST'){const item={id:next(),content:String(body.content||''),kind:body.kind||'fixed',pinned:0,tags:[],created_at:stamp(),updated_at:stamp()};state.memories.push(item);persist();return response(item);}
      return response(state.memories.filter(m=>m.kind===(query.get('kind')||'fixed')));
    }
    if ((match=path.match(/^\/api\/memory\/entries\/(\d+)$/))) {
      const item=state.memories.find(x=>x.id===+match[1]);if(!item)return response({error:'记忆不存在。'},404);
      if(method==='GET')return response(item);
      if(method==='DELETE')state.memories=state.memories.filter(x=>x!==item);
      else for(const key of ['content','pinned','kind','tags'])if(body[key]!==undefined)item[key]=body[key];
      persist();return response(item);
    }
    if (path==='/api/diary/latest') return response(state.diary.at(-1)||{});
    if (path==='/api/diary/calendar') return response({days:state.diary.map(n=>n.created_at.slice(0,10))});
    if (path==='/api/diary') {
      if(method==='POST'){const item={id:next(),content:String(body.content||''),tag:body.tag||'light',author:'user',favorite:0,comments:[],created_at:stamp(),updated_at:stamp()};state.diary.push(item);persist();return response(item);}
      return response(state.diary.filter(n=>(!query.get('date')||n.created_at.startsWith(query.get('date')))&&(!query.get('q')||n.content.includes(query.get('q')))&&(!query.get('tag')||n.tag===query.get('tag'))));
    }
    if ((match=path.match(/^\/api\/diary\/(\d+)(?:\/(favorite|comments))?$/))) {
      const item=state.diary.find(x=>x.id===+match[1]);if(!item)return response({error:'找不到记录。'},404);
      if(method==='GET')return response(item);
      if(method==='DELETE')state.diary=state.diary.filter(x=>x!==item);
      else if(match[2]==='comments')item.comments.push({id:next(),content:String(body.content||''),author:'user',created_at:stamp()});
      else if(method==='PUT')for(const key of ['content','favorite'])if(body[key]!==undefined)item[key]=body[key];
      persist();return response(item);
    }
    if (path==='/api/todos') {
      if(method==='POST'){const item={id:next(),text:String(body.text||''),done:0,created_at:stamp()};state.todos.push(item);persist();return response(item);}
      return response(state.todos);
    }
    if ((match=path.match(/^\/api\/todos\/(\d+)$/))) {
      const item=state.todos.find(x=>x.id===+match[1]);if(!item)return response({error:'待办不存在。'},404);
      if(method==='GET')return response(item);
      if(method==='DELETE')state.todos=state.todos.filter(x=>x!==item);else item.done=body.done?1:0;
      persist();return response(item);
    }
    const settingKey=path.slice(5);
    if (Object.hasOwn(state.settings,settingKey)&&settingKey!=='persona') {
      if(method==='PUT') {const allowed={identity:['user_name','assistant_name','assistant_pronoun'],'together-date':['date'],'home-greetings':['mode','morning','noon','evening','common'],'home-postcard':['enabled','content','caption','image_url'],'weather/city':['city']}[settingKey]||[];
        for(const key of allowed)if(body[key]!==undefined)state.settings[settingKey][key]=body[key];persist();}
      return response(state.settings[settingKey]);
    }
    if (path==='/api/assistant-settings') return method==='GET'?response(state.settings.assistants||{}):unavailable();
    if (path==='/api/push/status') return response({enabled:false,configured:false,available:false,subscriptions:[],preview:false});
    if (path==='/api/weather') return response({configured:false});
    if (path==='/api/music/status') return response({connected:false,platform:'kugou'});
    if (path==='/api/music/library') return response({favorites:[],queue:[],mode:'sequence'});
    if (path==='/api/cycle') return response({records:[],intimacy:[],private_notes:[],period_starts:[],prediction:{cycle_days:28,period_days:5,ovulation_offset:14}});
    if (['/api/auto-compression','/api/vision-status'].includes(path)) return method==='GET'?response({enabled:false,sessions:sessions(),session_ids:[]}):unavailable();
    if (path==='/api/stickers/meta') return response({series:[],tags:[]});
    const listPaths=['/api/favorites','/api/keepsakes','/api/artifacts','/api/tidy','/api/tidy/summaries','/api/usage','/api/http-logs','/api/music/listening','/api/music/comments','/api/music/search','/api/mcp-servers','/api/stickers','/api/ombre-recall/recent'];
    if (listPaths.includes(path)&&method==='GET') return response(state[path.slice(5)]||[]);
    if (path==='/api/usage/month') return response({requests:0,input_tokens:0,output_tokens:0,total_tokens:0,cost_usd:0,cache_read_input_tokens:0,cache_creation_input_tokens:0,cache_hit_rate:0});
    if (path==='/api/moments') return response({totals:{message_count:state.messages.length,text_units:0,today_message_count:0,month_message_count:0},daily:[],hourly:[],images:[],conversations:[]});
    if (path==='/api/github-backup/status') return response({configured:false,auto:{enabled:false}});
    if (path==='/api/tts/settings') return response({configured:false,enabled:false,providers:[],voices:[]});
    if (method!=='GET') return unavailable();
    return response({error:'静态演示没有这项接口。'},404);
  }
  globalThis.fetch = async (input, options={}) => {
    const url = new URL(typeof input==='string'?input:input.url,location.href);
    if (url.origin !== location.origin) return response({error:'演示页面不会自动访问外部服务。'},403);
    if (!url.pathname.startsWith('/api/')) {
      if (url.pathname.startsWith('/uploads/')) return response({error:'演示没有服务器上传目录。'},404);
      return nativeFetch(input,options);
    }
    let body={};
    try { if (typeof options.body==='string')body=JSON.parse(options.body); }
    catch { return response({error:'无效请求。'},400); }
    try { return await route(url.pathname,(options.method||'GET').toUpperCase(),body,url.searchParams,options.signal); }
    catch(error) {
      if(error.name==='AbortError')throw error;
      const message=error.message==='演示数据已满，请先导出或重置。'?error.message:'本次修改未能保存。请导出数据后检查浏览器存储空间。';
      notice(message);
      if (['/api/chat/send','/api/chat/regenerate'].includes(url.pathname)) {
        return new Response('data: '+JSON.stringify({type:'error',detail:message})+'\n\n',{headers:{'Content-Type':'text/event-stream'}});
      }
      return response({error:message},507);
    }
  };
  function refreshLabel() {
    const el=document.querySelector('#demoOpen');
    if(el)el.textContent=connection?'自带 Key · 本地保存':'演示回复 · 本地保存';
    if(typeof globalThis.applyModel==='function')globalThis.applyModel();
    if(typeof globalThis.loadProviders==='function')void globalThis.loadProviders();
  }
  function download() {
    const blob=new Blob([JSON.stringify({version:1,state,preferences:Object.fromEntries(Object.keys(rawStorage).filter(k=>k.startsWith(PREFIX)).map(k=>[k.slice(PREFIX.length),rawStorage.getItem(k)]))},null,2)],{type:'application/json'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='morrow-demo-export.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  }
  function mount() {
    const root=document.createElement('div');root.id='demoControls';
    root.innerHTML=`<button id="demoOpen" type="button" aria-haspopup="dialog">演示回复 · 本地保存</button>
      <dialog id="demoDialog" aria-labelledby="demoHeading"><button type="button" id="demoClose" aria-label="关闭演示说明">×</button>
      <h2 id="demoHeading">Morrow 演示空间</h2><p>用虚构的薄荷、雨天和晚饭，试着在这里待一会儿。</p>
      <p>默认回复来自预置示例，不是实时模型。小房间、未来纸条和主动消息展示虚构记录，不在后台运行任务。</p>
      <p>修改保存在当前浏览器的站点存储，刷新后保留。不会跨设备同步；清除站点数据后会丢失。不要在演示站存放重要资料。</p>
      <p id="demoNotice" role="status" hidden></p>
      <div class="demoActions"><button id="demoExport" type="button">导出本地数据</button><button id="demoReset" type="button">恢复示例数据</button></div>
      <details id="demoConnect"><summary>用自己的 Key 体验文本聊天</summary><form id="demoKeyForm">
      <p>只支持允许浏览器跨域访问的 OpenAI 兼容接口。Key 只在当前页面内存中保存，刷新即清除；会随请求发送到你填写的服务地址。当前对话最近 20 条文本和人设将一并发送。此模式不调用工具、不运行后台任务。</p>
      <label>接口地址<input id="demoEndpoint" type="url" required placeholder="https://api.example.com/v1" autocomplete="off"></label>
      <label>模型名<input id="demoModel" required placeholder="填写服务支持的模型名" autocomplete="off" maxlength="160"></label>
      <label>API Key<input id="demoKey" type="password" required autocomplete="off" spellcheck="false"></label>
      <button type="submit">仅本次页面使用</button><button id="demoDisconnect" type="button">清除连接</button><p id="demoKeyStatus" role="status"></p>
      </form></details><p class="demoFine">静态演示与私人部署相互独立。需要完整推送、工具和定时能力时，请使用完整版。</p></dialog>`;
    document.body.append(root);
    const dialog=document.querySelector('#demoDialog');
    document.querySelector('#demoOpen').onclick=()=>dialog.showModal();
    document.querySelector('#demoClose').onclick=()=>dialog.close();
    document.querySelector('#demoExport').onclick=download;
    document.querySelector('#demoReset').onclick=()=>{
      if(!confirm('清除本浏览器的演示聊天和设置，恢复虚构示例？请先导出想保留的数据。'))return;
      connection=null;storage.clear();rawStorage.removeItem(STATE_KEY);location.reload();
    };
    document.querySelector('#demoKeyForm').onsubmit=event=>{
      event.preventDefault();
      const status=document.querySelector('#demoKeyStatus');
      try {
        const address=endpoint(document.querySelector('#demoEndpoint').value.trim());
        const key=document.querySelector('#demoKey').value.trim(),name=document.querySelector('#demoModel').value.trim();
        if(!key||!name)throw new Error('请填写模型名和 Key。');
        connection={endpoint:address,key,model:name};document.querySelector('#demoKey').value='';
        status.textContent='已启用，仅向 '+new URL(address).host+' 发送模型请求。刷新或清除连接即可结束。';refreshLabel();
      } catch(error){status.textContent=error.message;}
    };
    document.querySelector('#demoDisconnect').onclick=()=>{connection=null;document.querySelector('#demoKey').value='';document.querySelector('#demoKeyStatus').textContent='已清除，恢复预置演示回复。';refreshLabel();};
    if(storageUnavailable)notice('浏览器存储不可用，修改可能无法在刷新后保留。');
    refreshLabel();
  }
  globalThis.MorrowDemo={storage,endpoint,getState:()=>clone(state),isConnected:()=>!!connection,
    unavailableWorker:()=>Promise.reject(new Error('静态演示不注册推送或后台任务。'))};
  document.addEventListener('DOMContentLoaded',mount,{once:true});
})();
