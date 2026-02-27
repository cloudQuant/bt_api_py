"""
OKX Sub-account API Usage Examples

This script demonstrates how to use the OKX Sub-account REST API interfaces.
"""

from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap
from bt_api_py.functions.utils import read_account_config
import queue

def init_okx_client():
    """Initialize OKX client with credentials from config."""
    data = read_account_config()
    data_queue = queue.Queue()
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        "proxies": data.get('proxies'),
    }
    return OkxRequestDataSwap(data_queue, **kwargs)


def example_get_sub_account_list():
    """Example: Get list of sub-accounts."""
    print("=" * 50)
    print("Example 1: Get Sub-account List")
    print("=" * 50)
    
    client = init_okx_client()
    
    # Get all sub-accounts
    result = client.get_sub_account_list(limit="10")
    
    if result.get_status():
        sub_accounts = result.get_data()
        print(f"Found {len(sub_accounts)} sub-account(s)")
        for account in sub_accounts:
            print(f"  - Name: {account.get('subAcct')}, "
                  f"Enabled: {account.get('enable')}, "
                  f"Label: {account.get('label', 'N/A')}")
    else:
        print("Failed to get sub-account list")
    
    print()


def example_get_sub_account_api_key():
    """Example: Get API keys for a specific sub-account."""
    print("=" * 50)
    print("Example 2: Get Sub-account API Keys")
    print("=" * 50)
    
    client = init_okx_client()
    
    # Get API keys for a sub-account
    # Replace 'your_sub_account_name' with actual sub-account name
    result = client.get_sub_account_api_key(sub_acct="your_sub_account_name")
    
    if result.get_status():
        api_keys = result.get_data()
        print(f"Found {len(api_keys)} API key(s)")
        for key in api_keys:
            print(f"  - API Key: {key.get('apiKey')}, "
                  f"Label: {key.get('label', 'N/A')}, "
                  f"Permissions: {key.get('perm', [])}")
    else:
        print("Failed to get sub-account API keys")
        print(f"Error: {result.get_input_data().get('msg', 'Unknown error')}")
    
    print()


def example_get_sub_account_funding_balance():
    """Example: Get funding balance for a sub-account."""
    print("=" * 50)
    print("Example 3: Get Sub-account Funding Balance")
    print("=" * 50)
    
    client = init_okx_client()
    
    # Get funding balance for a sub-account
    result = client.get_sub_account_funding_balance(
        sub_acct="your_sub_account_name"
    )
    
    if result.get_status():
        balances = result.get_data()
        print(f"Funding balances:")
        for balance in balances[:5]:  # Show first 5
            print(f"  - {balance.get('ccy')}: "
                  f"Balance: {balance.get('bal')}, "
                  f"Frozen: {balance.get('frozenBal')}")
    else:
        print("Failed to get sub-account funding balance")
        print(f"Error: {result.get_input_data().get('msg', 'Unknown error')}")
    
    print()


def example_get_sub_account_max_withdrawal():
    """Example: Get maximum withdrawal for a sub-account."""
    print("=" * 50)
    print("Example 4: Get Sub-account Max Withdrawal")
    print("=" * 50)
    
    client = init_okx_client()
    
    # Get max withdrawal for USDT
    result = client.get_sub_account_max_withdrawal(
        sub_acct="your_sub_account_name",
        ccy="USDT"
    )
    
    if result.get_status():
        max_withdrawals = result.get_data()
        for item in max_withdrawals:
            print(f"  - Currency: {item.get('ccy')}, "
                  f"Max Withdrawal: {item.get('maxWd')}")
    else:
        print("Failed to get sub-account max withdrawal")
        print(f"Error: {result.get_input_data().get('msg', 'Unknown error')}")
    
    print()


def example_create_sub_account():
    """Example: Create a new sub-account (requires actual API call)."""
    print("=" * 50)
    print("Example 5: Create Sub-account")
    print("=" * 50)
    print("Note: This is a placeholder example.")
    print("Replace with actual values to create a sub-account.")
    
    client = init_okx_client()
    
    # Create a new sub-account
    result = client.create_sub_account(
        sub_acct="new_sub_account_name",
        label="Trading Sub-account"
    )
    
    if result.get_status():
        print("Sub-account created successfully")
        print(f"Result: {result.get_data()}")
    else:
        print("Failed to create sub-account")
        print(f"Error: {result.get_input_data().get('msg', 'Unknown error')}")
    
    print()


def example_async_get_sub_account_list():
    """Example: Asynchronously get sub-account list."""
    print("=" * 50)
    print("Example 6: Async Get Sub-account List")
    print("=" * 50)
    
    data_queue = queue.Queue()
    data = read_account_config()
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        "proxies": data.get('proxies'),
    }
    client = OkxRequestDataSwap(data_queue, **kwargs)
    
    # Make async request
    client.async_get_sub_account_list(limit="10")
    
    # Wait for result (in real application, use proper async handling)
    import time
    time.sleep(3)
    
    try:
        result = data_queue.get(False)
        if result.get_status():
            sub_accounts = result.get_data()
            print(f"Found {len(sub_accounts)} sub-account(s) (async)")
        else:
            print("Failed to get sub-account list (async)")
    except:
        print("No data received from async request")
    
    print()


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("OKX Sub-account API Examples")
    print("=" * 50 + "\n")
    
    # Run examples
    example_get_sub_account_list()
    example_get_sub_account_api_key()
    example_get_sub_account_funding_balance()
    example_get_sub_account_max_withdrawal()
    # example_create_sub_account()  # Uncomment to test
    example_async_get_sub_account_list()
    
    print("=" * 50)
    print("Examples completed!")
    print("=" * 50)
