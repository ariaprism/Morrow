import unittest
import shutil
import subprocess
from pathlib import Path


class DesktopLayoutContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path("static/index.html").read_text(encoding="utf-8")
        start = cls.page.index("@media (min-width:900px)")
        cls.desktop = cls.page[start:cls.page.index("</style>", start)]

    def test_desktop_rules_are_isolated_from_mobile(self):
        self.assertIn("/* 桌面端使用独立布局；窄屏继续沿用上面的手机样式。 */", self.page)
        self.assertNotIn("@media (max-width:899px)", self.desktop)
        self.assertIn(".view{left:0;padding:72px 36px 40px;}", self.desktop)
        self.assertIn("#view-chat{left:0;padding:0;}", self.desktop)

    def test_notes_and_tasks_replace_the_home_layout(self):
        self.assertNotIn('#view-home', self.desktop)
        self.assertIn('#view-notes{padding:0;overflow:hidden;}', self.page)
        self.assertIn('width:min(420px,calc(100% - 32px))', self.page)

    def test_chat_paper_frame_is_persistent_and_morphs_without_moving_other_views(self):
        self.assertEqual(self.page.count('class="desktop-paper-stage"'), 1)
        self.assertIn('body.desktop-navigation{--desktop-nav-width:320px;}', self.page)
        self.assertIn('body.desktop-navigation.in-chat .desktop-paper-stage{inset:12px 12px 12px calc(var(--desktop-nav-width) - 4px);border-radius:28px;', self.page)
        self.assertIn('transition:inset .36s var(--ease),border-radius .36s var(--ease)', self.page)
        self.assertIn('body.desktop-navigation.in-chat #view-chat{left:calc(var(--desktop-nav-width) - 4px);', self.page)
        self.assertIn('body.desktop-navigation #view-diary{left:var(--desktop-nav-width);}', self.page)
        self.assertIn('body.desktop-navigation.in-chat #app{background-color:var(--nav-surface);background-image:var(--nav-paper-image);}', self.page)
        self.assertIn('body.desktop-navigation.in-chat .main-nav-page{background:transparent;}', self.page)

    def test_chat_header_messages_and_composer_share_a_reading_width(self):
        self.assertIn("padding-left:max(24px,calc((100% - 760px)/2));", self.desktop)
        self.assertIn(".composer{left:50%;right:auto;width:min(100%,808px);", self.desktop)
        self.assertIn("body.daily-chat .composer{padding:10px 24px 12px;}", self.desktop)
        self.assertIn("body.daily-chat .composer .plus-pop{width:calc(100% + 48px);", self.desktop)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_kept_compose_button_tracks_filter_center_and_resets_on_mobile(self):
        code = self.page.split('function syncDiaryComposePosition(){', 1)[1].split('addEventListener("resize",syncDiaryComposePosition', 1)[0]
        script = r'''
const assert=require('node:assert/strict');
let wide=true,active=true,rect={left:1600,width:38};
const button={offsetWidth:48,style:{removeProperty(key){delete this[key]}}};
const document={documentElement:{clientWidth:1920}},matchMedia=()=>({matches:wide});
const $=key=>key==='#diaryComposeOpen'?button:key==='#diaryFilterOpen'?{getBoundingClientRect:()=>rect}:{classList:{contains:()=>active}};
FUNCTION
syncDiaryComposePosition();assert.equal(button.style.right,'277px');
document.documentElement.clientWidth=1440;rect={left:1180,width:38};
syncDiaryComposePosition();assert.equal(button.style.right,'217px');
// A scrollbar can move the header without changing the full viewport width.
rect.left=1172;syncDiaryComposePosition();assert.equal(button.style.right,'225px');
wide=false;syncDiaryComposePosition();assert.equal(button.style.right,undefined);
wide=true;active=false;syncDiaryComposePosition();assert.equal(button.style.right,undefined);
'''.replace('FUNCTION', 'function syncDiaryComposePosition(){'+code)
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_desktop_navigation_does_not_duplicate_existing_controls(self):
        self.assertNotIn('class="bottom-nav"', self.page)
        self.assertEqual(self.page.count('id="mainNavPage"'), 1)
        for control in ("chatBack", "previewBtn", "menuBtn", "input", "plusBtn", "sendBtn", "todoInput"):
            self.assertEqual(self.page.count(f'id="{control}"'), 1)
        self.assertNotIn(".bottom-nav", self.desktop)

    def test_desktop_settings_keep_navigation_beside_the_active_panel(self):
        self.assertIn("#view-settings.active{grid-template-columns:230px minmax(0,1fr);", self.desktop)
        self.assertIn("#view-settings>#settings-home{grid-column:1;grid-row:1;position:sticky;", self.desktop)
        self.assertIn("#view-settings>.set-panel{grid-column:2;grid-row:1;}", self.desktop)
        self.assertIn("#view-settings .set-panel .set-back{display:none;}", self.desktop)
        self.assertIn('if(!name&&desktopSettingsQuery.matches)name="prefs";', self.page)
        self.assertIn('keepHome&&el.id==="settings-home"', self.page)
        self.assertIn('button.setAttribute("aria-current","page")', self.page)

    def test_settings_keep_one_instance_and_respond_to_viewport_changes(self):
        self.assertEqual(self.page.count('id="settings-home"'), 1)
        self.assertEqual(self.page.count('class="set-panel"'), 9)
        self.assertIn('desktopSettingsQuery.addEventListener("change"', self.page)
        self.assertIn('showSetPanel(current?current.id.replace("set-panel-",""):null);', self.page)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_attachment_animation_preserves_desktop_horizontal_centering(self):
        code = self.page.split('function togglePlus(show){', 1)[1].split('let plusHeld=', 1)[0]
        script = r'''
const assert=require('node:assert/strict');
const plusPop={hidden:true},plusBtn={classList:{toggle(){}}};
let plusPopAnimation=null,plusBottomSpace=false,plusSpaceAnimation=null,frames;
const composerEl={animate(value){frames=value;return {cancel(){}}}};
const log={scrollHeight:500,scrollTop:450,clientHeight:50},$=()=>log;
const matchMedia=()=>({matches:false}),showPlusPage=()=>{},closeStickerPopover=()=>{},syncComposerSpace=()=>{};
const plusPopFlowHeight=()=>240,settlePlusBottomSpace=()=>{};
FUNCTION
togglePlus(true);assert.equal(plusPop.hidden,false);
assert.deepEqual(frames,[{translate:'0 240px'},{translate:'0 0'}]);
assert.ok(frames.every(frame=>!('transform' in frame)));
plusPopAnimation.onfinish();togglePlus(false);
assert.deepEqual(frames,[{translate:'0 0'},{translate:'0 240px'}]);
plusPopAnimation.onfinish();assert.equal(plusPop.hidden,true);
'''.replace('FUNCTION', 'function togglePlus(show){'+code)
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        for control in ('wallClose', 'memoryMenuBtn', 'settingsNavBtn'):
            button = self.page.split(f'id="{control}"', 1)[1].split('</button>', 1)[0]
            self.assertIn('aria-label="Back to Home"', button)
            self.assertIn('m14 6-6 6 6 6', button)
            self.assertIn(f'$("#{control}").onclick=()=>navigateMain("chat")', self.page)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_persistent_navigation_restores_input_and_resizes_without_a_modal(self):
        code = self.page.split('function syncDesktopNavigation(){', 1)[1].split('window.matchMedia("(min-width:900px)").addEventListener', 1)[0]
        script = r'''
const assert=require('node:assert/strict');
function element(){const classes=new Set();return {inert:false,attrs:{},classList:{contains:k=>classes.has(k),
 toggle(k,v){v?classes.add(k):classes.delete(k)},remove(...keys){keys.forEach(k=>classes.delete(k))}},
 setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]}}}
const body=element(),page=element(),chat=element();chat.inert=true;
let wide=true,active='chat',refreshes=0,route={level:1,view:'chat',mainNav:true};
const window={matchMedia:()=>({matches:wide})},document={body,querySelector:()=>({id:'view-'+active})};
const $=()=>page,currentAppRoute=()=>route,history={replaceState:value=>route=value};
let mainNavInert=[chat];const requestAnimationFrame=fn=>fn(),autoGrow=()=>{},syncComposerSpace=()=>{};
const renderMainNavigationIdentity=()=>{},refreshMainNavigationOverview=()=>refreshes++;
FUNCTION
syncDesktopNavigation();
assert.equal(chat.inert,false);assert.equal(page.inert,false);assert.equal(route.mainNav,true);
assert.equal(route.level,1); // Layout refreshes must never rewrite navigation history.
assert.equal(body.classList.contains('desktop-navigation'),true);assert.equal(page.attrs['aria-modal'],undefined);
active='notes';syncDesktopNavigation();assert.equal(body.classList.contains('desktop-navigation'),true);
assert.equal(refreshes,1);
active='settings';syncDesktopNavigation();assert.equal(body.classList.contains('desktop-navigation'),false);
assert.equal(page.inert,true);assert.equal(page.attrs.role,'dialog');
active='chat';syncDesktopNavigation();wide=false;syncDesktopNavigation();
assert.equal(page.inert,true);assert.equal(body.classList.contains('desktop-navigation'),false);
'''.replace('FUNCTION', 'function syncDesktopNavigation(){'+code)
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('body.desktop-navigation #view-chat{left:var(--desktop-nav-width);transform:none;', self.page)
        self.assertIn('body.desktop-navigation .main-nav-chat-return{display:none;}', self.page)
        self.assertEqual(self.page.count('id="btnPhone"'), 1)
        self.assertIn('$("#btnPhone").onclick=()=>toast("开发中，待开放")', self.page)


if __name__ == "__main__":
    unittest.main()
