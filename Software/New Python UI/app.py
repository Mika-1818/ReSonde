"""
Radiosonde Ground Station - Flask Backend
Real-time telemetry visualization with Skew-T Log-P and Leaflet map
"""

# IMPORTANT: gevent monkey patching must happen BEFORE other imports
from gevent import monkey
monkey.patch_all()

import os
import datetime
import time
import json
from pathlib import Path

import serial
import numpy as np
import pandas as pd

from flask import Flask, render_template, jsonify, send_from_directory
from flask_socketio import SocketIO, emit

# === CONFIGURATION ===
SERIAL_PORT = "/dev/ttyUSB0"  # Change to 'COMx' on Windows
BAUD_RATE = 115200
GROUND_PRESSURE = 1034.0  # hPa - adjust for your launch site

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'radiosonde_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Global state
telemetry_history = {
    'lat': [], 'lon': [], 'alt': [],
    'temp': [], 'rh': [], 'pressure': [], 'dewpoint': [],
    'u_wind': [], 'v_wind': [],  # Wind components for wind barbs
    'packet_numbers': [],  # Track packet numbers
    'timestamps': []
}
current_session = {
    'serial_number': None,
    'launch_date': None,
    'data_folder': None,
    'raw_csv': None,
    'processed_csv': None
}
last_packet_time = None
serial_connected = False
serial_thread = None
last_pressure = GROUND_PRESSURE
last_altitude = 0


# === PHYSICS CALCULATIONS ===

def calculate_exact_pressure(z_current, z_prev, p_prev, temp_c, rh):
    """Calculates pressure using the Hypsometric Equation and Radiosonde data."""
    g = 9.80665  # Gravity (m/s^2)
    Rd = 287.05  # Specific gas constant for dry air (J/kg·K)

    # Convert Temp to Kelvin
    temp_k = temp_c + 273.15

    # Water Vapor Pressure (e) - Clamp RH to avoid math errors at 0%
    rh_adj = max(rh, 0.1)
    e = 6.112 * np.exp((17.67 * temp_c) / (temp_c + 243.5)) * (rh_adj / 100.0)

    # Virtual Temperature (Tv) - Accounts for moisture making air less dense
    virtual_temp_k = temp_k / (1 - (e / p_prev) * (1 - 0.622))

    # Hypsometric Equation
    dz = z_current - z_prev
    p_current = p_prev * np.exp(-(g * dz) / (Rd * virtual_temp_k))

    return p_current


def calculate_dewpoint(temp_c, rh):
    """Calculate dewpoint temperature from temperature and relative humidity."""
    if rh <= 0.1:
        rh = 0.1
    b = 17.62
    c = 243.12
    gamma = np.log(rh / 100.0) + (b * temp_c) / (c + temp_c)
    return (c * gamma) / (b - gamma)


def calculate_mixing_ratio(temp_c, pressure, rh):
    """Calculate mixing ratio in g/kg."""
    # Saturation vapor pressure (hPa)
    es = 6.112 * np.exp((17.67 * temp_c) / (temp_c + 243.5))
    # Actual vapor pressure
    e = es * (rh / 100.0)
    # Mixing ratio
    w = 0.622 * e / (pressure - e) * 1000  # g/kg
    return w


def calculate_theta(temp_c, pressure):
    """Calculate potential temperature (K)."""
    temp_k = temp_c + 273.15
    theta = temp_k * (1000.0 / pressure) ** 0.286
    return theta


def calculate_theta_e(temp_c, pressure, rh):
    """Calculate equivalent potential temperature (K)."""
    temp_k = temp_c + 273.15
    w = calculate_mixing_ratio(temp_c, pressure, rh) / 1000  # Convert to kg/kg
    
    # Bolton's formula for theta_e
    theta = temp_k * (1000.0 / pressure) ** 0.2854
    theta_e = theta * np.exp((2.5e6 * w) / (1004 * temp_k))
    return theta_e


# === DATA MANAGEMENT ===

def ensure_data_folder(serial_number):
    """Create data folder for this radiosonde session."""
    global current_session
    
    if current_session['serial_number'] != serial_number:
        launch_date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{serial_number}_{launch_date}"
        data_folder = Path("data") / folder_name
        data_folder.mkdir(parents=True, exist_ok=True)
        
        current_session['serial_number'] = serial_number
        current_session['launch_date'] = launch_date
        current_session['data_folder'] = str(data_folder)
        current_session['raw_csv'] = str(data_folder / "raw_data.csv")
        current_session['processed_csv'] = str(data_folder / "processed_data.csv")
        
        print(f"Created data folder: {data_folder}")
    
    return current_session['data_folder']


def save_raw_data(parts, timestamp):
    """Save raw serial data to CSV."""
    if current_session['raw_csv'] is None:
        return
    
    raw_file = current_session['raw_csv']
    columns = ['timestamp', 'SN', 'counter', 'time', 'lat', 'lon', 'alt', 
               'vSpeed', 'eSpeed', 'nSpeed', 'sats', 'temp', 'rh', 'battery', 'rssi']
    
    row = [timestamp] + parts
    df = pd.DataFrame([row], columns=columns)
    
    file_exists = os.path.exists(raw_file)
    df.to_csv(raw_file, mode='a', index=False, header=not file_exists)


def save_processed_data(data, timestamp):
    """Save processed telemetry data to CSV."""
    if current_session['processed_csv'] is None:
        return
    
    processed_file = current_session['processed_csv']
    columns = ['timestamp', 'serial_number', 'packet_counter', 'unix_time',
               'lat', 'lon', 'alt_m', 'vspeed_ms', 'espeed_ms', 'nspeed_ms',
               'satellites', 'temp_c', 'rh_percent', 'battery_v', 'rssi_dbm',
               'pressure_hpa', 'dewpoint_c', 'mixing_ratio', 'theta', 'theta_e']
    
    row = [timestamp] + [data.get(col.replace('_', ''), data.get(col, '')) for col in columns[1:]]
    
    # Build row properly
    row = [
        timestamp,
        data['serial_number'],
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
    file_exists = os.path.exists(processed_file)
    df.to_csv(processed_file, mode='a', index=False, header=not file_exists)


# === SERIAL PORT HANDLER ===

def parse_packet(line):
    """Parse incoming serial packet according to radiosonde format."""
    global last_pressure, last_altitude, last_packet_time
    
    parts = line.strip().split(',')
    if len(parts) != 14:
        print(f"Invalid packet length: {len(parts)} (expected 14)")
        return None
    
    try:
        # Parse raw values
        serial_number = int(parts[0].strip())
        packet_counter = int(parts[1].strip())
        unix_time = int(parts[2].strip())
        lat = float(parts[3].strip()) * 1e-7  # Convert to degrees
        lon = float(parts[4].strip()) * 1e-7  # Convert to degrees
        alt_mm = int(parts[5].strip())
        alt_m = alt_mm / 1000.0  # Convert mm to m
        
        # Speeds (invert vSpeed, convert from cm/s to m/s)
        vspeed_cms = int(parts[6].strip())
        vspeed_ms = -vspeed_cms / 100.0  # Inverted and cm/s to m/s
        espeed_ms = int(parts[7].strip()) / 100.0  # cm/s to m/s
        nspeed_ms = int(parts[8].strip()) / 100.0  # cm/s to m/s
        
        satellites = int(parts[9].strip())
        
        # Temperature (divide by 320 for °C)
        temp_raw = int(parts[10].strip())
        temp_c = temp_raw / 320.0
        
        # Relative humidity (divide by 2 for %)
        rh_raw = int(parts[11].strip())
        rh_percent = rh_raw / 2.0
        
        # Battery (0-255 maps to 0-3.3V)
        battery_raw = int(parts[12].strip())
        battery_v = (battery_raw / 255.0) * 3.3
        
        # RSSI in dBm (may come as float from radio.getRSSI())
        rssi_dbm = int(float(parts[13].strip()))
        
        # Ensure data folder exists
        ensure_data_folder(serial_number)
        
        # Save raw data
        timestamp = datetime.datetime.now().isoformat()
        save_raw_data(parts, timestamp)
        
        # Calculate pressure
        if last_pressure is None or last_altitude == 0:
            pressure_hpa = GROUND_PRESSURE
        else:
            pressure_hpa = calculate_exact_pressure(
                alt_m, last_altitude, last_pressure, temp_c, rh_percent
            )
        
        last_pressure = pressure_hpa
        last_altitude = alt_m
        
        # Calculate derived values for Skew-T
        dewpoint_c = calculate_dewpoint(temp_c, rh_percent)
        mixing_ratio = calculate_mixing_ratio(temp_c, pressure_hpa, rh_percent)
        theta = calculate_theta(temp_c, pressure_hpa)
        theta_e = calculate_theta_e(temp_c, pressure_hpa, rh_percent)
        
        # Build processed data dict
        processed_data = {
            'serial_number': serial_number,
            'packet_counter': packet_counter,
            'unix_time': unix_time,
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
        
        # Save processed data
        save_processed_data(processed_data, timestamp)
        
        # Update history for Skew-T (include wind components)
        telemetry_history['lat'].append(lat)
        telemetry_history['lon'].append(lon)
        telemetry_history['alt'].append(alt_m)
        telemetry_history['temp'].append(temp_c)
        telemetry_history['rh'].append(rh_percent)
        telemetry_history['pressure'].append(pressure_hpa)
        telemetry_history['dewpoint'].append(dewpoint_c)
        telemetry_history['u_wind'].append(espeed_ms)  # Eastward = u component
        telemetry_history['v_wind'].append(nspeed_ms)  # Northward = v component
        telemetry_history['packet_numbers'].append(packet_counter)
        telemetry_history['timestamps'].append(timestamp)
        
        last_packet_time = time.time()
        
        return processed_data
        
    except (ValueError, IndexError) as e:
        print(f"Parse error: {e} - Line: {line}")
        return None


def serial_reader_thread():
    """Background thread to read serial port data."""
    global serial_connected
    
    print(f"Attempting to connect to {SERIAL_PORT}...")
    socketio.emit('serial_status', {'connected': False, 'connecting': True})
    
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        serial_connected = True
        socketio.emit('serial_status', {'connected': True, 'connecting': False})
        print(f"Connected to {SERIAL_PORT}")
        ser.reset_input_buffer()
        
        consecutive_errors = 0
        while True:
            try:
                # Check if port is still open
                if not ser.is_open:
                    raise serial.SerialException("Port closed")
                
                line = ser.readline().decode('utf-8', errors='replace').strip()
                consecutive_errors = 0  # Reset on successful read
                
                if line:
                    data = parse_packet(line)
                    if data:
                        # Emit to all connected WebSocket clients
                        socketio.emit('telemetry', data)
                        print(f"Packet #{data['packet_counter']} | Alt: {data['alt_m']:.1f}m | T: {data['temp_c']:.1f}°C")
                        
            except (serial.SerialException, OSError) as e:
                # Serial port disconnected
                print(f"Serial port disconnected: {e}")
                serial_connected = False
                socketio.emit('serial_status', {'connected': False, 'connecting': False, 'error': str(e)})
                try:
                    ser.close()
                except:
                    pass
                return  # Exit thread, user can click to reconnect
                
            except Exception as e:
                consecutive_errors += 1
                print(f"Serial read error ({consecutive_errors}): {e}")
                
                # If too many consecutive errors, assume disconnected
                if consecutive_errors >= 5:
                    print("Too many errors, assuming USB disconnected")
                    serial_connected = False
                    socketio.emit('serial_status', {'connected': False, 'connecting': False, 'error': 'Too many read errors'})
                    try:
                        ser.close()
                    except:
                        pass
                    return
                    
                time.sleep(1)
                
    except Exception as e:
        print(f"Could not open serial port: {e}")
        serial_connected = False
        socketio.emit('serial_status', {'connected': False, 'connecting': False, 'error': str(e)})


def start_serial_thread():
    """Start or restart the serial reader thread."""
    global serial_connected
    
    # If already connected, don't restart
    if serial_connected:
        return {'success': False, 'message': 'Already connected'}
    
    serial_connected = False
    # Use socketio.start_background_task for eventlet compatibility
    socketio.start_background_task(serial_reader_thread)
    return {'success': True, 'message': 'Attempting to connect...'}


# === SKEW-T GENERATION ===

def generate_skewt():
    """Generate Skew-T Log-P diagram and save to static folder."""
    import matplotlib
    matplotlib.use('Agg')  # Non-GUI backend
    import matplotlib.pyplot as plt
    from metpy.plots import SkewT
    from metpy.units import units
    
    # Filter to only include packets after packet #5
    packet_nums = np.array(telemetry_history['packet_numbers'])
    mask = packet_nums > 5
    
    if np.sum(mask) < 2:
        return None
    
    try:
        # Create figure
        fig = plt.figure(figsize=(10, 10), dpi=100)
        skew = SkewT(fig, rotation=45)
        
        # Filter data using mask (only packets > 5)
        pressure_data = np.array(telemetry_history['pressure'])[mask]
        temp_data = np.array(telemetry_history['temp'])[mask]
        dewpoint_data = np.array(telemetry_history['dewpoint'])[mask]
        u_wind_data = np.array(telemetry_history['u_wind'])[mask]
        v_wind_data = np.array(telemetry_history['v_wind'])[mask]
        
        # Set up axes
        skew.ax.set_ylim(1050, max(100, min(pressure_data) - 50))
        skew.ax.set_xlim(-60, 40)
        
        # Plot reference lines
        skew.plot_dry_adiabats(t0=np.arange(233, 533, 10) * units.K, alpha=0.25, color='orangered')
        skew.plot_moist_adiabats(color='green', alpha=0.2)
        skew.plot_mixing_lines(color='blue', alpha=0.2)
        
        # Plot temperature and dewpoint profiles
        pressure = pressure_data * units.hPa
        temperature = temp_data * units.degC
        dewpoint = dewpoint_data * units.degC
        
        skew.plot(pressure, temperature, 'r', linewidth=2.5, label='Temperature')
        skew.plot(pressure, dewpoint, 'g', linewidth=2.5, label='Dewpoint')
        
        # Add wind barbs (sample every 10 points to avoid clutter)
        u_wind = u_wind_data * units('m/s')
        v_wind = v_wind_data * units('m/s')
        
        # Sample wind data for barbs (every 10th point or at least 5 barbs)
        n_points = len(pressure)
        if n_points > 0:
            step = max(1, n_points // 15)  # Aim for ~15 barbs
            barb_indices = slice(0, None, step)
            skew.plot_barbs(
                pressure[barb_indices], 
                u_wind[barb_indices], 
                v_wind[barb_indices],
                length=6,
                linewidth=0.8
            )
        
        # Add title and legend
        skew.ax.set_title('Live Skew-T Log-P Sounding', fontsize=14, fontweight='bold')
        skew.ax.legend(loc='upper left')
        
        # Ensure static folder exists
        static_folder = Path('static')
        static_folder.mkdir(exist_ok=True)
        
        # Save figure
        filepath = static_folder / 'skewt.png'
        fig.savefig(filepath, dpi=100, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        
        return str(filepath)
        
    except Exception as e:
        print(f"Error generating Skew-T: {e}")
        import traceback
        traceback.print_exc()
        return None


def skewt_generator_thread():
    """Background thread to regenerate Skew-T every 30 seconds."""
    while True:
        time.sleep(30)
        if len(telemetry_history['pressure']) >= 2:
            print("Regenerating Skew-T diagram...")
            result = generate_skewt()
            if result:
                socketio.emit('skewt_updated', {'path': '/static/skewt.png', 'timestamp': time.time()})
                print("Skew-T updated successfully")


# === FLASK ROUTES ===

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)


@app.route('/api/history')
def get_history():
    """Get telemetry history for initial page load."""
    return jsonify({
        'track': list(zip(telemetry_history['lat'], telemetry_history['lon'])),
        'skewt_data': {
            'pressure': telemetry_history['pressure'],
            'temp': telemetry_history['temp'],
            'dewpoint': telemetry_history['dewpoint']
        }
    })


@app.route('/api/status')
def get_status():
    """Get current system status."""
    return jsonify({
        'serial_connected': serial_connected,
        'packets_received': len(telemetry_history['timestamps']),
        'last_packet_time': last_packet_time,
        'session': current_session
    })


# === SOCKETIO EVENTS ===

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    # Send current status
    emit('status', {
        'serial_connected': serial_connected,
        'packets_received': len(telemetry_history['timestamps'])
    })
    # Send serial status
    emit('serial_status', {'connected': serial_connected, 'connecting': False})


@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')


@socketio.on('reconnect_serial')
def handle_reconnect_serial():
    """Handle request to reconnect serial port."""
    print('Client requested serial reconnect')
    result = start_serial_thread()
    emit('serial_reconnect_result', result)


# === MAIN ===

if __name__ == '__main__':
    # Ensure directories exist
    Path('static').mkdir(exist_ok=True)
    Path('data').mkdir(exist_ok=True)
    
    print("Starting Radiosonde Ground Station...")
    print("Open http://localhost:5000 in your browser")
    
    # Start background tasks using socketio (eventlet-compatible)
    socketio.start_background_task(serial_reader_thread)
    socketio.start_background_task(skewt_generator_thread)
    
    # Run Flask-SocketIO server
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
