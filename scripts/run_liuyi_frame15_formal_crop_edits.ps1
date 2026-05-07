param(
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArgs = @()
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$ScriptDir = Split-Path -Parent $PSCommandPath
$GenericRunner = Join-Path $ScriptDir "run_fixture_frame_script.ps1"

& $GenericRunner `
  -ProjectSlug "liuyi-that-day" `
  -FrameId "liuyi_frame_15" `
  -ScriptName "run_formal_crop_edits.ps1" `
  @ScriptArgs

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
