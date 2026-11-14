# Empty UI preview

Create `data/ui-empty-preview.flag` to show empty content without changing the
household database. Remove that flag and refresh to restore the normal view.
For installations with a custom database path, place the flag beside that file.

Authenticated content reads receive empty responses. Identity, theme, and other
configuration reads remain available. Preview requests cannot send messages,
save settings, upload, delete, or trigger integrations. Authentication is still
required. This is a temporary UI inspection mode, not a database reset or a
replacement for a backup. Existing background jobs are not changed.

The local flag is ignored by Git and is never included in a deployment.
