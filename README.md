# lychnion-data

Data for **Lychnion**, an offline safety assistant for Japan. The app
answers questions from knowledge packages and shows offline maps. It
downloads these files once and then works without a network.

This repository holds:

- the **signed records** of every package version (`records/`),
- Lychnion's **public key** (`keys/lychnion.pub`),
- Lychnion's **attestations** for official publishers (`attestations/`):
  signed statements that a publisher's key belongs to a public body, so
  the app shows it as "Checked by Lychnion",
- the data files themselves, in the
  [releases](https://github.com/lychnion/lychnion-data/releases),
- what goes into each package and map area, and the tools that build,
  sign and publish them ("How packages are made" below).

The AI models are not here. The app downloads them from Hugging Face
(`ggml-org/gemma-4-E2B-it-GGUF`, `ggml-org/gemma-4-E4B-it-GGUF`,
`ggml-org/embeddinggemma-2-GGUF`, and for Android
`litert-community/gemma-4-E2B-it-litert-lm`, Apache 2.0). Lychnion signs a
record for each model too, so the app knows exactly which file to expect.
A model record may say which platforms it is for (`"platforms":
["android"]`); without it, a model is for every platform.

## How the app trusts a file

The app does not trust a file because of where it came from. It trusts a
file because Lychnion signed its record.

1. **Record.** For each version of a package, map area or model there is
   one small JSON record: id, version, title, licence, notices, and every
   file with its size and SHA-256. Lychnion signs the record with its
   Ed25519 key. The app has the public key built in.
2. **Catalog.** A catalog (`catalog.json`) is a list of records, plus
   where to download the files. The app checks the signature of every
   record and ignores records that fail.
3. **Files.** The app downloads a file, checks its size and SHA-256
   against the signed record, and only then installs it. A file that does
   not match is deleted.
4. **Versions only go up.** The app never replaces a package with an
   older version.

Because the records are signed, a copy from anywhere is fine: this
repository, another web server, a computer at a shelter, or a USB stick.
The signature is the same everywhere. Records say *what* a file is, not
*where* it is; the catalog says where.

The private key is never in this repository or on any server.

## Layout

```text
keys/lychnion.pub                       Lychnion's public key (Ed25519, base64url)
records/<id>/<version>.json             one signed record per package version
packages/<id>.json                      what goes into a knowledge package: sources, licences, notice
packages/snapshots/, translations/      saved source texts; translations (checked by a person)
maps/<area>.json                        what goes into a map area
items.json                              every downloadable item (packages, maps, models)
tool/                                   the tools that build, sign and publish (below)
build/                                  built files (not in git; published as release assets)
```

Each release contains the files that are new in it, and a `catalog.json`
with the newest record of every item. Files that did not change stay in
older releases; the catalog points to them. The newest catalog is always
at
`https://github.com/lychnion/lychnion-data/releases/latest/download/catalog.json`,
so the app finds updates without an app update.

## How packages are made

The tools moved here from the app repository on 2026-11-18 (as of
lychnion/lychnion 57bc3bb). They need Python 3 and, for signing, Dart 3.12+.
On macOS, `.doc` and PDF sources use `textutil` and Swift.

```sh
# a knowledge package → build/packages/<id>.sqlite
python3 -I tool/build_package.py packages/jp.bousai.core.json
python3 -I tool/add_translations.py build/packages/jp.bousai.core.sqlite en
# optional: vectors made once by the app (phones then skip indexing)
python3 -I tool/add_vectors.py build/packages/jp.bousai.core.sqlite <cache>.vectors.json embeddinggemma-2-Q8_0.gguf
# and of the English texts, so English questions are compared with English text
# (lychnion/lychnion#85): a Mac build of the app with --dart-define=EMBED_TEXTS=true
# writes <ids>-<versions>-<model>@en.vectors.json for all its packages at once.
# Split it per package before changing any version (the name carries the versions
# it was made from; a new version also goes into the manifest and items.json, then
# build_package.py --meta-only); it prints the add_vectors.py command for each part
python3 -I tool/split_vectors.py <cache>@en.vectors.json build/vectors build/packages/*.sqlite
python3 -I tool/add_vectors.py build/packages/jp.bousai.core.sqlite \
  build/vectors/jp.bousai.core.embeddinggemma-2-Q8_0.gguf@en.vectors.json embeddinggemma-2-Q8_0.gguf@en

# a map area → build/maps/ (sites, base map, contour lines, walking routes, inundation tiles)
python3 -I tool/build_map.py maps/kushimoto.json
# (contour lines alone: python3 -I tool/contours.py maps/kushimoto.json)
python3 -m venv build/route-venv
build/route-venv/bin/pip install osmium pillow pyshp shapely
build/route-venv/bin/python -I tool/build_routes.py maps/kushimoto.json
build/route-venv/bin/python -I tool/build_inundation_tiles.py maps/kushimoto.json

# sign the records and write the catalog (key from 1Password, never on disk),
# from an up-to-date main: a stale clone leaves out a newer record or a
# withdrawal merged meanwhile
git pull
dart pub get
op read op://Private/lychnion-package-signing-key/credential \
  | dart run tool/records.dart sign --key - --items items.json --records records jp.bousai.core
# the catalog, with Lychnion's signed status (add --keys keys.json when it exists)
op read op://Private/lychnion-package-signing-key/credential \
  | dart run tool/records.dart catalog --key - --items items.json --records records \
  --base https://github.com/lychnion/lychnion-data/releases/download/<release>/ \
  --out build/catalog.json
# before it is uploaded or bundled: it must carry the status, and no record
# may be newer than it
dart run tool/records.dart verify --release build/catalog.json
```

The top of each tool says more. Records and catalogs use the format
code shared with the app (`packages/lychnion_format` in
[lychnion/lychnion](https://github.com/lychnion/lychnion): `PackageRecord`,
`CatalogDocument`, `KeysFile`), so the tools write what the app, a base
and a node read. `pubspec.yaml` names the commit it uses (`ref`), so a
change on the app's `main` never changes the tools by surprise: to move
it, set `ref` to a newer commit on the app's `main` (never a PR branch:
it may disappear) and run `dart pub upgrade lychnion_format`.

**Attestations.** After checking a public body's key on its official
website ([docs/attestations.md](https://github.com/lychnion/lychnion/blob/main/docs/attestations.md)
in the app repository), the holder of Lychnion's key signs an
attestation; the file goes into `attestations/` by pull request.

The values come from a public issue, so they go into a small JSON file
(for example `build/sendai.json`, not committed), never onto the command
line: a pasted `$(...)` or backtick would run in the same shell that
pipes the key. Write the file in an editor (or with a quoted heredoc,
`cat > build/sendai.json <<'EOF'`), never with `echo` or an unquoted
heredoc, and never paste issue text into a command.

```json
{"publisher": "jp.city.sendai", "key": "<the key from the publisher link>",
 "title": {"ja": "仙台市", "en": "Sendai City"},
 "url": "https://www.city.sendai.jp/…"}
```

```sh
op read op://Private/lychnion-package-signing-key/credential \
  | dart run tool/records.dart attest --key - --from build/sendai.json
# take it back (a newer file naming the newest attestation's key)
op read op://Private/lychnion-package-signing-key/credential \
  | dart run tool/records.dart attest --key - --withdraw --publisher jp.city.sendai
```

`attest` refuses a key not trusted for Lychnion, a result the app would
not accept, a method not written down, a check date in the future, a
second withdrawal, and a file that would not be newer than the
publisher's newest one (a clock behind).

`catalog` carries every file in `attestations/` next to the tool,
wherever it runs from (`--no-attestations` for a catalog without any;
another publisher's catalog, with `--public-key`, carries none unless
`--attestations DIR` is given). Build Lychnion's catalog from an
up-to-date `main` (`git pull` first): a stale clone leaves out a
withdrawal merged meanwhile, without an error. It stops when the folder is missing, when any
entry there is not a valid `<publisher>/<signed time>.json` at its own
path (a renamed `.bak`, a link; Finder's `.DS_Store` is skipped), or
when they would not all fit in 2 MB:
a withdrawal left out would leave a publisher shown as checked. `export`
passes on a catalog's attestations, `verify` reports them, and CI runs
`check-attestations`. Applications come in through the issue form "Ask
Lychnion to check your publisher key".

If Lychnion's key is **replaced**, its statements go into `keys.json`
(below); CI and the commands take `--keys keys.json`, so attestations
of the new key count. If it is **revoked**, its attestations lapse in the
app: a reviewed pull request moves its files to `attestations-lapsed/`
(kept for the record, never carried) and the ones still wanted are
signed again with the new key.

**Status.** Lychnion's catalog carries Lychnion's signed status
(lychnion/lychnion#117): the newest version of each item in it,
Lychnion's own key statements, and the attestations that are withdrawn
now. It travels unchanged through sticks, bases and nodes, so a phone
that got the status but an older file knows that something newer exists,
and a carrier cannot pass on the status without its revocations and
withdrawals. `catalog` signs it with the key from 1Password (`--key -`,
as for records) and refuses a key not trusted for Lychnion; without a
key it stops, unless `--no-status` (tests only: it prints "NO STATUS:
not for release"); it also stops when the items file names a newer
version than the signed records (sign it first). `export` passes a
catalog's statuses on (none signed more than a day ahead of the clock).
`verify` reports them, and notes a newer version held back elsewhere;
with `--release` (Lychnion's own catalog, before it is uploaded or
bundled into the app) it fails when there is no status of Lychnion's or a
record is newer than it (the catalog was not signed again).

When Lychnion replaces or revokes its key, the statements go into the
catalog with `catalog … --keys keys.json`; `export` keeps a catalog's
statements and `verify` applies them, so records of a revoked key then
fail `verify`. `catalog` and `export` refuse to write a catalog over
2 MB with its key statements, as the app refuses to read one (and
leaving statements out could leave a stolen key trusted).

**The root and the everyday key** (lychnion/lychnion#125). Lychnion's
built-in key is its *root*. The root can grant another key some
*scopes*: `data` (guides, maps, contour lines, the status), `models`,
`safety-lines` (the official alerts in a package) and `attestations`.
Only the root grants, revokes or replaces keys; the scope `keys` cannot
be granted. The everyday key holds all four scopes, so the root is
needed only for these key changes and can stay offline (#118). A grant
needs two signatures, and they are made in two steps, so neither key is
ever on the other one's computer:

```sh
# 1. with the new everyday key (on the everyday computer): a request
op read op://Private/lychnion-everyday-key/credential \
  | dart run tool/records.dart grant-request --key - \
      --scope data,models,safety-lines,attestations
# → build/grant-request.json, and it prints the new key: note it
# (on paper, or read it out), separately from the file
# 2. with the root: check the request, sign it, add it to keys/lychnion.json
dart run tool/records.dart grant --key /Volumes/ROOT-A/lychnion-root.lychnion-key --from build/grant-request.json \
    --expect-key <the new key noted in step 1>
# commit keys/lychnion.json
```

`grant` signs nothing unless the request grants the key given with
`--expect-key`, so a request swapped on the way is refused. It also
refuses a request for another root, one listing `keys`, and one changed
after the new key signed it. Every command (`sign`, `catalog`, `export`,
`check-records`, `attest`, and `verify` without `--release`) reads
`keys/lychnion.json` by itself, next to the tool wherever it runs from,
and stops when an entry in it is not valid, or made by a key that
Lychnion's root and its grants and replacements don't lead to (another
publisher's or a test key's, in the wrong file); CI checks it
with `check-keys` (a missing file means no statements: the review of the
pull request that deletes it is the guard). `verify --release` takes
only the statements the catalog carries (and `--keys` files given to it),
so a catalog without them fails. `sign` uses every key Lychnion trusts
to find an item's newest version, and refuses a key without the item's
scope (a package also needs `safety-lines`: without it the app would
drop its alerts).

`keys/lychnion.json` holds all of Lychnion's key statements, so
`grant` and `revoke-key` take no `--keys`: what they write must count
with that file alone. When the root is replaced, the replacement goes
into it too. The old root's grants and revocations then lapse: they stay
in the file for the record, and `check-keys` reports how many no longer
count (a former root's, or a revoked key's). Grant the everyday key again
with the new root, and revoke again what is still to be revoked. What
counts follows the app's own rules.

Start signing with the everyday key only once the app release with key
scopes is out and most phones have it: older builds ignore grants, so
they would not take records signed by it.

**The offline root** (lychnion/lychnion#118; the whole procedure is in
`docs/key-custody.md` in the app repository). The root lives only as an
encrypted key backup on two USB sticks, never on a disk; its passphrase
is in 1Password, apart from the sticks, and split on paper (SLIP-39, 2 of
3). Once both sticks open, the root's 1Password item is deleted:

```sh
# once: the backup, from the key in 1Password (the passphrase is asked
# twice at the terminal, never shown)
op read op://Private/lychnion-package-signing-key/credential \
  | dart run tool/records.dart backup --key - \
      --out /Volumes/ROOT-A/lychnion-root.lychnion-key
# check that it opens (prints the root's public key)
dart run tool/records.dart public --key /Volumes/ROOT-A/lychnion-root.lychnion-key
```

Any command takes the root as `--key FILE.lychnion-key` and asks for the
passphrase at the terminal. The backup carries no ready-made revocation
(the app's own backups do): whoever finds a stick can do nothing with it
without the passphrase, not even revoke Lychnion's root.
`keygen --1password --title lychnion-everyday-key` makes the everyday
key as a 1Password item of its own (without `--title` the item gets the
root's old name, `lychnion-package-signing-key`). The backup is written
with the strongest Argon2id cost the app reads, read back from the stick
and opened before `backup` says it is done; it never overwrites a file,
and refuses a folder that does not exist (a stick not mounted).

If a granted key is lost or stolen, the root revokes it:

```sh
dart run tool/records.dart revoke-key --key /Volumes/ROOT-A/lychnion-root.lychnion-key --revoked <its public key>
# commit keys/lychnion.json and publish a catalog: phones learn of the
# revocation from Lychnion's status
```

Everything the revoked key signed then stops counting: `sign` and
`check-records` stop at those records; remove them and sign the items
again with another key. Revoke a key only when it is lost or stolen.

**Area.** A package or map area can say which places it is for, as ISO
3166 codes: `"area": ["JP"]` for Japan, `["JP-30"]` for Wakayama. No area
means anywhere (first aid). Package manifests set it by hand; map areas
get it from the regions their evacuation sites are in (Nishinomiya:
`JP-27`, `JP-28`). The signed record carries it, so the app can show and
rank downloads for the user's region before downloading. Codes are checked
against the region file the app ships with (`assets/regions/regions.sqlite`
in the app repository, found next to this checkout or by
`LYCHNION_REGIONS`; `tool/check_areas.py` checks all manifests).

**Translations.** Passages in another language are translated once, by a
strong model, and stored in `packages/translations/<id>.<lang>.json`: for
each passage its original (`source`), the translation (`text`) and
`reviewed` (set by the person who checks it, never by a tool). The key is a
hash of the original. `tool/check_translations.py` runs in the build and in
CI, and writes a report per file to `build/translation-checks/` for the
reviewer:

- numbers and units must match (minutes, °C, phone numbers; 令和8年 =
  2026): the build fails otherwise;
- degree, negation and urgency words that appear on one side only ("not too
  tight", 「縛る」, "do not", 「すぐに」) are warnings, from the word list
  `packages/translations/terms.en-ja.json`.

A flag a person has checked and found fine gets an exception in the
passage, with a reason: `"accept": [{"check": "numbers", "item": "2",
"for": "<hash>", "reason": "..."}]`. `"for"` is the hash of the
translation text that was checked (the error message gives it), so editing
the translation brings the flag back. Headings take the same list in
`"accept_headings": {"<original heading>": [...]}`. When a source changes,
the build lists the passages that need a new translation and the stored
ones no longer used.

```sh
python3 -I tool/check_translations.py          # all translation files
python3 -I -m unittest discover -s tool -p 'test_*.py'
```

**Packages the app ships with.** The app bundles some packages and the
Nishinomiya evacuation sites, and its own copy of the catalog. After a
release, copy `build/packages/*.sqlite`, the bundled areas'
`build/maps/<id>.sites.sqlite` and `build/catalog.json` to the app's
`assets/packages/`, `assets/maps/` and `assets/downloads/`.

## Mirrors

Any web server, or a folder on a USB stick, can hold copies of the files.
Such a copy (a "node") keeps every file under its SHA-256:

```text
catalog.json
blobs/sha256/<sha256 of the file>
```

A catalog can list these nodes in `mirrors`. The app tries a file's own
address first, then each mirror. When one is unreachable, it goes on with
the next one from where it stopped. A file from a mirror is used only if
its SHA-256 matches the signed record, so a mirror needs no trust and no
signature.

To make a node from the files you have:

```sh
dart run tool/records.dart export catalog.json --out DIR FOLDER...
```

It copies every file it finds in the folders (only if it matches its
record) and writes a `catalog.json` that points to them.

## Files

| Item | Files | What it is |
|---|---|---|
| `jp.bousai.core` | `jp.bousai.core.sqlite` | Knowledge package "防災（基本）" (disaster preparedness basics): passages from official Japanese pages, with their sources and an English translation |
| `jp.outdoor.hiking` | `jp.outdoor.hiking.sqlite` | Knowledge package "登山（基本）" (hiking basics): getting lost, lightning, heat, ticks, hornets, volcanoes |
| `intl.firstaid.basics` | `intl.firstaid.basics.sqlite` | Knowledge package "応急手当" (first aid): bites, cold injuries, heat illness, from US government manuals, with a Japanese translation |
| `jp.map.nishinomiya` | `.pmtiles`, `.sites.sqlite`, `.routes.bin`, `.inundation.mbtiles` | Offline map of the Nishinomiya area (eastern Kobe to western Osaka) |
| `jp.map.kushimoto` | `.pmtiles`, `.sites.sqlite`, `.routes.bin`, `.inundation.mbtiles` | Offline map of Kushimoto (Wakayama) and around |
| `jp.map.nishinomiya.contours` | `jp.map.nishinomiya.contours.pmtiles` | Contour lines for the Nishinomiya map (optional) |
| `jp.map.kushimoto.contours` | `jp.map.kushimoto.contours.pmtiles` | Contour lines for the Kushimoto map (optional) |

Each map area has four files: the base map (vector tiles), evacuation
sites, walking routes (roads and paths with ground height and expected
tsunami inundation for every point), and tsunami inundation areas as map
tiles.

The build also makes a fifth file, `<id>.contours.pmtiles` (contour
lines and coastline). It is its own item, `<id>.contours`, so people can
skip it (lychnion/lychnion#57). Its kind is `map`, so every app version
reads it, but no app version takes it as a base map: it has no
`.sites.sqlite`, and its only `.pmtiles` ends in `.contours.pmtiles`. It
lands in the same folder as the map, where the app (since
lychnion/lychnion#84) finds the file next to the base map and draws it;
older versions install it but never open it. Deleting the map leaves the
contours installed until they are deleted too.

Its `area` is set in `items.json` (there is no sites file to take it
from); `records.dart sign` refuses it unless it matches the newest
signed record of its map.

## Sources and licences

Every package also lists its sources and licences inside the file, and
the app shows the source with each answer.

### 防災（基本） `jp.bousai.core`

Public Data License (Japan) 1.0
([PDL 1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)).
The text was split into passages; some pages were edited (加工).

- 出典：首相官邸ホームページ「防災の手引き」
  (https://www.kantei.go.jp/jp/headline/bousai/ : sonae, jishin, tsunami,
  hinan, setsugai, funka)
- 出典：消防庁ホームページ「口頭指導要領 止血法」
  (https://www.fdma.go.jp/laws/item/laws014_04_20130517-3w.doc)
- 出典：政府広報オンライン「クマに出会わないために。知っておきたい対策」
  (https://www.gov-online.go.jp/article/202609/entry-11625.html) を加工して作成
- 出典：政府広報オンライン「山の事故を防ごう！登山を楽しむために知っておきたい安全対策」
  (https://www.gov-online.go.jp/article/201407/entry-9829.html) を加工して作成

The English text is Lychnion's translation, not an official one.

### 登山（基本） `jp.outdoor.hiking`

PDL 1.0, as above. The English text is Lychnion's translation.

- 出典：警察庁ウェブサイト「令和７年における山岳遭難の概況等」
- 出典：気象庁ホームページ「雷」による災害、積乱雲が近づくサインを見逃さない、
  積乱雲が近づいてきたら…、噴火速報の説明、噴火警戒レベルの説明
- 出典：環境省熱中症予防情報サイト「熱中症の予防方法と対処方法」
- 出典：厚生労働省ホームページ「重症熱性血小板減少症候群（SFTS）に関するQ&A」
- 出典：林野庁ホームページ「森林内等の作業におけるダニ刺咬予防対策」「蜂刺され災害を防ごう」

The exact page addresses are in the package and shown in the app.

### 応急手当 `intl.firstaid.basics`

Public domain: works of the US government
([17 U.S.C. 105](https://www.law.cornell.edu/uscode/text/17/105)), marked
"Approved for public release; distribution is unlimited". Only the civilian
first-aid chapters are used.

- US Army, ATP 4-02.11 *Casualty Response, Tactical Combat Casualty Care,
  and First Aid* (2026), chapters 16–17
- US Army, TC 21-3 *Cold Weather Survival and Operations* (2025), cold
  injuries

The Japanese text is Lychnion's translation, not an official one, and has
not yet been checked by a medical expert.

### Base maps

© OpenStreetMap contributors, under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
Built with [Protomaps](https://protomaps.com/).

Each map manifest names the Protomaps daily build it is cut from
(`basemap.source`). Protomaps keeps daily builds for about a week (only
the last build of each Protomaps version stays longer), so a new base map
usually needs a newer build in the manifest; `build_map.py` says so and
names the newest one. Changing the build in the manifest does not change
released base maps: only a rebuild uses it, and a rebuilt file ships as a
new version.

### Contour lines

出典：国土地理院最適化ベクトルタイルを加工して作成 (only the contour and
coastline layers, cut to each map area). Used under the
[国土地理院コンテンツ利用規約](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html).
The same credit (without 出典：) is in each file's metadata
(`attribution`).

GSI marks the file as 検証中 (being tested), so its address or layers may
change. Each map manifest records the date of the GSI file it was built
from (`contours.file_date`, the UTC date of the server's Last-Modified),
and the build stops when the server has a file of another date.

### Walking routes

Roads and paths: © OpenStreetMap contributors, under the
[ODbL](https://opendatacommons.org/licenses/odbl/), from the
[Geofabrik](https://download.geofabrik.de/) Kansai extract.

Heights: 出典：国土地理院 標高タイル（基盤地図情報 数値標高モデル DEM5A・DEM10B）を加工して作成.
Used under the [国土地理院コンテンツ利用規約](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html).

### Tsunami inundation areas

出典：「国土数値情報（津波浸水想定データ）」（国土交通省）
(https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A40-2024.html),
cut to each map area and turned into map tiles and per-point values
(リクニオンが加工). Under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.ja).
Nishinomiya: 兵庫県 2016 and 大阪府 2023 data. Kushimoto: 和歌山県 2016 data.

These are the prefectures' expected inundation for the largest assumed
tsunami. A real tsunami can be larger or smaller.

### Evacuation sites

出典：国土地理院「指定緊急避難場所データ」「指定避難所データ」（リクニオンが範囲を絞って加工）
Used under the [国土地理院コンテンツ利用規約](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html).

GSI asks that these notes are passed on with the data (summary in English
below):

> 【ご利用上の注意】
>
> 1.本データは、災害対策基本法第49条の4・第49条の7に基づき市町村長が指定した指定緊急避難場所・指定避難所の情報を各市町村に登録いただいたものです。
> なお、最新でない場合や未掲載の場合があるため、最新かつ詳細の状況などは必ず当該市町村にご確認ください。
>
> 2.「指定緊急避難場所」と「指定避難所」の違いや、指定緊急避難場所が災害種別ごとに指定されていることを理解した上でご利用ください。
>
> 3.本データは随時更新されますので、利用される場合は、ご注意ください。
>
> ※指定緊急避難場所とは、災害の危険から命を守るために緊急的に避難する場所です。土砂災害、洪水、津波、地震等の災害種別ごとに指定が行われます。
>
> ※指定避難所とは、災害が発生した場合に避難をしてきた被災者が一定期間滞在するための施設です。

In short: the sites were registered by each city and may be out of date or
missing, so check with the city for the latest. Emergency evacuation sites
(指定緊急避難場所) are designated per hazard (tsunami, flood, landslide,
earthquake and so on) and are where you flee to; shelters (指定避難所) are
where people stay after the danger has passed.

## No warranty

Lychnion is not yet released; this data is for testing. Do not rely on it
alone in an emergency. Follow official warnings and your city's
instructions.

## Licence

The tools in `tool/` are under the GNU AGPL 3.0 ([LICENSE](LICENSE)), as
the app. The data keeps the licence of each source (see "Sources and
licences").
