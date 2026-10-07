function Invoke-NativeCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [string]$FilePath,

        [Parameter(Position = 1)]
        [string[]]$ArgumentList = @()
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $nativePreferenceAvailable =
        Test-Path variable:PSNativeCommandUseErrorActionPreference
    $exitCode = $null

    if ($nativePreferenceAvailable) {
        $previousNativePreference =
            $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        # native programs routinely write diagnostic information to stderr
        $ErrorActionPreference = "Continue"

        & $FilePath @ArgumentList 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    Write-Output $_.ToString()
                }
                else {
                    Write-Output $_
                }
            }

        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference

        if ($nativePreferenceAvailable) {
            $PSNativeCommandUseErrorActionPreference =
                $previousNativePreference
        }
    }

    if ($exitCode -ne 0) {
        $failure = [System.Exception]::new(
            "$FilePath failed with exit code $exitCode"
        )
        $failure.Data["NativeFilePath"] = $FilePath
        $failure.Data["NativeExitCode"] = $exitCode
        throw $failure
    }
}

function Invoke-CopyOutput {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [scriptblock]$Command
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Stop"

    $capturedOutput =
        [System.Collections.Generic.List[string]]::new()
    $commandFailure = $null
    $clipboardFailure = $null
    $clipboardSucceeded = $false

    $normalizeItem = {
        param(
            [AllowNull()]
            [object]$Item
        )

        if ($Item -is [System.Management.Automation.ErrorRecord]) {
            Write-Output $Item.ToString()
            return
        }

        if ($null -eq $Item) {
            Write-Output ""
            return
        }

        if ($Item -is [string]) {
            Write-Output $Item
            return
        }

        $Item |
            Out-String -Stream -Width 4096 |
            ForEach-Object {
                Write-Output $_
            }
    }

    try {
        & $Command *>&1 |
            ForEach-Object {
                foreach ($line in @(& $normalizeItem $_)) {
                    $capturedOutput.Add([string]$line)
                    Write-Output $line
                }
            }
    }
    catch {
        $commandFailure = $_
        $errorText = "ERROR: $($_.Exception.Message)"

        $capturedOutput.Add($errorText)
        Write-Output $errorText
    }
    finally {
        try {
            $clipboardText = [string]::Join(
                [Environment]::NewLine,
                $capturedOutput
            )

            Set-Clipboard -Value $clipboardText
            $clipboardSucceeded = $true
        }
        catch {
            $clipboardFailure = $_
            $clipboardText =
                "CLIPBOARD ERROR: $($_.Exception.Message)"

            $capturedOutput.Add($clipboardText)
            Write-Output $clipboardText
        }

        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($clipboardSucceeded) {
        Write-Output "clipboard_finalization=success"
    }
    else {
        Write-Output "clipboard_finalization=failed"
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
}
