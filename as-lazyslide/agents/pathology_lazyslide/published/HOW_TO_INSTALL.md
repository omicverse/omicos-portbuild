# How to install this agent bundle (pathology_lazyslide) into omicos-admin

This bundle contains ONE agent + 6 owned skill(s).

```bash
ADMIN=/path/to/omicos-admin

# Agent:
cp /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/agents/pathology_lazyslide/published/agents/pathology_lazyslide.md "$ADMIN/agents/"

# Skills (overwrites any existing folders of the same id):
rm -rf "$ADMIN/skills/pathology-lazyslide-he-analysis"
cp -r /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/agents/pathology_lazyslide/published/skills/pathology-lazyslide-he-analysis "$ADMIN/skills/"
rm -rf "$ADMIN/skills/pathology-lazyslide-cell-segmentation"
cp -r /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/agents/pathology_lazyslide/published/skills/pathology-lazyslide-cell-segmentation "$ADMIN/skills/"
rm -rf "$ADMIN/skills/pathology-lazyslide-supervised-classification"
cp -r /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/agents/pathology_lazyslide/published/skills/pathology-lazyslide-supervised-classification "$ADMIN/skills/"
rm -rf "$ADMIN/skills/pathology-lazyslide-survival-prediction"
cp -r /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/agents/pathology_lazyslide/published/skills/pathology-lazyslide-survival-prediction "$ADMIN/skills/"
rm -rf "$ADMIN/skills/pathology-lazyslide-he-transcriptomics-integration"
cp -r /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/agents/pathology_lazyslide/published/skills/pathology-lazyslide-he-transcriptomics-integration "$ADMIN/skills/"
rm -rf "$ADMIN/skills/pathology-lazyslide-virtual-staining"
cp -r /scratch/users/steorra/analysis/omicos_dev/omicos-skill-generate/as-lazyslide/agents/pathology_lazyslide/published/skills/pathology-lazyslide-virtual-staining "$ADMIN/skills/"
```

After copying, run admin's own `validate_skills.py` to confirm
everything parses on the deploy side.
