import time
import subprocess
import datetime
from datetime import timedelta
from scale_cached import predict_zone

LF = 48


def get_replica_count(predicted_rps):
    """根據預測的 RPS 返回需要的 replica 數量"""
    if predicted_rps <= 160:
        return 1
    elif predicted_rps <= 320:
        return 2
    else:
        return 3


def scale_radius_app(replicas: int):
    """Scale the Kubernetes deployment "radius-app" to the specified number of replicas."""
    cmd = [
        "kubectl", "scale", "deployment", "radius-app", f"--replicas={replicas}"
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"{datetime.datetime.now()}: Scaled to {replicas} replicas")
    except subprocess.CalledProcessError as e:
        print(f"{datetime.datetime.now()}: Scaling failed: {e.stderr.decode().strip()}")


def main():
    """每 2.5 分鐘執行一次，預測並調整 replica 數量"""
    print("Starting proactive scaler - running every 2.5 minutes")

    # 設定模擬日期為 9/17
    simulation_date = datetime.datetime(2025, 9, 17, 0, 0, 0)
    current_hour = 0

    while current_hour <= 23:
        # 計算當前模擬時間
        current_simulation_time = simulation_date + timedelta(hours=current_hour)
        ts = current_simulation_time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            # 預測下一個時間點的 load
            predicted_load = predict_zone(ts) / 3600 * LF

            # 根據預測的 load 決定 replica 數量
            desired_replicas = get_replica_count(predicted_load)

            print(f"Simulation time: {ts}, Predicted RPS: {predicted_load:.2f}, Replicas: {desired_replicas}")

            # 執行 scaling
            scale_radius_app(desired_replicas)

        except Exception as ex:
            print(f"{datetime.datetime.now()}: Error determining or applying scale: {ex}")

        # 等待 2.5 分鐘
        time.sleep(150)  # 2.5 分鐘 = 150 秒

        # 移動到下一個小時
        current_hour += 1

    print("Simulation completed for 24 hours")


if __name__ == "__main__":
    main()
