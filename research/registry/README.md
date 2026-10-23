# Study registry

One folder per study, `<YYYY-MM>-<short-name>/`:

| File | Written | Rule |
|---|---|---|
| `prereg.md` | by hand, from `TEMPLATE.md` | committed and reviewed before the study runs; never edited afterwards |
| `dataset_manifest.json` | copied from the frozen dataset | its sha256 is in `prereg.md` |
| `result.md` | by the study script only | PROTECTED (AGENTS.md); byte-identical for the same dataset id |

A study that is changed after its result exists is a new study with a new
folder. Its earlier result stays.
