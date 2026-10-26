# Customer Installation Guide

## What This Software Does

Brixigo Connector Agent connects your biometric attendance devices to Brixigo Payroll & HRMS.
It runs on a Windows PC/server inside your network, reads punches from devices, and uploads
them securely to the cloud.

## Install Steps

1. Download the official installer from Brixigo.
2. Right-click → **Run as administrator**.
3. Complete the wizard and keep the default install folder unless IT requires otherwise.
4. After install, the tray icon appears near the Windows clock.
5. Open **Open Dashboard** from the tray menu.

## Connect to Brixigo

1. Click **Settings**.
2. Enter:
   - Organisation ID
   - API Key / Device Token
   - Connector ID (provided by Brixigo or your admin)
3. Save.

Internet and Cloud status cards should turn **ONLINE**.

## Add Devices

### Option A — Manual

1. **Device Management → Add Device**
2. Enter name, manufacturer, IP, port, password/comm key
3. Fill Organization / Branch / Gate if used
4. Save → **Test / Reconnect**

### Option B — Discovery (recommended for many devices)

1. Click **Discover Devices**
2. Enter range, e.g. `192.168.1.1-254`
3. Click **Refresh Scan**
4. Select a device → **Add Device** (admin approval)
5. Or **Ignore Device** to hide it

Devices are never registered automatically.

## Verify

1. Click **Sync Now**
2. Confirm punches appear in Activity Log
3. Confirm Upload Queue decreases
4. In Brixigo Cloud, verify punches for the terminal code

## Daily Operation

- Leave the Windows Service running
- Use the dashboard only when needed
- Check Alerts tab for offline devices or upload issues
