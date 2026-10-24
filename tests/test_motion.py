import unittest
from pathlib import Path


class MotionContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path("static/index.html").read_text(encoding="utf-8")

    def test_chat_menu_animates_from_button_and_cleans_up_on_close(self):
        self.assertIn('@keyframes chat-menu-in', self.page)
        self.assertIn('transform-origin:calc(100% - 18px) 0', self.page)
        self.assertIn('popup.inert=true', self.page)
        self.assertIn('motion.finished.then(()=>popup.remove(),()=>popup.remove())', self.page)
        self.assertIn('if(reducedMotion){popup.remove();return;}', self.page)
        self.assertIn('.menu-pop{animation:none!important;}', self.page)
        self.assertIn('e.key==="Escape"&&menuPop', self.page)

    def test_settings_drilldown_uses_single_page_fade(self):
        self.assertIn("function transitionSettingsLayer(stage,current,next,duration=190)", self.page)
        self.assertIn('{opacity:0},{opacity:1}', self.page)
        self.assertNotIn('translateX(calc(100% + 18px))', self.page)
        self.assertNotIn('.settings-cover-layer', self.page)

    def test_provider_and_mcp_editors_share_the_cover_motion(self):
        self.assertIn('transitionSettingsLayer($("#set-panel-model"),list,edit,180)', self.page)
        self.assertIn('transitionSettingsLayer($("#set-panel-mcp"),list,edit,180)', self.page)
        self.assertIn('transitionSettingsLayer($("#set-panel-model"),current,list,175)', self.page)
        self.assertIn('transitionSettingsLayer($("#set-panel-mcp"),current,list,175)', self.page)

    def test_nested_settings_stages_do_not_clip_cards_vertically(self):
        selector = '#set-panel-model:not([hidden]),#set-panel-mcp:not([hidden])'
        self.assertIn(selector + '{display:grid;grid-template-columns:minmax(0,1fr);align-content:start;overflow:visible;}', self.page)
        self.assertNotIn(selector + '{display:grid;grid-template-columns:minmax(0,1fr);align-content:start;overflow-x:hidden;}', self.page)

    def test_tidy_drawer_extends_behind_floating_navigation(self):
        self.assertIn('.tidy-drawer-mask{position:fixed;z-index:18;\n      inset:0;', self.page)
        self.assertIn('.tidy-drawer{position:fixed;z-index:19;left:0;top:0;bottom:0;', self.page)
        self.assertIn('.bottom-nav{position:absolute;', self.page)
        self.assertIn('transition:transform .25s var(--ease);z-index:20;', self.page)

    def test_two_way_pills_use_a_sliding_indicator(self):
        self.assertIn('.mem-tabs::before', self.page)
        self.assertIn('--pill-x', self.page)
        self.assertIn('syncSlidingPill("#memTabs")', self.page)
        self.assertIn('syncSlidingPill("#mcpTabs")', self.page)
        self.assertIn('requestAnimationFrame(()=>syncSlidingPill("#mcpTabs"))', self.page)

    def test_shared_sheet_supports_spring_entry_and_drag_dismiss(self):
        self.assertIn('@keyframes sheetPullIn', self.page)
        self.assertIn('sheetGrip.addEventListener("pointerdown"', self.page)
        self.assertIn('addEventListener("pointermove",moveSheetDrag', self.page)
        self.assertIn('velocity>.55', self.page)
        self.assertIn('height*.28', self.page)

    def test_reasoning_header_and_body_animate_independently(self):
        self.assertIn('@keyframes thoughtHalo', self.page)
        self.assertIn('function toggleReasoning(rb)', self.page)
        self.assertIn('const animation=body.animate', self.page)
        self.assertIn('head.classList.add("thought-pulse")', self.page)
        self.assertIn('&&!rb.classList.contains("collapsed"))toggleReasoning(rb)', self.page)

    def test_reduced_motion_disables_decorative_motion(self):
        self.assertIn('@media (prefers-reduced-motion:reduce)', self.page)
        self.assertIn('if(reducedMotion){rb.classList.toggle("collapsed");return;}', self.page)

    def test_composer_sheet_uses_its_real_flow_height(self):
        self.assertIn('function plusPopFlowHeight()', self.page)
        self.assertIn('plusPop.offsetHeight+(parseFloat(style.marginTop)||0)+(parseFloat(style.marginBottom)||0)', self.page)
        self.assertNotIn('plusPop.offsetHeight+8', self.page)

    def test_study_note_collapse_has_no_padding_tail(self):
        self.assertIn('.study-wake-body{padding:0 2px;}', self.page)
        self.assertIn('.study-wake-actions{position:absolute;z-index:2;right:7px;bottom:5px;', self.page)

    def test_plus_workflow_stays_open_until_an_attachment_is_added(self):
        self.assertIn('plusFilePickers.forEach(picker=>picker.onclick=e=>e.stopPropagation())', self.page)
        self.assertIn('if(imageLimitHit)toast("一次最多选 9 张图片");inp.value="";if(added)togglePlus(false);', self.page)
        self.assertIn('e.target.closest?.("#stickerPopover,#stickerManager,#appDialogMask,#appDialogCard")', self.page)
        self.assertIn('plusFilePickers.includes(e.target)', self.page)

    def test_sticker_editor_uses_compact_paper_form_styling(self):
        self.assertIn('<span class="sticker-form-label">✦ 名字</span>', self.page)
        self.assertIn('<span class="sticker-form-label">✦ 画面</span>', self.page)
        self.assertIn('<span class="sticker-form-label">✦ 表达状态</span>', self.page)
        self.assertIn('<span class="sticker-form-label">✦ 专属</span>', self.page)
        self.assertIn('class="sticker-detail-pair"', self.page)
        self.assertIn('id="stickerDescription" rows="3"', self.page)
        self.assertIn('id="stickerExpression" rows="3"', self.page)
        self.assertIn('.sticker-form #stickerFormSubmit{justify-self:center;min-width:132px;', self.page)
        self.assertNotIn('NAME <small>名字</small>', self.page)

    def test_private_paper_objects_share_a_soft_material_language(self):
        self.assertIn('.diary-detail-panel{background:var(--diary-detail-bg);', self.page)
        self.assertIn('.diary-paper{position:relative;width:min(520px,calc(100% - 16px));', self.page)
        self.assertIn('box-shadow:inset 13px 0 18px -17px rgba(84,61,49,.62)', self.page)
        self.assertIn('box-shadow:inset 14px 0 19px -17px rgba(0,0,0,.78)', self.page)
        self.assertIn('<div class="study-door-slip">', self.page)
        self.assertIn('.study-note-stamp.sealed svg{animation:studyPawSeal', self.page)
        self.assertIn('class="horizon-stamp" aria-hidden="true"><svg class="postcard-art"', self.page)
        for design in ('light', 'leaf', 'tomorrow'):
            self.assertEqual(self.page.count('id="postcard-'+design+'"'), 1)

    def test_footprint_keeps_original_room_style_with_plain_monologues(self):
        self.assertIn('.study-monologue-event{padding:13px 15px 14px 18px;', self.page)
        self.assertIn('background:linear-gradient(145deg,var(--foot-card),var(--foot-card-soft));', self.page)
        self.assertIn('.footprint-recap{margin-top:14px;padding:10px 12px;border-left:2px solid var(--paper-mark);', self.page)
        self.assertIn('.footprint-recap-label{display:block;margin-bottom:1px;font-family:"EB Garamond"', self.page)
        self.assertIn('label.textContent="by the pillow"', self.page)
        self.assertIn('function plainStudyMonologue(value){', self.page)
        self.assertIn('text.textContent=plainStudyMonologue(event.text)', self.page)
        self.assertIn('.replace(/\\*([^*]+)\\*/g,"$1")', self.page)
        self.assertNotIn('.study-assistant-scale-stamp{', self.page)

    def test_tool_ledger_keeps_narration_above_function_and_seals_only_closed_runs(self):
        self.assertIn('const completed=run.items.every(t=>t.run_complete!==false)', self.page)
        self.assertIn('!completed?(tidyNarration(run.items.at(-1)?.narrate,run.items.at(-1)?.source)', self.page)
        self.assertIn('narrations.length===1?narrations[0]', self.page)
        self.assertIn('assistantName()+"在这一轮做了几件事"', self.page)
        self.assertIn('if(completed&&!hasError)', self.page)
        self.assertIn('method.textContent=t.tool', self.page)

    def test_chat_drawers_take_one_mobile_back_step_before_leaving_chat(self):
        self.assertIn('function pushChatDrawerRoute(drawer){', self.page)
        self.assertIn('history.pushState({...route,level:route.level+1,drawer}', self.page)
        self.assertIn('if(!e.state.drawer)hideAllChatDrawers();', self.page)
        self.assertIn('closeChatDrawerRoute("preview",hidePreview)', self.page)
        self.assertIn('closeChatDrawerRoute("history",hideHist)', self.page)
        self.assertIn('closeChatDrawerRoute("favorites",hideFav)', self.page)
        self.assertIn('closeChatDrawerRoute("settings",hideChatSettings)', self.page)

    def test_paper_details_have_tape_spine_and_edge_trim(self):
        self.assertNotIn('.study-door-slip::after{content:"";', self.page)
        self.assertIn('.study-note-stamp{display:grid;place-items:center;width:38px;height:38px;padding:0;border:0;', self.page)
        self.assertIn('background:transparent;color:var(--accent-strong);opacity:.78;box-shadow:none;', self.page)
        self.assertIn('box-shadow:inset 13px 0 18px -17px rgba(84,61,49,.62)', self.page)
        self.assertIn('.diary-paper::before{content:"";position:absolute;left:0;top:0;bottom:0;width:18px;', self.page)
        self.assertIn('--diary-detail-bg:#f1e7d9;', self.page)
        self.assertIn('background:color-mix(in srgb,var(--diary-detail-bg) 88%,transparent)', self.page)
        self.assertIn('-webkit-mask:radial-gradient(circle at 0 6px,transparent 0 3px,#000 3.5px)', self.page)
        self.assertIn('.study-wake-actions{position:absolute;z-index:2;right:7px;bottom:5px;', self.page)

    def test_ai_footer_time_follows_the_selected_reply_version(self):
        self.assertIn('fmtTime(wrap._ats[wrap._vi]||wrap._ts)', self.page)
        self.assertIn('const originalAt=wrap._ats[wrap._vi]||wrap._ts;', self.page)
        self.assertIn('wrap._models.push("已编辑");wrap._ats.push(originalAt);', self.page)


if __name__ == "__main__":
    unittest.main()
