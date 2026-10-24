import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


class SceneParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.stack = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.setdefault(attrs['id'], []).append((tag, attrs))
        if tag not in ('input', 'meta', 'link', 'br', 'hr', 'img', 'path'):
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())


class AuthPaperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path('static/index.html').read_text(encoding='utf-8')
        cls.scenes = cls.page.split('  <div id="bootScreen"', 1)[1].split('  <div id="app">', 1)[0]
        cls.parser = SceneParser()
        cls.parser.feed('<div id="bootScreen"' + cls.scenes)

    def test_auth_controls_remain_unique_and_accessible(self):
        for control in ('bootScreen', 'login', 'loginTitle', 'pw', 'pwToggle', 'loginBtn', 'loginErr'):
            self.assertEqual(len(self.parser.ids[control]), 1)
        _, password = self.parser.ids['pw'][0]
        self.assertEqual(password['type'], 'password')
        self.assertEqual(password['autocomplete'], 'current-password')
        self.assertEqual(password['aria-describedby'], 'loginErr')
        self.assertEqual(password['aria-label'], 'Your passphrase')
        self.assertEqual(self.parser.ids['loginErr'][0][1]['aria-live'], 'polite')
        self.assertIn('<span>Come in</span>', self.scenes)

    def test_entry_scenes_are_english_and_keep_the_welcome_heart(self):
        self.assertNotRegex(self.scenes, r'[\u4e00-\u9fff]')
        self.assertIn('A little room for today.', self.scenes)
        self.assertIn('Just a moment', self.scenes)
        self.assertIn('Welcome home', self.scenes)
        self.assertIn('class="login-heart"', self.scenes)
        self.assertNotIn('line-assistant', self.scenes)
        # The heart must remain outside the paper that fades out on success.
        self.assertIn('#login.success .login-inner{opacity:0;', self.page)
        self.assertIn('@keyframes draw-home-heart', self.page)
        self.assertIn('hasUserName()?"Welcome home,', self.page)

    def test_theme_gradient_and_loading_animation_have_motion_fallback(self):
        css = self.page.split('/* ===== paper desk:', 1)[1].split('/* ===== bottom sheet', 1)[0]
        self.assertIn('linear-gradient(90deg,var(--surface-raised)', css)
        self.assertIn('background:var(--paper)', css)
        self.assertIn('animation:paper-corner-lift 2.2s', css)
        reduced = css.split('@media (prefers-reduced-motion:reduce)', 1)[1]
        self.assertIn('.loading-paper-corner,#login.wrong .pw-row{animation:none!important;}', reduced)
        self.assertIn('stroke-dashoffset:0', reduced)

    def test_letter_ornaments_are_local_and_noninteractive(self):
        self.assertIn('A LETTER TO YOURSELF', self.scenes)
        self.assertNotIn('login-whisper', self.scenes)
        self.assertNotIn('login-sprig', self.scenes)
        self.assertNotIn('MADE FOR LITTLE MOMENTS', self.scenes)
        self.assertIn('class="login-colophon">JUST FOR YOU</div>', self.scenes)
        for decoration in ('login-postmark', 'login-flourish'):
            self.assertRegex(self.scenes, r'<svg class="login-decoration ' + decoration + r'"[^>]*focusable="false"')
        self.assertIn('class="login-footer" aria-hidden="true"', self.scenes)
        self.assertNotIn('<img', self.scenes)
        self.assertNotIn('<image', self.scenes)
        css = self.page.split('/* ===== paper desk:', 1)[1].split('/* ===== bottom sheet', 1)[0]
        self.assertIn('.login-inner::before,.login-inner::after', css)
        self.assertIn('color:var(--accent-strong);opacity:.5;pointer-events:none;', css)

    def test_wordmark_uses_bundled_calligraphy_and_button_has_theme_fill(self):
        self.assertTrue(Path('static/fonts/allura-latin-400-normal.woff2').read_bytes().startswith(b'wOF2'))
        self.assertIn('SIL OPEN FONT LICENSE', Path('static/fonts/Allura-OFL.txt').read_text(encoding='utf-8'))
        self.assertIn('"Allura","Segoe Script",cursive', self.page)
        self.assertIn('src:url("/static/fonts/allura-latin-400-normal.woff2")', self.page)
        self.assertIn('background:color-mix(in srgb,var(--accent-strong) 65%,var(--text-primary));color:var(--surface)', self.page)
        flourish = re.search(r'<svg class="login-decoration login-flourish"(.*?)</svg>', self.scenes, re.S).group(1)
        self.assertIn('viewBox="0 0 12 16"', flourish)
        self.assertEqual(flourish.count('<path'), 1)
        self.assertIn('fill:currentColor;stroke:none;opacity:1;', self.page)

    def test_passphrase_visibility_defaults_to_hidden_and_is_accessible(self):
        _, toggle = self.parser.ids['pwToggle'][0]
        self.assertEqual(toggle['type'], 'button')
        self.assertEqual(toggle['aria-controls'], 'pw')
        self.assertEqual(toggle['aria-label'], 'Show passphrase')
        self.assertEqual(toggle['aria-pressed'], 'false')
        self.assertIn('class="eye-closed"', self.scenes)
        self.assertIn('class="eye-open"', self.scenes)
        self.assertIn('input.type=visible?"text":"password"', self.page)
        self.assertIn('$("#pwToggle").onclick=()=>setPassphraseVisible($("#pw").type==="password")', self.page)
        self.assertIn('async function showLogin(){\n  setPassphraseVisible(false);', self.page)

    def test_paper_uses_a_local_faint_texture_and_leaf_has_no_cancel_lines(self):
        self.assertIn('/static/paper-grain.svg', self.page)
        grain = Path('static/paper-grain.svg').read_text(encoding='utf-8')
        self.assertIn('feTurbulence', grain)
        self.assertIn('slope=".045"', grain)
        self.assertNotIn('href=', grain)
        stamp = re.search(r'<svg class="login-decoration login-postmark"(.*?)</svg>', self.scenes, re.S).group(1)
        self.assertNotIn('h17', stamp)
        self.assertEqual(stamp.count('<path'), 1)

    def test_auth_still_verifies_on_server_and_only_then_shows_welcome(self):
        auth = self.page.split('/* ---------- auth ---------- */', 1)[1].split('$("#logoutBtn")', 1)[0]
        self.assertIn('api("/api/login",{method:"POST",body:JSON.stringify({password:$("#pw").value})})', auth)
        self.assertIn('if(r.ok){setPassphraseVisible(false);$("#pw").value="";$("#pw").blur();login.classList.add("success");await enterApp(true);return;}', auth)
        self.assertIn('if(e.key==="Enter")doLogin()', auth)
        self.assertIn('button.disabled=true', auth)
        self.assertIn('Unable to connect. Please try again.', auth)
        self.assertNotRegex(auth, r'[\u4e00-\u9fff]')
        dismiss = re.search(r'async function dismissBoot\(\)\{(.*?)\n\}', auth, re.S).group(1)
        self.assertNotIn('await pause', dismiss)


if __name__ == '__main__':
    unittest.main()
