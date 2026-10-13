**English** | [Deutsch](SECURITY.de.md)

# Security

## What this project is

A **maintained side project**, not a product with an on-call service. This is stated
here so you know what you are getting into — with software that secures chats, an
honest expectation is worth more than a big promise.

## Reporting vulnerabilities

**Please not as a public ticket.** Instead:

- **E-mail:** `gumbert@gekons.de`
- or via GitHub's private reporting ("Report a vulnerability" in the "Security"
  tab). The button is only there if the feature is enabled in the repository
  (Settings → Code security → "Private vulnerability reporting") — whoever runs the
  repository has to do that themselves. If the button is missing: please e-mail, no ticket.

Helpful: what is affected, how it can be reproduced, which version you are using.

## What you can expect

| | |
|---|---|
| First response | usually **within 7 days** |
| Assessment | within 14 days |
| Fix | depending on severity — critical bugs take precedence over everything else |
| Credit | on request, gladly by name |

If I'm on holiday, it takes longer. That is the honest answer.

## What this project does not secure

The installer sets up Matrix, Synapse, coturn and nginx. **Vulnerabilities in those
programs are to be reported there**, not here:

- Synapse and Matrix: https://matrix.org/security-disclosure-policy/
- Debian: https://www.debian.org/security/

What belongs here: bugs in the installer, in the invitation page, in the defaults —
that is, everything this project itself decides or writes.

## Known limits

Not bugs, but design — so nobody assumes something that isn't true:

- **Users have no 2FA.** Matrix can't do that; only the admin page has a second
  factor. 2FA for everyone would require a rebuild on top of the
  Matrix Authentication Service.
- **Metadata is visible.** Who writes to whom and when is on the server.
  Content is not. See the README.
- **Federation is open by default.** Your people can reach the worldwide network; other
  servers learn room metadata as a result. To turn it off: in
  `/etc/matrix-synapse/conf.d/50-eigene.yaml` add the line `federation_domain_whitelist: []`
  (empty list = no outside server) and `systemctl restart matrix-synapse`.
  The installer never touches this file; `20-betrieb.yaml`, on the other hand, is
  rewritten on every run, changes there are lost (see docs/operator.md, "Operation").
- **The operator is powerful.** They can create accounts, suspend them and set passwords —
  and thus, in theory, take over an account. They still can't read old messages,
  the keys are on the devices. The emoji device verification would make such an
  attempt visible.
- **No automatic updates for Synapse and Element Web.** Deliberately — but someone has
  to trigger them (`apt` or `./install.sh … --nur element`, see docs/operator.md).
