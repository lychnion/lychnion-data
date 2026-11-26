#!/bin/bash
# End-to-end test of grant-request / grant / check-keys / sign / revoke-key
# with throwaway keys (also run by CI). Run from the repository root.
set -u
T=$(mktemp -d "${TMPDIR:-/tmp}/grant-e2e.XXXXXX")
R() { dart run tool/records.dart "$@"; }
pass=0; fail=0
ok() { echo "PASS: $1"; pass=$((pass+1)); }
bad() { echo "FAIL: $1"; fail=$((fail+1)); }
expect_ok() { local d=$1; shift; if "$@" >"$T/out" 2>&1; then ok "$d"; else bad "$d"; cat "$T/out"; fi; }
expect_fail() { local d=$1; shift; if "$@" >"$T/out" 2>&1; then bad "$d (succeeded)"; cat "$T/out"; else ok "$d"; fi; }

ROOT=$(R keygen 2>"$T/rp"); RP=$(sed 's/public key: //' "$T/rp")
EVERY=$(R keygen 2>"$T/ep"); EP=$(sed 's/public key: //' "$T/ep")
NARROW=$(R keygen 2>"$T/np"); NP=$(sed 's/public key: //' "$T/np")

cat >"$T/items.json" <<JSON
{"publisher": "lychnion", "items": [
 {"id": "t.model", "kind": "model", "role": "chat", "version": "2026.11.0",
  "title": {"en": "T"}, "note": {"en": "T"}, "licence": "MIT",
  "files": [{"name": "m.bin", "size": 10,
   "sha256": "181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c",
   "url": "https://example.org/m.bin"}]}]}
JSON
K="$T/keys.json"

expect_fail "grant-request refuses the keys scope" \
  bash -c "echo $EVERY | dart run tool/records.dart grant-request --key - --scope keys --public-key $RP --out $T/x.json"
expect_ok "grant-request writes a request" \
  bash -c "echo $EVERY | dart run tool/records.dart grant-request --key - --scope data,models,safety-lines,attestations --public-key $RP --out $T/req.json"
grep -q "\"old\": \"$RP\"" "$T/req.json" && ok "request names the current root" || bad "request names the current root"
grep -q '"signature"' "$T/req.json" && bad "request has no root signature yet" || ok "request has no root signature yet"

expect_fail "grant refuses a key that is not the request's root" \
  bash -c "echo $EVERY | dart run tool/records.dart grant --key - --from $T/req.json --keys-file $K --public-key $RP"
python3 - "$T/req.json" "$T/tampered.json" <<'PY'
import json,sys
j=json.load(open(sys.argv[1])); j['scope']=['attestations','data','keys','models','safety-lines']
json.dump(j,open(sys.argv[2],'w'))
PY
expect_fail "grant refuses a request listing keys" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/tampered.json --keys-file $K --public-key $RP"
python3 - "$T/req.json" "$T/forged.json" <<'PY'
import json,sys
j=json.load(open(sys.argv[1])); j['scope']=['data']
json.dump(j,open(sys.argv[2],'w'))
PY
expect_fail "grant refuses a request changed after the countersignature" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/forged.json --keys-file $K --public-key $RP"
[ -e "$K" ] && bad "no keys file after refused grants" || ok "no keys file after refused grants"

expect_ok "grant signs and adds the grant" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/req.json --keys-file $K --public-key $RP"
expect_ok "check-keys accepts the file" R check-keys "$K" --public-key "$RP"
expect_fail "check-keys refuses it against another root" R check-keys "$K" --public-key "$NP"

expect_ok "the granted key signs a model" \
  bash -c "echo $EVERY | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"
expect_fail "an ungranted key cannot sign" \
  bash -c "echo $NARROW | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records2 --public-key $RP --keys $K t.model"

# A key granted data only cannot sign a model.
expect_ok "grant-request for a data-only key" \
  bash -c "echo $NARROW | dart run tool/records.dart grant-request --key - --scope data --public-key $RP --out $T/req2.json"
expect_ok "grant of the data-only key" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/req2.json --keys-file $K --public-key $RP"
expect_fail "a data-only key cannot sign a model" \
  bash -c "echo $NARROW | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records3 --public-key $RP --keys $K t.model"

expect_fail "revoke-key refuses a key the root did not grant" \
  bash -c "echo $ROOT | dart run tool/records.dart revoke-key --key - --revoked $RP --keys-file $K --public-key $RP"
expect_fail "a granted key cannot revoke" \
  bash -c "echo $EVERY | dart run tool/records.dart revoke-key --key - --revoked $NP --keys-file $K --public-key $RP"
expect_ok "the root revokes the everyday key" \
  bash -c "echo $ROOT | dart run tool/records.dart revoke-key --key - --revoked $EP --keys-file $K --public-key $RP"
expect_ok "check-keys accepts all three" R check-keys "$K" --public-key "$RP"
grep -c '"type"' "$K" | grep -q '^3$' && ok "three statements in the file" || bad "three statements in the file"
python3 - "$T/items.json" <<'PY'
import json,sys
p=sys.argv[1]; j=json.load(open(p)); j['items'][0]['version']='2026.11.1'; json.dump(j,open(p,'w'))
PY
expect_fail "the revoked key cannot sign" \
  bash -c "echo $EVERY | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"
expect_fail "a record by the revoked key stops the next sign" \
  bash -c "echo $ROOT | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"
grep -q "revoked key" "$T/out" && ok "it says why" || bad "it says why"
rm "$T/records/t.model/2026.11.0.json"
expect_ok "the root signs once it is removed" \
  bash -c "echo $ROOT | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"

rm -rf "$T"
echo "passed $pass, failed $fail"
[ $fail -eq 0 ]
