// Signs package records and assembles the download catalog.
//
// Records and catalogs: docs/distribution.md in lychnion/lychnion. A
// private key comes from 1Password (field `credential`) on stdin, so it
// never touches the disk; Lychnion's offline root comes from its
// encrypted key backup on a USB stick (`--key FILE.lychnion-key`, the
// passphrase typed at the terminal; docs/key-custody.md in the app
// repository):
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
//   # newest record of every item → catalog.json, with Lychnion's signed
//   # status (lychnion/lychnion#117: the newest version of each item, its
//   # key statements and withdrawn attestations), so phones can tell when
//   # a carrier held something back
//   op read op://Private/lychnion-package-signing-key/credential \
//     | dart run tool/records.dart catalog --key - --items items.json \
//     --records records \
//     --base https://github.com/lychnion/lychnion-data/releases/download/2026.11.2/ \
//     --out build/catalog.json \
//     [--mirror https://mirror.example.org/lychnion/ ...] \
//     [--keys keys.json ...]
//   (--keys: key statements of a replaced or revoked key, as the desktop
//   app writes them; only those made by a trusted key are taken.
//   --no-status instead of --key: a catalog without a status, for tests)
//
//   # a static node: catalog.json plus every file as blobs/sha256/<hash>,
//   # for a USB stick or any web host (files found in the FOLDERs by name)
//   dart run tool/records.dart export build/catalog.json \
//     --out /Volumes/STICK/lychnion build/packages build/maps
//   (--source-url URL: where the catalog came from, when it has no `base`
//   and its addresses are relative; the catalog's key statements are kept,
//   and --keys FILE adds more)
//
//   # check a catalog's signatures, and local files against it (its own
//   # key statements apply; --keys FILE adds more). --release: Lychnion's
//   # own catalog before a release: it must carry Lychnion's status, and
//   # no record may be newer than that status
//   dart run tool/records.dart verify build/catalog.json [FOLDER] [--keys FILE]
//     [--release]
//
//   # check every record in the records folder (also run by CI; after a
//   # key replacement add --keys keys.json)
//   dart run tool/records.dart check-records records
//
//   # attest that a publisher's key belongs to an official body, after
//   # checking it (docs/attestations.md in lychnion/lychnion); writes
//   # attestations/<publisher>/<time>.json for a pull request. The values
//   # come from a public issue: put them in a JSON file, never on the
//   # command line, where a pasted `$(...)` would run next to the key:
//   #   {"publisher": "jp.city.sendai", "key": "<from the publisher link>",
//   #    "title": {"ja": "仙台市", "en": "Sendai City"},
//   #    "url": "https://www.city.sendai.jp/…"}
//   op read op://Private/lychnion-package-signing-key/credential \
//     | dart run tool/records.dart attest --key - --from build/sendai.json
//         [--checked 2026-11-23]
//   (take it back: --withdraw --publisher NAME; it names the key of the
//   newest attestation for that publisher)
//
//   # check every attestation in the attestations folder (also run by CI)
//   dart run tool/records.dart check-attestations attestations
//
//   # grant the everyday key scopes (lychnion/lychnion#125; README "The
//   # root and the everyday key"). Step 1, with the new key: a request
//   op read op://Private/lychnion-everyday-key/credential \
//     | dart run tool/records.dart grant-request --key - \
//         --scope data,models,safety-lines,attestations
//         [--root KEY] [--out build/grant-request.json]
//   # step 2, with the root: check it, sign it, add it to keys/lychnion.json
//   # (--expect-key: the new key step 1 printed, noted apart from the file)
//   dart run tool/records.dart grant --key - --from build/grant-request.json
//       --expect-key KEY
//   # the root revokes a granted key that was lost or stolen
//   dart run tool/records.dart revoke-key --key - --revoked KEY
//   # check keys/lychnion.json (also run by CI)
//   dart run tool/records.dart check-keys
//
//   # the offline root (lychnion/lychnion#118, docs/key-custody.md there):
//   # an encrypted backup on a USB stick, without a ready-made revocation;
//   # the passphrase is asked twice at the terminal
//   op read op://Private/lychnion-package-signing-key/credential \
//     | dart run tool/records.dart backup --key - \
//         --out /Volumes/ROOT-A/lychnion-root.lychnion-key
//   # any command then takes the root as --key FILE.lychnion-key and asks
//   # for the passphrase (Wi-Fi off, see docs/key-custody.md)
//   dart run tool/records.dart grant --key /Volumes/ROOT-A/lychnion-root.lychnion-key \
//     --from build/grant-request.json --expect-key KEY
//   # a new key as its own 1Password item (the everyday key)
//   dart run tool/records.dart keygen --1password \
//       --title lychnion-everyday-key | op item create --vault Private -
//
// Every command reads keys/lychnion.json (Lychnion's grants and
// revocations) by itself, next to the --keys files.
//
// `catalog` carries every attestation in `attestations/` (or
// --attestations DIR; --no-attestations for a catalog without any), and
// stops when one is bad, a file there is not an attestation at its own
// path, or they would not fit: a withdrawal left out would leave a
// publisher shown as checked.
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
import 'package:cryptography/cryptography.dart' show Ed25519;
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
      final keyFrom = args.req('--key');
      if (args.positional().isNotEmpty) _strayArgument();
      final pair = await keyPairFromSeed(await _readKey(keyFrom));
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
    case 'attest':
      await _attest(args);
    case 'check-attestations':
      await _checkAttestations(args);
    case 'grant-request':
      await _grantRequest(args);
    case 'grant':
      await _grant(args);
    case 'revoke-key':
      await _revokeKey(args);
    case 'check-keys':
      await _checkKeys(args);
    case 'backup':
      await _backup(args);
    default:
      _fail('Unknown command ${argv.first}');
  }
}

/// A stray argument to a command that reads the key: not shown, it may
/// be the key itself.
Never _strayArgument() =>
    _fail('Unexpected argument (not shown: it may be the key)');

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

  /// The arguments left once every option was read; an option this
  /// command does not know (a typo) is refused instead of being taken
  /// for a file.
  List<String> positional() {
    for (final a in rest) {
      if (a.startsWith('--')) _fail('Unknown option ${a.split('=').first}');
    }
    return rest;
  }
}

/// The private seed from [from] (`-`: stdin, from `op read`; else a
/// file), checked for its form. Nothing about it reaches the output: a
/// wrong text could be the key with a stray character, and terminals,
/// shell logs and session transcripts keep what is printed.
Future<String> _readKey(String from) async {
  // The offline root (lychnion/lychnion#118): an encrypted key backup on
  // a USB stick, opened with its passphrase typed at the terminal.
  if (from != '-' && _isBackupFile(from)) return _openBackup(from);
  final String k;
  try {
    k = from == '-'
        ? (await stdin.transform(utf8.decoder).join()).trim()
        : (await File(from).readAsString()).trim();
  } on Object {
    _fail(
      from == '-'
          ? 'Could not read the key from stdin'
          // The "path" may be the seed itself, given by mistake.
          : 'Could not read the --key file (name not shown)',
    );
  }
  if (k.isEmpty) _fail('No key received (did `op read` fail?)');
  // An Ed25519 seed: 32 bytes, base64url (43 characters, maybe padded).
  var ok = RegExp(r'^[A-Za-z0-9_-]{43}=?$').hasMatch(k);
  if (ok) {
    try {
      ok = base64Url.decode(base64Url.normalize(k)).length == 32;
    } on Object {
      ok = false;
    }
  }
  if (!ok) _fail('Not a valid key (not shown). Check the 1Password item.');
  return k;
}

Future<void> _keygen(_Args args) async {
  // A typo (--1pasword) must not print a bare seed instead of the item.
  final onePassword = args.flag('--1password');
  final titleArg = args.opt('--title');
  if (titleArg != null && !onePassword) {
    _fail('--title names the 1Password item: only with --1password');
  }
  final title = titleArg ?? 'lychnion-package-signing-key';
  if (!RegExp(r'^[a-z0-9][a-z0-9-]{0,63}$').hasMatch(title)) {
    _fail('--title: lowercase letters, digits and hyphens');
  }
  if (args.positional() case [final first, ...]) _fail('Unexpected $first');
  final k = await newKeyPair();
  if (!onePassword) {
    stdout.writeln(k.seed);
    stderr.writeln('public key: ${k.publicKey}');
    return;
  }
  // API Credential item (1Password conventions: lowercase-hyphen title,
  // slash tags). Piped into `op item create -`, so the key is never shown.
  stdout.writeln(
    jsonEncode({
      'title': title,
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

/// Key statements from `--keys` files (keys.json) and [more] (a source
/// catalog's `keys`) that [trust] counts: made by a key it trusts.
/// Others are reported and left out.
Future<List<KeyStatement>> _keys(
  List<String> files,
  TrustedKeys trust, [
  List<Object?> more = const [],
  bool lychnion = true,
]) async {
  // Lychnion's own grants and revocations (keys/lychnion.json), unless
  // [lychnion] is false (verify --release: the catalog must carry them
  // itself): a release must never leave them out.
  final given = <Object?>[
    ...more,
    if (lychnion) ...await _lychnionStatements(),
  ];
  final perFile = <String, Set<String>>{};
  for (final f in files) {
    final String text;
    try {
      text = File(f).readAsStringSync();
    } on FileSystemException catch (e) {
      _fail('$f: ${e.osError?.message ?? e.message}');
    }
    final s = await KeysFile.parse(text);
    if (s.isEmpty) _fail('$f: no valid key statements');
    perFile[f] = {for (final x in s) x.id};
    given.addAll(s);
  }
  final kept = await relevantStatements(trust, given);
  // Given on purpose and none counts: the wrong file, or another
  // publisher's. Better to stop than to publish without the revocation.
  // Each file on its own: one wrong file (another publisher's) must not
  // pass because another file counts. By id: a statement also in the
  // catalog counts once, from there.
  final keptIds = {for (final k in kept) k.id};
  for (final e in perFile.entries) {
    if (!e.value.any(keptIds.contains)) {
      _fail(
        'None of the key statements in ${e.key} is made by a trusted key '
        '(another publisher\'s file, or --public-key wrong?)',
      );
    }
  }
  // Only worth saying for files given here: a third party's catalog
  // often carries statements about publishers not given.
  if (files.isNotEmpty && kept.length < given.length) {
    stderr.writeln(
      '${given.length - kept.length} key statements not made by a trusted '
      'key (or repeated), left out',
    );
  }
  return kept;
}

/// [c]'s text, or a clear error when it is over the 2 MB the app reads.
String _encode(CatalogDocument c, String out) {
  try {
    // Key statements left out to fit would leave records of a new key
    // with a bad signature, or a stolen key trusted: stop instead.
    final fitted = c.fitted();
    if (fitted.keys.length < c.keys.length) {
      _fail('$out: with its key statements it would be over 2 MB');
    }
    // Left out to fit, a withdrawal would leave a publisher shown as
    // checked by Lychnion.
    if (fitted.attestations.length < c.attestations.length) {
      _fail('$out: with its attestations it would be over 2 MB');
    }
    // Left out, a status would hide that a carrier held something back.
    if (fitted.status.length < c.status.length) {
      _fail('$out: with its status it would be over 2 MB');
    }
    return c.encode();
  } on CatalogTooLarge catch (e) {
    _fail('$out: $e (the app refuses larger catalogs)');
  }
}

CatalogDocument _readCatalog(String path) {
  try {
    return CatalogDocument.parse(File(path).readAsStringSync());
  } on FormatException catch (e) {
    _fail('$path: ${e.message}');
  }
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

final _areaCode = RegExp(r'^[A-Z]{2}(-[A-Z0-9]{1,3})?$');

/// The build tools check codes against the region file; this only checks
/// the form, so a hand-written area in the items file cannot be garbage.
List<String> _checkedArea(String id, List<String> area) {
  for (final c in area) {
    if (!_areaCode.hasMatch(c)) _fail('$id: area code $c is not like JP-30');
  }
  if (area.toSet().length != area.length) _fail('$id: area lists a code twice');
  return area;
}

/// `2026-11-18T09:00:00+09:00`
String _now() {
  final t = DateTime.now();
  final o = t.timeZoneOffset;
  String two(int n) => n.abs().toString().padLeft(2, '0');
  final date = t.toIso8601String().substring(0, 19);
  return '$date${o.isNegative ? '-' : '+'}${two(o.inHours)}:${two(o.inMinutes % 60)}';
}

/// Contours (`<map id>.contours`) have no sites file to take their area
/// from, so the items file sets it by hand: it must match the newest
/// signed record of their map, or the app ranks them for other places.
Future<void> _checkContoursArea(
  Directory dir,
  String id,
  Object? area,
  TrustedKeys trust,
) async {
  final mapId = id.substring(0, id.length - '.contours'.length);
  final map = await _newest(dir, mapId, trust);
  if (map == null) _fail('$id: sign its map $mapId first');
  final want = map.json['area'];
  if (jsonEncode(area) != jsonEncode(want)) {
    _fail(
      '$id: area ${jsonEncode(area)}, but $mapId ${map.version} has '
      '${jsonEncode(want)} (same codes in the same order)',
    );
  }
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
    if (r == null) {
      _fail(
        '${f.path}: bad record or signature (signed by a revoked key, or one '
        'without the scope? Remove it and sign the item again, at a version '
        'at least as high as the removed one)',
      );
    }
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
  final given = _trust(args);
  final keyFiles = args.all('--keys');
  final items = _readJson(args.req('--items'));
  final dir = Directory(args.req('--records'));
  // Contours after their maps: a map signed in the same call is the one
  // their area is checked against.
  final ids = [
    ...args.positional().where((i) => !i.endsWith('.contours')),
    ...args.positional().where((i) => i.endsWith('.contours')),
  ];
  if (ids.isEmpty) _fail('Name the items to sign.');
  final pair = await keyPairFromSeed(await _readKey(keyFrom));
  final publisher = items['publisher']! as String;
  final signer = TrustedKeys.canonicalKey(await publicKeyOf(pair));
  // Lychnion's items: every key Lychnion trusts (the root and the keys it
  // granted, keys/lychnion.json), so the newest version is found whoever
  // signed it, and the signer must hold the scope of each item.
  final TrustedKeys trust;
  if (given.known.contains(publisher)) {
    trust = given.withStatements(await _keys(keyFiles, given));
    if (!trust.keysOf(publisher).contains(signer)) {
      _fail(
        'The key given is not trusted for $publisher (wrong 1Password item, '
        'or its grant missing in $_lychnionKeys?)',
      );
    }
  } else {
    trust = TrustedKeys({
      publisher: [signer],
    });
  }
  final scopes = {
    for (final s in KeyScope.all)
      if (trust.keysFor(publisher, s).contains(signer)) s,
  };
  final byId = {
    for (final i in items['items']! as List) (i as Map)['id'] as String: i,
  };
  for (final id in ids) {
    final item = byId[id] ?? _fail('$id: not in the items file');
    // A package's official safety lines count only from a key holding
    // safety-lines (#125): signed without it, the lines would be dropped.
    final kind = item['kind'] as String?;
    for (final need in [
      KeyScope.forKind(kind ?? ''),
      if (kind == 'package') KeyScope.safetyLines,
    ]) {
      if (!scopes.contains(need)) {
        _fail('$id: the key given does not hold the scope "$need"');
      }
    }
    final unsigned = await _recordFor(publisher, item.cast());
    final version = unsigned['version']! as String;
    if (id.endsWith('.contours')) {
      await _checkContoursArea(dir, id, unsigned['area'], trust);
    }
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
  // Places the item is for ('JP', 'JP-30'); none means anywhere. From the
  // items file, else from the built package or map (tool/regions.py).
  var area = (item['area'] as List?)?.cast<String>();
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
    final builtArea =
        (item['kind'] == 'package' && name.endsWith('.sqlite')) ||
        (item['kind'] == 'map' && name.endsWith('.sites.sqlite'));
    if (area == null && builtArea) {
      final a = _sqliteMeta(path, 'area');
      if (a.isNotEmpty) area = (jsonDecode(a) as List).cast<String>();
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
    // Platforms the model runs on ('android'); none means every one.
    if (item['platforms'] case final List p when p.isNotEmpty)
      'platforms': p.cast<String>(),
    if (area != null && area.isNotEmpty) 'area': _checkedArea(id, area),
    'files': files,
    'signed': _now(),
  };
}

Future<void> _catalog(_Args args) async {
  // Before the options are read: another publisher's catalog (not with
  // Lychnion's own key, however it is spelled: that would only skip the
  // checks below).
  final i = args.rest.indexOf('--public-key');
  String? canon(String k) {
    try {
      return TrustedKeys.canonicalKey(k);
    } on FormatException {
      return null;
    }
  }

  final given0 = i >= 0 && i + 1 < args.rest.length
      ? canon(args.rest[i + 1])
      : null;
  final othersCatalog =
      i >= 0 &&
      !(given0 != null &&
          builtInPublishers.keysOf(attesterPublisher).contains(given0));
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
  final given = _trust(args);
  final keyFiles = args.all('--keys');
  final givenDir = args.opt('--attestations');
  final noAttestations = args.flag('--no-attestations');
  final keyFrom = args.opt('--key');
  final noStatus = args.flag('--no-status');
  if (keyFrom != null && noStatus) _fail('--key and --no-status: give one');
  // Lychnion's catalog always carries its status: without one, phones
  // cannot tell that a carrier held a newer version back.
  if (keyFrom == null && !noStatus && !othersCatalog) {
    _fail(
      "Give --key - (Lychnion's key from 1Password) to sign the status, or "
      '--no-status',
    );
  }
  if (noAttestations && givenDir != null) {
    _fail('--attestations and --no-attestations: give one');
  }
  // Lychnion's catalog: the attestations next to this tool, wherever it
  // runs from. Another publisher's (--public-key): none unless given,
  // since Lychnion's would not verify with its key.
  final attestationDir =
      givenDir ?? (othersCatalog ? null : _defaultAttestations);
  // A mistyped option (--key, --keys=FILE) must not leave a revocation
  // out of the catalog unnoticed.
  if (args.positional().isNotEmpty) _strayArgument();
  final keys = await _keys(keyFiles, given);
  // Records of a successor key count when a statement names it.
  final trust = given.withStatements(keys);
  // Every attestation (#119); a bad one stops the catalog: better than a
  // withdrawal silently missing.
  final attestations = noAttestations || attestationDir == null
      ? const <Attestation>[]
      : await _attestationsIn(attestationDir, trust);
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
      // With a status, it would be signed over the older version: sign the
      // new record first.
      if (keyFrom != null && isOlder(r.version, '${i['version']}')) {
        _fail(
          '$id: newest record is ${r.version}, items file says '
          '${i['version']}: sign it first',
        );
      }
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
  final status = keyFrom == null
      ? null
      : await _signStatus(
          keyFrom,
          records: records,
          keys: keys,
          attestations: attestations,
          trust: trust,
        );
  final catalog = CatalogDocument(
    base: base,
    refresh: refresh,
    records: records,
    // One address per hash, as a string: older apps read only that.
    urls: urls,
    mirrors: [for (final m in mirrors) m.endsWith('/') ? m : '$m/'],
    keys: [for (final s in keys) s.json],
    attestations: [for (final a in attestations) a.json],
    status: [?status?.json],
  );
  await File(out).writeAsString(_encode(catalog, out));
  stdout.writeln(
    '$out: ${records.length} records, ${attestations.length} attestations'
    '${attestationDir == null || noAttestations ? ' (none given)' : ' from ${Directory(attestationDir).absolute.path}'}'
    '; ${status == null ? 'NO STATUS: not for release' : 'status signed ${status.signed}'}',
  );
}

/// Writes a static node: the catalog (records verified and unchanged) and
/// every file found in the source folders as `blobs/sha256/<hash>`, which
/// the catalog then points to. Files are copied only when their size and
/// hash match the signed record; files not found keep their address from
/// the source catalog (the models on Hugging Face, for example).
Future<void> _export(_Args args) async {
  final out = Directory(args.req('--out'));
  final sourceUrl = args.opt('--source-url');
  final keyFiles = args.all('--keys');
  final given = _trust(args);
  final rest = args.positional();
  if (rest.isEmpty) _fail('Usage: export CATALOG --out DIR [FOLDER...]');
  final j = _readCatalog(rest.first);
  final keys = await _withStatusKeys(
    await _keys(keyFiles, given, j.keys),
    given,
    j.status,
  );
  final trust = given.withStatements(keys);
  final folders = rest.skip(1).toList();
  // Where the source catalog gets each file, for files not exported.
  final base = j.base != null
      ? Uri.parse(j.base!)
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
    for (final m in j.mirrors)
      if (Uri.tryParse(m) case final u?)
        if (u.hasScheme)
          if (web(u)) '$u' else ...[]
        else if (origin != null)
          if (web(origin.resolveUri(u))) '${origin.resolveUri(u)}',
  ];
  final hasMirrors = mirrors.isNotEmpty;
  String? elsewhere(RecordFile f) {
    final u = Uri.tryParse(j.urls[f.sha256] ?? f.name);
    if (u == null) return null;
    if (u.hasScheme) return '$u';
    return base == null ? null : '${base.resolveUri(u)}';
  }

  final blobs = Directory('${out.path}/blobs/sha256');
  await blobs.create(recursive: true);
  final records = <Map<String, Object?>>[];
  final urls = <String, String>{};
  var missing = 0;
  for (final raw in j.records) {
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
  // The source catalog's attestations that a key trusted here for
  // Lychnion signed, as the app passes them on.
  final inStatus = [
    for (final st in await PublisherStatus.checkAll(j.status, trust))
      ...st.attestations,
  ];
  final own = await Attestation.checkAll(j.attestations, trust);
  final attestations = await Attestation.checkAll([
    ...j.attestations,
    ...inStatus,
  ], trust);
  if (own.length < j.attestations.length) {
    stderr.writeln(
      '${j.attestations.length - own.length} attestations left '
      'out: not signed by a trusted key, repeated, or past the limits '
      '(${Attestation.maxPerSource}, or ${Attestation.maxBadSignatures} '
      'bad signatures)',
    );
  }
  // Statuses (#117): the newest of each publisher, none signed far ahead
  // of this computer's clock (a stolen key's could push the real one out).
  final status = PublisherStatus.newest(
    PublisherStatus.deciding(
      await PublisherStatus.checkAll(j.status, trust),
      DateTime.now(),
    ),
  );
  final catalog = CatalogDocument(
    refresh: j.refresh,
    records: records,
    urls: urls,
    mirrors: mirrors,
    keys: [for (final s in keys) s.json],
    attestations: [for (final a in attestations) a.json],
    status: [for (final st in status) st.json],
  );
  await File(
    '${out.path}/catalog.json',
  ).writeAsString(_encode(catalog, '${out.path}/catalog.json'));
  stdout.writeln(
    '${out.path}: ${records.length} records'
    '${missing > 0 ? ', $missing files not found (fetched elsewhere)' : ''}',
  );
}

Future<void> _verify(_Args args) async {
  final keyFiles = args.all('--keys');
  final release = args.flag('--release');
  final given = _trust(args);
  final rest = args.positional();
  if (rest.isEmpty) _fail('Usage: verify CATALOG [FOLDER] [--keys FILE]');
  final j = _readCatalog(rest.first);
  final folder = rest.length > 1 ? rest[1] : null;
  final keys = await _withStatusKeys(
    // --release: only what the catalog itself carries, so a catalog
    // without a grant or revocation of Lychnion's fails here instead of
    // passing on this computer's keys/lychnion.json.
    await _keys(keyFiles, given, j.keys, !release),
    given,
    j.status,
  );
  if (keys.isNotEmpty) stdout.writeln('${keys.length} key statements');
  final trust = given.withStatements(keys);
  var bad = 0;
  final status = await PublisherStatus.checkAll(j.status, trust);
  for (final st in status) {
    stdout.writeln(
      'ok   status ${st.publisher} ${st.signed}: ${st.items.length} items'
      '${st.keys.isEmpty ? '' : ', ${st.keys.length} key statements'}'
      '${st.attestations.isEmpty ? '' : ', ${st.attestations.length} withdrawals'}',
    );
  }
  if (status.length < j.status.length) {
    bad += j.status.length - status.length;
    stdout.writeln(
      'BAD  ${j.status.length - status.length} statuses: signature, form '
      'or repeated',
    );
  }
  // As devices see it: none signed more than a day ahead of the clock.
  final newestStatus = {
    for (final st in PublisherStatus.newest(
      PublisherStatus.deciding(status, DateTime.now()),
    ))
      st.publisher: st,
  };
  final hasLychnion = j.records.any(
    (r) => r is Map && r['publisher'] == attesterPublisher,
  );
  // Lychnion's own catalog (--release) always needs it, also when every
  // record was left out.
  if ((hasLychnion || release) && newestStatus[attesterPublisher] == null) {
    final ahead = status.any((st) => st.publisher == attesterPublisher);
    final why = ahead
        ? "Lychnion's status is signed too far ahead of this clock"
        : "no status of Lychnion's";
    if (release) {
      bad++;
      stdout.writeln('BAD  $why: not for release');
    } else {
      stdout.writeln('     $why');
    }
  }
  if (j.attestations.isNotEmpty) {
    final ok = await Attestation.checkAll(j.attestations, trust);
    stdout.writeln('${ok.length} attestations');
    for (final a in ok) {
      stdout.writeln(
        'ok   attestation ${a.publisher}'
        '${a.withdrawn ? ' (withdrawn)' : ''} ${a.signed}',
      );
    }
    if (ok.length < j.attestations.length) {
      bad += j.attestations.length - ok.length;
      stdout.writeln(
        'BAD  ${j.attestations.length - ok.length} attestations: signature, '
        'form or repeated',
      );
    }
  }
  for (final raw in j.records) {
    final r = await trust.check(raw);
    if (r == null) {
      bad++;
      stdout.writeln('BAD  ${(raw as Map)['id']}: signature or record');
      continue;
    }
    stdout.writeln('ok   ${r.id} ${r.version} (${r.publisher}, ${r.keyId})');
    // A record newer than its publisher's status in the same catalog: the
    // status was not signed again (phones would think this is old news).
    // In Lychnion's own catalog before a release (--release) that is an
    // error; elsewhere (a stick, a node) it can be right, and is noted.
    // A status naming a newer version is fine: it was held back here.
    if (newestStatus[r.publisher] case final st?) {
      final named = st.items[r.id];
      if (named == null || isOlder(named, r.version)) {
        if (release) bad++;
        stdout.writeln(
          '${release ? 'BAD ' : '    '} ${r.id} ${r.version}: the status '
          'names ${named ?? 'no version'} (catalog not signed again?)',
        );
      } else if (st.hasNewer(r.id, r.version)) {
        stdout.writeln('     ${r.id}: version $named exists elsewhere');
      }
    }
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
  final given = _trust(args);
  final keyFiles = args.all('--keys');
  final rest = args.positional();
  // Records of a key that replaced Lychnion's count when its statement
  // is given (keys.json), before the app ships the new key.
  final trust = given.withStatements(await _keys(keyFiles, given));
  if (rest.isEmpty) _fail('Usage: check-records FOLDER');
  final files =
      Directory(rest.first)
          .listSync(recursive: true)
          .whereType<File>()
          .where((f) => f.path.endsWith('.json'))
          .toList()
        ..sort((a, b) => a.path.compareTo(b.path));
  if (files.isEmpty) _fail('No records in ${rest.first}');
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

/// Attestation files: `attestations/<publisher>/<signed>.json`, where
/// `<signed>` is the signed time without colons (`20261123T140000+0900`).
String _attestationPath(String dir, Attestation a) =>
    '${dir.replaceFirst(RegExp(r'/+$'), '')}/${a.publisher}/${a.signed.replaceAll(RegExp('[-:]'), '')}.json';

/// Every attestation in [dir], checked with [trust]. The folder must
/// exist, and hold only `<publisher>/<signed time>.json` files (plain
/// files, not links), each signed by a key trusted for Lychnion and at its
/// own path, and a `README.md`: anything else (a renamed `.bak`, `.JSON`)
/// stops the tool, so a withdrawal can never be left out by a name.
Future<List<Attestation>> _attestationsIn(String dir, TrustedKeys trust) async {
  dir = dir.replaceFirst(RegExp(r'/+$'), '');
  final d = Directory(dir);
  if (!d.existsSync()) {
    _fail(
      '$dir: no such folder (${d.absolute.path}); --no-attestations '
      'for a catalog without any',
    );
  }
  final entries = d.listSync(recursive: true, followLinks: false)
    ..sort((a, b) => a.path.compareTo(b.path));
  final out = <Attestation>[];
  var bad = 0;
  void wrong(String path, String why) {
    bad++;
    stderr.writeln('BAD  $path: $why');
  }

  for (final e in entries) {
    final rel = e.path.substring(dir.length + 1).split('/');
    if (e is Link) {
      wrong(e.path, 'a link (only plain files)');
      continue;
    }
    if (e is Directory) {
      if (rel.length != 1 || !_publisherName.hasMatch(rel.first)) {
        wrong(e.path, 'not a publisher folder');
      }
      continue;
    }
    if (rel.length == 1 && rel.first == 'README.md') continue;
    // Finder's folder settings on macOS: never an attestation (only
    // parsed .json files are), so skipped rather than stopping the tool.
    // Anything else (.bak, ._x, x~) may hide a renamed withdrawal.
    if (rel.length <= 2 && rel.last == '.DS_Store') {
      stderr.writeln('note: skipped ${e.path}');
      continue;
    }
    if (rel.length != 2 || !rel.last.endsWith('.json')) {
      wrong(e.path, 'not <publisher>/<signed time>.json');
      continue;
    }
    final Object? json;
    try {
      json = jsonDecode((e as File).readAsStringSync());
    } on FormatException {
      wrong(e.path, 'not JSON');
      continue;
    } on FileSystemException {
      wrong(e.path, 'not readable');
      continue;
    }
    final a = await Attestation.check(json, trust);
    if (a == null) {
      wrong(e.path, 'signature, signer or form');
      continue;
    }
    if (_attestationPath(dir, a) != e.path) {
      wrong(e.path, 'should be ${_attestationPath(dir, a)}');
      continue;
    }
    out.add(a);
  }
  if (bad > 0) _fail('$bad bad entries in $dir');
  if (Attestation.capped(out).length < out.length) {
    _fail(
      '$dir: more than ${Attestation.maxPerSource} attestations; a catalog '
      'carries at most that many',
    );
  }
  return out;
}

/// `attestations/` in the repository this tool is in.
String get _defaultAttestations =>
    '${File.fromUri(Platform.script).parent.parent.path}/attestations';

final _publisherName = RegExp(r'^[a-z0-9][a-z0-9._-]{0,63}$');

/// The ways a key may be checked (docs/attestations.md in the app
/// repository writes a new one down before it is used).
const _methods = {'official-website'};

/// Signs an attestation and writes it to the attestations folder.
Future<void> _attest(_Args args) async {
  final keyFrom = args.req('--key');
  final dir = args.opt('--attestations') ?? _defaultAttestations;
  final from = args.opt('--from');
  final withdraw = args.flag('--withdraw');
  var publisher = args.opt('--publisher');
  final checked = args.opt('--checked') ?? _now().substring(0, 10);
  final method = args.opt('--method') ?? 'official-website';
  final given = _trust(args);
  final keyFiles = args.all('--keys');
  if (args.positional().isNotEmpty) _strayArgument();
  if (withdraw && from != null) {
    _fail(
      '--withdraw takes --publisher, not --from: it names the newest '
      'attestation\'s key',
    );
  }
  String? key, url;
  var title = <String, String>{};
  if (from != null) {
    // The values from the issue, never through the shell.
    final Object? j;
    try {
      j = jsonDecode(File(from).readAsStringSync());
    } on Object {
      _fail('$from: not a readable JSON file');
    }
    if (j is! Map ||
        j.keys.any((k) => !{'publisher', 'key', 'title', 'url'}.contains(k))) {
      _fail('$from: only publisher, key, title and url');
    }
    final Map<Object?, Object?> m = j;
    String? text(String field) {
      final v = m[field];
      if (v == null) return null;
      if (v is! String) _fail('$from: "$field" must be text');
      return v.trim();
    }

    final named = text('publisher');
    if (named == null) _fail('$from: "publisher" is missing');
    if (publisher != null && publisher != named) {
      _fail('--publisher and $from name different publishers');
    }
    publisher = named;
    key = text('key');
    url = text('url');
    if (j['title'] case final Map t) {
      for (final e in t.entries) {
        if (e.key is! String || e.value is! String) {
          _fail('$from: "title" must map languages to text');
        }
        title[e.key as String] = (e.value as String).trim();
      }
    } else if (j['title'] != null) {
      _fail('$from: "title" must map languages to text');
    }
  }
  if (publisher == null) _fail('Give --from FILE (or --publisher to withdraw)');
  if (!_methods.contains(method)) {
    _fail('--method $method is not written down in docs/attestations.md');
  }
  final today = _now().substring(0, 10);
  if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(checked) ||
      DateTime.tryParse(checked) == null) {
    _fail('--checked $checked: give the day as YYYY-MM-DD');
  }
  if (checked.compareTo(today) > 0) {
    _fail('--checked $checked is in the future');
  }
  final trust = given.withStatements(await _keys(keyFiles, given));
  final existing =
      (await _attestationsIn(
          dir,
          trust,
        )).where((a) => a.publisher == publisher).toList()
        ..sort((a, b) => a.signedAt.compareTo(b.signedAt));
  final newest = existing.isEmpty
      ? null
      : attestationFor(
          publisher,
          existing.last.key,
          existing,
          const [],
        ).attestation;
  if (withdraw) {
    if (newest == null) _fail('$publisher: no attestation to take back');
    if (newest.withdrawn) _fail('$publisher: already withdrawn');
    // The newest attestation's key, so the withdrawal reads clearly.
    key ??= newest.key;
    title = {};
    url = null;
  } else {
    if (from == null) _fail('Give --from FILE: the values from the issue');
    if (title['ja'] == null || title['en'] == null) {
      _fail('$from: title needs ja and en');
    }
    if (url == null) _fail('$from: url, the page where the key was checked');
    if (newest != null && !newest.withdrawn && newest.key != key) {
      stderr.writeln(
        'Note: $publisher had an attestation for another key '
        '(${newest.key}); this one replaces it.',
      );
    }
  }
  if (key == null) _fail('No publisher key (in $from: "key")');
  final signer = await keyPairFromSeed(await _readKey(keyFrom));
  final Attestation a;
  try {
    a = await Attestation.sign(
      publisher: publisher,
      key: key,
      title: title,
      checked: checked,
      method: method,
      url: url,
      signed: _now(),
      signer: signer,
      withdrawn: withdraw,
    );
  } on ArgumentError catch (e) {
    _fail('Not an attestation the app would accept: ${e.message}');
  } on FormatException catch (e) {
    _fail('The publisher key: ${e.message}');
  }
  // Only a key the app trusts for Lychnion makes attestations that count.
  if (await Attestation.check(a.json, trust) == null) {
    _fail(
      'The key given is not trusted for Lychnion, or lacks the scope '
      '(wrong 1Password item, a key granted without it, or --public-key / '
      '--keys missing after a key replacement)',
    );
  }
  // A file that would not decide (a clock behind the newest file) is
  // never written: a withdrawal must win.
  if (existing.any((e) => !a.signedAt.isAfter(e.signedAt))) {
    _fail(
      'This would be signed at ${a.signed}, not after the newest file for '
      '$publisher: check the clock',
    );
  }
  final path = _attestationPath(dir, a);
  if (File(path).existsSync()) _fail('$path exists already');
  await File(path).parent.create();
  await File(
    path,
  ).writeAsString('${const JsonEncoder.withIndent('  ').convert(a.json)}\n');
  stdout.writeln(
    '$path${withdraw ? ' (withdrawal of the attestation for ${a.key})' : ''}',
  );
}

/// Every attestation in FOLDER must be valid and at its own path (CI).
Future<void> _checkAttestations(_Args args) async {
  final given = _trust(args);
  final keyFiles = args.all('--keys');
  final rest = args.positional();
  if (rest.isEmpty) _fail('Usage: check-attestations FOLDER');
  final trust = given.withStatements(await _keys(keyFiles, given));
  final all = await _attestationsIn(rest.first, trust);
  // A clock far ahead writes a file that decides over every later one.
  final soon = DateTime.now().add(const Duration(days: 1));
  final ahead = [
    for (final a in all)
      if (a.signedAt.isAfter(soon)) a,
  ];
  for (final a in ahead) {
    stderr.writeln('BAD  ${a.publisher} ${a.signed}: signed in the future');
  }
  if (ahead.isNotEmpty) _fail('${ahead.length} attestations from the future');
  for (final a in all) {
    final r = attestationFor(a.publisher, a.key, all, const []);
    final decides = r.attestation?.id == a.id;
    stdout.writeln(
      'ok   ${a.publisher} ${a.signed}'
      '${a.withdrawn ? ' withdrawn' : ''}'
      '${decides ? ' (decides)' : ' (replaced by a newer one)'}',
    );
  }
  stdout.writeln('${all.length} attestations');
}

/// [keys] and the key statements inside the statuses in [rawStatus] that a
/// trusted key made (#117): whoever passed on a status passed those on.
Future<List<KeyStatement>> _withStatusKeys(
  List<KeyStatement> keys,
  TrustedKeys given,
  List<Object?> rawStatus,
) async {
  final trust = given.withStatements(keys);
  final status = await PublisherStatus.checkAll(rawStatus, trust);
  if (status.isEmpty) return keys;
  final seen = {for (final k in keys) k.id};
  return [
    ...keys,
    for (final k in await relevantStatements(trust, [
      for (final st in status) ...st.keys,
    ]))
      if (seen.add(k.id)) k,
  ];
}

/// Lychnion's status for a catalog of [records] (#117): the newest version
/// of each of its items, its own key statements in [keys], and the
/// attestations that are withdrawn now (the deciding one for each
/// publisher), signed with the key from [keyFrom]. Refused when that key is
/// not one [trust] has for Lychnion.
Future<PublisherStatus> _signStatus(
  String keyFrom, {
  required List<Map<String, Object?>> records,
  required List<KeyStatement> keys,
  required List<Attestation> attestations,
  required TrustedKeys trust,
}) async {
  final items = <String, String>{
    for (final r in records)
      if (r['publisher'] == attesterPublisher)
        r['id']! as String: r['version']! as String,
  };
  // The deciding attestation of each name (attestationFor picks the newest
  // for the name first, whichever key is asked about, and returns a
  // withdrawal before looking at the key): those that are withdrawals.
  final withdrawn = [
    for (final name in {for (final a in attestations) a.publisher})
      if (attestationFor(
            name,
            attestations.firstWhere((a) => a.publisher == name).key,
            attestations,
            const [],
          ).attestation
          case final a? when a.withdrawn)
        a,
  ];
  final signer = await keyPairFromSeed(await _readKey(keyFrom));
  final PublisherStatus status;
  try {
    status = await PublisherStatus.sign(
      publisher: attesterPublisher,
      signed: _now(),
      items: items,
      signer: signer,
      keys: [
        for (final k in keys)
          if (k.publisher == attesterPublisher) k,
      ],
      attestations: withdrawn,
    );
  } on ArgumentError {
    _fail('Not a status the app would accept (too many items or entries?)');
  }
  if (await PublisherStatus.check(status.json, trust) == null) {
    _fail(
      'The key given is not trusted for Lychnion, or lacks the scope '
      '(wrong 1Password item, a key granted without it, or --public-key / '
      '--keys missing after a key replacement)',
    );
  }
  return status;
}

/// Lychnion's own key statements: grants by its root, revocations of
/// granted keys, and replacements of the root (#125), committed so every
/// catalog carries them. Found next to this tool, wherever it runs from.
String get _lychnionKeys =>
    '${File.fromUri(Platform.script).parent.parent.path}/keys/lychnion.json';

/// The statements in [path] ([_lychnionKeys] when not given); none when
/// it does not exist, unless [required]. Stops when an entry is not a
/// valid statement, and, for [_lychnionKeys], when one is not made by a
/// key the app trusts: a grant or revocation left out silently is worse
/// than no release.
Future<List<KeyStatement>> _lychnionStatements({
  String? path,
  bool required = false,
}) async {
  final p = path ?? _lychnionKeys;
  final f = File(p);
  if (!f.existsSync()) {
    if (required) _fail('$p: no such keys file');
    return const [];
  }
  final text = f.readAsStringSync();
  Object? raw;
  try {
    raw = (jsonDecode(text) as Map)['keys'];
  } on Object {
    _fail('$p: not a keys file ({"keys": [...]})');
  }
  final parsed = await KeysFile.parse(text);
  if (raw is! List || parsed.length != raw.length) {
    _fail('$p: an entry is not a valid key statement');
  }
  if (path == null) {
    final foreign = await _foreignStatements(builtInPublishers, parsed);
    if (foreign.isNotEmpty) {
      _fail(
        '$p: ${foreign.length} statement(s) by a key the publisher\'s root '
        'or its grants and replacements do not lead to: run check-keys',
      );
    }
  }
  return parsed;
}

/// The statements of [all] made by a key that neither [given] nor the
/// statements of [all] lead to: not the publisher's root, nor a key a
/// grant or replacement in [all] names (from a known key). Such a
/// statement is another publisher's or a test key's, in the wrong file.
/// What counts of the rest is decided by the app's own rules
/// (relevantStatements): a former root's grants lapse, a revoked key's
/// statements stop counting; they stay in the file, for the record.
Future<List<KeyStatement>> _foreignStatements(
  TrustedKeys given,
  List<KeyStatement> all,
) async {
  final known = <String, Set<String>>{
    for (final p in given.known) p: given.keysOf(p).toSet(),
  };
  for (var grew = true; grew;) {
    grew = false;
    for (final s in all) {
      if (s.newKey == null) continue;
      final k = known[s.publisher];
      if (k != null &&
          k.contains(TrustedKeys.canonicalKey(s.maker)) &&
          k.add(TrustedKeys.canonicalKey(s.newKey!))) {
        grew = true;
      }
    }
  }
  return [
    for (final s in all)
      if (!(known[s.publisher]?.contains(TrustedKeys.canonicalKey(s.maker)) ??
          false))
        s,
  ];
}

String _b64(List<int> b) => base64Url.encode(b).replaceAll('=', '');

/// The keys `grant` and `revoke-key` check against. `--public-key` (tests)
/// only with `--keys-file`: a test root's statement must never land in
/// Lychnion's keys/lychnion.json.
TrustedKeys _trustForKeyFile(_Args args, String? file) {
  final testKey = args.rest.contains('--public-key');
  String norm(String f) {
    final a = File(f).absolute;
    try {
      return a.resolveSymbolicLinksSync();
    } on FileSystemException {
      return a.uri.normalizePath().toFilePath();
    }
  }

  if (testKey && (file == null || norm(file) == norm(_lychnionKeys))) {
    _fail('--public-key needs --keys-file (never Lychnion\'s own file)');
  }
  return _trust(args);
}

/// [given] with Lychnion's statements and those of the `--keys` [files].
///
/// `grant` and `revoke-key` take no `--keys`: what they write must count
/// with keys/lychnion.json alone.
Future<TrustedKeys> _withKeys(TrustedKeys given, List<String> files) async =>
    given.withStatements(await _keys(files, given));

/// Step 1 of a grant, with the key to be granted (`--key`): signs the
/// grant as that key (its countersignature) and writes the request for
/// the root to sign in step 2 (`grant`). Neither key has to be on the
/// other one's computer.
Future<void> _grantRequest(_Args args) async {
  final keyFrom = args.req('--key');
  final publisher = args.opt('--publisher') ?? attesterPublisher;
  final scopeArg = args.req('--scope');
  final out = args.opt('--out') ?? 'build/grant-request.json';
  final rootArg = args.opt('--root');
  final given = _trust(args);
  if (args.positional().isNotEmpty) _strayArgument();
  final scope = {for (final x in scopeArg.split(',')) x.trim()}..remove('');
  for (final x in scope) {
    if (!KeyScope.grantable.contains(x)) {
      _fail(
        '--scope $x: not a scope a grant may list '
        '(${KeyScope.grantable.join(', ')})',
      );
    }
  }
  if (scope.isEmpty) _fail('--scope: name at least one scope');
  // The root that will sign: the current one (the built-in key or its
  // newest successor), unless --root names it.
  final trust = await _withKeys(given, const []);
  final roots = trust.currentRootsOf(publisher);
  String? root;
  if (rootArg != null) {
    try {
      root = TrustedKeys.canonicalKey(rootArg);
    } on Object {
      _fail('--root: not a public key');
    }
  } else if (roots.length == 1) {
    root = roots.single;
  }
  if (root == null) {
    _fail('$publisher has no single current root: give --root KEY');
  }
  final pair = await keyPairFromSeed(await _readKey(keyFrom));
  final newKey = await publicKeyOf(pair);
  if (TrustedKeys.canonicalKey(newKey) == root) {
    _fail('The key given is the root itself: give the key to be granted');
  }
  final sorted = scope.toList()..sort();
  final j = <String, Object?>{
    'format': KeyStatement.format,
    'type': KeyStatementType.grant.name,
    'publisher': publisher,
    'old': root,
    'new': newKey,
    'scope': sorted,
    'signed': _now(),
  };
  final sig = await Ed25519().sign(
    utf8.encode(canonicalJson(j)),
    keyPair: pair,
  );
  j['new_signature'] = _b64(sig.bytes);
  final f = File(out);
  await f.parent.create(recursive: true);
  await f.writeAsString('${const JsonEncoder.withIndent('  ').convert(j)}\n');
  stdout.writeln(
    'Grant request → $out: $publisher key $newKey, scope '
    '${sorted.join(', ')}, for the root $root to sign.\n'
    'Note the key: on the root\'s computer, grant takes it as '
    '--expect-key $newKey, so a request swapped on the way is refused.',
  );
}

/// Step 2 of a grant, with the root (`--key`): checks the request from
/// `grant-request` against the key it should grant (`--expect-key`, noted
/// in step 1), signs it, and adds the grant to [_lychnionKeys].
Future<void> _grant(_Args args) async {
  final keyFrom = args.req('--key');
  final from = args.req('--from');
  final expect = args.req('--expect-key');
  final file = args.opt('--keys-file');
  final given = _trustForKeyFile(args, file);
  if (args.positional().isNotEmpty) _strayArgument();
  Map<String, Object?> j;
  try {
    j = (jsonDecode(File(from).readAsStringSync()) as Map)
        .cast<String, Object?>();
  } on Object {
    _fail('$from: not a grant request');
  }
  const fields = {
    'format',
    'type',
    'publisher',
    'old',
    'new',
    'scope',
    'signed',
    'new_signature',
  };
  if (j.keys.toSet().difference(fields).isNotEmpty ||
      !fields.every(j.containsKey) ||
      j['type'] != KeyStatementType.grant.name ||
      j['old'] is! String ||
      j['new'] is! String ||
      j['scope'] is! List ||
      (j['scope']! as List).any((x) => !KeyScope.grantable.contains(x))) {
    _fail('$from: not a grant request (or it lists a scope a grant may not)');
  }
  String expected;
  try {
    expected = TrustedKeys.canonicalKey(expect);
  } on Object {
    _fail('--expect-key: not a public key');
  }
  if (TrustedKeys.canonicalKey(j['new']! as String) != expected) {
    _fail(
      '$from grants ${j['new']}, not --expect-key: not the request made in '
      'step 1 (swapped on the way?). Nothing signed.',
    );
  }
  final pair = await keyPairFromSeed(await _readKey(keyFrom));
  final root = await publicKeyOf(pair);
  if (TrustedKeys.canonicalKey(root) !=
      TrustedKeys.canonicalKey(j['old']! as String)) {
    _fail('$from asks for the root ${j['old']}, not the key given');
  }
  final text = canonicalJson(Map.of(j)..remove('new_signature'));
  final sig = await Ed25519().sign(utf8.encode(text), keyPair: pair);
  j['signature'] = _b64(sig.bytes);
  final s = await KeyStatement.check(j);
  if (s == null) {
    _fail('$from: the request\'s countersignature is not valid');
  }
  final written = await _addStatement(
    given,
    file,
    s,
    'The grant would not count: the key given is not the current root of '
    '${s.publisher}',
  );
  stdout.writeln(
    'Granted ${s.publisher} key ${s.newKey}: ${s.scope.join(', ')} → '
    '$written. Commit it; every catalog carries it from now on.',
  );
}

/// The root (`--key`) revokes a key it granted (`--revoked KEY`) and adds
/// the revocation to [_lychnionKeys].
Future<void> _revokeKey(_Args args) async {
  final keyFrom = args.req('--key');
  final revokedArg = args.req('--revoked');
  final publisher = args.opt('--publisher') ?? attesterPublisher;
  final file = args.opt('--keys-file');
  final given = _trustForKeyFile(args, file);
  if (args.positional().isNotEmpty) _strayArgument();
  String revoked;
  try {
    revoked = TrustedKeys.canonicalKey(revokedArg);
  } on Object {
    _fail('--revoked: not a public key (not shown: it may be a key)');
  }
  final pair = await keyPairFromSeed(await _readKey(keyFrom));
  if (TrustedKeys.canonicalKey(await publicKeyOf(pair)) == revoked) {
    _fail(
      '--revoked is the root itself: a root is revoked with its own key '
      '(a revocation by the key itself), not with revoke-key',
    );
  }
  final s = await KeyStatement.revoke(
    publisher: publisher,
    revoked: revoked,
    by: pair,
    signed: _now(),
  );
  final written = await _addStatement(
    given,
    file,
    s,
    'The revocation would not count: the key given is not the current root '
    'of $publisher, or the key named is not one it granted',
  );
  stdout.writeln(
    'Revoked $publisher key $revoked → $written. Commit it and publish a '
    'catalog: phones learn of it from the status.',
  );
}

/// Adds [s] to the keys file [file] ([_lychnionKeys] when not given) when
/// it counts after the statements already there (made by a current root
/// of [given]); stops with [whyNot] otherwise. The file holds all of the
/// publisher's key statements, its root's replacements too, so whatever
/// counts here counts for every later command. Returns the file written.
Future<String> _addStatement(
  TrustedKeys given,
  String? file,
  KeyStatement s,
  String whyNot,
) async {
  final existing = await _lychnionStatements(path: file);
  final trust = given.withStatements(
    await relevantStatements(given, existing, max: existing.length + 1),
  );
  if ((await relevantStatements(trust, [s])).every((k) => k.id != s.id)) {
    _fail(whyNot);
  }
  final out = file ?? _lychnionKeys;
  await File(out).parent.create(recursive: true);
  await File(out).writeAsString(KeysFile.encode([...existing, s]));
  return out;
}

/// Every entry of [_lychnionKeys] (or the file given, which must exist) is
/// a valid statement made by a key the app trusts (also run by CI).
Future<void> _checkKeys(_Args args) async {
  final given = _trust(args);
  final pos = args.positional();
  if (pos.length > 1) _fail('Give at most one keys file');
  final file = pos.isEmpty ? _lychnionKeys : pos.single;
  final all = await _lychnionStatements(path: file, required: pos.isNotEmpty);
  final bad = await _foreignStatements(given, all);
  for (final s in bad) {
    stderr.writeln(
      '$file: ${s.type.name} of ${s.newKey ?? s.oldKey} is not made by a '
      'key the publisher\'s root or its grants and replacements lead to',
    );
  }
  if (bad.isNotEmpty) exit(1);
  final counted = await relevantStatements(given, all, max: all.length + 1);
  final idle = all.length - counted.length;
  stdout.writeln(
    '${all.length} key statements'
    '${idle > 0 ? ' ($idle no longer count: a former root\'s, or a revoked '
              'key\'s)' : ''}',
  );
}

/// Whether the --key file at [path] is a key backup: by its name, or by
/// its content (a seed file is a single line, a backup is JSON).
bool _isBackupFile(String path) {
  if (path.toLowerCase().endsWith(KeyBackup.extension)) return true;
  // Only a regular file, and only its start: a pipe (`--key <(op read
  // …)`, /dev/stdin) can be read once, and that read is the key's.
  if (FileSystemEntity.typeSync(path) != FileSystemEntityType.file) {
    return false;
  }
  try {
    final raf = File(path).openSync();
    final head = raf.readSync(64 * 1024);
    raf.closeSync();
    return utf8
        .decode(head, allowMalformed: true)
        .contains('"type": "${KeyBackup.type}"');
  } on FileSystemException {
    return false;
  }
}

/// The seed in the key backup at [path], opened with the passphrase typed
/// at the terminal. The file is checked before the passphrase is asked.
Future<String> _openBackup(String path) async {
  final String text;
  try {
    text = File(path).readAsStringSync();
  } on FileSystemException {
    _fail('Could not read the key backup (name not shown)');
  }
  try {
    final j = jsonDecode(text);
    if (j is! Map || j['type'] != KeyBackup.type) throw const FormatException();
  } on Object {
    _fail('Not a key backup, or a damaged one');
  }
  try {
    final k = await KeyBackup.open(text, _askPassphrase('Passphrase: '));
    stderr.writeln('Opened the key backup of ${k.name}: ${k.publicKey}');
    return k.seed;
  } on KeyBackupException catch (e) {
    _fail(switch (e.problem) {
      KeyBackupProblem.wrongPassphrase =>
        'The passphrase does not open this key backup',
      KeyBackupProblem.newer => 'Key backup made by a newer version',
      _ => 'Not a key backup, or a damaged one',
    });
  }
}

/// Passphrases asked so far in this run (the test hook takes one line of
/// its file per prompt).
var _asked = 0;

/// A passphrase typed at the terminal, not shown (`/dev/tty`: stdin may
/// carry the key). The terminal is set back however the program ends,
/// also on Ctrl-C.
///
/// Tests only: with LYCHNION_TEST=1, LYCHNION_PASSPHRASE_FILE gives the
/// passphrases instead, one line per prompt (never in real use: a
/// passphrase must not be in a file).
String _askPassphrase(String prompt) {
  final testFile = Platform.environment['LYCHNION_PASSPHRASE_FILE'];
  if (testFile != null) {
    if (Platform.environment['LYCHNION_TEST'] != '1') {
      _fail('LYCHNION_PASSPHRASE_FILE is for tests only (LYCHNION_TEST=1)');
    }
    stderr.writeln('WARNING: passphrase from LYCHNION_PASSPHRASE_FILE (tests)');
    List<String> lines;
    try {
      lines = File(testFile).readAsLinesSync();
    } on FileSystemException {
      _fail('LYCHNION_PASSPHRASE_FILE cannot be read');
    }
    if (_asked >= lines.length)
      _fail('LYCHNION_PASSPHRASE_FILE: too few lines');
    return lines[_asked++];
  }
  RandomAccessFile raf;
  try {
    raf = File('/dev/tty').openSync();
  } on FileSystemException {
    _fail('Type the passphrase at a terminal (no terminal here)');
  }
  String stty(String args) {
    final r = Process.runSync('sh', ['-c', 'stty $args < /dev/tty']);
    if (r.exitCode != 0) {
      raf.closeSync();
      _fail('Could not turn off the terminal echo: nothing asked');
    }
    return (r.stdout as String).trim();
  }

  final saved = stty('-g');
  if (saved.isEmpty) {
    raf.closeSync();
    _fail('Could not read the terminal settings: nothing asked');
  }
  final bytes = <int>[];
  var cancelled = false;
  try {
    // No echo, no signal keys, key by key: a signal cannot reach this
    // program while it waits in a blocking read, so Ctrl-C, Ctrl-D,
    // Ctrl-\ and Ctrl-Z come as keys and cancel here; the terminal is
    // set back below.
    stty('-echo -isig -icanon min 1 time 0');
    stderr.write(prompt);
    for (
      var b = raf.readByteSync();
      b != 10 && b != 13;
      b = raf.readByteSync()
    ) {
      if (b == -1 || b == 3 || b == 4 || b == 28 || b == 26) {
        cancelled = true;
        break;
      }
      if (b == 127 || b == 8) {
        // Backspace: the last character, whole (UTF-8).
        while (bytes.isNotEmpty && (bytes.removeLast() & 0xC0) == 0x80) {}
        continue;
      }
      bytes.add(b);
    }
  } finally {
    Process.runSync('sh', ['-c', "stty '$saved' < /dev/tty"]);
    raf.closeSync();
    stderr.writeln();
  }
  if (cancelled) _fail('Cancelled: no passphrase given');
  try {
    return utf8.decode(bytes);
  } on FormatException {
    _fail('The passphrase is not UTF-8 text (terminal encoding?)');
  }
}

/// The warning written into the root's key backup instead of the app's,
/// which says the file can revoke the key without the passphrase.
const _rootWarning = {
  'en':
      'This file holds Lychnion\'s root key, encrypted. Keep it offline, '
      'on this stick, in a safe place. Never put it on a computer\'s disk, '
      'a server or in a repository. Without the passphrase it can neither '
      'sign nor revoke anything.',
  'ja':
      'このファイルには Lychnion のルート鍵が暗号化されて入っています。'
      'この USB メモリに入れたまま、安全な場所にオフラインで保管してください。'
      'パソコンのディスク、サーバー、リポジトリには置かないでください。'
      'パスフレーズがなければ、署名も鍵の無効化もできません。',
};

/// Writes an encrypted backup of the key on stdin (`--key -`, from
/// 1Password) to `--out FILE.lychnion-key`, for the offline root
/// (lychnion/lychnion#118; docs/key-custody.md there). The passphrase is
/// asked twice at the terminal. Unlike the app's backups it carries no
/// ready-made revocation: whoever finds a stick must not be able to revoke
/// Lychnion's root without the passphrase. The file is read back from the
/// stick and opened before the command says it is done.
Future<void> _backup(_Args args) async {
  final keyFrom = args.req('--key');
  final out = args.req('--out');
  final name = args.opt('--publisher') ?? attesterPublisher;
  if (args.positional().isNotEmpty) _strayArgument();
  if (keyFrom != '-') _fail('--key -: the key comes from stdin (op read)');
  if (!out.endsWith(KeyBackup.extension)) {
    _fail('--out: the file name ends in ${KeyBackup.extension}');
  }
  final dir = File(out).parent;
  if (!dir.existsSync()) {
    _fail('${dir.path} does not exist: is the stick mounted?');
  }
  if (FileSystemEntity.typeSync(out, followLinks: false) !=
      FileSystemEntityType.notFound) {
    _fail('$out exists: not overwritten');
  }
  final seed = await _readKey(keyFrom);
  final pass = _askPassphrase(
    'Passphrase (at least ${KeyBackup.minPassphraseLength} characters): ',
  );
  if (_askPassphrase('Again: ') != pass) _fail('The passphrases differ');
  String text;
  try {
    text = await KeyBackup.create(
      name: name,
      seed: seed,
      passphrase: pass,
      signed: _now(),
      // The most the app reads: the root is opened rarely, on a computer.
      kdf: const KeyBackupKdf(memory: 131072, iterations: 4),
    );
  } on ArgumentError catch (e) {
    _fail(
      e.message == 'passphrase'
          ? 'The passphrase is shorter than '
                '${KeyBackup.minPassphraseLength} characters'
          : 'Not a backup the app would accept (${e.message})',
    );
  }
  final j = (jsonDecode(text) as Map).cast<String, Object?>()
    ..remove('revocation')
    ..['warning'] = _rootWarning;
  text = '${const JsonEncoder.withIndent('  ').convert(j)}\n';
  // Written to FILE.part first and renamed only once it was read back
  // from the stick and opened: a stopped run leaves no file of the real
  // name, only a .part to delete.
  final part = '$out.part';
  final f = File(part);
  try {
    f.createSync(exclusive: true);
  } on FileSystemException {
    _fail(
      '$part exists (an earlier run stopped?) or cannot be created: '
      'delete it and try again',
    );
  }
  String? problem;
  try {
    await f.writeAsString(text, flush: true);
    final back = await KeyBackup.open(await f.readAsString(), pass);
    if (back.seed != seed) throw const FormatException();
  } on Object {
    problem = 'could not be written and read back';
  }
  if (problem == null &&
      FileSystemEntity.typeSync(out, followLinks: false) !=
          FileSystemEntityType.notFound) {
    problem = 'exists now (another run?)';
  }
  if (problem == null) {
    try {
      f.renameSync(out);
    } on FileSystemException {
      problem = 'could not be renamed to $out';
    }
  }
  if (problem != null) {
    try {
      f.deleteSync();
    } on FileSystemException {
      _fail('$part $problem, and could NOT be removed: delete it by hand');
    }
    _fail('$part $problem: removed. Try again.');
  }
  // Only the owner may read it, where the stick supports it (FAT does
  // not: the content is encrypted either way).
  final owner = Process.runSync('chmod', ['600', out]).exitCode == 0;
  stdout.writeln(
    'Key backup of $name → $out, read back and opened'
    '${owner ? '' : ' (this stick has no file permissions; it is encrypted)'}'
    '. Copy it to the second stick, and check there with '
    '`public --key <copy>`.',
  );
}
