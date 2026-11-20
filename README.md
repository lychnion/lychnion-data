# lychnion-data

Data for **Lychnion**, an offline safety assistant for Japan. The app
answers questions from knowledge packages and shows offline maps. It
downloads these files once and then works without a network.

This repository holds:

- the **signed records** of every package version (`records/`),
- Lychnion's **public key** (`keys/lychnion.pub`),
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

# a map area → build/maps/ (sites, base map, walking routes, inundation tiles)
python3 -I tool/build_map.py maps/kushimoto.json
python3 -m venv build/route-venv
build/route-venv/bin/pip install osmium pillow pyshp shapely
build/route-venv/bin/python -I tool/build_routes.py maps/kushimoto.json
build/route-venv/bin/python -I tool/build_inundation_tiles.py maps/kushimoto.json

# sign the records and write the catalog (key from 1Password, never on disk)
dart pub get
op read op://Private/lychnion-package-signing-key/credential \
  | dart run tool/records.dart sign --key - --items items.json --records records jp.bousai.core
dart run tool/records.dart catalog --items items.json --records records \
  --base https://github.com/lychnion/lychnion-data/releases/download/<release>/ \
  --out build/catalog.json
```

The top of each tool says more. Records and catalogs use the format
code shared with the app (`packages/lychnion_format` in
[lychnion/lychnion](https://github.com/lychnion/lychnion): `PackageRecord`,
`CatalogDocument`, `KeysFile`), so the tools write what the app, a base
and a node read. `pubspec.yaml` names the commit it uses (`ref`), so a
change on the app's `main` never changes the tools by surprise: to move
it, set `ref` to a newer commit on the app's `main` (never a PR branch:
it may disappear) and run `dart pub upgrade lychnion_format`.

When Lychnion replaces or revokes its key, the statements go into the
catalog with `catalog … --keys keys.json`; `export` keeps a catalog's
statements and `verify` applies them, so records of a revoked key then
fail `verify`. `catalog` and `export` refuse to write a catalog over
2 MB with its key statements, as the app refuses to read one (and
leaving statements out could leave a stolen key trusted).

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

Each map area has four files: the base map (vector tiles), evacuation
sites, walking routes (roads and paths with ground height and expected
tsunami inundation for every point), and tsunami inundation areas as map
tiles.

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
