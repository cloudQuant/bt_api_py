$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'load01' }
if ($procs) { $procs | Select-Object -First 3 ProcessId, CommandLine | Format-List } else { Write-Output "NO-LOAD01-PROCESS" }
