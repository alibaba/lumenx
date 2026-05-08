$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$ProjectSlug = "PROJECT_SLUG"
$FrameId = "FRAME_ID"
$ProjectImageModel = "gpt-image2"
$CliImageModel = if ($ProjectImageModel -eq "gpt-image2") { "gpt-image" + "-2" } else { $ProjectImageModel }
$ImageGen = "C:\Users\PC\.codex\skills\.system\imagegen\scripts\image_gen.py"
$FrameDir = Split-Path -Parent $PSCommandPath
$RepoRoot = (Resolve-Path (Join-Path $FrameDir "..\..\..\..\..\..")).Path
$GeneratedDir = Join-Path $RepoRoot "output\codex_image_audit\$ProjectSlug\generated"
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
      --model $CliImageModel `
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
  -Label "CROP_ID" `
  -PromptFile (Join-Path $FrameDir "PROMPT_FILE.txt") `
  -InputCrop (Join-Path $GeneratedDir "FRAME_ID_stage1_base_crop_CROP_ID.png") `
  -ReferenceImage (Join-Path $FixtureDir "REFERENCE_IMAGE.png") `
  -OutputPath (Join-Path $GeneratedDir "FRAME_ID_stage2_CROP_ID_formal_v1.png") `
  -Size "1024x768"

& $ComposeWrapper `
  -ProjectSlug $ProjectSlug `
  -FrameId $FrameId

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
