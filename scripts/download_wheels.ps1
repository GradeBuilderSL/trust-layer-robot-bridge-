# Download pip wheels for linux/arm64 (robot). Run from repo root.
# Requires Docker Desktop. Output: .\wheels\*.whl - copy full repo to robot, then:
#   docker build -t trust-bridge .
# (build uses wheels/ when .whl files are present; no PyPI/DNS needed inside build.)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
New-Item -ItemType Directory -Force -Path "$root\wheels" | Out-Null
Get-ChildItem "$root\wheels\*.whl" -ErrorAction SilentlyContinue | Remove-Item -Force
docker run --platform linux/arm64 --rm `
  -v "${root}/wheels:/out" `
  -v "${root}/requirements.txt:/req.txt:ro" `
  python:3.11-slim `
  bash -c "pip install -q -U pip && pip download -d /out -r /req.txt"
Write-Host "Done. Wheels in $root\wheels - scp the folder to the robot with the rest of the repo."
