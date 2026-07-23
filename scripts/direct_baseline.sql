WITH latest AS (
    SELECT MAX(timestamp) AS latest_timestamp
    FROM sensor_readings
    WHERE trigger IN ('timer', 'button')
),
live AS (
    SELECT *
    FROM sensor_readings
    WHERE trigger IN ('timer', 'button')
      AND datetime(timestamp) >= datetime((SELECT latest_timestamp FROM latest), '-24 hours')
),
timer_rows AS (
    SELECT
        timestamp,
        unixepoch(timestamp)
          - unixepoch(LAG(timestamp) OVER (ORDER BY timestamp, id)) AS interval_sec
    FROM live
    WHERE trigger = 'timer'
),
timer_intervals AS (
    SELECT interval_sec
    FROM timer_rows
    WHERE interval_sec IS NOT NULL
)
SELECT json_object(
    'generated_at', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
    'backend', 'direct',
    'window', json_object(
        'start', MIN(timestamp),
        'end', MAX(timestamp),
        'duration_hours', ROUND(
            (julianday(MAX(timestamp)) - julianday(MIN(timestamp))) * 24.0,
            3
        )
    ),
    'records', json_object(
        'total', COUNT(*),
        'timer', SUM(trigger = 'timer'),
        'button', SUM(trigger = 'button')
    ),
    'timer_interval_sec', json_object(
        'samples', (SELECT COUNT(*) FROM timer_intervals),
        'average', (SELECT ROUND(AVG(interval_sec), 3) FROM timer_intervals),
        'minimum', (SELECT MIN(interval_sec) FROM timer_intervals),
        'maximum', (SELECT MAX(interval_sec) FROM timer_intervals),
        'outside_10s_plus_minus_0_5s', (
            SELECT SUM(ABS(interval_sec - 10.0) > 0.5) FROM timer_intervals
        )
    ),
    'sensor_valid_records', json_object(
        'light', SUM(light_raw IS NOT NULL AND light_voltage IS NOT NULL),
        'sound', SUM(sound_raw IS NOT NULL),
        'joystick', SUM(joystick_x IS NOT NULL AND joystick_y IS NOT NULL),
        'potentiometer', SUM(potentiometer_percent IS NOT NULL),
        'dht22', SUM(temp IS NOT NULL AND hum IS NOT NULL),
        'bme280', SUM(pressure IS NOT NULL),
        'mh_z19c', SUM(co2 IS NOT NULL)
    ),
    'sensor_ranges', json_object(
        'dht22_temp', json_object(
            'min', ROUND(MIN(CAST(temp AS REAL)), 3),
            'max', ROUND(MAX(CAST(temp AS REAL)), 3),
            'avg', ROUND(AVG(CAST(temp AS REAL)), 3)
        ),
        'dht22_humidity', json_object(
            'min', ROUND(MIN(CAST(hum AS REAL)), 3),
            'max', ROUND(MAX(CAST(hum AS REAL)), 3),
            'avg', ROUND(AVG(CAST(hum AS REAL)), 3)
        ),
        'pressure', json_object(
            'min', ROUND(MIN(CAST(pressure AS REAL)), 3),
            'max', ROUND(MAX(CAST(pressure AS REAL)), 3),
            'avg', ROUND(AVG(CAST(pressure AS REAL)), 3)
        ),
        'co2', json_object(
            'min', ROUND(MIN(CAST(co2 AS REAL)), 3),
            'max', ROUND(MAX(CAST(co2 AS REAL)), 3),
            'avg', ROUND(AVG(CAST(co2 AS REAL)), 3)
        )
    )
)
FROM live;
