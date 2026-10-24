"""Renaming a display label must not rewrite stored diary tags or defaults."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DiaryTagLabelTest(unittest.TestCase):
    def test_filter_and_compose_labels_keep_original_values(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn('<button data-tag="dive"><i class="tag-dive"></i>thoughts</button>', source)
        self.assertIn('<button class="tag-dive" data-tag="dive">thoughts</button>', source)
        self.assertNotIn('>deep</button>', source)
        self.assertIn('diaryDraftTag="light"', source)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_old_stored_tags_render_new_label(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        start = source.index('const DIARY_TAGS=')
        mapping = source[start:source.index('\n', start)]
        start = source.index('function diaryTagMarkup(')
        render = source[start:source.index('\n', start)]
        script = "const assert=require('node:assert/strict'),escHtml=value=>value;\n"+mapping+render+"""
assert.equal(diaryTagMarkup('dive'),'<span class="diary-tag tag-dive">thoughts</span>');
assert.ok(diaryTagMarkup('light').endsWith('>light</span>'));
assert.ok(diaryTagMarkup('anchor').endsWith('>anchor</span>'));
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
