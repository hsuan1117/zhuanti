import time
import csv
from concurrent.futures import ThreadPoolExecutor
from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet
import threading
import six
import hmac
import hashlib

TOTAL = 10
PARALLEL = 10
EACH = TOTAL // PARALLEL
SERVER = "10.121.252.145"
SECRET = b"testing123"

# 用於儲存結果的列表和鎖
results = []
results_lock = threading.Lock()

def create_radius_client():
    """建立 RADIUS 客戶端"""
    client = Client(server=SERVER, authport=31812,retries=1, secret=SECRET, dict=Dictionary("dictionary"))
    # 新增：設定超時（秒）
    client.timeout = 5
    return client

def send_auth_request(client, username, password, pkt_id):
    """發送 RADIUS 認證請求"""
    start = time.time()
    try:
        # 建立認證封包，插入 Message-Authenticator 為 16 個 0x00
        req = client.CreateAuthPacket(
            code=pyrad.packet.AccessRequest,
            User_Name=username,
        )
        req["User-Password"] = req.PwCrypt(password)
        req["Message-Authenticator"] = 16 * six.b("\x00")

        # 取得原始封包二進位資料
        raw_packet = req.RequestPacket()
        # 計算 hmac-md5
        digest = hmac.new(SECRET, raw_packet, hashlib.md5)
        # 寫回 Message-Authenticator
        req["Message-Authenticator"] = bytes.fromhex(digest.hexdigest())

        # 發送請求
        reply = client.SendPacket(req)
        end = time.time()
        print(reply)
        # 儲存結果
        with results_lock:
            results.append({
                'pkt_id': pkt_id,
                'start': start,
                'end': end
            })
        return True
    except Exception as e:
        print("Error", pkt_id, e)
        end = time.time()
        with results_lock:
            results.append({
                'pkt_id': pkt_id,
                'start': start,
                'end': end
            })
        return False

def worker(worker_id):
    """每個並行工作者執行的函數"""
    client = create_radius_client()
    send_auth_request(client, "testuser", "testpassword", 1)

def main():
    start_time = time.time()

    worker(1)

    end_time = time.time()
    elapsed = int(end_time - start_time)

    print(f"Total time for {TOTAL} radclient requests with {PARALLEL} parallel clients: {elapsed} seconds")
    print(f"Results saved to radius_results.csv")

if __name__ == "__main__":
    main()
