# ReSonde Dashboard - CloudPanel Deployment Guide

## Prerequisites
- CloudPanel installed on your server
- Domain `dashboard.resonde.de` pointed to your server IP
- Python 3.10+ available

---

## Step 1: Create Python Site in CloudPanel

1. Log into CloudPanel at `https://your-server-ip:8443`
2. Go to **Sites** → **Add Site**
3. Select **Create a Python Site**
4. Fill in:
   - **Domain**: `dashboard.resonde.de`
   - **Python Version**: 3.11 (or latest available)
   - **App Port**: 5000
5. Click **Create**

---

## Step 2: Upload Application Files

1. Connect via SFTP or SSH to your server
2. Navigate to your site directory:
   ```bash
   cd /home/dashboard-resonde-de/htdocs/dashboard.resonde.de
   ```
3. Upload all files from your `Server UI` folder:
   - `app.py`
   - `wsgi.py`
   - `requirements.txt`
   - `templates/` folder
   - `static/` folder

Or use `rsync`:
```bash
rsync -avz "/home/mika/ReSonde/Server UI/" user@your-server:/home/dashboard-resonde-de/htdocs/dashboard.resonde.de/
```

---

## Step 3: Install Dependencies

SSH into your server and activate the virtual environment:

```bash
cd /home/dashboard-resonde-de/htdocs/dashboard.resonde.de
source /home/dashboard-resonde-de/venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
```

---

## Step 4: Create systemd Service

CloudPanel typically uses systemd. Create a service file:

```bash
sudo nano /etc/systemd/system/resonde-dashboard.service
```

Add:
```ini
[Unit]
Description=ReSonde Dashboard
After=network.target

[Service]
User=dashboard-resonde-de
Group=dashboard-resonde-de
WorkingDirectory=/home/dashboard-resonde-de/htdocs/dashboard.resonde.de
Environment="PATH=/home/dashboard-resonde-de/venv/bin"
ExecStart=/home/dashboard-resonde-de/venv/bin/gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --workers 1 --bind 127.0.0.1:5000 wsgi:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable resonde-dashboard
sudo systemctl start resonde-dashboard
sudo systemctl status resonde-dashboard
```

---

## Step 5: Configure Nginx (Reverse Proxy)

CloudPanel auto-generates Nginx config. You need to update it to support WebSockets and prevent buffering.

Edit: `/etc/nginx/sites-enabled/dashboard.resonde.de.conf`

**1. Add this "map" block OUTSIDE the server block** (at the top of the file):
```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

**2. Update the `location /` block** inside the server block:
```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /socket.io {
    proxy_pass http://127.0.0.1:5000/socket.io;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
}
```

Reload Nginx:
```bash
sudo systemctl reload nginx
```

---

## Step 6: Enable SSL (HTTPS)

In CloudPanel:
1. Go to **Sites** → **dashboard.resonde.de**
2. Click **SSL/TLS**
3. Click **Actions** → **New Let's Encrypt Certificate**
4. Enable **Force HTTPS**

---

## Step 7: Open Firewall (IMPORTANT)

If your server has a firewall (UFW) active, you must allow traffic on ports 80 and 443:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

> [!IMPORTANT]
> If you are using a cloud provider (like AWS, GCP, Oracle Cloud), you **must** also open port 443 in their web console's **Security Groups** or **Ingress Rules**.

---

## Step 8: Create Data Directory

```bash
mkdir -p /home/dashboard-resonde-de/htdocs/dashboard.resonde.de/data
chown -R dashboard-resonde-de:dashboard-resonde-de /home/dashboard-resonde-de/htdocs/dashboard.resonde.de/data
```

---

## ESP32 Endpoint

Once deployed, your ESP32 devices should POST to:
```
https://dashboard.resonde.de/api/upload
```

---

## Troubleshooting

**Check logs:**
```bash
sudo journalctl -u resonde-dashboard -f
```

**Restart app:**
```bash
sudo systemctl restart resonde-dashboard
```

**Test locally on server:**
```bash
curl http://127.0.0.1:5000/api/sondes
```
