"""Diary comment names share the other comment surfaces' identity rules."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DiaryIdentityTest(unittest.TestCase):
    def test_both_comment_surfaces_support_live_identity_updates(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn('<span class="who" data-identity-comment-author=', source)
        self.assertIn('<div class="diary-paper-comment-head"><strong data-identity-comment-author=', source)
        self.assertGreaterEqual(source.count('escHtml(DIARY_COMMENT_EMOJIS[comment.author]||"·")'), 4)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_defaults_partial_names_and_explicit_brand_name(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        identity = source[source.index('let IDENTITY='):source.index('async function loadIdentity()')]
        start = source.index('const DIARY_COMMENT_EMOJIS=')
        getters = source[start:source.index('\n', start)]
        script = "const assert=require('node:assert/strict');\n" + identity + getters + """
assert.equal(DIARY_COMMENT_EMOJIS.user,'我');assert.equal(DIARY_COMMENT_EMOJIS.assistant,'他');
IDENTITY.user_name='June <3';assert.equal(DIARY_COMMENT_EMOJIS.user,'June <3');assert.equal(DIARY_COMMENT_EMOJIS.assistant,'他');
IDENTITY.assistant_name='Fern';assert.equal(DIARY_COMMENT_EMOJIS.assistant,'Fern');
IDENTITY.user_name='你';assert.equal(DIARY_COMMENT_EMOJIS.user,'我');
IDENTITY.assistant_name='Morrow';IDENTITY.assistant_name_set=true;
assert.equal(DIARY_COMMENT_EMOJIS.assistant,'Morrow');
IDENTITY.assistant_name_set=false;assert.equal(DIARY_COMMENT_EMOJIS.assistant,'他');
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
