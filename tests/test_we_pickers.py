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
        self.assertIn('<div class="we-calendar-panel" id="weDatePanel" hidden>', source)
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
    def test_calendar_and_picker_interaction(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        code = source[source.index('let weCalendarYear='):source.index('function tickClock()')]
        script = """
const assert=require('node:assert/strict');
function node(){return {value:'',dataset:{},children:[],attrs:{},setAttribute(key,value){this.attrs[key]=value;},
  replaceChildren(){this.children=[];},appendChild(value){this.children.push(value);},
  showModal(){this.open=true;},close(){this.open=false;},querySelector(){return node();}};}
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,node());return nodes.get(key);};
const document={createElement:()=>node()};
let chosen=null,opened=0,closed=0;
const openChoiceSheet=options=>{chosen=options;},openContentSheet=options=>{opened++;assert.equal(options.content,$('#weDatePanel'));};
const closeSheet=()=>closed++;
$('#greetingForm').oninput=()=>{$('#greetingModeText').textContent=$('#greetingMode').value==='random'?'随机':'按时段';};
const calendarDate=()=> '2026-10-11';let previews=0;const previewTogether=()=>previews++;
"""+code+"""
assert.equal(weCalendarCells(2024,1).filter(Boolean).length,29);
assert.equal(weCalendarCells(2023,1).filter(Boolean).length,28);
assert.equal(weCalendarCells(1,0).filter(Boolean)[0].value,'0001-01-01');
assert.equal(weCalendarCells(2026,9).filter(Boolean)[10].disabled,false);
assert.equal(weCalendarCells(2026,9).filter(Boolean)[11].disabled,true);
$('#weAssistantPronoun').value='她';$('#wePronounOpen').onclick();
assert.equal(chosen.value,'她');assert.deepEqual(chosen.items.map(item=>item.value),['他','她','ta']);
chosen.onChoose('ta');assert.equal($('#weAssistantPronoun').value,'ta');
assert.equal($('#wePronounText').textContent,'ta');
$('#greetingMode').value='timed';$('#greetingModeOpen').onclick();assert.equal(chosen.value,'timed');
chosen.onChoose('random');assert.equal($('#greetingMode').value,'random');assert.equal($('#greetingModeText').textContent,'随机');
$('#togetherDate').value='2024-02-28';$('#weDateOpen').onclick();
assert.equal(opened,1);
const leap=$('#weDateGrid').children.find(item=>item.attrs['aria-label']==='2024-02-29');
leap.onclick();assert.equal($('#togetherDate').value,'2024-02-29');
assert.equal(previews,1);assert.equal(closed,1);
$('#weDateOpen').onclick();assert.equal(opened,2);
assert.equal($('#weDateGrid').children.find(item=>item.attrs['aria-label']==='2024-02-29').attrs['aria-pressed'],'true');
$('#weDateClose').onclick();assert.equal(closed,2);
weCalendarYear=2024;weCalendarMonth=0;moveWeCalendar(-1);assert.equal(weCalendarYear,2023);assert.equal(weCalendarMonth,11);
weCalendarYear=1;weCalendarMonth=0;moveWeCalendar(-1);assert.equal(weCalendarYear,1);
weCalendarYear=9999;weCalendarMonth=11;moveWeCalendar(1);assert.equal(weCalendarYear,9999);
$('#weDateYear').value='0';$('#weDateYear').onchange();assert.equal(weCalendarYear,9999);
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
