import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import datetime
import os
import serial
import pandas as pd
import numpy as np

# Plotting Libraries
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Mapping & Meteorology
import cartopy.crs as ccrs
import cartopy.io.img_tiles as cimgt
from metpy.plots import SkewT
from metpy.units import units

# --- CONFIGURATION ---
SERIAL_PORT = "/dev/ttyUSB0"  # Change to 'COMx' on Windows
BAUD_RATE = 115200

GROUND_PRESSURE = 1034.0

TIMESTAMP_STR = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
RAW_CSV_FILE = f"raw_{TIMESTAMP_STR}.csv"
CLEAN_CSV_FILE = f"flight_data_{TIMESTAMP_STR}.csv"

COLUMNS = [
    'SystemTimestamp', 'SerialNumber', 'PacketNumber',
    'hour', 'minute', 'second', 'latitude', 'longitude', 'altitude',
    'verticalSpeed', 'eastwardSpeed', 'northwardSpeed', 'satellites',
    'temperature', 'relativHumidity', 'battery'
]

data_queue = queue.Queue()
app_running = True

# --- PHYSICS & CALCULATIONS ---
def calculate_pressure_isa(altitude_m):
    P0 = 1013.25
    T0 = 288.15
    g = 9.80665
    M = 0.0289644
    R = 8.3144598
    L = 0.0065
    exponent = (g * M) / (R * L)
    base = 1 - ((L * altitude_m) / T0)
    if isinstance(base, float) and base < 0:
        base = 0.001
    return P0 * (base ** exponent)


def calculate_exact_pressure(z_current, z_prev, p_prev, temp_c, rh):
    """Calculates pressure using the Hypsometric Equation and Radiosonde data."""
    g = 9.80665  # Gravity (m/s^2)
    Rd = 287.05  # Specific gas constant for dry air (J/kg·K)

    # 1. Convert Temp to Kelvin
    temp_k = temp_c + 273.15

    # 2. Water Vapor Pressure (e)
    # Clamp RH to avoid math errors at 0%
    rh_adj = max(rh, 0.1)
    e = 6.112 * np.exp((17.67 * temp_c) / (temp_c + 243.5)) * (rh_adj / 100.0)

    # 3. Virtual Temperature (Tv)
    # Accounts for moisture making air less dense
    virtual_temp_k = temp_k / (1 - (e / p_prev) * (1 - 0.622))

    # 4. Hypsometric Equation
    dz = z_current - z_prev
    p_current = p_prev * np.exp(- (g * dz) / (Rd * virtual_temp_k))

    return p_current


def calculate_dewpoint(T, RH):
    if RH <= 0.1:
        RH = 0.1
    b = 17.62
    c = 243.12
    gamma = np.log(RH / 100.0) + (b * T) / (c + T)
    return (c * gamma) / (b - gamma)

# --- SERIAL WORKER THREAD ---
def serial_worker():
    global app_running

    last_z = None
    last_p = None

    print(f"Attempting connection to {SERIAL_PORT}...")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        print(f"Connected to {SERIAL_PORT}")
    except Exception as e:
        print(f"CRITICAL: Could not open serial port. {e}")
        return

    ser.reset_input_buffer()
    while app_running:
        try:
            line = ser.readline().decode("utf-8", errors='replace').strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) == 15:
                now = datetime.datetime.now()
                sys_time = now.strftime('%Y-%m-%d %H:%M:%S')

                # Save raw
                row_raw = [sys_time] + parts
                df_raw = pd.DataFrame([row_raw], columns=COLUMNS)
                df_raw.to_csv(RAW_CSV_FILE, mode='a', index=False, header=not os.path.exists(RAW_CSV_FILE))

                try:
                    lat = float(parts[5]) * 1e-7
                    lon = float(parts[6]) * 1e-7
                    alt_m = float(parts[7]) / 1000.0
                    v_spd = float(parts[8]) / 1000.0
                    temp_c = float(parts[12]) / 320.0
                    rh = float(parts[13]) / 2.0
                    batt = float(parts[14]) / 100.0

                    if last_p is None:
                        pressure = GROUND_PRESSURE
                    else:
                        pressure = calculate_exact_pressure(alt_m, last_z, last_p, temp_c, rh)

                    last_z = alt_m
                    last_p = pressure

                    # pressure = calculate_pressure_isa(alt_m)
                    dewpoint = calculate_dewpoint(temp_c, rh)

                    # Save clean
                    clean_row = [
                        sys_time, parts[0], parts[1], parts[2], parts[3], parts[4],
                        lat, lon, alt_m, v_spd, float(parts[9])/1000.0, float(parts[10])/1000.0, parts[11],
                        temp_c, rh, batt
                    ]
                    df_clean = pd.DataFrame([clean_row], columns=COLUMNS)
                    df_clean.to_csv(CLEAN_CSV_FILE, mode='a', index=False, header=not os.path.exists(CLEAN_CSV_FILE))

                    packet = {
                        "lat": lat, "lon": lon, "alt": alt_m,
                        "temp": temp_c, "dp": dewpoint, "press": pressure,
                        "rh": rh, "batt": batt, "v_spd": v_spd,
                        "sats": int(parts[11]), "pkt": parts[1]
                    }
                    data_queue.put(packet)
                    print(f"Packet #{parts[1]} | Alt: {alt_m:.1f}m | T: {temp_c:.1f}°C")
                except ValueError as ve:
                    print(f"Parse error: {ve}")
        except Exception as e:
            print(f"Serial error: {e}")

    ser.close()
    print("Serial thread stopped.")

# --- GUI APPLICATION ---
class GroundStationGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Radiosonde Ground Station")
        self.root.geometry("1400x900")

        style = ttk.Style()
        style.theme_use('clam')

        self.history = {
            "lat": [], "lon": [], "press": [], "temp": [], "dp": []
        }

        self._setup_layout()
        self._setup_map()
        self._setup_skewt()
        self._setup_labels()

        self.root.after(100, self.update_loop)

    def _setup_layout(self):
        self.top_frame = tk.Frame(self.root)
        self.top_frame.pack(fill=tk.BOTH, expand=True)

        self.map_frame = tk.Frame(self.top_frame, bg="gray")
        self.map_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)

        self.skew_container = tk.Frame(self.top_frame)
        self.skew_container.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=2, pady=2)

        self.skew_frame = tk.Frame(self.skew_container)
        self.skew_frame.pack(fill=tk.BOTH, expand=True)

        self.download_btn = tk.Button(
            self.skew_container,
            text="Download Skew-T as PNG",
            font=("Helvetica", 12, "bold"),
            bg="#2e7d7d",
            fg="white",
            command=self.download_skewt
        )
        self.download_btn.pack(pady=10)

        self.bottom_frame = tk.Frame(self.root, height=150, bg="#222")
        self.bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

    def download_skewt(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png")],
            title="Save Skew-T Plot"
        )
        if file_path:
            try:
                self.skew_fig.savefig(file_path, dpi=300, bbox_inches='tight')
                messagebox.showinfo("Saved", f"Skew-T saved successfully to:\n{file_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save image:\n{e}")

    def _setup_map(self):
        self.map_fig = plt.Figure(figsize=(6, 6), dpi=100)
        self.osm_tiles = cimgt.OSM()
        self.ax_map = self.map_fig.add_subplot(1, 1, 1, projection=self.osm_tiles.crs)
        self.ax_map.set_extent([6.0, 15.0, 47.0, 55.0], crs=ccrs.PlateCarree())
        self.ax_map.add_image(self.osm_tiles, 10)

        self.map_line, = self.ax_map.plot([], [], transform=ccrs.PlateCarree(),
                                          color='red', linewidth=2, marker='o', markersize=5)

        self.map_canvas = FigureCanvasTkAgg(self.map_fig, master=self.map_frame)
        self.map_canvas.draw()
        self.map_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _setup_skewt(self):
        self.skew_fig = plt.Figure(figsize=(6, 6), dpi=100)
        self.skew = SkewT(self.skew_fig, rotation=45)
        self.skew.ax.set_ylim(1050, 100)
        self.skew.ax.set_xlim(-50, 40)

        self.skew.plot_dry_adiabats(t0=np.arange(233, 533, 10) * units.K, alpha=0.2, color='orange')
        self.skew.plot_moist_adiabats(color='green', alpha=0.1)
        self.skew.plot_mixing_lines(color='blue', alpha=0.1)

        self.skew.ax.set_title("Live Skew-T Sounding")
        self.line_temp, = self.skew.ax.plot([], [], 'r', linewidth=3, label='Temperature')
        self.line_dp, = self.skew.ax.plot([], [], 'g', linewidth=3, label='Dewpoint')
        self.skew.ax.legend()

        self.skew_canvas = FigureCanvasTkAgg(self.skew_fig, master=self.skew_frame)
        self.skew_canvas.draw()
        self.skew_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _setup_labels(self):
        self.labels = {}
        self.label_frames = {}

        fields = [
            ("PACKET", "pkt", ""),
            ("TIME", "time", ""),
            ("BATTERY", "batt", "V"),
            ("SATELLITES", "sats", ""),
            ("LATITUDE", "lat", "°"),
            ("LONGITUDE", "lon", "°"),
            ("ALTITUDE", "alt", "m"),
            ("V-SPEED", "v_spd", "m/s"),
            ("PRESSURE", "press", "hPa"),
            ("TEMP", "temp", "°C"),
            ("HUMIDITY", "rh", "%"),
            ("DEWPOINT", "dp", "°C"),
        ]

        for i, (name, key, unit) in enumerate(fields):
            frame = tk.Frame(self.bottom_frame, bg="#333", relief=tk.RAISED, bd=2, padx=10, pady=10)
            frame.grid(row=0, column=i, sticky="nsew", padx=4, pady=8)
            self.bottom_frame.grid_columnconfigure(i, weight=1)

            tk.Label(frame, text=name, bg="#333", fg="#aaa", font=("Consolas", 9)).pack()
            value_lbl = tk.Label(frame, text="--", bg="#333", fg="white", font=("Consolas", 16, "bold"))
            value_lbl.pack(pady=(5, 0))

            self.labels[key] = (value_lbl, unit)
            self.label_frames[key] = frame

            if key == "time":
                self.time_lbl = value_lbl

    def update_loop(self):
        has_new_data = False
        latest_pkt = None

        while not data_queue.empty():
            latest_pkt = data_queue.get()
            has_new_data = True

            if abs(latest_pkt['lat']) > 0.1 and abs(latest_pkt['lon']) > 0.1:
                self.history['lat'].append(latest_pkt['lat'])
                self.history['lon'].append(latest_pkt['lon'])

            self.history['press'].append(latest_pkt['press'])
            self.history['temp'].append(latest_pkt['temp'])
            self.history['dp'].append(latest_pkt['dp'])

        if has_new_data and latest_pkt:
            # Update time
            self.time_lbl.config(text=datetime.datetime.now().strftime("%H:%M:%S"))

            # Update all labels
            for key, (lbl, unit) in self.labels.items():
                if key in latest_pkt:
                    val = latest_pkt[key]
                    if isinstance(val, float):
                        lbl.config(text=f"{val:.2f}{unit}")
                    else:
                        lbl.config(text=f"{val}{unit}")

            # Color coding
            sats = latest_pkt.get('sats', 0)
            batt = latest_pkt.get('batt', 0.0)
            v_spd = latest_pkt.get('v_spd', 0.0)

            # Satellites
            if sats > 9:
                self.label_frames['sats'].config(bg="#006400")
            elif sats > 5:
                self.label_frames['sats'].config(bg="#cccc00")
            else:
                self.label_frames['sats'].config(bg="#8b0000")

            # Battery
            if batt > 1.4:
                self.label_frames['batt'].config(bg="#006400")
            elif batt > 1.2:
                self.label_frames['batt'].config(bg="#cccc00")
            else:
                self.label_frames['batt'].config(bg="#8b0000")

            # Vertical speed
            if v_spd > 2:
                self.label_frames['v_spd'].config(bg="#006400")
            elif v_spd >= -1:
                self.label_frames['v_spd'].config(bg="#555555")
            else:
                self.label_frames['v_spd'].config(bg="#00008b")

            self.update_plots()

        self.root.after(1000, self.update_loop)

    def update_plots(self):
        # Map update
        if self.history['lat']:
            self.map_line.set_data(self.history['lon'], self.history['lat'])

            min_lat, max_lat = min(self.history['lat']), max(self.history['lat'])
            min_lon, max_lon = min(self.history['lon']), max(self.history['lon'])
            buffer = 0.03
            self.ax_map.set_extent(
                [min_lon - buffer, max_lon + buffer, min_lat - buffer, max_lat + buffer],
                crs=ccrs.PlateCarree()
            )
            self.map_canvas.draw_idle()

        # Skew-T update
        if self.history['press']:
            self.line_temp.set_data(self.history['temp'], self.history['press'])
            self.line_dp.set_data(self.history['dp'], self.history['press'])

            min_p = min(self.history['press'])
            min_p = max(min_p, 10)  # Don't go below 100 hPa
            self.skew.ax.set_ylim(1050, min_p - 10)
            self.skew_canvas.draw_idle()

# --- MAIN ---
def on_closing():
    global app_running
    app_running = False
    root.destroy()

if __name__ == "__main__":
    thread = threading.Thread(target=serial_worker, daemon=True)
    thread.start()

    root = tk.Tk()
    app = GroundStationGUI(root)
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()