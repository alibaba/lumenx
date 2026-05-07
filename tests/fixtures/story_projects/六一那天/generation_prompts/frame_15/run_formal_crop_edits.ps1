$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$ImageGen = "C:\Users\PC\.codex\skills\.system\imagegen\scripts\image_gen.py"
$FrameDir = Split-Path -Parent $PSCommandPath
$RepoRoot = (Resolve-Path (Join-Path $FrameDir "..\..\..\..\..\..")).Path
$GeneratedDir = Join-Path $RepoRoot "output\codex_image_audit\liuyi-that-day\generated"
$FixtureDir = Join-Path $RepoRoot "output\uploads\fixtures"
$ComposeWrapper = Join-Path $RepoRoot "scripts\compose_fixture_frame_crops.ps1"

function Test-ImageSize {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][int]$Width,
    [Parameter(Mandatory = $true)][int]$Height
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    return $false
  }

  $check = @'
from pathlib import Path
from PIL import Image
import sys

path = Path(sys.argv[1])
width = int(sys.argv[2])
height = int(sys.argv[3])

if not path.exists():
    raise SystemExit(2)

with Image.open(path) as image:
    if image.size != (width, height):
        raise SystemExit(1)
'@

  & python -X utf8 -c $check $Path $Width $Height | Out-Null
  return $LASTEXITCODE -eq 0
}

function Invoke-FormalCropEdit {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$PromptFile,
    [Parameter(Mandatory = $true)][string]$InputCrop,
    [Parameter(Mandatory = $true)][string]$ReferenceImage,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$Size
  )

  $sizeParts = $Size -split "x"
  $targetWidth = [int]$sizeParts[0]
  $targetHeight = [int]$sizeParts[1]

  if (Test-ImageSize -Path $OutputPath -Width $targetWidth -Height $targetHeight) {
    Write-Host "$Label already matches $Size; skipping edit."
    return
  }

  $maxAttempts = 4
  for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    if (Test-Path -LiteralPath $OutputPath) {
      Remove-Item -LiteralPath $OutputPath -Force
    }

    & python $ImageGen edit `
      --model gpt-image-2 `
      --prompt-file $PromptFile `
      --image $InputCrop `
      --image $ReferenceImage `
      --size $Size `
      --quality medium `
      --output-format png `
      --out $OutputPath `
      --force

    if ($LASTEXITCODE -eq 0 -and (Test-ImageSize -Path $OutputPath -Width $targetWidth -Height $targetHeight)) {
      Write-Host "$Label completed at $Size."
      return
    }

    if ($attempt -lt $maxAttempts) {
      $waitSeconds = [Math]::Min(30 * [Math]::Pow(2, $attempt - 1), 180)
      Write-Host "$Label attempt $attempt did not land on $Size; retrying in $waitSeconds seconds..."
      Start-Sleep -Seconds $waitSeconds
    }
  }

  throw "$Label failed to produce $Size output at $OutputPath."
}

Invoke-FormalCropEdit `
  -Label "adult_xiaoqi" `
  -PromptFile (Join-Path $FrameDir "02a_adult_xiaoqi_crop_edit_prompt.txt") `
  -InputCrop (Join-Path $GeneratedDir "liuyi_frame_15_stage1_base_v3_crop_xiaoqi.png") `
  -ReferenceImage (Join-Path $FixtureDir "liuyi_char_xiaoqi_adult_full_body.png") `
  -OutputPath (Join-Path $GeneratedDir "liuyi_frame_15_stage2a_xiaoqi_crop_formal_v1.png") `
  -Size "640x1088"

Invoke-FormalCropEdit `
  -Label "boy_father" `
  -PromptFile (Join-Path $FrameDir "02c_father_crop_edit_prompt.txt") `
  -InputCrop (Join-Path $GeneratedDir "liuyi_frame_15_stage1_base_v3_crop_father.png") `
  -ReferenceImage (Join-Path $FixtureDir "liuyi_char_boy_father_full_body.png") `
  -OutputPath (Join-Path $GeneratedDir "liuyi_frame_15_stage2c_father_crop_formal_v1.png") `
  -Size "1024x768"

& $ComposeWrapper `
  -ProjectSlug "liuyi-that-day" `
  -FrameId "liuyi_frame_15"

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
