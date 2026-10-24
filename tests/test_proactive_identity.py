"""Proactive longing label uses the AI pronoun, not its display name."""
from pathlib import Path
import shutil
import subprocess
import unittest


class ProactiveIdentityTest(unittest.TestCase):
    def test_initial_markup_and_identity_refresh(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn('id="wantAssistant" type="button" aria-pressed="false">想他</button>', source)
        self.assertNotIn('"想Morrow"', source)
        identity = source[source.index('function renderIdentity()'):source.index('async function loadIdentity()')]
        self.assertIn('if(typeof renderProactiveSummary==="function")renderProactiveSummary();', identity)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_all_pronouns_and_pending_and_disabled_states(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        identity = source[source.index('let IDENTITY='):source.index('async function loadIdentity()')]
        summary = source[source.index('function renderProactiveSummary()'):source.index('$("#proactiveContext").onclick')]
        script = """
const assert=require('node:assert/strict'),nodes=new Map();
const $=key=>{if(!nodes.has(key))nodes.set(key,{classList:{toggle(){}},setAttribute(name,value){this[name]=value;}});return nodes.get(key);};
const $$=()=>[],proactiveJobLabel=()=> 'synthetic';
let PROACTIVE_STATE={enabled:true,job:{wanted:false},unread_count:0};
"""+identity+summary+"""
IDENTITY.assistant_name='A fictional name';
for(const pronoun of ['他','她','ta']){
  IDENTITY.assistant_pronoun=pronoun;renderProactiveSummary();
  assert.equal($('#wantAssistant').textContent,'想'+pronoun);
  assert.equal($('#wantAssistant').disabled,false);
  assert.equal($('#wantAssistant')['aria-pressed'],'false');
  PROACTIVE_STATE.job.wanted=true;renderProactiveSummary();
  assert.equal($('#wantAssistant').textContent,'想念放好了');
  assert.equal($('#wantAssistant')['aria-pressed'],'true');
  PROACTIVE_STATE.job.wanted=false;
}
PROACTIVE_STATE.enabled=false;renderProactiveSummary();assert.equal($('#wantAssistant').disabled,true);
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
