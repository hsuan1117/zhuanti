import os
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
import argparse
from collections import defaultdict

# 用於儲存結果的列表和鎖
results = []
results_lock = threading.Lock()

# 統計資訊
stats = {
    'total_sent': 0,
    'total_retransmits': 0,
    'total_succeeded': 0,
    'total_failed': 0,
    'total_no_reply': 0,
    'response_times': defaultdict(int)
}
stats_lock = threading.Lock()


def create_radius_client(server, secret):
    """建立 RADIUS 客戶端"""
    client = Client(server=server, authport=31812, secret=secret, dict=Dictionary("dictionary"))
    client.timeout = 5
    return client


def categorize_response_time(duration):
    """將回應時間分類"""
    if duration < 0.00001:  # < 10 usec
        return '< 10 usec'
    elif duration < 0.0001:  # < 100 usec
        return '< 100 usec'
    elif duration < 0.001:  # < msec
        return '< msec'
    elif duration < 0.01:  # < 10 msec
        return '< 10 msec'
    elif duration < 0.1:  # < 0.1s
        return '< 0.1s'
    elif duration < 1:  # < s
        return '< s'
    elif duration < 10:  # < 10s
        return '< 10s'
    else:  # < 100s
        return '< 100s'


def send_auth_request(client, username, password, pkt_id, secret, max_retries=3):
    """發送 RADIUS 認證請求"""
    start = time.time()
    retries = 0

    while retries <= max_retries:
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
            digest = hmac.new(secret, raw_packet, hashlib.md5)
            # 寫回 Message-Authenticator
            req["Message-Authenticator"] = bytes.fromhex(digest.hexdigest())

            # 發送請求
            reply = client.SendPacket(req)
            end = time.time()
            duration = end - start

            # 更新統計資訊
            with stats_lock:
                if retries == 0:
                    stats['total_sent'] += 1
                else:
                    stats['total_retransmits'] += retries
                stats['total_succeeded'] += 1
                category = categorize_response_time(duration)
                stats['response_times'][category] += 1

            # 儲存結果
            with results_lock:
                results.append({
                    'pkt_id': pkt_id,
                    'start': start,
                    'end': end,
                    'duration': duration
                })
            return True

        except Exception as e:
            retries += 1

            # 如果還有重試次數，繼續重試
            if retries <= max_retries:
                with stats_lock:
                    if retries == 1:
                        stats['total_sent'] += 1
                    stats['total_retransmits'] += 1

                # 短暫等待後重試
                time.sleep(0.1)
                continue
            else:
                # 已達最大重試次數，記錄失敗
                end = time.time()
                duration = end - start

                # 更新統計資訊
                with stats_lock:
                    if retries == 1:  # 第一次發送
                        stats['total_sent'] += 1
                    if "timeout" in str(e).lower():
                        stats['total_no_reply'] += 1
                    else:
                        stats['total_failed'] += 1

                # 儲存結果
                with results_lock:
                    results.append({
                        'pkt_id': pkt_id,
                        'start': start,
                        'end': end,
                        'duration': duration
                    })
                return False


def worker(worker_id, start_id, count, server, secret):
    """每個並行工作者執行的函數"""
    client = create_radius_client(server, secret)
    for i in range(count):
        pkt_id = start_id + i
        send_auth_request(client, "testuser", "testpassword", pkt_id, secret)


def save_results_to_csv(filename):
    """將結果儲存到 CSV 檔案"""
    with open(filename, 'w', newline='') as csvfile:
        fieldnames = ['pkt_id', 'start', 'end', 'duration']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # 按 pkt_id 排序
        sorted_results = sorted(results, key=lambda x: x['pkt_id'])
        for result in sorted_results:
            writer.writerow(result)


def print_statistics(total_time):
    """印出統計資訊"""
    print(f"\n             Total sent        :  {stats['total_sent']}")
    print(f"             Total retransmits :  {stats['total_retransmits']}")
    print(f"             Total succeeded   :  {stats['total_succeeded']}")
    print(f"             Total failed      :  {stats['total_failed']}")
    print(f"             Total no reply    :  {stats['total_no_reply']}")
    print(f"             Total time (s)    :  {total_time:.3f}")

    if total_time > 0:
        packets_per_sec = stats['total_sent'] / total_time
        print(f"             Packets/s         :  {int(packets_per_sec)}")

    print("             Response times:")
    time_categories = ['< 10 usec', '< 100 usec', '< msec', '< 10 msec', '< 0.1s', '< s', '< 10s', '< 100s']
    for category in time_categories:
        count = stats['response_times'].get(category, 0)
        print(f"                {category:<11}: {count}")

    # 新增 <3s 與 >3s 百分比分析
    total_requests = len(results)
    under_3s = sum(1 for r in results if r['duration'] < 3)
    over_3s = total_requests - under_3s
    if total_requests > 0:
        pct_under_3s = under_3s / total_requests * 100
        pct_over_3s = over_3s / total_requests * 100
        print(f"             < 3s             : {pct_under_3s:.2f}%")
        print(f"             > 3s             : {pct_over_3s:.2f}%")


def main():
    parser = argparse.ArgumentParser(description='RADIUS client performance test')
    parser.add_argument('-c', '--count', type=int, default=100, help='Total number of packets to send')
    parser.add_argument('-p', '--parallel', type=int, default=10, help='Number of parallel clients')
    parser.add_argument('--server', default='10.121.252.145', help='RADIUS server IP')
    parser.add_argument('--secret', default='testing123', help='RADIUS secret')

    args = parser.parse_args()

    total_packets = args.count
    parallel_clients = args.parallel
    server = args.server
    secret = args.secret.encode()

    # 計算每個工作者要發送的封包數
    packets_per_worker = total_packets // parallel_clients
    remaining_packets = total_packets % parallel_clients

    start_time = time.time()

    # 使用 ThreadPoolExecutor 並行發送請求
    with ThreadPoolExecutor(max_workers=parallel_clients) as executor:
        futures = []
        current_id = 1

        for i in range(parallel_clients):
            # 分配封包數量，確保總數正確
            count = packets_per_worker + (1 if i < remaining_packets else 0)
            if count > 0:
                future = executor.submit(worker, i, current_id, count, server, secret)
                futures.append(future)
                current_id += count

        # 等待所有工作完成
        for future in futures:
            future.result()

    end_time = time.time()
    total_time = end_time - start_time

    # 儲存結果到 CSV
    if not os.path.exists('results'):
        os.makedirs('results')

    current_time_fn = time.strftime("%Y%m%d_%H%M%S")
    save_results_to_csv(f"results/radius_results_{current_time_fn}.csv")

    # 印出統計資訊
    print_statistics(total_time)
    print(f"\nResults saved to {' radius_results_' + current_time_fn + '.csv'}")


if __name__ == "__main__":
    main()
