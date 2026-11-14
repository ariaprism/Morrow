"""Offline checks for generated theme backgrounds and upload precedence."""
from pathlib import Path
import shutil
import subprocess
import unittest


class ThemeBackgroundTest(unittest.TestCase):
    def test_generated_backgrounds_and_optional_image_layers(self):
        source = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('ellipse at 50% 42%,color-mix(in srgb,var(--accent) 32%,transparent)', source)
        self.assertIn('#view-chat{background:var(--surface);}', source)
        self.assertIn('#app.has-custom-background{background:var(--surface);}', source)
        self.assertNotIn('#app,#view-chat{background:', source)
        self.assertIn("background-image:var(--app-bg-image,none)", source)
        self.assertIn("background-image:var(--chat-bg-image,none)", source)
        self.assertNotIn("THEME_BACKGROUNDS", source)
        self.assertNotIn("function themeBackground()", source)
        self.assertIn("--surface:#fbf8f3; --surface-raised:#fffcf8;", source)
        self.assertIn("--accent:#b98283;", source)
        self.assertIn("--accent-strong:#92545b;", source)
        self.assertNotIn('id="themeChips"', source)
        self.assertIn('const t=night?"dark":"paper";', source)

    @unittest.skipUnless(shutil.which("node"), "Node is optional")
    def test_uploads_opacity_and_restore_to_generated_background(self):
        source = Path("static/index.html").read_text(encoding="utf-8")
        app_opacity = source[source.index("function appBgOpacity()"):source.index("function syncSystemBars(")]
        app_bg = source[source.index("function applyAppBg()"):source.index('$("#appBgPick").onclick')]
        chat_bg = source[source.index("function chatBgOpacity()"):source.index("async function compressChatBg(")]
        script = """
const assert=require('node:assert/strict');
const values=new Map(),localStorage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
const properties=new Map(),document={documentElement:{style:{setProperty:(key,value)=>properties.set(key,value)}}};
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{classList:{toggle(name,value){this[name]=value}}});return nodes.get(key);};
const syncRangeFill=()=>{};
"""+app_opacity+app_bg+chat_bg+"""
function refresh(){applyAppBg();applyChatBg();}
values.set('appBgOpacity','13');values.set('chatBgOpacity','21');refresh();
for(const area of ['app','chat']){
  assert.equal(properties.get('--'+area+'-bg-image'),'none');
  assert.equal(properties.get('--'+area+'-bg-opacity'),'0');
  assert.equal(properties.get('--'+area+'-bg-filter'),'none');
}
assert.equal($('#appBgOpacityBox').hidden,true);assert.equal($('#chatBgOpacityBox').hidden,true);
assert.equal($('#chatBgText').textContent,'Solid color');
assert.equal($('#app').classList['has-custom-background'],false);
values.set('appBgImage','data:image/png;base64,synthetic-app');
values.set('chatBgImage','data:image/png;base64,synthetic-chat');refresh();
assert.equal(properties.get('--app-bg-image'),'url("data:image/png;base64,synthetic-app")');
assert.equal(properties.get('--chat-bg-image'),'url("data:image/png;base64,synthetic-chat")');
assert.equal(properties.get('--app-bg-opacity'),'0.13');assert.equal(properties.get('--chat-bg-opacity'),'0.21');
assert.equal($('#appBgClear').hidden,false);assert.equal($('#chatBgClear').hidden,false);
assert.equal($('#app').classList['has-custom-background'],true);
values.delete('appBgImage');refresh();
assert.equal(properties.get('--app-bg-image'),'none');assert.equal(properties.get('--app-bg-opacity'),'0');
assert.equal($('#app').classList['has-custom-background'],false);
assert.equal(properties.get('--chat-bg-opacity'),'0.21');
values.delete('chatBgImage');refresh();
assert.equal(properties.get('--chat-bg-image'),'none');assert.equal(properties.get('--chat-bg-opacity'),'0');
assert.equal($('#chatBgText').textContent,'Solid color');
assert.equal(values.get('appBgOpacity'),'13');assert.equal(values.get('chatBgOpacity'),'21');
"""
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
