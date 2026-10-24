import unittest
from pathlib import Path


class DailyChatStyleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path("static/index.html").read_text(encoding="utf-8")

    def test_single_preference_controls_the_complete_daily_style(self):
        daily = self.page.index('id="tgDailyChat"')
        push = self.page.index('id="pushPrefsCard"')
        self.assertLess(daily, push)
        self.assertNotIn('id="tgNook"', self.page)
        self.assertIn('localStorage.getItem("prefNook")==="1"', self.page)
        self.assertIn('document.body.classList.toggle("ai-bubble",daily)', self.page)
        self.assertIn('document.body.classList.toggle("daily-chat",daily)', self.page)
        self.assertIn('localStorage.setItem("prefDailyChat",e.target.checked?"1":"0")', self.page)

    def test_daily_messages_are_grouped_and_quiet(self):
        self.assertIn('body.daily-chat .message-ai .ai-part:last-of-type{border-radius:17px 17px 17px 4px;}', self.page)
        self.assertIn('body.daily-chat .bubble-user.user-bubble-stack .user-part:last-child{border-radius:17px 17px 4px 17px;}', self.page)
        self.assertIn('body.daily-chat .ai-actions>button{display:none;}', self.page)
        self.assertIn("attachLongPress(wrap.querySelector(\".message-ai\"),()=>openAISheet(wrap),()=>document.body.classList.contains(\"daily-chat\"));", self.page)
        self.assertIn("daily?'想了一会儿'", self.page)
        self.assertIn('body.daily-chat .message-ai,body.daily-chat .message-ai .ai-part{-webkit-user-select:none;user-select:none;}', self.page)
        self.assertIn('body.daily-chat .ai-actions .vswitch{margin-left:auto;gap:0;font-size:10px;', self.page)
        self.assertIn('body.daily-chat .bubble-user.user-bubble-stack{gap:5px;max-width:84%;padding:0;border:0;background:transparent;', self.page)
        self.assertIn('box-shadow:none;backdrop-filter:none;-webkit-backdrop-filter:none;', self.page)
        self.assertIn('.sheet.daily-message-sheet button{gap:9px;padding:10px 12px;font-size:13px;', self.page)
        self.assertIn('body.daily-chat .msg.flash .bubble-user.user-bubble-stack,', self.page)
        self.assertIn('body.daily-chat .msg.flash .bubble-user.user-bubble-stack .user-part,', self.page)
        self.assertIn('body.daily-chat .msg.flash .message-ai .ai-part{animation:flash 1.4s var(--ease);}', self.page)

    def test_long_press_menu_absorbs_the_old_inline_actions_in_order(self):
        start = self.page.index("function openAISheet(wrap){")
        end = self.page.index("async function addFav", start)
        menu = self.page[start:end]
        regenerate = menu.index('label:"重新生成"')
        copy = menu.index('label:"复制"')
        edit = menu.index('label:"编辑"')
        keepsake = menu.index('label:kept?"已留影":"留影"')
        branch = menu.index('label:"分支"')
        delete = menu.index('label:"删除"')
        self.assertLess(regenerate, copy)
        self.assertLess(copy, edit)
        self.assertLess(edit, keepsake)
        self.assertLess(keepsake, branch)
        self.assertLess(branch, delete)
        self.assertIn('if(daily&&isLastAI(wrap))items.push', menu)
        self.assertIn('if(daily)items.push({label:"复制"', menu)
        self.assertIn('sheet.classList.toggle("daily-message-sheet",daily)', menu)

    def test_default_chat_keeps_selection_and_external_actions(self):
        self.assertIn('.message-ai,.message-ai *,', self.page)
        self.assertIn('-webkit-user-select:text;user-select:text;', self.page)
        self.assertIn('if(!enabled())return;e.preventDefault();fn();', self.page)
        self.assertIn('bar.appendChild(mk("复制"', self.page)
        self.assertIn('bar.appendChild(mk("更多"', self.page)

    def test_header_keeps_title_data_but_shows_nook_and_model_in_daily_mode(self):
        self.assertIn('class="name" id="chatTitle"', self.page)
        self.assertIn('class="daily-chat-nook">Morrow</div>', self.page)
        self.assertIn('id="dailyModelSwitch"', self.page)
        self.assertIn('if(daily)daily.textContent=name', self.page)
        self.assertIn('$("#dailyModelSwitch").onclick=openActiveModelPicker', self.page)
        self.assertIn('body.daily-chat .chat-head .name{display:none;}', self.page)

    def test_daily_composer_is_solid_single_row_without_duplicate_controls(self):
        self.assertEqual(self.page.count('id="input"'), 1)
        self.assertEqual(self.page.count('id="plusBtn"'), 1)
        self.assertEqual(self.page.count('id="sendBtn"'), 1)
        self.assertIn('body.daily-chat .composer-card{display:flex;align-items:stretch;', self.page)
        self.assertIn('body.daily-chat .composer-bottom>.model-switch{display:none;}', self.page)
        self.assertIn('class="daily-send-heart"', self.page)
        self.assertNotIn('class="daily-send-label">发送</span>', self.page)
        self.assertIn('body.daily-chat .composer-actions{height:100%;', self.page)
        self.assertIn('body.daily-chat .plus-pop{width:calc(100% + 20px);', self.page)
        self.assertIn('document.body.classList.contains("daily-chat")?8:28', self.page)

    def test_send_is_locked_before_attachment_preparation_finishes(self):
        send_start = self.page.index("async function send(){")
        send_end = self.page.index("async function regenerate", send_start)
        send_body = self.page[send_start:send_end]
        self.assertLess(send_body.index("if(sending||streaming)return;"), send_body.index("await uploadAttachments"))
        self.assertLess(send_body.index("sending=true;setSendPreparing(true);"), send_body.index("await uploadAttachments"))
        self.assertIn("finally{\n    sending=false;setSendPreparing(false);", send_body)
        self.assertIn('b.classList.toggle("preparing",on);b.disabled=on;', self.page)

    def test_user_sticker_stays_right_anchored_below_photo_layouts(self):
        self.assertIn('.user-media>.message-sticker{margin:12px 0 0 auto;}', self.page)
        self.assertIn('.user-media>.message-sticker:first-child{margin-top:0;}', self.page)
        self.assertIn('.deck-collapse[hidden]{display:inline-flex;visibility:hidden;pointer-events:none;}', self.page)

    def test_fullscreen_editor_has_a_quiet_text_only_done_action(self):
        self.assertNotIn('id="fsCancel"', self.page)
        self.assertNotIn('<span class="t">写长一点</span>', self.page)
        self.assertIn('<button class="fs-done" id="fsDone">完成</button>', self.page)
        self.assertIn('.fs-done{border:0;background:none;', self.page)

    def test_every_chat_style_uses_the_solid_topbar_status_color(self):
        self.assertIn('if(m&&$("#view-chat")?.classList.contains("active"))', self.page)
        self.assertIn('getPropertyValue("--surface-raised")', self.page)

    def test_daily_input_lifts_without_moving_actions(self):
        self.assertIn('box-shadow:inset 0 1px 4px color-mix(in srgb,var(--shadow) 54%,transparent);transform:translateY(-2px);', self.page)

    def test_existing_right_header_actions_are_unchanged(self):
        self.assertIn('id="previewBtn" aria-label="消息预览"', self.page)
        self.assertIn('id="menuBtn" aria-label="更多"', self.page)

    def test_chat_tool_visibility_list_uses_the_same_soft_expand_motion(self):
        self.assertIn('.chat-tool-list{display:grid;gap:2px;max-height:0;', self.page)
        self.assertIn('.chat-tool-list.open{max-height:520px;margin-top:11px;opacity:1;', self.page)
        self.assertIn('open=!list.classList.contains("open")', self.page)
        self.assertIn('requestAnimationFrame(()=>list.classList.toggle("open",open))', self.page)

    def test_daily_dividers_mark_cross_day_or_four_hour_pauses_only(self):
        self.assertIn('const CHAT_PAUSE_MS=4*60*60*1000;', self.page)
        self.assertIn('crossedDay||longPause', self.page)
        self.assertIn('if(!document.body.classList.contains("daily-chat"))return;', self.page)
        self.assertIn('divider.className="daily-time-divider"', self.page)
        self.assertIn('body.daily-chat .daily-time-divider{display:flex;}', self.page)
        self.assertIn('body.daily-chat .user-time{display:none!important;}', self.page)

    def test_daily_avatar_controls_and_tails_are_scoped_to_daily_mode(self):
        self.assertIn('id="dailyAvatarPrefs" hidden', self.page)
        self.assertIn('id="tgDailyAvatars"', self.page)
        self.assertIn('id="dailyUserAvatarFile"', self.page)
        self.assertIn('id="dailyAssistantAvatarFile"', self.page)
        self.assertIn('id="dailyAvatarActions" hidden', self.page)
        self.assertIn('data-kind="user"', self.page)
        self.assertIn('data-kind="assistant"', self.page)
        self.assertIn('document.body.classList.toggle("daily-avatars",avatars)', self.page)
        self.assertIn('const daily=prefDailyChat(),avatars=daily&&prefDailyAvatars();', self.page)
        self.assertIn('body.daily-chat.daily-avatars .message-ai .ai-part:first-of-type{border-radius:4px 17px 17px 17px;}', self.page)
        self.assertIn('body.daily-chat.daily-avatars .bubble-user.user-bubble-stack .user-part:first-child{border-radius:17px 4px 17px 17px;}', self.page)
        self.assertIn('background-color:#c9e3ec', self.page)
        self.assertIn('background-color:#efd3dc', self.page)

    def test_avatar_layout_adjustments_only_apply_with_daily_avatars(self):
        self.assertIn('body.daily-chat.daily-avatars .reasoning{margin-left:42px;}', self.page)
        self.assertIn('body.daily-chat.daily-avatars .ai-actions{padding-left:42px;}', self.page)
        self.assertIn('body.daily-chat.daily-avatars .user-media{margin-right:38px;}', self.page)
        self.assertIn('body.daily-chat .user-actions .vswitch{gap:0;font-size:10px;line-height:1;}', self.page)

    def test_avatar_setting_copy_and_lightweight_picker(self):
        self.assertIn('换成更像平常聊天的样子', self.page)
        self.assertIn('在每轮对话开始时露个脸', self.page)
        self.assertIn('>选择图片</button>', self.page)
        self.assertIn('>恢复默认</button>', self.page)
        self.assertNotIn('id="dailyUserAvatarPick"', self.page)
        self.assertIn('clip-path:circle(50% at 50% 50%)', self.page)
        self.assertIn('.daily-avatar-pair{display:flex;align-items:center;justify-content:center;', self.page)
        self.assertIn('.daily-avatar-actions{display:flex;align-items:center;justify-content:center;', self.page)
        self.assertIn('.daily-avatar-choice[data-kind="user"] small{right:calc(100% + 6px);', self.page)
        self.assertIn('.daily-avatar-choice[data-kind="assistant"] small{left:calc(100% + 6px);', self.page)
        self.assertIn('body.daily-chat .reasoning{margin:0 4px 0;padding:0;border:0;}', self.page)
        self.assertIn('body.daily-chat .reasoning.collapsed + .message-ai{margin-top:-6px;}', self.page)
        self.assertIn('.daily-avatar-prefs.open{max-height:260px;opacity:1;transform:none;', self.page)
        self.assertIn('.daily-avatar-uploads.open{max-height:140px;opacity:1;transform:none;', self.page)
        self.assertIn('.daily-avatar-actions.open{max-height:34px;margin-top:10px;opacity:1;', self.page)
        self.assertIn('clip-path:circle(50% at 50% 50%)', self.page)
        self.assertIn('!event.target.closest(".daily-avatar-choice,#dailyAvatarActions")', self.page)

    def test_daily_avatar_images_are_device_preferences_and_backed_up(self):
        self.assertIn('localStorage.setItem(kind==="user"?"dailyUserAvatar":"dailyAssistantAvatar"', self.page)
        self.assertIn('"prefDailyAvatars","dailyUserAvatar","dailyAssistantAvatar"', self.page)


if __name__ == "__main__":
    unittest.main()
