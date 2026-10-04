import unittest
from pathlib import Path


class NavigationHistoryContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path("static/index.html").read_text(encoding="utf-8")

    def test_android_back_uses_a_home_rooted_history_stack(self):
        self.assertIn('history.replaceState(appRoute(),"")', self.page)
        self.assertIn('history.pushState(appRoute(1,name),"")', self.page)
        self.assertIn('addEventListener("popstate"', self.page)
        self.assertIn('if(route.level>0)history.go(-route.level)', self.page)

    def test_diary_detail_is_a_real_back_navigation_level(self):
        self.assertIn('async function navigateDiaryDetail(entryId)', self.page)
        self.assertIn('history.pushState(appRoute(2,"diary",{detail:{type:"diary-entry",id:entryId}}),"")', self.page)
        self.assertIn('if(route.detail?.type==="diary-entry")void openDiaryDetail(route.detail.id);', self.page)
        self.assertIn('$("#diaryDetailBack").onclick=()=>backToAppLevel(1,showDiaryFeed);', self.page)

    def test_settings_drilldowns_have_their_own_history_levels(self):
        self.assertIn('history.pushState(appRoute(2,"settings",{panel:name}),"")', self.page)
        self.assertIn('history.pushState(appRoute(3,"settings",{panel:detail.panel,detail}),"")', self.page)
        self.assertIn('navigateSettingsDetail({type:"provider",panel:"model"', self.page)
        self.assertIn('navigateSettingsDetail({type:"mcp",panel:"mcp"', self.page)
        self.assertIn('navigateSettingsDetail({type:"assistant-prompt",panel:"assistant"', self.page)

    def test_data_and_assistant_polish_contract(self):
        self.assertIn('id="githubBackupToggle"', self.page)
        self.assertIn('aria-expanded="false"', self.page)
        self.assertIn('$("#githubBackupToggle").onclick=', self.page)
        self.assertNotIn('id="cloudSyncToggle"', self.page)
        self.assertEqual(self.page.count('<span>PROMPT</span>'), 3)
        self.assertIn('letter-spacing:.08em;color:var(--text-secondary)', self.page)
        self.assertIn('p?"编辑提供商":"添加供应商"', self.page)

    def test_preview_search_starts_at_and_highlights_the_match(self):
        self.assertIn('if(at>0)txt.append("…")', self.page)
        self.assertIn('document.createElement("mark")', self.page)
        self.assertIn('txt.append(mark,message.slice(at+q.length))', self.page)

    def test_context_limit_slider_is_collapsed_away_from_scroll_gestures(self):
        self.assertIn('id="ctxLimitOpen" type="button" aria-expanded="false"', self.page)
        self.assertIn('id="ctxLimitPanel"', self.page)
        self.assertIn('.ctx-limit-panel{display:grid;grid-template-rows:0fr;', self.page)
        self.assertIn('$("#ctxLimitOpen").onclick=', self.page)


if __name__ == "__main__":
    unittest.main()
