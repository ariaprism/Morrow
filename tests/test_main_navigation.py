import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


class MainNavigationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path("static/index.html").read_text(encoding="utf-8")

    def test_single_fullscreen_navigation_preserves_all_destinations(self):
        nav = self.page.split('id="mainNavPage"', 1)[1].split("</aside>", 1)[0]
        self.assertCountEqual(re.findall(r'data-view="(\w+)"', nav),
                              ["chat", "notes", "memory", "diary", "cycle"] + ["night"] * 9 + ["settings"])
        self.assertCountEqual(re.findall(r'data-tidy-group="(\w+)"', nav), ["room"] + ["cabinet"] * 4 + ["drawer"] * 4)
        self.assertCountEqual(re.findall(r'data-nav-group="(\w+)"', nav), ["cabinet", "drawer"])
        self.assertIn('data-tidy-group="room" data-mode="footprint"', nav)
        self.assertIn('data-tidy-group="cabinet" data-mode="tool"', nav)
        self.assertIn('data-tidy-group="drawer" data-mode="voice"', nav)
        self.assertNotIn('>Tidy<', nav)
        self.assertIn('aria-label="Navigation" aria-hidden="true" inert', nav)
        self.assertIn('id="mainNavNames"', nav)
        self.assertIn('id="mainNavTogether"', nav)
        self.assertNotIn("bottom-nav", self.page)
        self.assertIn('$("#chatBack").onclick=openMainNavigation;', self.page)
        self.assertIn('const chatReady=showView("chat")', self.page)
        self.assertIn('padding-top:calc(72px + env(safe-area-inset-top))', self.page)

    def test_internal_pages_use_the_single_global_navigation(self):
        for old_id in ("tidyDrawer", "tidyDrawerMask", "tidyNav", "tidyCabinetToggle", "tidyDrawerToggle"):
            self.assertNotIn(f'id="{old_id}"', self.page)
        self.assertNotIn('function setTidyDrawer(', self.page)
        self.assertIn('$("#tidyMenuBtn").onclick=openMainNavigation;', self.page)
        self.assertNotIn('id="mainNavToggle"', self.page)
        self.assertNotIn('id="mainNavClose"', self.page)
        self.assertIn('id="mainNavChatReturn"', self.page)
        self.assertIn('document.body.classList.toggle("in-night",name==="night");', self.page)
        self.assertIn('aria-controls="mainNavCabinetChildren"', self.page)
        self.assertIn('aria-controls="mainNavDrawerChildren"', self.page)
        self.assertIn('if(n.dataset.navGroup){setMainNavigationGroup(', self.page)
        self.assertIn('setMainNavigationGroup(tidyNavigationGroup(tidyMode),true)', self.page)

    def test_life_entries_and_weather_are_moved_out_of_home(self):
        self.assertNotIn('id="view-home"', self.page)
        nav = self.page.split('id="mainNavPage"', 1)[1].split('</aside>', 1)[0]
        for control in ("diaryEntry", "diaryEntryStatus", "cycleEntry", "cycleEntryStatus", "dateLine"):
            self.assertEqual(self.page.count(f'id="{control}"'), 1)
            self.assertIn(f'id="{control}"', nav)
        self.assertIn('<span class="entry-label">Kept</span><span class="entry-status"', nav)
        self.assertIn('<span class="entry-label">Tide</span><span class="entry-status"', nav)
        footer = nav.split('<footer class="main-nav-footer">', 1)[1]
        header = self.page.split('<header class="main-nav-page-header">', 1)[1].split('</header>', 1)[0]
        self.assertIn('id="dateLine"', header)
        self.assertNotIn('id="dateLine"', footer)
        self.assertIn('refreshMainNavigationOverview();', self.page)
        self.assertNotIn('$("#diaryEntry").onclick=', self.page)
        self.assertNotIn('$("#cycleEntry").onclick=', self.page)
        self.assertIn('$("#diaryBack").onclick=()=>navigateMain("chat");', self.page)
        self.assertIn('$("#cycleBack").onclick=()=>navigateMain("chat");', self.page)

    def test_modal_keyboard_touch_and_focus_are_supported(self):
        self.assertIn('mainNavInert.forEach(element=>element.inert=true)', self.page)
        self.assertIn('mainNavInert.forEach(element=>element.inert=false)', self.page)
        self.assertIn('mainNavReturnFocus.focus({preventScroll:true})', self.page)
        self.assertIn('event.shiftKey&&document.activeElement===first', self.page)
        self.assertIn('event.key==="Escape"', self.page)
        self.assertIn('if(start.open&&dx<0)closeMainNavigation()', self.page)
        self.assertIn('syncMainNavigation(!!e.state?.mainNav)', self.page)

    def test_bubble_palette_has_solid_distinct_colors_and_dark_counterpart(self):
        self.assertIn('--bubble-user:#f2dfdf; --bubble-user-solid:#f2dfdf;', self.page)
        self.assertIn('--bubble-ai:#efeae2; --bubble-ai-solid:#efeae2;', self.page)
        self.assertIn('--bubble-ai-solid:#343031', self.page)

    @unittest.skipUnless(shutil.which("node"), "Node is optional; needed for JS behavior checks")
    def test_sidebar_status_refresh_and_category_selection(self):
        overview = self.page.split('function refreshMainNavigationOverview(){', 1)[1].split('async function loadDiaryHomeStatus()', 1)[0]
        diary = self.page.split('async function loadDiaryHomeStatus(){', 1)[1].split('/* ---------- Cycle', 1)[0]
        cycle = self.page.split('function localDateKey(', 1)[1].split('function cycleGridRange()', 1)[0]
        weather = self.page.split('async function loadWeather(){', 1)[1].split('setInterval(', 1)[0]
        selection = self.page.split('function tidyNavigationGroup(', 1)[1].split('function showView(', 1)[0]
        group_toggle = self.page.split('function setMainNavigationGroup(', 1)[1].split('function renderMainNavigationIdentity()', 1)[0]
        script = r'''
const assert=require("node:assert/strict");process.env.TZ="Asia/Shanghai";
const NativeDate=Date;
global.Date=class extends NativeDate{
  constructor(...args){super(...(args.length?args:["2026-11-10T12:00:00+08:00"]))}
  static now(){return new NativeDate("2026-11-10T12:00:00+08:00").getTime()}
};
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{classList:{toggle(name,value){this[name]=value}},setAttribute(name,value){this[name]=value}});return nodes.get(key);};
let day="2026-11-09",cycleData={records:[],period_starts:["2026-10-15"],prediction:{cycle_days:28}},
    weatherCalls=0,statusCalls=0,fail=false,ticks=0,homeWeatherText="",navigationWeatherUpdatedAt=0;
const tickClock=()=>ticks++,wxEmoji=()=>"☀️";
const loadGreetingSettings=async()=>{},loadVisit=async()=>{},loadPostcardSettings=async()=>{},loadNotesOverview=async()=>{};
const api=async path=>{
  if(fail)throw Error("offline");
  let data;
  if(path==="/api/diary/latest"){statusCalls++;data={created_at:day};}
  else if(path.startsWith("/api/cycle?"))data=cycleData;
  else if(path==="/api/weather"){weatherCalls++;data={configured:false};}
  else throw Error("Unexpected request");
  return {ok:true,json:async()=>data};
};
let tidyMode="tool";
const nav=[{dataset:{view:"chat"}},{dataset:{view:"night",tidyGroup:"room",mode:"footprint"}},
  ...['cabinet','drawer'].flatMap(tidyGroup=>[
    {dataset:{view:"night",tidyGroup,navGroup:tidyGroup}},
    ...(tidyGroup==='cabinet'?['tool','summary','together']:['voice','alarm','keepsake']).map(mode=>({dataset:{view:"night",tidyGroup,mode}}))])];
nav.forEach(n=>{n.classList={toggle:(name,on)=>n[name]=on};n.setAttribute=(_,value)=>n.current=value;n.removeAttribute=()=>n.current=null});
const $$=()=>nav;
OVERVIEW
DIARY
CYCLE
WEATHER
SELECTION
GROUP_TOGGLE
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  refreshMainNavigationOverview();await settle();
  assert.equal($('#diaryEntryStatus').textContent,'yesterday');
  assert.equal($('#cycleEntryStatus').textContent,'潮汐将近');
  assert.equal(homeWeatherText,'');assert.equal(weatherCalls,1);
  day='2026-11-01';cycleData={records:[{record_date:'2026-11-10',menstruation:true}]};
  refreshMainNavigationOverview();await settle();
  assert.equal($('#diaryEntryStatus').textContent,'Nov 1');assert.equal($('#cycleEntryStatus').textContent,'潮汐中');
  assert.equal(weatherCalls,1);assert.equal(statusCalls,2);assert.ok(ticks>=2);
  fail=true;await loadDiaryHomeStatus();await loadCycleHomeStatus();await loadWeather();
  assert.equal($('#diaryEntryStatus').textContent,'blank');assert.equal($('#cycleEntryStatus').textContent,'寻常日');
  assert.equal(homeWeatherText,'天气暂不可用');
  for(const [mode,group] of [['footprint','room'],['summary','cabinet'],['together','cabinet'],['voice','drawer'],['alarm','drawer'],['keepsake','drawer']]){
    tidyMode=mode;syncMainNavigationSelection('night');
    assert.deepEqual(nav.filter(n=>n.active).map(n=>n.dataset.tidyGroup),[group]);
    assert.deepEqual(nav.filter(n=>n.active).map(n=>n.dataset.mode),[mode]);
    if(group!=='room')assert.equal(nav.find(n=>n.dataset.navGroup===group)['group-current'],true);
  }
  syncMainNavigationSelection('chat');assert.deepEqual(nav.filter(n=>n.active).map(n=>n.dataset.view),['chat']);
  for(const group of ['cabinet','drawer']){
    const suffix=group==='cabinet'?'Cabinet':'Drawer';
    setMainNavigationGroup(group,true);
    assert.equal($('#mainNav'+suffix+'Toggle')['aria-expanded'],'true');
    assert.equal($('#mainNav'+suffix+'Children').classList.open,true);assert.equal($('#mainNav'+suffix+'Children').inert,false);
    setMainNavigationGroup(group,false);
    assert.equal($('#mainNav'+suffix+'Toggle')['aria-expanded'],'false');
    assert.equal($('#mainNav'+suffix+'Children').classList.open,false);assert.equal($('#mainNav'+suffix+'Children').inert,true);
  }
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('OVERVIEW', 'function refreshMainNavigationOverview(){' + overview).replace(
            'DIARY', 'async function loadDiaryHomeStatus(){' + diary).replace(
            'CYCLE', 'function localDateKey(' + cycle).replace(
            'WEATHER', 'async function loadWeather(){' + weather).replace(
            'SELECTION', 'function tidyNavigationGroup(' + selection).replace(
            'GROUP_TOGGLE', 'function setMainNavigationGroup(' + group_toggle)
        result = subprocess.run([shutil.which("node")], input=script, text=True,
                                capture_output=True, timeout=15, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is optional; needed for JS behavior checks")
    def test_preferences_and_navigation_history_behavior(self):
        prefs = self.page.split('const prefDailyChat=', 1)[1].split('const prefNight=', 1)[0]
        routes = self.page.split('const APP_ROUTE_MARKER=', 1)[1].split('function navigateSetPanel', 1)[0]
        pop = self.page.split('addEventListener("popstate",e=>{', 1)[1].split('$$(".nav-item").forEach', 1)[0]
        script = r'''
const assert=require("node:assert/strict");
const saved=new Map(),localStorage={getItem:k=>saved.get(k)??null};
PREFERENCES
assert.equal(prefDailyChat(),true);assert.equal(prefDailyAvatars(),true);
saved.set("prefDailyChat","0");saved.set("prefDailyAvatars","0");
assert.equal(prefDailyChat(),false);assert.equal(prefDailyAvatars(),false);
saved.delete("prefDailyChat");saved.set("prefNook","0");assert.equal(prefDailyChat(),false);
saved.set("prefNook","1");assert.equal(prefDailyChat(),true);
saved.set("prefDailyChat","0");assert.equal(prefDailyChat(),false);
saved.set("prefDailyChat","1");assert.equal(prefDailyChat(),true);
let active="chat",tidyMode="tool",mainNavTarget=null,modalOpen=false,popHandler;
const noop=()=>{},document={querySelector:()=>({id:"view-"+active}),body:{classList:{contains:()=>false}}};
const wallNotesById=new Map(),hideWallNote=noop;
const showView=(name,options={})=>{active=name;if(name==="night")tidyMode=options.tidyMode||tidyMode;};
const showSetPanel=noop,hideAssistPrompt=noop;
const syncMainNavigation=open=>modalOpen=open,hideMusicNote=noop,hideAllChatDrawers=noop;
const noteWall={classList:{contains:()=>false}};
const addEventListener=(name,fn)=>popHandler=fn;
const stack=[];let position=-1;
const history={get state(){return stack[position]},
 replaceState(state){if(position<0)position=0;stack[position]=state},
 pushState(state){stack.splice(++position);stack.push(state)},
 go(delta){position+=delta;assert.ok(position>=0);queueMicrotask(()=>popHandler({state:this.state}))},
 back(){this.go(-1)},forward(){this.go(1)}};
const closeMainNavigation=()=>history.back();
ROUTES
addEventListener("popstate",e=>{POP
const settle=()=>new Promise(resolve=>setTimeout(resolve,15));
(async()=>{
initAppNavigation();assert.equal(history.state.view,"chat");assert.equal(history.state.level,0);
navigateMain("notes");assert.equal(active,"notes");assert.equal(history.state.level,1);
navigateMain("settings");assert.equal(active,"settings");assert.equal(stack.length,2);
history.pushState(appRoute(2,"settings",{panel:"prefs"}));
history.pushState({...history.state,level:3,mainNav:true});modalOpen=true;
navigateMain("chat");await settle();await settle();
assert.equal(active,"chat");assert.equal(history.state.level,0);assert.equal(modalOpen,false);
// Browser Back dismisses navigation while preserving the current page and drilldown.
navigateMain("settings");history.pushState(appRoute(2,"settings",{panel:"prefs"}));
history.pushState({...history.state,level:3,mainNav:true});modalOpen=true;
history.back();await settle();assert.equal(active,"settings");assert.equal(history.state.panel,"prefs");
assert.equal(modalOpen,false);
// Selecting Home from a deep settings panel resolves through the chat root.
navigateMain("notes");await settle();await settle();assert.equal(active,"notes");
assert.equal(history.state.level,1);history.back();await settle();assert.equal(active,"chat");
// Separate navigation destinations preserve their mode through modal dismissal and history.
navigateMain("night",{tidyMode:"tool"});assert.equal(tidyMode,"tool");
history.pushState({...history.state,level:2,mainNav:true});modalOpen=true;
navigateMain("night",{tidyMode:"voice"});await settle();await settle();
assert.equal(active,"night");assert.equal(tidyMode,"voice");assert.equal(history.state.tidyMode,"voice");
history.back();await settle();assert.equal(active,"chat");
history.forward();await settle();assert.equal(active,"night");assert.equal(tidyMode,"voice");
navigateMain("settings");history.pushState(appRoute(2,"settings",{panel:"prefs"}));
history.pushState({...history.state,level:3,mainNav:true});modalOpen=true;
navigateMain("night",{tidyMode:"footprint"});await settle();await settle();await settle();
assert.equal(active,"night");assert.equal(tidyMode,"footprint");assert.equal(history.state.tidyMode,"footprint");
navigateMain("home");await settle();assert.equal(active,"chat");assert.equal(history.state.level,0);
// Repeated settings exits must not leave a delayed destination that captures later clicks.
for(let i=0;i<3;i++){
 navigateMain('settings');history.pushState(appRoute(2,'settings',{panel:'prefs'}));
 navigateMain('chat');await settle();assert.equal(active,'chat');assert.equal(history.state.view,'chat');
 navigateMain('memory');assert.equal(active,'memory');assert.equal(history.state.view,'memory');
 navigateMain('night',{tidyMode:'tool'});assert.equal(active,'night');assert.equal(history.state.view,'night');
 navigateMain('notes');assert.equal(active,'notes');assert.equal(history.state.view,'notes');
 navigateMain('chat');await settle();assert.equal(active,'chat');
}
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace("PREFERENCES", "const prefDailyChat=" + prefs).replace(
            "ROUTES", "const APP_ROUTE_MARKER=" + routes).replace("POP", pop)
        # Parse every inline script too, catching syntax errors outside the exercised functions.
        scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", self.page, re.S)
        script = "\n".join("new Function(" + json.dumps(s) + ");" for s in scripts) + script
        result = subprocess.run([shutil.which("node")], input=script, text=True,
                                capture_output=True, timeout=15, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
