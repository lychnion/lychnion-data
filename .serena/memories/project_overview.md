# Lychnion data: overview

Data for the Lychnion app: signed records of every package, map and model
version, Lychnion's public key, and the tools (mostly Python in `tool/`) that
build, sign and publish packages and map areas. The data files themselves are
in the GitHub releases. Repo `lychnion/lychnion-data` is public: everything in
these memories is public too.

Do not restate the docs here. Read:
- `README.md`: how the app trusts a file, layout, how packages are made,
  mirrors, sources and licences.
- `.github/workflows/ci.yml`: the checks CI runs.

The app is in the sibling repo `lychnion`; publishers build their own guides
with `lychnion-studio`.
