const CACHE_PREFIX = 'nook-pwa-';
const CACHE_NAME = 'nook-pwa-v2';

self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil((async()=>{
    const names=await caches.keys();
    await Promise.all(names.filter(name=>name.startsWith(CACHE_PREFIX)&&name!==CACHE_NAME).map(name=>caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  event.respondWith(fetch(event.request));
});

async function updatePushEvent(payload,action){
  if(!payload.event_id||!payload.event_token)return {allow:true};
  try{
    const response=await fetch(`/api/push/events/${payload.event_id}/state`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:payload.event_token,action}),
      credentials:'omit',
      cache:'no-store',
    });
    if(!response.ok)return {allow:false};
    return await response.json();
  }catch(_error){
    // A notification whose current state cannot be verified is not safe to
    // replay later. Chat will still retain its unread dot on the server.
    return {allow:false};
  }
}

self.addEventListener('push', event => {
  let payload={};
  try{payload=event.data?event.data.json():{};}catch(_error){payload={};}
  event.waitUntil((async()=>{
    if(!payload.force_show){
      const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
      // Android may keep a background/locked standalone window marked visible.
      // Suppress only when Morrow is genuinely the window in the user's hand.
      const foreground=windows.find(client=>client.visibilityState==='visible'&&client.focused);
      if(foreground){
        await updatePushEvent(payload,'suppress');
        foreground.postMessage({type:'nook-push-suppressed',session_id:payload.session_id});
        return;
      }
      const claim=await updatePushEvent(payload,'claim');
      if(payload.event_id&&!claim.allow)return;
    }
    const title=String(payload.title||'Morrow');
    const bodies=(Array.isArray(payload.bodies)?payload.bodies:[])
      .slice(0,8).map(body=>String(body||'').trim()).filter(Boolean);
    if(!bodies.length)bodies.push(payload.body||`${title}发来一条消息`);
    const group=payload.group||payload.tag||'nook-message';
    const interval=Math.max(0,Math.min(Number(payload.interval_ms)||1500,2000));
    for(let index=0;index<bodies.length;index++){
      await self.registration.showNotification(title,{
        body:bodies[index],
        badge:'/static/icons/badge-96.png?v=cat-1',
        tag:bodies.length>1?`${group}-${index}`:(payload.tag||group),
        data:{url:payload.url||'/?push_chat=1',group,event_id:payload.event_id,event_token:payload.event_token},
      });
      if(index<bodies.length-1){
        await new Promise(resolve=>setTimeout(resolve,interval));
      }
    }
    await updatePushEvent(payload,'shown');
  })());
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target=new URL(event.notification.data?.url||'/',self.location.origin).href;
  event.waitUntil((async()=>{
    await updatePushEvent(event.notification.data||{},'read');
    const group=event.notification.data?.group;
    if(group){
      const notifications=await self.registration.getNotifications();
      notifications.forEach(item=>{if(item.data?.group===group)item.close();});
    }
    const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    const exact=windows.find(client=>client.url===target);
    if(exact)return exact.focus();
    const opened=await self.clients.openWindow(target);
    return opened?opened.focus():null;
  })());
});
