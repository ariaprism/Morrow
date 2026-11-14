# Configuration details

Display names can be edited under Memory → We without a first-run wizard. The default assistant name is Morrow. Names are stored in the existing settings table; saved conversations and persona text are not rewritten.

Chat is the default Home view. On desktop the navigation stays on the left, with a rounded chat paper above the shared oatmeal background; other sections use the right-hand content area. Settings remains a separate page. On mobile, navigation opens below the chat sheet. Notes, Memory, and the main Settings heading return to Home.

Portrait uploads live in We. Preferences controls whether portraits appear in Daily chat, and offers System, Serif, and Sans chat fonts. Without an uploaded portrait, navigation and Daily chat use matching solid circles and name initials; an unset user name displays as You in navigation. Paper chat remains available by turning Daily chat off.

The welcome screen says “Welcome home” until authentication loads a saved user name. Compression boundary captions follow the assistant name. Keepsake and song comments use saved display names, or “我” / “他” when names have not been set. Explicitly saving the default assistant name Morrow still counts as choosing that name. Display labels update without rewriting comment content.

We also offers the assistant's display pronoun (`他`, `她`, or `ta`), defaulting to `他`. Chinese signatures use LXGW WenKai alongside Caveat for Latin characters. The preview note says “你不在的时候，我也有一些话想告诉你。”; customized notes remain untouched. Empty chats say “你来啦” and the composer chooses among “What's on your mind... ”, “A little thought...”, and “Begin anywhere...” on focus. The editable persona starts blank; only an unchanged legacy default is cleared on upgrade, and an internal neutral fallback remains available to the model.

Home greetings and voice-lab example text omit a name with the default user label, and use the customized name after it is saved in We. Existing voice-lab drafts are not changed.

Home greetings use neutral time-of-day phrases, with a custom user name appended only to short salutations (for example, `早上好。Alex`), not conversational questions or invitations. A greeting stays stable during the page session. On upgrade, only the exact legacy first preview note is replaced; edited notes are preserved, and deleting the preview does not recreate it on startup.

Memory → We → Sidebar greeting supports time-of-day or random greeting pools, a live preview, and restoring defaults. Write one sentence per line (up to 30 lines per group, 200 characters per sentence). Blank groups use built-in defaults. Morning is 05:00–12:00, daytime 12:00–18:00, and evening 18:00–23:00; late night uses common greetings. Custom common lines supplement time-of-day pools, while random mode uses only the common pool. Insert `{你的名字}` where a custom sentence should show the user name; custom sentences do not append it automatically. Changes take effect after saving and remain stable during the page session. These are interface preferences, not persona instructions sent to the model.

We → 在一起 saves a shared start date. The home counter uses local calendar days, counting the start date as day 1, rather than elapsed 24-hour periods. Until a date is saved, the legacy device-local start date is preserved. Weather starts without a city and makes no weather request until configured; clearing the city disables it. Upgrading clears the old seeded city once, while preserving other saved cities. We fields use inset paper styling, and touch devices hide textarea resize handles. Latin assistant names retain Caveat; Chinese names use smaller LXGW WenKai signatures. The persona mark uses the initial Latin letter, or M for Chinese names.

We → Sidebar quote offers a visibility switch and one custom caption, up to 200 characters. Blank restores “A little time for us, tomorrow.” The old built-in Chinese caption also displays this English default; other custom captions remain unchanged. Preferences remain shared through the authenticated `/api/home-postcard` endpoint and existing settings table. Legacy design keys remain compatible. Captions are display-only, never model instructions. Diary comments use configured display names independently for each author, falling back to 我 / 他 only when that name is unset.

The diary label formerly displayed as `deep` is now `thoughts` in filters, composition, and existing entries. Internal `dive` storage values and `deep` tool arguments remain compatible; available tags and default selections are unchanged.

We reuses the themed bottom sheet for assistant pronouns and greeting rotation. The together date uses three scrolling Year, Month, and Day wheels with snapping, fading edges, keyboard controls, and valid calendar limits. Future dates cannot be selected. Cancel leaves the date unchanged; Done updates the form draft, and Save date persists it through the existing endpoint.

If previously working requests suddenly return HTTP 401, the server no longer accepts the browser's signed login session; this is not a missing-data response. Keep `SECRET_KEY` stable across restarts and all workers. Changing it invalidates old sessions. Also avoid overlapping default `session` cookies from different Flask applications on the same hostname: browser cookies are not isolated by port. A configured key alone does not rule out cookie collisions. Do not bypass authentication to work around this.

Default app backgrounds use a soft central rose wash and a faint paper grain; default chat backgrounds use a solid theme color. Uploaded app and chat images remain independent, retain their saved opacity, and replace their corresponding defaults. Clearing an upload restores the theme default. Earlier watercolor studies remain documented in [the design notes](design/theme-backgrounds-v1.md).

The [Notes wall](notes-wall.md) groups assistant letters and mirrored Room messages by month with separate signatures. Its navigation entry previews a new assistant letter before opening the wall. ZIP backups preserve authorship; existing optional cloud mirrors require the author-field and RPC update described in that document before relying on cloud round trips for user-authored notes.

An operator can enable a temporary [read-only empty UI preview](empty-ui-preview.md) without deleting household data. The local flag is not published, and normal installations do not enable this preview.

Conversation exports use both display names and label optional reasoning as `think...`. Existing database paths, backup format identifiers, and browser preference keys retain their legacy names for compatibility.

We is a separate Memory section. The optional Ombre console is hidden from the public navigation; its implementation and existing integration data are retained.

The checked-in `.env.example` documents weather, encrypted GitHub backups, Web Push identity, and model settings. Provider and MCP credentials can also be managed from the settings screen after login.
