# Attestations

Lychnion's signed statements that a publisher's key belongs to an
official body (lychnion/lychnion#119). One file per signing:
`<publisher>/<signed time>.json`, written by `tool/records.dart attest`
and reviewed in a pull request. A newer file for the same publisher
decides; taking an attestation back is a newer file with
`"withdrawn": true`. Files are never edited or deleted.

`catalog` carries all of them; CI runs `check-attestations`. Who can get
one and how Lychnion checks:
[docs/attestations.md](https://github.com/lychnion/lychnion/blob/main/docs/attestations.md)
in the app repository. Applications: the issue form "Ask Lychnion to
check your publisher key".
