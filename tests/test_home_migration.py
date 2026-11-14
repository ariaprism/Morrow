import shutil
import subprocess
import unittest
from pathlib import Path


class HomeMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path('static/index.html').read_text(encoding='utf-8')

    def test_home_contents_have_one_new_location_each(self):
        page = self.page
        for old in ('view-home', 'homePostcard', 'clock', 'assistantStatus', 'stickyNote'):
            self.assertNotIn(f'id="{old}"', page)
        self.assertNotIn('data-view="home"', page)
        nav = page.split('id="mainNavPage"', 1)[1].split('</aside>', 1)[0]
        for control in ('greeting', 'mainNavPostcard', 'nightLamp'):
            self.assertEqual(page.count(f'id="{control}"'), 1)
            self.assertIn(f'id="{control}"', nav)
        self.assertIn('data-view="notes"', nav)
        notes = page.split('id="view-notes"', 1)[1].split('</section>', 1)[0]
        self.assertIn('id="noteWall"', notes)
        self.assertIn('id="wallCalendarGrid"', notes)
        self.assertIn('$("#wallClose").onclick=()=>navigateMain("chat");', page)
        chat = page.split('id="view-chat"', 1)[1].split('<!-- 记忆', 1)[0]
        for control in ('todoBtn', 'todoCount', 'todoPanel', 'todoList', 'todoInput', 'todoDoneList'):
            self.assertEqual(page.count(f'id="{control}"'), 1)
            self.assertIn(f'id="{control}"', chat)
        self.assertIn('aria-labelledby="todoPanelTitle" hidden inert', chat)
        self.assertIn('pushChatDrawerRoute("todos")', page)
        self.assertIn('hideMoments();hideTodos();closeMenu();', page)

    def run_js(self, script):
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_task_count_mutations_failure_and_panel_draft(self):
        page = self.page
        panel = page[page.index('let todoReturnFocus='):page.index('async function loadTodos()')]
        load = page[page.index('async function loadTodos()'):page.index('function todoEl(')]
        toggle = page[page.index('async function toggleTodo('):page.index('let todoDeleteTarget=')]
        add = page[page.index('$("#todoInput").addEventListener'):page.index('$("#todoDoneHead").onclick=')]
        script = r'''
const assert=require('node:assert/strict');
class El{
 constructor(){this.dataset={};this.style={};this.hidden=false;this.value='';this.children=[];this.handlers={};this.attrs={};
   const classes=new Set();this.classList={contains:n=>classes.has(n),toggle:(n,on)=>{on=on??!classes.has(n);on?classes.add(n):classes.delete(n);return on},remove:n=>classes.delete(n)};}
 set innerHTML(value){this.children=[];this.html=value} get innerHTML(){return this.html}
 appendChild(el){this.children.push(el)} setAttribute(k,v){this.attrs[k]=v} addEventListener(k,fn){this.handlers[k]=fn} focus(){this.focused=true}
}
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,new El());return nodes.get(key)};
$('#todoPanel').hidden=true;
let tasks=[{id:1,text:'A walk',done:false},{id:2,text:'Water plants',done:false},{id:3,text:'Read',done:true}],fail=false,pushes=0;
const timers=[],setTimeout=fn=>timers.push(fn),toast=()=>{},todoEl=t=>({id:t.id});
const document={activeElement:{isConnected:true,focus(){}}},requestAnimationFrame=fn=>fn();
const closeTodoDelete=()=>{},pushChatDrawerRoute=name=>{assert.equal(name,'todos');pushes++},closeChatDrawerRoute=(name,hide)=>{assert.equal(name,'todos');hide()};
const api=async(path,options={})=>{
 if(fail)return {ok:false};
 if(options.method==='POST')tasks.push({id:4,text:JSON.parse(options.body).text,done:false});
 if(options.method==='PUT')tasks.find(t=>t.id===Number(path.split('/').at(-1))).done=JSON.parse(options.body).done;
 return {ok:true,json:async()=>tasks.map(t=>({...t}))};
};
PANEL
LOAD
TOGGLE
ADD
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await loadTodos();assert.equal($('#todoCount').textContent,'2');assert.equal($('#todoCount').hidden,true);
 assert.deepEqual($('#todoList').children.map(t=>t.id),[1,2]);assert.deepEqual($('#todoDoneList').children.map(t=>t.id),[3]);
 $('#todoInput').value='Keep this draft';openTodos();await settle();openTodos();assert.equal(pushes,1);assert.equal($('#todoPanel').inert,false);
 closeTodos();assert.equal($('#todoPanel').hidden,true);assert.equal($('#todoInput').value,'Keep this draft');
 const input=$('#todoInput'),event={key:'Enter',target:input,preventDefault(){}};
 fail=true;await input.handlers.keydown(event);assert.equal(input.value,'Keep this draft');assert.equal(input.disabled,false);
 fail=false;input.value='Stretch';await input.handlers.keydown(event);assert.equal(input.value,'');assert.equal($('#todoCount').textContent,'3');
 const task={id:1,text:'A walk',done:false},el=new El();el.setAttribute('aria-checked','false');
 fail=true;await toggleTodo(task,el);assert.equal(task.done,false);assert.equal(el.classList.contains('done'),false);assert.equal(el.attrs['aria-checked'],'false');
 fail=false;await toggleTodo(task,el);assert.equal(task.done,true);assert.equal(el.attrs['aria-checked'],'true');await timers.shift()();
 assert.equal($('#todoCount').textContent,'2');
 fail=true;assert.equal(await loadTodos(),false);assert.equal($('#todoCount').textContent,'2');assert.ok($('#todoStatus').textContent);
 fail=false;tasks=tasks.map(t=>({...t,done:true}));await loadTodos();assert.equal($('#todoCount').hidden,true);
 const count=tasks.length;input.value='Composing';await input.handlers.keydown({...event,isComposing:true});assert.equal(tasks.length,count);assert.equal(input.value,'Composing');
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('PANEL', panel).replace('LOAD', load).replace('TOGGLE', toggle).replace('ADD', add)
        self.run_js(script)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_notes_reader_keeps_safe_text_and_removes_pin_choice(self):
        self.assertNotIn('function setDisplayedNote(', self.page)
        self.assertNotIn('Pin note', self.page)
        self.assertIn('card.querySelector(".wn-body").textContent=String(note.content||"")', self.page)
        self.assertIn('$("#noteReaderBody").textContent=String(note.content||"")', self.page)
        self.assertIn('id="notesEntryPreview"', self.page)
        self.assertIn('noteReaderInert.forEach(e=>e.inert=false)', self.page)


if __name__ == '__main__':
    unittest.main()
