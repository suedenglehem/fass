# VMAF models

`vmaf_v0.6.1.json` — the official VMAF v0.6.1 model, JSON format.

- **Source:** https://raw.githubusercontent.com/Netflix/vmaf/master/model/vmaf_v0.6.1.json
- **sha256:** `5950d61fa1f861bd45d8149d80539ed9f3376cfc2495b8f0fa8e9f57cb131ee3`
- **Size:** 19101 bytes
- **Verified 2026-10-26:** re-downloaded from the URL above; byte-identical to
  this copy (`cmp` clean).

The official Netflix/vmaf distribution ships v0.6.1 as JSON only — there is no
`vmaf_v0.6.1.pkl` in `master` (float-precision variants aside). The planner's
`vmaf_model_path()` accepts both `.pkl` (legacy) and `.json` under
`FASS_VMAF_MODEL_DIR`.

## Where it is used

- **9.0.1 docker image:** the Dockerfile bakes this directory to
  `/opt/vmaf-models/` and sets `FASS_VMAF_MODEL=/opt/vmaf-models/vmaf_v0.6.1.json`,
  so the `libvmaf` filter loads this model by explicit path.
- **Host ffmpeg (Ubuntu 6.1.1):** no model applies — the distro build lacks the
  `libvmaf` *filter* entirely (only `vmafmotion`), so host-side libvmaf cases
  are skipped by design; the 9.0.1 container half of dual-build verification
  exercises `libvmaf` for real.
