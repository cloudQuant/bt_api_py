Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' } | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine | Format-List
