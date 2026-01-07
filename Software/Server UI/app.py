"""
ReSonde Dashboard - Multi-Receiver Server
Receives telemetry from multiple ESP32 receivers via HTTP API
https://dashboard.resonde.de
"""

# IMPORTANT: gevent monkey patching must happen BEFORE other imports
from gevent import monkey
monkey.patch_all()

import os
import time
import json
from pathlib import Path
from datetime import datetime
from collections import deque

import numpy as np
import pandas as pd

from flask import Flask, render_template, jsonify, request, send_file, send_from_directory
from flask_socketio import SocketIO, emit

# === CONFIGURATION ===
GROUND_PRESSURE = 1013.25  # hPa - default sea level pressure
DATA_DIR = Path("data")

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'radiosonde_server_secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# In-memory state per sonde (keyed by serial number)
sonde_state = {}  # {sn: {'last_pressure': float, 'last_altitude': float, 'last_packet_time': float}}


# === PHYSICS CALCULATIONS ===

def calculate_exact_pressure(z_current, z_prev, p_prev, temp_c, rh):
    """Calculates pressure using the Hypsometric Equation."""
    g = 9.80665
    Rd = 287.05
    temp_k = temp_c + 273.15
    rh_adj = max(rh, 0.1)
    e = 6.112 * np.exp((17.67 * temp_c) / (temp_c + 243.5)) * (rh_adj / 100.0)
    virtual_temp_k = temp_k / (1 - (e / p_prev) * (1 - 0.622))
    dz = z_current - z_prev
    p_current = p_prev * np.exp(-(g * dz) / (Rd * virtual_temp_k))
    return p_current


def calculate_dewpoint(temp_c, rh):
    """Calculate dewpoint temperature."""
    if rh <= 0.1:
        rh = 0.1
    b = 17.62
    c = 243.12
    gamma = np.log(rh / 100.0) + (b * temp_c) / (c + temp_c)
    return (c * gamma) / (b - gamma)


def calculate_mixing_ratio(temp_c, pressure, rh):
    """Calculate mixing ratio in g/kg."""
    es = 6.112 * np.exp((17.67 * temp_c) / (temp_c + 243.5))
    e = es * (rh / 100.0)
    w = 0.622 * e / (pressure - e) * 1000
    return w


def calculate_theta(temp_c, pressure):
    """Calculate potential temperature (K)."""
    temp_k = temp_c + 273.15
    return temp_k * (1000.0 / pressure) ** 0.286


def calculate_theta_e(temp_c, pressure, rh):
    """Calculate equivalent potential temperature (K)."""
    temp_k = temp_c + 273.15
    w = calculate_mixing_ratio(temp_c, pressure, rh) / 1000
    theta = temp_k * (1000.0 / pressure) ** 0.2854
    return theta * np.exp((2.5e6 * w) / (1004 * temp_k))


# === DATA MANAGEMENT ===

def get_sonde_folder(sn):
    """Get or create folder for a sonde."""
    folder = DATA_DIR / str(sn)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_csv_path(sn):
    """Get CSV file path for a sonde."""
    return get_sonde_folder(sn) / "processed_data.csv"


def get_skewt_path(sn):
    """Get Skew-T image path for a sonde."""
    return get_sonde_folder(sn) / "skewt.png"


def save_processed_data(sn, data):
    """Append processed data to sonde's CSV file."""
    csv_path = get_csv_path(sn)
    columns = [
        'timestamp', 'packet_counter', 'unix_time',
        'lat', 'lon', 'alt_m', 'vspeed_ms', 'espeed_ms', 'nspeed_ms',
        'satellites', 'temp_c', 'rh_percent', 'battery_v', 'rssi_dbm',
        'pressure_hpa', 'dewpoint_c', 'mixing_ratio', 'theta', 'theta_e'
    ]
    
    row = [
        data['timestamp'],
        data['packet_counter'],
        data['unix_time'],
        data['lat'],
        data['lon'],
        data['alt_m'],
        data['vspeed_ms'],
        data['espeed_ms'],
        data['nspeed_ms'],
        data['satellites'],
        data['temp_c'],
        data['rh_percent'],
        data['battery_v'],
        data['rssi_dbm'],
        data['pressure_hpa'],
        data['dewpoint_c'],
        data['mixing_ratio'],
        data['theta'],
        data['theta_e']
    ]
    
    df = pd.DataFrame([row], columns=columns)
    file_exists = csv_path.exists()
    df.to_csv(csv_path, mode='a', index=False, header=not file_exists)


def load_sonde_data(sn):
    """Load all data for a sonde from CSV."""
    csv_path = get_csv_path(sn)
    if not csv_path.exists():
        return None
    return pd.read_csv(csv_path)


def get_all_sondes():
    """Get list of all known sondes."""
    if not DATA_DIR.exists():
        return []
    sondes = []
    for folder in DATA_DIR.iterdir():
        if folder.is_dir() and folder.name.isdigit():
            csv_path = folder / "processed_data.csv"
            if csv_path.exists():
                df = pd.read_csv(csv_path)
                sondes.append({
                    'sn': int(folder.name),
                    'packet_count': len(df),
                    'last_update': df['timestamp'].iloc[-1] if len(df) > 0 else None
                })
    return sorted(sondes, key=lambda x: x['sn'])


# === DATA INGESTION ===

def process_upload(raw_data):
    """Process incoming telemetry from ESP32."""
    sn = int(raw_data['sn'])
    
    # Initialize sonde state if new
    if sn not in sonde_state:
        sonde_state[sn] = {
            'last_pressure': GROUND_PRESSURE,
            'last_altitude': 0,
            'recent_packets': deque(maxlen=50)  # Track last 50 packets for deduplication
        }
    
    state = sonde_state[sn]
    
    # Parse raw values (same conversions as local version)
    packet_counter = int(raw_data['counter'])
    
    # Deduplication: Check if we've already processed this packet
    if packet_counter in state['recent_packets']:
        print(f"[SN {sn}] Duplicate packet #{packet_counter} ignored")
        return None
        
    state['recent_packets'].append(packet_counter)
    
    unix_time = int(raw_data['time'])
    lat = float(raw_data['lat']) * 1e-7
    lon = float(raw_data['lon']) * 1e-7
    alt_m = float(raw_data['alt']) / 1000.0
    
    vspeed_ms = -float(raw_data['vSpeed']) / 100.0
    espeed_ms = float(raw_data['eSpeed']) / 100.0
    nspeed_ms = float(raw_data['nSpeed']) / 100.0
    
    satellites = int(raw_data['sats'])
    temp_c = float(raw_data['temp']) / 320.0
    rh_percent = float(raw_data['rh']) / 2.0
    battery_v = (float(raw_data['battery']) / 255.0) * 3.3
    rssi_dbm = float(raw_data['rssi'])
    
    # Calculate pressure
    if state['last_altitude'] == 0:
        pressure_hpa = GROUND_PRESSURE
    else:
        pressure_hpa = calculate_exact_pressure(
            alt_m, state['last_altitude'], state['last_pressure'], temp_c, rh_percent
        )
    
    state['last_pressure'] = pressure_hpa
    state['last_altitude'] = alt_m
    
    # Calculate derived values
    dewpoint_c = calculate_dewpoint(temp_c, rh_percent)
    mixing_ratio = calculate_mixing_ratio(temp_c, pressure_hpa, rh_percent)
    theta = calculate_theta(temp_c, pressure_hpa)
    theta_e = calculate_theta_e(temp_c, pressure_hpa, rh_percent)
    
    timestamp = datetime.now().isoformat()
    
    processed = {
        'serial_number': sn,
        'packet_counter': packet_counter,
        'unix_time': unix_time,
        'timestamp': timestamp,
        'lat': lat,
        'lon': lon,
        'alt_m': alt_m,
        'vspeed_ms': vspeed_ms,
        'espeed_ms': espeed_ms,
        'nspeed_ms': nspeed_ms,
        'satellites': satellites,
        'temp_c': temp_c,
        'rh_percent': rh_percent,
        'battery_v': battery_v,
        'rssi_dbm': rssi_dbm,
        'pressure_hpa': pressure_hpa,
        'dewpoint_c': dewpoint_c,
        'mixing_ratio': mixing_ratio,
        'theta': theta,
        'theta_e': theta_e
    }
    
    # Save to CSV
    save_processed_data(sn, processed)
    
    return processed


# === SKEW-T GENERATION ===

def generate_skewt(sn):
    """Generate Skew-T diagram for a sonde."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from metpy.plots import SkewT
    from metpy.units import units
    
    csv_path = get_csv_path(sn)
    skewt_path = get_skewt_path(sn)
    
    # Optimization: Check if Skew-T is already up to date
    if csv_path.exists() and skewt_path.exists():
        if skewt_path.stat().st_mtime >= csv_path.stat().st_mtime:
            return str(skewt_path)

    df = load_sonde_data(sn)
    if df is None or len(df) < 5:
        return None
    
    # Filter first 5 packets
    df = df.iloc[5:]
    if len(df) < 2:
        return None
    
    try:
        fig = plt.figure(figsize=(10, 10), dpi=100)
        skew = SkewT(fig, rotation=45)
        
        pressure = df['pressure_hpa'].values * units.hPa
        temperature = df['temp_c'].values * units.degC
        dewpoint = df['dewpoint_c'].values * units.degC
        u_wind = df['espeed_ms'].values * units('m/s')
        v_wind = df['nspeed_ms'].values * units('m/s')
        
        skew.ax.set_ylim(1050, max(100, pressure.magnitude.min() - 50))
        skew.ax.set_xlim(-60, 40)
        
        skew.plot_dry_adiabats(t0=np.arange(233, 533, 10) * units.K, alpha=0.25, color='orangered')
        skew.plot_moist_adiabats(color='green', alpha=0.2)
        skew.plot_mixing_lines(color='blue', alpha=0.2)
        
        skew.plot(pressure, temperature, 'r', linewidth=2.5, label='Temperature')
        skew.plot(pressure, dewpoint, 'g', linewidth=2.5, label='Dewpoint')
        
        # Wind barbs
        n_points = len(pressure)
        if n_points > 0:
            step = max(1, n_points // 15)
            barb_indices = slice(0, None, step)
            skew.plot_barbs(pressure[barb_indices], u_wind[barb_indices], v_wind[barb_indices], length=6, linewidth=0.8)
        
        skew.ax.set_title(f'Skew-T Log-P - Sonde {sn}', fontsize=14, fontweight='bold')
        skew.ax.legend(loc='upper left')
        
        filepath = get_skewt_path(sn)
        fig.savefig(filepath, dpi=100, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        
        return str(filepath)
    except Exception as e:
        print(f"Error generating Skew-T: {e}")
        return None


# === FLASK ROUTES ===

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)


# === API ROUTES ===

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """Receive telemetry from ESP32 receivers."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data'}), 400
        
        required = ['sn', 'counter', 'time', 'lat', 'lon', 'alt', 'vSpeed', 'eSpeed', 'nSpeed', 'sats', 'temp', 'rh', 'battery', 'rssi']
        for field in required:
            if field not in data:
                return jsonify({'error': f'Missing field: {field}'}), 400
        
        processed = process_upload(data)
        
        if processed is None:
            return jsonify({'success': True, 'status': 'duplicate'}), 200
        
        # Broadcast to all connected WebSocket clients
        socketio.emit('telemetry', processed)
        
        print(f"[SN {processed['serial_number']}] Pkt #{processed['packet_counter']} | Alt: {processed['alt_m']:.1f}m")
        
        return jsonify({'success': True, 'processed': processed}), 200
        
    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/sondes')
def api_sondes():
    """Get list of all known sondes."""
    return jsonify(get_all_sondes())


@app.route('/api/sonde/<int:sn>/data')
def api_sonde_data(sn):
    """Get all processed data for a sonde."""
    df = load_sonde_data(sn)
    if df is None:
        return jsonify({'error': 'Sonde not found'}), 404
    return jsonify(df.to_dict(orient='records'))


@app.route('/api/sonde/<int:sn>/track')
def api_sonde_track(sn):
    """Get GPS track for map."""
    df = load_sonde_data(sn)
    if df is None:
        return jsonify({'error': 'Sonde not found'}), 404
    
    # Filter out invalid coordinates
    df = df[(df['lat'].abs() > 0.1) | (df['lon'].abs() > 0.1)]
    track = df[['lat', 'lon']].values.tolist()
    return jsonify(track)


@app.route('/api/sonde/<int:sn>/latest')
def api_sonde_latest(sn):
    """Get latest telemetry for a sonde."""
    df = load_sonde_data(sn)
    if df is None or len(df) == 0:
        return jsonify({'error': 'Sonde not found'}), 404
    return jsonify(df.iloc[-1].to_dict())


@app.route('/api/sonde/<int:sn>/skewt')
def api_sonde_skewt(sn):
    """Generate and return Skew-T image."""
    result = generate_skewt(sn)
    if result is None:
        return jsonify({'error': 'Not enough data for Skew-T'}), 404
    return send_file(result, mimetype='image/png')


@app.route('/api/sonde/<int:sn>/download/csv')
def api_download_csv(sn):
    """Download processed CSV for a sonde."""
    csv_path = get_csv_path(sn)
    if not csv_path.exists():
        return jsonify({'error': 'Sonde not found'}), 404
    return send_file(csv_path, mimetype='text/csv', as_attachment=True, download_name=f'sonde_{sn}_data.csv')


@app.route('/api/sonde/<int:sn>/download/skewt')
def api_download_skewt(sn):
    """Download Skew-T image for a sonde."""
    result = generate_skewt(sn)
    if result is None:
        return jsonify({'error': 'Not enough data for Skew-T'}), 404
    return send_file(result, mimetype='image/png', as_attachment=True, download_name=f'sonde_{sn}_skewt.png')


# === SOCKETIO EVENTS ===

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('status', {'connected': True, 'sondes': get_all_sondes()})


@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')


@socketio.on('subscribe_sonde')
def handle_subscribe(data):
    """Client wants updates for a specific sonde."""
    sn = data.get('sn')
    print(f'Client subscribed to sonde {sn}')


# === MAIN ===

if __name__ == '__main__':
    DATA_DIR.mkdir(exist_ok=True)
    
    print("Starting Radiosonde Multi-Receiver Server...")
    print("Open http://localhost:5000 in your browser")
    print("ESP32 upload endpoint: POST http://localhost:5000/api/upload")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
