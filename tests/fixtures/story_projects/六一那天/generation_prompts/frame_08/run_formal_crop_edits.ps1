param(
  [switch]$Force
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$ProjectSlug = "liuyi-that-day"
$FrameId = "liuyi_frame_08"
$FrameDir = Split-Path -Parent $PSCommandPath
$RepoRoot = (Resolve-Path (Join-Path $FrameDir "..\..\..\..\..\..")).Path
$GeneratedDir = Join-Path $RepoRoot "output\codex_image_audit\$ProjectSlug\generated"
$CropManifest = Join-Path $FrameDir "crop_composition_manifest.json"
# The crop manifest and compose pass are guarded by visual_gate checks; file presence alone is not enough.
$ChildIdentityComposer = Join-Path $RepoRoot "scripts\compose_liuyi_child_identity_crop.py"
$ComposeWrapper = Join-Path $RepoRoot "scripts\compose_fixture_frame_crops.ps1"
$Stage2Output = Join-Path $GeneratedDir "liuyi_frame_08_stage2_child_xiaoqi_formal_v1.png"

if ($Force -and (Test-Path -LiteralPath $Stage2Output)) {
  Remove-Item -LiteralPath $Stage2Output -Force
}

& python -X utf8 $ChildIdentityComposer `
  --manifest $CropManifest `
  --output $Stage2Output

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

& $ComposeWrapper `
  -ProjectSlug $ProjectSlug `
  -FrameId $FrameId

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
