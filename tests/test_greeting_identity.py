"""Offline regression for default and customized greeting names."""
from pathlib import Path
import shutil
import subprocess
import unittest


class GreetingIdentityTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_default_and_custom_name_rendering(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        identity_code = source[source.index('let IDENTITY='):source.index('async function loadIdentity()')]
        greeting_code = source[source.index('const GREET='):source.index('async function loadVisit()')]
        script = """
const assert=require('node:assert/strict');
const nodes=new Map();
const $=key=>{if(!nodes.has(key))nodes.set(key,{setAttribute(){},classList:{toggle(name,value){this[name]=value;}},querySelector:()=>$('#avatarLabel')});return nodes.get(key);};
const pronounLabel={dataset:{assistantPronoun:'{ta}的轮廓'},tagName:'SPAN'};
const userComment={dataset:{identityCommentAuthor:'user'},setAttribute(){}};
const aiComment={dataset:{identityCommentAuthor:'assistant'},setAttribute(){}};
const closedSummary={},openSummary={};
const document={querySelectorAll:selector=>selector==='[data-assistant-pronoun]'?[pronounLabel]:selector==='[data-identity-comment-author]'?[userComment,aiComment]:selector==='.boundary-closed-label'?[closedSummary]:selector==='.boundary-open-label'?[openSummary]:[],querySelector:key=>$(key)};
""" + identity_code + greeting_code + """
currentGreeting='晚上好。';renderIdentity();
assert.equal($('.login-welcome').textContent,'Welcome home');
assert.equal(userComment.textContent,'我');assert.equal(aiComment.textContent,'他');
assert.equal($('#weAssistantPronoun').value,'他');
assert.equal(pronounLabel.textContent,'他的轮廓');
for(const pronoun of ['她','ta','他']){
  IDENTITY.assistant_pronoun=pronoun;renderIdentity();
  assert.equal(pronounLabel.textContent,pronoun+'的轮廓');
  assert.equal($('#avatarLabel').textContent,pronoun);
}
assert.equal($('#assistantStatus').textContent,'Morrow在想你');
assert.equal($('#personaIdentityMark').textContent,'M');
assert.equal($('#greeting').textContent,'晚上好。');
assert.equal(greetingText('早上好。'),'早上好。');
assert.equal($('#ttsScript').placeholder,'[softly] 没事的……我陪着你。');
IDENTITY.user_name='June <3';renderIdentity();
IDENTITY.assistant_name='Fern <3';renderIdentity();
assert.ok($('.login-welcome').textContent.includes('June <3'));
assert.equal(userComment.textContent,'June <3');assert.equal(aiComment.textContent,'Fern <3');
assert.equal(closedSummary.textContent,'Fern <3把前面的记录折进小本本了');
assert.equal(openSummary.textContent,'—— Fern <3把前面的记录折进小本本了 ——');
assert.equal($('#diaryReplyIdentityLabel').textContent,'June <3的留言');
assert.equal($('#keepsakeReplyIdentityLabel').textContent,'June <3的留言');
assert.equal($('#stickerAudience [data-value="user"]').textContent,'June <3');
assert.equal($('#stickerAudience [data-value="assistant"]').textContent,'Fern <3');
assert.equal($('#assistantStatus').textContent,'Fern <3在想你');
assert.equal($('#personaIdentityMark').textContent,'F');
assert.equal($('.daily-chat-nook').classList['chinese-name'],false);
IDENTITY.assistant_name='林';renderIdentity();
assert.equal($('#personaIdentityMark').textContent,'M');
assert.equal($('.daily-chat-nook').classList['chinese-name'],true);
assert.equal($('.note-nook-stamp span').classList['chinese-name'],true);
assert.equal($('#greeting').textContent,'晚上好。June <3');
assert.equal(greetingText('早上好。'),'早上好。June <3');
assert.equal($('#ttsScript').placeholder,'[softly] 没事的，June <3……我陪着你。');
for(const line of ['今天想聊什么？','想说什么都可以。','今天过得怎么样？'])assert.equal(greetingText(line),line);
for(const line of ['早上好。','下午好。','晚上好。','欢迎回来。'])assert.equal(greetingText(line),line+'June <3');
assert.deepEqual(GREET.back,['欢迎回来。']);
assert.equal(GREET.bits,undefined);
IDENTITY.user_name='lyre';assert.equal(greetingText('早上好。'),'早上好。lyre');
GREETING_PREFS={...defaultGreetingPrefs(),morning:'早上好。\\nHello {你的名字}',common:'A shared thought'};
assert.equal(greetingText('早上好。'),'早上好。');
assert.equal(greetingText('Hello {你的名字}'),'Hello lyre');
assert.deepEqual(greetingPool(GREETING_PREFS,7,true),['早上好。','Hello {你的名字}','A shared thought']);
assert.deepEqual(greetingPool(GREETING_PREFS,2),['A shared thought']);
assert.deepEqual(greetingPool({...GREETING_PREFS,mode:'random'},7),['A shared thought']);
for(const [hour,bucket] of [[5,'dawn'],[9,'morning'],[12,'noon'],[18,'evening'],[23,'night'],[0,'night']])assert.equal(greetBucket(hour),bucket);
assert.deepEqual(greetingPool(defaultGreetingPrefs(),7,true),['欢迎回来。']);
GREETING_PREFS=defaultGreetingPrefs();
assert.equal($('#ttsScript').value,undefined);
IDENTITY.user_name='你';renderIdentity();
assert.equal($('.login-welcome').textContent,'Welcome home');
IDENTITY.assistant_name='Morrow';IDENTITY.assistant_name_set=true;
assert.equal(commentAuthorName('assistant'),'Morrow');
IDENTITY.assistant_name_set=false;
assert.equal(commentAuthorName('assistant'),'他');
assert.equal($('#greeting').textContent,'晚上好。');
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixed_chat_copy_and_chinese_signature_fonts(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn('const EMPTY_LINES=[["你来啦",""]];', source)
        self.assertIn('const COMPOSER_HINTS=["What\'s on your mind... ","A little thought...","Begin anywhere..."];', source)
        self.assertIn('input.placeholder=pickOne(COMPOSER_HINTS)', source)
        self.assertIn('<textarea class="box persona-box" id="personaBox"></textarea>', source)
        self.assertNotIn("I'm listening", source)
        self.assertNotIn('🦊 的留言', source)
        self.assertNotIn('Welcome home,<br>Aria.', source)
        self.assertIn('author.textContent=commentAuthorName(comment.author)', source)
        self.assertIn('who.textContent=commentAuthorName(comment.author)', source)
        self.assertIn('<button class="we-save-button" type="submit">保存日期</button>', source)
        self.assertIn('<button class="we-save-button" type="submit">保存问候语</button>', source)
        self.assertIn('#memoryWePanel .we-save-button{justify-self:center;width:auto;', source)
        self.assertIn('@media (hover:none),(pointer:coarse){textarea{resize:none!important;}}', source)
        self.assertIn('.daily-chat-nook{font:600 22px/1 "Caveat"', source)
        self.assertIn('.daily-chat-nook.chinese-name{font:400 18px/1.3 "LXGW WenKai Screen"', source)
        self.assertIn('.note-nook-stamp span.chinese-name{font-family:"LXGW WenKai Screen",serif;font-size:14px;}', source)
        for selector in ('.note-nook-stamp span{', '.daily-chat-nook{'):
            rule = source.split(selector, 1)[1].split('}', 1)[0]
            self.assertIn('"Caveat","LXGW WenKai Screen"', rule)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_greeting_editor_preview_insert_save_and_reset(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        rules = source[source.index('const GREET='):source.index('async function loadVisit()')]
        editor = source[source.index('function readGreetingForm()'):source.index('let homeWeatherText=')]
        script = """
const assert=require('node:assert/strict');
const nodes=new Map(),button={disabled:false};
const $=key=>{if(!nodes.has(key))nodes.set(key,{value:'',selectionStart:0,selectionEnd:0,
querySelector:()=>button,focus(){},setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);}});return nodes.get(key);};
const greetingName=()=> 'lyre',toast=()=>{};
let rejectSave=false;
const api=async(url,options)=>({ok:!rejectSave,json:async()=>rejectSave?{error:'Synthetic failure'}:JSON.parse(options.body)});
""" + rules + editor + """
;(async()=>{
renderGreetingForm();
$('#greetingMode').value='random';$('#greetingForm').oninput();
assert.equal($('#greetingTimedFields').hidden,true);
const area=$('#greetingCommon');area.value='Hello ';area.selectionStart=6;area.selectionEnd=6;
area.onfocus();$('#greetingInsertName').onclick();
assert.equal(area.value,'Hello {你的名字}');
assert.equal($('#greetingPreview').textContent,'Hello lyre');
assert.equal(GREETING_PREFS.common,'');
await $('#greetingForm').onsubmit({preventDefault(){},target:$('#greetingForm')});
assert.equal($('#greeting').textContent,'Hello lyre');
assert.equal(GREETING_PREFS.common,'Hello {你的名字}');
assert.equal(button.disabled,false);
rejectSave=true;area.value='Unsaved draft';
await $('#greetingForm').onsubmit({preventDefault(){},target:$('#greetingForm')});
assert.equal(GREETING_PREFS.common,'Hello {你的名字}');
assert.equal($('#greeting').textContent,'Hello lyre');
assert.equal(button.disabled,false);
$('#greetingReset').onclick();
assert.equal($('#greetingMode').value,'timed');assert.equal(area.value,'');
assert.equal(GREETING_PREFS.common,'Hello {你的名字}');
rejectSave=false;
await $('#greetingForm').onsubmit({preventDefault(){},target:$('#greetingForm')});
assert.deepEqual(GREETING_PREFS,defaultGreetingPrefs());
})().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_greeting_pool_has_no_private_defaults(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        greeting_code = source[source.index('const GREET='):source.index('async function loadVisit()')]
        for old in ('过来。', '夜宵', '……还不睡', '你知道过了多久', '泡泡里晒太阳', 'GREET.bits'):
            self.assertNotIn(old, greeting_code)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_keepsake_and_music_render_actual_author_names(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        identity_code = source[source.index('let IDENTITY='):source.index('async function loadIdentity()')]
        start = source.index('function renderMusicNotes(')
        music_code = source[start:source.index('\n', start)]
        start = source.index('function renderKeepsakeComments(')
        keepsake_code = source[start:source.index('\nfunction ', start + 1)]
        script = """
const assert=require('node:assert/strict');
function element(){return {dataset:{},children:[],parts:new Map(),attributes:{},set innerHTML(value){this.children=[];},
querySelector(key){if(!this.parts.has(key))this.parts.set(key,element());return this.parts.get(key);},
setAttribute(key,value){this.attributes[key]=value;},appendChild(child){this.children.push(child);}};}
const document={createElement:()=>element()},box=element(),$=()=>box;
""" + identity_code + music_code + keepsake_code + """
const comments=[{author:'user',content:'Fictional note'},{author:'assistant',content:'Fictional reply'}];
function check(expected){
renderMusicNotes(comments);
assert.deepEqual(box.children.map(row=>row.querySelector('strong').textContent),expected);
const page=element();renderKeepsakeComments(page,{comments});
assert.deepEqual(page.children[0].children.map(row=>row.querySelector('strong').textContent),expected);
}
check(['我','他']);
IDENTITY.user_name='June <3';IDENTITY.assistant_name='Fern <3';check(['June <3','Fern <3']);
IDENTITY.user_name='你';IDENTITY.assistant_name='Morrow';IDENTITY.assistant_name_set=true;IDENTITY.user_name_set=true;check(['我','Morrow']);
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
