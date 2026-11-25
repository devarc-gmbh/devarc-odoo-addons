# Gmail catch-all

Recipe for the most common use case: route mail sent to an address your Workspace does not
recognize into Odoo, through a plus-addressed alias of an existing Gmail mailbox, without letting
Odoo touch the rest of that mailbox's mail.

## 1. Google Workspace: Default routing to a plus address

In the Google Workspace Admin console: **Apps > Google Workspace > Gmail > Default routing**, add
a rule for envelope recipients your Workspace does not recognize, action **"Change envelope
recipient"**, target a plus-addressed alias of an existing mailbox, e.g.
`user+odoo@example.com`. Mail sent to any unrecognized address at your domain then arrives in that
same mailbox, still addressed to the plus alias, instead of bouncing.

**Scope the rule to non-recognized addresses only.** In the same rule, set "Envelope recipient
pattern matches" and tick **"Perform this action only on non-recognized addresses"**. Without that
scope, Workspace can apply the routing rule to addresses it already recognizes too, so mail sent
directly to the target mailbox's own address (`user@example.com`) also arrives with `Delivered-To:
user+odoo@example.com`. Also check the target mailbox's own Gmail filters and any forwarding rule
for something that could relabel `Delivered-To` the same way. See
[Exclude your own mailbox](#exclude-your-own-mailbox) below for a criteria-level safety net against
this, and always recheck the probe count in step 5 after touching the routing rule.

## 2. Matching in Odoo: `Delivered-To` header vs `X-GM-RAW`, and the label alternative

Two ways to scope `imap_search_criteria` to only the catch-all mail, both explained in full in
[docs/admin-guide.md](admin-guide.md#search-criteria-syntax):

- **`HEADER Delivered-To`** (standard RFC 3501, works on most IMAP servers):
  `(UNSEEN HEADER Delivered-To "user+odoo@example.com")`.
- **`X-GM-RAW`** (Gmail-specific IMAP extension, runs a Gmail search-box query):
  `(UNSEEN X-GM-RAW "deliveredto:user+odoo@example.com")`.

**Alternative that avoids IMAP header search entirely:** create a Gmail filter matching
`deliveredto:user+odoo@example.com` with action "Apply label `Odoo`", then set `imap_folder =
Odoo` and leave `imap_search_criteria = (UNSEEN)` - folder membership alone does the filtering, no
`HEADER`/`X-GM-RAW` criteria needed. Trade-off: an extra piece of configuration to keep in sync
(the Gmail filter) instead of a single field on the incoming mail server.

Whichever option you pick, verify it with the read-only probe in step 5 below before activating
the server - do not assume a criteria "looks right" and go straight to production.

### Exclude your own mailbox

Even with the routing rule scoped correctly (see step 1), add a permanent safety net to the
criteria: exclude the mailbox's own address with `NOT TO`:

`(UNSEEN HEADER Delivered-To "user+odoo@example.com" NOT TO "user@example.com")`

On Gmail, `TO` is not a literal substring match on the raw header: the Gmail backend normalizes
plus addressing and treats `user+odoo@example.com` as the same address as `user@example.com` for
`TO` matching. Catch-all mail is unaffected, because its `To` header carries the original external
address, not the mailbox's own address in any form. `UNSEEN` still leads the criteria, so the
constraint in [docs/admin-guide.md](admin-guide.md#the-unseen-constraint) accepts it. Add one more
`NOT TO "..."` for every other address that forwards or routes into the same mailbox.

Real incident: a Default routing rule without the "non-recognized addresses only" scope matched 18
of 925 unread mails against `HEADER Delivered-To "user+odoo@example.com"` in one fetch cycle, of
which only 2 were genuine catch-all mail - the other 16 were personal mail sent directly to
`user@example.com` that Workspace had relabeled with the plus address anyway. Fixing the routing
rule scope stops new mail from being mislabeled, but the `NOT TO` clause above is what keeps a
future routing misconfiguration from reaching Odoo again.

Gotcha (verified 2026-09-10): the same normalization also excludes mail sent straight to the plus
address itself. `NOT TO "user@example.com"` matches, and therefore drops, a test mail addressed
directly to `user+odoo@example.com`, because Gmail resolves both to the same mailbox owner. This
does not affect real catch-all mail, whose `To` header stays the original external address, but it
means a manual test sent straight to the plus address will silently not be fetched while this
safety net is active - test through the actual routing path instead, or drop `NOT TO` temporarily
for that one test.

## 3. WARNING: what happens without a working filter (real incident)

If `imap_folder`/`imap_search_criteria` do not actually scope to the catch-all address - for
example the server is left on the default `INBOX` + `(UNSEEN)` while pointed at a mailbox that
also receives personal or unrelated mail - Odoo's fetch cycle pulls in **every unread mail in that
mailbox**, not just the catch-all ones.

This is not a hypothetical: a production deployment left in that state fetched **1140** distinct
personal mails in a single incident. All 1140 failed to process ("no possible route found" - none
of them matched any mail alias), so **none of them were stored anywhere in Odoo**, but all 1140
were nonetheless marked as read in the mailbox, because Odoo marks a message `\Seen` as soon as it
fetches it, independent of whether processing later succeeds.

Treat an unscoped catch-all mailbox as the expected failure mode, not an edge case: no data is
lost, but every unread mail in the mailbox silently becomes "read" and nothing about the failure is
visible outside the Odoo log. Always scope the criteria (`HEADER Delivered-To`, `X-GM-RAW`, or a
dedicated label/folder) before pointing this addon at any mailbox that also receives mail you care
about reading yourself, and verify with the probe below first.

## 4. Set a fallback model with "Create a New Record"

Not a feature of this addon, but without it a catch-all mailbox has nowhere to put its mail. The
incoming mail server also has a **"Create a New Record"** field (`object_id`, under "Actions to
Perform on Incoming Mails"). If it is left empty and a mail does not match any alias and does not
belong to an existing thread, Odoo logs "No possible route found", marks the mail read anyway (see
the warning above), and stores it nowhere. For a catch-all mailbox, set this field to a model that
makes sense for unrecognized senders, e.g. **CRM Lead**, so unmatched mail is captured instead of
silently disappearing.

## 5. Read-only probe before activating

Run this in `odoo shell` against the **archived** server, and never call `env.cr.commit()`:

```python
srv = env["fetchmail.server"].with_context(active_test=False).browse(<id>)
conn = srv._connect__(allow_archived=True)
conn.select(srv._imap_mailbox_argument(), readonly=True)
_typ, data = conn.search(None, srv.imap_search_criteria)
print(len(data[0].split()))
conn.logout()
```

`srv._imap_mailbox_argument()` mirrors the automatic quoting the connection wrapper applies to
`imap_folder`; calling `conn.select()` with the raw field value instead would fail for a folder
name containing a space or a special character, e.g. `[Gmail]/All Mail`.

- `readonly=True` opens the mailbox with IMAP `EXAMINE` instead of `SELECT`, so the server rejects
  any `STORE` (flag change) even if something in the session attempted one.
- Only `select`, `search` and `logout` are called - no `fetch`, no `store` anywhere in this
  snippet, so nothing gets marked `\Seen` and nothing is downloaded.
- The server stays `active = False` throughout, and the shell session is never committed, so this
  probe leaves no trace on the database either.
- Compare the printed count against the number of test mails you actually sent to the catch-all
  address. A count that is far larger than expected means the criteria is not scoping correctly -
  see the warning in step 3 - do not activate yet; adjust the folder or criteria and probe again.
- Repeat this probe after every change to the routing rule or the criteria, not just once before
  the first activation - a count is only trustworthy for the exact configuration it was measured
  against.

**Writing the criteria from a shell is not read-only, even on an archived server** (verified in
production 2026-09-10). Odoo core's `fetchmail.server.write()` calls `_update_cron()`, which runs
`cron.toggle(model, domain)` over `search([("state", "=", "done"), ("server_type", "!=",
"local")])`. That `search` inherits the calling context, so `env["fetchmail.server"].with_context(
active_test=False).browse(<id>).write({...})` against an archived server lets the search see it and
turns on the "Mail: Fetchmail Service" cron, even though the server itself stays archived. The
probe above never calls `write()`, so it is unaffected. If you do edit the criteria from a shell,
either `write()` through `browse(<id>)` without `active_test=False` (an archived record is still
reachable by id for a write), or check `mail.ir_cron_mail_gateway_action` afterwards and disable it
again if it got toggled on.

## 6. Activation and rollback

- **Activation:** once the probe count matches expectations, Unarchive the incoming mail server
  record (Settings > Technical > Incoming Mail Servers > filter Archived). The scheduled fetch
  cron reactivates itself automatically on unarchive - no separate cron step needed.
- **Rollback:** Archive the server again. This immediately stops the cron from fetching and is the
  safe, fast way back if anything looks wrong after activation.
