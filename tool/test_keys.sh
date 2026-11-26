#!/bin/bash
# End-to-end test of grant-request / grant / check-keys / sign / revoke-key
# with throwaway keys (also run by CI). Run from the repository root.
set -u
cd "$(dirname "$0")/.." || exit 1
T=$(mktemp -d "${TMPDIR:-/tmp}/grant-e2e.XXXXXX")
LK=keys/lychnion.json
made=
trap 'rm -rf "$T"; [ -n "$made" ] && rm -f "$LK"' EXIT
R() { dart run tool/records.dart "$@"; }
pass=0; fail=0
ok() { echo "PASS: $1"; pass=$((pass+1)); }
bad() { echo "FAIL: $1"; fail=$((fail+1)); }
expect_ok() { local d=$1; shift; if "$@" >"$T/out" 2>&1; then ok "$d"; else bad "$d"; cat "$T/out"; fi; }
expect_fail() { local d=$1; shift; if "$@" >"$T/out" 2>&1; then bad "$d (succeeded)"; cat "$T/out"; else ok "$d"; fi; }

ROOT=$(R keygen 2>"$T/rp"); RP=$(sed 's/public key: //' "$T/rp")
EVERY=$(R keygen 2>"$T/ep"); EP=$(sed 's/public key: //' "$T/ep")
NARROW=$(R keygen 2>"$T/np"); NP=$(sed 's/public key: //' "$T/np")
for v in "$ROOT" "$RP" "$EVERY" "$EP" "$NARROW" "$NP"; do
  [ -n "$v" ] || { echo "FAIL: keygen gave nothing"; exit 1; }
done
# expect_fail_says DESC TEXT CMD...: fails, and says TEXT
expect_fail_says() {
  local d=$1 t=$2; shift 2
  if "$@" >"$T/out" 2>&1; then bad "$d (succeeded)"; cat "$T/out"
  elif grep -q -- "$t" "$T/out"; then ok "$d"
  else bad "$d (other reason)"; cat "$T/out"; fi
}

cat >"$T/items.json" <<JSON
{"publisher": "lychnion", "items": [
 {"id": "t.model", "kind": "model", "role": "chat", "version": "2026.11.0",
  "title": {"en": "T"}, "note": {"en": "T"}, "licence": "MIT",
  "files": [{"name": "m.bin", "size": 10,
   "sha256": "181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c",
   "url": "https://example.org/m.bin"}]}]}
JSON
K="$T/keys.json"

expect_fail_says "grant-request refuses the keys scope" "not a scope a grant may list" \
  bash -c "echo $EVERY | dart run tool/records.dart grant-request --key - --scope keys --public-key $RP --out $T/x.json"
expect_ok "grant-request writes a request" \
  bash -c "echo $EVERY | dart run tool/records.dart grant-request --key - --scope data,models,safety-lines,attestations --public-key $RP --out $T/req.json"
grep -q "\"old\": \"$RP\"" "$T/req.json" && ok "request names the current root" || bad "request names the current root"
grep -q '"signature"' "$T/req.json" && bad "request has no root signature yet" || ok "request has no root signature yet"

expect_fail_says "grant refuses a key that is not the request's root" "not the key given" \
  bash -c "echo $EVERY | dart run tool/records.dart grant --key - --from $T/req.json --expect-key $EP --keys-file $K --public-key $RP"
expect_fail_says "grant needs --expect-key" "Missing --expect-key" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/req.json --keys-file $K --public-key $RP"
# A request swapped on the way: made by another key, valid in itself.
expect_ok "a swapped request (another key's)" \
  bash -c "echo $NARROW | dart run tool/records.dart grant-request --key - --scope data --public-key $RP --out $T/swapped.json"
expect_fail_says "grant refuses a swapped request" "not the request made in" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/swapped.json --expect-key $EP --keys-file $K --public-key $RP"
python3 - "$T/req.json" "$T/tampered.json" <<'PY'
import json,sys
j=json.load(open(sys.argv[1])); j['scope']=['attestations','data','keys','models','safety-lines']
json.dump(j,open(sys.argv[2],'w'))
PY
expect_fail_says "grant refuses a request listing keys" "not a grant request" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/tampered.json --expect-key $EP --keys-file $K --public-key $RP"
python3 - "$T/req.json" "$T/forged.json" <<'PY'
import json,sys
j=json.load(open(sys.argv[1])); j['scope']=['data']
json.dump(j,open(sys.argv[2],'w'))
PY
expect_fail_says "grant refuses a request changed after the countersignature" "countersignature is not valid" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/forged.json --expect-key $EP --keys-file $K --public-key $RP"
[ -e "$K" ] && bad "no keys file after refused grants" || ok "no keys file after refused grants"

expect_ok "grant signs and adds the grant" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/req.json --expect-key $EP --keys-file $K --public-key $RP"
expect_ok "check-keys accepts the file" R check-keys "$K" --public-key "$RP"
expect_fail_says "check-keys refuses it against another root" "lead to" R check-keys "$K" --public-key "$NP"
expect_fail_says "check-keys refuses a missing file" "no such keys file" R check-keys "$T/missing.json" --public-key "$RP"

expect_ok "the granted key signs a model" \
  bash -c "echo $EVERY | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"
expect_fail_says "an ungranted key cannot sign" "not trusted for lychnion" \
  bash -c "echo $NARROW | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records2 --public-key $RP --keys $K t.model"

# A key granted data only cannot sign a model.
expect_ok "grant-request for a data-only key" \
  bash -c "echo $NARROW | dart run tool/records.dart grant-request --key - --scope data --public-key $RP --out $T/req2.json"
expect_ok "grant of the data-only key" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/req2.json --expect-key $NP --keys-file $K --public-key $RP"
expect_fail_says "a data-only key cannot sign a model" 'does not hold the scope "models"' \
  bash -c "echo $NARROW | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records3 --public-key $RP --keys $K t.model"

expect_fail_says "revoke-key refuses the root itself" "is the root itself" \
  bash -c "echo $ROOT | dart run tool/records.dart revoke-key --key - --revoked $RP --keys-file $K --public-key $RP"
expect_fail_says "revoke-key refuses a key the root did not grant" "not one it granted" \
  bash -c "echo $ROOT | dart run tool/records.dart revoke-key --key - --revoked $(R keygen 2>&1 >/dev/null | sed 's/public key: //') --keys-file $K --public-key $RP"
expect_fail_says "a granted key cannot revoke" "not the current root" \
  bash -c "echo $EVERY | dart run tool/records.dart revoke-key --key - --revoked $NP --keys-file $K --public-key $RP"
expect_ok "the root revokes the everyday key" \
  bash -c "echo $ROOT | dart run tool/records.dart revoke-key --key - --revoked $EP --keys-file $K --public-key $RP"
expect_ok "check-keys accepts all three" R check-keys "$K" --public-key "$RP"
grep -c '"type"' "$K" | grep -q '^3$' && ok "three statements in the file" || bad "three statements in the file"
python3 - "$T/items.json" <<'PY'
import json,sys
p=sys.argv[1]; j=json.load(open(p)); j['items'][0]['version']='2026.11.1'; json.dump(j,open(p,'w'))
PY
expect_fail_says "the revoked key cannot sign" "not trusted for lychnion" \
  bash -c "echo $EVERY | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"
expect_fail "a record by the revoked key stops the next sign" \
  bash -c "echo $ROOT | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"
grep -q "revoked key" "$T/out" && ok "it says why" || bad "it says why"
rm "$T/records/t.model/2026.11.0.json"
expect_ok "the root signs once it is removed" \
  bash -c "echo $ROOT | dart run tool/records.dart sign --key - --items $T/items.json --records $T/records --public-key $RP --keys $K t.model"

expect_fail_says "grant with a test key needs --keys-file" "needs --keys-file" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/req.json --expect-key $EP --public-key $RP"
expect_fail_says "and not Lychnion's own file" "needs --keys-file" \
  bash -c "echo $ROOT | dart run tool/records.dart grant --key - --from $T/req.json --expect-key $EP --public-key $RP --keys-file ./keys/../keys/lychnion.json"

# The offline root as an encrypted key backup (#118). The passphrase
# comes from a test file (LYCHNION_PASSPHRASE_FILE, tests only), one line
# per prompt.
PF="$T/pass"; B="$T/root.lychnion-key"
pass() { printf '%s\n' "$@" >"$PF"; }
pass "short" "short"
expect_fail_says "backup refuses a short passphrase" "shorter than" \
  bash -c "echo $ROOT | LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart backup --key - --out $B"
pass "correct horse battery" "correct horse batterY"
expect_fail_says "backup refuses differing passphrases" "differ" \
  bash -c "echo $ROOT | LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart backup --key - --out $B"
pass "correct horse battery" "correct horse battery"
expect_ok "backup writes the root's key backup" \
  bash -c "echo $ROOT | LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart backup --key - --out $B"
grep -q '"revocation"' "$B" && bad "the backup has no ready-made revocation" || ok "the backup has no ready-made revocation"
grep -q "$ROOT" "$B" && bad "the backup does not show the key" || ok "the backup does not show the key"
grep -q "even without the passphrase" "$B" && bad "the app's warning is gone" || ok "the app's warning is gone"
grep -q "neither sign nor revoke" "$B" && ok "the root's warning is in" || bad "the root's warning is in"
[ "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$B")" = 0o600 ] \
  && ok "only the owner may read it" || bad "only the owner may read it"
[ -e "$B.part" ] && bad "no .part left" || ok "no .part left"
cp "$B" "$T/renamed.bak"
pass "correct horse battery"
expect_ok "a renamed backup opens (by its content)" \
  bash -c "LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart public --key $T/renamed.bak"
expect_ok "a key from a pipe still works (--key <(...))" \
  bash -c "dart run tool/records.dart public --key <(printf %s $ROOT)"
grep -q "^$RP\$" "$T/out" && ok "to the right key" || bad "to the right key"
touch "$T/stale.lychnion-key.part"
pass "correct horse battery" "correct horse battery"
expect_fail_says "a stale .part from a stopped run is named" "delete it first" \
  bash -c "echo $ROOT | LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart backup --key - --out $T/stale.lychnion-key"
pass "correct horse battery" "correct horse battery"
expect_fail_says "backup needs the stick's folder to exist" "is the stick mounted" \
  bash -c "echo $ROOT | LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart backup --key - --out $T/nostick/r.lychnion-key"
expect_fail_says "the passphrase file is for tests only" "tests only" \
  bash -c "LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart public --key $B"
echo "$ROOT" >"$T/seed.lychnion-key"
expect_fail_says "a seed file named like a backup is refused" "Not a key backup" \
  R public --key "$T/seed.lychnion-key"
expect_fail_says "keygen --title needs --1password" "only with --1password" \
  R keygen --title lychnion-everyday-key
pass "correct horse battery" "correct horse battery"
expect_fail_says "backup never overwrites" "not overwritten" \
  bash -c "echo $ROOT | LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart backup --key - --out $B"
pass "correct horse battery"
expect_ok "public opens the backup" \
  bash -c "LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart public --key $B"
grep -q "^$RP\$" "$T/out" && ok "to the root's public key" || bad "to the root's public key"
pass "wrong horse battery"
expect_fail_says "a wrong passphrase does not open it" "does not open" \
  bash -c "LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart public --key $B"
# The same root again under a new passphrase (a stolen 1Password).
pass "correct horse battery" "a brand new passphrase" "a brand new passphrase"
expect_ok "backup makes the root again from a backup, new passphrase" \
  bash -c "LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart backup --key $B --out $T/rekeyed.lychnion-key"
pass "a brand new passphrase"
expect_ok "the new backup opens with the new passphrase" \
  bash -c "LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart public --key $T/rekeyed.lychnion-key"
grep -q "^$RP\$" "$T/out" && ok "to the same root" || bad "to the same root"
pass "correct horse battery"
expect_fail_says "and not with the old one" "does not open" \
  bash -c "LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart public --key $T/rekeyed.lychnion-key"
THIRD=$(R keygen 2>"$T/tp"); TP=$(sed 's/public key: //' "$T/tp")
expect_ok "grant-request for a third key" \
  bash -c "echo $THIRD | dart run tool/records.dart grant-request --key - --scope data --public-key $RP --out $T/req3.json"
pass "correct horse battery"
expect_ok "the root grants from its key backup" \
  bash -c "LYCHNION_TEST=1 LYCHNION_PASSPHRASE_FILE=$PF dart run tool/records.dart grant --key $B --from $T/req3.json --expect-key $TP --keys-file $K --public-key $RP"

# Lychnion's own keys/lychnion.json, found next to the tool wherever it
# runs from. Only when the repository has none (CI, a clean checkout):
# the test file is removed again on exit.
if [ ! -e "$LK" ]; then
  made=1
  TOOL="$PWD/tool/records.dart"
  echo '{"keys": [{"bogus": 1}]}' >"$LK"
  expect_fail_says "a broken keys/lychnion.json stops check-records" "not a valid key statement" \
    R check-records records
  expect_fail_says "it is found from another folder" "not a valid key statement" \
    bash -c "cd '$T' && dart '$TOOL' check-keys"
  # Statements by a key outside Lychnion's chain (the test root's).
  cp "$K" "$LK"
  expect_fail_says "statements by a foreign key stop check-keys" "lead to" \
    R check-keys
  expect_fail_says "and every other command" "do not lead to" \
    R check-records records
  rm -f "$LK"
  expect_ok "no keys/lychnion.json: check-keys passes" R check-keys
else
  echo "SKIP: keys/lychnion.json exists; its own tests need a clean checkout"
fi

echo "passed $pass, failed $fail"
[ $fail -eq 0 ]
