"""Generate Cloud Missions end-to-end integration PDF report."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).resolve().parents[1] / "docs" / "Brixigo_Cloud_Missions_End_to_End_Report.pdf"

BRAND = HexColor("#0F766E")
BRAND_DARK = HexColor("#134E4A")
MUTED = HexColor("#475569")
LIGHT = HexColor("#F1F5F9")
BORDER = HexColor("#CBD5E1")


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="CoverTitle",
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=28,
            textColor=BRAND_DARK,
            alignment=TA_CENTER,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="CoverSub",
            fontName="Helvetica",
            fontSize=12,
            leading=16,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H1Custom",
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=BRAND_DARK,
            spaceBefore=14,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2Custom",
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            textColor=BRAND,
            spaceBefore=10,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyCustom",
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=HexColor("#0F172A"),
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BulletCustom",
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=HexColor("#0F172A"),
            leftIndent=12,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Mono",
            fontName="Courier",
            fontSize=8.2,
            leading=11,
            textColor=BRAND_DARK,
            backColor=LIGHT,
            leftIndent=4,
            rightIndent=4,
            spaceBefore=2,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Caption",
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="StepTitle",
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12,
            textColor=BRAND_DARK,
            spaceBefore=2,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=MUTED,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Cell",
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=HexColor("#0F172A"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="CellBold",
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=BRAND_DARK,
        )
    )
    styles.add(
        ParagraphStyle(
            name="CellHead",
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=white,
        )
    )
    return styles


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 8 * mm, "Brixigo Cloud Missions — End-to-End Integration Report")
    canvas.drawRightString(A4[0] - 18 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def section_rule():
    return HRFlowable(width="100%", thickness=1, color=BRAND, spaceBefore=2, spaceAfter=8)


def box_table(rows, col_widths=None):
    table = Table(rows, colWidths=col_widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("BACKGROUND", (0, 1), (-1, -1), LIGHT),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def main() -> Path:
    styles = build_styles()
    story = []

    # Cover
    story.append(Spacer(1, 28 * mm))
    story.append(Paragraph("BRIXIGO", styles["CoverTitle"]))
    story.append(Paragraph("Cloud Missions Integration", styles["CoverTitle"]))
    story.append(Spacer(1, 4 * mm))
    story.append(section_rule())
    story.append(
        Paragraph(
            "Final Report — Mission Integration &amp; End-to-End Data Transfer Flow",
            styles["CoverSub"],
        )
    )
    story.append(
        Paragraph(
            "Partner: Silicon Wireless STL040 / BAMS (Port 80 URL Push)",
            styles["CoverSub"],
        )
    )
    story.append(
        Paragraph(
            "Also covered: existing Port 4370 LAN Connector path",
            styles["CoverSub"],
        )
    )
    story.append(Spacer(1, 10 * mm))

    meta = [
        [
            Paragraph("Document", styles["CellHead"]),
            Paragraph("Version", styles["CellHead"]),
            Paragraph("Date", styles["CellHead"]),
            Paragraph("Status", styles["CellHead"]),
        ],
        [
            Paragraph("Cloud Missions E2E Report", styles["Cell"]),
            Paragraph("1.0", styles["Cell"]),
            Paragraph("26 Nov 2026", styles["Cell"]),
            Paragraph("Implemented &amp; Tested", styles["Cell"]),
        ],
    ]
    story.append(box_table(meta, [55 * mm, 25 * mm, 35 * mm, 45 * mm]))
    story.append(Spacer(1, 12 * mm))
    story.append(
        Paragraph(
            "This report explains how partner biometric <b>missions</b> send attendance over a cloud URL "
            "(Port 80 / HTTPS) to <b>connector.brixigo.com</b>, how that data is stored, shown on the "
            "<b>Cloud Missions</b> page, and forwarded to <b>a.brixigo.com</b> — while keeping the existing "
            "Port <b>4370</b> LAN connector flow unchanged.",
            styles["BodyCustom"],
        )
    )

    story.append(PageBreak())

    # 1
    story.append(Paragraph("1. Executive Summary", styles["H1Custom"]))
    story.append(section_rule())
    story.append(
        Paragraph(
            "Brixigo now supports <b>two ways</b> to collect biometric attendance. Both end in the same cloud "
            "storage and the same transfer to HR / Payroll on a.brixigo.com.",
            styles["BodyCustom"],
        )
    )
    summary_rows = [
        [
            Paragraph("Path", styles["CellHead"]),
            Paragraph("How it works", styles["CellHead"]),
            Paragraph("Port", styles["CellHead"]),
            Paragraph("UI page", styles["CellHead"]),
        ],
        [
            Paragraph("<b>Cloud Missions</b> (new)", styles["Cell"]),
            Paragraph(
                "Mission device pushes attendance by calling a URL on every login",
                styles["Cell"],
            ),
            Paragraph("80 / 443", styles["Cell"]),
            Paragraph("Cloud Missions", styles["Cell"]),
        ],
        [
            Paragraph("<b>LAN Connector</b> (existing)", styles["Cell"]),
            Paragraph(
                "Windows Connector pulls logs from device on private network",
                styles["Cell"],
            ),
            Paragraph("4370", styles["Cell"]),
            Paragraph("Attendance / Connector Server", styles["Cell"]),
        ],
    ]
    story.append(box_table(summary_rows, [38 * mm, 78 * mm, 22 * mm, 32 * mm]))
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "<b>Partner protocol:</b> Silicon Wireless Biometric Attendance Management System (STL040) "
            "Developer Manual 1.9 — HTTP GET query push and HTTP POST JSON push. Not ZK ADMS/iclock.",
            styles["BodyCustom"],
        )
    )

    # 2
    story.append(Paragraph("2. What is a Cloud Mission?", styles["H1Custom"]))
    story.append(section_rule())
    story.append(
        Paragraph(
            "A <b>Cloud Mission</b> is a biometric attendance device manufactured for Brixigo that does "
            "<b>not</b> need a local Windows PC polling it. At device setup time you enter (by SMS) the "
            "Brixigo cloud host URL. After that, every employee login/punch is sent directly to the website "
            "over HTTP Port 80 (or HTTPS).",
            styles["BodyCustom"],
        )
    )
    story.append(Paragraph("Simple meaning for users:", styles["H2Custom"]))
    story.append(
        Paragraph(
            "• <b>Cloud Missions</b> = devices that send punches by calling a cloud URL",
            styles["BulletCustom"],
        )
    )
    story.append(
        Paragraph(
            "• <b>Connector Server (4370)</b> = devices that stay on office LAN and are pulled by the Connector app",
            styles["BulletCustom"],
        )
    )

    # 3
    story.append(Paragraph("3. End-to-End Data Transfer Flow (Cloud Missions)", styles["H1Custom"]))
    story.append(section_rule())
    story.append(
        Paragraph(
            "Below is the full path from finger/card punch to HRMS on a.brixigo.com.",
            styles["BodyCustom"],
        )
    )

    flow_steps = [
        (
            "Step 1 — Employee punches on mission",
            "Fingerprint / RFID / keypad login happens on the Silicon Wireless device at the branch.",
        ),
        (
            "Step 2 — Device calls cloud URL (Port 80)",
            "Device opens HTTP to connector.brixigo.com using the host URL configured by SMS "
            "(*555#001#…#80#). It sends either GET /attendance? or POST /attservice with Machine ID + punch list.",
        ),
        (
            "Step 3 — nginx receives Port 80 / 443",
            "Public DNS connector.brixigo.com terminates on nginx (listen 80 and optionally 443) "
            "and proxies to the Cloud app on port 9990.",
        ),
        (
            "Step 4 — STL API accepts and acknowledges",
            "Cloud parses Silicon Wireless payload, maps SID to organisation, upserts device, stores punches. "
            'Device receives required ack: GET → $RFID=0#  |  POST → {"DeviceResponse": 0}',
        ),
        (
            "Step 5 — Stored as AttendancePunch",
            "Each punch is saved with source=cloud-mission, idempotency key, employee code, IN/OUT, "
            "and terminal Machine ID.",
        ),
        (
            "Step 6 — Visible on Cloud Missions page",
            "Admin UI shows URL punch feed and cloud mission devices under Cloud Missions.",
        ),
        (
            "Step 7 — Transfer to a.brixigo.com",
            "Existing HRMS outbox enqueues each punch and POSTs batches to "
            "{HRMS_BASE_URL}/api/integrations/brixigo-cloud/attendance/batch "
            "(production target: a.brixigo.com).",
        ),
    ]
    for title, body in flow_steps:
        story.append(
            KeepTogether(
                [
                    Paragraph(title, styles["StepTitle"]),
                    Paragraph(body, styles["BodyCustom"]),
                ]
            )
        )

    story.append(Paragraph("ASCII flow diagram", styles["H2Custom"]))
    diagram_lines = [
        "Employee punch on STL040 mission",
        "        |",
        "        |  HTTP GET /attendance?  or  POST /attservice",
        "        |  Port 80 (or 443 HTTPS)",
        "        v",
        "connector.brixigo.com  (nginx → Cloud FastAPI)",
        "        |",
        "        |  parse SID / MachineID, store AttendancePunch",
        "        |  enqueue HRMS outbox",
        "        v",
        "Cloud Missions page (live URL punch feed)",
        "        |",
        "        |  HTTPS batch sync",
        "        v",
        "a.brixigo.com  (HR & Payroll attendance)",
    ]
    for line in diagram_lines:
        safe = line.replace(" ", "&nbsp;").replace("&", "&amp;") if line.strip() else "&nbsp;"
        # Fix double-escape of &nbsp;
        safe = line.replace("&", "&amp;").replace(" ", "&nbsp;") if line.strip() else "&nbsp;"
        story.append(Paragraph(safe, styles["Mono"]))
    story.append(Paragraph("Figure 1 — Cloud Missions end-to-end path", styles["Caption"]))

    # 4
    story.append(Paragraph("4. How this relates to Port 4370 (unchanged)", styles["H1Custom"]))
    story.append(section_rule())
    story.append(
        Paragraph(
            "The previous implementation continues to work. LAN ZKTeco / eSSL devices are still polled by the "
            "Windows Connector over TCP <b>4370</b>, queued locally, then uploaded with connector auth headers "
            "to the same cloud platform.",
            styles["BodyCustom"],
        )
    )
    compare = [
        [
            Paragraph("Item", styles["CellHead"]),
            Paragraph("Cloud Missions", styles["CellHead"]),
            Paragraph("LAN Connector", styles["CellHead"]),
        ],
        [
            Paragraph("Who starts connection", styles["Cell"]),
            Paragraph("Device → Cloud", styles["Cell"]),
            Paragraph("Connector → Device", styles["Cell"]),
        ],
        [
            Paragraph("Network", styles["Cell"]),
            Paragraph("Internet / GPRS / Wi-Fi", styles["Cell"]),
            Paragraph("Private LAN", styles["Cell"]),
        ],
        [
            Paragraph("Port", styles["Cell"]),
            Paragraph("80 / 443", styles["Cell"]),
            Paragraph("4370", styles["Cell"]),
        ],
        [
            Paragraph("Protocol", styles["Cell"]),
            Paragraph("HTTP GET / JSON POST (STL040)", styles["Cell"]),
            Paragraph("ZK binary TCP", styles["Cell"]),
        ],
        [
            Paragraph("Needs Windows PC on site", styles["Cell"]),
            Paragraph("No", styles["Cell"]),
            Paragraph("Yes (Connector)", styles["Cell"]),
        ],
        [
            Paragraph("Cloud ingest", styles["Cell"]),
            Paragraph("/attendance, /attservice", styles["Cell"]),
            Paragraph("/connectors/{terminal}/push", styles["Cell"]),
        ],
        [
            Paragraph("Downstream", styles["Cell"]),
            Paragraph("Same HRMS outbox → a.brixigo.com", styles["Cell"]),
            Paragraph("Same HRMS outbox → a.brixigo.com", styles["Cell"]),
        ],
    ]
    story.append(box_table(compare, [40 * mm, 65 * mm, 65 * mm]))

    story.append(PageBreak())

    # 5
    story.append(Paragraph("5. Partner Protocol Details (STL040)", styles["H1Custom"]))
    story.append(section_rule())
    story.append(Paragraph("5.1 HTTP GET push", styles["H2Custom"]))
    story.append(Paragraph("URL example configured on device:", styles["BodyCustom"]))
    story.append(Paragraph("connector.brixigo.com/attendance?", styles["Mono"]))
    story.append(Paragraph("Request shape:", styles["BodyCustom"]))
    story.append(Paragraph("...?$SID&amp;MID&amp;RFID&amp;DOT,RFID&amp;DOT*", styles["Mono"]))
    story.append(
        Paragraph(
            "• SID = Org ID (5 digits) &nbsp;&nbsp;• MID = Machine ID (2 digits)",
            styles["BulletCustom"],
        )
    )
    story.append(
        Paragraph(
            "• RFID / PunchID = employee / card id &nbsp;&nbsp;• DOT = Date of Transaction DDMMYYYYHHMMSS",
            styles["BulletCustom"],
        )
    )
    story.append(
        Paragraph(
            "• Multiple records separated by comma; payload ends with *",
            styles["BulletCustom"],
        )
    )
    story.append(Paragraph("Server acknowledgement (required):", styles["BodyCustom"]))
    story.append(Paragraph("$RFID=0#", styles["Mono"]))

    story.append(Paragraph("5.2 HTTP POST JSON push", styles["H2Custom"]))
    story.append(Paragraph("URL example:", styles["BodyCustom"]))
    story.append(Paragraph("connector.brixigo.com/attservice?", styles["Mono"]))
    story.append(
        Paragraph(
            "Body fields: MachineID (7 digits), Attendance[].PunchID, PunchTime (DDMMYYYYHHMMSS), "
            "PunchStatus (0=IN, 1=OUT).",
            styles["BodyCustom"],
        )
    )
    story.append(Paragraph("Server acknowledgement (required):", styles["BodyCustom"]))
    story.append(Paragraph('{"DeviceResponse": 0}', styles["Mono"]))

    story.append(Paragraph("5.3 Device SMS setup (from manufacturer manual)", styles["H2Custom"]))
    story.append(Paragraph("Set host URL on Port 80:", styles["BodyCustom"]))
    story.append(
        Paragraph("*555#001#connector.brixigo.com/attendance?#80#", styles["Mono"])
    )
    story.append(Paragraph("Set Org ID + Machine ID (SID + MID):", styles["BodyCustom"]))
    story.append(Paragraph("*555#007#9999988#", styles["Mono"]))
    story.append(
        Paragraph(
            "Optional SSL: *563OK or *555#017#01# then use Port 443.",
            styles["Small"],
        )
    )

    # 6
    story.append(Paragraph("6. Setup Checklist (Go-Live)", styles["H1Custom"]))
    story.append(section_rule())
    for item in [
        "1. Deploy Cloud app; point DNS <b>connector.brixigo.com</b> to the server.",
        "2. Configure nginx for Port <b>80</b> (and 443) proxy to uvicorn :9990 — see DEPLOYMENT.md.",
        "3. Set <b>BRIXIGO_CLOUD_HRMS_BASE_URL</b> to a.brixigo.com (or your HRMS base) and enable HRMS sync.",
        "4. In admin UI open <b>Cloud Missions</b> → save 5-digit <b>STL SID</b> for the organisation.",
        "5. SMS the device host URL and SID+MID as above; set push interval if needed (*555#006#…).",
        "6. Perform a test punch → confirm row on Cloud Missions page → confirm arrival on a.brixigo.com.",
    ]:
        story.append(Paragraph(item, styles["BulletCustom"]))

    # 7
    story.append(Paragraph("7. What is stored and transferred", styles["H1Custom"]))
    story.append(section_rule())
    data_rows = [
        [
            Paragraph("Field", styles["CellHead"]),
            Paragraph("Meaning", styles["CellHead"]),
            Paragraph("Example", styles["CellHead"]),
        ],
        [
            Paragraph("source", styles["Cell"]),
            Paragraph("Marks URL-push punches", styles["Cell"]),
            Paragraph("cloud-mission", styles["Cell"]),
        ],
        [
            Paragraph("terminal_code / MachineID", styles["Cell"]),
            Paragraph("Device identity (SID+MID)", styles["Cell"]),
            Paragraph("9999988", styles["Cell"]),
        ],
        [
            Paragraph("device_employee_code", styles["Cell"]),
            Paragraph("Employee / card id from device", styles["Cell"]),
            Paragraph("8478100", styles["Cell"]),
        ],
        [
            Paragraph("punch_time", styles["Cell"]),
            Paragraph("Normalized ISO time", styles["Cell"]),
            Paragraph("2013-12-27T11:37:00", styles["Cell"]),
        ],
        [
            Paragraph("event_type", styles["Cell"]),
            Paragraph("IN or OUT", styles["Cell"]),
            Paragraph("IN", styles["Cell"]),
        ],
        [
            Paragraph("idempotency_key", styles["Cell"]),
            Paragraph("Prevents duplicate storage", styles["Cell"]),
            Paragraph("sha256(...)", styles["Cell"]),
        ],
    ]
    story.append(box_table(data_rows, [48 * mm, 70 * mm, 52 * mm]))
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "Transfer to a.brixigo.com uses the same outbound batch contract already used by Port 4370 punches "
            "(/api/integrations/brixigo-cloud/attendance/batch).",
            styles["BodyCustom"],
        )
    )

    # 8
    story.append(Paragraph("8. Important URLs &amp; APIs", styles["H1Custom"]))
    story.append(section_rule())
    api_rows = [
        [Paragraph("Purpose", styles["CellHead"]), Paragraph("Path", styles["CellHead"])],
        [
            Paragraph("Device GET push", styles["Cell"]),
            Paragraph("/attendance?  and  /rfid/attendance.aspx?", styles["Cell"]),
        ],
        [
            Paragraph("Device JSON POST", styles["Cell"]),
            Paragraph("/attservice  and  /api/stl/attservice", styles["Cell"]),
        ],
        [
            Paragraph("Admin settings / SID", styles["Cell"]),
            Paragraph("/api/v1/cloud-missions/settings", styles["Cell"]),
        ],
        [
            Paragraph("Admin punch feed", styles["Cell"]),
            Paragraph("/api/v1/cloud-missions/punches", styles["Cell"]),
        ],
        [
            Paragraph("Admin devices", styles["Cell"]),
            Paragraph("/api/v1/cloud-missions/devices", styles["Cell"]),
        ],
        [
            Paragraph("LAN connector push (4370 path)", styles["Cell"]),
            Paragraph(
                "/api/wf/biometric-enterprise/connectors/{terminal}/push",
                styles["Cell"],
            ),
        ],
    ]
    story.append(box_table(api_rows, [55 * mm, 115 * mm]))

    # 9
    story.append(Paragraph("9. Implementation map (code)", styles["H1Custom"]))
    story.append(section_rule())
    for item in [
        "• cloud/app/services/stl_attendance.py — parse, map org, store, enqueue HRMS",
        "• cloud/app/api/partners/stl.py — public Port 80 device endpoints",
        "• cloud/app/api/admin/cloud_missions.py — Cloud Missions admin API",
        "• cloud/templates/dashboard.html + static/app.js — Cloud Missions page",
        "• cloud/docs/DEPLOYMENT.md — nginx for connector.brixigo.com",
        "• cloud/docs/CLOUD_MISSIONS.md — short ops guide",
        "• cloud/tests/test_stl_cloud_missions.py — 6 automated tests (passed)",
    ]:
        story.append(Paragraph(item, styles["BulletCustom"]))

    # 10
    story.append(Paragraph("10. Verification result", styles["H1Custom"]))
    story.append(section_rule())
    story.append(
        Paragraph(
            "Automated tests cover GET ack + storage, ASPX alias + duplicate protection, JSON POST IN/OUT, "
            "multi-record GET, unknown-org POST acknowledgement, and Cloud Missions settings/SMS example. "
            "Result: <b>6 passed</b>.",
            styles["BodyCustom"],
        )
    )

    story.append(Spacer(1, 8 * mm))
    story.append(section_rule())
    story.append(
        Paragraph(
            "Bottom line: partner missions push attendance on Port 80 to connector.brixigo.com; "
            "Cloud Missions shows the data; the same pipeline delivers punches to a.brixigo.com. "
            "Port 4370 LAN connector remains fully supported.",
            styles["BodyCustom"],
        )
    )
    story.append(Paragraph("— End of report —", styles["Caption"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title="Brixigo Cloud Missions — End-to-End Integration Report",
        author="Brixigo Connector",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return OUT


if __name__ == "__main__":
    path = main()
    print(path)
    print(f"bytes={path.stat().st_size}")
