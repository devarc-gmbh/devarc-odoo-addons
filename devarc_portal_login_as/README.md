# DevArc Portal Login As

Lets authorised staff of an Odoo 20 Community database see the customer portal exactly as a portal
user sees it. The staff member gives a reason, confirms their own password and the browser session
switches to the portal user for at most 30 minutes. Every session is recorded in an audit log and the
customer gets a notification.

## Use case

A B2B customer complains that an invoice, quotation or document is missing in the portal. The
salesperson opens the customer, presses *View as customer*, sees the portal with the customer's own
access rights, finds the cause and presses *Return* to get back to the backend, on the audit log
record of that session.

## Features

- Button *View as customer* on the user form of a portal user and smart button on a contact that has
  exactly one active portal user. The buttons are only shown when the current user is allowed to use
  them.
- Wizard with a mandatory reason. The password of the staff member is always asked again before the
  switch, even when it was confirmed a few minutes earlier.
- The browser session is switched with a new session id. The original session id stops working.
- Yellow banner on every portal page with the name of the customer, the end time and a *Return*
  button.
- *Return* switches back to the staff member, again with a new session id, and opens the audit log
  record.
- The session ends automatically after 30 minutes. The next request logs out.
- Audit log in the *Portal view-as* app (top menu, visible to administrators and both Login As
  groups) with the staff member, the portal user, the customer, the reason, the start, expiry and
  end time, the end reason, the IP address and the browser. The log has a chatter. The names of the
  staff member and the portal user are stored on the log, so a user can still be deleted and the
  log stays readable.
- The portal user gets a notification message (e-mail with the light layout) when the session starts.
- Administrators can stop any active session with the *Stop* button on the log record.

## Configuration

Two groups are added under *Settings > Users & Companies > Users*:

1. **Login As: my customers** - the user may view the portal of portal users whose company has the
   user as *Salesperson*. The user sees the log entries of their own sessions.
2. **Login As: all portal users** - the user may view the portal of every active portal user of the
   companies they have access to. The user sees all log entries. Meant for support.

*All portal users* includes *my customers*. Only internal users in one of these groups can use the
feature, for everyone else the buttons are hidden.

## Security

- Only active portal users can be viewed. Internal users, inactive users and users of a company the
  staff member has no access to are refused.
- The password of the staff member is checked before every switch.
- During a session, security settings of the portal user cannot be changed: the password check
  wizard is refused, and changing the portal password needs the old password, which the staff member
  does not know.
- *Return* is a POST request with a CSRF token. Before switching back, the add-on checks that the
  staff member still has the same password and is still active. When the password changed or the
  user was archived meanwhile, the session is logged out instead.
- Logout, expiry, *Stop* by an administrator and a refused *Return* end the log entry with the
  matching end reason.
- Log entries cannot be created, edited or deleted from the user interface.

### Known limitation

*View my customers* trusts the **Salesperson** field of the customer company. A staff member who
can edit contacts (for example with *Contacts: Creation*) can set themselves as Salesperson of any
customer and then view that customer's portal. The view is still logged with its reason and the
customer is notified. If this matters to you, let only managers change the Salesperson field, or
give *View my customers* only to users who cannot edit contacts.

During a session the staff member can change the customer's contact details, including the e-mail
address, on the customer's behalf. This is intended: customers often ask support to do exactly
that.

## Usage

1. Open a contact or a portal user and press *View as customer*.
2. Enter the reason and press *Start*, then confirm your password.
3. Check the portal. Press *Return* in the banner when you are done.

## Tests

```
odoo-bin -d <db> -i devarc_portal_login_as --test-enable --test-tags=/devarc_portal_login_as --stop-after-init
```

The tests cover the access rules, the audit log, the session switch and return over HTTP, CSRF and
method checks, expiry, the administrator stop, logout and the blocked password check during a session.

## License

LGPL-3.

## Support

DevArc GmbH, https://devarc.ch, info@devarc.ch
