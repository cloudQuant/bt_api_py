import hashlib
from pathlib import Path

def pytest_sessionstart(session):
    import bt_api_execution.group_coordinator as coordinator
    import bt_api_execution.store as store
    expected = Path(__file__).resolve().parents[1] / 'src/bt_api_execution'
    for module, name, digest in (
        (coordinator, 'group_coordinator.py', '3d7412e9b52698560db26cc866c3e1a3a80e27b2972bfe031c7c29400f8b615f'),
        (store, 'store.py', 'b3ccf1030e28522ceef12934a52c847f435ccb29c9b11e7eb7087d07b44168ed'),
    ):
        actual = Path(module.__file__).resolve()
        assert actual == expected / name
        assert hashlib.sha256(actual.read_bytes()).hexdigest() == digest
        print('ROOT_SOURCE_ORIGIN', module.__name__, str(actual))
