param(
  [Parameter(Mandatory = $true)][string]$ProjectSlug,
  [Parameter(Mandatory = $true)][string]$FrameId,
  [Parameter(Mandatory = $true)][string]$ScriptName,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArgs = @()
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$ScriptDir = Split-Path -Parent $PSCommandPath
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$FixtureRoot = Join-Path $RepoRoot "tests\fixtures\story_projects"

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

$Manifest = Get-FixtureProjectManifest -Root $FixtureRoot -Slug $ProjectSlug
if (-not $Manifest) {
  throw "Project manifest was not found for $ProjectSlug under tests\fixtures\story_projects."
}

if ($FrameId -notmatch 'frame_(\d+)') {
  throw "FrameId must contain frame_<number>."
}

$FrameDir = Join-Path $Manifest.DirectoryName ("generation_prompts\frame_{0}" -f $Matches[1])
if (-not (Test-Path -LiteralPath $FrameDir)) {
  throw "Frame directory was not found for $FrameId at $FrameDir."
}

$FrameScript = Join-Path $FrameDir $ScriptName
if (-not (Test-Path -LiteralPath $FrameScript)) {
  throw "Frame script was not found at $FrameScript."
}

& $FrameScript @ScriptArgs
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
