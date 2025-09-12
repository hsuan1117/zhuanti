import os
import time
import csv
import threading
import queue
import hmac
import hashlib
import argparse
from collections import defaultdict
from typing import Dict, Any

# 使用 pyrad 函式庫來處理 RADIUS 協議
from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet
import six

# --- 全域變數和鎖，用於在多執行緒環境下安全地共享數據 ---

# 用於儲存每個請求的詳細結果
results = []
results_lock = threading.Lock()

# 用於即時統計匯總數據
stats: Dict[str, Any] = {
    'total_sent': 0,
    'total_succeeded': 0,
    'total_failed': 0,
    'total_no_reply': 0,
    'response_times': defaultdict(int)
}
stats_lock = threading.Lock()

# 封包 ID 計數器
packet_id_counter = 0
packet_id_lock = threading.Lock()


# --- RADIUS 相關的核心函式 ---

def create_radius_client(server: str, secret: bytes) -> Client:
    """建立並配置一個 RADIUS 客戶端實例"""
    client = Client(server=server, authport=31812, secret=secret, dict=Dictionary("dictionary"))
    client.timeout = 5  # 設置請求超時為 5 秒
    client.retries = 2  # 設置重試次數為 2 次
    return client


def categorize_response_time(duration: float) -> str:
    """將回應時間 (秒) 分類到不同的區間"""
    if duration < 0.01: return '< 10ms'
    if duration < 0.05: return '< 50ms'
    if duration < 0.1: return '< 100ms'
    if duration < 0.2: return '< 200ms'  # 這是常見的 SLO 目標
    if duration < 0.5: return '< 500ms'
    if duration < 1.0: return '< 1s'
    return '>= 1s'


def send_auth_request(client: Client, username: str, password: str, secret: bytes):
    """
    發送單一的 RADIUS 認證請求並記錄結果。
    這個函式是執行緒中實際執行的工作。
    """
    global packet_id_counter
    with packet_id_lock:
        pkt_id = packet_id_counter
        packet_id_counter += 1

    start_time = time.monotonic()

    try:
        # 建立 RADIUS Access-Request 封包
        req = client.CreateAuthPacket(code=pyrad.packet.AccessRequest, User_Name=username)
        req["User-Password"] = req.PwCrypt(password)

        # 為了安全，RADIUS協定要求一個 Message-Authenticator
        # 這裡我們先填入 16 bytes 的零
        req["Message-Authenticator"] = 16 * six.b("\x00")
        raw_packet = req.RequestPacket()

        # 使用 HMAC-MD5 和 secret 計算正確的 Message-Authenticator
        digest = hmac.new(secret, raw_packet, hashlib.md5).digest()
        req["Message-Authenticator"] = digest

        # 發送封包並等待回應
        reply = client.SendPacket(req)

        # 請求成功
        with stats_lock:
            stats['total_succeeded'] += 1
        status = 'succeeded'

    except pyrad.client.Timeout as e:
        # 請求超時，沒有收到回應
        with stats_lock:
            stats['total_no_reply'] += 1
        status = 'no_reply'
    except Exception as e:
        # 其他類型的錯誤
        with stats_lock:
            stats['total_failed'] += 1
        status = f'failed: {e}'

    end_time = time.monotonic()
    duration = end_time - start_time

    # 無論成功或失敗，都更新總發送數和回應時間分類
    with stats_lock:
        stats['total_sent'] += 1
        category = categorize_response_time(duration)
        stats['response_times'][category] += 1

    # 將詳細結果加入列表，以便後續分析 (如計算P95)
    with results_lock:
        results.append({
            'pkt_id': pkt_id,
            'status': status,
            'start_time': start_time,
            'duration': duration
        })


# --- 壓力測試執行框架 (Producer-Consumer 模式) ---

def consumer_worker(task_queue: queue.Queue, server: str, secret: bytes):
    """
    消費者執行緒：從隊列中取出任務並執行。
    每個執行緒會建立自己的 RADIUS client，避免共享造成的執行緒安全問題。
    """
    client = create_radius_client(server, secret)
    while True:
        task = task_queue.get()
        # 如果收到 None，代表任務結束，退出循環
        if task is None:
            break

        send_auth_request(client, task['username'], task['password'], secret)
        task_queue.task_done()


def run_test(rps: int, duration: int, workers: int, server: str, secret: bytes):
    """
    執行壓力測試的主函式 (Producer)。
    它會以指定的 RPS 速率向隊列中放入任務，持續指定的秒數。
    """
    print(f"🚀 Starting test: {rps} RPS for {duration} seconds with {workers} workers...")

    task_queue: queue.Queue = queue.Queue(maxsize=workers * 2)
    threads = []

    # 建立並啟動消費者執行緒
    for _ in range(workers):
        thread = threading.Thread(target=consumer_worker, args=(task_queue, server, secret))
        thread.start()
        threads.append(thread)

    # 生產者邏輯
    start_time = time.monotonic()
    test_end_time = start_time + duration
    interval = 1.0 / rps
    total_tasks = rps * duration

    for i in range(total_tasks):
        if time.monotonic() > test_end_time:
            print("⚠️ Test duration elapsed, stopping producer.")
            break

        # 構造任務並放入隊列
        task = {"username": "testuser", "password": "testpassword"}
        task_queue.put(task)

        # 透過精確計算下一次派發的時間點來維持穩定的 RPS，避免誤差累積
        next_dispatch_time = start_time + (i + 1) * interval
        sleep_duration = next_dispatch_time - time.monotonic()
        if sleep_duration > 0:
            time.sleep(sleep_duration)

    # 所有任務都已放入隊列，發送結束信號 (None)
    for _ in range(workers):
        task_queue.put(None)

    # 等待所有消費者執行緒處理完畢並退出
    for thread in threads:
        thread.join()

    print("✅ Test finished.")


# --- 數據統計與報告 ---

def save_results_to_csv(filename: str):
    """將詳細結果儲存到 CSV 檔案"""
    if not results:
        return

    print(f"\n💾 Saving results to {filename}...")
    # 建立 results 目錄 (如果不存在)
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with open(filename, 'w', newline='') as csvfile:
        fieldnames = ['pkt_id', 'status', 'start_time', 'duration']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        sorted_results = sorted(results, key=lambda x: x['pkt_id'])
        writer.writerows(sorted_results)


def print_statistics(total_time: float):
    """在終端印出格式化的統計報告"""
    print("\n--- 📊 Test Statistics ---")

    total_sent = stats['total_sent']
    if total_time > 0:
        actual_rps = total_sent / total_time
        print(f"    - Actual RPS        : {actual_rps:.2f}")

    print(f"    - Total Sent        : {total_sent}")
    print(f"    - Succeeded         : {stats['total_succeeded']}")
    print(f"    - Failed            : {stats['total_failed']}")
    print(f"    - No Reply (Timeout)  : {stats['total_no_reply']}")
    print(f"    - Total Time        : {total_time:.2f} s")

    print("\n--- ⏱️ Response Time Distribution ---")
    time_categories = ['< 10ms', '< 50ms', '< 100ms', '< 200ms', '< 500ms', '< 1s', '>= 1s']
    for category in time_categories:
        count = stats['response_times'].get(category, 0)
        percentage = (count / total_sent * 100) if total_sent > 0 else 0
        print(f"    - {category:<10}: {count:<6} ({percentage:.2f}%)")

    # 計算 P95, P99 延遲
    if results:
        durations = sorted([r['duration'] for r in results])
        p95_index = int(len(durations) * 0.95) - 1
        p99_index = int(len(durations) * 0.99) - 1
        p95_latency = durations[p95_index] if p95_index >= 0 else 0
        p99_latency = durations[p99_index] if p99_index >= 0 else 0
        print("\n--- 📈 Percentile Latencies ---")
        print(f"    - P95 Latency       : {p95_latency * 1000:.2f} ms")
        print(f"    - P99 Latency       : {p99_latency * 1000:.2f} ms")

    print("-------------------------\n")


def reset_stats():
    """重置全域統計數據，用於 ramp 模式"""
    global results, stats, packet_id_counter
    results.clear()
    stats = {
        'total_sent': 0,
        'total_succeeded': 0,
        'total_failed': 0,
        'total_no_reply': 0,
        'response_times': defaultdict(int)
    }
    packet_id_counter = 0


# --- 主程式入口與命令列參數解析 ---

def main():
    parser = argparse.ArgumentParser(
        description='🚀 RADIUS RPS-based Performance Test Tool',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--server', default='127.0.0.1', help='RADIUS server IP address')
    parser.add_argument('--secret', default='testing123', help='RADIUS secret')
    parser.add_argument('-w', '--workers', type=int, default=50, help='Number of consumer worker threads')

    subparsers = parser.add_subparsers(dest='mode', required=True, help='Test mode')

    # `run` 模式：以固定的 RPS 運行
    parser_run = subparsers.add_parser('run', help='Run a test with a fixed RPS.')
    parser_run.add_argument('-r', '--rps', type=int, required=True, help='Target requests per second')
    parser_run.add_argument('-d', '--duration', type=int, required=True, help='Test duration in seconds')

    # `ramp` 模式：自動化階梯式壓力測試
    parser_ramp = subparsers.add_parser('ramp', help='Run a ramping test to find the breaking point.')
    parser_ramp.add_argument('--start-rps', type=int, default=50, help='Starting RPS')
    parser_ramp.add_argument('--step-rps', type=int, default=25, help='RPS increment for each step')
    parser_ramp.add_argument('--step-duration', type=int, default=30, help='Duration of each RPS step in seconds')
    parser_ramp.add_argument('--max-rps', type=int, default=500, help='Maximum RPS to test')
    parser_ramp.add_argument('--slo-ms', type=float, default=200,
                             help='P95 Latency SLO in milliseconds. The ramp will stop if this is violated.')

    args = parser.parse_args()
    secret_bytes = args.secret.encode()

    if args.mode == 'run':
        start_time = time.monotonic()
        run_test(args.rps, args.duration, args.workers, args.server, secret_bytes)
        total_time = time.monotonic() - start_time

        print_statistics(total_time)
        current_time_fn = time.strftime("%Y%m%d_%H%M%S")
        filename = f"results/run_{args.rps}rps_{args.duration}s_{current_time_fn}.csv"
        save_results_to_csv(filename)

    elif args.mode == 'ramp':
        print("--- 📈 Starting Ramping Test ---")
        slo_s = args.slo_ms / 1000.0

        for rps in range(args.start_rps, args.max_rps + 1, args.step_rps):
            reset_stats()  # 每個階段開始前重置統計數據

            start_time = time.monotonic()
            run_test(rps, args.step_duration, args.workers, args.server, secret_bytes)
            total_time = time.monotonic() - start_time

            print(f"--- 📋 Results for {rps} RPS ---")
            print_statistics(total_time)

            # 檢查是否違反 SLO
            if results:
                durations = sorted([r['duration'] for r in results])
                p95_index = int(len(durations) * 0.95) - 1
                if p95_index >= 0:
                    p95_latency = durations[p95_index]
                    if p95_latency > slo_s:
                        print(
                            f"🚨 SLO VIOLATED! P95 Latency ({p95_latency * 1000:.2f}ms) > SLO ({args.slo_ms}ms) at {rps} RPS.")
                        print("--- 🏁 Ramping Test Stopped ---")
                        break
        else:
            print("--- ✅ Ramping Test Completed without violating SLO ---")


if __name__ == "__main__":
    main()
