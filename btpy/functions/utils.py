import sys
import os
import yaml
import importlib
import requests


def get_public_ip():
    try:
        # 使用一个查询公共 IP 地址的服务
        response = requests.get('https://api.ipify.org')
        # 如果请求成功，返回响应的文本内容，即当前设备的公共 IP 地址
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"Error occurred: {e}")
        try:
            response = requests.get('https://api.myip.com')
            response.raise_for_status()  # 检查请求是否成功
            data = response.json()
            return data.get('ip')
        except requests.RequestException as e:
            print(f"Error fetching IP: {e}")
            return None
    # 如果发生任何异常或请求失败，返回 None
    return None


def get_package_path(package_name="lv"):
    """获取包的路径值
    :param package_name: 包的名称
    :return: 返回的路径值
    """
    try:
        importlib.import_module(package_name)
        package = sys.modules[package_name]
    except KeyError:
        print(f"Package {package_name} not found")
        return None
    if package.__file__ is not None:
        return os.path.dirname(package.__file__)
    else:
        return package.__path__.__dict__["_path"][0]


def read_yaml_file(file_name, data_root=None):
    """读取放在btpy根目录中的yaml文件
    :param data_root: 文件所在目录
    :param file_name: 文件名称
    :return: 返回的yaml文件的内容
    """
    if data_root is None:
        package_path = get_package_path("btpy")
        file_path = package_path + "/configs/" + file_name
    else:
        file_path = data_root + "/configs/" + file_name
    with open(file_path, 'r') as file:
        file_content = yaml.load(file, Loader=yaml.FullLoader)
    return file_content


def update_extra_data(extra_data, **kwargs):
    """
    update extra_data using kwargs
    :param extra_data: extra_data is None or dict
    :param kwargs: kwargs is dict
    :return: extra_data, dict
    """
    if extra_data is None:
        extra_data = kwargs
    else:
        extra_data.update(kwargs)
    return extra_data


def from_dict_get_string(content, key, default=None):
    if key not in content:
        return default
    else:
        val = content[key]
        if isinstance(val, str):
            return val
        else:
            return str(val)


def from_dict_get_bool(content, key, default=None):
    if key not in content:
        return default
    else:
        value = content[key]
        return True if value == "true" else False


def from_dict_get_float(content, key, default=None):
    if key not in content:
        return default
    value = content[key]
    if value == '':
        return None
    elif value is None:
        return None
    elif isinstance(value, float):
        return value
    else:
        return float(value)


def from_dict_get_int(content, key, default=None):
    if key not in content:
        return default
    value = content[key]
    if value == '':
        return None
    elif value is None:
        return None
    elif isinstance(value, int):
        return value
    else:
        return int(value)


if __name__ == "__main__":
    print(get_package_path("btpy"))
    # 获取并打印当前设备的公共 IP 地址
    public_ip = get_public_ip()
    if public_ip:
        print(f"Public IP address: {public_ip}")
    else:
        print("Failed to retrieve public IP address.")
