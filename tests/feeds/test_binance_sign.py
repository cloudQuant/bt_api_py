import hmac
import urllib
from urllib.parse import urlencode


def test_binance_sign():
    """签名

    Args:
        content (TYPE): Description
    """
    content = {'recvWindow': 3000,
               'timestamp': 1709265105581,
               'symbol': 'OPUSDT'}
    sign_value = urlencode(content)
    win_sig = '0e567ed596f286653cb3e1bd34beaf5730feb4f777d9fe8ec342d1ba0fc1fb60'
    private_key = 's4eqlypRMA6svUEcxOSHTgyMW4W2waxkSZ3zqLUTPICyPjuRY9g3N1M23F8cTeQE'
    sign = hmac.new(
        private_key.encode('utf-8'), sign_value.encode('utf-8'), digestmod='sha256'
    ).hexdigest()
    assert win_sig == sign


if __name__ == '__main__':
    test_binance_sign()
