param(
  [Parameter(Mandatory = $true)][string]$ProjectSlug,
  [Parameter(Mandatory = $true)][string]$FrameId,
  [string]$Out,
  [switch]$NoVerify,
  [switch]$DetectOnly,
  [string]$WriteDetectedManifest
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$ScriptDir = Split-Path -Parent $PSCommandPath
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$FixtureRoot = Join-Path $RepoRoot "tests\fixtures\story_projects"
$ComposeScript = Join-Path $RepoRoot "scripts\compose_frame_crops.py"

function Get-FixtureProjectManifest {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Slug
  )

  $slugPattern = '"slug"\s*:\s*"' + [regex]::Escape($Slug) + '"'
  foreach ($candidate in Get-ChildItem -LiteralPath $Root -Recurse -Filter "project_manifest.json") {
    $content = Get-Content -LiteralPath $candidate.FullName -Raw -Encoding UTF8
    if ($content -match $slugPattern) {
      return $candidate
    }
  }

  return $null
}

$ProjectManifest = Get-FixtureProjectManifest -Root $FixtureRoot -Slug $ProjectSlug
if (-not $ProjectManifest) {
  throw "Project manifest was not found for $ProjectSlug under tests\fixtures\story_projects."
}

if ($FrameId -notmatch 'frame_(\d+)') {
  throw "FrameId must contain frame_<number>."
}

$FrameDir = Join-Path $ProjectManifest.DirectoryName ("generation_prompts\frame_{0}" -f $Matches[1])
if (-not (Test-Path -LiteralPath $FrameDir)) {
  throw "Frame directory was not found for $FrameId at $FrameDir."
}

$CropManifest = Join-Path $FrameDir "crop_composition_manifest.json"
if (-not (Test-Path -LiteralPath $CropManifest)) {
  throw "Crop composition manifest was not found at $CropManifest."
}

$PythonArgs = @(
  "-X",
  "utf8",
  $ComposeScript,
  "--manifest",
  $CropManifest
)

if ($Out) {
  $PythonArgs += @("--out", $Out)
}
if ($NoVerify) {
  $PythonArgs += "--no-verify"
}
if ($DetectOnly) {
  $PythonArgs += "--detect-only"
}
if ($WriteDetectedManifest) {
  $PythonArgs += @("--write-detected-manifest", $WriteDetectedManifest)
}

& python @PythonArgs
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
