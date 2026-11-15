import re
import shutil
import subprocess
import unittest
from pathlib import Path


class ChatNavigationRefinementTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_push_preference_copy_tracks_assistant_name(self):
        page = Path('static/index.html').read_text(encoding='utf-8')
        code = page[page.index('function syncPushPrefs('):page.index('async function currentPushSubscription(')]
        script = r'''
const assert=require('node:assert/strict'),nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{});return nodes.get(key)};
const PUSH_STATE={enabled:true,preview:true,busy:false,supported:true},window={isSecureContext:true};
let name='Fern';const assistantName=()=>name;
CODE
syncPushPrefs();assert.equal($('#pushPrefState').textContent,'回复完成或Fern来找你时，会轻轻提醒');
name='River';syncPushPrefs();assert.ok($('#pushPrefState').textContent.includes('River'));
syncPushPrefs('A permission message');assert.equal($('#pushPrefState').textContent,'A permission message');
'''.replace('CODE', code)
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_english_default_model_preserves_custom_names(self):
        page = Path('static/index.html').read_text(encoding='utf-8')
        code = page[page.index('function modelDisplayName('):page.index('function readModelChoice(')]
        script = r'''
const assert=require('node:assert/strict'),nodes=new Map();
const $=key=>{if(!nodes.has(key))nodes.set(key,{});return nodes.get(key)};
let active=null;const getActive=()=>active;
CODE
applyModel();assert.equal($('#modelName').textContent,'Default');
active={name:'默认模型',id:'fictional-model'};applyModel();
assert.equal($('#modelName').textContent,'Default');assert.equal($('#dailyModelName').textContent,'Default');
active={name:'自定义名字',id:'fictional-model'};applyModel();assert.equal($('#modelName').textContent,'自定义名字');
assert.equal(modelDisplayName({id:'fictional-model'}),'fictional-model');
'''.replace('CODE', code)
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    @classmethod
    def setUpClass(cls):
        cls.page = Path('static/index.html').read_text(encoding='utf-8')

    def test_memory_and_settings_have_one_navigation_entry_each(self):
        for removed in ('memoryDrawer', 'memoryDrawerMask', 'mainNavToggle', 'mainNavClose'):
            self.assertNotIn(f'id="{removed}"', self.page)
        self.assertNotIn('function setMemoryDrawer(', self.page)
        self.assertIn('$("#memoryMenuBtn").onclick=()=>navigateMain("chat");', self.page)
        self.assertIn('$("#settingsNavBtn").onclick=()=>navigateMain("chat");', self.page)
        memory = self.page.split('id="memoryNav"', 1)[1].split('</div>', 1)[0]
        self.assertLess(memory.index('data-memory-mode="we"'), memory.index('data-memory-mode="light"'))
        self.assertIn('role="tablist"', self.page)
        heading = self.page.split('<div class="settings-heading">', 1)[1].split('</div>', 1)[0]
        self.assertLess(heading.index('id="settingsNavBtn"'), heading.index('class="page-title"'))

    def test_stop_button_preserves_send_button_geometry(self):
        # State styles must inherit the circular send control's geometry so
        # switching to stop cannot overflow the composer's 32px grid column.
        rules = re.findall(r'([^{}]+)\{([^{}]*)\}', self.page.split('</style>', 1)[0])
        stop_rules = [body for selector, body in rules
                      if any(part.strip().endswith('.send-btn.stop')
                             for part in selector.split(','))]
        self.assertTrue(stop_rules)
        for body in stop_rules:
            properties = {declaration.split(':', 1)[0].strip()
                          for declaration in body.split(';') if ':' in declaration}
            self.assertFalse(properties & {'width', 'height', 'min-width', 'min-height',
                                           'padding', 'border-radius', 'transform'})

    def test_composer_order_and_live_chat_reveal(self):
        toolbar = self.page.split('<div class="composer-bottom">', 1)[1].split('<div class="plus-pop"', 1)[0]
        self.assertLess(toolbar.index('id="plusBtn"'), toolbar.index('id="modelSwitch"'))
        self.assertLess(toolbar.index('id="modelSwitch"'), toolbar.index('id="sendBtn"'))
        self.assertIn('grid-template-columns:32px minmax(0,1fr) 32px;align-items:center', self.page)
        self.assertIn('backdrop-filter:blur(12px) saturate(115%)', self.page)
        self.assertIn('.chat-topbar .menu-pop{pointer-events:auto;}', self.page)
        self.assertIn('translateX(var(--nav-reveal)) scale(.96)', self.page)
        self.assertIn('$("#mainNavChatReturn").onclick=()=>navigateMain("chat");', self.page)
        self.assertIn('document.body.classList.toggle("main-nav-open",open)', self.page)
        self.assertNotIn('cloneNode', self.page.split('function syncMainNavigation(', 1)[1].split('function openMainNavigation(', 1)[0])
        bottom = self.page.split('id="toBottomBtn"', 1)[1].split('</button>', 1)[0]
        self.assertIn('m7 10 5 5 5-5', bottom)
        self.assertNotIn('M12 5v14', bottom)
        self.assertIn('.composer .to-bottom{width:28px;height:28px;', self.page)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_navigation_does_not_focus_the_composer(self):
        navigation = self.page[self.page.index('function syncMainNavigation('):self.page.index('function openMainNavigation(')]
        view = self.page[self.page.index('function showView('):self.page.index('const APP_ROUTE_MARKER=')]
        script = r'''
const assert=require('node:assert/strict');
const nodes=new Map();
function element(){const classes=new Set();return {inert:false,isConnected:true,editable:false,
 classList:{contains:n=>classes.has(n),toggle:(n,v)=>v?classes.add(n):classes.delete(n),add:n=>classes.add(n),remove:n=>classes.delete(n)},
 setAttribute(){},matches(){return this.editable},focus(){this.focuses=(this.focuses||0)+1}}}
const $=key=>{if(!nodes.has(key))nodes.set(key,element());return nodes.get(key)},$$=()=>[];
const document={body:element()},setTimeout=()=>{throw Error('Navigation must not schedule input focus')};
let mainNavTarget=null,mainNavInert=[],mainNavReturnFocus=element();
// The return-animation timer is unrelated to keyboard focus.
const returnTimer=()=>{};
const closeNotesEntryPreview=()=>{};
NAVIGATION
document.body.classList.add('in-chat');$('#mainNavPage').classList.add('open');
mainNavReturnFocus.editable=true;syncMainNavigation(false);
assert.equal(mainNavReturnFocus.focuses,undefined);assert.equal($('#chatBack').focuses,1);
$('#mainNavPage').classList.add('open');mainNavReturnFocus=element();syncMainNavigation(false);
assert.equal(mainNavReturnFocus.focuses,1);
const hideTodos=()=>{},syncMainNavigationSelection=()=>{},syncSystemBars=()=>{},
 ensureSession=()=>Promise.resolve(),autoGrow=()=>{},loadTodos=()=>{},syncProactiveStatus=()=>{};
document.documentElement={getAttribute:()=>null};
VIEW
showView('chat');assert.equal($('#input').focuses,undefined);
'''.replace('NAVIGATION', navigation.replace('setTimeout(()=>document.body.classList.remove("main-nav-returning"),280)', 'returnTimer()')).replace('VIEW', view)
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_hidden_composer_keeps_its_height_until_shown(self):
        grow = self.page[self.page.index('function autoGrow('):self.page.index('input.addEventListener("input",autoGrow)')]
        script = r'''
const assert=require('node:assert/strict');
let visible=false,updates=0,spaces=0;
const input={style:{height:'60px'},getClientRects:()=>visible?[{}]:[],get scrollHeight(){return visible?35:0}},
 updateSendAvailability=()=>updates++,syncComposerSpace=()=>spaces++;
GROW
autoGrow();assert.equal(input.style.height,'60px');
visible=true;autoGrow();assert.equal(input.style.height,'35px');
assert.equal(updates,2);assert.equal(spaces,2);
'''.replace('GROW', grow)
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sidebar_structure_and_preferences_order(self):
        nav = self.page.split('<nav class="main-nav-links"', 1)[1].split('</nav>', 1)[0]
        self.assertEqual(nav.count('class="main-nav-chevron"'), 2)
        self.assertNotIn('aria-hidden="true">⌄', nav)
        self.assertIn('grid-template-rows:0fr', self.page)
        self.assertIn('grid-template-rows:1fr', self.page)
        self.assertIn('transform:scaleY(1)', self.page)
        self.assertLess(nav.index('id="cycleEntry"'), nav.index('class="main-nav-divider"'))
        self.assertLess(nav.index('class="main-nav-divider"'), nav.index('data-tidy-group="room"'))
        prefs = self.page.split('id="set-panel-prefs"', 1)[1].split('id="pushPrefsCard"', 1)[0]
        self.assertLess(prefs.index('id="appBgFile"'), prefs.index('id="tgDailyChat"'))
        self.assertLess(prefs.index('id="tgDailyChat"'), prefs.index('id="chatFontChips"'))
        self.assertIn('id="tgTextNavigation"', prefs)
        self.assertIn('body.text-navigation .nav-item>svg:not(.main-nav-chevron){display:none;}', self.page)
        self.assertIn('feTurbulence', self.page)
        self.assertIn('ellipse at 50% 42%', self.page)

    def test_quiet_sidebar_daily_header_and_todo_paper_slip(self):
        header = self.page.split('<header class="main-nav-page-header">', 1)[1].split('</header>', 1)[0]
        self.assertIn('id="nightLamp"', header)
        self.assertLess(header.index('id="dateLine"'), header.index('id="nightLamp"'))
        self.assertIn('.main-nav-links .nav-item{font-family:"EB Garamond",Georgia,serif;}', self.page)
        self.assertIn('.nav-item.active,.nav-item.group-current{background:transparent;', self.page)
        self.assertIn('.main-nav-avatar{width:56px;height:56px;}', self.page)
        capsule = self.page.split('<div class="chat-preview-menu">', 1)[1].split('</div>', 1)[0]
        self.assertIn('id="previewBtn"', capsule)
        self.assertIn('id="menuBtn"', capsule)
        self.assertNotIn('id="todoBtn"', capsule)
        self.assertIn('animation:todoSlipIn .3s var(--ease)', self.page)
        self.assertIn('clip-path:polygon(0% 10px,2% 0,4% 10px', self.page)
        self.assertIn('count.hidden=true;', self.page)
        self.assertIn('$("#todoPanelClose").focus({preventScroll:true})', self.page)
        self.assertIn('Daily chat</div>', self.page)
        self.assertIn('On: Daily · Off: Paper', self.page)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_reasoning_toggle_does_not_measure_collapsed_body_or_lock_rapid_clicks(self):
        toggle = self.page[self.page.index('function toggleReasoning('):self.page.index('function openAttachmentImage(')]
        script = r'''
const assert=require('node:assert/strict');
let reducedMotion=false,animations=0;
const classes=new Set(['collapsed']),head={animate(){animations++}},
 rb={classList:{toggle(name){classes.has(name)?classes.delete(name):classes.add(name)}},
 querySelector(selector){assert.equal(selector,'.head');return head},
 get scrollHeight(){throw Error('No synchronous geometry read')}};
TOGGLE
toggleReasoning(rb);assert.equal(classes.has('collapsed'),false);
toggleReasoning(rb);assert.equal(classes.has('collapsed'),true);assert.equal(animations,2);
reducedMotion=true;toggleReasoning(rb);assert.equal(classes.has('collapsed'),false);assert.equal(animations,2);
'''.replace('TOGGLE', toggle)
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('<div class="reasoning-reveal"><div class="reasoning-clip"><div class="body">', self.page)
        self.assertIn('body:not(.ai-bubble) .reasoning{margin:4px 4px 0;padding-left:0;border:0;}', self.page)
        self.assertNotIn('.reasoning.collapsed .body{', self.page)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_live_status_icons_follow_actions_without_rendering_untrusted_labels(self):
        code = self.page[self.page.index('const LIVE_STATUS_ICONS='):self.page.index('const input=$("#input");')]
        script = r'''
const assert=require('node:assert/strict');
const document={createElement(){const nodes=new Map();return {dataset:{},querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{});return nodes.get(selector)}}}};
CODE
for(const [tool,words,kind] of [
 ['write_note','记下天气','write'],['read_note','在翻便签','read'],['get_weather','在看天气','weather'],
 ['search_kept','翻一翻','search'],['play_music','放一首歌','listen'],['','在想','thinking'],['','在看日记','read']]){
 assert.equal(liveStatusKind(tool,words),kind);
}
const p=makeThinkPill();assert.equal(p.dataset.kind,'thinking');assert.equal(p.querySelector('.txt').textContent,'在想');
updateToolNarr(p,'Morrow在写便签','write_note');assert.equal(p.dataset.kind,'write');
assert.equal(p.querySelector('.txt').textContent,'在写便签');
updateToolNarr(p,'<img src=x onerror=alert(1)>','get_weather');assert.equal(p.dataset.kind,'weather');
assert.equal(p.querySelector('.status-icon').innerHTML,LIVE_STATUS_ICONS.weather);
assert.equal(p.querySelector('.txt').textContent,'<img src=x onerror=alert(1)>');
assert.equal(p.querySelector('.txt').innerHTML,undefined);
const q=makeToolNarr('在翻便签','read_note');assert.equal(q.className,'tool-narr');assert.equal(q.dataset.kind,'read');
'''.replace('CODE', code)
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('updateToolNarr(toolEl,o.narrate,o.tool)', self.page)
        self.assertIn('"Songti SC","SimSun",serif', self.page)

    def test_portraits_live_in_we_while_display_toggle_stays_in_preferences(self):
        we = self.page.split('id="memoryWePanel"', 1)[1].split('id="memoryNookPanel"', 1)[0]
        prefs = self.page.split('id="set-panel-prefs"', 1)[1].split('<!-- MCP', 1)[0]
        for control in ('dailyAvatarUploads', 'dailyUserAvatarFile', 'dailyAssistantAvatarFile', 'dailyAvatarPick', 'dailyAvatarClear'):
            self.assertIn(f'id="{control}"', we)
            self.assertNotIn(f'id="{control}"', prefs)
            self.assertEqual(self.page.count(f'id="{control}"'), 1)
        self.assertIn('id="tgDailyAvatars"', prefs)
        self.assertNotIn('id="tgDailyAvatars"', we)
        self.assertIn('if(memoryMode==="we"){syncPortraitUI();return;}', self.page)
        self.assertIn('.we-wheel-selection{position:absolute;top:88px;height:44px;', self.page)
        self.assertIn('mask-image:linear-gradient(transparent,#000 30%', self.page)
        drawer = self.page.split('id="chatSetDrawer"', 1)[1].split('id="chatToolSheetMask"', 1)[0]
        self.assertNotIn('<svg', drawer)
        self.assertNotIn('class="set-ico"', drawer)
        self.assertNotIn('id="thinkingIdentityCopy"', drawer)
        for title in ('Thinking', 'Prompt cache', 'Context', 'Image limit', 'Latest image only', 'Tools', 'Fold history', 'Continue chat', 'Background'):
            self.assertIn('>'+title+'<', drawer)
        nav = self.page.split('<nav class="main-nav-links"', 1)[1].split('</nav>', 1)[0]
        for label in ('Home', 'Tide', 'Room'):
            self.assertIn('>'+label+'</span>', nav)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_portraits_are_editable_without_daily_or_visible_avatars(self):
        code = self.page[self.page.index('function syncPortraitUI()'):self.page.index('function syncPrefUI()')]
        script = r'''
const assert=require('node:assert/strict');
const values=new Map([['prefDailyChat','0'],['prefDailyAvatars','0'],['dailyUserAvatar','data:image/png;base64,synthetic']]),
 localStorage={getItem:key=>values.get(key)??null};
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{style:{},classList:{toggle(name,value){this[name]=value}}});return nodes.get(key)};
const choices=['user','assistant'].map(kind=>({dataset:{kind},classList:{toggle(name,value){this[name]=value}}})), $$=()=>choices;
let dailyAvatarEditing='';
CODE
syncPortraitUI();assert.equal($('#dailyAvatarUploads').hidden,false);assert.equal($('#dailyAvatarUploads').classList.open,true);
assert.equal($('#dailyAvatarActions').classList.open,false);assert.equal(choices[0].classList['has-image'],true);
dailyAvatarEditing='assistant';syncPortraitUI();assert.equal($('#dailyAvatarActions').classList.open,true);assert.equal(choices[1].classList.on,true);
'''.replace('CODE', code)
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_inside_labels_and_halo_feedback_are_independent_of_expansion(self):
        code = self.page[self.page.index('function toggleReasoning('):self.page.index('function openAttachmentImage(')]
        labels = self.page[self.page.index('function setReasoningHead('):self.page.index('function renderReasoning(')]
        script = r'''
const assert=require('node:assert/strict');
let reducedMotion=false,cancels=0,animations=[],daily=true;
const head={animate(frames,options){animations.push({frames,options});return {cancel(){cancels++}}}},
 classes=new Set(['collapsed']),rb={classList:{toggle(n){classes.has(n)?classes.delete(n):classes.add(n)}},querySelector:()=>head};
const document={body:{classList:{contains:()=>daily}}},thinkSeconds=()=> '2s';
CODE
LABELS
toggleReasoning(rb);toggleReasoning(rb);assert.equal(cancels,1);assert.equal(classes.has('collapsed'),true);
assert.ok(animations[0].frames[1].boxShadow.includes('15px'));assert.equal(animations[0].options.duration,380);
reducedMotion=true;toggleReasoning(rb);assert.equal(animations.length,2);assert.equal(cancels,2);
const wrap={querySelector:()=>head};setReasoningHead(wrap,'thinking',2000);assert.ok(head.innerHTML.includes('Inside'));assert.ok(head.innerHTML.includes('breath-dots'));
setReasoningHead(wrap,'thought',2000);assert.ok(head.innerHTML.includes('Inside'));assert.ok(!head.innerHTML.includes('breath-dots'));
daily=false;setReasoningHead(wrap,'thought',2000);assert.ok(head.innerHTML.includes('Inside · 2s'));
'''.replace('CODE', code).replace('LABELS', labels)
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_weather_hints_date_header_and_text_navigation_preference(self):
        weather = self.page[self.page.index('async function loadWeatherCity()'):self.page.index('$("#wxCitySave").onclick=')]
        clock = self.page[self.page.index('function tickClock()'):self.page.index('setInterval(', self.page.index('function tickClock()'))]
        pref = self.page[self.page.index('const prefTextNavigation='):self.page.index('const prefNav=')]
        handler = self.page.split('$("#tgTextNavigation").onchange=', 1)[1].split('\n', 1)[0]
        script = r'''
const assert=require('node:assert/strict');
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{});return nodes.get(key)};
const values=new Map(),localStorage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
const document={body:{classList:{toggle(name,value){this[name]=value}}}};
PREF
const change=HANDLER
assert.equal(prefTextNavigation(),false);change({target:{checked:true}});assert.equal(prefTextNavigation(),true);
assert.equal(document.body.classList['text-navigation'],true);change({target:{checked:false}});assert.equal(prefTextNavigation(),false);
let keyConfigured=false,city='';const api=async()=>({json:async()=>({key_configured:keyConfigured,city})});
WEATHER
let homeWeatherText='',togetherDays=()=>7;
CLOCK
(async()=>{
 await loadWeatherCity();assert.equal($('#wxCityInput').placeholder,'未填写天气key，请在env中填写');
 keyConfigured=true;city='Fictional City';await loadWeatherCity();
 assert.equal($('#wxCityInput').placeholder,'填写城市，留空关闭天气');assert.equal($('#wxCityInput').value,city);
 tickClock();assert.match($('#dateLine').textContent,/^[A-Z][a-z]{2}, [A-Z][a-z]{2} \d+/);
 assert.ok(!$('#dateLine').textContent.includes(' · '));
 homeWeatherText='18°C';tickClock();assert.ok($('#dateLine').textContent.endsWith(' · 18°C'));
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('PREF', pref).replace('HANDLER', handler).replace('WEATHER', weather).replace('CLOCK', clock)
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_warm_surfaces_and_bundled_sans_font(self):
        self.assertIn('--nav-surface:#eee3d8;', self.page)
        self.assertIn('--text-primary:#4d4039;', self.page)
        self.assertIn('--glass:rgba(248,242,236,0.48);', self.page)
        self.assertIn('repeating-linear-gradient(0deg', self.page)
        self.assertIn('stroke-linecap:round;stroke-linejoin:round;', self.page)
        identity = self.page.split('<div class="main-nav-identity">', 1)[1].split('</nav>', 1)[0]
        self.assertLess(identity.index('id="mainNavTogether"'), identity.index('id="greeting"'))
        for block in (self.page.split('var chatFonts=', 1)[1].split(';', 1)[0],
                      self.page.split('const CHAT_FONTS=', 1)[1].split(';', 1)[0]):
            self.assertIn('sans:\'"Morrow Sans",', block)
        self.assertIn('src:url("/static/fonts/noto-sans-sc-400.woff2")', self.page)
        font = Path('static/fonts/noto-sans-sc-400.woff2').read_bytes()
        self.assertEqual(font[:4], b'wOF2')
        self.assertGreater(len(font), 100_000)
        self.assertIn('SIL OPEN FONT LICENSE', Path('static/fonts/NotoSansSC-OFL.txt').read_text(encoding='utf-8'))

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_font_preference_and_memory_tabs_preserve_saved_choices(self):
        fonts = self.page[self.page.index('const CHAT_FONTS='):self.page.index('const prefNav=')]
        memory = self.page[self.page.index('function setMemoryMode('):self.page.index('function renderOmbreServers(')]
        keyboard = self.page[self.page.index('$("#memoryNav").addEventListener'):self.page.index('$("#memoryNav").addEventListener') + 1100]
        keyboard = keyboard.split('\n});', 1)[0] + '\n});'
        script = r'''
const assert=require('node:assert/strict');
const values=new Map(),localStorage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
FONTS
assert.equal(prefChatFont(),'system');
for(const font of ['system','serif','sans']){values.set('prefChatFont',font);assert.equal(prefChatFont(),font);assert.ok(CHAT_FONTS[font]);}
values.set('prefChatFont','url(https://example.test/font)');assert.equal(prefChatFont(),'system');
assert.ok(CHAT_FONTS.serif.includes('serif'));assert.ok(CHAT_FONTS.sans.includes('SimHei'));
let memoryMode='light',loads=0,pills=0;
const tabs=['we','light'].map(mode=>({dataset:{memoryMode:mode},classList:{toggle(_,active){this.active=active}},setAttribute(name,value){this[name]=value},focus(){this.focused=true}}));
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{});return nodes.get(key)};
const $$=()=>tabs,loadMemory=()=>loads++,syncSlidingPill=()=>pills++;
$('#memoryNav').addEventListener=(_,fn)=>$('#memoryNav').keydown=fn;$('#memoryNav').querySelectorAll=()=>tabs;
MEMORY
KEYBOARD
setMemoryMode('we',false);assert.equal(values.get('memoryMode'),'we');assert.equal($('#memoryWePanel').hidden,false);assert.equal($('#memoryNookPanel').hidden,true);
assert.equal(tabs[0]['aria-selected'],'true');assert.equal(tabs[0].tabIndex,0);assert.equal(tabs[1].tabIndex,-1);assert.equal(loads,0);
$('#memoryNav').keydown({key:'ArrowRight',preventDefault(){}});assert.equal(memoryMode,'light');assert.equal(tabs[1].focused,true);assert.equal(loads,1);
$('#memoryNav').keydown({key:'Home',preventDefault(){}});assert.equal(memoryMode,'we');
setMemoryMode('invalid',false);assert.equal(memoryMode,'light');assert.ok(pills>=3);
'''.replace('\nFONTS\n', '\n'+fonts+'\n').replace('\nMEMORY\n', '\n'+memory+'\n').replace('\nKEYBOARD\n', '\n'+keyboard+'\n')
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"prefFont","prefChatFont"', self.page)
        self.assertIn('--chat-font-family', self.page.split('/* 偏好：开屏前', 1)[1].split('</script>', 1)[0])


if __name__ == '__main__':
    unittest.main()
