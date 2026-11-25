# Administrator guide

## Fields

**Settings > Technical > Incoming Mail Servers**, form view, right after the "SSL/TLS" checkbox
(`is_ssl`), visible when `server_type` is `imap` or `gmail`. Source:
`models/fetchmail_server.py`.

| Field | Label | Type | Default | Required | Help text shown in the UI |
|---|---|---|---|---|---|
| `imap_folder` | IMAP Folder | Char | `INBOX` | yes | "IMAP mailbox the incoming mails are fetched from, e.g. 'INBOX' or a Gmail label such as 'Odoo' fed by a filter. Non-ASCII characters are encoded to IMAP modified UTF-7 and names containing a space or a special character are quoted automatically. Ignored for POP servers." |
| `imap_search_criteria` | IMAP Search Criteria | Char | `(UNSEEN)` | yes | "IMAP search criteria selecting which mails are fetched, e.g. '(UNSEEN X-GM-RAW \"deliveredto:odoo@example.com\")' fetches only the mails a catch-all rule delivered to this mailbox. Ignored for POP servers." |

Both fields are hidden on the form for `server_type = 'pop'` and are simply not read by the
connection wrapper for POP servers - see [POP servers are not affected](#pop-servers-are-not-affected)
below.

## Search criteria syntax

`imap_search_criteria` is passed verbatim to the IMAP `SEARCH` command
(`connection.search(None, imap_search_criteria)`), so it follows plain **RFC 3501 IMAP SEARCH**
syntax:

- `(UNSEEN)` - the Odoo core default: every unread mail in the folder.
- `(UNSEEN HEADER Delivered-To "user+odoo@example.com")` - unread mail whose `Delivered-To` header
  matches the given address. Standard RFC 3501 `HEADER` search, works on most IMAP servers.
- `(UNSEEN FROM "billing@example.com")`, `(UNSEEN SUBJECT "Invoice")` - any other standard RFC 3501
  search key can be combined the same way.

### Gmail extension: `X-GM-RAW`

Against `imap.gmail.com`, Gmail additionally accepts the non-standard `X-GM-RAW` search key, which
runs a normal Gmail search-box query over IMAP:

- `(UNSEEN X-GM-RAW "deliveredto:user+odoo@example.com")` - the Gmail-search equivalent of the
  `Delivered-To` header check above.

`X-GM-RAW` only works against Gmail's own IMAP server; it is rejected by other IMAP servers, and the
fetch now fails outright with the IMAP error recorded on the server's Error field instead of being
silently misread. Some IMAP servers, Gmail included, do not consistently return matches for
`HEADER` search the same way a `X-GM-RAW`/native search does - always verify a new criteria with the
read-only probe in [docs/gmail-catchall.md](gmail-catchall.md) before relying on it in production,
on any server.

Quoting matters: values with spaces or special characters must stay inside their own double quotes
(`"deliveredto:user+odoo@example.com"`), and the whole criteria string must have balanced
parentheses. The field is stored and sent to IMAP exactly as typed, with no reformatting.

## The `UNSEEN` constraint

`@api.constrains("imap_search_criteria")` rejects any value whose criteria string does not start
with `UNSEEN`, ignoring leading whitespace or parentheses (case-insensitive regex
`[\s(]*UNSEEN\b`), with:

> IMAP search criteria must start with UNSEEN, so that every search key is combined with UNSEEN.
> Criteria starting with NOT or OR can match already read mails, which are then fetched and
> processed on every run.

This is a safety net, not a formatting rule: top-level IMAP `SEARCH` keys are combined with AND, so
only a criteria that starts with `UNSEEN` is guaranteed to exclude already read mails regardless of
what follows. `(UNSEEN)`, `(UNSEEN HEADER Delivered-To "...")` and `(UNSEEN OR FROM a FROM b)` are
all accepted; `(NOT UNSEEN)`, `(OR UNSEEN SEEN)`, `(HEADER X UNSEEN)` and `(ALL)` are all rejected,
because `UNSEEN` is not the first search key there. There is no way to save a server with such a
criteria - the form save and any programmatic `write()` both go through the same constraint.

## Line breaks in folder or criteria are rejected

`@api.constrains("imap_folder", "imap_search_criteria")` rejects any `imap_folder` or
`imap_search_criteria` value containing `\r` or `\n`, with:

> IMAP folder and search criteria must not contain line breaks, the IMAP server would read the rest
> of the line as a separate command.

This closes an IMAP command injection path: without the check, a value ending in a line break could
append an arbitrary extra IMAP command to the `SELECT`/`SEARCH` request sent to the server. As with
the `UNSEEN` constraint, the form save and any programmatic `write()` both go through this check.

## Non-ASCII criteria and IMAP literals are rejected

`imap_search_criteria` must contain ASCII characters only, because the IMAP connection sends the
search command as ASCII; saving a criteria with a character such as `ú` fails with:

> The IMAP Search Criteria field must contain ASCII characters only, the IMAP connection cannot send other characters in a search.

Folder names may contain any character: before `SELECT` they are encoded to IMAP modified UTF-7
(RFC 3501 section 5.1.3), e.g. `Faktúry` is sent as `Fakt&APo-ry`.

An IMAP literal marker such as `{5}` is rejected in both fields, because the IMAP server would
wait for 5 bytes of literal data that are never sent:

> IMAP folder and search criteria must not contain an IMAP literal such as {5}, the IMAP server would wait for data that is never sent.

## Wrong folder

If the server answers `SELECT` with anything other than `OK` (folder does not exist, wrong case or
label spelling), the fetch stops before any search with an IMAP error that names the folder, e.g.
`IMAP SELECT of folder 'Odoo' returned NO: [b"Mailbox doesn't exist"]`. Odoo logs it and records
it on the server like any other fetch error; no mail is fetched until the folder is fixed.

## POP servers are not affected

The connection wrapper only replaces `check_unread_messages()` when
`self._get_connection_type() == "imap"`. For `server_type = 'pop'`, `_connect__()` returns the
unmodified core connection, `imap_folder` and `imap_search_criteria` are never read, and the two
fields are hidden on the form (`invisible="server_type not in ('imap', 'gmail')"`).

## Gmail servers

`server_type = 'gmail'` servers authenticate through `google_gmail`'s own OAuth (XOAUTH2) login
flow; this addon never depends on `google_gmail`. `_get_connection_type()`, provided by
`google_gmail` when it is installed, reports `imap` for Gmail servers as well, so the same
`imap_folder`/`imap_search_criteria` wrapper applies to them automatically once both modules are
installed - no extra configuration is needed beyond installing `google_gmail`. If `google_gmail` is
not installed, the two fields are still visible for `server_type = 'imap'` servers pointed at
`imap.gmail.com` with app-password authentication; `server_type = 'gmail'` servers simply do not
exist yet in that case.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Save fails with "IMAP search criteria must start with UNSEEN" | The criteria does not start with `UNSEEN` (e.g. `(ALL)`, `(NOT UNSEEN)`, `(OR UNSEEN SEEN)`, or `UNSEEN` was moved or deleted while editing). | Put `UNSEEN` first inside the parentheses, e.g. `(UNSEEN ...)`. |
| Save fails with "must not contain line breaks" | The folder or criteria field has a stray `\r` or `\n` in it, usually from pasting text copied from another app or document. | Retype or clean the value; the addon rejects line breaks to block IMAP command injection. |
| Fetch fails with an IMAP error mentioning `SEARCH ... returned NO` or `BAD` | The criteria is syntactically invalid for this server, or uses `X-GM-RAW` against a non-Gmail IMAP server. | Fix the criteria syntax, or drop `X-GM-RAW` outside Gmail; the exact IMAP response is in the server's Error field. |
| Folder name with a space or special character (e.g. `[Gmail]/All Mail`) fails to select when typed with manual quotes | The addon already quotes `imap_folder` automatically before `SELECT`; quotes added manually in the field become part of the folder name. | Type the folder name without quotes, e.g. `[Gmail]/All Mail`, not `"[Gmail]/All Mail"`. |
| Fetch fails with an IMAP error `IMAP SELECT of folder '...' returned NO` | The folder does not exist on the server or is spelled differently (case and exact label spelling matter, especially on Gmail). | Fix the folder name; no search is sent while `SELECT` fails. |
| Save fails with "must contain ASCII characters only" | The criteria contains a non-ASCII character, e.g. an accented letter in a `SUBJECT` value. | Search for an ASCII part of the value instead. |
| Save fails with "must not contain an IMAP literal" | The folder or criteria contains a marker such as `{5}`. | Remove the braces; quote values with `"..."` instead. |
| No mail is ever fetched, but mail is arriving in the mailbox | The criteria does actually match any message (unbalanced quotes, wrong header/value). | Verify the folder name and criteria with the read-only probe in [docs/gmail-catchall.md](gmail-catchall.md) before assuming the fetch itself is broken. |
| Every unread mail in the mailbox gets fetched, not just the intended ones | Criteria is still the default `(UNSEEN)` (or otherwise too broad) against a mailbox that also receives unrelated mail. | Narrow the criteria (`HEADER Delivered-To`/`X-GM-RAW`/folder+label) before pointing the server at a shared or personal mailbox - see the warning in [docs/gmail-catchall.md](gmail-catchall.md). |
| Count is much higher than expected / personal mail matches too | The Default routing rule at Workspace level is not scoped to non-recognized addresses only, or a Gmail filter/forward relabels `Delivered-To` on mail sent directly to the mailbox's own address. | Scope the Workspace routing rule and add a permanent `NOT TO` safety net to the criteria - see [Exclude your own mailbox](gmail-catchall.md#exclude-your-own-mailbox). |
| Changing `imap_folder` has no visible effect on a POP server | The field is ignored for `server_type = 'pop'` by design. | Not a bug; POP has no folder/criteria concept in this addon. |
| Criteria written in lowercase (`unseen`) is accepted but IMAP behaves oddly on some servers | The constraint check is case-insensitive, but the IMAP `SEARCH` keyword itself is conventionally uppercase. | Write `UNSEEN` (and other search keys) in uppercase, matching RFC 3501 convention, even though the constraint alone would accept lowercase. |
| A test mail sent directly to the plus address is not fetched | On Gmail, `TO` search normalizes plus addressing, so `NOT TO "user@example.com"` also matches, and excludes, mail addressed straight to `user+odoo@example.com`. | Expected behavior of the `NOT TO` safety net; test through the real catch-all routing path instead of mailing the plus address directly, or drop `NOT TO` for that one test - see [Exclude your own mailbox](gmail-catchall.md#exclude-your-own-mailbox). |
