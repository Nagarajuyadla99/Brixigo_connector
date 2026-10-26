# Brixigo Connector Agent
## Complete Product Documentation

**Product Family:** Brixigo HRMS · Brixigo Payroll · Brixigo Workforce Management  
**Component:** Brixigo Connector Agent  
**Document Type:** Client Training · Customer Documentation · Sales Demonstration · Technical Documentation · Support · Implementation  
**Version Covered:** Connector Agent 3.0.2 · Cloud Platform 1.0.2  
**Publisher:** Brixigo · https://brixigo.com  
**Audience:** Customers, Implementation Partners, Support Engineers, Internal Developers, Sales Engineers, HR Administrators, IT Administrators  

---

### Document Control

| Item | Detail |
|------|--------|
| Document Title | Brixigo Connector Agent — Complete Product Documentation |
| Purpose | Explain the full biometric-to-cloud attendance ecosystem from installation through payroll-ready attendance |
| Scope | Architecture, workflows, installation, operations, security, troubleshooting, training, FAQ |
| Out of Scope | Source code, API implementation, database schemas for development, migrations |
| Classification | Customer-facing and internal enablement |
| Related Products | Brixigo HRMS, Brixigo Payroll, Brixigo Workforce Management, Brixigo Cloud |

### How to Use This Document

- **Sales & Pre-Sales:** Sections 1–3, 19, 24 for value and architecture storytelling.
- **Implementation Team:** Sections 4–10, 21 for go-live readiness.
- **Client Training:** Sections 6–7, 14–15, 20 for hands-on enablement.
- **Support Team:** Sections 11–12, 17–18, 22 for incident handling.
- **Security & IT:** Sections 3, 16, 23 for network and hardening decisions.
- **HR & Payroll Users:** Sections 10, 13–14 for daily attendance understanding.

### Table of Contents

1. Introduction  
2. System Architecture  
3. Why the Connector Is Required  
4. Client Requirements  
5. Installation Flow  
6. First-Time Setup  
7. How the Client Connects a Biometric Machine  
8. How the Connector Talks to the Device  
9. How the Connector Talks to Brixigo Cloud  
10. Complete Attendance Flow  
11. What Happens When the Internet Is Down  
12. What Happens When the Device Is Offline  
13. Employee Enrollment Flow  
14. How the Client Uses the System Daily  
15. Administrator Workflow  
16. Security  
17. Client Troubleshooting  
18. Support Workflow  
19. Complete End-to-End Flow  
20. Client Training Guide  
21. Implementation Checklist  
22. FAQ (50+)  
23. Best Practices  
24. Business Benefits  

---

# SECTION 1 — INTRODUCTION

## 1.1 What Is Brixigo Connector?

**Brixigo Connector Agent** is on-premise Windows software that sits inside the customer’s office network and safely connects biometric attendance machines to **Brixigo Cloud**.

In simple terms:

> Your fingerprint / face / card devices stay on the local office network.  
> Brixigo HRMS, Payroll, and Workforce Management live in the cloud.  
> The Connector is the trusted bridge between those two worlds.

It continuously:

1. Talks to biometric devices on the LAN (typically over TCP port **4370** for ZKTeco / eSSL-class devices).  
2. Reads attendance punches stored on those devices.  
3. Stores those punches temporarily in a local durable queue (SQLite).  
4. Uploads them securely to Brixigo Cloud over **HTTPS**.  
5. Sends health and heartbeat signals so administrators know the site is online.  
6. Optionally syncs employees from cloud to devices so workforce identity stays consistent.

The Connector is not a replacement for Brixigo HRMS or Payroll. It is the **field agent** that makes biometric attendance available to those cloud products in near real time, without asking customers to open firewall holes into their private network.

## 1.2 Why Is It Needed?

Modern enterprises want cloud HR and payroll because they are available anywhere, always updated, and easier to scale across branches. Biometric devices, however, were designed for local networks:

- They have private IP addresses (for example `192.168.1.50`).  
- They speak device protocols, not cloud HRMS language.  
- They often sit behind NAT and corporate firewalls.  
- They cannot safely “publish themselves” to the public internet.

Without a connector, organizations typically fall back to:

- Manual USB download of attendance logs.  
- Spreadsheet uploads.  
- Vendor desktop utilities that only work when someone remembers to run them.  
- Incomplete or delayed attendance that breaks payroll accuracy.

Brixigo Connector removes that friction. Once installed and configured, it runs as a Windows service named **BrixigoConnector** (display name **Brixigo Connector Agent**) and keeps attendance flowing automatically.

## 1.3 What Problems Does It Solve?

| Business Pain | How Connector Solves It |
|---------------|-------------------------|
| Manual attendance collection | Automatic device polling and cloud upload |
| Late payroll because punches arrived late | Continuous sync with local queue and retry |
| Lost punches after internet outage | Local SQLite queue retains punches until upload succeeds |
| Multiple branches, multiple devices | Multi-device scheduler with per-device intervals |
| No visibility into device health | Heartbeat, device health, alerts, diagnostics |
| Inconsistent employee identity on devices | Cloud-to-device employee sync lifecycle |
| Security risk of exposing devices online | Devices stay private; only outbound HTTPS from connector |
| Duplicate punches corrupting attendance | Idempotency keys prevent duplicate upload processing |
| Dependence on a person opening a desktop tool | Windows service with delayed auto-start and crash recovery |

## 1.4 Why Can’t Biometric Devices Directly Connect to the Cloud?

This is one of the most common customer questions. The short answer: **most office biometric devices are not designed as always-on, internet-facing cloud clients.**

### Technical reasons

1. **Private addressing**  
   Devices usually receive addresses such as `192.168.x.x`, `10.x.x.x`, or `172.16–31.x.x`. Those addresses are not reachable from the public internet.

2. **NAT (Network Address Translation)**  
   The office router shares one public IP among many internal machines. Outside systems cannot open a connection “into” a specific biometric device unless special inbound rules are created — which most IT teams refuse to do for attendance hardware.

3. **Firewall policy**  
   Enterprise firewalls block unsolicited inbound traffic. Exposing port 4370 to the world would be a serious security risk.

4. **Protocol mismatch**  
   Device communication uses vendor/device protocols (for example ZK-style TCP sessions). Brixigo Cloud expects authenticated HTTPS REST payloads with organization identity, connector identity, and signed operational controls.

5. **Reliability and buffering**  
   Cloud systems expect durable, retryable delivery. A device alone usually cannot manage multi-day offline queues, dead-letter handling, batch compression, and organizational mapping.

6. **Operational control**  
   Enterprises need centralized credentials, machine binding, auditability, remote commands, and support diagnostics. A bare device cannot provide that enterprise control plane.

### Business reason

Customers should not weaken site security just to enable attendance. The Connector keeps security posture strong: **outbound only**, encrypted credentials, and no need to publish biometric hardware on the internet.

## 1.5 Why Is the Connector Installed on a Customer Machine?

The Connector must live where the devices live — on the customer LAN — because that is the only place that can both:

- Reach private device IPs, and  
- Reach Brixigo Cloud over the internet.

Think of it as a bilingual messenger stationed in the office:

- On one side, it speaks “device language” on the LAN.  
- On the other side, it speaks “Brixigo Cloud language” over HTTPS.

It is installed on a Windows PC or small server that should remain **always on**, share the same network as the biometric machines, and have stable internet access. From the user’s point of view, after setup it mostly disappears into the system tray and Windows Services, quietly doing its job.

## 1.6 Where It Fits in the Brixigo Product Family

```
Brixigo HRMS                 → People, organization, policies
Brixigo Workforce Management → Shifts, attendance rules, compliance
Brixigo Payroll              → Salary calculation from verified attendance
Brixigo Cloud                → Control plane, APIs, dashboards
Brixigo Connector Agent      → On-site biometric bridge
Biometric Devices            → Finger / face / card capture at gate
```

Attendance starts at the device. Trustworthy payroll ends in Brixigo. The Connector is the operational link that makes the journey automatic.

## 1.7 Simple Analogy for Client Training

Imagine a bank branch:

- The biometric machine is like a local teller drawer that records every transaction (punch).  
- The drawer cannot send money to headquarters by itself.  
- The Connector is the bonded courier who collects the day’s slips, stores them in a locked bag if roads are closed, and delivers them to headquarters when the highway (internet) is open.  
- Headquarters (Brixigo Cloud) then posts the transactions into the ledger (Attendance Engine → Payroll → Reports).

No slips are thrown away because the road was closed for a few hours. That is the promise of the local queue.

---

# SECTION 2 — SYSTEM ARCHITECTURE

## 2.1 High-Level Architecture

The Brixigo biometric ecosystem has a clear left-to-right flow: capture locally, bridge safely, process centrally, consume in HR and payroll.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         CUSTOMER OFFICE / BRANCH                         │
│                                                                          │
│   ┌─────────────────────┐         ┌──────────────────────────────────┐   │
│   │  Biometric Machine  │  LAN    │     Brixigo Connector Agent      │   │
│   │  (ZKTeco / eSSL)    │◄───────►│  Tray UI + Windows Service       │   │
│   │  IP: 192.168.x.x    │ TCP     │  Adapters · Poll · Queue · Upload│   │
│   │  Port: 4370         │ 4370    │  SQLite · Encrypted Config       │   │
│   └─────────────────────┘         └────────────────┬─────────────────┘   │
│                                                    │                     │
└────────────────────────────────────────────────────┼─────────────────────┘
                                                     │ HTTPS (Outbound)
                                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           INTERNET / TLS                                 │
└────────────────────────────────────────────────────┬─────────────────────┘
                                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                            BRIXIGO CLOUD                                 │
│   Connector APIs · Registration · Heartbeat · Commands · Employee Sync   │
│   Raw Attendance Ingest · Device Health · Alerts · Admin Dashboard       │
└────────────────────────────────────────────────────┬─────────────────────┘
                                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         ATTENDANCE ENGINE                                │
│   Shift Matching · Late · Early Out · Overtime · Status Determination    │
└────────────────────────────────────────────────────┬─────────────────────┘
                                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     PAYROLL  →  REPORTS  →  HR ACTIONS                   │
└──────────────────────────────────────────────────────────────────────────┘
```

## 2.2 Layered View

```
+--------------------------------------------------------------+
| Presentation / Operations                                    |
|  - Tray Dashboard, Alerts, Diagnostics, Device Management    |
+--------------------------------------------------------------+
| Connector Runtime                                            |
|  - Windows Service (sync owner)                              |
|  - Poll Scheduler · Upload Engine · Heartbeat · Health       |
|  - Employee Sync · Remote Commands · Discovery               |
+--------------------------------------------------------------+
| Local Persistence                                            |
|  - SQLite (attendance_queue, devices, employees, health)     |
|  - Encrypted configuration (connector.config.enc)            |
|  - Rotating logs under logs/                                 |
+--------------------------------------------------------------+
| Device Access Layer                                          |
|  - ZKTeco / eSSL adapters (ZK protocol over TCP/UDP)         |
|  - Future/planned packs: Anviz, Matrix, others               |
+--------------------------------------------------------------+
| Cloud Access Layer                                           |
|  - HTTPS REST client                                         |
|  - API key / device token, org ID, connector ID              |
|  - Optional JWT, HMAC command verification, optional mTLS    |
+--------------------------------------------------------------+
```

## 2.3 Customer Office Components

### Biometric Machine
Physical terminal at entry/exit points. Captures biometric verification and stores punches with employee identity and timestamp.

### LAN Network
Private office network that allows the Connector host to reach device IPs. This network must not depend on the public internet for device communication.

### Brixigo Connector Host
A Windows PC/server that runs:

- **BrixigoConnectorService.exe** as Windows service `BrixigoConnector`  
- **BrixigoConnector.exe** as tray/dashboard UI  

When the service is running, the tray operates in **UI-only mode** so attendance sync is never duplicated by two engines.

## 2.4 Cloud and Downstream Components

### Brixigo Cloud
Receives connector registration, heartbeats, device health, attendance batches, alerts, and employee sync acknowledgements. Provides admin visibility and command/control.

### Attendance Engine
Converts raw punches into business attendance: shift assignment, late coming, early going, overtime, missing punch handling, and final attendance status.

### Payroll
Consumes verified attendance outcomes to calculate payable days, overtime amounts, deductions, and related salary components.

### Reports
Provides operational and compliance reporting for HR, managers, and auditors.

## 2.5 Runtime Topology Inside the Connector

```
Devices (eSSL / ZKTeco)
        |
        | TCP 4370 (typical)
        v
   Device Adapters
        |
        v
   Poll Scheduler  -----> Poll Service -----> attendance_queue (SQLite)
        |
        +---------------> Health / Status
        |
   Upload Engine -------> HTTPS gzip batches -----> Brixigo Cloud
        |
   Heartbeat Service ---> Host metrics / queue depth
        |
   Employee Sync -------> Cloud events -----> Device user apply
```

## 2.6 Data at Rest on Customer Premises

Typical install root:

`C:\Program Files\Brixigo\Connector`

Important folders:

| Path | Purpose |
|------|---------|
| `data\connector.db` | Local operational database and queues |
| `data\connector.config.enc` | Encrypted settings |
| `data\backups\` | Backup artifacts when enabled |
| `logs\` | Category logs with daily rotation |

## 2.7 Multi-Branch Mental Model

Large customers often deploy one Connector per site (or per network segment), each registered under the same organization with a unique Connector ID. Devices are mapped to branch / location / gate hierarchy so cloud attendance remains organizationally meaningful.

```
Organization
 ├── Branch A  → Connector A → Devices A1, A2
 ├── Branch B  → Connector B → Devices B1, B2, B3
 └── Branch C  → Connector C → Device C1
                 |
                 +---- HTTPS ----> Same Brixigo Cloud Tenant
```

## 2.8 What the Architecture Intentionally Avoids

- Opening inbound ports to biometric devices from the internet  
- Requiring cloud servers to dial into private IPs  
- Depending on a logged-in user session for sync (service owns sync)  
- Storing cloud secrets in plain text configuration files  
- Allowing duplicate sync engines on the same host  

This design is deliberate: **reliability + security + operational simplicity**.

---

# SECTION 3 — WHY CONNECTOR IS REQUIRED

## 3.1 The Core Network Reality

Biometric devices remain inside the local network. Cloud platforms live on the public internet. Those two zones cannot freely talk unless something trusted bridges them.

```
[ Biometric Device ] -- private --> [ Office LAN ] -- NAT/Firewall --> [ Internet ] -- TLS --> [ Brixigo Cloud ]
        ^                               ^
        |                               |
   Not reachable                  Connector lives here
   from cloud                     and initiates outbound HTTPS
```

## 3.2 Private IP Addressing Explained Simply

Private IPs are reserved for internal use. Common examples:

| Range | Example Device IP | Meaning |
|-------|-------------------|---------|
| `192.168.0.0` – `192.168.255.255` | `192.168.1.50` | Typical small office LAN |
| `10.0.0.0` – `10.255.255.255` | `10.20.5.12` | Common in larger enterprises |
| `172.16.0.0` – `172.31.255.255` | `172.16.8.30` | Common in segmented networks |

A server on the public internet cannot open a connection to `192.168.1.50` any more than a person outside a gated colony can walk directly into Flat B-402 without being invited through the gate.

## 3.3 NAT Explained for Non-Network Audiences

**NAT (Network Address Translation)** lets many internal machines share one public IP address.

Example:

- Device: `192.168.1.50`  
- Connector PC: `192.168.1.20`  
- Office public IP: `203.0.113.40`

When the Connector calls Brixigo Cloud, the router rewrites the source address to the public IP. Cloud replies return to the router, which remembers the conversation and forwards the response to the Connector.

But if Cloud tried to start a brand-new conversation toward `192.168.1.50`, the router would not know which internal machine should receive it — and firewalls usually block that attempt anyway.

**Conclusion:** Communication must be initiated from inside. That is exactly what the Connector does.

## 3.4 Firewall Explained

Firewalls enforce “who may talk to whom.”

Typical enterprise stance:

- Allow outbound HTTPS (443) to trusted business SaaS.  
- Deny inbound connections to internal endpoints unless explicitly approved.  
- Deny unusual inbound device ports such as 4370 from the internet.

The Connector respects this model:

- It needs **outbound** access to Brixigo Cloud.  
- It needs **LAN** access to devices.  
- It does **not** require the customer to expose biometric hardware publicly.

## 3.5 The Connector as a Bridge

```
 ================== BRIDGE ROLE ==================

  DEVICE SIDE                      CLOUD SIDE
  -----------                      ----------
  Private IP                       Public HTTPS endpoint
  Device protocol                  REST APIs
  Local punches                    Organizational attendance
  Comm password                    API key / HMAC / TLS
  Poll / download                  Upload / heartbeat / sync

                 [ BRIXIGO CONNECTOR ]
                 translates + queues + secures
```

The bridge is active, not passive:

1. Authenticates to devices.  
2. Downloads new logs.  
3. Normalizes punch structure.  
4. Deduplicates via idempotency keys.  
5. Queues durably.  
6. Uploads in batches (often compressed).  
7. Reports health.  
8. Applies employee sync commands when configured.  

## 3.6 Why “Just Put the Device on the Internet” Is a Bad Idea

Even if a customer could assign a public IP or port-forward 4370:

- Attack surface expands dramatically.  
- Device firmware is rarely hardened like an enterprise gateway.  
- Credential stuffing and protocol abuse become realistic risks.  
- Audit and least-privilege controls are weaker.  
- Support complexity rises across every branch router.

Brixigo’s architecture chooses the safer pattern used across enterprise integrations: **an on-prem agent with outbound-only cloud connectivity.**

## 3.7 Why One Shared Desktop Utility Is Not Enough

Older attendance practices relied on someone opening a vendor tool, downloading logs, and uploading a file. That approach fails when:

- The person is on leave.  
- The PC is locked or logged off.  
- Multiple devices exist across floors.  
- Internet blips occur during upload.  
- Payroll cutoff arrives and files are incomplete.

The Connector replaces fragile human ritual with a supervised always-on service.

---

# SECTION 4 — CLIENT REQUIREMENTS

Before installation day, the customer and implementation team should confirm the following prerequisites. Missing any one of these is the most common cause of delayed go-live.

## 4.1 Windows PC or Server

| Requirement | Guidance |
|-------------|----------|
| Operating System | Supported Windows desktop/server environment suitable for always-on services |
| Role | Dedicated or shared machine that can remain powered on |
| Resources | Modest CPU/RAM is usually enough for typical branch device counts; scale host resources for large multi-device sites |
| Disk | Enough free space for SQLite growth, logs, and backups |
| Time sync | Windows clock should be accurate (NTP recommended) |

The Connector host does not need to be a high-end server for small deployments, but it must be stable.

## 4.2 Always-ON Expectation

Attendance devices capture punches 24×7. The Connector should be available whenever employees can punch.

Recommended operational posture:

- Machine set not to sleep / hibernate during working coverage windows (preferably never for factory/shift sites).  
- Power options configured for continuous operation.  
- UPS recommended for sites with unstable electricity.  
- Windows Update reboots planned outside peak punch windows where possible.

## 4.3 Same LAN as Biometric Devices

The Connector must reach device private IPs directly (or via routed internal networks intentionally designed for that access).

Checklist:

- Ping or port connectivity from Connector host to device IP.  
- No guest Wi-Fi isolation that blocks device LAN access.  
- Correct VLAN routing if devices and PC are segmented.  
- Static IP or DHCP reservation for devices (strongly recommended).

## 4.4 Internet Connectivity

Required for:

- Cloud registration  
- Heartbeat  
- Attendance upload  
- Employee sync  
- Remote configuration / commands  
- Support diagnostics against cloud reachability  

Outbound HTTPS to the configured Cloud Base URL (default production target conceptually `https://brixigo.com`) must be allowed. Proxy settings can be configured when the enterprise requires an HTTP proxy.

## 4.5 Administrator Rights

Administrator rights are required to:

- Install the application under Program Files  
- Register/unregister the Windows service  
- Configure certain machine-level environment settings in production hardening scenarios  
- Allow firewall rules if IT chooses host-based firewall changes  

Day-to-day HR users do not need admin rights merely to view the dashboard after installation.

## 4.6 Firewall Allowances

| Direction | Destination | Purpose |
|-----------|-------------|---------|
| Outbound | Brixigo Cloud HTTPS (443) | Uploads, heartbeat, sync |
| LAN | Device IP:4370 (or configured port) | Device communication |
| Optional | DNS resolvers | Name resolution for cloud URL |
| Optional | Proxy | If corporate proxy is mandatory |

Inbound internet exposure of devices is **not** required.

## 4.7 Device Network Details

Collect before setup:

| Item | Why Needed |
|------|------------|
| Device IP | Primary connection target |
| Device Port | Usually **4370** |
| Communication Password / Comm Key | Device authentication |
| Manufacturer | eSSL or ZKTeco for production-ready path |
| Device Name | Human-friendly label in dashboard |
| Terminal Code | Maps uploads to the correct cloud terminal identity |
| Timezone | Punch interpretation context (commonly `Asia/Kolkata` in India deployments) |
| Branch / Location / Gate | Organizational mapping |

## 4.8 Organization Login / Cloud Credentials

Provided by Brixigo or the customer’s Brixigo administrator:

| Credential | Meaning |
|------------|---------|
| Organisation ID | Tenant identity |
| API Key / Device Token | Connector authentication to cloud |
| Connector ID | Unique connector instance identity |
| Command Signing Secret | Shared HMAC secret for secure remote commands |
| Cloud Base URL | Target cloud environment |

Treat these like production secrets. Do not share over unprotected chat channels.

## 4.9 People and Process Prerequisites

- An IT owner for the Windows host and network.  
- An HR/admin owner for employee master data in Brixigo.  
- A go-live window for test punches.  
- A support contact path for the first 48 hours after cutover.

## 4.10 Pre-Installation Readiness Card (Printable)

```
[ ] Windows host identified and always-on policy agreed
[ ] Host on same reachable network as devices
[ ] Internet / proxy validated
[ ] Admin installer available on setup day
[ ] Device IPs documented
[ ] Ports confirmed (default 4370)
[ ] Comm passwords available
[ ] Organisation ID / API key / Connector ID / signing secret received
[ ] Branch and terminal naming convention agreed
[ ] Test employees identified for punch verification
```

---

# SECTION 5 — INSTALLATION FLOW

## 5.1 What the Installer Delivers

Depending on packaging, customers receive either:

- **Inno Setup installer:** `BrixigoConnectorSetup-3.0.2.exe`, or  
- **Client pack ZIP** with `INSTALL.bat` / install scripts, binaries, and a client guide.

Both paths aim to place a production-ready Connector on the host.

## 5.2 Installation Flow Diagram

```
Client receives installer / client pack
                |
                v
     Run as Administrator
                |
                v
   Choose install location
   (default: C:\Program Files\Brixigo\Connector)
                |
                v
   Files copied:
     - BrixigoConnector.exe          (Tray / Dashboard)
     - BrixigoConnectorService.exe   (Windows Service engine)
     - Service setup scripts
                |
                v
   Windows Service registered:
     Service name: BrixigoConnector
     Display name: Brixigo Connector Agent
     Start type: Delayed Automatic
                |
                v
   Runtime folders prepared:
     - data\   (config + SQLite DB + backups)
     - logs\   (rotating operational logs)
                |
                v
   Optional desktop shortcut / tray autostart
                |
                v
   First launch → Setup wizard / Settings
```

## 5.3 Step-by-Step Installation Narrative

### Step 1 — Download or receive the package
Customer obtains the official package from Brixigo implementation/support — never from unofficial mirrors.

### Step 2 — Unblock if Windows SmartScreen warns
For ZIP/batch packs, use file Properties → Unblock when needed, or run the installer elevated.

### Step 3 — Run as Administrator
Right-click and run with admin rights so service registration can succeed.

### Step 4 — Follow the setup wizard
Accept the install directory unless IT standards require a different path.

### Step 5 — Service installation
The installer (or setup script) registers **BrixigoConnector** as a Windows service with delayed auto-start and recovery restarts (commonly after 5s / 15s / 60s on failure sequences).

### Step 6 — Tray application availability
The tray executable provides the human operations console: settings, devices, queue visibility, alerts, diagnostics, and service restart actions.

### Step 7 — Database creation
On first run, local SQLite storage is initialized for devices, attendance queue, sync logs, health, commands/nonces, employees, and related operational tables.

### Step 8 — Logs creation
Category logs such as connector, connection, sync, upload, error, crash, heartbeat, health, service, and UI logs are prepared under `logs\` with rotation.

### Step 9 — Configuration container creation
Encrypted configuration storage is prepared (`connector.config.enc`). Sensitive values are not left in plain text.

### Step 10 — Startup behavior
Service starts with Windows (delayed). Tray may autostart for operator convenience.

### Step 11 — Desktop shortcut
A shortcut labeled **Brixigo Connector** is typically created for easy dashboard access.

## 5.4 Installed Layout (Conceptual)

```
C:\Program Files\Brixigo\Connector\
├── BrixigoConnector.exe
├── BrixigoConnectorService.exe
├── setup_service.ps1
├── uninstall_service.ps1
├── data\
│   ├── connector.config.enc
│   ├── connector.db
│   └── backups\
└── logs\
    ├── connector.log
    ├── sync.log
    ├── upload.log
    ├── error.log
    └── ...
```

## 5.5 What “Success” Looks Like After Install

- Service `BrixigoConnector` exists in Windows Services.  
- Tray icon is available.  
- Dashboard opens.  
- Data and logs folders exist.  
- No repeated crash loop in service recovery.  

Cloud connection and device connection come next in First-Time Setup; installation alone does not finish business go-live.

## 5.6 Uninstall / Reinstall Notes for Implementers

Uninstallation should use the official uninstall path/scripts so the Windows service is cleanly removed. Configuration and database retention policies should be agreed with the customer before wiping `data\` — that folder may contain queued punches and encrypted settings needed for recovery.

---

# SECTION 6 — FIRST-TIME SETUP

## 6.1 Goal of First Launch

At the end of first-time setup, the Connector must be:

1. Authenticated to the correct Brixigo organization.  
2. Registered as a known connector instance.  
3. Able to complete HTTPS reachability checks.  
4. Sending heartbeat / showing **Online** status.  

Only then should device onboarding begin.

## 6.2 First Launch Flow

```
Open Brixigo Connector (tray / desktop shortcut)
                |
                v
        Open Dashboard
                |
                v
           Settings
                |
                v
 Enter cloud credentials & identifiers
                |
                v
              Save
                |
                v
         Test Connection
                |
                v
       Register Connector
                |
                v
           Heartbeat starts
                |
                v
        Connector shows ONLINE
```

## 6.3 Credentials and Fields Explained

### Organisation ID
Identifies the customer tenant inside Brixigo. Wrong value means the connector is talking to the wrong organizational context (or failing auth).

### API Key / Device Token
Shared secret presented to cloud (commonly via device-token style header). Proves this connector installation is allowed to upload and sync for the organization.

### Connector ID
Unique ID for this connector instance. Essential when one organization has many branches/connectors. Keep it stable after registration.

### Cloud Base URL
The cloud environment endpoint. Production commonly points to the Brixigo cloud base (for example `https://brixigo.com`). Implementation teams may provide a specific environment URL for staged rollouts.

### Command Signing Secret
Shared HMAC secret used so remote commands from cloud can be verified as authentic and untampered. This must match the cloud-side command signing configuration. If mismatched, command security fails closed.

### Optional JWT
Some deployments may also use bearer tokens for additional API authorization pathways. Follow the credentials sheet provided by Brixigo for the specific tenant.

## 6.4 Screen-by-Screen Guidance (Conceptual UI Walkthrough)

### Tray Icon
Located near the Windows clock. Provides quick actions such as Open Dashboard, Restart Service, and status cues.

### Dashboard Home / Status Cards
Operators typically see high-level health:

- Internet / DNS reachability  
- Cloud connectivity  
- Service running state  
- Queue depth  
- Device online/offline counts  
- Last sync indicators  

### Settings Screen
Where cloud credentials and connector identity are entered and saved. Saving writes into encrypted configuration.

### Test Connection Action
Validates that the Connector can reach cloud health/APIs with the provided credentials. This is the “don’t proceed until green” gate for implementers.

### Registration
Connector announces itself to cloud (register/refresh). Cloud then recognizes the instance for heartbeats, uploads, and commands.

### Heartbeat
Periodic signal (commonly about every 60 seconds) containing host metrics and operational counters such as pending queue size, connected devices, failed uploads, and version information.

### Online State
“Online” means cloud is receiving connector life signals within expected freshness windows. It does **not** by itself prove every biometric device is online — device health is a separate signal.

## 6.5 First-Time Setup Validation Checklist

```
[ ] Organisation ID entered exactly as issued
[ ] API Key / Device Token entered with no extra spaces
[ ] Connector ID confirmed unique and recorded in handover sheet
[ ] Cloud URL correct for the environment
[ ] Command Signing Secret matches cloud configuration
[ ] Save completed without error
[ ] Test Connection succeeded
[ ] Registration succeeded
[ ] Heartbeat visible / connector online in cloud or local status
[ ] Credentials stored securely offline (password manager / sealed handover)
```

## 6.6 Common First-Setup Mistakes

1. Using lab/HTTP URLs in production.  
2. Mixing staging credentials with production URL.  
3. Reusing another branch’s Connector ID accidentally.  
4. Pasting signing secret with hidden whitespace.  
5. Assuming “dashboard opens” equals “cloud connected.”  

## 6.7 After Cloud Is Online

Proceed to device onboarding (Section 7). Do not enroll dozens of devices before cloud authentication is proven; otherwise troubleshooting becomes ambiguous.

---

# SECTION 7 — HOW CLIENT CONNECTS BIOMETRIC MACHINE

## 7.1 Purpose

This section explains how an administrator adds a physical biometric terminal so the Connector can poll it and upload its punches.

## 7.2 Connection Flow

```
Open Connector Dashboard
            |
            v
     Device Management
            |
            v
        Add Device
            |
            v
   Select Vendor / Manufacturer
            |
            v
 Enter device identity & network fields
            |
            v
  Map branch / location / timezone
            |
            v
           Save
            |
            v
      Test Connection
            |
            v
   Read Device Information
            |
            v
 Display serial, firmware, SDK/protocol info,
 user count, attendance count (as available)
```

## 7.3 Two Onboarding Paths

### Path A — Manual Add (precise, controlled)
Best when device IP and credentials are already known.

### Path B — Discovery Scan (efficient for many devices)
Administrator scans a subnet range (for example `192.168.1.1-254`). Discovered candidates can be added intentionally. Devices are **never auto-registered** without approval — this prevents accidental onboarding of unknown hardware.

## 7.4 Field-by-Field Explanation

### Vendor / Manufacturer
Production-ready choices are typically **ZKTeco** and **eSSL** (ZK protocol family). Selecting the correct manufacturer ensures the right adapter behavior.

### Device Name
A friendly label such as `HO-Main-Gate-IN` or `Plant2-Canteen`. Good naming saves hours during support.

### Device IP
IPv4 address on the LAN. Prefer DHCP reservation/static addressing so the device does not silently move.

### Port
Default **4370**. Some environments may use alternate ports; discovery may also probe related ports such as 4371. Confirm with device documentation/network team.

### Communication Password (Comm Key)
Device-side authentication value required to open a management/session channel. Wrong password is one of the top connection failures.

### Timezone
Ensures punch timestamps are interpreted in the correct regional context. Many India deployments use `Asia/Kolkata`.

### Branch
Links the device to an organizational branch for reporting and employee sync targeting.

### Location / Building / Floor / Gate (as used)
Hierarchical placement helps multi-gate sites understand where a punch occurred and which access point is unhealthy.

### Terminal Code
Critical cloud mapping field. Uploaded punches are associated through terminal identity. Keep terminal codes unique and aligned with Brixigo terminal master data.

### Polling Interval
How often the Connector asks the device for new logs. A common default is around **60 seconds**. Shorter intervals increase freshness and device chatter; longer intervals reduce load but delay visibility.

### Model / Serial / MAC / Firmware (informational)
Captured during information read to support inventory and troubleshooting.

## 7.5 Test Connection — What It Proves

A successful test typically proves:

- Network path to IP/port is open.  
- Comm password is accepted.  
- Adapter can perform a basic session.  
- Device is reachable now (not merely configured on paper).

## 7.6 Read Device Information — What Operators Should Record

When available, capture:

| Attribute | Why It Matters |
|-----------|----------------|
| Serial Number | Asset identity / support correlation |
| Firmware | Compatibility and known-issue diagnosis |
| Protocol / SDK path version | Integration expectations |
| User Count | Enrollment capacity planning |
| Attendance Count | Confirms device has punch history |
| Device Time | Detects clock drift early |

Clock drift on devices is a silent payroll killer. If device time is wrong, correct it before go-live.

## 7.7 Save and Activate

After save + successful test:

- Device appears in Device Management.  
- Scheduler includes it in polling.  
- Health monitoring begins.  
- Sync Now can be used for immediate validation.

## 7.8 Practical Naming Convention Example

```
ORG-BRANCH-AREA-DIRECTION

Examples:
  ACME-HO-LOBBY-IN
  ACME-HO-LOBBY-OUT
  ACME-PUNE-FACTORY-GATE1
  ACME-BLR-ITPARK-BASEMENT
```

Consistent names make alerts actionable: “ACME-PUNE-FACTORY-GATE1 offline” is clearer than “Device 3 offline.”

## 7.9 Post-Add Verification Mini-Flow

```
Add Device → Test OK → Sync Now → Local Activity shows punches
        → Upload queue drains → Cloud/HRMS shows raw punches
```

If any stage fails, stop and fix that stage before enrolling more devices.

---

# SECTION 8 — HOW CONNECTOR TALKS TO THE DEVICE

## 8.1 Easy-Language Summary

The Connector opens a network conversation with the biometric machine, proves it is allowed to talk (communication password), asks for new attendance logs, and sometimes reads or writes user records. It repeats this on a schedule and watches whether the device stays healthy.

## 8.2 TCP and Port 4370

Most ZKTeco/eSSL-class deployments use **TCP** to port **4370**.

```
Connector Host                     Biometric Device
192.168.1.20                       192.168.1.50:4370
     |                                    |
     |-------- TCP connect -------------->|
     |<------- session / auth ------------|
     |-------- request logs ------------->|
     |<------- punch records -------------|
     |-------- disconnect / reuse --------|
```

TCP is used because it provides ordered, reliable delivery of the device protocol exchange on the LAN.

## 8.3 Protocol / SDK Role

“SDK” in customer language often means “the software method used to speak the device’s protocol.” In Brixigo Connector, device adapters implement the required protocol conversations for supported manufacturers. The business user does not operate SDK code; they only provide IP, port, password, and manufacturer.

## 8.4 Authentication to Device

Before privileged operations, the Connector authenticates using the device communication password/comm key. If authentication fails:

- Connection test fails.  
- Polling cannot download logs.  
- Alerts/logs show auth-related errors.  

Never share device comm passwords in public tickets without redaction policy.

## 8.5 Polling

Polling means: “Ask the device on a timer whether new punches exist since the last successful cursor.”

```
Every N seconds (per device):
  1. Connect / session
  2. Authenticate
  3. Download logs since last_sync_cursor
  4. Normalize and enqueue new punches
  5. Advance cursor
  6. Mark device online / update health
```

Polling is intentionally pull-based from the Connector. The device does not need to know how to call Brixigo Cloud.

## 8.6 Downloading Attendance Logs

When employees punch, the device stores records locally. The Connector downloads those records, converts them into a normalized punch structure (employee code, time, event type, verification method, terminal metadata), and places them into the local attendance queue.

Duplicate protection uses an idempotency key conceptually formed from manufacturer, device, employee, punch time, and event type. If the same punch is seen again, it is skipped instead of double-uploaded.

## 8.7 Downloading Users

For inventory and support, the Connector can read users present on the device (counts and records as supported). This helps answer: “Is employee X actually present on Gate 1?”

## 8.8 Uploading Users (Enrollment / Sync Direction)

When employee sync is enabled, the Connector can push user create/update/delete/deactivate actions to the device. That is how cloud HR master data becomes device-recognizable identity. Fingerprint/face template capture still happens at the device during physical enrollment, depending on device capabilities and process design.

## 8.9 Health Monitoring

Separately from punch download, the Connector checks whether devices respond, and may capture capacity, firmware/time, and other health signals when available. Results are stored locally and can be pushed to cloud for centralized monitoring.

## 8.10 Heartbeat Versus Device Health

These are different signals:

| Signal | Meaning |
|--------|---------|
| Connector Heartbeat | The Windows agent host is alive and talking to cloud |
| Device Health | A specific biometric terminal is reachable and reporting status |

A site can have connector online + one device offline. Operators must read both.

## 8.11 Timeouts and Resilience (Operator View)

Connections use finite timeouts so a hung device does not freeze the entire system. Multi-device sites poll concurrently within safe worker limits. One bad device should not permanently block all others.

## 8.12 What Operators Should Never Do

- Change device IP without updating Connector settings.  
- Change comm password on device and forget to update Connector.  
- Place devices on isolated Wi-Fi where the Connector PC cannot route to them.  
- Power off the Connector host overnight in shift environments.  

---


# SECTION 9 — HOW CONNECTOR TALKS TO BRIXIGO CLOUD

## 9.1 Easy-Language Summary

After punches are safely stored on the office PC, the Connector packages them and sends them to Brixigo Cloud using encrypted internet communication (HTTPS). It also regularly says “I am alive” (heartbeat), reports device health, pulls employee changes, and accepts verified remote commands.

## 9.2 HTTPS as the Transport

All production cloud communication should use **HTTPS**.

Why HTTPS matters for customers:

- Traffic is encrypted in transit.  
- Server identity can be validated with certificates.  
- Credentials and attendance payloads are protected against casual network snooping.  

HTTP may appear in lab scenarios, but production guidance is HTTPS with certificate verification enabled. Enterprises may supply a custom CA bundle when TLS interception proxies are used.

## 9.3 REST API Style Communication

The Connector uses authenticated REST-style HTTPS calls against the Brixigo biometric enterprise API surface under the configured cloud base URL.

Conceptually, the connector performs operations such as:

- Register / refresh connector identity  
- Fetch remote configuration  
- Upload attendance batches to a terminal endpoint  
- Send heartbeat  
- Send device health / device status  
- Pull and acknowledge commands  
- Pull and acknowledge employee sync events  
- Raise alerts / monitoring feeds  

Administrators do not call these APIs manually during normal operations; the service does.

## 9.4 Authentication Model

Cloud authentication combines several identity pieces:

| Element | Role |
|---------|------|
| Organisation ID | Which customer tenant |
| Connector ID | Which on-site agent instance |
| API Key / Device Token | Secret proving the agent is authorized |
| Optional JWT | Additional bearer authorization when issued |
| Connector Version | Informational compatibility/context header |

If organisation ID or API key is wrong, uploads and registration fail. Support should treat repeated authentication failures as urgent configuration defects, not “network flakiness.”

## 9.5 Connector Secret / Command Signing Secret (HMAC)

Remote command security uses an HMAC signing secret shared between cloud and connector.

Plain explanation:

1. Cloud creates a command (for example sync employees, request health, apply policy action).  
2. Cloud signs canonical command content.  
3. Connector verifies the signature with the local signing secret.  
4. Connector also protects against replay using nonce tracking and time skew checks.  
5. If verification fails, the command is rejected (fail-closed).  

This prevents a random caller on the internet from instructing your connector to change device state, even if they somehow guess an endpoint path.

## 9.6 Heartbeat

Heartbeat is the connector’s periodic check-in.

Typical contents include:

- Connector version and status  
- Host CPU / memory / disk indicators  
- Connected vs offline device counts  
- Last sync markers  
- Pending queue depth  
- Failed upload counters  
- Today’s punch counters  
- Average upload latency  
- Last error summary  
- Restart timing metadata  

Default cadence is commonly around **60 seconds**. Cloud uses freshness windows to decide whether a connector is stale/offline for monitoring.

## 9.7 Attendance Upload

Upload engine behavior in business terms:

1. Read pending punches from SQLite queue.  
2. Group into batches (commonly up to a few hundred records).  
3. Compress payload (gzip) for efficient transfer.  
4. POST to the terminal-scoped push endpoint.  
5. Mark records uploaded on success.  
6. Retry with backoff on transient failures.  
7. Move persistently failing rows to a dead-letter state after max retries so they do not block the healthy queue.

Payloads are shaped for payroll/attendance consumption and include stable punch fields such as employee device code, punch time, event type, verification method, idempotency key, terminal code, and connector/org metadata.

## 9.8 Employee Sync Channel

On a slower interval (commonly around two minutes), the Connector asks cloud for employee change events:

- create  
- update  
- delete  
- deactivate  
- reactivate  

It applies them locally, pushes relevant changes to target devices, and acknowledges events so cloud knows delivery progressed.

## 9.9 Retry, Offline Queue, and Automatic Recovery

```
Internet OK?
   |-- yes --> upload pending batches
   |-- no  --> keep punches in SQLite queue
                continue device polling if LAN is up
                retry uploads when connectivity returns
```

Retry uses limited attempts with exponential backoff and a cap, so a broken network does not create a denial-of-service storm against cloud. After extended failure on a specific row, dead-letter isolation protects the rest of the pipeline.

## 9.10 Encryption and Certificate Trust

| Control | Customer Meaning |
|---------|------------------|
| TLS/HTTPS | Data protected on the wire |
| Certificate verification | Talk to the real Brixigo endpoint, not an impostor |
| Optional mTLS | Mutual certificate authentication for stricter enterprises |
| Encrypted local config | Secrets not stored as plain text on disk |
| Log redaction | Secrets stripped from operational logs |

## 9.11 Complete Cloud Communication Picture

```
Connector Service
   |
   +--> HTTPS Register
   +--> HTTPS Heartbeat (periodic)
   +--> HTTPS Device Health / Status
   +--> HTTPS Attendance Push (batched, gzip)
   +--> HTTPS Employee Sync Pull + Ack
   +--> HTTPS Commands Pull + Ack (HMAC verified)
   +--> HTTPS Alerts / Monitoring (as enabled)
```

All of this is outbound-initiated from the customer network.

## 9.12 What “Cloud Connected” Means Operationally

Cloud connected means:

- DNS resolves the cloud host.  
- TLS handshake succeeds.  
- Authentication headers are accepted.  
- Heartbeat or equivalent lifecycle calls succeed.  

It does **not** automatically mean:

- Every device is online.  
- Every historical punch has already uploaded.  
- Payroll has already finalized the month.  

Those are downstream stages.

---

# SECTION 10 — COMPLETE ATTENDANCE FLOW

## 10.1 End-to-End Business Flow

```
Employee
   |
   v
Finger / Face / Card Scan
   |
   v
Biometric Device verifies identity
   |
   v
Device stores punch locally
   |
   v
Connector polls and reads new punches
   |
   v
SQLite Queue (durable local buffer)
   |
   v
HTTPS Upload to Brixigo Cloud
   |
   v
Raw Attendance Ingest
   |
   v
Attendance Engine
   |
   +--> Shift Matching
   +--> Late Calculation
   +--> Early Out Calculation
   +--> Overtime Calculation
   +--> Attendance Status Decision
   |
   v
Payroll Inputs
   |
   v
Reports / HR Actions / Manager Views
```

## 10.2 Step-by-Step Explanation

### Step 1 — Employee arrives
An employee reaches the gate/lobby/shop-floor terminal at the start of duty, after break, or at exit.

### Step 2 — Biometric verification
The device matches fingerprint, face, card, or PIN according to site policy. Verification success is a device event, not yet a payroll result.

### Step 3 — Device stores the punch
The terminal writes a local attendance record containing at least identity + timestamp (+ event direction when available).

### Step 4 — Connector reads the punch
Within the polling interval, Connector downloads new logs since its cursor and normalizes them.

### Step 5 — Local queue persistence
Each new punch is inserted into SQLite attendance queue with pending status. Duplicates are ignored via idempotency key.

### Step 6 — Upload to cloud
Upload engine sends pending batches to Brixigo Cloud over HTTPS.

### Step 7 — Raw attendance
Cloud stores/accepts the punch as raw attendance evidence tied to organization, connector, terminal, and employee code.

### Step 8 — Attendance Engine processing
Business rules transform raw punches into daily attendance outcomes.

#### Shift Matching
Determines which shift pattern applies (fixed shift, roster, flexible policy, night shift windows, etc.).

#### Late Calculation
Compares first relevant in-punch against shift start + grace rules.

#### Early Out
Compares out-punch against shift end expectations.

#### Overtime
Calculates extra worked duration according to policy thresholds and approval rules where applicable.

#### Attendance Status
Produces statuses such as Present, Absent, Half-day, On Leave (when combined with leave data), Week Off, Holiday interaction, and exception states used by HR.

### Step 9 — Payroll consumption
Payroll uses finalized attendance metrics (payable days, OT hours, penalties, etc.) to compute salary components.

### Step 10 — Reports
HR, plant heads, and auditors view dashboards and statutory/operational reports without manually merging device Excel exports.

## 10.3 Timing Expectations

| Stage | Typical Expectation |
|-------|---------------------|
| Punch captured on device | Immediate |
| Visible in connector activity after poll | Seconds to ~1–2 minutes depending on interval |
| Uploaded when internet healthy | Shortly after enqueue / next upload tick |
| Visible as raw cloud attendance | Near real time after successful upload |
| Final attendance status | Depends on engine rules and day closure policies |
| Payroll impact | According to payroll cycle configuration |

## 10.4 Idempotency and Why It Matters to Payroll

If the same punch were processed twice, an employee might appear to have duplicate ins/outs, corrupting late/OT logic. Idempotency keys ensure re-downloads or retries do not create duplicate business events.

## 10.5 Multi-Device Day Example

```
08:59  Gate-IN punch   (Device A)
13:01  Canteen-OUT     (Device C)   [optional break terminals]
13:45  Canteen-IN      (Device C)
18:05  Gate-OUT punch  (Device B)
```

All can flow through one branch connector, land as raw punches, and be interpreted by attendance policy into one coherent workday.

## 10.6 What Can Still Require Human Review

Automation does not eliminate all exceptions:

- Missing out-punch  
- Wrong shift allocation  
- Proxy punch investigations  
- Device clock corrected mid-day  
- Employee punching on an unassigned terminal  

Brixigo attendance workflows provide exception handling; Connector’s job is to deliver trustworthy raw evidence quickly and completely.

---

# SECTION 11 — WHAT HAPPENS WHEN THE INTERNET IS DOWN

## 11.1 The Guarantee Customers Care About

**Attendance is not lost because the internet is down.**

Employees continue punching on biometric devices. Devices continue storing punches. The Connector, if the LAN is healthy, continues reading punches into the local SQLite queue. Uploads pause or fail temporarily, then resume automatically when connectivity returns.

## 11.2 Offline Behavior Diagram

```
Internet OUTAGE
      |
      v
Employees keep punching on devices
      |
      v
Devices store punches locally
      |
      v
Connector keeps polling LAN devices
      |
      v
Punches accumulate in SQLite queue (pending)
      |
      v
Upload attempts fail transiently / are deferred
      |
      v
Internet RESTORED
      |
      v
Automatic retry with backoff
      |
      v
Queued punches upload in batches
      |
      v
Queue drains → cloud catches up
```

## 11.3 SQLite Queue as the Safety Net

The local database is the system of durability on-site. Once a punch is successfully written to the queue, it survives:

- Temporary ISP failure  
- Cloud maintenance windows  
- Proxy outages  
- Connector process restarts  
- Brief power events (with disk integrity)  

Rows stuck in an intermediate uploading state are recoverable after stale timeouts so crashes during upload do not permanently strand records.

## 11.4 Retry and Backoff

Blindly hammering the cloud during an outage helps no one. The Connector retries with controlled attempts and exponential backoff, then continues later. Persistent poison records can be isolated as dead-letter items for support review without blocking the healthy majority of the queue.

## 11.5 What Operators Should Do During an Outage

1. Confirm devices are still reachable on LAN.  
2. Confirm queue depth is rising (expected).  
3. Avoid panic reinstalls.  
4. Avoid deleting `data\` folders.  
5. When internet returns, watch queue drain and cloud catch-up.  
6. If queue does not drain after connectivity restores, collect logs for support.

## 11.6 What Operators Should Not Do

- Do not format the Connector PC.  
- Do not manually clear the database to “make it healthy.”  
- Do not change Connector ID casually.  
- Do not assume punches are lost without checking device storage + local queue + logs.

## 11.7 Communication Template for HR During Outage

> “Internet to cloud is currently unavailable. Employees should continue punching normally. Attendance is being stored safely and will synchronize automatically when connectivity is restored. Payroll raw punches will catch up after reconnect.”

---

# SECTION 12 — WHAT HAPPENS WHEN THE DEVICE IS OFFLINE

## 12.1 Different Failure Domain

Internet outage and device outage are not the same incident.

| Scenario | Employee Punch Capture | Local Queue Growth | Cloud Upload |
|----------|------------------------|--------------------|--------------|
| Internet down, device up | Yes | Yes (from device polls) | Deferred |
| Device down, internet up | No (on that device) | No new punches from that device | Other devices unaffected |
| Both down | No on that device | No new from that device | Deferred |

## 12.2 Detection

Connector health monitoring detects that a device no longer responds within expected checks. Dashboard/alerts show the device as offline. Cloud device-health freshness windows also help centralized monitoring teams notice silent failures.

## 12.3 Connector Response Pattern

```
Device check fails
      |
      v
Mark device unhealthy / offline locally
      |
      v
Raise alert (UI / cloud as configured)
      |
      v
Continue retrying on schedule
      |
      v
Do not stop other healthy devices
      |
      v
When device returns, resume log download from cursor
```

## 12.4 Important Nuance About Punches During Device Power Loss

If a device is powered off, employees cannot punch on that device until it recovers (unless alternate terminals exist). Punches that already existed on the device before it went offline remain on device storage and can be downloaded after restoration, subject to device memory capacity and retention behavior.

This is why capacity monitoring and periodic sync matter: do not wait weeks to discover a device was full or offline.

## 12.5 Operator Playbook for Offline Device

1. Physical check: power, network cable/Wi-Fi, display status.  
2. Verify IP unchanged.  
3. Ping / port check from Connector host.  
4. Confirm comm password unchanged.  
5. Test Connection from dashboard.  
6. Review connection/health logs.  
7. If device comes back, run Sync Now and verify catch-up.  
8. If device storage was cleared by vendor tooling, escalate immediately — historical punches may be unrecoverable from that device.

## 12.6 Alert Hygiene

Not every momentary blip needs war-room urgency. But repeated offline events on a gate used by hundreds of employees is a business-critical incident because it blocks capture itself, not merely upload.

---

# SECTION 13 — EMPLOYEE ENROLLMENT FLOW

## 13.1 Lifecycle Overview

```
Employee created in Brixigo (HRMS)
                |
                v
Employee sync event sent to Connector
                |
                v
Connector stores employee locally
                |
                v
Connector uploads/applies employee to target device(s)
                |
                v
Employee enrolls biometric template on device
                |
                v
Employee punches successfully
                |
                v
Attendance sync to cloud / engine / payroll
```

## 13.2 Step 1 — Create Employee in Brixigo

HR creates the employee master: code, name, branch, department, status, and other workforce attributes. This is the system of record for employment identity.

## 13.3 Step 2 — Sync to Connector

Employee Sync Service periodically pulls change events from cloud (create/update/delete/deactivate/reactivate). Events are acknowledged after local handling so delivery remains trackable.

## 13.4 Step 3 — Apply to Device

Connector determines target devices using explicit device lists and/or org hierarchy matching (branch/department/gate mapping). It then applies user operations on devices (set/update/delete or privilege demotion patterns for deactivation, depending on action).

## 13.5 Step 4 — Physical Biometric Enrollment

Having a user record on the device is not always the same as having a high-quality fingerprint/face template. Sites typically perform supervised enrollment at the terminal:

- Confirm correct employee code on device.  
- Capture fingerprints/face per policy (often multiple fingers).  
- Test verify once before releasing the employee to daily punching.  

## 13.6 Step 5 — Daily Punching

Enrolled employees authenticate and generate punches as described in Section 10.

## 13.7 Step 6 — Attendance Sync Continues

No special manual export is required for each new employee after the pipeline is live. New punches follow the standard queue/upload path.

## 13.8 Lifecycle Events Beyond Hiring

| HR Event | Expected Connector/Device Effect |
|----------|----------------------------------|
| New joiner | User create on relevant devices + enrollment appointment |
| Transfer to new branch | Update mapping; add to new devices; remove from old when policy says so |
| Name correction | Update user profile fields on devices |
| Deactivation / exit | Remove or demote device access promptly |
| Reactivation | Restore access on approved devices |

## 13.9 Controls and Audit Expectations

Enterprise sites should define:

- Who may enroll biometrics.  
- Whether duplicate finger enrollment across employees is monitored.  
- How quickly leavers are removed from gate devices.  
- Whether contractors use separate terminal groups.  

Connector automation supports the technical path; HR policy governs the human path.

## 13.10 Enrollment Day Checklist

```
[ ] Employee exists in Brixigo with final employee code
[ ] Branch/device mapping confirmed
[ ] Sync cycle completed / forced sync command if used
[ ] User visible on target device
[ ] Biometric captured and verify-tested
[ ] Test punch performed
[ ] Punch visible in connector activity
[ ] Punch visible in cloud raw attendance
```

---

# SECTION 14 — HOW CLIENT USES DAILY

## 14.1 The Ideal Daily Reality

For a well-implemented site, most employees never think about the Connector. They punch. HR opens Brixigo. Payroll runs on time. The Connector remains a quiet background service.

## 14.2 Employee Day Walkthrough

```
Morning
  Employee punches IN at gate
      |
Work begins
      |
Lunch / break punches (if site uses them)
      |
Return punches (if site uses them)
      |
Evening
  Employee punches OUT
      |
Connector automatically polls + queues + uploads
      |
HR views attendance in Brixigo
      |
Payroll uses verified attendance in cycle
```

## 14.3 What HR Does Daily / Periodically

- Review exception queues (missing punches, late trends).  
- Verify newly joined employees are punching.  
- Confirm branch-level attendance percentages.  
- Investigate alert-driven anomalies (sudden zero punches at a gate).  

HR generally does **not** need to click Sync every morning if the service is healthy.

## 14.4 What IT / Admin Does Daily

- Glance at connector status (online, queue depth, alerts).  
- Confirm Windows host is up.  
- Confirm no disk-full condition on log/data volume.  
- Act on device offline alerts quickly for critical gates.

## 14.5 What Plant Leadership Sees

Leadership cares about outcomes, not protocols:

- Who is present?  
- Which lines are understaffed?  
- Are contractors compliant with entry control?  
- Are overtime patterns exploding on a shift?  

Those answers depend on trustworthy punch capture — the Connector’s daily contribution.

## 14.6 Month-End Behavior

Near payroll cutoff:

1. Confirm all connectors online.  
2. Confirm queues are drained.  
3. Confirm no dead-letter backlog without review.  
4. Confirm device clocks sane.  
5. Freeze unnecessary device maintenance that could interrupt capture.  

A green connector estate on cutoff eve prevents classic “device Excel emergency” fire drills.

## 14.7 Multi-Shift Note

For factories with night shifts, “daily use” is continuous. The always-on service model is not optional in those environments; it is foundational.

---

# SECTION 15 — ADMINISTRATOR WORKFLOW

## 15.1 Administrator Responsibilities Map

```
Install → Configure → Monitor → Diagnose → Restart → Update → Backup
```

## 15.2 Install

Perform or supervise Section 5 installation with admin rights, validate service registration, and document install path and host identity.

## 15.3 Configure

- Enter cloud credentials.  
- Register connector.  
- Add devices with correct terminal codes.  
- Set polling intervals appropriate to site scale.  
- Configure proxy if required.  
- Confirm timezone and branch mappings.  
- Harden production secrets and master key handling per Brixigo guidance.

## 15.4 Monitor

Regular checks:

- Service Running  
- Connector Online  
- Device health summary  
- Pending queue depth  
- Failed upload counters  
- Alert tab  
- Disk space for `data\` and `logs\`  

## 15.5 Check Logs

Key log categories under `logs\`:

| Log | Typical Use |
|-----|-------------|
| connector.log | General runtime narrative |
| connection.log | Device/cloud connectivity clues |
| sync.log | Poll/download activity |
| upload.log | Batch upload successes/failures |
| error.log | Consolidated errors |
| crash.log | Fatal failures |
| heartbeat.log | Heartbeat cadence issues |
| health.log | Device/host health |
| service.log | Windows service lifecycle |
| ui.log | Dashboard/tray issues |

Logs rotate daily with retention backups. Redact secrets before sharing externally.

## 15.6 Restart Service

When configuration changes or a stuck state requires it:

- Prefer dashboard action **Restart Service** when available.  
- Or use Windows service controls for `BrixigoConnector`.  

After restart, verify heartbeat resumes and queue processing continues.

## 15.7 Update Connector

Follow Brixigo-provided update packages and release notes. Maintain backups of `data\` before major upgrades. Confirm version appears in heartbeat/UI after update.

## 15.8 Backup

Backup priorities:

1. Encrypted configuration  
2. SQLite database (including queue state)  
3. Documented credentials/secrets in an approved secret store  
4. Device inventory worksheet (IPs, terminal codes, names)  

Test restore conceptually during implementation, not for the first time during an outage.

## 15.9 Access Discipline

Limit who can:

- View API keys / signing secrets  
- Change device comm passwords  
- Uninstall the service  
- Delete data directories  

Administrator workflow is as much governance as it is clicking buttons.

---

# SECTION 16 — SECURITY

## 16.1 Security Design Principles

1. Devices remain on private networks.  
2. Cloud connectivity is outbound HTTPS.  
3. Secrets are encrypted at rest on the connector host.  
4. Remote commands are authenticity-checked (HMAC) and replay-resistant.  
5. Sync ownership is controlled (service engine, UI-only tray when service runs).  
6. Least privilege and auditability guide operations.

## 16.2 HTTPS / TLS / SSL

Production deployments verify TLS certificates. Optional mutual TLS (mTLS) can be enabled for organizations requiring client certificates in addition to token-based auth.

## 16.3 Encrypted Credentials

Device communication passwords and connector settings are stored using strong encryption (Fernet-based protection in the product architecture). A production master key approach is preferred so recovery is controlled by IT, not accidental machine entropy alone.

## 16.4 Machine Binding

When configuration encryption material is derived from machine characteristics, moving disks to another PC without proper key handling can prevent decryption. This is a protection against casual config theft, and a reason backups must include key/secret recovery procedures.

## 16.5 API Keys and Tenant Isolation

Organisation ID + device token/API key confine a connector to its tenant context. Compromised keys should be rotated through Brixigo administrative processes.

## 16.6 HMAC Command Authentication

Signed commands protect the control channel. Missing/wrong signing secret fails closed. Nonce and skew checks reduce replay risk.

## 16.7 Windows Service Security Posture

Running as a service:

- Removes dependence on interactive user login for sync.  
- Allows controlled start/stop by administrators.  
- Supports automatic recovery after failures.  

Service account selection should follow customer IT standards while ensuring access to needed local resources.

## 16.8 Least Privilege Recommendations

- HR operators: dashboard visibility, not secret management.  
- IT admins: service and host management.  
- Implementation consultants: temporary elevated access during go-live only.  
- Vendors/support: controlled remote sessions with ticketed scope.

## 16.9 Logging and Data Minimization

Logs are essential for support but should not become a second secret store. The product redacts common secret patterns. Still, customers should avoid pasting full config screenshots containing keys into mailbox threads.

## 16.10 Network Security Summary

```
Allow:
  - LAN to device ports (4370 etc.)
  - Outbound HTTPS to Brixigo Cloud
Deny / Avoid:
  - Inbound internet to biometric devices
  - Port forwarding device admin ports to WAN
  - Shared use of connector host as a general-purpose risky workstation
```

## 16.11 Physical Security Reminder

A connector host left unlocked in a public lobby is a governance failure no protocol can fully compensate for. Place the host in a restricted IT/admin area where practical.

---


# SECTION 17 — CLIENT TROUBLESHOOTING

## 17.1 How to Use This Section

Find the symptom, review likely causes, apply the least invasive fix first, then escalate with logs if unresolved. Avoid deleting data folders as a first step.

## 17.2 Troubleshooting Table

| Problem | Possible Cause | Solution |
|---------|----------------|----------|
| Cannot connect device | Wrong IP address | Verify device IP on terminal/network docs; update Connector device settings; prefer DHCP reservation |
| Cannot connect device | Wrong port | Confirm port **4370** (or actual configured port); check device network settings |
| Cannot connect device | Host firewall blocking LAN | Allow Connector host to reach device IP/port; test with port connectivity tools approved by IT |
| Cannot connect device | Device and PC on isolated VLANs | Engage network team for routing; place Connector on reachable segment |
| Cannot connect device | Wrong communication password | Reset/confirm comm key on device and in Connector; retest |
| Cannot connect device | Device powered off / network unplugged | Physical inspection; restore power/link |
| Cannot connect device | Duplicate IP conflict | Resolve DHCP conflict; assign unique reserved IP |
| Discovery finds nothing | Wrong subnet range | Scan the correct VLAN range; confirm Connector host is on/routed to that subnet |
| Cloud unreachable | No internet | Check ISP/WAN; validate general browsing/DNS from host |
| Cloud unreachable | DNS failure | Resolve cloud hostname; fix DNS settings |
| Cloud unreachable | Proxy required but not set | Configure proxy URL in settings; validate corporate proxy auth |
| Cloud unreachable | TLS inspection / cert trust issue | Install/configure approved CA bundle; coordinate with IT security |
| Cloud unreachable | Wrong Cloud Base URL | Correct URL for production/staging environment |
| Authentication failed | Wrong API key / device token | Re-enter token from secure credentials sheet; rotate if compromised |
| Authentication failed | Wrong Organisation ID | Confirm tenant ID with Brixigo admin |
| Authentication failed | Connector ID mismatch | Use the issued connector ID; do not invent duplicates casually |
| Commands not applying | Command signing secret mismatch | Align connector signing secret with cloud HMAC secret |
| Connector offline in cloud | Service stopped | Start `BrixigoConnector` service; check service recovery |
| Connector offline in cloud | Host asleep | Disable sleep/hibernate; use always-on policy |
| Tray open but sync idle | Expectation misunderstanding | If service is running, tray is UI-only; service owns sync — verify service status |
| Punches on device but not in cloud | Upload failures | Inspect upload logs/queue; fix auth/network; watch retries |
| Punches on device but not in connector | Polling/auth failure | Test connection; check sync logs; verify cursor progression |
| Queue not draining | Persistent cloud errors | Fix root cause; review dead-letter rows; contact support for reprocess guidance |
| Duplicate concerns | Re-download anxiety | Idempotency keys prevent duplicate business processing of same punch |
| Device offline alerts flapping | Unstable Wi-Fi device link | Prefer wired where possible; improve AP coverage; increase stability before widening poll aggressiveness |
| Wrong attendance times | Device clock drift | Synchronize device time; investigate timezone settings |
| Wrong attendance times | Timezone misconfigured | Set correct timezone on device record/policy |
| Employee cannot punch | Not enrolled / no template | Complete biometric enrollment; verify user exists on device |
| Employee cannot punch | User not synced to device | Check employee sync mapping and device application results |
| Service won’t install | Missing admin rights | Re-run installer as Administrator |
| SmartScreen blocks pack | Windows trust prompt | Unblock file / run elevated from official package |
| High disk usage | Log/data growth | Verify rotation; archive old backups; ensure disk alerts exist |
| Config won’t open after PC change | Machine-bound encryption without master key recovery | Restore using documented master key/backup procedure; contact support |
| SDK / protocol mismatch narrative | Unsupported manufacturer selected | Use supported production manufacturers (ZKTeco/eSSL); escalate for roadmap vendors |
| Terminal upload mapping wrong | Incorrect terminal code | Correct terminal code to match Brixigo terminal master |
| After reboot, long delay | Delayed auto-start waiting for network | Expected design; validate eventual online state; adjust only with IT guidance |
| Cannot see historical punches | Device overwritten/cleared | Prevent uncontrolled device wipes; rely on timely sync; restore from cloud history if already uploaded |

## 17.3 Diagnostics First Approach

Before deep speculation, run dashboard diagnostics concepts:

1. Internet reachable?  
2. DNS OK?  
3. API reachable?  
4. Latency acceptable?  
5. Certificate OK?  
6. Device ping/port OK?  

This splits “office WAN problem” from “device LAN problem” in minutes.

## 17.4 Evidence to Capture Before Escalation

- Connector version  
- Approximate time of first failure  
- Device name/IP/terminal code  
- Whether employees can still punch  
- Queue depth screenshot (no secrets)  
- Redacted logs: error/upload/connection/sync  
- Recent changes (network, password, Windows update, IP change)

## 17.5 Severity Guidance

| Severity | Example | Response Target Mindset |
|----------|---------|-------------------------|
| Critical | Main gate device offline during shift entry | Immediate physical + network response |
| High | Cloud auth failing, queue growing across all devices | Urgent config/network fix |
| Medium | One secondary device intermittent | Same-day stabilization |
| Low | Naming cleanup, non-blocking UI preference | Planned maintenance |

---

# SECTION 18 — SUPPORT WORKFLOW

## 18.1 Purpose of Support

Support exists to restore attendance reliability quickly while protecting security and data integrity. The best support engagements are structured, evidence-based, and staged from least invasive to most invasive.

## 18.2 Standard Support Flow

```
Client reports issue
        |
        v
Ticket opened / severity classified
        |
        v
Collect environment facts + redacted logs
        |
        v
Remote access (if approved) OR guided checks
        |
        v
Check Connector host & Windows service
        |
        v
Check device connectivity & credentials
        |
        v
Check cloud tenant status / auth / terminal mapping
        |
        v
Apply fix / configuration correction
        |
        v
Verify with test punch + queue drain + cloud visibility
        |
        v
Document root cause & preventive action
        |
        v
Close ticket with customer confirmation
```

## 18.3 Collect Logs

Support typically requests relevant files from `logs\` and non-secret configuration summaries. Customers should remove or mask API keys, signing secrets, and passwords before emailing. Prefer secure upload channels provided by Brixigo.

## 18.4 Remote Access

When policy allows:

- Use approved remote support tools.  
- Ensure an authorized customer representative is present.  
- Limit session to the Connector host and needed network tests.  
- Do not casually browse unrelated business files.  
- End session promptly and record actions taken.

## 18.5 Check Connector

Support validates:

- Service state and restart behavior  
- Encrypted config presence  
- Database accessible  
- Heartbeat cadence  
- Queue states (pending/failed/dead)  
- Version mismatches  

## 18.6 Check Device

Support validates:

- Power and network link  
- IP/port reachability from connector host  
- Comm password validity via controlled test  
- Device time  
- User/attendance capacity signals  

## 18.7 Check Cloud

Support/admin validates:

- Tenant credentials validity  
- Connector registration status  
- Terminal code mapping  
- Command signing alignment  
- Whether cloud-side incidents exist  

## 18.8 Resolve and Verify

A fix is not complete until:

1. Test punch succeeds on device.  
2. Connector activity shows the punch.  
3. Upload queue handles it.  
4. Cloud raw attendance shows it.  
5. Monitoring returns to healthy baselines.

## 18.9 Escalation Ladder

1. L1: Guided checklist, service restart, basic network confirmation  
2. L2: Log analysis, credential/terminal mapping, device protocol behavior  
3. L3: Product engineering / cloud platform specialists for defects or data repair  

## 18.10 Support Do-Not List

- Do not delete `connector.db` to “clear errors” without explicit senior approval and backup.  
- Do not reuse another customer’s credentials for testing.  
- Do not disable TLS verification as a permanent workaround.  
- Do not expose device ports to the public internet as a “quick fix.”  

---

# SECTION 19 — COMPLETE END-TO-END FLOW

## 19.1 One-Page Master Diagram

```
┌─────────────┐
│  EMPLOYEE   │
└──────┬──────┘
       │ biometric verification
       v
┌─────────────────────┐
│ BIOMETRIC MACHINE   │  stores punch on device
│ (LAN private IP)    │
└──────┬──────────────┘
       │ TCP 4370 poll/download
       v
┌─────────────────────┐
│ BRIXIGO CONNECTOR   │  Windows Service + Tray
│ Agent on office PC  │
└──────┬──────────────┘
       │ persist
       v
┌─────────────────────┐
│   SQLITE QUEUE      │  durable pending/uploaded/failed/dead
└──────┬──────────────┘
       │ outbound
       v
┌─────────────────────┐
│      HTTPS          │  TLS authenticated REST uploads
└──────┬──────────────┘
       v
┌─────────────────────┐
│   BRIXIGO CLOUD     │  ingest + control plane
└──────┬──────────────┘
       v
┌─────────────────────┐
│ ATTENDANCE ENGINE   │  shift/late/early/OT/status
└──────┬──────────────┘
       v
┌─────────────────────┐
│      PAYROLL        │  salary computation inputs
└──────┬──────────────┘
       v
┌─────────────────────┐
│      REPORTS        │  HR, managers, auditors
└─────────────────────┘
```

## 19.2 Parallel Control Planes (Same Journey, Extra Lanes)

While attendance flows forward, additional lanes run continuously:

```
Connector ──heartbeat──> Cloud monitoring
Connector ──device health──> Cloud device status
Cloud ──signed commands──> Connector actions
Cloud ──employee events──> Connector ──user apply──> Devices
```

## 19.3 End-to-End Success Definition

The ecosystem is healthy when all are true:

- Employees can verify on devices.  
- Connector service is running.  
- Devices are mostly online during operating hours.  
- Queue does not grow without bound.  
- Cloud shows fresh heartbeats.  
- Raw punches appear timely.  
- Attendance statuses calculate correctly.  
- Payroll cycle consumes trusted attendance.  

---

# SECTION 20 — CLIENT TRAINING GUIDE

## 20.1 Training Objectives

After training, a customer team should be able to install confidence, connect at least one device, verify a punch in cloud attendance, enroll an employee, and know how to escalate with the right evidence.

## 20.2 Audience Split

| Role | Focus |
|------|-------|
| IT Admin | Install, service, network, security, backups |
| HR Admin | Employee master, enrollment coordination, attendance verification |
| Plant/Admin Operator | Daily monitoring, device basics, first-line checks |
| Leadership (short session) | Business value, reliability model, what “green” looks like |

## 20.3 Day 1 — Install, Connect, Sync, Verify

### Morning: Concepts (60–90 minutes)
- Why connector exists (private IP / NAT / firewall story)  
- Architecture walkthrough with Section 2 diagram  
- What is automatic vs what needs humans  

### Midday: Hands-on Install (60 minutes)
- Run installer as admin  
- Confirm service `BrixigoConnector`  
- Open dashboard/tray  
- Enter cloud credentials  
- Test connection and registration  
- Confirm online/heartbeat  

### Afternoon: Device + Punch Proof (90 minutes)
- Add one device carefully  
- Test connection  
- Read device info  
- Perform controlled test punches  
- Sync Now  
- Prove punch in connector activity  
- Prove punch in Brixigo cloud/raw attendance  

### Day 1 Exit Criteria
```
[ ] Service running
[ ] Cloud online
[ ] One device online
[ ] One test employee punch visible in cloud
[ ] Team knows where logs live
```

## 20.4 Day 2 — Employee, Enrollment, Attendance, Reports

### Morning: Employee Sync & Enrollment (90 minutes)
- Create/select employee in Brixigo  
- Wait/force sync path as per deployment  
- Confirm user on device  
- Capture biometric templates  
- Verify authentication  

### Midday: Real Attendance Scenarios (60–90 minutes)
- IN punch, OUT punch  
- Late scenario simulation (optional)  
- Missing punch discussion and HR exception handling  
- Multi-device notes if site has separate in/out terminals  

### Afternoon: Operations & Reports (60 minutes)
- Daily monitoring habits  
- Alert interpretation  
- Attendance reports walkthrough in Brixigo  
- Month-end readiness habits  
- Support escalation mini-drill (what to capture)

### Day 2 Exit Criteria
```
[ ] New employee enrolled end-to-end
[ ] Attendance visible in reports path
[ ] Admin can restart service safely
[ ] Team can describe offline internet behavior confidently
[ ] Handover sheet completed (IPs, terminal codes, owners)
```

## 20.5 Training Tips That Improve Retention

- Use the customer’s real device names, not generic “Device1.”  
- Make one successful punch the emotional “aha” moment.  
- Teach failure domains early: device vs internet vs auth.  
- Give a one-page cheat sheet with service name, paths, and contacts.  

## 20.6 Post-Training Reinforcement (First Week)

Day 3: IT checks queue + heartbeat once.  
Day 4: HR verifies random sample punches.  
Day 5: Simulate brief service restart in a safe window.  
End of week: Confirm no unbounded queue growth and no unresolved critical alerts.

---

# SECTION 21 — IMPLEMENTATION CHECKLIST

## 21.1 Before Go-Live Master Checklist

### Host & Install
- [ ] Windows host prepared and always-on policy agreed  
- [ ] Time sync (NTP) validated on host  
- [ ] Official installer/pack used  
- [ ] Installed under approved path  
- [ ] Windows Service `BrixigoConnector` installed  
- [ ] Service set to delayed automatic start  
- [ ] Service recovery policy verified  
- [ ] Tray/dashboard opens successfully  
- [ ] Desktop shortcut available to operators  

### Cloud Connectivity
- [ ] Organisation ID configured  
- [ ] API Key / Device Token configured  
- [ ] Connector ID assigned and recorded  
- [ ] Command Signing Secret aligned with cloud  
- [ ] Cloud Base URL correct  
- [ ] Test Connection successful  
- [ ] Connector registered  
- [ ] Heartbeat observed  
- [ ] Connector shows Online  

### Network & Security
- [ ] LAN connectivity to each device proven  
- [ ] Outbound HTTPS allowed  
- [ ] Proxy configured if required  
- [ ] TLS verification strategy agreed  
- [ ] Secrets stored in approved secret manager  
- [ ] Least-privilege access for operators defined  

### Devices
- [ ] Device inventory sheet completed  
- [ ] Supported manufacturers selected  
- [ ] IPs reserved/static  
- [ ] Ports confirmed  
- [ ] Comm passwords set in connector  
- [ ] Terminal codes unique and mapped in Brixigo  
- [ ] Timezone correct  
- [ ] Branch/location/gate mapping complete  
- [ ] Test Connection OK per device  
- [ ] Device info captured (serial/firmware)  
- [ ] Device clocks validated  

### Workforce & Attendance Proof
- [ ] Employees synced for pilot group  
- [ ] Biometric enrollment completed for pilot group  
- [ ] Test punches successful  
- [ ] Punches visible in connector activity  
- [ ] Upload queue drains  
- [ ] Raw attendance visible in cloud  
- [ ] Attendance engine outcomes validated for pilot  
- [ ] Reports verified for pilot  
- [ ] Payroll test calculation validated on sample (as in scope)  

### Operations Readiness
- [ ] Health monitoring OK  
- [ ] Alert routing/ownership defined  
- [ ] Backup of config/DB credentials process configured  
- [ ] Restore responsibility assigned  
- [ ] Support contact path published  
- [ ] Admin trained on restart/logs  
- [ ] Go-live communication sent to employees/security desk  

## 21.2 Go-Live Day Mini Runbook

1. Confirm service running before first shift.  
2. Confirm all critical gate devices online.  
3. Watch queue for first 30–60 minutes.  
4. Sample-check punches in cloud every hour for first half-day.  
5. Keep IT and HR bridge open for rapid clarification.  
6. At day end, confirm no critical unresolved alerts and queue is stable.

## 21.3 Hypercare (First 7–14 Days)

- Daily health review  
- Rapid response to offline gates  
- Enrollment backlog burn-down for remaining employees  
- Capture recurring issues into site runbook updates  

## 21.4 Formal Acceptance Criteria (Suggested)

Customer accepts go-live when:

- Critical devices online during agreed observation window.  
- Attendance sync delay within agreed expectation for online conditions.  
- No unexplained punch loss in sampled verification.  
- Admin team can perform restart and basic troubleshooting without vendor presence.  

---

# SECTION 22 — FAQ

## General Product

**1. What is Brixigo Connector Agent?**  
It is on-premise Windows software that connects office biometric devices to Brixigo Cloud for attendance and workforce processes.

**2. Is the Connector a cloud application?**  
No. It installs at the customer site and communicates outbound to cloud services.

**3. Which Brixigo products benefit from it?**  
Brixigo HRMS, Brixigo Payroll, and Brixigo Workforce Management through shared attendance trust.

**4. Does every employee need the Connector installed on their laptop?**  
No. One (or a few) always-on site hosts are enough.

**5. Can we use it without internet for a whole month?**  
Devices and local queue can hold continuity for outages, but cloud features, monitoring, and eventual payroll sync require reconnect. Extended isolation should be architected with support.

**6. Is this only for fingerprint devices?**  
It supports biometric terminal families used for attendance (fingerprint/face/card class devices) through supported manufacturers/adapters. Verification method depends on the device.

**7. What manufacturers are production-ready?**  
ZKTeco and eSSL paths are the production-ready focus. Additional vendors may be planned or scaffolded.

**8. What is the default device port?**  
TCP **4370** is the common default.

**9. Do we need to open inbound ports from the internet to devices?**  
No. That is intentionally avoided.

**10. Who owns attendance accuracy — Connector or Attendance Engine?**  
Connector owns reliable capture and delivery of raw punches. Attendance Engine owns business interpretation (late/OT/status).

## Installation & Windows

**11. What is the Windows service name?**  
`BrixigoConnector` (display name: Brixigo Connector Agent).

**12. Why delayed automatic start?**  
So Windows networking is more likely ready before device/cloud connections begin.

**13. Do I need Administrator rights every day?**  
No. Admin rights are needed for install/service changes, not routine dashboard viewing.

**14. Where are files installed by default?**  
Typically under `C:\Program Files\Brixigo\Connector`.

**15. Where is the database?**  
Under the install directory `data\connector.db`.

**16. Where are logs?**  
Under `logs\` with category files and rotation.

**17. Can the tray and service run together?**  
Yes. When service is running, tray should remain UI-only so sync is not duplicated.

**18. What if someone starts only the tray and not the service?**  
Depending on mode/configuration, UI-centered operation may occur, but production sites should run the Windows service as the sync owner.

**19. How do I restart the connector?**  
Use dashboard Restart Service or Windows service stop/start for `BrixigoConnector`.

**20. Will Windows updates break it?**  
Usually not, but reboots can interrupt capture briefly. Plan updates and confirm service restart health.

## Configuration & Cloud

**21. What credentials are required?**  
Organisation ID, API Key/Device Token, Connector ID, Cloud URL, and Command Signing Secret (plus any environment-specific extras).

**22. What is the Command Signing Secret?**  
A shared HMAC secret that authenticates remote commands from cloud to connector.

**23. What happens if Organisation ID is wrong?**  
Authentication/registration against the intended tenant fails or misroutes operationally; fix immediately.

**24. Can one organization have multiple connectors?**  
Yes. Typical for multi-branch networks.

**25. Can two sites share one Connector ID?**  
They should not. Each connector instance needs distinct identity.

**26. What does Online mean?**  
Cloud is receiving fresh connector life signals. Check devices separately.

**27. How often is heartbeat sent?**  
Commonly about every 60 seconds.

**28. Can we use a proxy?**  
Yes, configure proxy settings when corporate policy requires it.

**29. Is HTTPS mandatory?**  
Required for production-quality deployments.

**30. Can we point staging connectors to production cloud by mistake?**  
Yes — and it causes confusion. Guard with environment checklists.

## Devices & Enrollment

**31. Devices are never auto-registered from discovery — why?**  
To prevent accidental onboarding of unknown or unintended hardware.

**32. What is a terminal code?**  
The identity used to map uploaded punches to the correct cloud terminal/business mapping.

**33. Why is device time important?**  
Wrong clocks create wrong attendance timelines and payroll disputes.

**34. How often does polling happen?**  
Per-device interval; a common default is around 60 seconds.

**35. Can one connector manage many devices?**  
Yes, subject to host sizing, network design, and operational monitoring.

**36. Employee exists in HRMS but cannot punch — why?**  
Possibly not synced to device, not biometrically enrolled, wrong device mapping, or deactivated.

**37. Does connector capture fingerprints to cloud automatically?**  
Enrollment processes and template handling follow product capabilities and policy; daily punch sync is about attendance events. Treat biometric template governance carefully.

**38. How are leavers handled?**  
Deactivate/delete sync actions should remove or demote device access according to configured action behavior.

**39. Can contractors use the same devices?**  
Yes if business process allows; map them with clear employee codes and access policies.

**40. What if a device memory fills up?**  
Risk of lost new punches on device. Monitor capacity and keep sync healthy; remediate device storage per vendor guidance.

## Queue, Offline, Reliability

**41. Are punches lost when internet is down?**  
No — once captured on device and queued locally, they are retained for retry/upload.

**42. What is the SQLite queue?**  
A local durable buffer of attendance records waiting to upload or already processed through upload states.

**43. What is a dead-letter punch?**  
A record that exceeded retry limits and is isolated so it cannot block the active queue.

**44. Can dead-letter records be recovered?**  
Often yes with support after root cause is fixed; do not casually delete them.

**45. What happens if power fails on the Connector PC?**  
Queued punches already written to disk remain. Use UPS for better continuity. Device-side punches still depend on device power.

**46. Does the system retry automatically?**  
Yes, with controlled backoff and limits.

**47. Why is my queue growing though internet works?**  
Auth failures, wrong terminal mapping, TLS issues, or cloud-side rejection causes — inspect upload/error logs.

**48. Can duplicate punches be uploaded?**  
Retries are safe because idempotency keys prevent duplicate business acceptance of the same punch identity.

**49. Device offline but connector online — is that possible?**  
Yes. Very common. Investigate that specific device path.

**50. Internet offline but devices online — what should HR tell staff?**  
Continue punching normally; sync will catch up after connectivity returns.

## Security & Compliance

**51. Are credentials stored in plain text?**  
No — configuration and device passwords are stored encrypted.

**52. What is machine binding?**  
Encryption material may be tied to machine characteristics unless a managed master key strategy is used; this reduces casual config portability/theft.

**53. How are remote commands protected?**  
HMAC signatures, nonce/replay protections, and fail-closed verification.

**54. Should we port-forward port 4370 to the internet?**  
No.

**55. Who should have access to signing secrets?**  
Only trusted administrators under secret-management policy.

**56. Do logs contain passwords?**  
Product redacts common secrets, but still review before sharing and avoid screenshotting secret fields.

**57. Can we enable stricter certificate authentication?**  
Enterprises may use mTLS where required.

**58. Is attendance data encrypted in transit to cloud?**  
Yes, via HTTPS/TLS in proper deployments.

## Operations, Support, Payroll

**59. Do HR users need to click Sync Now every morning?**  
No for healthy production service operation; Sync Now is for validation/troubleshooting.

**60. How do we know go-live succeeded?**  
Service online, devices online, test punches visible in cloud, queue stable, reports validated.

**61. What should we back up?**  
Encrypted config, database, credential escrow, and device inventory documentation.

**62. How do we escalate to support?**  
Open ticket with severity, timeline, device details, and redacted logs.

**63. Can support fix a device firmware bug?**  
Support can diagnose and coordinate; firmware defects may require device vendor pathways.

**64. Will Connector calculate overtime?**  
No. Overtime is an Attendance Engine/Payroll policy function after raw punches arrive.

**65. Will Connector mark someone Present/Absent?**  
It delivers punches; status determination is performed by attendance business rules in Brixigo.

**66. Can we run payroll if one branch connector is down?**  
Possibly for other branches, but the affected branch may have incomplete catch-up risk if outage persists. Treat critically before cutoff.

**67. How long after punch should cloud show data?**  
Often near real time under healthy poll/upload conditions (commonly within about a minute or shortly thereafter), network and intervals permitting.

**68. What is the recommended host placement?**  
Restricted IT/admin area, same reachable network as devices, always-on power and network.

**69. Can we virtualize the connector host?**  
Often yes if network adjacency/reachability and always-on characteristics are preserved. Validate with implementation.

**70. What is the single biggest customer mistake?**  
Treating installation as complete without proving a real punch from device → queue → cloud, or exposing devices publicly instead of using the connector bridge model.

---

# SECTION 23 — BEST PRACTICES

## 23.1 Recommended Deployment Pattern

- One connector per site/network segment unless architecture review says otherwise.  
- Wired network for critical gate devices whenever possible.  
- DHCP reservations or static IPs for all terminals.  
- Dedicated always-on host (physical or stable VM).  
- UPS for host and critical network gear.  
- Official installer only.  
- Service-owned sync in production.  

## 23.2 Security Best Practices

- HTTPS with verification in production.  
- Rotate API tokens if staff with secret access exits.  
- Store signing secrets in enterprise secret vaults.  
- Restrict RDP/local admin on connector hosts.  
- Do not reuse device admin passwords across all branches without risk acceptance.  
- Keep host patched on a controlled cadence.  
- Disable sleep states.  
- Avoid using the connector PC as a general browsing workstation.

## 23.3 Network Best Practices

- Document VLAN paths from connector to devices.  
- Monitor DHCP scope exhaustion.  
- Prefer structured cabling over unstable Wi-Fi for turnstiles.  
- Explicitly allow outbound cloud HTTPS and DNS.  
- Test failover WAN behavior if dual-ISP exists.  
- Keep device subnet inventories current.

## 23.4 Backup & Recovery Best Practices

- Nightly backup of `data\` (config + DB) to secure storage.  
- Versioned backups, not a single overwritten file.  
- Quarterly restore drill on a lab host.  
- Document master key recovery steps.  
- Keep offline paper/secure digital inventory of IPs and terminal codes.

## 23.5 Maintenance Best Practices

- Weekly: glance health/alerts/queue depth.  
- Monthly: review offline flapping devices and disk growth.  
- Before payroll cutoff: drain queues and confirm critical gates.  
- After any network change: retest device connectivity.  
- After device password changes: update connector immediately.  

## 23.6 Update Best Practices

- Read release notes.  
- Backup first.  
- Update in a maintenance window for large sites.  
- Validate version, heartbeat, one forced test punch after upgrade.  
- Do not skip multiple major updates blindly without guidance.

## 23.7 Enrollment & HR Process Best Practices

- Finalize employee codes before device enrollment.  
- Enroll with quality capture (clean sensors, supervised).  
- Remove leavers promptly.  
- Pilot first, then wave-roll remaining population.  
- Train security desk on “device down” temporary manual procedures.

## 23.8 Observability Best Practices

- Treat unbounded queue growth as an incident.  
- Distinguish connector online from device online in every status meeting.  
- Keep naming conventions strict for fast alert comprehension.  
- Sample-verify cloud punches daily during hypercare, then periodically.

## 23.9 Anti-Patterns to Avoid

- Port-forwarding biometric devices to WAN.  
- One shared “admin/admin” culture for device passwords.  
- Deleting databases to clear alarms.  
- Running production sync only while someone is logged into Windows UI.  
- Skipping terminal code governance.  
- Assuming discovery auto-adds are safer (they are intentionally not automatic).

---

# SECTION 24 — BUSINESS BENEFITS

## 24.1 Why Customers Choose Brixigo Connector

Brixigo Connector is not merely middleware. It is the operational foundation that makes cloud HR, workforce, and payroll trustworthy for organizations that still depend on physical biometric capture at the door.

## 24.2 Time Saving

Manual USB downloads, vendor utility rituals, and spreadsheet stitching consume hours every month and collapse under multi-branch scale. Automatic polling and upload return that time to HR and IT.

## 24.3 No Manual Upload

Once configured, attendance moves without daily human export/import. People intervene for exceptions, not for routine transport.

## 24.4 Real-Time Attendance Visibility

Managers and HR gain timely presence insight instead of waiting for end-of-day file drops. Workforce decisions become proactive.

## 24.5 Payroll Accuracy

Payroll disputes often start as capture failures: missing punches, delayed files, wrong clocks, duplicate imports. Durable queues, idempotency, and continuous sync materially reduce those failure modes.

## 24.6 Scalability Across Branches

Add devices and sites with repeating patterns: host, connector identity, device inventory, prove a punch, monitor. The architecture is designed for multi-branch organizations rather than a single desktop experiment.

## 24.7 Security by Design

Private devices stay private. Outbound HTTPS, encrypted local secrets, signed commands, and service discipline create an enterprise-defensible posture that “expose the device to cloud directly” approaches cannot match.

## 24.8 Reliability Under Imperfect Networks

Real offices have ISP blips, maintenance windows, and power events. Local queue + retry + service recovery acknowledge reality instead of assuming perfect connectivity.

## 24.9 Better Supportability

Heartbeats, device health, alerts, diagnostics, and category logs mean support can locate failure domains quickly — saving customer downtime and vendor guesswork.

## 24.10 Cleaner Compliance and Audit Story

When auditors ask how attendance evidence travels from finger to payroll, the organization can describe a controlled pipeline with durable storage, secure transport, and business-rule processing — not an ad-hoc chain of personal USB drives.

## 24.11 Strategic Alignment with Cloud HR Transformation

Many enterprises want cloud HRMS/Payroll benefits without ripping out existing biometric estates overnight. Brixigo Connector makes that hybrid journey practical: keep proven devices, modernize the system of record and calculation in Brixigo Cloud.

## 24.12 Value Summary Table

| Benefit | Customer Outcome |
|---------|------------------|
| Time Saving | Less manual attendance operations |
| No Manual Upload | Continuous automatic sync |
| Real-Time Attendance | Faster operational decisions |
| Payroll Accuracy | Fewer missing/duplicate punch disputes |
| Scalable | Multi-device, multi-branch ready |
| Secure | No need to publish devices on internet |
| Reliable | Survives internet interruptions with local queue |
| Supportable | Clear health signals and logs |
| Trainable | Simple operator model after go-live |
| Future-ready | Foundation for workforce and payroll cloud maturity |

## 24.13 Closing Statement for Sales and Executive Audiences

> Brixigo Connector Agent is the trusted on-site bridge between your biometric devices and Brixigo Cloud. It protects your network design, preserves attendance during connectivity problems, and delivers clean punch evidence to Attendance, Payroll, and Reports — automatically, securely, and at enterprise scale.

---

# APPENDIX A — GLOSSARY

| Term | Meaning |
|------|---------|
| Connector Agent | On-premise Brixigo Windows software bridge |
| Tray App | Desktop UI/dashboard process |
| Windows Service | Background sync engine (`BrixigoConnector`) |
| Comm Password | Device communication authentication key |
| Terminal Code | Cloud mapping identity for a device/terminal |
| Polling | Periodic request for new device logs |
| Heartbeat | Periodic connector alive/metrics signal |
| SQLite Queue | Local durable attendance buffer |
| Idempotency Key | Unique punch identity to prevent duplicates |
| Dead Letter | Isolated record after repeated upload failure |
| HMAC | Signature method protecting remote commands |
| Attendance Engine | Cloud business rules over raw punches |
| NAT | Network Address Translation at site edge |
| mTLS | Mutual certificate authentication |

# APPENDIX B — QUICK REFERENCE CARD

```
Service:           BrixigoConnector
Display Name:      Brixigo Connector Agent
Default Port:      4370
Default Cloud:     https://brixigo.com (confirm per tenant)
Install (typical): C:\Program Files\Brixigo\Connector
Data:              data\connector.config.enc , data\connector.db
Logs:              logs\*.log
Supported focus:   ZKTeco , eSSL
Sync owner:        Windows Service (tray UI-only when service runs)
```

# APPENDIX C — ROLE-BASED OPERATING MANUALS

This appendix expands the same ecosystem for different job roles. It does not change architecture; it translates architecture into daily accountability.

## C.1 IT Administrator Operating Manual

### Mission
Keep the bridge alive: Windows host healthy, service running, network paths open, secrets protected, backups recoverable.

### Weekly Rhythm
1. Confirm `BrixigoConnector` service is Running.  
2. Open dashboard status cards: Cloud Online, critical devices Online.  
3. Confirm pending queue is not trending upward without explanation.  
4. Skim Alerts for flapping devices.  
5. Confirm free disk space on the install volume.  
6. Confirm last backup of `data\` completed.

### Monthly Rhythm
1. Review recurring offline devices and escalate cabling/Wi-Fi fixes.  
2. Validate that Windows patches did not leave the service stopped.  
3. Rotate temporary local admin credentials used by contractors if needed.  
4. Re-check proxy/TLS assumptions with security team if policy changed.  
5. Verify connector version against Brixigo recommended release notes.

### Incident First Five Minutes
1. Are employees still able to punch on the physical device?  
2. Is the connector host powered on and networked?  
3. Is the Windows service running?  
4. Is this a device LAN issue or cloud WAN issue?  
5. Capture time, symptom, and redacted screenshot/log pointers.

### Communication Template to HR
> “Capture is continuing on devices. Synchronization to Brixigo is delayed because [internet/cloud auth/device network]. We are not deleting any local queue data. ETA for next update: [time].”

## C.2 HR Administrator Operating Manual

### Mission
Ensure every active employee can punch on the correct device and that attendance outcomes in Brixigo are trustworthy for payroll and managerial decisions.

### Daily Rhythm
1. Spot-check attendance for a sample of employees across shifts.  
2. Review missing punch / exception queues in Brixigo.  
3. Confirm new joiners scheduled for enrollment actually completed enrollment.  
4. Confirm exited employees no longer appear as unexpected presents due to stale device access.

### Enrollment Coordination Script
1. Employee code finalized in Brixigo.  
2. Branch and gate mapping confirmed with IT.  
3. Wait for sync / request sync confirmation.  
4. Escort employee to terminal.  
5. Capture biometric quality samples.  
6. Immediate verify test.  
7. Controlled IN punch.  
8. Confirm visibility in Brixigo.  
9. Sign enrollment completion checklist.

### What HR Should Not Change Directly on Devices
Avoid uncontrolled manual user edits on terminals that conflict with cloud master data, unless following an approved emergency procedure. Dual maintenance creates identity drift.

## C.3 Plant / Security Desk Operating Manual

### Mission
Keep gates flowing. Detect “device dead” quickly. Follow fallback attendance procedure only when capture itself is impossible.

### If Device Screen Is Dark / Unresponsive
1. Check power adapter and socket.  
2. Check network cable lights / Wi-Fi indicator.  
3. Inform IT with device name and location.  
4. Do not reboot repeatedly without noting times.  
5. If policy allows temporary manual register, use the approved paper/app fallback and later reconciliation process.

### If Employees Say “Finger Not Accepting”
1. Confirm correct terminal (IN vs OUT, contractor vs staff).  
2. Retry with guidance on finger placement.  
3. Check whether employee is newly joined and possibly not enrolled.  
4. Escalate to HR enrollment desk — not to random device menu experimentation.

## C.4 Payroll Operating Manual

### Mission
Consume trusted attendance outcomes; escalate capture gaps early, not on salary-processing day only.

### Cutoff Minus Five Business Days
1. Ask IT for connector estate health summary.  
2. Confirm no branch has growing dead-letter backlog.  
3. Confirm known device outages have reconciliation plans.  
4. Freeze nonessential device firmware experiments.

### Cutoff Minus One Business Day
1. Confirm queues drained for all critical sites.  
2. Confirm attendance exceptions are owned.  
3. Confirm no connector left offline overnight without watch.

---

# APPENDIX D — MULTI-SITE ROLLOUT PLAYBOOK

## D.1 Rollout Philosophy

Do not activate twenty factories on the same morning. Use a pilot site, then wave deployment.

```
Wave 0: Lab / UAT connector + one test device
Wave 1: Pilot production site (one branch)
Wave 2: Similar network twin sites
Wave 3: Complex multi-VLAN / multi-gate sites
Wave 4: Long-tail small offices
```

## D.2 Pilot Success Metrics

| Metric | Example Target |
|--------|----------------|
| Connector uptime during pilot week | >= 99% during operating hours |
| Critical device online percentage | >= 98% |
| Median punch-to-cloud visibility | Within agreed near-real-time window when online |
| Enrollment completion for pilot roster | 100% of in-scope employees |
| Unexplained punch loss in sample audit | Zero |

## D.3 Site Handover Pack Contents

Every site should leave implementation with:

1. Host name / IP / physical location.  
2. Connector ID.  
3. Device inventory table (name, IP, port, terminal code, branch, gate).  
4. Credential escrow confirmation (not plaintext email).  
5. Network allowlist notes.  
6. Local owners: IT primary, HR primary, security desk contact.  
7. Fallback attendance SOP.  
8. Support ticket channel and severity definitions.  

## D.4 Cross-Site Naming Standard

```
Connector ID idea:  ORG-SITE-CONN01
Device name idea:   ORG-SITE-GATE-DIRECTION
Terminal code idea: aligned to Brixigo terminal master, never improvised casually
```

Consistent naming turns nationwide monitoring from noise into signal.

## D.5 Central Governance Committee (Recommended)

Monthly 30-minute forum:

- Open critical incidents  
- Devices pending replacement  
- Enrollment backlog  
- Version compliance  
- Upcoming network changes that could break LAN paths  

---

# APPENDIX E — SCENARIO WORKSHOPS (TRAINING EXERCISES)

Use these workshops during Day 2 training or hypercare refreshers.

## Workshop 1 — “Internet Died at 10:40”

Facilitator prompt: ISP fails mid-morning. Employees keep punching.

Expected trainee answers:
- Devices still store punches.  
- Connector continues LAN polling if host/LAN healthy.  
- Queue grows.  
- No data wipe.  
- After restore, automatic upload catch-up.  
- HR messaging: continue punching.

Pass criteria: trainees do not propose uninstall/reinstall as first action.

## Workshop 2 — “Main Gate Shows Offline”

Facilitator prompt: Connector online, Gate-1 offline, Gate-2 online.

Expected trainee path:
- Physical power/network check.  
- Confirm IP.  
- Test connection.  
- Do not blame cloud first.  
- Keep Gate-2 trusted.  
- Consider temporary redirection of staff to Gate-2 if policy allows.

## Workshop 3 — “New Joiner Cannot Punch”

Expected path:
- Confirm employee in Brixigo.  
- Confirm sync to connector/device.  
- Confirm biometric enrolled.  
- Test verify.  
- Controlled punch.  
- Confirm cloud visibility.

## Workshop 4 — “Payroll Says OT Is Wrong”

Expected clarification:
- Connector delivers raw punch times.  
- OT is attendance policy/payroll logic.  
- First verify punch timestamps and device clock.  
- Then escalate to attendance rule owners if punches are correct.

## Workshop 5 — “Someone Changed Device Password”

Expected path:
- Update communication password in connector device settings.  
- Retest connection.  
- Resume sync.  
- Document change in site log.  
- Avoid leaving old password in handover sheets.

---

# APPENDIX F — RACI FOR BIOMETRIC ATTENDANCE OPERATIONS

| Activity | Customer IT | Customer HR | Security Desk | Brixigo Implementation | Brixigo Support | Brixigo Cloud Admin |
|----------|-------------|-------------|---------------|------------------------|-----------------|---------------------|
| Host provisioning | A/R | C | I | C | I | I |
| Connector install | R | I | I | A/C | C | I |
| Cloud credentials issuance | I | C | I | C | I | A/R |
| Device network readiness | A/R | I | C | C | I | I |
| Device onboarding in connector | R | C | C | A/C | C | I |
| Employee master data | C | A/R | I | C | I | C |
| Biometric enrollment execution | C | A/R | R | C | I | I |
| Daily monitoring | R | C | C | I | C | C |
| Incident L1 | R | C | C | I | C | I |
| Incident L2/L3 | C | I | I | C | A/R | C |
| Payroll cutoff readiness | C | A/R | I | C | C | C |
| Backup/restore ownership | A/R | I | I | C | C | I |

Legend: R = Responsible, A = Accountable, C = Consulted, I = Informed

---

# APPENDIX G — DETAILED FIELD DICTIONARY FOR CLIENT FORMS

## G.1 Cloud Settings Fields

| Field | Business Meaning | Failure If Wrong |
|-------|------------------|------------------|
| Cloud Base URL | Which Brixigo environment to talk to | Completely wrong destination or TLS failure |
| Organisation ID | Customer tenant identity | Auth failure / wrong tenant risk |
| Connector ID | This host’s agent identity | Monitoring confusion; command targeting errors |
| API Key / Device Token | Proof of authorization | Upload/register denied |
| Command Signing Secret | Verifies remote commands | Commands rejected; automation features fail closed |
| Proxy URL | Corporate web egress path | Cloud unreachable in locked-down networks |

## G.2 Device Fields

| Field | Business Meaning | Failure If Wrong |
|-------|------------------|------------------|
| Manufacturer | Which adapter language to speak | Session/protocol failures |
| Name | Human operations label | Slow incident response |
| IP | Where the device lives on LAN | Cannot connect |
| Port | Device listener port | Cannot connect |
| Communication Password | Device auth secret | Auth failed |
| Terminal Code | Cloud terminal mapping | Punches land incorrectly or reject |
| Timezone | Time interpretation context | Shift/late distortions |
| Branch/Location/Gate | Org placement | Wrong reports and sync targeting |
| Polling Interval | Freshness vs load tradeoff | Too slow visibility or unnecessary chatter |

## G.3 Operational Status Fields Operators Should Understand

| Status Concept | Meaning |
|----------------|---------|
| Service Running | Windows engine process is up |
| Connector Online | Cloud sees fresh heartbeat |
| Device Online | Terminal responds to health/poll |
| Pending Queue | Punches waiting to upload |
| Failed Uploads | Transient or recurring upload problems |
| Dead Letter | Isolated stubborn records needing review |
| Last Sync | When device logs were last successfully processed |

---

# APPENDIX H — SALES DEMONSTRATION SCRIPT

## H.1 Demo Goal

In 20–30 minutes, a prospect should understand: why a connector is required, how attendance remains safe offline, and how Brixigo turns punches into workforce and payroll outcomes.

## H.2 Suggested Demo Storyboard

1. **Pain slide:** manual downloads, delayed payroll, firewall objections.  
2. **Architecture ASCII:** device → connector → cloud → attendance → payroll.  
3. **Live or recorded UI:** connector online card.  
4. **Add/test device** (or show already connected pilot device).  
5. **Employee punch** on a real/demo terminal.  
6. **Watch activity/queue.**  
7. **Show cloud raw punch.**  
8. **Show attendance interpretation.**  
9. **Unplug WAN metaphor / offline story** with queue explanation.  
10. **Security close:** no inbound device exposure.  
11. **Commercial close:** implementation checklist and timeline.

## H.3 Objection Handling

**Objection:** “Our devices can push to cloud themselves.”  
**Response:** Some can in limited modes, but enterprise customers still need durable queue governance, organizational mapping, signed control, multi-vendor consistency, and outbound-only security. Connector standardizes that layer for Brixigo.

**Objection:** “We can email Excel exports.”  
**Response:** Excel is a process, not a control system. It fails silently, duplicates easily, and does not scale to multi-gate factories with shift rigor.

**Objection:** “We do not want another always-on PC.”  
**Response:** The always-on host replaces many hidden manual costs and reduces payroll fire drills. A small stable host is cheaper than recurring attendance disputes.

---

# APPENDIX I — SUPPORT SEVERITY MODEL AND RESPONSE EXPECTATIONS

| Severity | Definition | Example | Customer Interim Action |
|----------|------------|---------|-------------------------|
| S1 | Capture broken for critical workforce entry | Main gate offline at shift start | Redirect to backup gate if any; start fallback register; call support immediately |
| S2 | Capture works but sync broken broadly | Cloud auth failure, queue rising on all devices | Keep punching; do not wipe data; open urgent ticket |
| S3 | Partial impairment | One non-critical device flapping | Move traffic if possible; schedule fix same day |
| S4 | Guidance / cosmetic / request | Rename devices, training question | Ticket with business context |

Support cannot promise outcomes that require on-site cabling vendors, but can direct diagnosis precisely so the right team is mobilized.

---

# APPENDIX J — CHANGE MANAGEMENT GUIDE

## J.1 Changes That Always Require Retest

- Device IP change  
- Device communication password change  
- Connector host migration  
- Cloud URL / environment change  
- Terminal code change  
- Major network ACL change  
- Connector version upgrade  

Retest means: Test Connection + controlled punch + cloud visibility confirmation.

## J.2 Communication Before Change Windows

Notify HR and security desk:

- When change starts/ends  
- Whether brief punch disruption is expected  
- Whether fallback attendance procedure is activated  
- Who is bridge owner during the window  

## J.3 Post-Change Verification Evidence

Keep a short record:

```
Date/time:
Change:
Devices tested:
Test employee codes:
Punch visible in connector: Y/N
Punch visible in cloud: Y/N
Queue depth after test:
Issues found:
Sign-off:
```

---

# APPENDIX K — HEALTHY SITE SCORECARD

Use this scorecard weekly during hypercare and monthly thereafter.

| Area | Green | Amber | Red |
|------|-------|-------|-----|
| Connector service | Always running | One unexpected restart | Repeated down |
| Heartbeat freshness | Continuous | Occasional gaps | Stale/offline |
| Critical devices | Online | Intermittent | Down in peak hours |
| Queue depth | Near zero / stable | Elevated but draining | Monotone rising |
| Dead letters | None / explained | Small backlog owned | Growing unexplained |
| Enrollment backlog | None | Managed | Blocking joiners |
| Backup | Verified recently | Exists untested | Missing |
| Payroll readiness | Confident | Minor risks | High cutoff risk |

A site can be “cloud green” and still “device red.” Scorecard forces both lenses.

---

# APPENDIX L — EXTENDED NARRATIVE: A WEEK IN THE LIFE OF ATTENDANCE

### Monday
Factory opens. Connector already running from Sunday night. Gate devices healthy. Employees punch IN. Within about a minute, punches move device → queue → cloud. HR sees expected headcount forming.

### Tuesday
Brief ISP blip at 11:10 for 20 minutes. Queue climbs. Nobody notices on the shop floor because punching continues. When WAN returns, upload engine drains the backlog. HR messaging not required because duration was short and catch-up automatic.

### Wednesday
New joiner induction. HR creates employee in Brixigo. Sync applies user to Plant Gate devices. Security desk enrolls two fingers and verifies. First official punch appears in reports path same day.

### Thursday
One canteen device Wi-Fi flap creates offline alerts. IT finds weak AP coverage. Device temporarily moved to wired drop. No payroll impact because gate IN/OUT terminals remained healthy.

### Friday
Before weekend, admin confirms service running, queue low, backups completed. Leadership reviews weekly attendance exceptions in Brixigo rather than chasing USB files from guards.

This is the operating dream state the product is designed to create: **quiet reliability**.

---

# APPENDIX M — IMPLEMENTATION INTERVIEW QUESTIONS (DISCOVERY)

Ask these before promising go-live dates:

1. How many sites and how many devices per site?  
2. Which manufacturers/models are in production?  
3. Are devices wired, Wi-Fi, or mixed?  
4. Are IN and OUT on separate terminals?  
5. What is the current attendance collection ritual?  
6. Who owns device admin passwords today?  
7. Is there an always-on PC/server per site?  
8. Is outbound internet filtered by proxy?  
9. Are there multi-VLAN separations between IT and turnstiles?  
10. What is payroll cutoff cadence and tolerance for late punches?  
11. How are contractors managed?  
12. What fallback exists when a gate fails today?  
13. Are device clocks known to drift?  
14. Is there an asset register with IPs and serials?  
15. Who will be trained as primary operator?

Answers determine whether a site is a two-day implementation or a phased network remediation project.

---

# APPENDIX N — CUSTOMER COMMUNICATION PACK (READY-TO-SEND TEXT)

## N.1 Go-Live Announcement (Employees)

> From [date], attendance punching continues on the same biometric devices. Backend synchronization to Brixigo is now automated. Please punch as usual at your assigned gates. If a device is unavailable, follow instructions from Security Desk for the backup gate or approved fallback process.

## N.2 Go-Live Announcement (Managers)

> Attendance data will flow automatically from biometric terminals to Brixigo. Please review exception queues daily during the first week and escalate missing punch patterns early. Do not request manual Excel extracts unless Support confirms a capture outage.

## N.3 Outage Notice (Internet)

> Internet connectivity to Brixigo is currently impaired. Please continue punching on biometric devices. Attendance is being retained for automatic synchronization after recovery. Next update at [time].

## N.4 Outage Notice (Device)

> The [Gate Name] terminal is temporarily unavailable. Please use [Backup Gate] / follow Security Desk instructions. IT ticket [#] is in progress.

---

# APPENDIX P — DETAILED ATTENDANCE EXCEPTION CATALOG

This catalog helps HR and Support interpret issues that appear “after” Connector delivery. Many disputes are policy outcomes, not transport failures. Always verify raw punch presence first.

## P.1 Exception Types and First Diagnostic Question

| Exception Seen in HRMS/Payroll | First Question | If Raw Punch Missing | If Raw Punch Present |
|--------------------------------|----------------|----------------------|----------------------|
| Absent despite working | Did the employee punch any terminal? | Investigate device/enrollment/connector path | Investigate shift rules / day type / leave clash |
| Late marked incorrectly | Is punch timestamp correct on device and cloud? | Fix capture/clock path | Review grace minutes and shift start |
| Early out incorrect | Was OUT punch captured? | Device/out-gate issue | Review shift end and early thresholds |
| Overtime missing | Were extra minutes actually punched? | Capture gap | OT policy thresholds/approvals |
| Duplicate presence artifacts | Are unique punches duplicated in raw? | Idempotency/support review | Rule interpreting multiple gates oddly |
| Wrong branch attendance | Which terminal code produced the punch? | Mapping/onboarding error | Employee org assignment mismatch |

## P.2 “Raw Punch Present” Proof Standard

A raw punch is considered present when all are true:

1. Activity/log on connector shows the employee code and time.  
2. Queue state shows uploaded (or cloud already accepted).  
3. Brixigo raw attendance / punch explorer shows the same event.  

If (1) exists but (3) does not, treat as upload/mapping incident.  
If (1) does not exist but device history shows it, treat as poll/download incident.  
If device history also lacks it, treat as capture/enrollment/device incident.

## P.3 Night Shift Special Notes

Night shifts often cross calendar midnight. Training audiences should understand:

- A punch at 23:50 and 06:10 may belong to one logical duty window.  
- Wrong timezone or device clock changes can shatter that window.  
- Attendance Engine policies — not Connector — decide which date the shift belongs to.  
- During go-live, validate at least one night-shift employee end-to-end.

## P.4 Contractor and Visitor Considerations

If contractors punch on shared gates:

- Ensure distinct employee/contractor codes.  
- Ensure deactivation is prompt at contract end.  
- Decide whether contractor attendance feeds payroll, gate reports only, or both.  
- Avoid temporary handwritten codes that never enter Brixigo master data.

---

# APPENDIX Q — CAPACITY AND SIZING GUIDANCE (NON-CODE)

## Q.1 Sizing Dimensions That Matter

1. Number of devices per connector.  
2. Punches per day per device.  
3. Polling interval aggressiveness.  
4. Network quality to devices.  
5. WAN quality to cloud.  
6. Whether employee sync volume is high (mass joiner waves).  

## Q.2 Practical Guidance

| Site Profile | Typical Pattern | Guidance |
|--------------|-----------------|----------|
| Small office (1–2 devices) | Low punch volume | Single always-on PC is enough |
| Mid branch (3–10 devices) | Moderate volume | Dedicated stable host recommended |
| Large plant (10+ devices, multi-gate) | High concurrency at shift bells | Stronger host, wired gates, careful poll planning, active monitoring |
| Multi-building campus | Segmented networks | Confirm routing; consider multiple connectors by segment if needed |

## Q.3 Shift-Bell Peak Awareness

Many factories generate a burst of punches in a few minutes at shift start. Architecture already batches uploads and polls per device, but operators should expect short queue spikes at bells that drain quickly when WAN is healthy. A spike that does not drain is an incident; a spike that drains is normal elasticity.

---

# APPENDIX R — SECURITY AWARENESS BRIEF FOR CUSTOMER STAKEHOLDERS

## R.1 What We Protect

- Employee attendance evidence integrity  
- Device access control identities  
- Cloud tenant credentials  
- Remote command authority  
- Network boundary (no need to publish terminals)

## R.2 What Customers Must Protect

- The connector host physically and logically  
- API tokens and signing secrets  
- Device communication passwords  
- Backup locations containing encrypted config/DB  
- Remote support session approvals  

## R.3 Social Engineering Warning

Attackers may impersonate “biometric vendor support” and ask for:

- Comm passwords  
- Screenshare with unrestricted admin  
- Disabling firewall “temporarily forever”  
- Public port forwards “just for testing”

Customer policy should require ticketed identity verification before any such change.

## R.4 Minimum Security Baseline Checklist

```
[ ] Connector host not used as open kiosk
[ ] Local admin limited
[ ] Disk encryption per IT standard where possible
[ ] HTTPS verification enabled in production
[ ] Secrets in vault / controlled handover only
[ ] No WAN port-forward to device 4370
[ ] Backup access restricted
[ ] Leavers removed from connector admin groups
```

---

# APPENDIX S — FULL GO-LIVE TIMELINE EXAMPLE (TWO-WEEK)

### Week -2
- Discovery interview completed  
- Device inventory draft  
- Host nominated  
- Firewall/proxy request raised  

### Week -1
- Host prepared always-on  
- Credentials issued into vault  
- Install in UAT/pilot mode  
- One device connected in controlled test  
- Training Day 1 scheduled  

### Go-Live Week Day 1–2
- Training Day 1 and Day 2  
- Pilot employee group enrolled  
- Punch proof signed  

### Go-Live Week Day 3–4
- Remaining critical gates onboarded  
- Enrollment waves continue  
- Hypercare bridge active  

### Go-Live Week Day 5
- Scorecard review  
- Open risks listed with owners  
- Payroll stakeholder informed of readiness state  

### Week +1
- Daily scorecard  
- Reduce war-room intensity if green  
- Convert lessons into site runbook  

This timeline compresses or expands with network readiness. A site with unknown IPs and missing passwords is not a software delay; it is a discovery delay.

---

# APPENDIX U — COMPARISON: LEGACY ATTENDANCE COLLECTION VS BRIXIGO CONNECTOR

## U.1 Side-by-Side Operating Model

| Topic | Legacy Manual / Utility Model | Brixigo Connector Model |
|-------|-------------------------------|-------------------------|
| Who moves punches | A person remembers to download/export | Windows service moves punches continuously |
| Where punches wait during outage | Often stuck on device until someone visits | Device + local SQLite queue with automatic retry |
| Multi-device operations | Repeated manual steps per device | Scheduler handles many devices |
| Security posture | Temptation to expose devices or share USB sprawl | Outbound HTTPS bridge; devices stay private |
| Duplicate risk | High with repeated file imports | Idempotency keys reduce duplicate business events |
| Visibility | Delayed files | Near-real-time when online |
| Supportability | “Send us the Excel” | Logs, heartbeat, device health, queue states |
| Payroll cutoff behavior | Panic collection | Planned health + drained queues |
| Scalability | Linear human effort | Repeatable site pattern |

## U.2 Cost-of-Delay Narrative for Executives

Every delayed punch pipeline creates hidden cost:

- HR hours spent chasing guards and USB sticks  
- Manager disputes about who was present  
- Payroll re-runs and exception meetings  
- Overtime leakage or underpayment risk  
- Audit findings on weak attendance controls  

The Connector does not eliminate all HR judgment, but it removes the weakest link: **unreliable transport of biometric evidence**.

## U.3 What Changes for Each Stakeholder After Adoption

**Employees:** Usually nothing visible; same devices, same punch habit.  
**Security Desk:** Clearer offline device escalation path; fewer ad-hoc Excel requests.  
**HR:** More time on exceptions and workforce decisions, less time on file logistics.  
**IT:** One monitored service per site instead of tribal utility knowledge.  
**Payroll:** Cleaner inputs and earlier warning when a site is unhealthy.  
**Leadership:** Attendance as an operational signal, not a month-end surprise.

## U.4 Adoption Success Signs in the First Month

1. Ticket volume shifts from “please upload attendance” to “please explain this exception.”  
2. Enrollment becomes a standard joiner checklist item.  
3. Device offline alerts are treated like gate outages, not ignored noise.  
4. Payroll cutoff week is calmer.  
5. Multi-site monitoring uses consistent connector/device naming.

If after thirty days teams still manually export device logs as routine, adoption is incomplete — revisit training and ownership, not only software settings.

---

# APPENDIX V — GLOSSARY EXPANSION FOR CLIENT WORKSHOPS

| Term | One-line teaching definition |
|------|------------------------------|
| Always-on host | PC/server that must stay powered for continuous sync |
| Bridge | Connector role between private devices and public cloud |
| Catch-up | Automatic upload of queued punches after outage |
| Cursor | Connector memory of how far device logs have been read |
| Dead letter | Punch isolated after repeated upload failure |
| Delayed auto-start | Service starts after Windows network is more ready |
| Discovery | Scan that finds candidate devices without auto-adding them |
| Fail-closed | Security behavior that rejects unverifiable commands |
| Heartbeat | Regular “I am alive” signal to cloud |
| Hypercare | Intensified support period right after go-live |
| Idempotency | Same punch processed once despite retries |
| LAN | Local office network where devices live |
| NAT | Router technique that hides private IPs from internet |
| Polling | Connector asking device for new punches on a timer |
| Raw attendance | Punch evidence before business status calculation |
| Signing secret | Shared secret used to verify remote commands |
| Terminal code | Business/cloud identity of a device endpoint |
| UI-only mode | Tray runs without starting a second sync engine |
| Wave rollout | Staged multi-site deployment approach |

Use this table as a laminated handout during Day 1 concept training.

---

# APPENDIX X — QUICK EXECUTIVE BRIEF (ONE PAGE NARRATIVE)

Brixigo Connector Agent is the on-site software that makes biometric attendance usable by Brixigo HRMS, Workforce Management, and Payroll without weakening corporate network security.

Biometric machines remain on private office networks. Cloud applications cannot dial into addresses like `192.168.x.x` through NAT and firewalls safely. The Connector solves that by living beside the devices, reading punches over the LAN, storing them durably if the internet is unavailable, and uploading them to Brixigo Cloud over HTTPS when connectivity exists.

Once punches reach the cloud as raw attendance, the Attendance Engine applies shift, late, early-out, overtime, and status rules. Payroll and reports then consume trusted outcomes. Employees keep punching as usual. HR spends less time collecting files and more time managing exceptions. IT monitors one Windows service and device health instead of tribal USB processes.

A successful deployment is not “installer finished.” Success is proven when a real punch travels device → connector queue → cloud → attendance outcome, the service stays always-on, secrets are governed, and site owners can run basic monitoring without vendor presence.

That is the complete business promise of the Brixigo biometric connector ecosystem: **secure bridge, durable sync, payroll-ready attendance evidence.**

---

# APPENDIX Z — READER PATH INDEX

| If you are… | Start here | Then read |
|-------------|------------|-----------|
| Sales / pre-sales | Sections 1, 2, 3, 24 | Appendix H, U, X |
| Implementation consultant | Sections 4–10, 21 | Appendices D, M, S |
| Customer IT admin | Sections 5, 6, 15, 16, 17 | Appendices C.1, J, R |
| Customer HR admin | Sections 10, 13, 14, 20 | Appendices C.2, P, N |
| Support engineer | Sections 11, 12, 17, 18, 22 | Appendices I, G, K |
| Internal developer (orientation only) | Sections 2, 8, 9, 16 | Existing technical docs in repo `connector/docs` and `cloud/docs` |
| Executive sponsor | Section 24 + Appendix X | Section 3 and Appendix U |

This index keeps a long document usable. The product story is one pipeline; the reader paths differ by responsibility.

**Documentation metrics (approximate):** 24 primary sections, 70 FAQ entries, extended appendices A–Z for training, support, sales, and implementation enablement.

**How to maintain this document:** When product behavior changes (new supported manufacturer, default interval changes, installer packaging changes), update the affected section and appendices in the same release cycle as customer packaging. Do not maintain a second conflicting “sales-only” architecture story. One ecosystem truth serves training, support, implementation, and demos alike.

**Companion materials already in the product repository (for specialists):** customer installation notes, admin guides, troubleshooting references, backup/restore guidance, and cloud support manuals under `connector/docs` and `cloud/docs`. This Complete Product Documentation is the cross-audience narrative layer above those specialist guides.

---

# APPENDIX AA — FINAL DOCUMENT CONTROL

| Version | Notes |
|---------|-------|
| 1.0 | Sections 1–24 complete product documentation for Connector Agent 3.0.2 |
| 1.1 | Role manuals, rollout playbook, workshops, RACI, sales script, scorecards, communication pack |
| 1.2 | Exception catalog, sizing guidance, security brief, two-week timeline |
| 1.3 | Legacy-vs-connector comparison, adoption signs, workshop glossary expansion |
| 1.4 | Executive brief and reader path index; documentation finalized for multi-audience enablement |

---

**End of Document — Brixigo Connector Agent Complete Product Documentation**

*© Brixigo. For customer training, implementation, support enablement, and sales demonstration use. No source-code implementation content included by design.*
