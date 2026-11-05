# fieldkit-data

Data files for **fieldkit**, a proof of concept of an offline AI helper for
disaster preparedness and the outdoors in Japan ("fieldkit" is a code name).
The app downloads these files once and then works without a network.

The files are in the [releases](https://github.com/dkastl/fieldkit-data/releases).
Each release has a `catalog.json` with the size and SHA-256 of every file;
the app checks both before it uses a file. The newest catalog is always at
`https://github.com/dkastl/fieldkit-data/releases/latest/download/catalog.json`.

The AI models are not here: the app downloads them from Hugging Face
(`ggml-org/gemma-4-E2B-it-GGUF`, `ggml-org/gemma-4-E4B-it-GGUF`,
`ggml-org/embeddinggemma-2-GGUF`, Apache 2.0).

## Files

| File | What it is |
|---|---|
| `jp.bousai.core.sqlite` | Knowledge package "防災（基本）": passages from official Japanese pages on disaster preparedness, with their sources |
| `jp.map.nishinomiya.pmtiles` | Base map of the Nishinomiya area (eastern Kobe to western Osaka), vector tiles, zoom 0 to 15 |
| `jp.map.nishinomiya.sites.sqlite` | Evacuation sites in the same area, from GSI |
| `jp.map.nishinomiya.routes.bin` | Walking routes for the same area: roads and paths from OpenStreetMap, with the ground height and the expected tsunami inundation of every point (from release 2026.11.1) |
| `jp.map.nishinomiya.inundation.mbtiles` | Expected tsunami inundation areas for the same area as map tiles, coloured by depth (from release 2026.11.1) |
| `catalog.json` | List of the files with version, size and SHA-256 |

## Sources and licences

### Knowledge package

All sources are published under the Public Data License (Japan) 1.0
([PDL 1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)).
The text was split into passages; the bears and mountain pages were also
edited (加工).

- 出典：首相官邸ホームページ「防災の手引き」
  (https://www.kantei.go.jp/jp/headline/bousai/ : sonae, jishin, tsunami,
  hinan, setsugai, funka)
- 出典：消防庁ホームページ「口頭指導要領 止血法」
  (https://www.fdma.go.jp/laws/item/laws014_04_20130517-3w.doc)
- 出典：政府広報オンライン「クマに出会わないために。知っておきたい対策」
  (https://www.gov-online.go.jp/article/202609/entry-11625.html) を加工して作成
- 出典：政府広報オンライン「山の事故を防ごう！登山を楽しむために知っておきたい安全対策」
  (https://www.gov-online.go.jp/article/201407/entry-9829.html) を加工して作成

### Base map

© OpenStreetMap contributors, under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
Built with [Protomaps](https://protomaps.com/).

### Walking routes

Roads and paths: © OpenStreetMap contributors, under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/),
from the [Geofabrik](https://download.geofabrik.de/) Kansai extract.

Heights: 出典：国土地理院 標高タイル（基盤地図情報 数値標高モデル DEM5A・DEM10B）を加工して作成.
Used under the [国土地理院コンテンツ利用規約](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html).

### Tsunami inundation areas

出典：「国土数値情報（津波浸水想定データ）」（国土交通省）
(https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A40-2024.html),
兵庫県 2016 and 大阪府 2023 data, cut to the map area and turned into map
tiles and per-point values (fieldkit が加工). Under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.ja).

These are the prefectures' expected inundation for the largest assumed
tsunami. A real tsunami can be larger or smaller.

### Evacuation sites

出典：国土地理院「指定緊急避難場所データ」「指定避難所データ」（fieldkit が範囲を絞って加工）
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

This is test data for a proof of concept. Do not rely on it alone in an
emergency; follow official warnings and your city's instructions.
