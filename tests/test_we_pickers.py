"""Offline tests for themed We controls and calendar boundaries."""
from pathlib import Path
import shutil
import subprocess
import unittest


class WePickerTest(unittest.TestCase):
    def test_no_browser_pronoun_or_date_popups(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn('<input type="hidden" id="weAssistantPronoun"', source)
        self.assertIn('<input type="hidden" id="togetherDate">', source)
        self.assertIn('<div class="we-wheel-panel" id="weDatePanel" hidden>', source)
        self.assertIn('<input type="hidden" id="greetingMode"', source)
        self.assertNotIn('id="wePronounDialog"', source)
        self.assertNotIn('id="weDateDialog"', source)
        self.assertIn('openContentSheet({title:"从哪一天开始",content:weDatePanel})', source)
        self.assertIn('openChoiceSheet({title:"如何称呼 ta"', source)
        self.assertIn('openChoiceSheet({title:"问候轮换方式"', source)
        self.assertIn('appearance:none;-webkit-appearance:none;', source)
        self.assertIn('$("#wePronounText").textContent=assistantPronoun()', source)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_shared_sheet_content_survives_model_and_calendar_switches(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        code = source[source.index('const sheet=$("#sheet")'):source.index('sheetMask.onclick=closeSheet;')]
        script = """
const assert=require('node:assert/strict'),reducedMotion=true;
const setTimeout=()=>0,clearTimeout=()=>{};
function element(){return {children:[],parts:new Map(),style:{removeProperty(){}},
  classList:{values:new Set(),add(...keys){keys.forEach(key=>this.values.add(key));},remove(...keys){keys.forEach(key=>this.values.delete(key));},
    contains(key){return this.values.has(key);},toggle(key,value){if(value)this.add(key);else this.remove(key);}},
  set innerHTML(value){this.children.forEach(child=>child.parent=null);this.children=[];},
  appendChild(child){child.remove();child.parent=this;this.children.push(child);},
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}},
  querySelector(key){const child=this.children.find(item=>'.'+item.className===key);if(child)return child;
    if(!this.parts.has(key))this.parts.set(key,element());return this.parts.get(key);}};}
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,element());return nodes.get(key);};
const document={createElement:()=>element()};
"""+code+"""
const content=element();content.hidden=true;content.year='2024';
openContentSheet({title:'Date',content});
assert.equal(content.hidden,false);assert.equal(content.parent,sheetBody);
assert.ok(sheet.classList.contains('on'));assert.ok(sheet.classList.contains('choice-sheet'));
closeSheet();let picked='';
openChoiceSheet({title:'Models',items:[{value:'fictional-model',label:'Fictional'}],onChoose:value=>picked=value});
assert.equal(content.parent,null);
sheetBody.querySelector('.sheet-choice-list').children[0].onclick();
assert.equal(picked,'fictional-model');assert.equal(sheet.classList.contains('on'),false);
openContentSheet({title:'Date',content});
assert.equal(content.parent,sheetBody);assert.equal(content.year,'2024');
assert.equal(sheetBody.children.some(child=>child.className==='sheet-choice-list'),false);
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_wheel_dates_and_draft_confirmation(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        code = source[source.index('let weWheelDraft='):source.index('function tickClock()')]
        script = r"""
const assert=require('node:assert/strict');
function element(){return {value:'',dataset:{},style:{},children:[],attrs:{},scrollTop:0,
 setAttribute(k,v){this.attrs[k]=v},addEventListener(){},replaceChildren(){this.children=[]},
 appendChild(child){if(child.fragment)this.children.push(...child.children);else this.children.push(child)},
 scrollTo({top}){this.scrollTop=top}}}
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,element());return nodes.get(key)};
const document={createElement:element,createDocumentFragment:()=>({...element(),fragment:true})};
const calendarDate=()=> '2026-10-11',reducedMotion=true;
const requestAnimationFrame=fn=>{fn();return 1},clearTimeout=()=>{};
let closed=0,previews=0,chosen;
const closeSheet=()=>closed++,previewTogether=()=>previews++;
const openContentSheet=()=>{},openChoiceSheet=value=>chosen=value;
"""+code+r"""
assert.equal(weDaysInMonth(2024,2),29);
assert.equal(weDaysInMonth(1900,2),28);
assert.equal(weDaysInMonth(2000,2),29);
assert.deepEqual(clampWeDate([2023,2,29]),[2023,2,28]);
assert.deepEqual(clampWeDate([2024,4,31]),[2024,4,30]);
assert.deepEqual(clampWeDate([2027,12,31]),[2026,10,11]);
assert.deepEqual(clampWeDate([1,1,1]),[1,1,1]);
assert.deepEqual(weWheelLimits([2026,10,11]),[2026,10,11]);
assert.deepEqual(weWheelLimits([2024,2,29]),[2026,12,29]);
$('#togetherDate').value='2024-02-29';$('#weDateOpen').onclick();
assert.equal($('#weWheelDay').children.length,29);
assert.equal($('#weWheelDay').attrs['aria-valuenow'],'29');
$('#weWheelYear').scrollTop=(2023-1)*44;settleWeWheel(weWheelStates[0]);
assert.deepEqual(weWheelDraft,[2023,2,28]);
assert.equal($('#weWheelDay').children.length,28);
assert.equal($('#togetherDate').value,'2024-02-29');
$('#weDateClose').onclick();assert.equal(previews,0);
$('#weDateOpen').onclick();$('#weWheelDay').scrollTop=27*44;
$('#weDateDone').onclick();assert.equal($('#togetherDate').value,'2024-02-28');
assert.equal(previews,1);assert.equal(closed,2);
$('#togetherDate').value='2026-10-01';$('#weDateOpen').onclick();
$('#weWheelDay').onkeydown({key:'End',preventDefault(){}});
$('#weDateDone').onclick();assert.equal($('#togetherDate').value,'2026-10-11');
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('scroll-snap-type:y mandatory', source)
        self.assertIn('role="spinbutton"', source)
