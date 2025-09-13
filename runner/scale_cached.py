import os
import pickle
import pandas as pd
import holidays

CACHE_FILE = 'scale_cache.pkl'


def build_cache():
    # Compute thresholds and training averages
    tw_holidays = holidays.country_holidays('TW')
    # Load and filter data
    df = pd.read_csv('parsed_logs.csv', parse_dates=['time'])
    df.set_index('time', inplace=True)
    df = df[(df.index >= pd.Timestamp('2025-02-14')) & (df.index <= pd.Timestamp('2025-06-01'))]
    # Hourly count of 'ok'
    ok_count = df[df['type'] == 'ok'].resample('H').size()
    # Thresholds
    max_ok = ok_count.max()
    thresholds = [0, 0, 0] if max_ok == 0 else [max_ok / 3, max_ok * 2 / 3, max_ok]
    # Prepare training averages
    count_df = ok_count.reset_index(name='y').rename(columns={'time': 'ds'})
    count_df['hour_of_day'] = count_df['ds'].dt.hour
    count_df['is_holiday'] = count_df['ds'].dt.date.isin(tw_holidays) | (count_df['ds'].dt.weekday >= 5)
    count_df['day_of_week'] = count_df['ds'].dt.weekday
    train_df = count_df[(count_df['ds'] >= pd.Timestamp('2025-02-14')) & (count_df['ds'] < pd.Timestamp('2025-05-01'))]
    train_avg = train_df.groupby(['hour_of_day', 'is_holiday', 'day_of_week'])['y'].mean().reset_index(name='yhat')
    # Cache results
    with open(CACHE_FILE, 'wb') as f:
        pickle.dump({'thresholds': thresholds, 'train_avg': train_avg}, f)
    return thresholds, train_avg, tw_holidays


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'rb') as f:
            data = pickle.load(f)
        tw_holidays = holidays.country_holidays('TW')
        return data['thresholds'], data['train_avg'], tw_holidays
    else:
        return build_cache()


# Load or build cache once at import
thresholds, train_avg, tw_holidays = load_cache()


def assign_zone(val: float) -> int:
    """
    Assign zone based on thresholds.
    """
    if val <= thresholds[0]:
        return 1
    elif val <= thresholds[1]:
        return 2
    else:
        return 3


def predict_zone(timestamp) -> int:
    """
    Predict the replica zone (1, 2, or 3) for a given timestamp.
    Input can be a string or datetime-like object.
    """
    ts = pd.to_datetime(timestamp)
    hour = ts.hour
    is_hol = ts.date() in tw_holidays or ts.weekday() >= 5
    dow = ts.weekday()
    grp = train_avg[
        (train_avg.hour_of_day == hour) &
        (train_avg.is_holiday == is_hol) &
        (train_avg.day_of_week == dow)
    ]
    yhat = float(grp['yhat'].iloc[0]) if not grp.empty else 0.0
    return yhat
    return assign_zone(yhat)


if __name__ == '__main__':
    import sys
    ts_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if ts_arg:
        zone = predict_zone(ts_arg)
        print(zone)
    else:
        print('Usage: python scale_cached.py "YYYY-MM-DD HH:MM:SS"')

