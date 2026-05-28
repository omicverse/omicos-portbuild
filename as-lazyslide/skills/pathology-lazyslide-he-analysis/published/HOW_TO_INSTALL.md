# How to install this skill into omicos-admin

This factory NEVER writes into omicos-admin/ directly.

```bash
ADMIN=/path/to/omicos-admin
rm -rf "$ADMIN/skills/pathology-lazyslide-he-analysis"
cp -r /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/skills/pathology-lazyslide-he-analysis/published/skills/pathology-lazyslide-he-analysis "$ADMIN/skills/"
```

This is a skill-only bundle — install the OWNING AGENT separately
(it bundles this skill plus any siblings).
