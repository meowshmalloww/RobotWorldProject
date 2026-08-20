<#
Downloads the exact DINOv3 ViT-L/16 image conditioner used by the local
TRELLIS.2-4B gateway. This is intentionally native Windows only: no Docker.

Prerequisite: accept access terms at
https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m
#>

$ErrorActionPreference = 'Stop'

$hf = 'D:\TRELLIS.2-runtime\.venv\Scripts\hf.exe'
$destination = 'D:\DINOv3'
$model = 'facebook/dinov3-vitl16-pretrain-lvd1689m'

if (-not (Test-Path -LiteralPath $hf -PathType Leaf)) {
    throw "Hugging Face CLI was not found at $hf"
}

Write-Host 'A Hugging Face read token is requested by the official CLI.'
Write-Host 'Paste it only into the CLI prompt; do not put it in source files.'
& $hf auth login
if ($LASTEXITCODE -ne 0) {
    throw 'Hugging Face authentication was not completed.'
}

New-Item -ItemType Directory -Force -Path $destination | Out-Null
& $hf download $model --local-dir $destination
if ($LASTEXITCODE -ne 0) {
    throw 'The DINOv3 checkpoint download failed. Confirm model access was accepted first.'
}

$config = Join-Path $destination 'config.json'
$weights = Get-ChildItem -LiteralPath $destination -File -Recurse |
    Where-Object { $_.Name -match '^model.*\.(safetensors|bin)$' -or $_.Name -eq 'model.safetensors.index.json' } |
    Select-Object -First 1

if (-not (Test-Path -LiteralPath $config) -or -not $weights) {
    throw "Download completed without the expected Transformers checkpoint files in $destination"
}

Write-Host "DINOv3 is installed and verified at $destination"
Write-Host 'Return to RobotWorld; the TRELLIS gateway will detect this folder and the existing sink job can be rerun.'
