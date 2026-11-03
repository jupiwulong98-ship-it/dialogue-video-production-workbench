# CPU-first Windows 11 setup (local Python dependencies only)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
Write-Host "Setting up Windows 11 CPU-only dialogue workbench"
$launcher = $null
$launcherArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" 2>$null
  if ($LASTEXITCODE -eq 0) { $launcher = "py"; $launcherArgs = @("-3.11") }
}
if (-not $launcher -and (Get-Command python -ErrorAction SilentlyContinue)) {
  & python -c "import sys; assert sys.version_info[:2] == (3, 11)" 2>$null
  if ($LASTEXITCODE -eq 0) { $launcher = "python" }
}
if (-not $launcher) { throw "Python 3.11 missing. Try: winget install -e --id Python.Python.3.11" }
$exe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $exe)) {
  & $launcher @launcherArgs -m venv ".venv"
  if ($LASTEXITCODE -ne 0) { throw "Python virtual environment setup failed." }
}
& $exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $exe -m pip install -e ".[desktop]"
if ($LASTEXITCODE -ne 0) { throw "pip dependency installation failed" }
Write-Host "App dependencies installed. No dedicated GPU or CUDA required."
Write-Host "Optional LaMa CPU repair is NOT installed. Main workflow can run without torch."
foreach ($item in @("ffmpeg", "ffprobe", "codex", "dreamina-canvas")) {
  $found = Get-Command $item -ErrorAction SilentlyContinue
  if ($found) { Write-Host ("OK: " + $item + " => " + $found.Source) }
  else { Write-Warning ("Missing external tool: " + $item) }
}
Write-Host "After installing the missing CLIs and logging in, run start_windows.bat."
