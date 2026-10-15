$artifact = 'D:\temp\iteration10-cfg02-real-20261015-r1'
Set-Location -LiteralPath $artifact
$env:PYTHONPATH = Join-Path $artifact 'test-only'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:BT_API_ITERATION10_CFG02_SERVER_PYTHON = Join-Path $artifact 'server-venv\Scripts\python.exe'
$clientPython = Join-Path $artifact 'client-venv\Scripts\python.exe'
$testPath = Join-Path $artifact 'test-only\tests\unit\test_iteration10_optional_install_matrix.py'
$junitPath = Join-Path $artifact 'junit-cfg02.xml'
$baseTemp = Join-Path $artifact 'pytest-tmp'
& $clientPython -m pytest -s -vv --noconftest --import-mode=importlib -p no:cacheprovider --basetemp $baseTemp --junitxml $junitPath $testPath 2>&1 | Tee-Object -FilePath (Join-Path $artifact 'pytest-cfg02.log')
$pytestExit = $LASTEXITCODE
Set-Content -LiteralPath (Join-Path $artifact 'pytest-cfg02-exit.txt') -Value $pytestExit -Encoding ascii
exit $pytestExit
