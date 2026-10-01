[CmdletBinding()]
param(
    [string]$GameDirectory = "E:/SteamLibrary/steamapps/common/Helldivers 2",
    [switch]$DryRun,
    [switch]$Rollback,
    [switch]$Followup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$script:ExpectedGameSha256 = "2e2c3b7c2500646dadd5f2b4c6e0504dbb7e7896139f64cddc0d1813c718f51e"
$script:ExpectedGameSize = 15522408L
$script:ExpectedLoaderZipSha256 = "53af5698aeacfb27b98dfa00054923d11dc854e1e67b4af14798877812a93ba6"
$script:ExpectedProbeZipSha256 = "10d7f2ae692be43430327b90eb834697393c7b9b90bf0edf593eac8db292b941"
$script:ProbeZipName = "HD2ChatProbe.zip"
$script:ExpectedProbePatchLength = 43424L
# 补充探针使用独立来源包；原包保留以回滚首次部署。
if ($Followup) {
    $script:ExpectedProbeZipSha256 = "7e562cda0307d5db679e9d3a8c57073044c25247c03581ecf29f35c7459f2334"
    $script:ProbeZipName = "HD2ChatProbeFollowup.zip"
    $script:ExpectedProbePatchLength = 44448L
}
$script:LoaderResourceId = [Convert]::ToUInt64("7251fdd9bb62480a", 16)
$script:ProbeResourceId = [Convert]::ToUInt64("c509c11199f753c2", 16)
$script:ArchiveMagic = [Convert]::ToUInt32("F0000011", 16)
$script:ArchiveHeaderBytes = 72L
$script:ArchiveEntryBytes = 80L
$script:ReceiptPath = Join-Path $script:ProjectRoot ".local/chat-probe-deployment.json"
$script:ReceiptPartialPath = $script:ReceiptPath + ".partial"

function ConvertTo-NormalizedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "路径为空。" }
    $full = [System.IO.Path]::GetFullPath($Path)
    $trimmed = $full.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar))
    if ($trimmed.Length -eq 2 -and $trimmed[1] -eq ":") { return $trimmed + [System.IO.Path]::DirectorySeparatorChar }
    return $trimmed
}

function Test-SamePath([string]$Left, [string]$Right) {
    return [string]::Equals(
        (ConvertTo-NormalizedPath $Left),
        (ConvertTo-NormalizedPath $Right),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-LeafSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-NotReparsePoint([string]$Path, [string]$Label) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label 是重解析点，为避免越界写入已停止：$Path"
    }
}

function Resolve-GameContext([string]$RequestedPath) {
    if (-not (Test-Path -LiteralPath $RequestedPath -PathType Container)) {
        throw "游戏目录不存在：$RequestedPath"
    }
    Assert-NotReparsePoint $RequestedPath "游戏目录"
    $resolvedRoot = ConvertTo-NormalizedPath (Resolve-Path -LiteralPath $RequestedPath).ProviderPath
    $binPath = Join-Path $resolvedRoot "bin"
    $dataPath = Join-Path $resolvedRoot "data"
    if (-not (Test-Path -LiteralPath $binPath -PathType Container)) { throw "缺少 bin 目录：$binPath" }
    if (-not (Test-Path -LiteralPath $dataPath -PathType Container)) { throw "缺少 data 目录：$dataPath" }
    Assert-NotReparsePoint $binPath "游戏 bin 目录"
    Assert-NotReparsePoint $dataPath "游戏 data 目录"
    $resolvedBin = ConvertTo-NormalizedPath (Resolve-Path -LiteralPath $binPath).ProviderPath
    $resolvedData = ConvertTo-NormalizedPath (Resolve-Path -LiteralPath $dataPath).ProviderPath
    if (-not (Test-SamePath $resolvedBin (Join-Path $resolvedRoot "bin"))) {
        throw "bin 目录解析结果不在指定游戏目录内。"
    }
    if (-not (Test-SamePath $resolvedData (Join-Path $resolvedRoot "data"))) {
        throw "data 目录解析结果不在指定游戏目录内。"
    }

    $exePath = Join-Path $resolvedBin "helldivers2.exe"
    $gameFilesPath = Join-Path $resolvedData "game"
    if (-not (Test-Path -LiteralPath $gameFilesPath -PathType Container)) { throw "缺少 data/game 目录：$gameFilesPath" }
    Assert-NotReparsePoint $gameFilesPath "游戏资源目录"
    $dllPath = Join-Path $gameFilesPath "game.dll"
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "缺少游戏 EXE：$exePath" }
    if (-not (Test-Path -LiteralPath $dllPath -PathType Leaf)) { throw "缺少 game.dll：$dllPath" }
    Assert-NotReparsePoint $exePath "游戏 EXE"
    Assert-NotReparsePoint $dllPath "game.dll"
    $dllItem = Get-Item -LiteralPath $dllPath
    if ([long]$dllItem.Length -ne $script:ExpectedGameSize) {
        throw "game.dll 大小不符合已核验构建（期望 $($script:ExpectedGameSize)，实际 $($dllItem.Length)）。"
    }
    $dllHash = Get-LeafSha256 $dllPath
    if ($dllHash -cne $script:ExpectedGameSha256) {
        throw "game.dll SHA-256 不符合已核验构建。"
    }
    return [pscustomobject]@{
        Root = $resolvedRoot
        Bin = $resolvedBin
        Data = $resolvedData
        Exe = $exePath
        GameDll = $dllPath
        GameDllSha256 = $dllHash
        GameDllSize = [long]$dllItem.Length
    }
}

function Assert-GameStopped {
    $running = @(Get-Process -Name "helldivers2" -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        throw "检测到 Helldivers 2 正在运行。请先正常退出游戏，再执行部署或回滚。"
    }
}

function Read-ZipEntryBytes([string]$ZipPath, [string]$EntryPath) {
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $matches = @($archive.Entries | Where-Object { $_.FullName -ceq $EntryPath })
        if ($matches.Count -ne 1) { throw "压缩包固定条目缺失或重复：$EntryPath" }
        $entry = $matches[0]
        if ([long]$entry.Length -gt 16MB) { throw "压缩包条目超过安全读取上限：$EntryPath" }
        $input = $entry.Open()
        $memory = [System.IO.MemoryStream]::new()
        try {
            $input.CopyTo($memory)
            $bytes = $memory.ToArray()
        }
        finally {
            $input.Dispose()
            $memory.Dispose()
        }
        if ([long]$bytes.LongLength -ne [long]$entry.Length) { throw "压缩包条目长度不一致：$EntryPath" }
        return ,$bytes
    }
    finally {
        $archive.Dispose()
    }
}

function Get-VerifiedPayloads {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $loaderZip = Join-Path $script:ProjectRoot "artifacts/Bingus-Shared-Loader-v18.zip"
    $probeZip = Join-Path $script:ProjectRoot ("artifacts/" + $script:ProbeZipName)
    foreach ($zipPath in @($loaderZip, $probeZip)) {
        if (-not (Test-Path -LiteralPath $zipPath -PathType Leaf)) { throw "缺少固定来源 ZIP：$zipPath" }
    }
    $loaderHash = Get-LeafSha256 $loaderZip
    $probeHash = Get-LeafSha256 $probeZip
    if ($loaderHash -cne $script:ExpectedLoaderZipSha256) { throw "Bingus loader ZIP 指纹不匹配。" }
    if ($probeHash -cne $script:ExpectedProbeZipSha256) { throw "Chat probe ZIP 指纹不匹配。" }

    $definitions = @(
        [pscustomobject]@{
            Role = "probe"
            ZipPath = $probeZip
            ZipSha256 = $probeHash
            Entries = @(
                [pscustomobject]@{ Suffix = ""; EntryPath = "Addon/9ba626afa44a3aa3.patch_0"; ExpectedLength = $script:ExpectedProbePatchLength }
                [pscustomobject]@{ Suffix = ".stream"; EntryPath = "Addon/9ba626afa44a3aa3.patch_0.stream"; ExpectedLength = 0L }
                [pscustomobject]@{ Suffix = ".gpu_resources"; EntryPath = "Addon/9ba626afa44a3aa3.patch_0.gpu_resources"; ExpectedLength = 0L }
            )
        }
        [pscustomobject]@{
            Role = "loader"
            ZipPath = $loaderZip
            ZipSha256 = $loaderHash
            Entries = @(
                [pscustomobject]@{ Suffix = ""; EntryPath = "data/9ba626afa44a3aa3.patch_0"; ExpectedLength = 20816L }
                [pscustomobject]@{ Suffix = ".stream"; EntryPath = "data/9ba626afa44a3aa3.patch_0.stream"; ExpectedLength = 0L }
                [pscustomobject]@{ Suffix = ".gpu_resources"; EntryPath = "data/9ba626afa44a3aa3.patch_0.gpu_resources"; ExpectedLength = 0L }
            )
        }
    )

    $groups = [ordered]@{}
    foreach ($definition in $definitions) {
        $files = [System.Collections.Generic.List[object]]::new()
        foreach ($entryDefinition in $definition.Entries) {
            $bytes = Read-ZipEntryBytes $definition.ZipPath $entryDefinition.EntryPath
            if ([long]$bytes.LongLength -ne $entryDefinition.ExpectedLength) {
                throw "来源条目大小不符合固定包：$($entryDefinition.EntryPath)"
            }
            $digest = [System.Security.Cryptography.SHA256]::Create()
            try {
                $entryHash = [BitConverter]::ToString($digest.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
            }
            finally {
                $digest.Dispose()
            }
            [void]$files.Add([pscustomobject]@{
                Suffix = $entryDefinition.Suffix
                EntryPath = $entryDefinition.EntryPath
                Bytes = $bytes
                Sha256 = $entryHash
            })
        }
        $groups[$definition.Role] = [pscustomobject]@{
            Role = $definition.Role
            ZipPath = (ConvertTo-NormalizedPath $definition.ZipPath)
            ZipSha256 = $definition.ZipSha256
            Files = @($files.ToArray())
        }
    }
    return $groups
}

function Get-PatchFiles([string]$DataPath) {
    $items = [System.Collections.Generic.List[object]]::new()
    foreach ($file in [System.IO.Directory]::EnumerateFiles($DataPath)) {
        $leaf = [System.IO.Path]::GetFileName($file)
        if ($leaf -match '^9ba626afa44a3aa3\.patch_(\d+)$') {
            $slot = [long]0
            if (-not [long]::TryParse($Matches[1], [ref]$slot) -or $slot -lt 0) {
                throw "发现无法安全解析的 patch 槽位：$leaf"
            }
            $attributes = [System.IO.File]::GetAttributes($file)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "patch 文件是重解析点，已停止：$file"
            }
            [void]$items.Add([pscustomobject]@{
                Slot = $slot
                Path = (ConvertTo-NormalizedPath $file)
            })
        }
    }
    return $items.ToArray()
}

function Test-PatchArchiveForResource([string]$Path, [System.Collections.Generic.HashSet[UInt64]]$ResourceIds) {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $reader = [System.IO.BinaryReader]::new($stream)
    try {
        if ($stream.Length -lt $script:ArchiveHeaderBytes) {
            return [pscustomobject]@{ Recognized = $false; ContainsResource = $false; Inspectable = $true }
        }
        $magic = $reader.ReadUInt32()
        if ($magic -ne $script:ArchiveMagic) {
            return [pscustomobject]@{ Recognized = $false; ContainsResource = $false; Inspectable = $true }
        }
        $typeCount = [long]$reader.ReadUInt32()
        $entryCount = [long]$reader.ReadUInt32()
        $entryStart = 72L + 32L * $typeCount
        $tableEnd = $entryStart + $script:ArchiveEntryBytes * $entryCount
        if ($typeCount -lt 0 -or $typeCount -gt 1000000 -or
            $entryCount -lt 0 -or $entryCount -gt 1000000 -or
            $entryStart -lt 72 -or $tableEnd -lt $entryStart -or $tableEnd -gt $stream.Length) {
            return [pscustomobject]@{ Recognized = $true; ContainsResource = $false; Inspectable = $false }
        }
        for ($index = 0L; $index -lt $entryCount; $index++) {
            $stream.Position = $entryStart + $script:ArchiveEntryBytes * $index
            $resourceId = $reader.ReadUInt64()
            if ($ResourceIds.Contains($resourceId)) {
                return [pscustomobject]@{ Recognized = $true; ContainsResource = $true; Inspectable = $true }
            }
        }
        return [pscustomobject]@{ Recognized = $true; ContainsResource = $false; Inspectable = $true }
    }
    finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Find-UnreceiptedResources($Payloads, [string]$DataPath) {
    $ids = [System.Collections.Generic.HashSet[UInt64]]::new()
    [void]$ids.Add($script:LoaderResourceId)
    [void]$ids.Add($script:ProbeResourceId)
    $probePatch = $Payloads.probe.Files | Where-Object { $_.Suffix -ceq "" } | Select-Object -First 1
    $loaderPatch = $Payloads.loader.Files | Where-Object { $_.Suffix -ceq "" } | Select-Object -First 1
    $knownHashes = @($probePatch.Sha256, $loaderPatch.Sha256)
    $found = [System.Collections.Generic.List[string]]::new()
    $uninspectable = [System.Collections.Generic.List[string]]::new()
    foreach ($patch in @(Get-PatchFiles $DataPath)) {
        $item = Get-Item -LiteralPath $patch.Path
        if ([long]$item.Length -eq [long]$probePatch.Bytes.LongLength -or [long]$item.Length -eq [long]$loaderPatch.Bytes.LongLength) {
            $digest = Get-LeafSha256 $patch.Path
            if ($knownHashes -ccontains $digest) {
                [void]$found.Add($patch.Path)
                continue
            }
        }
        $inspection = Test-PatchArchiveForResource $patch.Path $ids
        if ($inspection.ContainsResource) { [void]$found.Add($patch.Path) }
        elseif (-not $inspection.Inspectable) { [void]$uninspectable.Add($patch.Path) }
    }
    return [pscustomobject]@{
        Found = @($found.ToArray())
        Uninspectable = @($uninspectable.ToArray())
    }
}

function Get-ReceiptLocation([switch]$CreateDirectory) {
    $localPath = Join-Path $script:ProjectRoot ".local"
    if (Test-Path -LiteralPath $localPath) {
        if (-not (Test-Path -LiteralPath $localPath -PathType Container)) { throw ".local 路径不是目录。" }
        Assert-NotReparsePoint $localPath ".local 目录"
    }
    elseif ($CreateDirectory) {
        [void][System.IO.Directory]::CreateDirectory($localPath)
    }
    return [pscustomobject]@{
        Directory = (ConvertTo-NormalizedPath $localPath)
        Receipt = (ConvertTo-NormalizedPath $script:ReceiptPath)
        Partial = (ConvertTo-NormalizedPath $script:ReceiptPartialPath)
    }
}

function Read-ReceiptData($ReceiptLocation) {
    if (Test-Path -LiteralPath $ReceiptLocation.Receipt -PathType Container) { throw "部署收据路径是目录。" }
    if (-not (Test-Path -LiteralPath $ReceiptLocation.Receipt -PathType Leaf)) { return $null }
    $item = Get-Item -LiteralPath $ReceiptLocation.Receipt
    if ([long]$item.Length -lt 2 -or [long]$item.Length -gt 128KB) { throw "部署收据大小不合法。" }
    try {
        $json = [System.IO.File]::ReadAllText($ReceiptLocation.Receipt, [System.Text.Encoding]::UTF8)
        return ConvertFrom-Json -InputObject $json -AsHashtable -ErrorAction Stop
    }
    catch {
        throw "部署收据 JSON 无效。"
    }
}

function Get-VerifiedReceiptPlan($Receipt, $GameContext, $Payloads) {
    if ($Receipt -isnot [System.Collections.IDictionary] -or $Receipt.schemaVersion -ne 1) {
        throw "部署收据 schema 无效。"
    }
    if (-not (Test-SamePath ([string]$Receipt.gameData) $GameContext.Data)) {
        throw "收据记录的游戏 data 路径与本次显式路径不同。"
    }
    if ($Receipt.sourceZips -isnot [System.Collections.IDictionary]) { throw "收据缺少来源 ZIP 指纹。" }
    foreach ($role in @("probe", "loader")) {
        $recordedSource = $Receipt.sourceZips[$role]
        $currentSource = $Payloads[$role]
        if (($recordedSource -isnot [System.Collections.IDictionary]) -or
            (-not (Test-SamePath ([string]$recordedSource.path) $currentSource.ZipPath)) -or
            (([string]$recordedSource.sha256).ToLowerInvariant() -cne $currentSource.ZipSha256)) {
            throw "收据中的 $role 来源 ZIP 指纹不匹配。"
        }
    }
    $deployments = @($Receipt.deployments)
    if ($deployments.Count -ne 2) { throw "收据必须记录 probe 与 loader 两组文件。" }
    $byRole = @{}
    foreach ($deployment in $deployments) {
        if ($deployment -isnot [System.Collections.IDictionary]) { throw "收据部署组格式无效。" }
        $role = [string]$deployment.role
        if ($role -notin @("probe", "loader") -or $byRole.ContainsKey($role)) { throw "收据部署角色无效或重复。" }
        if ($deployment.slot -isnot [int] -and $deployment.slot -isnot [long]) { throw "收据槽位类型无效。" }
        $slot = [long]$deployment.slot
        if ($slot -lt 0 -or $slot -ge [long]::MaxValue) { throw "收据槽位越界。" }
        $byRole[$role] = $deployment
    }
    if (-not $byRole.ContainsKey("probe") -or -not $byRole.ContainsKey("loader")) { throw "收据缺少部署角色。" }
    $probeSlot = [long]$byRole.probe.slot
    $loaderSlot = [long]$byRole.loader.slot
    if ($loaderSlot -ne $probeSlot + 1) { throw "收据槽位顺序无效。" }

    $plan = [System.Collections.Generic.List[object]]::new()
    foreach ($role in @("probe", "loader")) {
        $deployment = $byRole[$role]
        $slot = [long]$deployment.slot
        $baseName = "9ba626afa44a3aa3.patch_$slot"
        if ([string]$deployment.baseName -cne $baseName) { throw "收据 patch 文件名与槽位不符。" }
        $fileRecords = @($deployment.files)
        if ($fileRecords.Count -ne 3) { throw "收据 $role 文件数不为三。" }
        $group = $Payloads[$role]
        foreach ($sourceFile in $group.Files) {
            $matches = @($fileRecords | Where-Object { [string]$_.suffix -ceq [string]$sourceFile.Suffix })
            if ($matches.Count -ne 1) { throw "收据 $role 缺少唯一 $($sourceFile.Suffix) 文件记录。" }
            $record = $matches[0]
            $destination = ConvertTo-NormalizedPath (Join-Path $GameContext.Data ($baseName + $sourceFile.Suffix))
            if (-not (Test-SamePath ([string]$record.path) $destination)) {
                throw "收据目标路径不在指定 data 槽位中。"
            }
            $recordedHash = ([string]$record.sha256).ToLowerInvariant()
            if ($recordedHash -notmatch '^[0-9a-f]{64}$' -or $recordedHash -cne $sourceFile.Sha256) {
                throw "收据 $role 文件摘要与固定来源条目不匹配。"
            }
            [void]$plan.Add([pscustomobject]@{
                Role = $role
                Slot = $slot
                Path = $destination
                Sha256 = $sourceFile.Sha256
                Suffix = $sourceFile.Suffix
            })
        }
    }
    return ,$plan.ToArray()
}

function Assert-ReceiptFilesMatch($Plan) {
    foreach ($file in $Plan) {
        if (-not (Test-Path -LiteralPath $file.Path -PathType Leaf)) { throw "收据拥有文件缺失，未做任何删除：$($file.Path)" }
        $attributes = [System.IO.File]::GetAttributes($file.Path)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "收据文件是重解析点，未做任何删除。" }
        if ((Get-LeafSha256 $file.Path) -cne $file.Sha256) { throw "收据拥有文件摘要不匹配，未做任何删除：$($file.Path)" }
    }
}

function New-InstallPlan($GameContext, $Payloads, [long]$ProbeSlot, [long]$LoaderSlot) {
    $plan = [System.Collections.Generic.List[object]]::new()
    foreach ($role in @("probe", "loader")) {
        $slot = if ($role -ceq "probe") { $ProbeSlot } else { $LoaderSlot }
        $baseName = "9ba626afa44a3aa3.patch_$slot"
        foreach ($sourceFile in $Payloads[$role].Files) {
            $destination = ConvertTo-NormalizedPath (Join-Path $GameContext.Data ($baseName + $sourceFile.Suffix))
            if (Test-Path -LiteralPath $destination) { throw "目标已存在，不覆盖任何 mod 文件：$destination" }
            [void]$plan.Add([pscustomobject]@{
                Role = $role
                Slot = $slot
                BaseName = $baseName
                Path = $destination
                Sha256 = $sourceFile.Sha256
                Suffix = $sourceFile.Suffix
                Bytes = $sourceFile.Bytes
                ZipPath = $Payloads[$role].ZipPath
                EntryPath = $sourceFile.EntryPath
            })
        }
    }
    return ,$plan.ToArray()
}

function Show-InstallPlan($Plan, [switch]$AlreadyPresent) {
    if ($AlreadyPresent) { Write-Host "已部署且六个文件摘要均匹配；不会创建重复文件。"; return }
    if ($DryRun) { Write-Host "Dry-run：以下为计划，不会写入游戏目录或收据。" }
    foreach ($file in $Plan) {
        Write-Host ("{0} slot {1}: {2} <- {3} [{4}]" -f $file.Role, $file.Slot, $file.Path, $file.ZipPath, $file.EntryPath)
    }
    Write-Host "probe 位于 loader 前一槽；loader 使用最高槽位以保留其优先级。"
}

function Write-NewOwnedFile($File, [System.Collections.Generic.List[object]]$Created) {
    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $File.Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        [void]$Created.Add([pscustomobject]@{ Path = $File.Path; Sha256 = $File.Sha256 })
        if ($File.Bytes.Length -gt 0) { $stream.Write($File.Bytes, 0, $File.Bytes.Length) }
        $stream.Flush($true)
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
    if ((Get-LeafSha256 $File.Path) -cne $File.Sha256) { throw "新建文件摘要不符：$($File.Path)" }
}

function Remove-CreatedFilesIfUnchanged([System.Collections.Generic.List[object]]$Created) {
    for ($index = $Created.Count - 1; $index -ge 0; $index--) {
        $file = $Created[$index]
        if (-not (Test-Path -LiteralPath $file.Path -PathType Leaf)) { continue }
        try {
            if ((Get-LeafSha256 $file.Path) -ceq $file.Sha256) {
                [System.IO.File]::Delete($file.Path)
            }
            else {
                Write-Warning "失败清理时发现文件内容已变，保留该文件：$($file.Path)"
            }
        }
        catch {
            Write-Warning "无法安全清理本次创建的文件，已保留：$($file.Path)"
        }
    }
}

function Get-ReceiptObject($GameContext, $Payloads, $Plan) {
    $deployments = [System.Collections.Generic.List[object]]::new()
    foreach ($role in @("probe", "loader")) {
        $roleFiles = @($Plan | Where-Object { $_.Role -ceq $role })
        if ($roleFiles.Count -ne 3) { throw "内部部署计划的 $role 文件数不正确。" }
        $files = @(
            foreach ($file in $roleFiles) {
                [ordered]@{ suffix = $file.Suffix; path = $file.Path; sha256 = $file.Sha256 }
            }
        )
        [void]$deployments.Add([ordered]@{
            role = $role
            slot = [long]$roleFiles[0].Slot
            baseName = $roleFiles[0].BaseName
            files = $files
        })
    }
    return [ordered]@{
        schemaVersion = 1
        createdUtc = [DateTimeOffset]::UtcNow.ToString("o")
        gameData = $GameContext.Data
        sourceZips = [ordered]@{
            probe = [ordered]@{ path = $Payloads.probe.ZipPath; sha256 = $Payloads.probe.ZipSha256 }
            loader = [ordered]@{ path = $Payloads.loader.ZipPath; sha256 = $Payloads.loader.ZipSha256 }
        }
        deployments = @($deployments.ToArray())
    }
}

function Write-ReceiptAtomically($ReceiptLocation, $ReceiptObject) {
    if (Test-Path -LiteralPath $ReceiptLocation.Receipt) { throw "部署收据已存在，拒绝覆盖。" }
    if (Test-Path -LiteralPath $ReceiptLocation.Partial) { throw "收据 partial 已存在，拒绝覆盖：$($ReceiptLocation.Partial)" }
    if (-not (Test-Path -LiteralPath $ReceiptLocation.Directory -PathType Container)) {
        [void][System.IO.Directory]::CreateDirectory($ReceiptLocation.Directory)
    }
    $encoding = [System.Text.UTF8Encoding]::new($false)
    $bytes = $encoding.GetBytes(($ReceiptObject | ConvertTo-Json -Depth 8))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $expectedHash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
    $script:ReceiptPartialOwned = [pscustomobject]@{ Path = $ReceiptLocation.Partial; Sha256 = $expectedHash }
    $stream = [System.IO.File]::Open(
        $ReceiptLocation.Partial,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    if ((Get-LeafSha256 $ReceiptLocation.Partial) -cne $expectedHash) { throw "收据 partial 摘要不符。" }
    [System.IO.File]::Move($ReceiptLocation.Partial, $ReceiptLocation.Receipt)
    $script:ReceiptPartialOwned = $null
}

function Remove-ReceiptPartialIfUnchanged {
    $partial = $script:ReceiptPartialOwned
    if ($null -eq $partial -or -not (Test-Path -LiteralPath $partial.Path -PathType Leaf)) { return }
    try {
        if ((Get-LeafSha256 $partial.Path) -ceq $partial.Sha256) {
            [System.IO.File]::Delete($partial.Path)
        }
        else {
            Write-Warning "收据 partial 已变化，保留：$($partial.Path)"
        }
    }
    catch {
        Write-Warning "无法安全清理收据 partial，已保留：$($partial.Path)"
    }
}

function Invoke-Install($GameContext, $Payloads, $Plan, $ReceiptLocation) {
    $created = [System.Collections.Generic.List[object]]::new()
    $script:ReceiptPartialOwned = $null
    try {
        foreach ($file in $Plan) { Write-NewOwnedFile $file $created }
        $receipt = Get-ReceiptObject $GameContext $Payloads $Plan
        Write-ReceiptAtomically $ReceiptLocation $receipt
        Write-Host "部署完成。收据：$($ReceiptLocation.Receipt)"
    }
    catch {
        Remove-ReceiptPartialIfUnchanged
        Remove-CreatedFilesIfUnchanged $created
        throw
    }
}

function Invoke-Rollback($Plan, $ReceiptLocation) {
    Assert-ReceiptFilesMatch $Plan
    if ($DryRun) {
        Write-Host "Dry-run rollback：以下六个文件与收据、固定来源条目摘要均匹配，不会删除文件。"
        foreach ($file in $Plan) { Write-Host ("删除计划 slot {0}: {1}" -f $file.Slot, $file.Path) }
        Write-Host "收据：$($ReceiptLocation.Receipt)"
        return
    }
    Assert-GameStopped
    # 删除前再次整组复核，避免只回滚组内一部分已被修改的文件。
    Assert-ReceiptFilesMatch $Plan
    foreach ($file in $Plan) { [System.IO.File]::Delete($file.Path) }
    [System.IO.File]::Delete($ReceiptLocation.Receipt)
    Write-Host "回滚完成；只删除了收据校验通过的六个文件和固定收据。"
}

try {
    $payloads = Get-VerifiedPayloads
    $game = Resolve-GameContext $GameDirectory
    $receiptLocation = Get-ReceiptLocation

    if ($Rollback) {
        if (Test-Path -LiteralPath $receiptLocation.Partial) { throw "发现未完成收据 partial，需人工检查后再回滚。" }
        $receipt = Read-ReceiptData $receiptLocation
        if ($null -eq $receipt) { throw "没有可回滚的部署收据：$($receiptLocation.Receipt)" }
        $rollbackPlan = Get-VerifiedReceiptPlan $receipt $game $payloads
        Invoke-Rollback $rollbackPlan $receiptLocation
        return
    }

    if (-not $DryRun) { Assert-GameStopped }
    if (Test-Path -LiteralPath $receiptLocation.Partial) { throw "发现未完成收据 partial，拒绝再次部署：$($receiptLocation.Partial)" }
    $receipt = Read-ReceiptData $receiptLocation
    if ($null -ne $receipt) {
        $ownedPlan = Get-VerifiedReceiptPlan $receipt $game $payloads
        Assert-ReceiptFilesMatch $ownedPlan
        Show-InstallPlan $ownedPlan -AlreadyPresent
        return
    }

    $existingScan = Find-UnreceiptedResources $payloads $game.Data
    if ($existingScan.Found.Count -gt 0) {
        throw "发现无收据的 loader/probe 资源，拒绝重复部署：$($existingScan.Found -join ', ')"
    }
    if ($existingScan.Uninspectable.Count -gt 0 -and -not $DryRun) {
        throw "部分现有 patch 无法安全读取 TOC，拒绝部署：$($existingScan.Uninspectable -join ', ')"
    }

    $patches = @(Get-PatchFiles $game.Data)
    $highest = -1L
    foreach ($patch in $patches) { if ($patch.Slot -gt $highest) { $highest = $patch.Slot } }
    if ($highest -ge ([long]::MaxValue - 2)) { throw "没有可用的下一个 patch 槽位。" }
    $probeSlot = $highest + 1
    $loaderSlot = $highest + 2
    $installPlan = New-InstallPlan $game $payloads $probeSlot $loaderSlot
    Show-InstallPlan $installPlan
    if ($existingScan.Uninspectable.Count -gt 0) {
        Write-Warning "Dry-run 计划可见，但实际部署会因无法检查上述 TOC 而拒绝。"
    }
    if ($DryRun) { return }
    Invoke-Install $game $payloads $installPlan $receiptLocation
}
catch {
    Write-Host ("已停止：{0}" -f $_.Exception.Message)
    exit 1
}
