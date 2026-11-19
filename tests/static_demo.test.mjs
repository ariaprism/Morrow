import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

function setup(saved) {
  const backing={...saved}, calls=[];
  const localStorage=Object.defineProperties(backing,{
    getItem:{value:key=>backing[key]??null},
    setItem:{value:(key,value)=>{backing[key]=String(value);}},
    removeItem:{value:key=>{delete backing[key];}},
  });
  const context=vm.createContext({
    Response,ReadableStream,TextEncoder,DOMException,URL,Date,AbortController,setTimeout,clearTimeout,
    localStorage,location:{href:'https://demo.example.test/',origin:'https://demo.example.test'},
    document:{addEventListener(){},querySelector(){return null;}},
    fetch:async (...args)=>{calls.push(args);return new Response('{}');},
  });
  for(const name of ['seed.js','runtime.js'])vm.runInContext(fs.readFileSync(new URL('../demo/'+name,import.meta.url),'utf8'),context);
  const request=async(path,method='GET',body)=>context.fetch(path,{method,...(body?{body:JSON.stringify(body)}:{})});
  return {context,request,calls,backing};
}

test('boot routes and fictional pages work without an API server',async()=>{
  const {request,calls}=setup();
  assert.equal((await(await request('/api/auth/check')).json()).authed,true);
  for(const path of ['/api/chat/sessions','/api/study/runs','/api/alarms','/api/notes/all','/api/memory/entries','/api/diary']){
    const r=await request(path);assert.equal(r.status,200);assert.ok(Array.isArray(await r.json()));
  }
  assert.equal(calls.length,0);
});
test('all API writes and external services fail closed unless explicitly supported',async()=>{
  const {request,context,calls,backing}=setup();
  for(const path of ['/api/providers','/api/study/runs','/api/push/subscribe','/api/backup/export'])assert.equal((await request(path,'POST',{api_key:'synthetic-key-only'})).status,501);
  assert.equal((await request('/api/unknown')).status,404);
  assert.equal((await context.fetch('https://other.example.test/api/send')).status,403);
  assert.equal(calls.length,0);assert.ok(!JSON.stringify(backing).includes('synthetic-key-only'));
});
test('chat emits the production SSE protocol and persists in this browser',async()=>{
  const {request,backing}=setup();
  const r=await request('/api/chat/send','POST',{session_id:1,message:'薄荷今天好一点了'});
  const events=(await r.text()).trim().split('\n\n').map(x=>JSON.parse(x.slice(6)));
  assert.equal(events[0].type,'session');assert.equal(events.at(-1).type,'done');
  assert.ok(events.some(x=>x.type==='delta'));
  const reloaded=setup(backing);
  const messages=await(await reloaded.request('/api/chat/messages/1')).json();
  assert.equal(messages.at(-2).content,'薄荷今天好一点了');assert.equal(messages.at(-1).role,'assistant');
});
test('editing local todos and memories survives reload',async()=>{
  const {request,backing}=setup();
  const item=await(await request('/api/todos','POST',{text:'买两个番茄'})).json();
  await request('/api/todos/'+item.id,'PUT',{done:1});
  const memory=await(await request('/api/memory/entries','POST',{content:'示例记忆',kind:'fixed'})).json();
  const reload=setup(backing);
  assert.equal((await(await reload.request('/api/todos')).json()).find(x=>x.id===item.id).done,1);
  assert.ok((await(await reload.request('/api/memory/entries')).json()).some(x=>x.id===memory.id));
});
test('demo preferences never clear other same-origin storage',()=>{
  const {context,backing}=setup({'another-app':'keep'});
  context.MorrowDemo.storage.setItem('prefNight','1');context.MorrowDemo.storage.clear();
  assert.equal(backing['another-app'],'keep');assert.equal(context.MorrowDemo.storage.getItem('prefNight'),null);
});
test('reading details never cancels alarms or changes completed todos',async()=>{
  const {request,context}=setup();
  const before=JSON.stringify(context.MorrowDemo.getState());
  for(const path of ['/api/alarms/2','/api/todos/2','/api/memory/entries/1','/api/diary/1','/api/chat/sessions/1'])assert.equal((await request(path)).status,200);
  assert.equal(JSON.stringify(context.MorrowDemo.getState()),before);
});
test('provider endpoint validation prevents accidental credentials or insecure URLs',()=>{
  const {context}=setup();
  assert.equal(context.MorrowDemo.endpoint('https://api.example.test/v1'),'https://api.example.test/v1/chat/completions');
  for(const url of ['http://api.example.test/v1','https://name:secret@example.test/v1','https://api.example.test/v1?key=secret','javascript:alert(1)'])assert.throws(()=>context.MorrowDemo.endpoint(url));
});
test('cancelled demo replies are never saved as completed messages',async()=>{
  const {request,context}=setup();
  const control=new AbortController();
  const r=await context.fetch('/api/chat/send',{method:'POST',body:JSON.stringify({session_id:1,message:'下雨了'}),signal:control.signal});
  control.abort();await assert.rejects(r.text());
  const messages=await(await request('/api/chat/messages/1')).json();
  assert.equal(messages.at(-1).role,'user');
});
