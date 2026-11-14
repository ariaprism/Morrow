# Notes wall

Notes use the existing household authentication. The wall groups letters by month,
shows the current author name, and opens a paper reader. A new assistant letter
opens inline from the navigation before entering the wall. Read receipts identify
the exact letter, so a newer arrival remains unread.

The `notes.author` SQLite column defaults to `assistant` for older notes. Room
messages use `user`: saving edits the pending mirror, withdrawing removes that
mirror, and delivery detaches it while preserving the archived paper. Old root
room runs are imported once; recovery attempts do not duplicate their letters.
Deleting a wall copy never edits a pending room message. The assistant's
`read_note` tool continues to read only assistant letters.

ZIP backups retain the author column. Older backups default to assistant authors.
The optional cloud snapshot payload also includes `notes[].author`, and restores
preserve it when supplied. Existing cloud installations must add the field and
include it in the `nook_apply_snapshot` notes insert before relying on cloud
round trips for user-authored letters:

```sql
alter table public.nook_notes
  add column if not exists author text not null default 'assistant';
```

If the existing RPC explicitly enumerates note columns, include `author` in its
insert/select with `coalesce(value->>'author', 'assistant')`. Preserve its existing
transaction, authorization, and RLS model. No remote schema is changed by the
application's local SQLite migration.
