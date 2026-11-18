// Signs package records and assembles the download catalog.
//
// Records and catalogs: docs/distribution.md in lychnion/lychnion. The private key is in
// 1Password (item `lychnion-package-signing-key`, field `credential`) and
// is read from stdin, so it never touches the disk:
//
//   # once: make the key pair and store it in 1Password
//   dart run tool/records.dart keygen --1password \
//     | op item create --vault Private -
//
//   # print the public key (goes into lychnion/lychnion:
//   # packages/lychnion_format/lib/src/publishers.dart)
//   op read op://Private/lychnion-package-signing-key/credential \
//     | dart run tool/records.dart public --key -
//
//   # sign records for built packages (versions only go up)
//   op read op://Private/lychnion-package-signing-key/credential \
//     | dart run tool/records.dart sign --key - \
//         --items items.json --records records \
//         jp.bousai.core jp.map.kushimoto
//
//   # newest record of every item → catalog.json
//   dart run tool/records.dart catalog --items items.json \
//     --records records \
//     --base https://github.com/lychnion/lychnion-data/releases/download/2026.11.2/ \
//     --out build/catalog.json \
//     [--mirror https://mirror.example.org/lychnion/ ...]
//
//   # a static node: catalog.json plus every file as blobs/sha256/<hash>,
//   # for a USB stick or any web host (files found in the FOLDERs by name)
//   dart run tool/records.dart export build/catalog.json \
//     --out /Volumes/STICK/lychnion build/packages build/maps
//   (--source-url URL: where the catalog came from, when it has no `base`
//   and its addresses are relative)
//
//   # check a catalog's signatures, and local files against it
//   dart run tool/records.dart verify build/catalog.json [FOLDER]
//
//   # check every record in the records folder (also run by CI)
//   dart run tool/records.dart check-records records
//
// Mirrors (`--mirror`, repeatable) are nodes that keep files by hash under
// `blobs/sha256/`. The app tries a file's own address first, then each
// mirror. They are not signed: the hash in the signed record decides.
//
// items.json describes each item (title, note, licence, version, and
// its files: a local `path`, or `size`, `sha256` and `url` for files
// hosted elsewhere, like the models on Hugging Face). Packages take their
// notice from the SQLite file, and their version must match its meta.
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart' as crypto;
import 'package:lychnion_format/lychnion_format.dart';

const _refresh =
    'https://github.com/lychnion/lychnion-data/releases/latest/download/catalog.json';

Never _fail(String msg) {
  stderr.writeln(msg);
  exit(1);
}

Future<void> main(List<String> argv) async {
  if (argv.isEmpty) _fail('Usage: see the comment at the top of this file.');
  final args = _Args(argv.skip(1).toList());
  switch (argv.first) {
    case 'keygen':
      await _keygen(args);
    case 'public':
      final pair = await keyPairFromSeed(await _readKey(args.req('--key')));
      stdout.writeln(await publicKeyOf(pair));
    case 'sign':
      await _sign(args);
    case 'catalog':
      await _catalog(args);
    case 'export':
      await _export(args);
    case 'verify':
      await _verify(args);
    case 'check-records':
      await _checkRecords(args);
    default:
      _fail('Unknown command ${argv.first}');
  }
}

class _Args {
  _Args(this.rest);
  final List<String> rest;

  String? opt(String name) {
    final i = rest.indexOf(name);
    if (i < 0) return null;
    if (i + 1 >= rest.length) _fail('$name needs a value');
    final v = rest[i + 1];
    rest.removeRange(i, i + 2);
    return v;
  }

  String req(String name) => opt(name) ?? _fail('Missing $name');

  /// All values of a repeatable option.
  List<String> all(String name) => [
    for (String? v; (v = opt(name)) != null;) v!,
  ];

  bool flag(String name) => rest.remove(name);
}

Future<String> _readKey(String from) async {
  final k = from == '-'
      ? (await stdin.transform(utf8.decoder).join()).trim()
      : (await File(from).readAsString()).trim();
  if (k.isEmpty) _fail('No key received (did `op read` fail?)');
  return k;
}

Future<void> _keygen(_Args args) async {
  final k = await newKeyPair();
  if (!args.flag('--1password')) {
    stdout.writeln(k.seed);
    stderr.writeln('public key: ${k.publicKey}');
    return;
  }
  // API Credential item (1Password conventions: lowercase-hyphen title,
  // slash tags). Piped into `op item create -`, so the key is never shown.
  stdout.writeln(
    jsonEncode({
      'title': 'lychnion-package-signing-key',
      'category': 'API_CREDENTIAL',
      'tags': ['product/lychnion', 'kind/signing-key'],
      'fields': [
        {
          'id': 'credential',
          'type': 'CONCEALED',
          'label': 'credential',
          'value': k.seed,
        },
        {
          'id': 'public_key',
          'type': 'STRING',
          'label': 'public key',
          'value': k.publicKey,
        },
        {
          'id': 'notesPlain',
          'type': 'STRING',
          'purpose': 'NOTES',
          'label': 'notesPlain',
          'value':
              'Ed25519 private seed (base64url) that signs Lychnion package '
              'records. Used by tool/records.dart in lychnion/lychnion-data. '
              'The public key is built into the app '
              '(lychnion/lychnion: packages/lychnion_format/lib/src/publishers.dart). '
              'Never copy it to a node or '
              'a repository.',
        },
      ],
    }),
  );
  stderr.writeln('public key: ${k.publicKey}');
}

/// `--public-key K` checks against K (tests, other publishers' catalogs);
/// default: the keys built into the app.
TrustedKeys _trust(_Args args) {
  final k = args.opt('--public-key');
  return k == null
      ? builtInPublishers
      : TrustedKeys({
          'lychnion': [k],
        });
}

Map<String, Object?> _readJson(String path) =>
    (jsonDecode(File(path).readAsStringSync()) as Map).cast();

Future<String> _sha256(File f) async =>
    (await crypto.sha256.bind(f.openRead()).first).toString();

String _sqliteMeta(String path, String key) {
  final r = Process.runSync('sqlite3', [
    '-readonly',
    path,
    "SELECT value FROM meta WHERE key = '$key'",
  ]);
  if (r.exitCode != 0) _fail('$path: ${r.stderr}');
  return (r.stdout as String).trim();
}

/// `2026-11-18T09:00:00+09:00`
String _now() {
  final t = DateTime.now();
  final o = t.timeZoneOffset;
  String two(int n) => n.abs().toString().padLeft(2, '0');
  final date = t.toIso8601String().substring(0, 19);
  return '$date${o.isNegative ? '-' : '+'}${two(o.inHours)}:${two(o.inMinutes % 60)}';
}

/// The newest record of [id] in [dir]/[id]/, verified with [trust].
Future<PackageRecord?> _newest(
  Directory dir,
  String id,
  TrustedKeys trust,
) async {
  final d = Directory('${dir.path}/$id');
  if (!d.existsSync()) return null;
  PackageRecord? best;
  for (final f in d.listSync().whereType<File>()) {
    if (!f.path.endsWith('.json')) continue;
    final r = await trust.check(jsonDecode(f.readAsStringSync()));
    if (r == null) _fail('${f.path}: bad record or signature');
    if (r.id != id) _fail('${f.path}: record of ${r.id}');
    if (best == null || compareVersions(r.version, best.version) > 0) {
      best = r;
    }
  }
  return best;
}

Map<String, Object?> _withoutSignature(Map<String, Object?> j) =>
    Map.of(j)
      ..removeWhere((k, _) => const {'signed', 'signature', 'key'}.contains(k));

Future<void> _sign(_Args args) async {
  final keyFrom = args.req('--key');
  final items = _readJson(args.req('--items'));
  final dir = Directory(args.req('--records'));
  final ids = args.rest;
  if (ids.isEmpty) _fail('Name the items to sign.');
  final pair = await keyPairFromSeed(await _readKey(keyFrom));
  final publisher = items['publisher']! as String;
  final trust = TrustedKeys({
    publisher: [await publicKeyOf(pair)],
  });
  final byId = {
    for (final i in items['items']! as List) (i as Map)['id'] as String: i,
  };
  for (final id in ids) {
    final item = byId[id] ?? _fail('$id: not in the items file');
    final unsigned = await _recordFor(publisher, item.cast());
    final version = unsigned['version']! as String;
    final last = await _newest(dir, id, trust);
    if (last != null) {
      final c = compareVersions(version, last.version);
      if (c < 0) {
        _fail('$id $version: older than the signed ${last.version}');
      }
      if (c == 0) {
        if (canonicalJson(_withoutSignature(last.json)) ==
            canonicalJson(_withoutSignature(unsigned))) {
          stdout.writeln('$id $version: already signed, unchanged');
          continue;
        }
        _fail(
          '$id $version: already signed with other content. '
          'Raise the version (versions only go up).',
        );
      }
    }
    final r = await PackageRecord.sign(unsigned, pair);
    final out = File('${dir.path}/$id/$version.json');
    await out.parent.create(recursive: true);
    await out.writeAsString(
      '${const JsonEncoder.withIndent('  ').convert(r.json)}\n',
    );
    stdout.writeln('$id $version: signed → ${out.path}');
  }
}

Future<Map<String, Object?>> _recordFor(
  String publisher,
  Map<String, Object?> item,
) async {
  final id = item['id']! as String;
  final version = item['version']! as String;
  final files = <Map<String, Object?>>[];
  Map<String, Object?>? notice = (item['notice'] as Map?)?.cast();
  for (final f in (item['files']! as List).cast<Map>()) {
    final name = f['name']! as String;
    final path = f['path'] as String?;
    if (path == null) {
      files.add({'name': name, 'size': f['size'], 'sha256': f['sha256']});
      continue;
    }
    final file = File(path);
    if (!file.existsSync()) _fail('$id: $path not found (build it first)');
    files.add({
      'name': name,
      'size': file.lengthSync(),
      'sha256': await _sha256(file),
    });
    if (item['kind'] == 'package' && name.endsWith('.sqlite')) {
      final built = _sqliteMeta(path, 'version');
      if (built != version) {
        _fail('$id: items file says $version, $path says $built');
      }
      final n = _sqliteMeta(path, 'notice');
      if (notice == null && n.isNotEmpty) {
        notice = (jsonDecode(n) as Map).cast();
      }
    }
  }
  return {
    'format': 1,
    'id': id,
    'kind': item['kind'],
    'version': version,
    'publisher': publisher,
    'title': item['title'],
    if (item['note'] != null) 'note': item['note'],
    'licence': item['licence'] ?? '',
    if (notice != null && notice.isNotEmpty) 'notice': notice,
    if (item['role'] != null) 'role': item['role'],
    if (item['desktop'] == true) 'desktop': true,
    'files': files,
    'signed': _now(),
  };
}

Future<void> _catalog(_Args args) async {
  final items = _readJson(args.req('--items'));
  final dir = Directory(args.req('--records'));
  final base = args.req('--base');
  final out = args.req('--out');
  final refresh = args.opt('--refresh') ?? _refresh;
  final mirrors = args.all('--mirror');
  for (final m in mirrors) {
    final u = Uri.tryParse(m);
    if (u == null ||
        !(u.isScheme('https') || u.isScheme('http')) ||
        u.host.isEmpty) {
      _fail('--mirror $m: not an http(s) address');
    }
  }
  final trust = _trust(args);
  final records = <Map<String, Object?>>[];
  final urls = <String, String>{};
  for (final i in (items['items']! as List).cast<Map>()) {
    final id = i['id']! as String;
    final r = await _newest(dir, id, trust);
    if (r == null) {
      stderr.writeln('$id: no signed record, left out');
      continue;
    }
    if (r.version != i['version']) {
      stderr.writeln(
        '$id: newest record is ${r.version}, items file says ${i['version']}',
      );
    }
    records.add(r.json);
    // Where files hosted elsewhere come from; the rest is fetched by name
    // next to the catalog (or from `base`).
    final url = {
      for (final f in (i['files']! as List).cast<Map>())
        if (f['url'] != null) f['name'] as String: f['url'] as String,
    };
    for (final f in r.files) {
      if (url[f.name] != null) urls[f.sha256] = url[f.name]!;
    }
  }
  final catalog = {
    'format': 2,
    'base': base,
    'refresh': refresh,
    'records': records,
    // One address per hash, as a string: older apps read only that.
    'urls': urls,
    if (mirrors.isNotEmpty)
      'mirrors': [for (final m in mirrors) m.endsWith('/') ? m : '$m/'],
  };
  await File(
    out,
  ).writeAsString('${const JsonEncoder.withIndent('  ').convert(catalog)}\n');
  stdout.writeln('$out: ${records.length} records');
}

/// Writes a static node: the catalog (records verified and unchanged) and
/// every file found in the source folders as `blobs/sha256/<hash>`, which
/// the catalog then points to. Files are copied only when their size and
/// hash match the signed record; files not found keep their address from
/// the source catalog (the models on Hugging Face, for example).
Future<void> _export(_Args args) async {
  final out = Directory(args.req('--out'));
  final sourceUrl = args.opt('--source-url');
  final trust = _trust(args);
  if (args.rest.isEmpty) _fail('Usage: export CATALOG --out DIR [FOLDER...]');
  final j = _readJson(args.rest.first);
  if (j['format'] != 2) _fail('Not a format 2 catalog');
  final folders = args.rest.skip(1).toList();
  // Where the source catalog gets each file, for files not exported.
  final base = j['base'] is String
      ? Uri.parse(j['base']! as String)
      : sourceUrl == null
      ? null
      : Uri.parse(sourceUrl);
  // Mirrors as absolute addresses: relative ones are resolved against
  // where the source catalog came from (--source-url, else its base), as
  // the app does; without either they are left out.
  final origin = sourceUrl != null ? Uri.parse(sourceUrl) : base;
  // Only what the app accepts as a mirror: http(s) with a host.
  bool web(Uri u) =>
      (u.isScheme('http') || u.isScheme('https')) && u.host.isNotEmpty;
  final mirrors = <String>[
    for (final m in (j['mirrors'] as List?) ?? const [])
      if (m is String)
        if (Uri.tryParse(m) case final u?)
          if (u.hasScheme)
            if (web(u)) '$u' else ...[]
          else if (origin != null)
            if (web(origin.resolveUri(u))) '${origin.resolveUri(u)}',
  ];
  final hasMirrors = mirrors.isNotEmpty;
  final from = ((j['urls'] as Map?) ?? const {}).cast<String, Object?>();
  String? elsewhere(RecordFile f) {
    final listed = from[f.sha256];
    final u = Uri.tryParse(listed is String ? listed : f.name);
    if (u == null) return null;
    if (u.hasScheme) return '$u';
    return base == null ? null : '${base.resolveUri(u)}';
  }

  final blobs = Directory('${out.path}/blobs/sha256');
  await blobs.create(recursive: true);
  final records = <Map<String, Object?>>[];
  final urls = <String, String>{};
  var missing = 0;
  for (final raw in j['records']! as List) {
    final r = await trust.check(raw);
    if (r == null) _fail('${(raw as Map)['id']}: bad signature or record');
    records.add(r.json);
    for (final f in r.files) {
      final blob = File('${blobs.path}/${f.sha256}');
      if (blob.existsSync() &&
          blob.lengthSync() == f.size &&
          await _sha256(blob) == f.sha256) {
        stdout.writeln('have   ${f.name}');
        urls[f.sha256] = 'blobs/sha256/${f.sha256}';
        continue;
      }
      File? src;
      for (final d in folders) {
        final c = File('$d/${f.name}');
        if (c.existsSync() &&
            c.lengthSync() == f.size &&
            await _sha256(c) == f.sha256) {
          src = c;
          break;
        }
      }
      if (src == null) {
        missing++;
        stdout.writeln('none   ${f.name} (${r.id} ${r.version})');
        if (elsewhere(f) case final u?) {
          urls[f.sha256] = u;
        } else if (!hasMirrors) {
          _fail(
            '${f.name}: not in the folders and no address to get it from. '
            'Add the folder that has it, or give --source-url.',
          );
        }
        continue;
      }
      final part = File('${blob.path}.part');
      await src.copy(part.path);
      await part.rename(blob.path);
      urls[f.sha256] = 'blobs/sha256/${f.sha256}';
      stdout.writeln('copied ${f.name}');
    }
  }
  final catalog = {
    'format': 2,
    if (j['refresh'] != null) 'refresh': j['refresh'],
    'records': records,
    'urls': urls,
    if (mirrors.isNotEmpty) 'mirrors': mirrors,
  };
  await File(
    '${out.path}/catalog.json',
  ).writeAsString('${const JsonEncoder.withIndent('  ').convert(catalog)}\n');
  stdout.writeln(
    '${out.path}: ${records.length} records'
    '${missing > 0 ? ', $missing files not found (fetched elsewhere)' : ''}',
  );
}

Future<void> _verify(_Args args) async {
  final trust = _trust(args);
  if (args.rest.isEmpty) _fail('Usage: verify CATALOG [FOLDER]');
  final j = _readJson(args.rest.first);
  final folder = args.rest.length > 1 ? args.rest[1] : null;
  if (j['format'] != 2) _fail('Not a format 2 catalog');
  var bad = 0;
  for (final raw in j['records']! as List) {
    final r = await trust.check(raw);
    if (r == null) {
      bad++;
      stdout.writeln('BAD  ${(raw as Map)['id']}: signature or record');
      continue;
    }
    stdout.writeln('ok   ${r.id} ${r.version} (${r.publisher}, ${r.keyId})');
    if (folder == null) continue;
    for (final f in r.files) {
      final file = File('$folder/${f.name}');
      if (!file.existsSync()) continue;
      final ok = file.lengthSync() == f.size && await _sha256(file) == f.sha256;
      if (!ok) bad++;
      stdout.writeln('${ok ? 'ok  ' : 'BAD '}   ${f.name}');
    }
  }
  if (bad > 0) _fail('$bad problems');
}

/// Every record file under FOLDER (`<id>/<version>.json`) must be signed by
/// a trusted key and match its place: id and version as in its path.
Future<void> _checkRecords(_Args args) async {
  final trust = _trust(args);
  if (args.rest.isEmpty) _fail('Usage: check-records FOLDER');
  final files =
      Directory(args.rest.first)
          .listSync(recursive: true)
          .whereType<File>()
          .where((f) => f.path.endsWith('.json'))
          .toList()
        ..sort((a, b) => a.path.compareTo(b.path));
  if (files.isEmpty) _fail('No records in ${args.rest.first}');
  var bad = 0;
  for (final f in files) {
    final segs = f.uri.pathSegments;
    final id = segs[segs.length - 2];
    final version = segs.last.substring(0, segs.last.length - 5);
    final r = await trust.check(_readJson(f.path));
    final ok = r != null && r.id == id && r.version == version;
    if (!ok) bad++;
    stdout.writeln('${ok ? 'ok ' : 'BAD'}  $id $version');
  }
  if (bad > 0) _fail('$bad bad records');
}
