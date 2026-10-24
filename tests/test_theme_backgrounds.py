"""Offline checks for theme wallpaper defaults and upload precedence."""
from pathlib import Path
import shutil
import struct
import subprocess
import unittest


class ThemeBackgroundTest(unittest.TestCase):
    def test_packaged_backgrounds_and_layer_styles(self):
        for name in ('afternoon', 'new-leaf', 'letter'):
            data = Path('static/backgrounds/'+name+'-v1.png').read_bytes()
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', data[16:24])
            self.assertGreaterEqual(width, 1024)
            self.assertGreater(height, width)
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn('filter:var(--app-bg-filter,none)', source)
        self.assertIn('filter:var(--chat-bg-filter,none)', source)
        prefs = source[source.index('function applyPrefs()'):source.index('function syncPrefUI()')]
        self.assertIn('applyAppBg();', prefs)
        self.assertIn('applyChatBg();', prefs)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_switch_night_mode_uploads_and_restore(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        helpers = source[source.index('const THEME_BACKGROUNDS='):source.index('function syncSystemBars(')]
        app_bg = source[source.index('function applyAppBg()'):source.index('$("#appBgPick").onclick')]
        chat_bg = source[source.index('function chatBgOpacity()'):source.index('async function compressChatBg(')]
        script = """
const assert=require('node:assert/strict');
const values=new Map(),localStorage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
const properties=new Map(),document={documentElement:{style:{setProperty:(key,value)=>properties.set(key,value)}}};
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{});return nodes.get(key);};
const syncRangeFill=()=>{};
"""+helpers+app_bg+chat_bg+"""
function refresh(){applyAppBg();applyChatBg();}
for(const [theme,image] of Object.entries(THEME_BACKGROUNDS)){
  values.set('prefTheme',theme);values.set('appBgOpacity','13');values.set('chatBgOpacity','21');refresh();
  for(const area of ['app','chat']){
    assert.equal(properties.get('--'+area+'-bg-image'),'url("'+image+'")');
    assert.equal(properties.get('--'+area+'-bg-opacity'),area==='app'?'0.8':'0.5');
    assert.equal(properties.get('--'+area+'-bg-filter'),'none');
  }
  assert.equal($('#appBgOpacityBox').hidden,true);assert.equal($('#chatBgOpacityBox').hidden,true);
  values.set('prefNight','1');refresh();
  assert.equal(properties.get('--app-bg-opacity'),'0.8');
  assert.ok(properties.get('--app-bg-filter').includes('brightness'));
  assert.ok(properties.get('--chat-bg-filter').includes('brightness'));
  values.delete('prefNight');
}
values.set('prefTheme','unknown');refresh();
assert.equal(properties.get('--app-bg-image'),'url("'+THEME_BACKGROUNDS.paper+'")');
values.set('appBgImage','data:image/png;base64,synthetic-app');
values.set('chatBgImage','data:image/png;base64,synthetic-chat');
values.set('prefNight','1');refresh();
assert.equal(properties.get('--app-bg-opacity'),'0.13');assert.equal(properties.get('--chat-bg-opacity'),'0.21');
assert.equal(properties.get('--app-bg-filter'),'none');assert.equal(properties.get('--chat-bg-filter'),'none');
assert.equal($('#appBgClear').hidden,false);assert.equal($('#chatBgClear').hidden,false);
values.delete('appBgImage');refresh();
assert.equal(properties.get('--app-bg-opacity'),'0.8');
assert.equal(properties.get('--chat-bg-opacity'),'0.21');
values.delete('chatBgImage');values.set('prefTheme','green');refresh();
assert.equal(properties.get('--chat-bg-image'),'url("'+THEME_BACKGROUNDS.green+'")');
assert.equal(properties.get('--chat-bg-opacity'),'0.5');
assert.equal($('#chatBgText').textContent,'使用主题底图');
assert.equal(values.get('appBgOpacity'),'13');assert.equal(values.get('chatBgOpacity'),'21');
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
