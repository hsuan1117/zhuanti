import os
import time
import csv
import threading
import queue
import hmac
import hashlib
import argparse
from collections import defaultdict
from datetime import datetime

from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet
import six

# 全域統計
stats = {
    'total_sent': 0,
    'total_succeeded': 0,
    'total_failed': 0,
    'total_timeout': 0,
    'response_times': []
}
stats_lock = threading.Lock()


def create_radius_client(server='127.0.0.1', secret=b'testing123'):
    client = Client(server=server, authport=31812, secret=secret, dict=Dictionary("dictionary"))
    client.timeout = 5
    client.retries = 2
    return client


def send_auth_request(client, username='testuser', password='testpassword', secret=b'testing123'):
    start_time = time.monotonic()

    try:
        req = client.CreateAuthPacket(code=pyrad.packet.AccessRequest, User_Name=username)
        req["User-Password"] = req.PwCrypt(password)
        req["Message-Authenticator"] = 16 * six.b("\x00")
        raw_packet = req.RequestPacket()
        digest = hmac.new(secret, raw_packet, hashlib.md5).digest()
        req["Message-Authenticator"] = digest

        reply = client.SendPacket(req)

        with stats_lock:
            stats['total_succeeded'] += 1

    except pyrad.client.Timeout:
        with stats_lock:
            stats['total_timeout'] += 1
    except Exception:
        with stats_lock:
            stats['total_failed'] += 1

    duration = time.monotonic() - start_time

    with stats_lock:
        stats['total_sent'] += 1
        stats['response_times'].append(duration)


def worker(task_queue, server='127.0.0.1', secret=b'testing123'):
    client = create_radius_client(server, secret)
    while True:
        task = task_queue.get()
        if task is None:
            break
        send_auth_request(client, secret=secret)
        task_queue.task_done()


def run_test(rps, duration, workers=50, server='127.0.0.1', secret=b'testing123'):
    task_queue = queue.Queue(maxsize=workers * 2)
    threads = []

    for _ in range(workers):
        thread = threading.Thread(target=worker, args=(task_queue, server, secret))
        thread.start()
        threads.append(thread)

    start_time = time.monotonic()
    test_end_time = start_time + duration
    interval = 1.0 / rps if rps > 0 else 1.0
    total_tasks = int(rps * duration)

    for i in range(total_tasks):
        if time.monotonic() > test_end_time:
            break

        task_queue.put({"task": i})

        next_dispatch_time = start_time + (i + 1) * interval
        sleep_duration = next_dispatch_time - time.monotonic()
        if sleep_duration > 0:
            time.sleep(sleep_duration)

    for _ in range(workers):
        task_queue.put(None)

    for thread in threads:
        thread.join()


def reset_stats():
    global stats
    stats = {
        'total_sent': 0,
        'total_succeeded': 0,
        'total_failed': 0,
        'total_timeout': 0,
        'response_times': []
    }


def calculate_percentiles(times):
    if not times:
        return 0, 0
    sorted_times = sorted(times)
    p95_idx = int(len(sorted_times) * 0.95) - 1
    p99_idx = int(len(sorted_times) * 0.99) - 1
    p95 = sorted_times[p95_idx] if p95_idx >= 0 else sorted_times[0]
    p99 = sorted_times[p99_idx] if p99_idx >= 0 else sorted_times[0]
    return p95 * 1000, p99 * 1000  # Convert to ms


def main():
    parser = argparse.ArgumentParser(description="RADIUS Load Simulation")
    parser.add_argument('--input_csv', type=str, default='weekday_profile.csv', help='Input CSV file with hourly RPS data')
    args = parser.parse_args()
    # 建立結果目錄
    os.makedirs('results', exist_ok=True)

    # 開啟日誌檔案
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"results/simulation_{timestamp}.txt"
    result_csv = f"results/simulation_{timestamp}.csv"

    with open(log_filename, 'w') as log_file, open(result_csv, 'w', newline='') as csv_file:
        # 寫入 CSV 標題
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(
            ['hour', 'target_rps', 'actual_rps', 'sent', 'succeeded', 'failed', 'timeout', 'success_rate', 'p95_ms',
             'p99_ms'])

        def log_print(msg):
            print(msg)
            log_file.write(msg + '\n')
            log_file.flush()

        # 讀取 CSV
        log_print(f"Starting simulation at {datetime.now()}")
        log_print("=" * 60)

        hourly_data = []
        with open(args.input_csv, 'r') as f:
            reader = csv.DictReader(f, skipinitialspace=True)
            for row in reader:
                hourly_data.append({
                    'hour': int(row['hour']),
                    'rps_modified': int(float(row['rps_modified']))
                })

        duration_per_hour = 150  # 2.5 minutes
        total_stats = defaultdict(int)

        # 執行模擬
        for data in hourly_data:
            hour = data['hour']
            rps = data['rps_modified']

            log_print(f"\nHour {hour:02d}: RPS={rps}")

            reset_stats()

            start_time = time.monotonic()
            run_test(rps, duration_per_hour)
            elapsed = time.monotonic() - start_time

            # 計算統計
            actual_rps = stats['total_sent'] / elapsed if elapsed > 0 else 0
            success_rate = (stats['total_succeeded'] / stats['total_sent'] * 100) if stats['total_sent'] > 0 else 0
            p95, p99 = calculate_percentiles(stats['response_times'])

            # 輸出結果
            log_print(f"  Sent: {stats['total_sent']}, Success: {stats['total_succeeded']}, "
                      f"Failed: {stats['total_failed']}, Timeout: {stats['total_timeout']}")
            log_print(f"  Success Rate: {success_rate:.1f}%, P95: {p95:.1f}ms, P99: {p99:.1f}ms")

            # 寫入 CSV
            csv_writer.writerow([
                hour, rps, f"{actual_rps:.1f}", stats['total_sent'],
                stats['total_succeeded'], stats['total_failed'], stats['total_timeout'],
                f"{success_rate:.1f}", f"{p95:.1f}", f"{p99:.1f}"
            ])
            csv_file.flush()

            # 累積總計
            total_stats['sent'] += stats['total_sent']
            total_stats['succeeded'] += stats['total_succeeded']
            total_stats['failed'] += stats['total_failed']
            total_stats['timeout'] += stats['total_timeout']

        # 輸出總結
        log_print("\n" + "=" * 60)
        log_print("SIMULATION SUMMARY")
        log_print(f"Total Requests: {total_stats['sent']}")
        log_print(f"Total Succeeded: {total_stats['succeeded']}")
        log_print(f"Total Failed: {total_stats['failed']}")
        log_print(f"Total Timeout: {total_stats['timeout']}")

        if total_stats['sent'] > 0:
            overall_success_rate = total_stats['succeeded'] / total_stats['sent'] * 100
            log_print(f"Overall Success Rate: {overall_success_rate:.2f}%")

        log_print(f"\nResults saved to:")
        log_print(f"  - {log_filename}")
        log_print(f"  - {result_csv}")


if __name__ == "__main__":
    main()
