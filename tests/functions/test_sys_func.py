import os.path

from bt_api_py.functions.utils import get_package_path, read_yaml_file


def test_get_package_path():
    path = get_package_path('numpy')
    assert path is not None
    path2 = get_package_path("bt_api_py")
    assert "bt_api_py" in path2


def test_read_yaml_file():
    path = get_package_path('bt_api_py')
    if os.path.exists(path + "\\" + "account_config.yaml"):
        content = read_yaml_file("account_config.yaml")
        assert content is not None


if __name__ == '__main__':
    test_get_package_path()
    test_read_yaml_file()
