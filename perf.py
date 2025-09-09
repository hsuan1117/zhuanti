import time
import csv
from concurrent.futures import ThreadPoolExecutor
from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet
import threading

TOTAL = 10000
PARALLEL = 10
EACH = TOTAL // PARALLEL
SERVER = "radius-service"
SECRET = b"testing123"

# 用於儲存結果的列表和鎖
results = []
results_lock = threading.Lock()

def create_radius_client():
    """建立 RADIUS 客戶端"""
    client = Client(server=SERVER, secret=SECRET, dict=Dictionary("dictionary"))
    return client

def send_auth_request(client, username, password, pkt_id):
    """發送 RADIUS 認證請求"""
    start = time.time()
    try:
        # 建立認證封包
        req = client.CreateAuthPacket(code=pyrad.packet.AccessRequest,
                                      User_Name=username,
                                      User_Password=password)

        # 發送請求
        reply = client.SendPacket(req)
        end = time.time()

        # 儲存結果
        with results_lock:
            results.append({
                'pkt_id': pkt_id,
                'start': start,
                'end': end
            })
        return True
    except Exception:
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
    for i in range(EACH):
        pkt_id = worker_id * EACH + i + 1
        send_auth_request(client, "testuser", "testpassword", pkt_id)

def main():
    start_time = time.time()

    # 使用 ThreadPoolExecutor 來並行執行
    with ThreadPoolExecutor(max_workers=PARALLEL) as executor:
        futures = []
        for i in range(PARALLEL):
            future = executor.submit(worker, i)
            futures.append(future)

        # 等待所有工作完成
        for future in futures:
            future.result()

    end_time = time.time()
    elapsed = int(end_time - start_time)

    # 將結果寫入 CSV
    with open('radius_results.csv', 'w', newline='') as csvfile:
        fieldnames = ['pkt_id', 'start', 'end']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # 按 pkt_id 排序後寫入
        sorted_results = sorted(results, key=lambda x: x['pkt_id'])
        writer.writerows(sorted_results)

    print(f"Total time for {TOTAL} radclient requests with {PARALLEL} parallel clients: {elapsed} seconds")
    print(f"Results saved to radius_results.csv")

if __name__ == "__main__":
    main()
