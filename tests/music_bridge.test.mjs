import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { client, runRequest } from '../music_bridge.mjs';

class FakeMeting {
  static calls=[];
  static failSong=false;
  static failLyric=false;
  constructor(platform){this.platform=platform;this.header={Cookie:'os=android; __remember_me=true'};FakeMeting.calls.push(['client',platform]);}
  format(){return this;}
  cookie(value){this.header.Cookie=value;FakeMeting.calls.push(['cookie',this.platform,value]);return this;}
  async _curl(url){FakeMeting.calls.push(['request',url]);return this;}
  async search(query,options){FakeMeting.calls.push(['search',this.platform,query,options]);return JSON.stringify([{id:42,name:'Fictional song'}]);}
  async song(id){FakeMeting.calls.push(['song',this.platform,id]);if(FakeMeting.failSong)throw Error('synthetic detail failure');return JSON.stringify([{id,pic_id:'998877'}]);}
  async url(id){FakeMeting.calls.push(['url',this.platform,id]);return JSON.stringify({url:'https://audio.example.test/song.mp3'});}
  async pic(id){FakeMeting.calls.push(['pic',this.platform,id]);return JSON.stringify({url:'https://img.example.test/'+id+'.jpg'});}
  async lyric(id){FakeMeting.calls.push(['lyric',this.platform,id]);if(FakeMeting.failLyric)throw Error('synthetic lyric failure');return JSON.stringify({lyric:'[00:00]Fictional lyric',tlyric:''});}
}
beforeEach(()=>{FakeMeting.calls=[];FakeMeting.failSong=false;FakeMeting.failLyric=false;});

test('NetEase search selects only NetEase and caps result limit',async()=>{
 const data=await runRequest({action:'search',platform:'netease',query:'fictional',cookie:'MUSIC_U=synthetic',limit:99},FakeMeting);
 assert.equal(data.result[0].id,42);assert(FakeMeting.calls.filter(c=>c[0]==='client').every(c=>c[1]==='netease'));
 assert.equal(FakeMeting.calls.find(c=>c[0]==='search')[3].limit,20);
});
test('legacy request defaults to KuGou',async()=>{
 await runRequest({action:'search',query:'fictional',cookie:'KugooID=7; t=synthetic'},FakeMeting);
 assert(FakeMeting.calls.filter(c=>c[0]==='client').every(c=>c[1]==='kugou'));
});
test('NetEase cover uses image ID from song detail, not the song ID',async()=>{
 const data=await runRequest({action:'prepare',platform:'netease',id:'42',cookie:'MUSIC_U=synthetic'},FakeMeting);
 assert.equal(data.pic.url,'https://img.example.test/998877.jpg');
 assert(FakeMeting.calls.some(c=>c[0]==='song'&&c[2]==='42'));
 assert(FakeMeting.calls.some(c=>c[0]==='url'&&c[2]==='42'));
 assert(FakeMeting.calls.some(c=>c[0]==='lyric'&&c[2]==='42'));
});
test('KuGou cover and old IDs retain their original behavior',async()=>{
 await runRequest({action:'prepare',id:'legacy-hash',cookie:'KugooID=7'},FakeMeting);
 assert(FakeMeting.calls.some(c=>c[0]==='pic'&&c[2]==='legacy-hash'));
 assert(!FakeMeting.calls.some(c=>c[0]==='song'));
});
test('missing artwork and lyrics do not discard a playable audio URL',async()=>{
 FakeMeting.failSong=true;FakeMeting.failLyric=true;
 const data=await runRequest({action:'prepare',platform:'netease',id:'42'},FakeMeting);
 assert.equal(data.url.url,'https://audio.example.test/song.mp3');assert.deepEqual(data.pic,{});assert.equal(data.lyric.lyric,'');
});
test('NetEase requests use HTTPS and user Cookie overrides default fields',async()=>{
 const meting=client('netease','MUSIC_U=synthetic; __remember_me=false',FakeMeting);
 await meting._curl('http://music.163.com/api/example');
 assert(FakeMeting.calls.some(c=>c[0]==='request'&&c[1]==='https://music.163.com/api/example'));
 assert(meting.header.Cookie.includes('os=android'));assert(meting.header.Cookie.includes('MUSIC_U=synthetic'));
 assert(meting.header.Cookie.includes('__remember_me=false'));assert(!meting.header.Cookie.includes('__remember_me=true'));
 assert.throws(()=>client('unknown','',FakeMeting));assert.throws(()=>client('netease','MUSIC_U=synthetic\r\nInjected: value',FakeMeting));
});
test('CLI errors never include the submitted Cookie',()=>{
 const result=spawnSync(process.execPath,[fileURLToPath(new URL('../music_bridge.mjs',import.meta.url))],{input:JSON.stringify({action:'unknown',cookie:'synthetic-private'}),encoding:'utf8'});
 assert.equal(result.status,1);assert.equal(JSON.parse(result.stdout).ok,false);assert(!result.stdout.includes('synthetic-private'));assert(!result.stderr.includes('synthetic-private'));
});
test('installed provider search, crypto, playback, cover and lyric decoders work with an offline fetch fixture',async()=>{
 const savedFetch=globalThis.fetch;const calls=[];
 const song={id:42,name:'Fictional song',al:{name:'Fictional album',pic:998877,picUrl:'https://img.example.test/998877.jpg'},ar:[{name:'Fictional artist'}]};
 globalThis.fetch=async(url,options)=>{
  calls.push(String(url));assert(String(url).startsWith('https://'));assert(options.headers.Cookie.includes('MUSIC_U=synthetic'));
  let data;
  if(String(url).includes('cloudsearch'))data={result:{songs:[song]}};
  else if(String(url).includes('song/detail'))data={songs:[song]};
  else if(String(url).includes('player/url'))data={data:[{url:'https://audio.example.test/song.mp3',size:12345,br:320000}]};
  else if(String(url).includes('lyric'))data={lrc:{lyric:'[00:00]Fictional lyric'},tlyric:{lyric:''}};
  else throw Error('Unexpected offline request');
  return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
 };
 try{
  const search=await runRequest({action:'search',platform:'netease',query:'fictional',cookie:'MUSIC_U=synthetic'});
  assert.equal(search.result[0].id,42);
  const result=await runRequest({action:'prepare',platform:'netease',id:'42',cookie:'MUSIC_U=synthetic'});
  assert.equal(result.url.url,'https://audio.example.test/song.mp3');assert(result.pic.url.includes('/998877.jpg'));assert(result.lyric.lyric.includes('Fictional lyric'));assert(calls.length>=4);
 }finally{globalThis.fetch=savedFetch;}
});
