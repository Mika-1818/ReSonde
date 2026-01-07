/**
 * Radiosonde Ground Station - Frontend JavaScript
 * Real-time telemetry visualization with WebSocket, Leaflet map, and Skew-T updates
 */

// ===========================
// Configuration
// ===========================
const CONFIG = {
    MAP_CENTER: [51.0, 10.0],  // Germany center - adjust as needed
    MAP_ZOOM: 6,
    SKEWT_REFRESH_INTERVAL: 30000,  // 30 seconds
    LAST_PACKET_UPDATE_INTERVAL: 1000  // 1 second
};

// ===========================
// State
// ===========================
let socket = null;
let map = null;
let flightTrack = null;
let flightPath = [];
let lastPacketTimestamp = null;
let currentMarker = null;
let serialConnected = false;

// ===========================
// Initialization
// ===========================
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    initSocket();
    initLastPacketTimer();
    initSkewTRefresh();
    initReconnectButton();
});

// ===========================
// Map Initialization
// ===========================
function initMap() {
    map = L.map('map', {
        center: CONFIG.MAP_CENTER,
        zoom: CONFIG.MAP_ZOOM,
        zoomControl: true
    });

    // Dark tile layer for modern look
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 19
    }).addTo(map);

    // Initialize empty flight track polyline
    flightTrack = L.polyline([], {
        color: '#58a6ff',
        weight: 3,
        opacity: 0.9,
        smoothFactor: 1
    }).addTo(map);

    // Custom marker icon
    const currentPosIcon = L.divIcon({
        className: 'current-position-marker',
        html: `<div class="marker-pulse"></div><div class="marker-center"></div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
    });

    // Add marker style - X marker for current position
    const style = document.createElement('style');
    style.textContent = `
        .current-position-marker {
            position: relative;
        }
        .marker-x {
            position: absolute;
            width: 24px;
            height: 24px;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
        }
        .marker-x::before,
        .marker-x::after {
            content: '';
            position: absolute;
            width: 4px;
            height: 24px;
            background: #ff4444;
            left: 50%;
            top: 50%;
            border-radius: 2px;
            box-shadow: 0 0 8px rgba(255, 68, 68, 0.8), 0 0 4px rgba(0, 0, 0, 0.5);
        }
        .marker-x::before {
            transform: translate(-50%, -50%) rotate(45deg);
        }
        .marker-x::after {
            transform: translate(-50%, -50%) rotate(-45deg);
        }
    `;
    document.head.appendChild(style);
}

// ===========================
// WebSocket Connection
// ===========================
function initSocket() {
    socket = io();

    socket.on('connect', () => {
        console.log('Connected to server');
    });

    socket.on('disconnect', () => {
        console.log('Disconnected from server');
        updateSerialStatus(false, false);
    });

    socket.on('status', (data) => {
        console.log('Status:', data);
    });

    socket.on('serial_status', (data) => {
        console.log('Serial status:', data);
        if (data.connecting) {
            updateSerialStatus(false, true);
        } else {
            updateSerialStatus(data.connected, false);
        }
    });

    socket.on('serial_reconnect_result', (data) => {
        console.log('Reconnect result:', data);
    });

    socket.on('telemetry', (data) => {
        console.log('Telemetry received:', data);
        lastPacketTimestamp = Date.now();
        updateTelemetry(data);
        updateMap(data);
    });

    socket.on('skewt_updated', (data) => {
        console.log('Skew-T updated:', data);
        refreshSkewTImage(data.timestamp);
    });
}

// ===========================
// Reconnect Button
// ===========================
function initReconnectButton() {
    const statusEl = document.getElementById('connectionStatus');
    statusEl.addEventListener('click', () => {
        if (!serialConnected) {
            console.log('Requesting serial reconnect...');
            updateSerialStatus(false, true);
            socket.emit('reconnect_serial');
        }
    });
}

// ===========================
// Serial Status
// ===========================
function updateSerialStatus(connected, connecting) {
    serialConnected = connected;
    const statusEl = document.getElementById('connectionStatus');
    const textEl = statusEl.querySelector('.status-text');

    statusEl.classList.remove('connected', 'disconnected', 'connecting');

    if (connecting) {
        statusEl.classList.add('connecting');
        textEl.textContent = 'Connecting...';
        statusEl.style.cursor = 'default';
    } else if (connected) {
        statusEl.classList.add('connected');
        textEl.textContent = 'USB Connected';
        statusEl.style.cursor = 'default';
    } else {
        statusEl.classList.add('disconnected');
        textEl.textContent = 'USB Disconnected (Click to retry)';
        statusEl.style.cursor = 'pointer';
    }
}

// ===========================
// Telemetry Updates
// ===========================
function updateTelemetry(data) {
    try {
        // Serial Number
        if (data.serial_number !== undefined) {
            updateBox('sn', data.serial_number, '', null);
        }

        // Time (convert unix timestamp to readable time)
        if (data.unix_time && data.unix_time > 0) {
            const time = new Date(data.unix_time * 1000);
            const timeStr = time.toLocaleTimeString('en-GB');
            updateBox('time', timeStr, '', null);
        } else {
            updateBox('time', new Date().toLocaleTimeString('en-GB'), '', null);
        }

        // Position
        if (data.lat !== undefined) {
            updateBox('lat', Number(data.lat).toFixed(6), '°', null);
        }
        if (data.lon !== undefined) {
            updateBox('lon', Number(data.lon).toFixed(6), '°', null);
        }
        if (data.alt_m !== undefined) {
            updateBox('alt', Number(data.alt_m).toFixed(1), ' m', null);
        }

        // Ascent Rate with color coding
        if (data.vspeed_ms !== undefined) {
            const ascentRate = Number(data.vspeed_ms);
            let ascentStatus = null;
            if (ascentRate > 2) {
                ascentStatus = 'ascending';
            } else if (ascentRate < -1) {
                ascentStatus = 'descending';
            }
            updateBox('ascent', ascentRate.toFixed(2), ' m/s', ascentStatus);
        }

        // Satellites with color coding
        if (data.satellites !== undefined) {
            const sats = Number(data.satellites);
            let satStatus = null;
            if (sats > 8) {
                satStatus = 'good';
            } else if (sats >= 6) {
                satStatus = 'warning';
            } else {
                satStatus = 'danger';
            }
            updateBox('sats', sats, '', satStatus);
        }

        // Temperature
        if (data.temp_c !== undefined) {
            updateBox('temp', Number(data.temp_c).toFixed(1), ' °C', null);
        }

        // Humidity
        if (data.rh_percent !== undefined) {
            updateBox('rh', Number(data.rh_percent).toFixed(1), ' %', null);
        }

        // Pressure
        if (data.pressure_hpa !== undefined) {
            updateBox('pressure', Number(data.pressure_hpa).toFixed(1), ' hPa', null);
        }

        // RSSI with color coding
        if (data.rssi_dbm !== undefined) {
            const rssi = Number(data.rssi_dbm);
            let rssiStatus = null;
            if (rssi > -120) {
                rssiStatus = 'good';
            } else if (rssi >= -130) {
                rssiStatus = 'warning';
            } else {
                rssiStatus = 'danger';
            }
            updateBox('rssi', rssi, ' dBm', rssiStatus);
        }

        // Update track points counter
        if (data.lat !== undefined && data.lon !== undefined) {
            flightPath.push([data.lat, data.lon]);
            document.getElementById('trackPoints').textContent = `${flightPath.length} points`;
        }
    } catch (error) {
        console.error('Error updating telemetry:', error, data);
    }
}

function updateBox(id, value, unit, status) {
    const box = document.getElementById(`box-${id}`);
    const valueEl = document.getElementById(`val-${id}`);

    if (!box || !valueEl) {
        console.warn(`Box or value element not found for id: ${id}`);
        return;
    }

    // Update value with animation
    const newValue = `${value}${unit}`;
    if (valueEl.textContent !== newValue) {
        valueEl.textContent = newValue;
        valueEl.classList.add('updated');
        setTimeout(() => valueEl.classList.remove('updated'), 300);
    }

    // Update status class
    box.classList.remove('status-good', 'status-warning', 'status-danger', 'status-ascending', 'status-descending');
    if (status) {
        box.classList.add(`status-${status}`);
    }
}

// ===========================
// Map Updates
// ===========================
function updateMap(data) {
    const lat = data.lat;
    const lon = data.lon;

    const noGpsEl = document.getElementById('mapNoGps');

    // Ignore invalid coordinates (no GPS lock)
    if (Math.abs(lat) < 0.1 && Math.abs(lon) < 0.1) {
        if (noGpsEl) noGpsEl.style.display = 'block';
        return;
    }

    if (noGpsEl) noGpsEl.style.display = 'none';

    const latlng = [lat, lon];

    // Update flight track
    flightTrack.addLatLng(latlng);

    // Update or create current position marker
    if (currentMarker) {
        currentMarker.setLatLng(latlng);
    } else {
        const icon = L.divIcon({
            className: 'current-position-marker',
            html: `<div class="marker-x"></div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12]
        });
        currentMarker = L.marker(latlng, { icon: icon }).addTo(map);
    }

    // Center map on current position with some padding
    if (flightPath.length <= 5) {
        map.setView(latlng, 12);
    } else {
        // Fit bounds to track
        map.fitBounds(flightTrack.getBounds(), { padding: [50, 50] });
    }
}

// ===========================
// Last Packet Timer
// ===========================
function initLastPacketTimer() {
    setInterval(() => {
        const box = document.getElementById('box-lastpkt');
        const valueEl = document.getElementById('val-lastpkt');

        if (!lastPacketTimestamp) {
            valueEl.textContent = '-- s';
            box.classList.remove('status-good', 'status-danger');
            return;
        }

        const elapsed = Math.floor((Date.now() - lastPacketTimestamp) / 1000);
        valueEl.textContent = `${elapsed} s`;

        box.classList.remove('status-good', 'status-danger');
        if (elapsed < 5) {
            box.classList.add('status-good');
        } else {
            box.classList.add('status-danger');
        }
    }, CONFIG.LAST_PACKET_UPDATE_INTERVAL);
}

// ===========================
// Skew-T Refresh
// ===========================
function initSkewTRefresh() {
    // Try to load existing Skew-T
    refreshSkewTImage(Date.now());

    // Poll for updates (backup in case WebSocket notification is missed)
    setInterval(() => {
        refreshSkewTImage(Date.now());
    }, CONFIG.SKEWT_REFRESH_INTERVAL);
}

function refreshSkewTImage(timestamp) {
    const img = document.getElementById('skewt-image');
    const placeholder = document.getElementById('skewt-placeholder');
    const status = document.getElementById('skewTStatus');

    // Create new image to test if file exists
    const testImg = new Image();
    testImg.onload = () => {
        img.src = `/static/skewt.png?t=${timestamp}`;
        img.style.display = 'block';
        placeholder.style.display = 'none';
        status.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    };
    testImg.onerror = () => {
        // Skew-T not available yet
        status.textContent = 'Waiting for data...';
    };
    testImg.src = `/static/skewt.png?t=${timestamp}`;
}

// ===========================
// Utility Functions
// ===========================
function formatNumber(num, decimals = 2) {
    return Number(num).toFixed(decimals);
}
