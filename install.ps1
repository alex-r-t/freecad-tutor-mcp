<#
.SYNOPSIS
Install FreeCAD Tutor MCP on Windows 10/11 (PowerShell 5.1+ compatible).

.DESCRIPTION
Designed for both developer machines and clean Windows 11 PCs with no Python
or developer tools preinstalled:
  1. Finds the FreeCAD user Mod directory (including versioned v1-1/v1-0 paths).
  2. Installs the FreeCADTutorMCP workbench addon (copies files by default; use
     -DevLink to write a thin loader pointing back to this repo).
  3. Ensures uv or standalone Python >= 3.10 is available (skipping Windows Store
     App Execution Alias stubs), creates .venv, and installs freecad-tutor-mcp.
  4. Safely backs up and merges "freecad-tutor" into Claude Desktop's config in
     clean BOM-less UTF-8 JSON using .venv\Scripts\python.exe.

.PARAMETER ModDir
Explicit FreeCAD Mod directory path (optional).

.PARAMETER DevLink
Write a thin InitGui.py loader pointing back to freecad_addon\ instead of copying.

.PARAMETER SkipClaudeConfig
Do not create or modify claude_desktop_config.json.
#>
[CmdletBinding()]
param(
    [string]$ModDir = "",
    [switch]$DevLink,
    [switch]$SkipClaudeConfig
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AddonSrc = Join-Path $RepoRoot "freecad_addon"

if (-not (Test-Path (Join-Path $AddonSrc "InitGui.py"))) {
    throw "Could not find freecad_addon\InitGui.py under $RepoRoot"
}

function Resolve-FreeCADModDir {
    param([string]$ExplicitModDir)

    if ($ExplicitModDir) {
        return $ExplicitModDir
    }

    # 1. Check FreeCADCmd.exe on PATH and common Windows install locations.
    $cmdCandidates = @()
    $onPath = Get-Command "FreeCADCmd.exe", "freecadcmd.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($onPath) { $cmdCandidates += $onPath.Source }

    $searchRoots = @(
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        (Join-Path $env:LOCALAPPDATA "Programs"),
        (Join-Path $env:USERPROFILE "scoop\apps")
    )
    foreach ($root in $searchRoots) {
        if ($root -and (Test-Path $root)) {
            $dirs = Get-ChildItem -Path $root -Directory -Filter "FreeCAD*" -ErrorAction SilentlyContinue
            foreach ($d in $dirs) {
                $exe1 = Join-Path $d.FullName "bin\FreeCADCmd.exe"
                $exe2 = Join-Path $d.FullName "current\bin\FreeCADCmd.exe"
                if (Test-Path $exe1) { $cmdCandidates += $exe1 }
                if (Test-Path $exe2) { $cmdCandidates += $exe2 }
            }
        }
    }

    foreach ($cmdExe in ($cmdCandidates | Select-Object -Unique)) {
        try {
            $pyOut = & $cmdExe -c "import os, FreeCAD; print(os.path.join(FreeCAD.getUserAppDataDir(), 'Mod'))" 2>$null
            $candidate = ($pyOut | Select-Object -Last 1)
            if ($candidate) {
                $trimmed = $candidate.Trim()
                if ($trimmed) { return $trimmed }
            }
        } catch {
            # Fall back to probing %APPDATA%\FreeCAD
        }
    }

    # 2. Probe %APPDATA%\FreeCAD\v1-* (newest first), then unversioned Mod.
    $fcAppData = Join-Path $env:APPDATA "FreeCAD"
    if (Test-Path $fcAppData) {
        $versioned = Get-ChildItem -Path $fcAppData -Directory -Filter "v1-*" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($v in $versioned) {
            return (Join-Path $v.FullName "Mod")
        }
        return (Join-Path $fcAppData "Mod")
    }

    # 3. Default for a fresh Windows 11 FreeCAD 1.1+ installation.
    return (Join-Path $fcAppData "v1-1\Mod")
}

function Find-UvExe {
    $cmd = Get-Command "uv.exe", "uv" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }

    foreach ($p in @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Find-RealPython {
    # Windows 11 places a zero-byte Microsoft Store stub at %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe.
    # Always reject anything inside WindowsApps and verify Python >= 3.10.
    $candidates = @()
    foreach ($name in @("python.exe", "python3.exe")) {
        $found = Get-Command $name -All -ErrorAction SilentlyContinue
        foreach ($item in $found) {
            if ($item.Source -and ($item.Source -notlike "*\Microsoft\WindowsApps\*")) {
                $candidates += $item.Source
            }
        }
    }

    $pyRoots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        "$env:ProgramFiles\Python*"
    )
    foreach ($r in $pyRoots) {
        Get-Item $r -ErrorAction SilentlyContinue | ForEach-Object {
            Get-ChildItem $_.FullName -Filter "python.exe" -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                ForEach-Object { $candidates += $_.FullName }
        }
    }

    foreach ($exe in ($candidates | Select-Object -Unique)) {
        try {
            $verOk = & $exe -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)" 2>$null
            if ($verOk -and $verOk.Trim() -eq "1") {
                return $exe
            }
        } catch {
            continue
        }
    }
    return $null
}

function Install-UvIfMissing {
    $uv = Find-UvExe
    if ($uv) { return $uv }

    Write-Host "  uv not found; attempting automatic install..." -ForegroundColor Yellow
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($winget) {
        try {
            & $winget.Source install --id astral-sh.uv --silent --accept-package-agreements --accept-source-agreements | Out-Null
            $uv = Find-UvExe
            if ($uv) { return $uv }
        } catch {
            # Continue to Astral script fallback
        }
    }

    try {
        $installer = Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" -UseBasicParsing
        Invoke-Expression $installer
        $uv = Find-UvExe
        if ($uv) { return $uv }
    } catch {
        # Let caller fall back to standalone Python if available
    }
    return $null
}

# ── 1. Install FreeCAD Addon ─────────────────────────────────────────────
$ResolvedModDir = Resolve-FreeCADModDir -ExplicitModDir $ModDir
$TargetAddonDir = Join-Path $ResolvedModDir "FreeCADTutorMCP"

Write-Host "Installing FreeCAD Tutor MCP..." -ForegroundColor Cyan
Write-Host "  Repo root : $RepoRoot"
Write-Host "  Mod target: $TargetAddonDir"

New-Item -ItemType Directory -Force -Path $TargetAddonDir | Out-Null

if ($DevLink) {
    $EscapedAddonSrc = $AddonSrc.Replace("\", "\\")
    $LoaderLines = @(
        "import os"
        "import sys"
        ""
        "_ADDON_DIR = `"$EscapedAddonSrc`""
        "if _ADDON_DIR not in sys.path:"
        "    sys.path.insert(0, _ADDON_DIR)"
        ""
        "_project_init = os.path.join(_ADDON_DIR, `"InitGui.py`")"
        "_ns = dict(globals())"
        "_ns[`"__file__`"] = _project_init"
        "with open(_project_init, `"r`", encoding=`"utf-8`") as _f:"
        "    exec(compile(_f.read(), _project_init, `"exec`"), _ns)"
    )
    $LoaderText = ($LoaderLines -join "`r`n") + "`r`n"
    [System.IO.File]::WriteAllText((Join-Path $TargetAddonDir "InitGui.py"), $LoaderText, (New-Object System.Text.UTF8Encoding($false)))
    if (Test-Path (Join-Path $AddonSrc "package.xml")) {
        Copy-Item -Path (Join-Path $AddonSrc "package.xml") -Destination (Join-Path $TargetAddonDir "package.xml") -Force
    }
    Write-Host "  [OK] Installed developer loader InitGui.py in $TargetAddonDir" -ForegroundColor Green
} else {
    Get-ChildItem -Path $AddonSrc -Force | Where-Object { $_.Name -ne "__pycache__" } | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $TargetAddonDir -Recurse -Force
    }
    Write-Host "  [OK] Copied FreeCADTutorMCP addon files into $TargetAddonDir" -ForegroundColor Green
}

if (-not (Test-Path (Join-Path $TargetAddonDir "InitGui.py"))) {
    throw "Addon install verification failed: InitGui.py missing in $TargetAddonDir"
}

# ── 2. Set up isolated .venv for the MCP server ──────────────────────────
Push-Location $RepoRoot
try {
    $uvExe = Find-UvExe
    $realPy = Find-RealPython

    if (-not $uvExe -and -not $realPy) {
        $uvExe = Install-UvIfMissing
    }

    if ($uvExe) {
        Write-Host "  Setting up .venv via uv ($uvExe)..."
        if ($realPy) {
            & $uvExe venv --allow-existing .venv
        } else {
            # On a clean Windows 11 PC with no Python installed, uv downloads Python 3.12 automatically.
            & $uvExe venv --python 3.12 --allow-existing .venv
        }
        & $uvExe pip install --python .venv\Scripts\python.exe -e .
    } elseif ($realPy) {
        Write-Host "  Setting up .venv via standalone Python ($realPy)..."
        & $realPy -m venv .venv
        & .venv\Scripts\python.exe -m pip install --upgrade pip
        & .venv\Scripts\python.exe -m pip install -e .
    } else {
        throw "Neither uv nor Python >= 3.10 was found (and automatic uv install failed). Install uv (https://docs.astral.sh/uv/) or Python 3.12 and re-run."
    }
} finally {
    Pop-Location
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$ServerExe = Join-Path $RepoRoot ".venv\Scripts\freecad-tutor-mcp.exe"
if (-not (Test-Path $ServerExe)) {
    throw "Expected entry point not found: $ServerExe"
}
Write-Host "  [OK] Installed MCP server entry point: $ServerExe" -ForegroundColor Green

# ── 3. Merge Claude Desktop config using Python (BOM-less UTF-8, PS 5.1 safe) ──
if (-not $SkipClaudeConfig) {
    $ClaudeDir = Join-Path $env:APPDATA "Claude"
    $ClaudeConfigPath = Join-Path $ClaudeDir "claude_desktop_config.json"

    $PatchScript = @'
import json
import os
import shutil
import sys
from datetime import datetime

config_path, server_exe = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(config_path), exist_ok=True)

data = {}
if os.path.exists(config_path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{config_path}.bak.{stamp}"
    shutil.copy2(config_path, backup)
    print(f"  Backed up existing Claude config to {backup}")
    with open(config_path, "r", encoding="utf-8-sig") as f:
        raw = f.read().strip()
        if raw:
            data = json.loads(raw)

if not isinstance(data, dict):
    raise ValueError("Existing claude_desktop_config.json is not a JSON object")

servers = data.get("mcpServers")
if not isinstance(servers, dict):
    servers = {}
    data["mcpServers"] = servers

servers["freecad-tutor"] = {
    "command": server_exe,
    "env": {
        "FREECAD_MCP_HOST": "127.0.0.1",
        "FREECAD_MCP_PORT": "9876",
    },
}

with open(config_path, "w", encoding="utf-8", newline="\n") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
'@

    # Pass via Base64 because Windows PowerShell 5.1 strips double quotes when forwarding strings to native EXEs.
    $PatchB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($PatchScript))
    & $VenvPython -c "import base64, sys; exec(base64.b64decode(sys.argv.pop(1)).decode('utf-8'))" $PatchB64 $ClaudeConfigPath $ServerExe
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update $ClaudeConfigPath"
    }
    Write-Host "  [OK] Updated Claude Desktop config (BOM-less UTF-8): $ClaudeConfigPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Launch (or restart) FreeCAD 1.0/1.1+. Check Report View for: 'FreeCAD Tutor MCP: RPC server auto-started on 127.0.0.1:9876'."
Write-Host "  2. Launch (or restart) Claude Desktop."
Write-Host "  3. Recommended: Create a Claude Desktop Project named 'FreeCAD Course' and paste CLAUDE_PROJECT_INSTRUCTIONS.md."
