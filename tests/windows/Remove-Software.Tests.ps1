# Runs the removal script on a real Windows (GitHub Actions, windows-latest).
#
# Most tests use fake software: a registry entry plus a tiny "uninstaller" we
# control, so every branch of the script is exercised without depending on a
# vendor. Two tests install and remove the real 7-Zip (MSI, then EXE).
#
# Needs administrator rights (it writes to HKLM) and Pester 5.

BeforeAll {
    $script:Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:Python = Join-Path $script:Repo '.venv\Scripts\python.exe'
    $script:Work = Join-Path $env:TEMP ('iafd-tests-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $script:Work -Force | Out-Null
    $script:Counter = 0
    $script:Created = @()

    $script:Roots = @{
        HKLM64 = @{ Ps = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'; Reg = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall' }
        HKLM32 = @{ Ps = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'; Reg = 'HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall' }
        HKCU   = @{ Ps = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'; Reg = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall' }
    }

    # Renders the script through the real Python renderer and runs it like Intune does.
    function Invoke-Removal {
        param([string]$Mode, [hashtable]$Config, [switch]$From32Bit)
        $script:Counter++
        $configPath = Join-Path $script:Work ("config-$($script:Counter).json")
        $scriptPath = Join-Path $script:Work ("script-$($script:Counter).ps1")
        $Config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
        & $script:Python -m iafd.removal $Mode $configPath $scriptPath
        if ($LASTEXITCODE -ne 0) { throw "rendering failed with exit code $LASTEXITCODE" }
        # Windows PowerShell turns anything a native program writes to stderr into an
        # error record: keep going so the test can show it.
        $ErrorActionPreference = 'Continue'
        $shell = 'powershell.exe'
        # The Intune agent is a 32-bit process: this is how it would start the script.
        if ($From32Bit) { $shell = Join-Path $env:SystemRoot 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe' }
        $output = & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath 2>&1
        return [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Output   = (@($output) -join "`n")
            Path     = $scriptPath
        }
    }

    # A fake program: a registry entry and an uninstaller that removes that entry.
    function New-FakeSoftware {
        param(
            [string]$Name,
            [string]$Version = '1.0',
            [ValidateSet('HKLM64', 'HKLM32', 'HKCU')][string]$Where = 'HKLM64',
            # quiet: registers QuietUninstallString. plain: only UninstallString.
            # stubborn: exits 0 but leaves the entry. slow: never ends in time.
            # late: returns at once and removes the entry a few seconds later.
            [ValidateSet('quiet', 'plain', 'stubborn', 'slow', 'late')][string]$Behaviour = 'quiet'
        )
        $script:Counter++
        $keyName = 'IAFD-Test-' + $script:Counter
        $root = $script:Roots[$Where]
        $folder = Join-Path $script:Work ("fake program $($script:Counter)")   # a space, on purpose
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        $uninstaller = Join-Path $folder 'uninstall.cmd'
        $marker = Join-Path $folder 'installed.txt'
        $arguments = Join-Path $folder 'arguments.txt'
        Set-Content -LiteralPath $marker -Value 'installed'
        $regKey = $root.Reg + '\' + $keyName
        $delete = "reg delete `"$regKey`" /f /reg:64 >nul 2>&1"
        $lines = @('@echo off', "echo %* > `"$arguments`"")
        switch ($Behaviour) {
            'stubborn' { $lines += 'exit /b 0' }
            'slow' { $lines += 'ping -n 60 127.0.0.1 >nul'; $lines += $delete }
            'late' { $lines += "start `"`" /b cmd /c `"ping -n 5 127.0.0.1 >nul & $delete`""; $lines += 'exit /b 0' }
            default { $lines += $delete; $lines += "del `"$marker`""; $lines += 'exit /b 0' }
        }
        Set-Content -LiteralPath $uninstaller -Value $lines -Encoding ASCII

        $psKey = Join-Path $root.Ps $keyName
        New-Item -Path $psKey -Force | Out-Null
        Set-ItemProperty -Path $psKey -Name DisplayName -Value $Name
        Set-ItemProperty -Path $psKey -Name DisplayVersion -Value $Version
        Set-ItemProperty -Path $psKey -Name UninstallString -Value ('"' + $uninstaller + '"')
        Set-ItemProperty -Path $psKey -Name InstallLocation -Value $folder
        if ($Behaviour -eq 'quiet') {
            Set-ItemProperty -Path $psKey -Name QuietUninstallString -Value ('"' + $uninstaller + '" /quiet')
        }
        $script:Created += $psKey
        return [pscustomobject]@{ Key = $psKey; Marker = $marker; Arguments = $arguments; Folder = $folder }
    }

    function Get-InstalledEntry {
        param([string]$Like)
        foreach ($root in @($script:Roots.HKLM64.Ps, $script:Roots.HKLM32.Ps)) {
            foreach ($key in @(Get-ChildItem -Path $root -ErrorAction SilentlyContinue)) {
                $properties = Get-ItemProperty -Path $key.PSPath
                if ($properties.PSObject.Properties['DisplayName'] -and $properties.DisplayName -like $Like) { return $properties }
            }
        }
        return $null
    }
}

AfterAll {
    foreach ($key in $script:Created) { Remove-Item -Path $key -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $script:Work -Recurse -Force -ErrorAction SilentlyContinue
}

Describe 'Detection' {
    It 'finds only the program whose name is exactly the one asked, whatever the case' {
        $wanted = New-FakeSoftware -Name 'IAFD Exact App'
        $other = New-FakeSoftware -Name 'IAFD Exact App Extra'
        $result = Invoke-Removal -Mode Detect -Config @{ names = @('iafd exact app') }
        $result.ExitCode | Should -Be 0
        $result.Output | Should -Be 'Found: IAFD Exact App 1.0'
        Test-Path $wanted.Key | Should -BeTrue
        Test-Path $other.Key | Should -BeTrue
    }

    It 'writes nothing and exits 0 when the program is absent (Intune reads that as "not detected")' {
        $result = Invoke-Removal -Mode Detect -Config @{ names = @('IAFD Nobody Has This') }
        $result.ExitCode | Should -Be 0
        $result.Output | Should -BeNullOrEmpty
    }

    It 'answers present or absent for a requirement rule' {
        New-FakeSoftware -Name 'IAFD Requirement App' | Out-Null
        (Invoke-Removal -Mode Requirement -Config @{ names = @('IAFD Requirement App') }).Output | Should -Be 'present'
        (Invoke-Removal -Mode Requirement -Config @{ names = @('IAFD Nobody Has This') }).Output | Should -Be 'absent'
    }

    It 'exits 1 for a remediation when the program is present, 0 when it is absent' {
        New-FakeSoftware -Name 'IAFD Remediation App' | Out-Null
        (Invoke-Removal -Mode RemediationDetect -Config @{ names = @('IAFD Remediation App') }).ExitCode | Should -Be 1
        (Invoke-Removal -Mode RemediationDetect -Config @{ names = @('IAFD Nobody Has This') }).ExitCode | Should -Be 0
    }

    It 'sees 32-bit programs and per-user programs' {
        New-FakeSoftware -Name 'IAFD Wow App' -Where HKLM32 | Out-Null
        New-FakeSoftware -Name 'IAFD User App' -Where HKCU | Out-Null
        (Invoke-Removal -Mode Detect -Config @{ names = @('IAFD Wow App') }).Output | Should -Match 'IAFD Wow App'
        (Invoke-Removal -Mode Detect -Config @{ names = @('IAFD User App'); kind = 'user' }).Output | Should -Match 'IAFD User App'
        # A machine-wide rule must not look into the user profile, and the reverse.
        (Invoke-Removal -Mode Detect -Config @{ names = @('IAFD User App') }).Output | Should -BeNullOrEmpty
        (Invoke-Removal -Mode Detect -Config @{ names = @('IAFD Wow App'); kind = 'user' }).Output | Should -BeNullOrEmpty
    }

    It 'applies version rules' {
        New-FakeSoftware -Name 'IAFD Versioned App' -Version '1.4.0' | Out-Null
        New-FakeSoftware -Name 'IAFD Versioned App' -Version '3.0' | Out-Null
        $below = Invoke-Removal -Mode Detect -Config @{ names = @('IAFD Versioned App'); versions = @{ mode = 'below'; value = '2.0' } }
        $below.Output | Should -Be 'Found: IAFD Versioned App 1.4.0'
        $listed = Invoke-Removal -Mode Detect -Config @{ names = @('IAFD Versioned App'); versions = @{ mode = 'list'; values = @('3.0') } }
        $listed.Output | Should -Be 'Found: IAFD Versioned App 3.0'
        $none = Invoke-Removal -Mode Detect -Config @{ names = @('IAFD Versioned App'); versions = @{ mode = 'below'; value = '1.0' } }
        $none.Output | Should -BeNullOrEmpty
    }
}

Describe 'Removal' {
    It 'uses the silent command the program declares, and leaves its neighbour alone' {
        $wanted = New-FakeSoftware -Name 'IAFD Quiet App'
        $other = New-FakeSoftware -Name 'IAFD Quiet App Pro'
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Quiet App'); settle_seconds = 10 }
        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match 'IAFD Quiet App 1.0: removed'
        Test-Path $wanted.Key | Should -BeFalse
        Test-Path $wanted.Marker | Should -BeFalse
        (Get-Content -LiteralPath $wanted.Arguments -Raw).Trim() | Should -Be '/quiet'
        Test-Path $other.Key | Should -BeTrue
        Test-Path $other.Marker | Should -BeTrue
    }

    It 'refuses to guess: without a silent method nothing is run and the exit code is 1' {
        $program = New-FakeSoftware -Name 'IAFD Plain App' -Behaviour plain
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Plain App'); settle_seconds = 2 }
        $result.ExitCode | Should -Be 1
        $result.Output | Should -Match 'no silent uninstall method'
        Test-Path $program.Key | Should -BeTrue
        Test-Path $program.Arguments | Should -BeFalse   # the uninstaller was never started
    }

    It 'runs the command given in the tool, with the uninstaller path filled in' {
        $program = New-FakeSoftware -Name 'IAFD Command App' -Behaviour plain
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Command App'); command = '{UninstallString} /S /norestart'; settle_seconds = 10 }
        $result.ExitCode | Should -Be 0
        Test-Path $program.Key | Should -BeFalse
        (Get-Content -LiteralPath $program.Arguments -Raw).Trim() | Should -Be '/S /norestart'
    }

    It 'removes a 32-bit program and a per-user program' {
        $wow = New-FakeSoftware -Name 'IAFD Wow Removal' -Where HKLM32
        $user = New-FakeSoftware -Name 'IAFD User Removal' -Where HKCU
        (Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Wow Removal'); settle_seconds = 10 }).ExitCode | Should -Be 0
        (Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD User Removal'); kind = 'user'; settle_seconds = 10 }).ExitCode | Should -Be 0
        Test-Path $wow.Key | Should -BeFalse
        Test-Path $user.Key | Should -BeFalse
    }

    It 'removes only the versions below the limit' {
        $old = New-FakeSoftware -Name 'IAFD Upgrade App' -Version '19.00'
        $new = New-FakeSoftware -Name 'IAFD Upgrade App' -Version '24.08'
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Upgrade App'); versions = @{ mode = 'below'; value = '23.0' }; settle_seconds = 10 }
        $result.ExitCode | Should -Be 0
        Test-Path $old.Key | Should -BeFalse
        Test-Path $new.Key | Should -BeTrue
    }

    It 'reports a failure when the uninstaller ends but the program is still there' {
        $program = New-FakeSoftware -Name 'IAFD Stubborn App' -Behaviour stubborn
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Stubborn App'); command = '{UninstallString}'; settle_seconds = 3 }
        $result.ExitCode | Should -Be 1
        $result.Output | Should -Match 'still present'
        Test-Path $program.Key | Should -BeTrue
    }

    It 'stops an uninstaller that never ends and reports a failure' {
        $program = New-FakeSoftware -Name 'IAFD Slow App' -Behaviour slow
        $started = Get-Date
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Slow App'); command = '{UninstallString}'; timeout_seconds = 5; settle_seconds = 2 }
        $result.ExitCode | Should -Be 1
        ((Get-Date) - $started).TotalSeconds | Should -BeLessThan 45
        Test-Path $program.Key | Should -BeTrue
    }

    It 'waits for an uninstaller that returns before it has finished' {
        $program = New-FakeSoftware -Name 'IAFD Late App' -Behaviour late
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Late App'); command = '{UninstallString}'; settle_seconds = 40 }
        $result.ExitCode | Should -Be 0
        Test-Path $program.Key | Should -BeFalse
    }

    It 'restarts itself in 64-bit when started from a 32-bit process, as the Intune agent does' {
        $program = New-FakeSoftware -Name 'IAFD Bitness App'
        $log = Join-Path $env:TEMP 'IAFD-Remove-iafd-bitness-app.log'
        $intuneLog = Join-Path $env:ProgramData 'Microsoft\IntuneManagementExtension\Logs\IAFD-Remove-iafd-bitness-app.log'
        Remove-Item -LiteralPath $log, $intuneLog -Force -ErrorAction SilentlyContinue
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Bitness App'); settle_seconds = 10 } -From32Bit
        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match 'IAFD Bitness App 1.0: removed'
        Test-Path $program.Key | Should -BeFalse
        $written = @($log, $intuneLog) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        $written | Should -Not -BeNullOrEmpty
        $lines = Get-Content -LiteralPath $written
        ($lines -join "`n") | Should -Match '64-bit\]'
        ($lines -join "`n") | Should -Not -Match '32-bit\]'
    }

    It 'does nothing and succeeds when the program is not installed' {
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @('IAFD Nobody Has This') }
        $result.ExitCode | Should -Be 0
        $result.Output | Should -Be 'Nothing to remove'
    }

    It 'treats a hostile name as plain text' {
        $marker = Join-Path $script:Work 'hostile.txt'
        $name = "x'; Set-Content -LiteralPath '$marker' -Value pwned; '"
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @($name) }
        $result.ExitCode | Should -Be 0
        Test-Path $marker | Should -BeFalse
    }
}

Describe 'Real software' {
    # Neither program is part of the GitHub runner image. If one is found before the
    # test installs it, the test is skipped rather than removing something it did not install.

    It 'removes a program installed from an MSI, using its product code' {
        if (Get-InstalledEntry -Like 'Everything*') { Set-ItResult -Skipped -Because 'Everything is already installed'; return }
        $msi = Join-Path $script:Work 'everything.msi'
        Invoke-WebRequest -Uri 'https://www.voidtools.com/Everything-1.4.1.1026.x64.msi' -OutFile $msi -UseBasicParsing
        $install = Start-Process -FilePath msiexec.exe -ArgumentList ('/i "' + $msi + '" /qn /norestart') -Wait -PassThru
        $install.ExitCode | Should -Be 0
        $entry = Get-InstalledEntry -Like 'Everything*'
        $entry | Should -Not -BeNullOrEmpty
        $entry.PSChildName | Should -Match '^\{[0-9A-Fa-f-]{36}\}$'   # a real MSI product code

        (Invoke-Removal -Mode Detect -Config @{ names = @($entry.DisplayName) }).Output | Should -Match '^Found: Everything'
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @($entry.DisplayName) }
        $result.ExitCode | Should -BeIn @(0, 3010)
        Get-InstalledEntry -Like 'Everything*' | Should -BeNullOrEmpty
        (Invoke-Removal -Mode Detect -Config @{ names = @($entry.DisplayName) }).Output | Should -BeNullOrEmpty
    }

    It 'removes a program installed from an EXE, with the command given in the tool' {
        if (Get-InstalledEntry -Like 'Notepad++*') { Set-ItResult -Skipped -Because 'Notepad++ is already installed'; return }
        $exe = Join-Path $script:Work 'npp.exe'
        Invoke-WebRequest -Uri 'https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.6.9/npp.8.6.9.Installer.x64.exe' -OutFile $exe -UseBasicParsing
        $install = Start-Process -FilePath $exe -ArgumentList '/S' -Wait -PassThru
        $install.ExitCode | Should -Be 0
        $entry = Get-InstalledEntry -Like 'Notepad++*'
        $entry | Should -Not -BeNullOrEmpty
        # This installer does not record where it installed: use the folder of its uninstaller.
        $program = Join-Path (Split-Path -Parent $entry.UninstallString.Trim('"')) 'notepad++.exe'
        Test-Path $program | Should -BeTrue

        # This installer declares no silent command: the script must refuse to guess...
        $refused = Invoke-Removal -Mode Uninstall -Config @{ names = @($entry.DisplayName); settle_seconds = 2 }
        $refused.ExitCode | Should -Be 1
        Get-InstalledEntry -Like 'Notepad++*' | Should -Not -BeNullOrEmpty

        # ...and remove it once the command is given. Its uninstaller returns before it
        # has finished, which is what the settle wait is for.
        $result = Invoke-Removal -Mode Uninstall -Config @{ names = @($entry.DisplayName); command = '{UninstallString} /S' }
        $result.ExitCode | Should -Be 0
        Get-InstalledEntry -Like 'Notepad++*' | Should -BeNullOrEmpty
        Test-Path $program | Should -BeFalse
    }
}

Describe 'Store apps and helpers' {
    BeforeAll {
        # Load the functions without running anything, with Store settings.
        $configPath = Join-Path $script:Work 'store.json'
        $script:StoreScript = Join-Path $script:Work 'store.ps1'
        @{ names = @('Contoso.Weather'); kind = 'store'; versions = @{ mode = 'below'; value = '5.0' } } |
            ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
        & $script:Python -m iafd.removal Detect $configPath $script:StoreScript
        . $script:StoreScript -Mode Library
    }

    It 'compares versions as numbers and leaves alone what it cannot compare' {
        (ConvertTo-ComparableVersion -Text '9.20') | Should -BeLessThan (ConvertTo-ComparableVersion -Text '23.01')
        (ConvertTo-ComparableVersion -Text '8') | Should -Be ([version]'8.0')
        (ConvertTo-ComparableVersion -Text 'beta') | Should -BeNullOrEmpty
        Test-VersionWanted -Version '4.9.1' | Should -BeTrue
        Test-VersionWanted -Version '5.0' | Should -BeFalse
        Test-VersionWanted -Version 'unknown' | Should -BeFalse
    }

    It 'selects Store packages by exact name and version' {
        Mock Get-AppxPackage {
            @(
                [pscustomobject]@{ Name = 'Contoso.Weather'; Version = '4.2.0.0'; PackageFullName = 'Contoso.Weather_4.2.0.0_x64__abc' },
                [pscustomobject]@{ Name = 'Contoso.Weather'; Version = '6.0.0.0'; PackageFullName = 'Contoso.Weather_6.0.0.0_x64__abc' },
                [pscustomobject]@{ Name = 'Contoso.WeatherPro'; Version = '1.0.0.0'; PackageFullName = 'Contoso.WeatherPro_1.0.0.0_x64__abc' }
            )
        }
        $found = Get-StoreSoftware
        $found.Count | Should -Be 1
        $found[0].PackageFullName | Should -Be 'Contoso.Weather_4.2.0.0_x64__abc'
    }

    It 'removes the package for every user and its provisioned copy' {
        Mock Remove-AppxPackage { }
        Mock Get-AppxProvisionedPackage {
            @(
                [pscustomobject]@{ DisplayName = 'Contoso.Weather'; PackageName = 'Contoso.Weather_4.2.0.0_neutral_~_abc' },
                [pscustomobject]@{ DisplayName = 'Contoso.Other'; PackageName = 'Contoso.Other_1.0.0.0_neutral_~_abc' }
            )
        }
        Mock Remove-AppxProvisionedPackage { }
        $entries = @([pscustomobject]@{ Name = 'Contoso.Weather'; Version = '4.2.0.0'; PackageFullName = 'Contoso.Weather_4.2.0.0_x64__abc' })
        $result = @(Remove-StoreSoftware -Entries $entries)
        $result[-1] | Should -Be 0
        Should -Invoke Remove-AppxPackage -Times 1 -Exactly -ParameterFilter { $Package -eq 'Contoso.Weather_4.2.0.0_x64__abc' -and $AllUsers }
        Should -Invoke Remove-AppxProvisionedPackage -Times 1 -Exactly -ParameterFilter { $PackageName -eq 'Contoso.Weather_4.2.0.0_neutral_~_abc' }
    }

    It 'reports a failure when Windows refuses to remove a package' {
        Mock Remove-AppxPackage { throw 'access denied' }
        Mock Get-AppxProvisionedPackage { @() }
        $entries = @([pscustomobject]@{ Name = 'Contoso.Weather'; Version = '4.2.0.0'; PackageFullName = 'Contoso.Weather_4.2.0.0_x64__abc' })
        $result = @(Remove-StoreSoftware -Entries $entries)
        $result[-1] | Should -Be 1
    }
}

Describe 'Script quality' {
    It 'has no PSScriptAnalyzer error' {
        $template = Join-Path $script:Repo 'src\iafd\scripts\Remove-Software.ps1'
        $problems = @(Invoke-ScriptAnalyzer -Path $template -Severity Error)
        ($problems | ForEach-Object { "$($_.Line): $($_.RuleName) $($_.Message)" }) -join "`n" | Should -BeNullOrEmpty
    }
}
