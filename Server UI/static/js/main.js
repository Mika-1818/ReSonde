/**
 * Radiosonde Server UI - Frontend JavaScript
 * Handles sonde selection, track persistence, downloads, and real-time updates
 */

// ===========================
// Configuration
// ===========================
const CONFIG = {
    MAP_CENTER: [51.0, 10.0],
    MAP_ZOOM: 6,
    SKEWT_REFRESH_INTERVAL: 30000,
    LAST_PACKET_UPDATE_INTERVAL: 1000
};

// ===========================
// State
// ===========================
let socket = null;
let map = null;
let flightTrack = null;
let currentMarker = null;
let lastPacketTimestamp = null;
let selectedSonde = null;

// ===========================
// Initialization
// ===========================
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    initSocket();
    initSondeSelector();
    initDownloadButtons();
    initLastPacketTimer();
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

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap &copy; CARTO',
        subdomains: 'abcd',
        maxZoom: 19
    }).addTo(map);

    flightTrack = L.polyline([], {
        color: '#58a6ff',
        weight: 3,
        opacity: 0.9,
        smoothFactor: 1
    }).addTo(map);
}

// ===========================
// WebSocket Connection
// ===========================
function initSocket() {
    socket = io();

    socket.on('connect', () => {
        console.log('Connected to server');
        updateConnectionStatus('connected');
    });

    socket.on('disconnect', () => {
        console.log('Disconnected from server');
        updateConnectionStatus('disconnected');
    });

    socket.on('status', (data) => {
        console.log('Status:', data);
        if (data.sondes) {
            populateSondeSelector(data.sondes);
        }
    });

    socket.on('telemetry', (data) => {
        console.log('Telemetry received:', data);

        // Only update if this is for our selected sonde
        if (selectedSonde && data.serial_number === selectedSonde) {
            lastPacketTimestamp = Date.now();
            updateTelemetry(data);
            addTrackPoint(data.lat, data.lon);
        }

        // Refresh sonde list
        refreshSondeList();
    });
}

function updateConnectionStatus(status) {
    const statusEl = document.getElementById('connectionStatus');
    const textEl = statusEl.querySelector('.status-text');

    statusEl.classList.remove('connected', 'disconnected');
    statusEl.classList.add(status);
    textEl.textContent = status === 'connected' ? 'Connected' : 'Disconnected';
}

// ===========================
// Sonde Selector
// ===========================
function initSondeSelector() {
    const select = document.getElementById('sondeSelect');
    const refreshBtn = document.getElementById('refreshSondes');

    select.addEventListener('change', (e) => {
        const sn = e.target.value ? parseInt(e.target.value) : null;
        selectSonde(sn);
    });

    refreshBtn.addEventListener('click', refreshSondeList);

    // Initial load
    refreshSondeList();
}

function refreshSondeList() {
    fetch('/api/sondes')
        .then(res => res.json())
        .then(sondes => populateSondeSelector(sondes))
        .catch(err => console.error('Failed to load sondes:', err));
}

function populateSondeSelector(sondes) {
    const select = document.getElementById('sondeSelect');
    const currentValue = select.value;

    // Clear existing options except the placeholder
    while (select.options.length > 1) {
        select.remove(1);
    }

    // Add sonde options
    sondes.forEach(sonde => {
        const option = document.createElement('option');
        option.value = sonde.sn;
        option.textContent = `SN ${sonde.sn} (${sonde.packet_count} packets)`;
        select.appendChild(option);
    });

    // Restore selection
    if (currentValue) {
        select.value = currentValue;
    }
}

function selectSonde(sn) {
    selectedSonde = sn;

    const downloadCsv = document.getElementById('downloadCsv');
    const downloadSkewt = document.getElementById('downloadSkewt');
    const mapNoSonde = document.getElementById('mapNoSonde');

    if (!sn) {
        // No sonde selected
        downloadCsv.disabled = true;
        downloadSkewt.disabled = true;
        mapNoSonde.style.display = 'block';
        clearMap();
        clearTelemetry();
        clearSkewT();
        return;
    }

    downloadCsv.disabled = false;
    downloadSkewt.disabled = false;
    mapNoSonde.style.display = 'none';

    // Load sonde data
    loadSondeData(sn);
    loadSondeTrack(sn);
    loadSkewT(sn);

    // Subscribe to updates for this sonde
    socket.emit('subscribe_sonde', { sn: sn });
}

// ===========================
// Data Loading
// ===========================
function loadSondeData(sn) {
    fetch(`/api/sonde/${sn}/latest`)
        .then(res => res.json())
        .then(data => {
            if (!data.error) {
                updateTelemetry(data);
                lastPacketTimestamp = Date.now();
            }
        })
        .catch(err => console.error('Failed to load sonde data:', err));
}

function loadSondeTrack(sn) {
    fetch(`/api/sonde/${sn}/track`)
        .then(res => res.json())
        .then(track => {
            clearMap();

            if (track.length > 0) {
                track.forEach(([lat, lon]) => {
                    if (Math.abs(lat) > 0.1 || Math.abs(lon) > 0.1) {
                        flightTrack.addLatLng([lat, lon]);
                    }
                });

                document.getElementById('trackPoints').textContent = `${track.length} points`;

                // Update current position marker
                const lastValid = track.filter(([lat, lon]) => Math.abs(lat) > 0.1 || Math.abs(lon) > 0.1);
                if (lastValid.length > 0) {
                    const [lat, lon] = lastValid[lastValid.length - 1];
                    updateMarker(lat, lon);

                    // Fit map to track
                    if (flightTrack.getLatLngs().length > 0) {
                        map.fitBounds(flightTrack.getBounds(), { padding: [50, 50] });
                    }
                }
            }
        })
        .catch(err => console.error('Failed to load track:', err));
}

function loadSkewT(sn) {
    const img = document.getElementById('skewt-image');
    const placeholder = document.getElementById('skewt-placeholder');
    const status = document.getElementById('skewTStatus');

    const testImg = new Image();
    testImg.onload = () => {
        img.src = `/api/sonde/${sn}/skewt?t=${Date.now()}`;
        img.style.display = 'block';
        placeholder.style.display = 'none';
        status.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    };
    testImg.onerror = () => {
        img.style.display = 'none';
        placeholder.style.display = 'flex';
        status.textContent = 'Not enough data';
    };
    testImg.src = `/api/sonde/${sn}/skewt?t=${Date.now()}`;
}

// ===========================
// Map Functions
// ===========================
function clearMap() {
    flightTrack.setLatLngs([]);
    if (currentMarker) {
        map.removeLayer(currentMarker);
        currentMarker = null;
    }
    document.getElementById('trackPoints').textContent = '0 points';
}

function addTrackPoint(lat, lon) {
    const noGpsEl = document.getElementById('mapNoGps');

    if (Math.abs(lat) < 0.1 && Math.abs(lon) < 0.1) {
        noGpsEl.style.display = 'block';
        return;
    }

    noGpsEl.style.display = 'none';
    flightTrack.addLatLng([lat, lon]);
    updateMarker(lat, lon);

    const points = flightTrack.getLatLngs().length;
    document.getElementById('trackPoints').textContent = `${points} points`;

    if (points <= 5) {
        map.setView([lat, lon], 12);
    } else {
        map.fitBounds(flightTrack.getBounds(), { padding: [50, 50] });
    }
}

function updateMarker(lat, lon) {
    if (currentMarker) {
        currentMarker.setLatLng([lat, lon]);
    } else {
        const icon = L.divIcon({
            className: 'current-position-marker',
            html: `<div class="marker-x"></div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12]
        });
        currentMarker = L.marker([lat, lon], { icon: icon }).addTo(map);
    }
}

// ===========================
// Telemetry Updates
// ===========================
function updateTelemetry(data) {
    try {
        if (data.serial_number !== undefined) updateBox('sn', data.serial_number, '', null);
        if (data.packet_counter !== undefined) updateBox('pkt', data.packet_counter, '', null);

        if (data.unix_time && data.unix_time > 0) {
            const time = new Date(data.unix_time * 1000);
            updateBox('time', time.toLocaleTimeString('en-GB'), '', null);
        }

        if (data.lat !== undefined) updateBox('lat', Number(data.lat).toFixed(6), '°', null);
        if (data.lon !== undefined) updateBox('lon', Number(data.lon).toFixed(6), '°', null);
        if (data.alt_m !== undefined) updateBox('alt', Number(data.alt_m).toFixed(1), ' m', null);

        if (data.vspeed_ms !== undefined) {
            const rate = Number(data.vspeed_ms);
            let status = null;
            if (rate > 2) status = 'ascending';
            else if (rate < -1) status = 'descending';
            updateBox('ascent', rate.toFixed(2), ' m/s', status);
        }

        if (data.satellites !== undefined) {
            const sats = Number(data.satellites);
            let status = sats > 8 ? 'good' : (sats >= 6 ? 'warning' : 'danger');
            updateBox('sats', sats, '', status);
        }

        if (data.temp_c !== undefined) updateBox('temp', Number(data.temp_c).toFixed(1), ' °C', null);
        if (data.rh_percent !== undefined) updateBox('rh', Number(data.rh_percent).toFixed(1), ' %', null);
        if (data.pressure_hpa !== undefined) updateBox('pressure', Number(data.pressure_hpa).toFixed(1), ' hPa', null);

        // Battery voltage with color coding
        if (data.battery_v !== undefined) {
            const batt = Number(data.battery_v);
            let status = null;
            if (batt > 1.25) status = 'good';
            else if (batt >= 1.1) status = 'warning';
            else status = 'danger';
            updateBox('battery', batt.toFixed(2), ' V', status);
        }

        // RSSI with color coding
        if (data.rssi_dbm !== undefined) {
            const rssi = Number(data.rssi_dbm);
            let status = rssi > -120 ? 'good' : (rssi >= -130 ? 'warning' : 'danger');
            updateBox('rssi', rssi, ' dBm', status);
        }
    } catch (error) {
        console.error('Error updating telemetry:', error);
    }
}

function updateBox(id, value, unit, status) {
    const box = document.getElementById(`box-${id}`);
    const valueEl = document.getElementById(`val-${id}`);

    if (!box || !valueEl) return;

    const newValue = `${value}${unit}`;
    if (valueEl.textContent !== newValue) {
        valueEl.textContent = newValue;
        valueEl.classList.add('updated');
        setTimeout(() => valueEl.classList.remove('updated'), 300);
    }

    box.classList.remove('status-good', 'status-warning', 'status-danger', 'status-ascending', 'status-descending');
    if (status) box.classList.add(`status-${status}`);
}

function clearTelemetry() {
    const boxes = ['sn', 'pkt', 'time', 'lat', 'lon', 'alt', 'ascent', 'sats', 'temp', 'rh', 'pressure', 'battery', 'lastpkt', 'rssi'];
    boxes.forEach(id => {
        const valueEl = document.getElementById(`val-${id}`);
        if (valueEl) valueEl.textContent = '--';
        const box = document.getElementById(`box-${id}`);
        if (box) box.classList.remove('status-good', 'status-warning', 'status-danger', 'status-ascending', 'status-descending');
    });
    lastPacketTimestamp = null;
}

function clearSkewT() {
    document.getElementById('skewt-image').style.display = 'none';
    document.getElementById('skewt-placeholder').style.display = 'flex';
    document.getElementById('skewTStatus').textContent = 'Select a sonde';
}

// ===========================
// Download Buttons
// ===========================
function initDownloadButtons() {
    document.getElementById('downloadCsv').addEventListener('click', () => {
        if (selectedSonde) {
            window.location.href = `/api/sonde/${selectedSonde}/download/csv`;
        }
    });

    document.getElementById('downloadSkewt').addEventListener('click', () => {
        if (selectedSonde) {
            window.location.href = `/api/sonde/${selectedSonde}/download/skewt`;
        }
    });
}

// ===========================
// Last Packet Timer
// ===========================
function initLastPacketTimer() {
    setInterval(() => {
        const box = document.getElementById('box-lastpkt');
        const valueEl = document.getElementById('val-lastpkt');

        if (!lastPacketTimestamp || !selectedSonde) {
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
