[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$helperPath = Join-Path $PSScriptRoot "powershell-helpers.ps1"
$testResults = [System.Collections.Generic.List[object]]::new()
$originalClipboard = $null
$clipboardWasRead = $false

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool]$Condition,

        [Parameter(Mandatory)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Contains {
    param(
        [AllowEmptyString()]
        [string]$Actual,

        [Parameter(Mandatory)]
        [string]$Expected,

        [Parameter(Mandatory)]
        [string]$Message
    )

    if (-not $Actual.Contains($Expected)) {
        throw "$Message Expected to find: $Expected"
    }
}

function Assert-NotContains {
    param(
        [AllowEmptyString()]
        [string]$Actual,

        [Parameter(Mandatory)]
        [string]$Unexpected,

        [Parameter(Mandatory)]
        [string]$Message
    )

    if ($Actual.Contains($Unexpected)) {
        throw "$Message Unexpected value: $Unexpected"
    }
}

function Invoke-AcceptanceCase {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [scriptblock]$Test
    )

    try {
        & $Test
        $testResults.Add(
            [pscustomobject]@{
                Name = $Name
                Result = "PASS"
                Detail = ""
            }
        )
        Write-Output "PASS: $Name"
    }
    catch {
        $testResults.Add(
            [pscustomobject]@{
                Name = $Name
                Result = "FAIL"
                Detail = $_.Exception.Message
            }
        )
        Write-Output "FAIL: $Name"
        Write-Output "  $($_.Exception.Message)"
    }
}

function Invoke-CapturedFailure {
    param(
        [Parameter(Mandatory)]
        [scriptblock]$Command
    )

    $lines = [System.Collections.Generic.List[string]]::new()
    $failure = $null

    try {
        & $Command |
            ForEach-Object {
                $lines.Add([string]$_)
            }
    }
    catch {
        $failure = $_
    }

    [pscustomobject]@{
        Lines = $lines.ToArray()
        Failure = $failure
    }
}


function Invoke-BoundedFallbackFixture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [int]$ExitCode,

        [switch]$SimulateClipboardFailure
    )

    $evidencePath = Join-Path `
        ([System.IO.Path]::GetTempPath()) `
        ("saltbytes-helper-fallback-{0}.log" -f [guid]::NewGuid())
    $commandFailure = $null
    $clipboardFailure = $null
    $previousErrorActionPreference = $ErrorActionPreference
    $nativePreferenceAvailable =
        Test-Path variable:PSNativeCommandUseErrorActionPreference

    Assert-True (
        -not $evidencePath.StartsWith(
            $repoRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) "The fallback evidence file was placed inside the repository."

    New-Item -ItemType File -Path $evidencePath -Force | Out-Null

    if ($nativePreferenceAvailable) {
        $previousNativePreference =
            $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        $ErrorActionPreference = "Continue"

        & "cmd.exe" @(
            "/d",
            "/c",
            "echo fallback-stdout & echo fallback-stderr 1>&2 & exit /b $ExitCode"
        ) 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.ToString()
                }
                else {
                    "$_"
                }
            } |
            Tee-Object -FilePath $evidencePath -Append |
            ForEach-Object {
                Write-Output $_
            }

        $nativeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference

        if ($nativePreferenceAvailable) {
            $PSNativeCommandUseErrorActionPreference =
                $previousNativePreference
        }
    }

    Add-Content -LiteralPath $evidencePath `
        -Value "native_exit_code=$nativeExitCode"
    Add-Content -LiteralPath $evidencePath `
        -Value "read_only_diagnostic=fallback-probe"

    if ($nativeExitCode -ne 0) {
        $commandException = [System.Exception]::new(
            "cmd.exe failed with exit code $nativeExitCode"
        )
        $commandException.Data["NativeExitCode"] = $nativeExitCode
        $commandException.Data["EvidencePath"] = $evidencePath
        $commandFailure = [System.Management.Automation.ErrorRecord]::new(
            $commandException,
            "NativeCommandFailed",
            [System.Management.Automation.ErrorCategory]::NotSpecified,
            $null
        )
    }

    try {
        if ($SimulateClipboardFailure) {
            throw "simulated fallback clipboard failure"
        }

        $evidenceText = Get-Content -LiteralPath $evidencePath -Raw
        Set-Clipboard -Value $evidenceText
        Write-Output "clipboard_finalization=success"
    }
    catch {
        $clipboardFailure = $_
        $clipboardFailure.Exception.Data["EvidencePath"] = $evidencePath
        Write-Output (
            "clipboard_finalization=failed: {0}" -f
            $_.Exception.Message
        )
    }

    if ($null -ne $commandFailure) {
        if ($null -ne $clipboardFailure) {
            $commandFailure.Exception.Data["ClipboardFailure"] =
                $clipboardFailure.Exception.Message
        }

        $PSCmdlet.ThrowTerminatingError($commandFailure)
    }

    if ($null -ne $clipboardFailure) {
        $PSCmdlet.ThrowTerminatingError($clipboardFailure)
    }

    Write-Output "evidence_path=$evidencePath"
}

if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw "Canonical helper file is missing: $helperPath"
}

. $helperPath

foreach ($functionName in @("Invoke-NativeCommand", "Invoke-CopyOutput")) {
    if (-not (Get-Command $functionName -CommandType Function -ErrorAction SilentlyContinue)) {
        throw "Required helper function is unavailable: $functionName"
    }
}

try {
    $originalClipboard = Get-Clipboard -Raw -ErrorAction Stop
    $clipboardWasRead = $true
}
catch {
    Write-Output "clipboard_preservation=unavailable"
}

try {
    Invoke-AcceptanceCase "successful native stdout" {
        $output = @(
            Invoke-CopyOutput {
                Invoke-NativeCommand `
                    -FilePath "cmd.exe" `
                    -ArgumentList @(
                        "/d",
                        "/c",
                        "echo success-line & exit /b 0"
                    )
            }
        )

        $text = $output -join "`n"
        $clipboard = Get-Clipboard -Raw

        Assert-Contains $text "success-line" `
            "Successful stdout was not displayed."
        Assert-Contains $clipboard "success-line" `
            "Successful stdout was not copied."
    }

    Invoke-AcceptanceCase "successful native stderr" {
        $output = @(
            Invoke-CopyOutput {
                Invoke-NativeCommand `
                    -FilePath "cmd.exe" `
                    -ArgumentList @(
                        "/d",
                        "/c",
                        "echo diagnostic-line 1>&2 & exit /b 0"
                    )
            }
        )

        $text = $output -join "`n"
        $clipboard = Get-Clipboard -Raw

        Assert-Contains $text "diagnostic-line" `
            "Exit zero stderr was not displayed."
        Assert-Contains $clipboard "diagnostic-line" `
            "Exit zero stderr was not copied."
    }

    Invoke-AcceptanceCase "failed native stdout and stderr" {
        $result = Invoke-CapturedFailure {
            Invoke-CopyOutput {
                Invoke-NativeCommand `
                    -FilePath "cmd.exe" `
                    -ArgumentList @(
                        "/d",
                        "/c",
                        "echo stdout-line & echo stderr-line 1>&2 & exit /b 7"
                    )
            }
        }

        $text = $result.Lines -join "`n"
        $clipboard = Get-Clipboard -Raw

        Assert-True ($null -ne $result.Failure) `
            "Native exit 7 did not propagate."
        Assert-Contains $text "stdout-line" `
            "Failed stdout was not displayed."
        Assert-Contains $text "stderr-line" `
            "Failed stderr was not displayed."
        Assert-Contains $text "exit code 7" `
            "The exact exit code was not displayed."
        Assert-Contains $clipboard "stdout-line" `
            "Failed stdout was not copied."
        Assert-Contains $clipboard "stderr-line" `
            "Failed stderr was not copied."
        Assert-Contains $clipboard "exit code 7" `
            "The exact exit code was not copied."
        Assert-True (
            $result.Failure.Exception.Data["NativeExitCode"] -eq 7
        ) "The propagated native exit code was not authoritative."
    }

    Invoke-AcceptanceCase "earlier output and dependent stop" {
        $result = Invoke-CapturedFailure {
            Invoke-CopyOutput {
                Write-Output "before-stage"
                Invoke-NativeCommand `
                    -FilePath "cmd.exe" `
                    -ArgumentList @(
                        "/d",
                        "/c",
                        "echo failure-line & exit /b 9"
                    )
                Write-Output "SHOULD-NOT-RUN"
            }
        }

        $text = $result.Lines -join "`n"
        $clipboard = Get-Clipboard -Raw

        Assert-True ($null -ne $result.Failure) `
            "Native exit 9 did not propagate."
        Assert-Contains $text "before-stage" `
            "Earlier output was not preserved."
        Assert-NotContains $text "SHOULD-NOT-RUN" `
            "A dependent command ran after failure."
        Assert-Contains $clipboard "before-stage" `
            "Earlier output was not copied."
        Assert-NotContains $clipboard "SHOULD-NOT-RUN" `
            "Dependent output reached the clipboard."
    }

    Invoke-AcceptanceCase "terminating PowerShell error" {
        $result = Invoke-CapturedFailure {
            Invoke-CopyOutput {
                Write-Output "before-throw"
                throw "boom"
                Write-Output "SHOULD-NOT-RUN"
            }
        }

        $text = $result.Lines -join "`n"
        $clipboard = Get-Clipboard -Raw

        Assert-True ($null -ne $result.Failure) `
            "The terminating PowerShell error did not propagate."
        Assert-Contains $text "before-throw" `
            "Output before the terminating error was lost."
        Assert-Contains $text "ERROR: boom" `
            "The terminating error was not normalized."
        Assert-NotContains $text "SHOULD-NOT-RUN" `
            "A dependent command ran after a terminating error."
        Assert-Contains $clipboard "ERROR: boom" `
            "The terminating error was not copied."
    }

    Invoke-AcceptanceCase "long running streamed output" {
        $powerShellPath = (Get-Process -Id $PID).Path
        $observations = [System.Collections.Generic.List[object]]::new()
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

        Invoke-CopyOutput {
            Invoke-NativeCommand `
                -FilePath $powerShellPath `
                -ArgumentList @(
                    "-NoProfile",
                    "-Command",
                    "[Console]::Out.WriteLine('stream-first'); [Console]::Out.Flush(); Start-Sleep -Seconds 2; [Console]::Out.WriteLine('stream-second'); [Console]::Out.Flush()"
                )
        } |
            ForEach-Object {
                $observations.Add(
                    [pscustomobject]@{
                        Line = [string]$_
                        Milliseconds = $stopwatch.ElapsedMilliseconds
                    }
                )
            }

        $first = $observations |
            Where-Object { $_.Line -eq "stream-first" } |
            Select-Object -First 1
        $second = $observations |
            Where-Object { $_.Line -eq "stream-second" } |
            Select-Object -First 1
        $clipboard = Get-Clipboard -Raw
        $clipboardFirst = $clipboard.IndexOf("stream-first")
        $clipboardSecond = $clipboard.IndexOf("stream-second")

        Assert-True ($null -ne $first) `
            "The first streamed line was not observed."
        Assert-True ($null -ne $second) `
            "The second streamed line was not observed."
        Assert-True ($first.Milliseconds -lt 1500) `
            "The first line was buffered until command completion."
        Assert-True ($second.Milliseconds -ge 1500) `
            "The timing fixture did not exercise delayed output."
        Assert-True ($clipboardFirst -ge 0) `
            "The first streamed line was not copied."
        Assert-True ($clipboardSecond -gt $clipboardFirst) `
            "The delayed streamed lines were not copied in order."
    }

    Invoke-AcceptanceCase "safe diagnostic follow up" {
        $originalFailure = $null
        $diagnosticLines = @()
        $finalFailure = $null

        try {
            Invoke-CopyOutput {
                Invoke-NativeCommand `
                    -FilePath "cmd.exe" `
                    -ArgumentList @(
                        "/d",
                        "/c",
                        "echo original-failure & exit /b 5"
                    )
            } | Out-Null
        }
        catch {
            $originalFailure = $_
        }

        Assert-True ($null -ne $originalFailure) `
            "The original native failure was not captured."

        try {
            $diagnosticLines = @(
                Invoke-CopyOutput {
                    Write-Output "read-only-diagnostic"
                }
            )

            throw $originalFailure
        }
        catch {
            $finalFailure = $_
        }

        Assert-Contains ($diagnosticLines -join "`n") `
            "read-only-diagnostic" `
            "The permitted diagnostic was not collected."
        Assert-Contains $finalFailure.Exception.Message "exit code 5" `
            "The original native failure was not authoritative after diagnostics."
    }

    Invoke-AcceptanceCase "clipboard failure after command success" {
        function Set-Clipboard {
            throw "simulated clipboard failure"
        }

        try {
            $result = Invoke-CapturedFailure {
                Invoke-CopyOutput {
                    Write-Output "command-succeeded"
                }
            }
        }
        finally {
            Remove-Item Function:\Set-Clipboard -Force
        }

        $text = $result.Lines -join "`n"

        Assert-True ($null -ne $result.Failure) `
            "Clipboard failure did not fail the evidence package."
        Assert-Contains $text "CLIPBOARD ERROR" `
            "Clipboard failure was not reported."
        Assert-Contains $text "clipboard_finalization=failed" `
            "Clipboard finalization status was not reported."
        Assert-Contains $result.Failure.Exception.Message `
            "simulated clipboard failure" `
            "Clipboard failure was not propagated after command success."
    }

    Invoke-AcceptanceCase "command and clipboard failure precedence" {
        function Set-Clipboard {
            throw "simulated clipboard failure"
        }

        try {
            $result = Invoke-CapturedFailure {
                Invoke-CopyOutput {
                    Invoke-NativeCommand `
                        -FilePath "cmd.exe" `
                        -ArgumentList @(
                            "/d",
                            "/c",
                            "echo original-diagnostic & exit /b 13"
                        )
                }
            }
        }
        finally {
            Remove-Item Function:\Set-Clipboard -Force
        }

        $text = $result.Lines -join "`n"

        Assert-True ($null -ne $result.Failure) `
            "The combined failure did not propagate."
        Assert-Contains $text "original-diagnostic" `
            "Original command diagnostics were obscured."
        Assert-Contains $text "CLIPBOARD ERROR" `
            "Clipboard failure was not reported."
        Assert-Contains $result.Failure.Exception.Message "exit code 13" `
            "Clipboard failure replaced the command failure."
        Assert-True (
            $result.Failure.Exception.Data["NativeExitCode"] -eq 13
        ) "The native exit code was not authoritative."
        Assert-Contains (
            [string]$result.Failure.Exception.Data["ClipboardFailure"]
        ) "simulated clipboard failure" `
            "The clipboard failure was not preserved with the command failure."
    }

    Invoke-AcceptanceCase "canonical helper reload" {
        function Invoke-NativeCommand {
            throw "stale helper definition"
        }

        . $helperPath

        $output = @(
            Invoke-NativeCommand `
                -FilePath "cmd.exe" `
                -ArgumentList @(
                    "/d",
                    "/c",
                    "echo canonical-reload & exit /b 0"
                )
        )

        Assert-Contains ($output -join "`n") "canonical-reload" `
            "Dot sourcing did not replace the stale helper."
    }

    Invoke-AcceptanceCase "mixed PowerShell and native output" {
        $output = @(
            Invoke-CopyOutput {
                Write-Output "powershell-before"
                Invoke-NativeCommand `
                    -FilePath "cmd.exe" `
                    -ArgumentList @(
                        "/d",
                        "/c",
                        "echo native-stdout & echo native-stderr 1>&2 & exit /b 0"
                    )

                $previousPreference = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                Write-Error "nonterminating-error"
                $ErrorActionPreference = $previousPreference

                Write-Output "powershell-after"
            }
        )

        $text = $output -join "`n"
        $positions = @(
            $text.IndexOf("powershell-before")
            $text.IndexOf("native-stdout")
            $text.IndexOf("native-stderr")
            $text.IndexOf("nonterminating-error")
            $text.IndexOf("powershell-after")
        )

        foreach ($position in $positions) {
            Assert-True ($position -ge 0) `
                "A mixed output item was not preserved."
        }

        for ($index = 1; $index -lt $positions.Count; $index++) {
            Assert-True ($positions[$index] -gt $positions[$index - 1]) `
                "Mixed output order was not preserved."
        }

        $clipboard = Get-Clipboard -Raw
        $clipboardPositions = @(
            $clipboard.IndexOf("powershell-before")
            $clipboard.IndexOf("native-stdout")
            $clipboard.IndexOf("native-stderr")
            $clipboard.IndexOf("nonterminating-error")
            $clipboard.IndexOf("powershell-after")
        )

        foreach ($position in $clipboardPositions) {
            Assert-True ($position -ge 0) `
                "A mixed output item was not copied."
        }

        for ($index = 1; $index -lt $clipboardPositions.Count; $index++) {
            Assert-True (
                $clipboardPositions[$index] -gt
                    $clipboardPositions[$index - 1]
            ) "Mixed clipboard output order was not preserved."
        }

        Assert-NotContains $text "NativeCommandError" `
            "Verbose native error metadata became the primary result."
        Assert-NotContains $clipboard "NativeCommandError" `
            "Verbose native error metadata reached the clipboard."
    }

    Invoke-AcceptanceCase "bounded Tee-Object fallback" {
        $result = Invoke-CapturedFailure {
            Invoke-BoundedFallbackFixture -ExitCode 17
        }

        $text = $result.Lines -join "`n"
        $evidencePath =
            [string]$result.Failure.Exception.Data["EvidencePath"]
        $logText = Get-Content -LiteralPath $evidencePath -Raw
        $clipboard = Get-Clipboard -Raw

        Assert-True ($null -ne $result.Failure) `
            "The fallback did not propagate native failure."
        Assert-Contains $text "fallback-stdout" `
            "Fallback stdout was not streamed."
        Assert-Contains $text "fallback-stderr" `
            "Fallback stderr was not streamed."
        Assert-Contains $text "clipboard_finalization=success" `
            "Fallback clipboard success was not reported."
        Assert-Contains $logText "fallback-stdout" `
            "Fallback stdout was not preserved."
        Assert-Contains $logText "fallback-stderr" `
            "Fallback stderr was not preserved."
        Assert-Contains $logText "native_exit_code=17" `
            "Fallback exit code was not recorded."
        Assert-Contains $logText "read_only_diagnostic=fallback-probe" `
            "Fallback read only diagnostics were not appended."
        Assert-Contains $clipboard "fallback-stdout" `
            "The complete fallback evidence file was not copied."
        Assert-True (
            $result.Failure.Exception.Data["NativeExitCode"] -eq 17
        ) "Fallback native failure was not authoritative."

        Remove-Item -LiteralPath $evidencePath -Force
        Assert-True (-not (Test-Path -LiteralPath $evidencePath)) `
            "Fallback evidence was not removable after diagnosis."
    }

    Invoke-AcceptanceCase "fallback clipboard failure after command success" {
        $result = Invoke-CapturedFailure {
            Invoke-BoundedFallbackFixture `
                -ExitCode 0 `
                -SimulateClipboardFailure
        }

        $text = $result.Lines -join "`n"
        $evidencePath =
            [string]$result.Failure.Exception.Data["EvidencePath"]

        Assert-True ($null -ne $result.Failure) `
            "Fallback clipboard failure did not fail the package."
        Assert-Contains $text "clipboard_finalization=failed" `
            "Fallback clipboard failure status was not reported."
        Assert-Contains $result.Failure.Exception.Message `
            "simulated fallback clipboard failure" `
            "Fallback clipboard failure was not propagated."
        Assert-True (Test-Path -LiteralPath $evidencePath) `
            "Fallback evidence was not retained for diagnosis."

        Remove-Item -LiteralPath $evidencePath -Force
    }

    Invoke-AcceptanceCase "fallback command and clipboard precedence" {
        $result = Invoke-CapturedFailure {
            Invoke-BoundedFallbackFixture `
                -ExitCode 19 `
                -SimulateClipboardFailure
        }

        $text = $result.Lines -join "`n"
        $evidencePath =
            [string]$result.Failure.Exception.Data["EvidencePath"]

        Assert-True ($null -ne $result.Failure) `
            "The combined fallback failure did not propagate."
        Assert-Contains $text "fallback-stdout" `
            "Fallback command diagnostics were obscured."
        Assert-Contains $text "clipboard_finalization=failed" `
            "Fallback clipboard failure was not reported."
        Assert-Contains $result.Failure.Exception.Message "exit code 19" `
            "Fallback clipboard failure replaced the command failure."
        Assert-True (
            $result.Failure.Exception.Data["NativeExitCode"] -eq 19
        ) "Fallback native exit code was not authoritative."
        Assert-Contains (
            [string]$result.Failure.Exception.Data["ClipboardFailure"]
        ) "simulated fallback clipboard failure" `
            "Fallback clipboard failure was not preserved."
        Assert-True (Test-Path -LiteralPath $evidencePath) `
            "Fallback evidence was not retained for diagnosis."

        Remove-Item -LiteralPath $evidencePath -Force
    }

    Invoke-AcceptanceCase "Start-Transcript restriction" {
        $helperText = Get-Content -LiteralPath $helperPath -Raw
        $testText = Get-Content -LiteralPath $PSCommandPath -Raw
        $invocationPattern = '(?m)^\s*&?\s*Start-Transcript\b'

        Assert-True (
            -not [regex]::IsMatch($helperText, $invocationPattern)
        ) "The canonical helpers invoke Start-Transcript."
        Assert-True (
            -not [regex]::IsMatch($testText, $invocationPattern)
        ) "The acceptance script invokes Start-Transcript."
    }
}
finally {
    if ($clipboardWasRead) {
        try {
            Set-Clipboard -Value $originalClipboard
            Write-Output "clipboard_restore=success"
        }
        catch {
            Write-Output "clipboard_restore=failed: $($_.Exception.Message)"
        }
    }
}

$failed = @(
    $testResults |
        Where-Object { $_.Result -eq "FAIL" }
)

Write-Output "acceptance_total=$($testResults.Count)"
Write-Output "acceptance_passed=$($testResults.Count - $failed.Count)"
Write-Output "acceptance_failed=$($failed.Count)"

if ($failed.Count -ne 0) {
    foreach ($failure in $failed) {
        Write-Output "failed_case=$($failure.Name): $($failure.Detail)"
    }

    throw "$($failed.Count) PowerShell helper acceptance case(s) failed"
}
